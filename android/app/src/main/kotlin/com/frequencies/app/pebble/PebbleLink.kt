package com.frequencies.app.pebble

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.AudioManager
import android.net.Uri
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.frequencies.app.R
import com.frequencies.app.TagFilter
import com.frequencies.app.accent
import com.frequencies.app.artists
import com.frequencies.app.library
import com.frequencies.app.playback.AdNow
import com.frequencies.app.playback.ArtistNow
import com.frequencies.app.playback.SleepTimer
import com.frequencies.app.playback.SongNow
import com.frequencies.app.ui.MainActivity
import com.frequencies.app.ui.NowPlaying
import com.frequencies.app.ui.PlayerConnection
import com.frequencies.core.Comfort
import com.frequencies.core.Featured
import com.frequencies.core.FeaturedHistory
import com.frequencies.core.FeaturedList
import com.frequencies.core.LyricsClient
import com.frequencies.core.Pebble
import com.frequencies.core.Pebble.Key
import com.frequencies.core.RadioBrowser
import com.frequencies.core.ReportQueue
import com.frequencies.core.Search
import com.frequencies.core.Settings
import com.frequencies.core.ShellThemes
import com.frequencies.core.Twitch
import com.frequencies.core.str
import com.frequencies.core.Shuffle
import com.frequencies.core.Station
import com.frequencies.core.Tags
import com.frequencies.core.ThumbsSnapshot
import com.frequencies.core.num
import io.rebble.pebblekit2.client.DefaultPebbleAndroidAppPicker
import io.rebble.pebblekit2.client.DefaultPebbleSender
import io.rebble.pebblekit2.common.model.PebbleDictionaryItem
import io.rebble.pebblekit2.common.model.TransmissionResult
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.File
import java.util.UUID

/**
 * The Frequencies watchapp on a Pebble (task 234, build 190): answers its
 * commands and keeps it up to date while it is open, through PebbleKit
 * Android 2. Nothing runs while the watchapp is closed. The messages are
 * core's [Pebble] (notes/pebble-protocol.md).
 */
class PebbleLink private constructor(private val context: Context) {
    private val library = context.library
    private val kit = context.artists
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val out = Channel<Map<UInt, PebbleDictionaryItem>>(Channel.UNLIMITED)
    private var sender: DefaultPebbleSender? = null
    private var connection: PlayerConnection? = null
    private var watching: Job? = null
    private var pump: Job? = null

    @Volatile private var open = false
    @Volatile private var caps = Pebble.Caps.COLOR or Pebble.Caps.LARGE
    private val large get() = caps and Pebble.Caps.LARGE != 0
    private val color get() = caps and Pebble.Caps.COLOR != 0

    // what the watch has now
    private var lastNow: Map<UInt, PebbleDictionaryItem> = emptyMap()
    private var coverKey = ""              // the picture the watch should have
    private var coverSent = ""             // the picture it has (the same one is never sent twice)
    private var coverJob: Job? = null
    private val coverLock = Mutex()
    private var logoKey = ""
    private var photoKey = ""
    /** The bio is open on the watch: its text and photo follow the artist lookup until the watch says it closed. */
    private var bioOpen = false
    /** The watch asked for the playing station's settings: they follow changes for the rest of the visit (build 196). */
    private var stationWanted = false
    private var stationSent: List<Byte> = emptyList()
    private var stationCover = ""
    private val coverRows = HashMap<Long, String>()   // COVERS row id -> station id
    private var lyricsOn = false
    private var lyricKey = ""
    private var lyricView: Int? = null      // a line chosen with Up/Down (null = follow the song)
    private var lyricStuck = false
    private var lyricMovedAt = 0L

    // the rows the watch is looking at, by the ids it hands back
    private val searchRows = HashMap<Long, RadioBrowser.Result>()
    private val featuredRows = HashMap<Long, JsonObject>()
    private val historyRows = HashMap<Long, JsonObject>()
    private var searchList: List<Pebble.Row> = emptyList()   // the last search, for the watch's later pages
    private var tagNames: List<String> = emptyList()
    private val featuredDir = File(context.filesDir, "data/featured")

    // ------------------------------------------------------------------ open / close

    fun opened() {
        if (open) return
        open = true
        Log.i(TAG, "[pebble] watchapp opened")
        sender = DefaultPebbleSender(context)
        pump = scope.launch { sendLoop() }
        ContextCompat.getMainExecutor(context).execute {
            connection = PlayerConnection(context).also { it.connect() }
        }
    }

    fun closed() {
        if (!open) return
        open = false
        Log.i(TAG, "[pebble] watchapp closed")
        watching?.cancel(); watching = null
        pump?.cancel(); pump = null
        while (out.tryReceive().isSuccess) Unit
        runCatching { sender?.close() }
        sender = null
        lyricsOn = false
        lastNow = emptyMap(); coverKey = ""; coverSent = ""; logoKey = ""; photoKey = ""; bioOpen = false
        stationWanted = false; stationSent = emptyList(); stationCover = ""
        coverJob?.cancel(); coverJob = null
        ContextCompat.getMainExecutor(context).execute { connection?.release(); connection = null }
    }

    private suspend fun sendLoop() {
        val uuid = UUID.fromString(Pebble.WATCHAPP_UUID)
        for (msg in out) {
            val s = sender ?: return
            var tries = 0
            while (tries < 3) {
                val result = runCatching { s.sendDataToPebble(uuid, msg) }.getOrNull()
                val r = result?.values?.firstOrNull()
                if (r == null || r is TransmissionResult.Success) break
                if (r is TransmissionResult.FailedDifferentAppOpen || r is TransmissionResult.FailedWatchNotConnected) { closed(); return }
                tries++
                delay(250L * tries)
            }
        }
    }

