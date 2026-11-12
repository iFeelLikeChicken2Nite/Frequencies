package com.frequencies.app

import android.content.Context
import android.net.Uri
import com.frequencies.app.playback.ArtProvider
import com.frequencies.core.Backup
import com.frequencies.core.Health
import com.frequencies.core.LibraryFiles
import com.frequencies.core.RadioBrowser
import com.frequencies.core.Settings
import com.frequencies.core.Station
import com.frequencies.core.addStations
import com.frequencies.core.renumberManualOrder
import com.frequencies.core.safeFileName
import com.frequencies.core.sortStations
import com.frequencies.core.truthy
import java.io.File
import java.io.OutputStream
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * The saved frequencies and settings, shared by the screens and the
 * playback service (one process). Files are the desktop's own formats in
 * the app's private folder; writes go out on one background thread, in
 * order.
 */
class Library(context: Context) : com.frequencies.core.SyncLibrary {
    val files = LibraryFiles(File(context.filesDir, "frequencies"))
    private val writer = Executors.newSingleThreadExecutor { Thread(it, "library-writer") }
    private val lock = Any()
    private val packageName = context.packageName
    /** For the car's own icons (the tag strip). */
    val appContext: Context = context.applicationContext

    private val _stations = MutableStateFlow(files.loadStations())
    private val _settings = MutableStateFlow(files.loadSettings())
    val stations: StateFlow<List<Station>> = _stations.asStateFlow()
    val settings: StateFlow<Settings> = _settings.asStateFlow()

    private val _history = MutableStateFlow(files.loadHistory())
    /** history.json: the songs Song ID named, newest first (core SongHistory). */
    val history: StateFlow<JsonArray> = _history.asStateFlow()

    private val _health = MutableStateFlow(files.loadHealth())
    /** health.json: which frequencies are marked down (core Health), in the desktop's format. */
    val health: StateFlow<JsonObject> = _health.asStateFlow()

    /**
     * Directory results played from Add Frequency without being saved:
     * playable through the service like a saved one, but never written,
     * counted or marked down.
     */
    private val previews = ConcurrentHashMap<String, Station>()

    /** The station directory behind Add Frequency. */
    val directory = RadioBrowser()

    val newId: () -> String = { UUID.randomUUID().toString() }
    fun now(): Double = System.currentTimeMillis() / 1000.0

    /** The list in the order Saved Frequencies shows it. */
    fun sorted(): List<Station> = sortStations(_stations.value, _settings.value.sortMode, _settings.value.sortDescending)

    fun station(id: String?): Station? = id?.let { wanted -> _stations.value.firstOrNull { it.id == wanted } ?: previews[wanted] }

    /** A saved frequency (not a directory preview). */
    fun isSavedId(id: String?): Boolean = id != null && _stations.value.any { it.id == id }

    /** Play a directory result without saving it: the service finds it by its id. */
    fun preview(station: Station) {
        if (previews.size > 20) previews.clear()
        previews[station.id] = station
    }

    fun isPreview(id: String?): Boolean = id != null && previews.containsKey(id) && !isSavedId(id)

    // -- health ---------------------------------------------------------------

    private fun changeHealth(change: (JsonObject) -> JsonObject) {
        synchronized(lock) {
            val next = change(_health.value)
            if (next === _health.value) return
            _health.value = next
            writer.execute { files.saveHealth(next) }
        }
    }

    fun isDown(id: String?): Boolean = Health.isDown(_health.value, id)

    /** Reached: the down mark goes (storage.mark_ok). Only saved frequencies are recorded. */
    fun markOk(id: String) {
        if (!isSavedId(id)) return
        changeHealth { Health.markOk(it, id, now()) }
    }

    /**
     * Why a station can't be played here now, in a few words for a list
     * ("Needs the internet" while offline, "Not on this phone" for a music
     * folder that isn't here), or null. Set by the app (LocalMusic).
     */
    var unavailable: (Station) -> String? = { null }

