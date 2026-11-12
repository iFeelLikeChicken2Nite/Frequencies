package com.frequencies.app.playback

import android.os.SystemClock
import android.util.Log
import com.frequencies.app.Library
import com.frequencies.app.Connectivity
import com.frequencies.core.LyricsSearch
import com.frequencies.core.CoverSearch
import com.frequencies.core.LyricsClient
import com.frequencies.core.LyricsResult
import com.frequencies.core.NowPlayingRules
import com.frequencies.core.SongHistory
import com.frequencies.songid.Downsampler
import com.frequencies.songid.Shazam
import com.frequencies.songid.Signature
import com.frequencies.songid.SongId
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient

/**
 * The song Song ID named, its lyrics and the lyric clock: shared by the
 * playback service, which finds them, and the screens, which show them.
 */
object SongNow {
    data class Song(
        val stationId: String,
        val title: String,
        val artist: String,
        val album: String,
        val coverUrl: String,
        val appleMusicUrl: String,
    ) {
        val key: String get() = SongHistory.trackKey(artist, title)
    }

    sealed interface Lyrics {
        /** Nothing identified yet, or lyrics are off (the reason says which). */
        data class Idle(val reason: String) : Lyrics
        data object Loading : Lyrics
        data class Found(val result: LyricsResult.Found) : Lyrics
        /** None came, or the service is busy: [search] is the web search for them (task 213), "" for none. */
        data class Missing(val message: String, val search: String = "") : Lyrics
    }

    private val _song = MutableStateFlow<Song?>(null)
    val song: StateFlow<Song?> = _song.asStateFlow()
    private val _lyrics = MutableStateFlow<Lyrics>(Lyrics.Idle(WAITING))
    val lyrics: StateFlow<Lyrics> = _lyrics.asStateFlow()

    /** elapsedRealtime when the song's first moment was (or would have been) heard. */
    @Volatile private var zero = 0L
    /** elapsedRealtime the clock was held at (paused), or 0. */
    @Volatile private var heldAt = 0L
    private val _resyncs = MutableStateFlow(0)
    /** Bumped on each hand resync, so the lyrics view follows again. */
    val resyncs: StateFlow<Int> = _resyncs.asStateFlow()

    const val WAITING = "Lyrics show up here once a song is identified."

    /** Where we are in the song, in milliseconds (dead reckoning from Song ID's offset). */
    fun positionMs(): Long {
        if (zero == 0L) return 0
        val now = if (heldAt != 0L) heldAt else SystemClock.elapsedRealtime()
        return (now - zero).coerceAtLeast(0)
    }

    /** "The line being sung is this one" (mainwindow.resync_lyrics). */
    fun resync(lineMs: Long) {
        val now = if (heldAt != 0L) heldAt else SystemClock.elapsedRealtime()
        zero = now - lineMs
        _resyncs.value++
    }

    /** Paused: the words hold still; playing again: they carry on from there. */
    fun hold(held: Boolean) {
        val now = SystemClock.elapsedRealtime()
        if (held && heldAt == 0L) heldAt = now
        else if (!held && heldAt != 0L) {
            if (zero != 0L) zero += now - heldAt
            heldAt = 0
        }
    }

    internal fun set(song: Song?, zeroAt: Long) {
        _song.value = song
        zero = zeroAt
    }

    internal fun update(song: Song) { _song.value = song }
    internal fun setLyrics(l: Lyrics) { _lyrics.value = l }
}

/**
 * Song ID while a station plays (feeds.run_shazam): the first check once
 * six seconds have been heard, then every 30 seconds, one at a time, a
 * check stuck for 60 seconds let go. A hit goes to the history, the now
 * playing line, the cover and the lyrics; a miss keeps what was showing.
 */