    private fun send(vararg pairs: Pair<Int, PebbleDictionaryItem>) {
        if (open) out.trySend(pairs.associate { it.first.toUInt() to it.second })
    }

    // Not inlined: Kotlin 2.2.20 crashes constant-folding Int.toUInt() from stdlib 2.3.20 (which PebbleKit 2 pulls in).
    private fun key(k: Int): UInt = k.toUInt()

    private fun u8(v: Int) = PebbleDictionaryItem.UInt8(v.coerceIn(0, 255).toUByte())
    private fun u16(v: Int) = PebbleDictionaryItem.UInt16(v.coerceIn(0, 65535).toUShort())
    private fun u32(v: Long) = PebbleDictionaryItem.UInt32((v and 0xFFFFFFFFL).toUInt())
    private fun i32(v: Int) = PebbleDictionaryItem.Int32(v)
    private fun text(s: String, max: Int) = PebbleDictionaryItem.Text(Pebble.fit(s, max))
    private fun bytes(b: ByteArray) = PebbleDictionaryItem.Bytes(b)

    // ------------------------------------------------------------------ commands

    /** One message from the watch. Runs on the listener's coroutine; quick. */
    fun received(values: Map<Int, Any?>) {
        val c = Pebble.parse(values) ?: return
        if (c.cmd == Pebble.Cmd.HELLO) { caps = c.caps; opened() }
        if (!open) opened()
        scope.launch { runCatching { handle(c) }.onFailure { Log.w(TAG, "[pebble] command ${c.cmd} failed: $it") } }
    }

    private suspend fun main(block: () -> Unit) = withContext(Dispatchers.Main) { block() }