    /** Whether the internet is gone (Connectivity, set by the app). */
    var offline: () -> Boolean = { false }

    /** storage._recent_failures: (when, station, its entry before), for [forgetRecentFailures]. */
    private val recentFailures = ArrayList<Triple<Double, String, kotlinx.serialization.json.JsonElement?>>()

    /**
     * Couldn't be reached: marked down at once (storage.mark_failed, down_after=1).
     * Never while offline (task 214): a station isn't down because the internet is.
     */
    fun markFailed(id: String, reason: String) {
        if (!isSavedId(id) || offline()) return
        synchronized(lock) {
            val t = now()
            recentFailures.removeAll { t - it.first >= 90 }
            recentFailures += Triple(t, id, _health.value[id])
        }
        changeHealth { Health.markFailed(it, id, reason, now()) }
    }

    /**
     * storage.forget_recent_failures: the internet just turned out to be
     * gone, so the marks of the last [seconds] were the connection, not the
     * stations; they are put back as they were.
     */
    fun forgetRecentFailures(seconds: Double = 60.0): Int {
        val undo = synchronized(lock) {
            val t = now()
            recentFailures.filter { t - it.first <= seconds }.reversed().also { recentFailures.clear() }
        }
        if (undo.isEmpty()) return 0
        changeHealth { h ->
            val next = LinkedHashMap(h)
            for ((_, id, before) in undo) if (before == null) next.remove(id) else next[id] = before
            JsonObject(next)
        }
        return undo.map { it.second }.toSet().size
    }

    /** Connecting cleared it: only when there is something to clear. */
    fun markConnected(id: String) {
        if (Health.needsClearing(_health.value, id)) markOk(id)
    }

    /** The station's picture for the phone's own screens: its file, else null (the letter). */
    fun artUri(station: Station): Uri? {
        val path = station.customArtPath
        val file = if (path.isEmpty()) null else File(path)
        if (file != null && file.isFile && file.length() > 0) return Uri.fromFile(file)
        // No favicons (desktop bridge.logo_for; Android batch 22): a site's tiny icon,
        // often see-through or slow, made tiles look empty (Radio Caprice). The
        // letter shows until the station has a picture of its own.
        return null
    }

    /**
     * The station's picture as a link other apps can open (Android Auto,
     * the notification, the lock screen): a content:// link into the art
     * folder, or the full picture's https link when the saved one is only
     * a QR code's small thumbnail or isn't on this phone, else its
     * directory icon.
     */
    fun sharedArtUri(station: Station): Uri? {
        val link = station.artUrl.takeIf { it.startsWith("https://") }?.let(Uri::parse)
        if (link != null && truthy(station["art_from_code"])) return link
        val path = station.customArtPath
        val local = if (path.isEmpty()) null else ArtProvider.uriFor(packageName, files.artDir, File(path))
        return local ?: link ?: station.favicon.takeIf { it.startsWith("https://") }?.let(Uri::parse)
    }

    private fun saveList(list: List<Station>) {
        _stations.value = list
        writer.execute { files.saveStations(list) }
    }

    /** Any change to the list, in one go. */
    fun changeStations(change: (List<Station>) -> List<Station>) {
        synchronized(lock) { saveList(change(_stations.value)) }
    }

    /** Stations and settings changed together (tag renames move colors too). */
    fun changeStationsAndSettings(change: (List<Station>, Settings) -> List<Station>) {
        synchronized(lock) {
            val next = Settings(LinkedHashMap(_settings.value.data))
            saveList(change(_stations.value, next))
            _settings.value = next
            writer.execute { files.saveSettings(next) }
        }
    }

    /** New stations at the end of the list; addresses already saved are skipped. Returns the ones added. */
    fun add(incoming: List<Station>, markNew: Boolean = true): List<Station> {
        synchronized(lock) {
            val (list, added) = addStations(_stations.value, incoming, newId, now(), markNew)
            if (added.isNotEmpty()) saveList(list)
            return added
        }
    }