class SongWatch(
    private val library: Library,
    private val tap: AudioTap,
    private val scope: CoroutineScope,
    /** The line, art or lyrics changed: tell the session. */
    private val changed: () -> Unit,
) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).build()
    private val shazam = Shazam(http)
    private val lyricsClient = LyricsClient(http)
    private val covers = CoverSearch(http)

    private var stationId: String? = null
    private var job: Job? = null
    private var startedAt = 0L
    private var nextAt = 0L
    private var lastFailure = ""
    private var failures = 0
    private var lyricsKey: String? = null

    /** A new station (or none): forget the last one's song. */
    fun tuned(id: String?) {
        if (id == stationId) return
        stationId = id
        job?.cancel()
        job = null
        tap.forget()
        nextAt = 0
        failures = 0
        lastFailure = ""
        lyricsKey = null
        SongNow.set(null, 0)
        SongNow.setLyrics(SongNow.Lyrics.Idle(SongNow.WAITING))
    }

    /**
     * The same station went on with another track (a YouTube playlist's next video, an
     * iBroadcast library's or a music folder's next song; build 198, task 244): the last
     * song, its lyrics and cover go, and Song ID listens again from the new track's start
     * (the old clip is the old song). Before, the last song stayed until a check named
     * the new one, and a miss kept it.
     */
    fun newTrack(id: String) {
        if (id != stationId) return
        job?.cancel()
        job = null
        tap.forget()
        nextAt = 0
        lyricsKey = null
        if (SongNow.song.value != null) {
            SongNow.set(null, 0)
            changed()
        }
        SongNow.setLyrics(SongNow.Lyrics.Idle(SongNow.WAITING))
    }

    /** Called about once a second while playing. */
    fun tick() {
        val id = stationId ?: return
        val station = library.station(id) ?: return
        if (!NowPlayingRules.identifies(station)) return
        // An iBroadcast library names its own tracks: nothing to identify (bridge._announce_ibroadcast).
        if (com.frequencies.core.IBroadcast.isIBroadcast(station)) return
        val now = SystemClock.elapsedRealtime()
        job?.let {
            if (it.isActive && now - startedAt < STUCK_MS) return
            if (it.isActive) {
                Log.i(TAG, "[songid] '${station.displayName}': the last song check has been stuck for ${(now - startedAt) / 1000} s; letting it go and starting a new one")
                it.cancel()
            }
        }
        if (now < nextAt) return
        val clip = tap.last(SongId.SECONDS * 1000L) ?: return
        nextAt = now + SongId.INTERVAL_SECONDS * 1000L
        startedAt = now
        job = scope.launch { check(id, clip) }
    }

    private suspend fun check(id: String, clip: AudioTap.Clip) {
        val result = runCatching {
            withContext(Dispatchers.Default) {
                withTimeout(STUCK_MS) {
                    val samples = Downsampler(clip.rate).process(clip.samples)
                    val signature = Signature.make(samples)
                    withContext(Dispatchers.IO) { shazam.identify(signature) }
                }
            }
        }
        if (id != stationId) return                 // an old station's answer: say nothing
        val station = library.station(id) ?: return
        result.onFailure { e ->
            if (e is kotlinx.coroutines.CancellationException && e !is kotlinx.coroutines.TimeoutCancellationException) return
            failures++
            val message = e.message ?: e.javaClass.simpleName
            if (message != lastFailure) {
                lastFailure = message
                Log.w(TAG, "[songid] '${station.displayName}': song check failed: $message")
            }
            return
        }
        val track = result.getOrNull()
        if (track == null) {
            Log.i(TAG, "[songid] '${station.displayName}': no match")
            return
        }
        if (failures > 0) {
            Log.i(TAG, "[songid] song checks working again after $failures that failed: ${track.artist} - ${track.title}")
            failures = 0
            lastFailure = ""
        }
        val song = SongNow.Song(id, track.title, track.artist, track.album, track.coverUrl, track.appleMusicUrl)
        val current = SongNow.song.value
        if (current?.key == song.key && current.stationId == id) return   // the same song carrying on
        // Where the sample began in the song, heard about now: the clip ended
        // [endedAt]; the sound reaches the speaker a little after the tap.
        val offset = ((track.matchOffset ?: 0.0) * 1000).toLong()
        val zero = clip.endedAt - clip.durationMs + OUTPUT_LATENCY_MS - offset
        SongNow.set(song, zero)
        Log.i(TAG, "[songid] '${station.displayName}': ${song.artist} - ${song.title} (${offset / 1000.0} s in)")

        val limit = SongHistory.limit(library.settings.value)
        library.changeHistory {
            SongHistory.add(it, song.title, song.artist, station.displayName, library.now(), song.appleMusicUrl, song.coverUrl, limit)
        }
        changed()
        if (song.coverUrl.isEmpty()) findCover(song)
        fetchLyrics(song)
    }

    /**
     * mainwindow._ibroadcast_track: a track the station named itself (an
     * iBroadcast library), shown the way Song ID's answer is, with its
     * cover, lyrics and a line in the history. [positionMs]: how far into
     * the song playback is, for the lyric clock.
     */
    fun known(id: String, title: String, artist: String, album: String, cover: String, positionMs: Long) {
        if (id != stationId || title.isBlank()) return
        val station = library.station(id) ?: return
        val song = SongNow.Song(id, title, artist, album, cover, "")
        val zero = SystemClock.elapsedRealtime() - positionMs
        val current = SongNow.song.value
        if (current?.key == song.key && current.stationId == id) { SongNow.set(current, zero); return }
        SongNow.set(song, zero)
        Log.i(TAG, "[ibroadcast] '${station.displayName}': ${song.artist} - ${song.title}")
        val limit = SongHistory.limit(library.settings.value)
        library.changeHistory {
            SongHistory.add(it, song.title, song.artist, station.displayName, library.now(), "", song.coverUrl, limit)
        }
        changed()
        if (song.coverUrl.isEmpty()) findCover(song)
        fetchLyrics(song)
    }

    /** The lyric clock follows a seek (joining a song partway through). */
    fun movedTo(id: String, positionMs: Long) {
        val current = SongNow.song.value?.takeIf { it.stationId == id && id == stationId } ?: return
        SongNow.set(current, SystemClock.elapsedRealtime() - positionMs)
    }

    private fun findCover(song: SongNow.Song) = scope.launch {
        val url = withContext(Dispatchers.IO) { covers.find(song.artist, song.title, song.album) } ?: return@launch
        val now = SongNow.song.value
        if (now?.key != song.key || now.stationId != song.stationId) return@launch
        Log.i(TAG, "[art] cover for '${song.title}': found by search")
        SongNow.update(now.copy(coverUrl = url))
        library.changeHistory { history ->
            val list = SongHistory.entries(history)
            val i = list.indexOfFirst { SongHistory.entryKey(it) == song.key && SongHistory.coverUrl(it).isEmpty() }
            if (i < 0) null else kotlinx.serialization.json.JsonArray(list.toMutableList().also {
                it[i] = kotlinx.serialization.json.JsonObject(LinkedHashMap(it[i]).apply { put("cover_url", kotlinx.serialization.json.JsonPrimitive(url)) })
            })
        }
        changed()
    }

    /** feeds._maybe_fetch_lyrics */
    private fun fetchLyrics(song: SongNow.Song) {
        val station = library.station(song.stationId) ?: return
        val settings = library.settings.value
        if (!settings.bool("lyrics_on", true)) { SongNow.setLyrics(SongNow.Lyrics.Idle("Lyrics are off in Settings.")); return }
        if (!NowPlayingRules.wantsLyrics(settings, station)) { SongNow.setLyrics(SongNow.Lyrics.Idle("Lyrics are off for this frequency.")); return }
        if (song.key == lyricsKey) return
        if (Connectivity.offline.value) {
            // No lookup while offline (task 214), and no web search either.
            SongNow.setLyrics(SongNow.Lyrics.Idle("You're offline, so lyrics can't be looked up right now."))
            return
        }
        lyricsKey = song.key
        SongNow.setLyrics(SongNow.Lyrics.Loading)
        scope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { lyricsClient.fetch(song.artist, song.title) }.getOrElse { LyricsResult.Failed(LyricsClient.BUSY) }
            }
            if (lyricsKey != song.key || stationId != song.stationId) return@launch
            SongNow.setLyrics(
                when (result) {
                    is LyricsResult.Found -> SongNow.Lyrics.Found(result)
                    is LyricsResult.Failed -> SongNow.Lyrics.Missing(result.message, LyricsSearch.url(song.artist, song.title))
                    LyricsResult.None -> SongNow.Lyrics.Missing("No lyrics found for this track.", LyricsSearch.url(song.artist, song.title))
                },
            )
            Log.i(TAG, "[lyrics] '${song.title}': ${when (result) { is LyricsResult.Found -> "${if (result.timed) "timed" else "plain"}, ${result.source}"; is LyricsResult.Failed -> result.message; else -> "none" }}")
        }
    }

    fun release() {
        job?.cancel()
    }

    companion object {
        private const val TAG = "Frequencies"
        private const val STUCK_MS = 60_000L
        /** Tap to speaker: the sink's buffer and the phone's own output, about. Tap a line to correct it. */
        const val OUTPUT_LATENCY_MS = 350L
    }
}