    private suspend fun handle(c: Pebble.Command) {
        val conn = { connection }
        when (c.cmd) {
            Pebble.Cmd.HELLO -> hello()
            Pebble.Cmd.BYE -> closed()
            Pebble.Cmd.PLAY_PAUSE -> main { conn()?.togglePlay() }
            Pebble.Cmd.NEXT -> main { conn()?.next() }
            Pebble.Cmd.PREV -> main { conn()?.previous() }
            Pebble.Cmd.SCAN_TO -> shown().getOrNull(c.arg)?.let { s -> main { conn()?.play(s, shown(), fromSaved = true) } }
            Pebble.Cmd.PLAY_ITEM -> shown().firstOrNull { Pebble.id32(it.id) == c.arg.toLong() and 0xFFFFFFFFL }
                ?.let { s -> main { conn()?.play(s, shown(), fromSaved = true) } }
            Pebble.Cmd.PLAY_RANDOM -> shown().randomOrNull()?.let { s -> main { conn()?.play(s, shown(), fromSaved = true) } }
            Pebble.Cmd.STAR -> star()
            Pebble.Cmd.SHUFFLE -> library.updateSettings { it[Shuffle.SETTING] = JsonPrimitive(!Shuffle.on(it)) }
            Pebble.Cmd.SONG_ID -> current()?.let { st ->
                val on = Comfort.songIdOn(st)
                library.update(st.id) { it["shazam_auto"] = JsonPrimitive(!on); it["shazam_auto_set"] = JsonPrimitive(true) }
            }
            Pebble.Cmd.THUMB_UP -> { kit.thumbsUp(c.arg == 1); send(Key.VERDICT to u8(if (c.arg == 1) 1 else 0)) }
            Pebble.Cmd.THUMB_DOWN -> thumbsDown(c.arg)
            Pebble.Cmd.THUMB_UNDO -> { kit.thumbsUp(false); send(Key.VERDICT to u8(0), Key.THUMBS_LEFT to u8(0)) }
            Pebble.Cmd.THUMB_SEND_NOW -> { kit.sendNow(); send(Key.THUMBS_LEFT to u8(0)) }
            Pebble.Cmd.SLEEP_SET -> if (c.arg <= 0) SleepTimer.cancel() else SleepTimer.start(c.arg)
            Pebble.Cmd.SLEEP_ACTION -> library.updateSettings {
                it["sleep_action"] = JsonPrimitive(Pebble.SLEEP_ACTIONS.getOrElse(c.arg) { "fade" })
                if (c.arg == 2) tagNames.getOrNull(c.arg2)?.let { t -> it["sleep_wake_tag"] = JsonPrimitive(t) }
            }
            Pebble.Cmd.GET_LIST -> sendList(c.arg, c.arg2)
            Pebble.Cmd.SET_TAG -> tagNames.getOrNull(c.arg)?.let { tag ->
                TagFilter.set(Pebble.setTag(TagFilter.query.value, tag, c.arg2))
                main { conn()?.follow(shown()) }
                sendTags()
            }
            Pebble.Cmd.FAVORITE -> historyRows[c.arg.toLong() and 0xFFFFFFFFL]?.let { e ->
                val key = com.frequencies.core.SongHistory.entryKey(e)
                library.changeHistory { com.frequencies.core.SongHistory.setFavorite(it, key, !com.frequencies.core.SongHistory.isFavorite(e), library.now()) }
            }
            // arg 1 (watch 1.0.6+): the bio closed. Otherwise it opened: send it now and again as the lookup finishes
            // or the artist changes (user, 2026-11-10: photos didn't always reach the watch)
            Pebble.Cmd.GET_BIO -> if (c.arg == 1) bioOpen = false else { bioOpen = true; photoKey = ""; sendBio() }
            Pebble.Cmd.LYRICS -> { lyricsOn = c.arg == 1; lyricKey = ""; lyricView = null; lyricStuck = false; if (lyricsOn) sendLyrics() }
            Pebble.Cmd.LYRIC_STEP -> { lyricView = (lyricView ?: liveLine()) + if (c.arg > 0) 1 else -1; lyricMovedAt = System.currentTimeMillis(); sendLyrics() }
            Pebble.Cmd.LYRIC_HOLD -> { lyricStuck = c.arg == 1; lyricView = if (lyricStuck) liveLine() else null; sendLyrics() }
            Pebble.Cmd.LYRIC_SYNC -> {
                lyricLines().getOrNull(lyricView ?: -1)?.let { SongNow.resync(it.ms) }
                lyricStuck = false; lyricView = null; sendLyrics()
            }
            Pebble.Cmd.SEARCH -> search(c.arg, c.text)
            Pebble.Cmd.LISTEN -> listen(c.arg.toLong() and 0xFFFFFFFFL, c.arg2)
            Pebble.Cmd.SAVE -> save(c.arg.toLong() and 0xFFFFFFFFL, c.arg2)
            Pebble.Cmd.OPEN_SERVICE -> historyRows[c.arg.toLong() and 0xFFFFFFFFL]?.let { e ->
                val links = com.frequencies.core.SongHistory.searchLinks(com.frequencies.core.SongHistory.artist(e), com.frequencies.core.SongHistory.title(e))
                links.getOrNull(c.arg2)?.let { (name, url) ->
                    notify("Open in $name", com.frequencies.core.SongHistory.title(e), Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                }
            }
            Pebble.Cmd.SORT -> library.updateSettings {
                it["sort_mode"] = JsonPrimitive(Pebble.SORT_MODES.getOrElse(c.arg) { "manual" })
                it["sort_desc"] = JsonPrimitive(c.arg2 == 1)
            }
            Pebble.Cmd.PREF -> pref(c.arg, c.arg2)
            Pebble.Cmd.FIND_MORE -> notify("Add a frequency", "URL, QR code, Local, YouTube, Twitch or iBroadcast",
                Intent(context, MainActivity::class.java).putExtra(OPEN_ADD, "1"))
            Pebble.Cmd.GET_FEATURED -> sendFeatured()
            Pebble.Cmd.VOLUME -> volume(c.arg)
            Pebble.Cmd.STATION -> station(c.arg, c.arg2)
        }
    }

    // ------------------------------------------------------------------ now playing

    private fun hello() {
        lastNow = emptyMap(); coverKey = ""; coverSent = ""; logoKey = ""
        send(Key.PROTOCOL to u8(Pebble.PROTOCOL))
        sendLook()
        sendTags()
        sendNow(force = true)
        watch()
    }

    /** Follows playback, songs, the timer, ads, settings and tags while the watchapp is open. */
    private fun watch() {
        watching?.cancel()
        watching = scope.launch {
            launch {
                val conn = withContext(Dispatchers.Main) { connection }
                val state = conn?.state ?: return@launch
                combine(state, SongNow.song, SleepTimer.endsAt, AdNow.cover, library.settings) { _, _, _, _, _ -> Unit }
                    .debounce(150)
                    .collect { sendNow() }
            }
            launch { combine(library.history, SongNow.lyrics, ArtistNow.state) { _, _, _ -> Unit }.debounce(300).collect { sendNow() } }
            launch {
                ArtistNow.state.map { st -> listOf(st.busy, st.panel?.name, st.panel?.bio, st.panel?.bioSource, st.panel?.photo) }
                    .distinctUntilChanged().debounce(300).collect { if (bioOpen) sendBio() }
            }
            launch { TagFilter.query.collect { sendTags(); sendNow() } }
            launch { library.settings.debounce(300).collect { sendLook() } }
            // the playing station's settings, once the watch has asked: another station, or changed on the phone
            launch { combine(library.stations, library.settings) { _, _ -> Unit }.debounce(300).collect { sendStation() } }
            launch { context.accent.color.collect { sendLook() } }
            // lyric lines move with the song
            launch { while (isActive) { if (lyricsOn) sendLyrics(); delay(500) } }
        }
    }

    private fun nowState(): NowPlaying = connection?.state?.value ?: NowPlaying()
    private fun current(): Station? = library.station(nowState().stationId)

    /** Saved Frequencies as the phone shows them: sorted, filtered by the tag pills. */
    private fun shown(): List<Station> = Search.filter(library.sorted(), TagFilter.query.value)

    private fun song(): SongNow.Song? = SongNow.song.value?.takeIf { it.stationId == nowState().stationId }

    private fun sendNow(force: Boolean = false) {
        if (!open) return
        val np = nowState()
        val station = current()
        val s = song()
        val title = s?.title ?: if (np.hasSong) np.title else ""
        val artist = s?.artist ?: if (np.hasSong) np.artist else ""
        val state = when {
            !np.hasStation -> Pebble.State.STOPPED
            np.error != null -> Pebble.State.DOWN
            np.isPlaying -> Pebble.State.PLAYING
            np.buffering -> Pebble.State.BUFFERING
            np.playWhenReady -> Pebble.State.TUNING
            else -> Pebble.State.PAUSED
        }
        val settings = library.settings.value
        val logo = if (artist.isNotEmpty()) kit.logoFor(artist) else null
        var flags = 0
        if (s != null && com.frequencies.core.SongHistory.canFavorite(station) &&
            com.frequencies.core.SongHistory.favoriteKeys(library.history.value).contains(s.key)) flags = flags or Pebble.Flag.STARRED
        if (Shuffle.on(settings)) flags = flags or Pebble.Flag.SHUFFLE
        if (Comfort.songIdOn(station)) flags = flags or Pebble.Flag.SONG_ID
        if (SongNow.lyrics.value is SongNow.Lyrics.Found) flags = flags or Pebble.Flag.LYRICS
        if (ArtistNow.state.value.panel?.canJudge == true) flags = flags or Pebble.Flag.BIO
        if (!np.hasStation || library.isSavedId(np.stationId)) flags = flags or Pebble.Flag.SAVED
        if (title.isNotEmpty()) flags = flags or Pebble.Flag.HAS_SONG
        if (logo != null) flags = flags or Pebble.Flag.LOGO
        if (!com.frequencies.core.SongHistory.canFavorite(station)) flags = flags or Pebble.Flag.NO_STAR
        val list = shown()
        val pos = list.indexOfFirst { it.id == np.stationId }.coerceAtLeast(0)
        val ad = AdNow.cover.value?.let { AdNow.timeLeft() }
        val dict = linkedMapOf(
            key(Key.STATION) to text(station?.displayName ?: np.stationName, 43),
            key(Key.SONG) to text(title, 67),
            key(Key.ARTIST) to text(artist, 51),
            key(Key.STATE) to u8(state),
            key(Key.FLAGS) to u16(flags),   // two bytes since 199 (NO_STAR); an older watch reads the low byte
            key(Key.SLEEP_LEFT) to i32(Pebble.secondsLeft(SleepTimer.remainingMs())),
            key(Key.SLEEP_ACTION) to u8(Pebble.SLEEP_ACTIONS.indexOf(settings.string("sleep_action", "fade")).coerceAtLeast(0)),
            key(Key.AD_LEFT) to u16(ad?.let { kotlin.math.ceil(it).toInt() } ?: 0),
            key(Key.POS) to u16(pos),
            key(Key.COUNT) to u16(list.size),
        )
        // only what changed (the sleep time always goes with a change: the watch counts down itself)
        val changed = if (force) dict else dict.filter { (k, v) -> lastNow[k] != v || k == key(Key.SLEEP_LEFT) && dict.any { (k2, v2) -> k2 != k && lastNow[k2] != v2 } }
        val sleepOnly = changed.keys == setOf(key(Key.SLEEP_LEFT))
        if (changed.isNotEmpty() && !sleepOnly) out.trySend(changed)
        lastNow = dict
        // pictures after the words
        val station0 = np.artworkUri?.toString().orEmpty()
        val coverWant = station0 + "|" + (s?.coverUrl ?: "")
        if (coverWant != coverKey) {
            // Song ID sometimes loses the song for a few seconds and finds it again: keep the song's cover meanwhile
            // instead of flashing the station's picture (user, build 190: "the art doesn't always carry over").
            val songLost = s == null && coverKey.substringBefore('|') == station0 && coverKey.substringAfter('|').isNotEmpty()
            coverKey = coverWant
            coverJob?.cancel()
            coverJob = scope.launch {
                if (songLost) delay(COVER_GRACE_MS)
                sendCover(np, s, coverWant)
            }
        }
        val logoWant = logo?.file?.path.orEmpty()
        if (logoWant != logoKey) {
            logoKey = logoWant
            scope.launch { sendLogo(logo?.file) }
        }
        sendStation()   // another station: its own settings (only once the watch has asked)
    }

    /** The phone's media volume, in its own steps: [step] -1 only asks. */
    private fun volume(step: Int) {
        val am = context.getSystemService(AudioManager::class.java) ?: return
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        if (step >= 0) runCatching { am.setStreamVolume(AudioManager.STREAM_MUSIC, step.coerceIn(0, max), 0) }
            .onFailure { Log.w(TAG, "[pebble] volume: $it") }
        val now = am.getStreamVolume(AudioManager.STREAM_MUSIC)
        send(Key.VOLUME to bytes(byteArrayOf(now.coerceIn(0, 255).toByte(), max.coerceIn(1, 255).toByte())))
    }

    // ------------------------------------------------------------------ the playing station's settings (build 196)

    /** Station settings on the watch (user's sketch, 2026-11-10): the same keys the phone's station settings save. */
    private fun station(field: Int, value: Int) {
        stationWanted = true
        val st = current()
        if (field != Pebble.StationSet.ASK && st != null && library.isSavedId(st.id)) {
            val coverId = if (field == Pebble.StationSet.AD_COVER) coverRows[value.toLong() and 0xFFFFFFFFL] else null
            library.changeStations { list -> list.map { old -> if (old.id != st.id) old else old.copy().also { Pebble.StationSet.apply(it, field, value, coverId) } } }
        }
        sendStation(force = true)
    }

    /** STATION_SET and STATION_COVER, when they changed (or [force]). */
    private fun sendStation(force: Boolean = false) {
        if (!open || !stationWanted) return
        val st = current() ?: return
        val b = Pebble.StationSet.bytes(st, library.isSavedId(st.id), library.settings.value.bool("eq_advanced"))
        val coverId = st["ad_filler_station_id"].str().orEmpty()
        val cover = library.station(coverId)?.displayName.orEmpty()
        if (!force && b.toList() == stationSent && cover == stationCover) return
        stationSent = b.toList(); stationCover = cover
        send(Key.STATION_SET to bytes(b), Key.STATION_COVER to text(cover, 39))
    }

    /** "Cover it with…": a plain radio stream that starts at once, A to Z (the phone's own list). */
    private fun coverList(): List<Pebble.Row> {
        val st = current()
        val chosen = st?.get("ad_filler_kind").str() == "chosen"
        val chosenId = st?.get("ad_filler_station_id").str().orEmpty()
        coverRows.clear()
        return library.stations.value.filter { it.id != st?.id && Twitch.playableCover(it) }
            .sortedBy { it.displayName.trim().lowercase() }
            .mapIndexed { i, c ->
                val id = Pebble.id32(c.id)
                coverRows[id] = c.id
                Pebble.Row(i, id, c.displayName, flags = if (chosen && c.id == chosenId) Pebble.RowFlag.CURRENT else 0)
            }
    }

    private fun star() {
        val s = song() ?: return
        // No favorites from a music folder or iBroadcast (task 241): the watch's own flip of its star is put back.
        if (!com.frequencies.core.SongHistory.canFavorite(current())) {
            sendNow(force = true)
            return
        }
        val on = !com.frequencies.core.SongHistory.favoriteKeys(library.history.value).contains(s.key)
        com.frequencies.app.ui.starSong(library, s, current()?.displayName ?: nowState().stationName, on)
    }

    // ------------------------------------------------------------------ look

    private fun sendLook() {
        val st = library.settings.value
        val follow = st.bool(FOLLOW, true)
        val themeKey = if (follow) st.darkTheme else st.string(THEME, "outrun")
        val theme = ShellThemes.find(themeKey)
        val themeAccent = st.bool(ACCENT_THEME, false)
        val accent = if (themeAccent) theme.accent else (context.accent.color.value and 0xFFFFFF)
        send(
            Key.THEME to bytes(Pebble.themeBytes(theme.tokens, accent, theme.light)),
            Key.PREFS to bytes(byteArrayOf(if (follow) 1 else 0, ShellThemes.all.indexOf(theme).coerceAtLeast(0).toByte(), if (themeAccent) 1 else 0, if (st.bool(GROW, true)) 1 else 0,
                Pebble.Names.mode(st.string(NAMES, "both")).toByte(), namesSeconds(st).toByte())),
            Key.THEME_NAME to text(theme.name + if (follow) " (from the phone)" else "", 27),
            Key.SORT to u8(Pebble.sortByte(st.sortMode, st.sortDescending ?: com.frequencies.core.defaultDescending(st.sortMode))),
        )
    }

    private fun pref(which: Int, value: Int) {
        library.updateSettings {
            when (which) {
                Pebble.Pref.FOLLOW -> it[FOLLOW] = JsonPrimitive(value == 1)
                Pebble.Pref.THEME -> ShellThemes.all.getOrNull(value)?.let { t -> it[THEME] = JsonPrimitive(t.key); it[FOLLOW] = JsonPrimitive(false) }
                Pebble.Pref.ACCENT -> it[ACCENT_THEME] = JsonPrimitive(value == 1)
                Pebble.Pref.GROW -> it[GROW] = JsonPrimitive(value == 1)
                Pebble.Pref.NAMES -> Pebble.Names.KEYS.getOrNull(value)?.let { k -> it[NAMES] = JsonPrimitive(k) }
                Pebble.Pref.NAMES_SECONDS -> it[NAMES_SECONDS] = JsonPrimitive(Pebble.Names.clampSeconds(value))
            }
        }
        sendLook()
    }

    // ------------------------------------------------------------------ tags and lists

    private fun sendTags() {
        val settings = library.settings.value
        val stations = library.stations.value
        tagNames = Tags.library(stations, settings).take(24)
        val q = TagFilter.query.value
        val tags = tagNames.map { t ->
            Pebble.Tag(t, Pebble.gcolor8(ShellThemes.parseHex(settings.tagColor(t))), Pebble.tagState(q, t),
                stations.count { s -> s.tags.any { it.equals(t, ignoreCase = true) } })
        }
        send(Key.TAGS to bytes(Pebble.packTags(tags)))
    }

    private suspend fun sendList(kind: Int, start: Int) {
        val rows: List<Pebble.Row> = when (kind) {
            Pebble.Lists.SAVED -> savedRows()
            Pebble.Lists.TAGS, Pebble.Lists.PICK_TAG -> { sendTags(); return }
            Pebble.Lists.FAVORITES, Pebble.Lists.HISTORY -> historyList(kind == Pebble.Lists.FAVORITES)
            Pebble.Lists.PREV_FEATURED -> prevFeatured()
            Pebble.Lists.THEMES -> {
                val st = library.settings.value
                val mine = if (st.bool(FOLLOW, true)) "" else st.string(THEME, "outrun")
                ShellThemes.all.mapIndexed { i, t -> Pebble.Row(i, i.toLong(), t.name, flags = if (t.key == mine) Pebble.RowFlag.CURRENT else 0) }
            }
            Pebble.Lists.SEARCH -> searchList   // the rows of the last SEARCH, page by page
            Pebble.Lists.COVERS -> coverList()
            else -> return
        }
        sendRows(kind, rows, start)
    }

    private fun sendRows(kind: Int, rows: List<Pebble.Row>, start: Int) {
        val window = rows.drop(start.coerceAtLeast(0)).take(WINDOW)
        val msgs = Pebble.rowMessages(window)
        if (msgs.isEmpty()) send(Key.LIST_KIND to u8(kind), Key.LIST_TOTAL to u16(rows.size), Key.LIST_START to u16(start))
        for (m in msgs) send(Key.LIST_KIND to u8(kind), Key.LIST_TOTAL to u16(rows.size), Key.LIST_START to u16(start), Key.LIST_ROWS to bytes(m))
    }

    private fun savedRows(): List<Pebble.Row> {
        val np = nowState()
        val health = library.health.value
        return shown().mapIndexed { i, s ->
            val down = library.isDown(s.id)
            var f = 0
            if (s.id == np.stationId && (np.isPlaying || np.playWhenReady)) f = f or Pebble.RowFlag.PLAYING
            if (down) f = f or Pebble.RowFlag.DOWN
            val since = if (down) ((health[s.id] as? JsonObject)?.get("last_check").num() ?: 0.0).toLong() else 0L
            Pebble.Row(i, Pebble.id32(s.id), s.displayName, "", f, 0, since,
                s.tags.mapNotNull { t -> tagNames.indexOfFirst { it.equals(t, ignoreCase = true) }.takeIf { it >= 0 } })
        }
    }

    private fun historyList(favoritesOnly: Boolean): List<Pebble.Row> {
        val H = com.frequencies.core.SongHistory
        val all = H.sorted(library.history.value).filter { !favoritesOnly || H.isFavorite(it) }
        historyRows.clear()
        val out = ArrayList<Pebble.Row>()
        var day = ""
        val zone = java.time.ZoneId.systemDefault()
        val today = java.time.LocalDate.now(zone)
        for (e in all) {
            val at = H.at(e)
            val d = java.time.Instant.ofEpochSecond(at.toLong()).atZone(zone).toLocalDate()
            val label = when (d) {
                today -> "Today"
                today.minusDays(1) -> "Yesterday"
                else -> d.format(java.time.format.DateTimeFormatter.ofPattern("EEEE, MMMM d"))
            }
            if (label != day) { day = label; out += Pebble.Row(out.size, 0, label, flags = Pebble.RowFlag.HEADING) }
            val id = Pebble.id32(H.entryKey(e))
            historyRows[id] = e
            out += Pebble.Row(out.size, id, H.title(e).ifEmpty { "Unknown song" },
                listOf(H.artist(e), H.station(e)).filter { it.isNotEmpty() }.joinToString(" · "),
                if (H.isFavorite(e)) Pebble.RowFlag.FAVORITE else 0, 0, at.toLong())
            if (out.size >= 300) break
        }
        return out
    }

    // ------------------------------------------------------------------ add: search, featured

    private suspend fun search(chip: Int, text: String?) {
        val results = withContext(Dispatchers.IO) {
            runCatching {
                when {
                    chip == 0 -> library.directory.search(text.orEmpty(), limit = 40)
                    RadioBrowser.BROWSE.getOrNull(chip - 1)?.first == "new" -> library.directory.newStations(40)
                    else -> library.directory.search("", limit = 40, order = RadioBrowser.BROWSE.getOrNull(chip - 1)?.first ?: "clickcount")
                }
            }.getOrElse { emptyList() }
        }
        searchRows.clear()
        val np = nowState()
        val playingUrl = current()?.url?.trim().orEmpty()
        val rows = results.mapIndexed { i, r ->
            val id = Pebble.id32(r.uuid.ifEmpty { r.url })
            searchRows[id] = r
            var f = 0
            if (library.isSaved(r.url)) f = f or Pebble.RowFlag.SAVED
            if (playingUrl.isNotEmpty() && r.url.trim().equals(playingUrl, true) && np.playWhenReady) f = f or Pebble.RowFlag.PLAYING
            val sub = listOf(r.countryCode.ifEmpty { r.country }, r.tags.split(',').map { it.trim() }.filter { it.isNotEmpty() }.take(2).joinToString(", "),
                if (r.bitrate > 0) "${r.bitrate} kbps" else "").filter { it.isNotEmpty() }.joinToString(" · ")
            Pebble.Row(i, id, r.name, sub, f)
        }
        searchList = rows
        sendRows(Pebble.Lists.SEARCH, rows, 0)
    }

    private suspend fun sendFeatured() {
        val entries = withContext(Dispatchers.IO) { runCatching { FeaturedList(featuredDir, kit.http).load() }.getOrElse { emptyList() } }
        featuredRows.clear()
        val rows = entries.take(8).mapIndexed { i, e ->
            val id = Pebble.id32(FeaturedList.url(e).ifEmpty { "featured$i" })
            featuredRows[id] = e
            Pebble.Row(i, id, FeaturedList.card(e).name, "", if (library.isSaved(FeaturedList.url(e))) Pebble.RowFlag.SAVED else 0)
        }
        sendRows(Pebble.Lists.FEATURED, rows, 0)
        val size = if (large) 80 else 56
        entries.take(8).forEachIndexed { i, e ->
            val file = runCatching { FeaturedList.artFile(featuredDir, e) }.getOrNull()
            val bm = file?.takeIf { it.exists() }?.let { decodeFile(it, size) }
            sendPicture(Pebble.Art.FEATURED, 100L + i, bm?.let { square(it, size) }, slot = i)
        }
    }

    private suspend fun prevFeatured(): List<Pebble.Row> {
        val now = withContext(Dispatchers.IO) { runCatching { FeaturedList(featuredDir, kit.http).load() }.getOrElse { emptyList() } }
        val prev = withContext(Dispatchers.IO) { runCatching { FeaturedHistory(featuredDir, kit.http).previous(now) }.getOrElse { emptyList() } }
        return prev.mapIndexed { i, p ->
            val id = Pebble.id32(FeaturedList.url(p.item).ifEmpty { "prev$i" })
            featuredRows[id] = p.item
            val card = FeaturedList.card(p.item)
            Pebble.Row(i, id, card.name, card.subtitle, if (library.isSaved(FeaturedList.url(p.item))) Pebble.RowFlag.SAVED else 0)
        }
    }

    private fun stationFor(id: Long): Pair<Station, Boolean>? {
        searchRows[id]?.let { r ->
            library.stations.value.firstOrNull { it.url.trim().equals(r.url.trim(), true) }?.let { return it to true }
            return RadioBrowser.toStation(r, library.newId, library.now()) to false
        }
        featuredRows[id]?.let { e ->
            val url = FeaturedList.url(e).trim()
            library.stations.value.firstOrNull { it.url.trim().equals(url, true) }?.let { return it to true }
            return Featured.toStation(e, library.files.artDir, library.newId) to false
        }
        return null
    }

    private suspend fun listen(id: Long, kind: Int) {
        val (station, saved) = stationFor(id) ?: return
        main {
            if (saved) connection?.play(station, library.sorted())
            else { library.preview(station); connection?.preview(station) }
        }
        searchRows[id]?.let { r -> scope.launch(Dispatchers.IO) { runCatching { library.directory.registerClick(r.uuid) } } }
    }

    private fun save(id: Long, kind: Int) {
        if (id == 0L) {  // the frequency heard now
            val st = current() ?: return
            if (!library.isSavedId(st.id)) library.add(listOf(st))
            return
        }
        val (station, saved) = stationFor(id) ?: return
        if (!saved) library.add(listOf(station))
    }

    // ------------------------------------------------------------------ bio, thumbs, lyrics

    private suspend fun sendBio() {
        val panel = ArtistNow.state.value.panel
        val verdict = when (ArtistNow.verdict.value) { "up" -> 1; "down" -> 2; else -> 0 }
        val from = panel?.bioSource.orEmpty().removePrefix("Biography from ").removePrefix("biography from ")
        send(
            Key.BIO_NAME to text(panel?.name ?: song()?.artist.orEmpty(), 51),
            Key.BIO_FROM to text(from, 27),
            Key.BIO_TEXT to text(panel?.bio?.ifEmpty { null } ?: (if (ArtistNow.state.value.busy) "Looking for the bio…" else "No bio found for this artist."), 2000),
            Key.VERDICT to u8(verdict),
        )
        val photo = panel?.photo.orEmpty()
        if (photo != photoKey) {
            val size = if (large) 116 else 76   // the big photo on the bio (193); the watch makes the small one
            val bm = if (photo.isEmpty()) null else load(photo, size)
            // a photo that didn't load is tried again with the next update
            if (photo.isEmpty() || bm != null) photoKey = photo
            sendPicture(Pebble.Art.PHOTO, Pebble.id32(photo), bm?.let { square(it, size) })
        }
    }

    private fun thumbsDown(what: Int) {
        val panel = ArtistNow.state.value.panel ?: return
        val word = when (what) { 1 -> "bio"; 2 -> "photo"; else -> "both" }
        val snap = ThumbsSnapshot.of(panel, kit.stationName())
        ArtistNow.verdict.value = "down"
        kit.report(snap, word, holdSeconds = ReportQueue.HOLD_SECONDS, canOpenBrowser = false)
        send(Key.VERDICT to u8(2), Key.THUMBS_LEFT to u8(ReportQueue.HOLD_SECONDS.toInt()), Key.THUMBS_WHAT to u8(what))
    }

    private fun lyricLines() = (SongNow.lyrics.value as? SongNow.Lyrics.Found)?.result?.lines.orEmpty()
    private fun liveLine(): Int = LyricsClient.lineAt(lyricLines(), SongNow.positionMs()).coerceAtLeast(0)

    private fun sendLyrics() {
        val lines = lyricLines()
        // a line looked at with Up/Down goes back to the song after 10 s, unless stuck
        if (!lyricStuck && lyricView != null && System.currentTimeMillis() - lyricMovedAt > 10_000) lyricView = null
        val i = (lyricView ?: liveLine()).coerceIn(0, maxOf(0, lines.lastIndex))
        if (lyricView != null) lyricView = i
        fun at(k: Int) = lines.getOrNull(k)?.text.orEmpty()
        val state = when { lines.isEmpty() -> 0; lyricStuck -> 2; else -> 1 }
        val key = "$state|$i|${lines.size}|${at(i)}"
        if (key == lyricKey) return
        lyricKey = key
        send(
            Key.LYRIC_PREV to text(at(i - 1), 95), Key.LYRIC_CUR to text(at(i), 95), Key.LYRIC_NEXT to text(at(i + 1), 95),
            Key.LYRIC_STATE to u8(state),
        )
    }

    // ------------------------------------------------------------------ pictures

    // one cover at a time; dropped when the song moved on while it loaded, or when the watch has it already
    private suspend fun sendCover(np: NowPlaying, s: SongNow.Song?, want: String) = coverLock.withLock {
        if (want == coverSent) return@withLock
        val size = if (large) 144 else 96   // the grown picture's exact size: no stretching on the watch (bigger since 192)
        val bm = s?.coverUrl?.takeIf { it.isNotEmpty() }?.let { load(it, size) }
            ?: np.artworkUri?.let { load(it.toString(), size) }
        if (want != coverKey || !open) return@withLock
        sendPicture(Pebble.Art.COVER, Pebble.id32(want), bm?.let { square(it, size) })
        coverSent = want
    }

    private fun sendLogo(file: File?) {
        val bm = file?.let { decodeFile(it, 400) }
        if (bm == null) { sendPicture(Pebble.Art.LOGO, 0, null); return }
        val w = bm.width; val h = bm.height
        val px = IntArray(w * h).also { bm.getPixels(it, 0, w, 0, 0, w, h) }
        // trim the see-through edges, then fit the watch's logo box
        var x0 = w; var y0 = h; var x1 = -1; var y1 = -1
        for (y in 0 until h) for (x in 0 until w) if ((px[y * w + x] ushr 24) >= 128) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y }
        val cropped = if (x1 >= x0 && y1 >= y0) Bitmap.createBitmap(bm, x0, y0, x1 - x0 + 1, y1 - y0 + 1) else bm
        val (lw, lh) = Pebble.fitSize(cropped.width, cropped.height, if (large) 170 else 120, if (large) 28 else 20, 0.125)
        val scaled = Bitmap.createScaledBitmap(cropped, lw, lh, true)
        val sp = IntArray(lw * lh).also { scaled.getPixels(it, 0, lw, 0, 0, lw, lh) }
        sendBytes(Pebble.Art.LOGO, Pebble.id32(file.path), lw, lh, Pebble.mono(sp, lw, lh, logo = true), 0)
    }

