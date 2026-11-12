package com.frequencies.app

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import com.frequencies.core.ArtistInfo
import com.frequencies.core.ArtistLookup
import com.frequencies.core.ArtistOverrides
import com.frequencies.core.ArtistPhotos
import com.frequencies.core.FreqJson
import com.frequencies.core.PackAddresses
import com.frequencies.core.PackUpdater
import com.frequencies.core.PhotoPackStore
import com.frequencies.core.ReportPoster
import com.frequencies.core.ReportQueue
import com.frequencies.core.ThumbsSnapshot
import com.frequencies.core.ThumbsDown
import com.frequencies.core.str
import java.io.File
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import okhttp3.OkHttpClient

/**
 * Artist info, the photo pack and thumbs-down reports for the whole app
 * (the playback service looks artists up; the screens show them and the
 * Settings switch). What the build ships comes from assets/shipped/
 * (plain data in the public source; tools/ship_assets.py refreshes it from
 * the dev picks with the desktop's own ship tools).
 */
class ArtistKit(private val context: Context, private val library: Library, private val scope: CoroutineScope) {
    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS).readTimeout(20, TimeUnit.SECONDS).build()

    private fun shipped(name: String): String? =
        runCatching { context.assets.open("shipped/$name").use { it.readBytes().toString(Charsets.UTF_8) } }.getOrNull()

    private fun shippedObject(name: String): JsonObject =
        shipped(name)?.let { runCatching { FreqJson.parseToJsonElement(it) as? JsonObject }.getOrNull() } ?: JsonObject(emptyMap())

    private val shippedArtists = shippedObject("artist_overrides.json")
    private val shippedStations = shippedObject("station_overrides.json")

    /** photo_pack.json: the pack, report and picture-link addresses. */
    val addresses: PackAddresses = PackAddresses.read(shipped("photo_pack.json"))

    /**
     * The developer's report Worker (build 151, cloudflare/report-worker.js),
     * from photo_pack.json's "report_service". No token is in the app: the
     * Worker holds it. Empty: reports open in the browser.
     */
    val reportService: String get() = addresses.reportService

    val store = PhotoPackStore(File(context.filesDir, "data"))
    val info = ArtistInfo(http)
    val photos = ArtistPhotos(http, context.cacheDir)
    val overrides = ArtistOverrides(
        { shippedArtists }, { shippedStations },
        { library.files.loadObject("artist_overrides.json") },
        { library.files.loadObject("station_overrides.json") },
        store,
    )
    val lookup = ArtistLookup(info, photos, overrides)
    /** Bumped when a new pack or new bio fixes arrive, or the pack goes: the Artist tab looks again. */
    val packChanged = MutableStateFlow(0)

    val keeper = PackKeeper(library, store, PackUpdater(http, store), addresses, scope) { info.forgetCachedBios(); packChanged.value++ }

    // -- band logos (desktop build 141) -------------------------------------------

    /** The logo pack: logo-pack.json beside photo-pack.json, kept in its own folder. */
    val logoStore = PhotoPackStore(File(context.filesDir, "data"), com.frequencies.core.BandLogos.FOLDER)
    /** Bumped when new logos arrive or go: the Artist tab looks again. */
    val logosChanged = MutableStateFlow(0)
    private var logoJob: Job? = null

    fun logosOn(): Boolean = library.settings.value.bool(com.frequencies.core.BandLogos.SETTING, true)

    /** The band's logo for [artist], or null (switched off, none in the pack, or not downloaded). */
    fun logoFor(artist: String?): com.frequencies.core.BandLogos.Logo? =
        runCatching { com.frequencies.core.BandLogos.find(logoStore, artist, logosOn()) }.getOrNull()

    /**
     * bandlogo.LogoUpdate: fetched whether or not the photos are on, a
     * minute after launch and then once a day, unless "Band logos" is off
     * (then the logos are deleted).
     */
    fun checkLogos(delayMs: Long = PackKeeper.CHECK_AFTER_MS) {
        logoJob?.cancel()
        val address = addresses.beside(com.frequencies.core.BandLogos.MANIFEST)
        logoJob = scope.launch {
            delay(delayMs)
            while (true) {
                if (!logosOn()) {
                    if (withContext(Dispatchers.IO) { logoStore.remove() }) { Log.i("Frequencies", "[logos] removed (switched off)"); logosChanged.value++ }
                    return@launch
                }
                if (address.isEmpty()) return@launch
                val result = withContext(Dispatchers.IO) { PackUpdater(http, logoStore).updateLogos(address) }
                Log.i("Frequencies", "[logos] ${result.state}: ${result.text}")
                if (result.state == "updated") logosChanged.value++
                delay(if (result.state == "failed") PackKeeper.RETRY_MS else PackKeeper.CHECK_EVERY_MS)
            }
        }
    }

    // -- thumbs down (desktop build 152, task 215) -------------------------------

    /** photopack.ReportQueue: reports waiting to go out, in a file (survives a restart). */
    private val queue = ReportQueue(File(library.files.dir, ReportQueue.FILE))
    private val queueLock = Any()

    /**
     * The Bio / Photo / Both question, about what was on screen at the
     * press. Shown over every screen (FrequenciesAppUi), so it stays open
     * whatever plays next or wherever the user goes.
     */
    val question = MutableStateFlow<ThumbsSnapshot?>(null)

    /** The thumb pressed down on [panel]: the snapshot is taken now, then the question. */
    fun press(panel: com.frequencies.core.ArtistPanel) {
        val snap = ThumbsSnapshot.of(panel, stationName())
        com.frequencies.app.playback.ArtistNow.verdict.value = "down"
        question.value = snap
    }

    /** The question answered: "bio", "photo", "both", or "" (Cancel takes the thumb back). */
    fun answer(snap: ThumbsSnapshot, what: String) {
        question.value = null
        if (what.isEmpty()) {
            val now = com.frequencies.app.playback.ArtistNow
            if (now.state.value.panel?.name == snap.artist) now.verdict.value = ""
            return
        }
        report(snap, what, holdSeconds = ReportQueue.HOLD_SECONDS, canOpenBrowser = true)
    }

    /**
     * mainwindow._store_verdict, from the snapshot: the local note, then
     * into the queue (one per artist a day), which a WorkManager job sends
     * to the report Worker once the fifteen seconds are over and the phone
     * is online. No Worker address in the build: the pre-filled issue opens
     * in the browser when [canOpenBrowser].
     */
    fun report(snap: ThumbsSnapshot, what: String, holdSeconds: Long, canOpenBrowser: Boolean) {
        val photo = if (what == "photo" || what == "both") ThumbsDown.photoLink(snap.photo, store, overrides.artistOverride(snap.artist)) else ""
        var added: ReportQueue.Added = ReportQueue.Added.Nowhere
        synchronized(queueLock) {
            // A report waiting on another artist goes out now (the desktop's earlier verdict).
            queue.releaseHolds()
            library.updateSettings { s ->
                ThumbsDown.noteWrong(s, snap.artist, snap.source, what)
                added = queue.add(s, snap, what, photo, reportService, addresses.reportRepo, BuildConfig.FREQ_BUILD.toString(), hold = holdSeconds)
            }
        }
        when (val a = added) {
            ReportQueue.Added.Queued -> {
                Log.i(TAG, "[pack] thumbs down for '${snap.artist}' queued ($what, sent in ${holdSeconds}s or when online)")
                ReportWorker.enqueue(context, holdSeconds)
            }
            ReportQueue.Added.Already -> Log.i(TAG, "[pack] thumbs down for '${snap.artist}' already reported today")
            ReportQueue.Added.Nowhere -> Log.i(TAG, "[pack] thumbs down for '${snap.artist}' not reported: this build has no report address")
            is ReportQueue.Added.Browser -> {
                Log.i(TAG, "[pack] thumbs down for '${snap.artist}': this build has no report service address, ${if (canOpenBrowser) "opening the browser" else "not reported (no screen)"}")
                if (canOpenBrowser && a.address.isNotEmpty()) openBrowser(a.address)
            }
        }
    }

    /** A thumbs up, or the thumb taken back: a report still in its fifteen seconds goes. */
    fun thumbsUp(on: Boolean) {
        val now = com.frequencies.app.playback.ArtistNow
        now.verdict.value = if (on) "up" else ""
        val artist = now.state.value.panel?.name ?: return
        val taken = synchronized(queueLock) { queue.takeBack(artist) }
        if (taken) {
            library.updateSettings { ThumbsDown.takeBackNote(it, artist) }
            Log.i(TAG, "[pack] thumbs down for '$artist' taken back")
        }
    }

    /** "Hold Select to send now" on the Pebble: the report waiting its fifteen seconds goes at once. */
    fun sendNow() {
        synchronized(queueLock) { queue.releaseHolds() }
        ReportWorker.enqueue(context, 0)
    }

    /** Reports are waiting (the app starts: send what an earlier run left). */
    fun queueWaiting(): Boolean = synchronized(queueLock) { queue.items().isNotEmpty() }

    /**
     * One step of ReportWorker: the first report whose hold is over goes to
     * the Worker. The network call runs outside every lock; the artist is
     * marked in the real settings once it went.
     */
    fun sendNextReport(): ReportQueue.Sent {
        val scratch = com.frequencies.core.Settings(LinkedHashMap(library.settings.value.data))
        val result = synchronized(queueLock) {
            queue.sendNext(scratch, reportService, addresses.reportRepo) { fields -> ReportPoster(http).send(reportService, fields) }
        }
        when (result) {
            is ReportQueue.Sent.Filed -> library.updateSettings { ThumbsDown.markSent(it, result.artist) }
            is ReportQueue.Sent.GaveUp -> library.updateSettings { ThumbsDown.markSent(it, result.artist) }
            else -> Unit
        }
        return result
    }

    /** The station playing now, by the name the user sees (for the report). */
    fun stationName(): String =
        library.station(com.frequencies.app.playback.ArtistNow.state.value.stationId)?.displayName.orEmpty()

    /** Dev builds: photopack.test_report (what "test" to the Worker). */
    suspend fun testReport(): String {
        if (reportService.isEmpty()) return "Failed: this build has no report address."
        val fields = ReportPoster.fields("Test report from Frequencies", "", "", "test", build = BuildConfig.FREQ_BUILD.toString())
        return when (val r = withContext(Dispatchers.IO) { ReportPoster(http).send(reportService, fields) }) {
            is ReportPoster.Result.Filed -> "Sent: ${r.url}"
            ReportPoster.Result.Already -> "Sent: already reported today."
            is ReportPoster.Result.Failed -> "Failed: ${r.why}."
        }
    }

    fun openBrowser(address: String) {
        runCatching {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(address)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    companion object { const val TAG = "Frequencies" }
}

/**
 * photopack.Keeper: the photo pack (opt-in) a minute after start and once
 * a day, an hour after a failure; the bio fixes for everyone, on their own
 * timer. [status] and [bioStatus] are the lines Settings shows.
 */
class PackKeeper(
    private val library: Library,
    private val store: PhotoPackStore,
    private val updater: PackUpdater,
    private val addresses: PackAddresses,
    private val scope: CoroutineScope,
    private val changed: () -> Unit,
) {
    private val _status = MutableStateFlow("")
    val status: StateFlow<String> = _status.asStateFlow()
    private val _bioStatus = MutableStateFlow("")
    val bioStatus: StateFlow<String> = _bioStatus.asStateFlow()
    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    private var packTimer: Job? = null
    private var bioTimer: Job? = null
    private var packJob: Job? = null
    private var bioJob: Job? = null
    @Volatile private var cancel = false

    val hasAddress: Boolean get() = addresses.pack.isNotEmpty()
    fun enabled(): Boolean = library.settings.value.bool(PhotoPackStore.SETTING) && hasAddress

    fun start() {
        _status.value = resting()
        _bioStatus.value = bioResting()
        if (enabled()) packTimer = later(CHECK_AFTER_MS) { checkPack() }
        if (hasAddress) bioTimer = later(CHECK_AFTER_MS) { checkBios() }
    }

    private fun later(ms: Long, then: () -> Unit) = scope.launch { delay(ms); then() }

    private fun resting(): String {
        if (!hasAddress) return "This build has no photo pack address."
        val have = store.installed()
        if (!library.settings.value.bool(PhotoPackStore.SETTING)) return "Off." + if (have.isNotEmpty()) " The pack is still here." else ""
        return if (have.isNotEmpty()) lines("Up to date") else "Not downloaded yet."
    }

    /** Last updated / Up to date or Update available / Last downloaded (build 100). */
    private fun lines(state: String): String {
        val have = store.installed()
        return "Last updated: ${have["made"].str()?.ifEmpty { null } ?: "?"}\n$state\nLast downloaded: ${have["installed"].str()?.ifEmpty { null } ?: "?"}"
    }

    private fun bioResting(): String {
        if (!hasAddress) return ""
        val have = store.bioInfo() ?: return "Bio fixes from the developer: none yet."
        return "Bio fixes from the developer: ${have.third} artists, made ${have.second.ifEmpty { "?" }}."
    }

    /** The Settings switch (already saved). */
    fun setEnabled(on: Boolean) {
        if (on) {
            Log.i(TAG, "[pack] opted in")
            checkNow()
            return
        }
        packTimer?.cancel()
        cancel = true
        scope.launch {
            val removed = withContext(Dispatchers.IO) { store.remove() }
            if (removed) { Log.i(TAG, "[pack] pack removed (switched off)"); changed() }
            _status.value = "Off. The pack was deleted."
        }
    }

    fun checkNow() {
        checkBios()
        if (!enabled()) { _status.value = resting(); return }
        checkPack()
    }

    private fun checkPack() {
        if (packJob?.isActive == true) return
        packTimer?.cancel()
        cancel = false
        _status.value = "Checking for a new pack…"
        _busy.value = true
        packJob = scope.launch {
            Log.i(TAG, "[pack] checking ${addresses.pack}")
            val result = withContext(Dispatchers.IO) {
                updater.updatePack(addresses.pack, { cancel }) { percent ->
                    _status.value = if (percent == 0) lines("Update available, downloading…") else "Downloading… $percent%"
                }
            }
            _busy.value = false
            Log.i(TAG, "[pack] pack ${result.state}: ${result.text}")
            if (!library.settings.value.bool(PhotoPackStore.SETTING)) {
                if (withContext(Dispatchers.IO) { store.remove() }) changed()
                _status.value = resting()
                return@launch
            }
            if (result.state == "failed") {
                _status.value = "Failed: ${result.text}. Trying again in an hour."
                packTimer = later(RETRY_MS) { checkPack() }
                return@launch
            }
            _status.value = lines("Up to date")
            if (result.state == "updated") changed()
            packTimer = later(CHECK_EVERY_MS) { checkPack() }
        }
    }

    private fun checkBios() {
        if (!hasAddress || bioJob?.isActive == true) return
        bioTimer?.cancel()
        bioJob = scope.launch {
            val result = withContext(Dispatchers.IO) { updater.updateBios(addresses.bioFixes) }
            Log.i(TAG, "[pack] bio fixes ${result.state}: ${result.text}")
            if (result.state == "failed") {
                _bioStatus.value = "Bio fixes: couldn't check (${result.text}). Trying again in an hour."
                bioTimer = later(RETRY_MS) { checkBios() }
                return@launch
            }
            _bioStatus.value = bioResting()
            if (result.state == "updated") changed()
            bioTimer = later(CHECK_EVERY_MS) { checkBios() }
        }
    }

    companion object {
        private const val TAG = "Frequencies"
        const val CHECK_AFTER_MS = 60_000L
        const val CHECK_EVERY_MS = 24 * 3600_000L
        const val RETRY_MS = 3600_000L
    }
}