    fun isSaved(url: String): Boolean = _stations.value.any { it.url.trim().equals(url.trim(), ignoreCase = true) }

    fun remove(ids: Set<String>) = changeStations { list -> renumberManualOrder(list.filter { it.id !in ids }.sortedBy { it.sortIndex }) }

    fun update(id: String, change: (Station) -> Unit) =
        changeStations { list -> list.map { if (it.id == id) it.copy().also(change) else it } }

    /** The manual order after a drag: [ids] top to bottom. */
    fun setManualOrder(ids: List<String>) = changeStations { list ->
        val byId = list.associateBy { it.id }
        val ordered = ids.mapNotNull { byId[it] } + list.filter { it.id !in ids.toSet() }
        renumberManualOrder(ordered)
    }

    /** A picture the user chose, copied into the art folder. */
    fun setArt(id: String, name: String, bytes: ByteArray) {
        files.artDir.mkdirs()
        val file = File(files.artDir, "$id-${System.currentTimeMillis()}-${safeFileName(name)}")
        file.writeBytes(bytes)
        update(id) {
            it["custom_art_path"] = JsonPrimitive(file.absolutePath)
            it["art_source"] = JsonPrimitive("custom")
            it["art_from_code"] = JsonPrimitive(false)
            it["art_url"] = JsonPrimitive("")
            it["art_url_sha"] = JsonPrimitive("")
        }
    }

    fun clearArt(id: String) = update(id) {
        it["custom_art_path"] = JsonPrimitive("")
        it["art_source"] = JsonPrimitive("auto")
        it["art_from_code"] = JsonPrimitive(false)
    }

    /** This phone's own choices, kept by a Replace restore (like the desktop's storage.keep_local_settings). */
    private val localSettings = listOf(
        Settings.ANDROID_THEME, "android_asked_notifications", "last_station_id",
        // storage.LOCAL_SETTINGS: the theme and the photo pack opt-in are each computer's (and phone's) own.
        "theme", "photo_pack", "band_logos",
    )

    /** The desktop's full backup: everything, pictures embedded. */
    fun backup(out: OutputStream) {
        val contents = Backup.Contents(
            stations = _stations.value,
            settings = _settings.value,
            health = _health.value,
            history = files.loadHistory(),
            artistOverrides = files.loadObject("artist_overrides.json"),
            stationOverrides = files.loadObject("station_overrides.json"),
        )
        out.write(com.frequencies.core.encodeJson(Backup.export(contents)).toByteArray(Charsets.UTF_8))
    }

    /**
     * A backup read in. Replace: its library and settings become this
     * phone's (this phone's theme kept); Merge: only stations not saved
     * yet are added. Returns how many stations came in.
     */
    fun restore(contents: Backup.Contents, replace: Boolean): Int {
        if (!replace) return add(contents.stations, markNew = false).size
        synchronized(lock) {
            val mine = _settings.value
            val next = Settings(LinkedHashMap(contents.settings.data))
            for (key in localSettings) {
                val value = mine[key]
                if (value != null) next[key] = value else next.data.remove(key)
            }
            saveList(contents.stations)
            _settings.value = next
            _history.value = contents.history
            _health.value = contents.health
            writer.execute {
                files.saveSettings(next)
                files.saveHealth(contents.health)
                files.saveHistory(contents.history)
                files.saveObject("artist_overrides.json", contents.artistOverrides)
                files.saveObject("station_overrides.json", contents.stationOverrides)
            }
        }
        return contents.stations.size
    }

    /** A station started playing: play count, last played, no longer new, and remembered for next time. */
    fun recordPlay(id: String) {
        if (!isSavedId(id)) return
        synchronized(lock) {
            val now = now()
            saveList(_stations.value.map { if (it.id == id) it.copy().apply { recordPlay(now) } else it })
        }
        updateSettings { it.lastStationId = id }
    }