    private fun sendPicture(kind: Int, id: Long, bm: Bitmap?, slot: Int = 0) {
        if (bm == null) {
            send(Key.ART_KIND to u8(kind), Key.ART_SLOT to u8(slot), Key.ART_ID to u32(0), Key.ART_TOTAL to u32(0), Key.ART_OFFSET to u32(0))
            return
        }
        val w = bm.width; val h = bm.height
        val px = IntArray(w * h).also { bm.getPixels(it, 0, w, 0, 0, w, h) }
        val data = if (color) Pebble.color8(px, w, h) else Pebble.mono(px, w, h, logo = false)
        sendBytes(kind, id, w, h, data, slot)
    }

    private fun sendBytes(kind: Int, id: Long, w: Int, h: Int, data: ByteArray, slot: Int) {
        for ((off, piece) in Pebble.chunks(data)) {
            if (off == 0) send(
                Key.ART_ID to u32(id), Key.ART_KIND to u8(kind), Key.ART_SLOT to u8(slot), Key.ART_W to u16(w), Key.ART_H to u16(h),
                Key.ART_TOTAL to u32(data.size.toLong()), Key.ART_OFFSET to u32(0), Key.ART_DATA to bytes(piece),
            ) else send(Key.ART_ID to u32(id), Key.ART_OFFSET to u32(off.toLong()), Key.ART_DATA to bytes(piece))
        }
    }

    private fun square(bm: Bitmap, size: Int): Bitmap {
        val side = minOf(bm.width, bm.height)
        val c = Bitmap.createBitmap(bm, (bm.width - side) / 2, (bm.height - side) / 2, side, side)
        return Bitmap.createScaledBitmap(c, size, size, true)
    }

    private fun decodeFile(f: File, want: Int): Bitmap? = runCatching {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(f.path, o)
        BitmapFactory.decodeFile(f.path, BitmapFactory.Options().apply { inSampleSize = sample(o.outWidth, o.outHeight, want) })
    }.getOrNull()

    private fun sample(w: Int, h: Int, want: Int): Int {
        var s = 1
        while (minOf(w, h) / (s * 2) >= want) s *= 2
        return s
    }

    /** A picture from an http(s) address, a content:// or file:// uri, or a path. */
    private suspend fun load(where: String, want: Int): Bitmap? = withContext(Dispatchers.IO) {
        runCatching {
            val bytes = when {
                where.startsWith("http://") || where.startsWith("https://") ->
                    kit.http.newCall(okhttp3.Request.Builder().url(where).build()).execute().use { r -> if (r.isSuccessful) r.body?.bytes() else null }
                where.startsWith("content:") || where.startsWith("file:") || where.startsWith("android.resource:") ->
                    context.contentResolver.openInputStream(Uri.parse(where))?.use { it.readBytes() }
                else -> File(where).takeIf { it.exists() }?.readBytes()
            } ?: return@runCatching null
            val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample(o.outWidth, o.outHeight, want) })
        }.getOrNull()
    }

    // ------------------------------------------------------------------ the phone's side of a press

    /** Opening something on the phone from the watch goes through a notification (Android won't open screens from the background). */
    private fun notify(title: String, body: String, intent: Intent) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL) == null) {
            manager.createNotificationChannel(NotificationChannel(CHANNEL, "From your Pebble", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "What you asked for on the watch, to open on the phone"
            })
        }
        val pi = PendingIntent.getActivity(context, title.hashCode(), intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_frequencies)
            .setContentTitle(title)
            .setContentText(body)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .setLocalOnly(true)  // not back to the watch
            .build()
        runCatching { manager.notify(NOTE_ID, n) }
    }

    companion object {
        private const val TAG = "Frequencies"
        private const val CHANNEL = "pebble"
        private const val NOTE_ID = 0x9EB
        private const val WINDOW = 24
        private const val COVER_GRACE_MS = 12_000L
        const val OPEN_ADD = "freq_open_add"
        // Settings › Pebble (Android-only keys, ride along in a backup)
        const val FOLLOW = "pebble_follow"
        const val THEME = "pebble_theme"
        const val ACCENT_THEME = "pebble_accent_theme"
        const val GROW = "pebble_grow"
        const val NAMES = "pebble_names"                  // "both", "artist" or "station" (192)
        const val NAMES_SECONDS = "pebble_names_seconds"  // 3..60, default 7

        fun namesSeconds(st: com.frequencies.core.Settings): Int {
            val v = st.double(NAMES_SECONDS, Pebble.Names.SECONDS_DEFAULT.toDouble())
            return if (v.isNaN()) Pebble.Names.SECONDS_DEFAULT else Pebble.Names.clampSeconds(Math.round(v.coerceIn(-1e6, 1e6)).toInt())
        }
        const val ONLY_APP = "pebble_only_app"

        @Volatile private var instance: PebbleLink? = null
        fun get(context: Context): PebbleLink =
            instance ?: synchronized(this) { instance ?: PebbleLink(context.applicationContext).also { instance = it } }

        /** Settings › Pebble › "Only this Pebble app": [pkg] null lets any Pebble app connect (PebbleKit's default). */
        suspend fun onlyApp(context: Context, pkg: String?) {
            val picker = DefaultPebbleAndroidAppPicker.getInstance(context)
            picker.enableAutoSelect = pkg == null
            picker.selectApp(pkg)
        }

        /** PebbleKit keeps the chosen app, but not "only that one": set again whenever the process starts. */
        fun applyOnlyApp(context: Context) {
            val only = context.library.settings.value.string(ONLY_APP, "")
            runCatching { DefaultPebbleAndroidAppPicker.getInstance(context).enableAutoSelect = only.isEmpty() }
        }

        fun pebbleApps(context: Context): List<String> = runCatching { DefaultPebbleAndroidAppPicker.getInstance(context).getAllEligibleApps() }.getOrElse { emptyList() }
    }
}