    /** A change to the song history; [change] returns null when nothing changed. */
    fun changeHistory(change: (JsonArray) -> JsonArray?) {
        synchronized(lock) {
            val next = change(_history.value) ?: return
            _history.value = next
            writer.execute { files.saveHistory(next) }
        }
    }

    /** Settings changed on the main thread: shown at once, applied under the lock on a thread of its own (build 203, batch 28). */
    private val quickSettings = com.frequencies.core.QuickChanges("settings-change") { android.util.Log.w("Frequencies", "[settings] couldn't apply a change: $it") }

    /**
     * On the main thread (Settings' switches, Appearance, the Pebble page…)
     * this never waits for the lock: a sync merge can hold it for seconds
     * (found in build 198). The new value shows at once; the change is
     * applied again under the lock once it is free (every change here only
     * sets values, so applying it twice is the same as once).
     */
    fun updateSettings(change: (Settings) -> Unit) {
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) {
            quickSettings.change(
                show = { _settings.update { Settings(LinkedHashMap(it.data)).also(change) } },
                apply = { applySettings(change) },
            )
            return
        }
        applySettings(change)
    }

    private fun applySettings(change: (Settings) -> Unit) {
        synchronized(lock) {
            val next = Settings(LinkedHashMap(_settings.value.data)).also(change)
            _settings.value = next
            writer.execute { files.saveSettings(next) }
        }
    }

    /**
     * Sync (SyncKeeper): copies of everything under the lock; what the
     * step marks changed is shown and saved, and health follows a station
     * that was merged into its twin or deleted on the other device.
     */
    /** Copies of what is shown now, without the lock (StateFlow values are whole snapshots; stations are copied on change). */
    override fun <T> peek(block: (com.frequencies.core.SyncEdit) -> T): T = block(
        com.frequencies.core.SyncEdit(
            _stations.value.map { it.copy() }.toMutableList(),
            Settings(LinkedHashMap(_settings.value.data)),
            com.frequencies.core.SongHistory.entries(_history.value).toMutableList(),
        ),
    )

    override fun <T> edit(block: (com.frequencies.core.SyncEdit) -> T): T = synchronized(lock) {
        val e = com.frequencies.core.SyncEdit(
            _stations.value.map { it.copy() }.toMutableList(),
            Settings(LinkedHashMap(_settings.value.data)),
            com.frequencies.core.SongHistory.entries(_history.value).toMutableList(),
        )
        val result = block(e)
        if (e.stationsChanged) saveList(e.stations.toList())
        if (e.settingsChanged) {
            val next = e.settings
            _settings.value = next
            writer.execute { files.saveSettings(next) }
        }
        if (e.historyChanged) {
            val next = JsonArray(e.history.toList())
            _history.value = next
            writer.execute { files.saveHistory(next) }
        }
        if (e.renamed.isNotEmpty() || e.removed.isNotEmpty()) {
            changeHealth { h ->
                val next = LinkedHashMap(h)
                for ((old, kept) in e.renamed) next.remove(old)?.let { if (kept !in next) next[kept] = it }
                for (gone in e.removed) next.remove(gone)
                if (next == h) h else JsonObject(next)
            }
        }
        result
    }

    /** History's "Clear": stars stay, and the time is kept so a paired desktop clears the same rows. */
    fun clearHistory() {
        synchronized(lock) {
            changeHistory { com.frequencies.core.SongHistory.clear(it) }
            updateSettings { it[com.frequencies.core.SyncData.CLEARED_KEY] = com.frequencies.core.floatJson(now()) }
        }
    }

    /** Dev builds: throw the list away and start again with the default frequencies. */
    fun resetToDefaults() {
        synchronized(lock) { saveList(files.defaultStations()) }
    }
}
