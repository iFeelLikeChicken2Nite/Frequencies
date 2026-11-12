package com.frequencies.app.playback

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.MediaItem
import androidx.media3.common.Metadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.HttpDataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.ResolvingDataSource
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.exoplayer.source.LoadEventInfo
import androidx.media3.exoplayer.source.MediaLoadData
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.extractor.metadata.icy.IcyInfo
import androidx.media3.session.DefaultMediaNotificationProvider
import androidx.media3.session.LibraryResult
import androidx.media3.session.MediaLibraryService
import androidx.media3.session.MediaSession
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionCommands
import androidx.media3.session.SessionError
import androidx.media3.session.SessionResult
import androidx.media3.session.CommandButton
import android.os.Bundle
import com.frequencies.app.Library
import com.frequencies.app.TagFilter
import com.frequencies.app.R
import com.frequencies.app.accent
import com.frequencies.app.artists
import com.frequencies.app.Notices
import com.frequencies.app.health
import com.frequencies.app.schedule
import com.frequencies.app.library
import com.frequencies.app.localMusic
import com.frequencies.app.Connectivity
import com.frequencies.app.TwitchSource
import com.frequencies.app.ibroadcast
import com.frequencies.app.youtube
import com.frequencies.core.IBroadcast
import com.frequencies.core.Twitch
import com.frequencies.core.YouTube
import java.io.IOException
import com.frequencies.app.ui.MainActivity
import com.frequencies.core.Browse
import com.frequencies.core.Equalizer
import com.frequencies.core.Health
import com.frequencies.core.Recovery
import com.frequencies.core.NowPlayingRules
import com.frequencies.core.Playlists
import com.frequencies.core.Prebuffer
import com.frequencies.core.Shuffle
import kotlinx.serialization.json.JsonPrimitive
import com.frequencies.core.StationTls
import com.frequencies.core.StreamTitle
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.ListeningExecutorService
import com.google.common.util.concurrent.MoreExecutors
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient

/**
 * Plays the saved frequencies. Everything that controls playback (the
 * app's screens, the notification, the lock screen, Bluetooth and
 * headphone buttons, and Android Auto) talks to this one session,
 * so the session's play state is the truth, like the desktop's play
 * button.
 */
@OptIn(UnstableApi::class)
class PlaybackService : MediaLibraryService() {
    private lateinit var library: Library
    private lateinit var exo: ExoPlayer
    private lateinit var static: StaticPlayer
    private var session: MediaLibrarySession? = null
    private val handler = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    /** The streams a .pls/.m3u address lists, and which one is being tried. */
    private val candidates = ConcurrentHashMap<String, List<String>>()
    private val tryIndex = ConcurrentHashMap<String, Int>()

    /** The desktop's loudness leveling (task 250); what the station plays, for Song ID; and the station's EQ and level trim. */
    private val leveler = LevelProcessor()
    private val tap = AudioTap()
    private val eq = EqProcessor()
    private lateinit var songWatch: SongWatch
    /** The stream's own title for the current station, cleaned. */
    private var streamLine: String? = null
    /** Which half of a stream line is the artist (MusicBrainz), once known; null: the whole line is the title. */
    private val splits = ConcurrentHashMap<String, Pair<String, String>>()
    private val splitDone = ConcurrentHashMap.newKeySet<String>()
    private lateinit var artistWatch: ArtistWatch
    private val tuner = Tuner()
    /** Broken-certificate stations (the `insecure` flag): only their requests skip the check. */
    private lateinit var tls: StationTls.Clients
    private lateinit var router: StationTls.Router
    @Volatile private var insecureUrls: Set<String> = emptySet()
    /** Station id -> pre-buffer in ms, for the stations with "Pre-buffer this station" on (read on the playback thread). */
    @Volatile private var prebuffer: Map<String, Long> = emptyMap()
    /** Talk stations: muted, not paused, while navigation or the assistant speaks (core TalkDuck). */
    private lateinit var talkFocus: TalkFocus
    /** The station's volume as the static, fades and sleep timer want it; [talkMuted] silences it without losing it. */
    private var levelWanted = 1f
    private var level: Float
        get() = levelWanted
        set(v) { levelWanted = v; exo.volume = if (talkMuted) 0f else v }
    private var talkMuted = false
        set(v) { field = v; exo.volume = if (v) 0f else levelWanted }

    // -- local files stations and offline mode (task 214) --
    private lateinit var localMusic: com.frequencies.app.LocalMusic
    /** The song each local files station is on (resolve picks it), and the last one, so it doesn't repeat. */
    private val localSong = ConcurrentHashMap<String, Uri>()
    private val localLast = ConcurrentHashMap<String, Uri>()
    /** The song number in the item's address; a new one loads the next song. */
    private val localCount = ConcurrentHashMap<String, Int>()
    /** Tuned in just now: the song is joined partway through once it is ready. */
    private val localJoin = ConcurrentHashMap.newKeySet<String>()
    /** Stopped by the internet going; plays again when it is back. */
    private var offlineStopped: String? = null

    // -- streaming services (AP10b): iBroadcast, YouTube, Twitch --
    /** What each freqsvc:// link became (one per track: a seek reopens the same link and must get the same song). */
    private val svcPick = ConcurrentHashMap<String, Picked>()
    /** The link number in each station's item; a new one loads the next track. */
    private val svcCount = ConcurrentHashMap<String, Int>()
    /** Tuned in just now: the track is joined partway through once it is ready. */
    private val svcJoin = ConcurrentHashMap.newKeySet<String>()
    /**
     * A station going on with its next song or track (a music folder, an iBroadcast library, a YouTube
     * playlist): the seek back to its item is not a tune-in (build 198, task 242). It was taken for one,
     * so the static came on and the next YouTube video (or folder song) was joined at a random spot.
     */
    private var continuing: String? = null
    /** Where each iBroadcast station is in its queue (bridge's _ib_at), and the last YouTube video. */
    private val ibAt = ConcurrentHashMap<String, Int>()
    private val ytLast = ConcurrentHashMap<String, String>()
    /** A refused track was given its one retry (iBroadcast, Twitch); YouTube videos tried in a row. */
    private val retried = ConcurrentHashMap.newKeySet<String>()
    private val ytTries = ConcurrentHashMap<String, Int>()
    /** iBroadcast tracks refused in a row (build 176). */
    private val ibTries = ConcurrentHashMap<String, Int>()
    /** Songs of a music folder station that wouldn't play, in a row (build 198). */
    private val localTries = ConcurrentHashMap<String, Int>()

    // -- a station that stops by itself (tasks 239 and 240, build 198; core Recovery) --
    /** The station being reconnected and how many reconnects it has had since it last played steadily (any kind of drop, build 206). */
    private val tries = Recovery.Tries()
    /** Stations that failed one after another with nothing heard: a full lap and the phone stops. */
    private var failedInARow = 0
    private val reconnect = Runnable { reconnectNow() }
    /** Every failed load of the stream, Media3's own silent reconnects included (task 249, build 206). */
    private val breaks = object : AnalyticsListener {
        override fun onLoadError(
            eventTime: AnalyticsListener.EventTime,
            loadEventInfo: LoadEventInfo,
            mediaLoadData: MediaLoadData,
            error: IOException,
            wasCanceled: Boolean,
        ) = tuner.loadFailed(eventTime.windowIndex, error)
    }
    /** The current YouTube video's picture (its "stream" art). */
    private val svcThumb = ConcurrentHashMap<String, String>()
    private lateinit var resolving: ResolvingDataSource.Factory
    private val ad = AdCover()
    // Shuffle (comfort spec 7): the desktop's "shuffle" setting, shown to the notification and Android Auto as Media3's shuffle mode.
    private lateinit var stationPlayer: StationPlayer
    @Volatile private var shuffleOn = false
    // bridge._back_stack: Previous goes back to the frequency played before, shuffle or not.
    private val back = Shuffle.BackStack()
    private var lastId: String? = null
    private var goingBack = false
    // Android Auto (batch 24): whether the phone is on a car's screen, and the lists the car has open.
    private lateinit var carLink: CarLink
    @Volatile private var inCar = false
    /** The car's line under the song: the artist and station taking turns, and whose turn it is (user, build 197). */
    private var carParts: Pair<String, String>? = null
    private var carShowsStation = false
    private val carTurn = object : Runnable {
        override fun run() {
            if (!inCar) return
            val (artist, station) = carParts ?: return
            if (!NowPlayingRules.carTakesTurns(artist, station)) return
            carShowsStation = !carShowsStation
            swapCarLine()
            handler.postDelayed(this, NowPlayingRules.CAR_TURN_MS)
        }
    }
    private val browsed = ConcurrentHashMap.newKeySet<String>()
    /** The car started playing from Saved: its tag filter drives the queue ([followQueue]). */
    @Volatile private var carSavedQueue = false
    /**
     * The tag filter the car's queue follows: the app's one tag filter
     * ([TagFilter], user, build 189), set by the car's Tags tab, a filtered
     * Saved list, the ⋯ tag buttons and the phone's own tag pills alike.
     */
    private var carQuery: String
        get() = TagFilter.query.value
        set(value) { TagFilter.set(value) }
    /** A tag tile's tap is being answered: the player keeps what it is doing ([StationPlayer]). */
    private var keepPlaying = false
    /** The tags the car's ⋯ tag buttons were last offered for (their commands change with them). */
    private var carTags: List<String> = emptyList()
    /**
     * Android Auto's lists are worked out here, never on the main thread
     * (build 185: an 800-station Saved sorted on the main thread over and
     * over froze the app in the car, so the static never handed over).
     */
    private val browseThread: ListeningExecutorService =
        MoreExecutors.listeningDecorator(java.util.concurrent.Executors.newSingleThreadExecutor { r -> Thread(r, "car-browse") })
    /** Changes come in bursts (a tune-in, a sync): the car is told once they settle. */
    private val notifyLater = Runnable { notifyBrowsersNow(library.stations.value) }

    /** One service link's answer: the address to open, its headers, and what it is. */
    private class Picked(
        val station: String,
        val url: String = "",
        val headers: Map<String, String> = emptyMap(),
        val index: Int = -1,
        val title: String = "",
        val durationS: Double = 0.0,
        val video: String = "",
    )

    /** A music folder station with nothing to play (not one song that won't). */
    class NoSongs(message: String) : java.io.FileNotFoundException(message)

    /** A service said no: the reason in its own words (and whether a Twitch channel just isn't live). */
    class ServiceError(message: String, val notLive: Boolean = false, val signedOut: Boolean = false) : IOException(message)

    override fun onCreate() {
        super.onCreate()
        library = applicationContext.library
        static = StaticPlayer(this)

        val http = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            // A YouTube or Twitch link wants its own browser name; the stream agent stays for everything else.
            .addInterceptor { chain ->
                val request = chain.request()
                val agent = request.header(AGENT_HEADER)
                chain.proceed(if (agent == null) request else request.newBuilder().removeHeader(AGENT_HEADER).header("User-Agent", agent).build())
            }
            .build()
        tls = StationTls.Clients(http)
        router = StationTls.Router(tls)
        insecureUrls = Health.insecureUrls(library.stations.value)
        prebuffer = Prebuffer.targets(library.stations.value)
        scope.launch {
            library.stations.collect {
                insecureUrls = Health.insecureUrls(it)
                prebuffer = Prebuffer.targets(it)
                // "Talk radio" switched in Station settings while it plays: takes effect now, not on the next tune.
                if (::talkFocus.isInitialized) library.station(exo.currentMediaItem?.mediaId)?.let { s -> talkFocus.station(isTalk(s), s.displayName) }
            }
        }
        val upstream = OkHttpDataSource.Factory(router).setUserAgent(Playlists.STREAM_AGENT)
        localMusic = applicationContext.localMusic
        // Local files stations play content:// and file:// links; everything else goes to the web as before.
        val sources = DefaultDataSource.Factory(this, upstream)
        // Runs on the loading thread, so fetching a playlist (or a music folder) here is fine.
        resolving = ResolvingDataSource.Factory(sources) { spec -> resolve(spec) }

        // The leveling first, as on the desktop (dynaudnorm ahead of the EQ and trim); then the tap, so the
        // level meter hears what goes out when it goes out (the leveling holds 2.8 s back, see player.py's
        // build_filter_chain: a meter ahead of it hand the static over too early); then the EQ and trim.
        val renderers = object : DefaultRenderersFactory(this) {
            override fun buildAudioSink(
                context: Context,
                enableFloatOutput: Boolean,
                enableAudioTrackPlaybackParams: Boolean,
            ): AudioSink = DefaultAudioSink.Builder(context)
                .setAudioProcessors(arrayOf(leveler, tap, eq))
                .setEnableFloatOutput(enableFloatOutput)
                .setEnableAudioTrackPlaybackParams(enableAudioTrackPlaybackParams)
                .build()
        }
        exo = ExoPlayer.Builder(this, renderers)
            .setMediaSourceFactory(DefaultMediaSourceFactory(resolving))
            // Media3's own buffer, bigger only for the stations set to pre-buffer.
            .setLoadControl(PrebufferLoadControl { id -> prebuffer[id] ?: 0L })
            .setAudioAttributes(
                AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(),
                true,
            )
            .setHandleAudioBecomingNoisy(true)
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .build()
        talkFocus = TalkFocus(this, exo, handler) { on -> talkMuted = on }
        // The list wraps around for next and previous frequency.
        exo.repeatMode = Player.REPEAT_MODE_ALL
        // Songs (a music folder, iBroadcast, YouTube) play as they are; stations are leveled. Set before the
        // tuner hears of the change, and before the new sound reaches the leveler.
        exo.addListener(object : Player.Listener {
            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                leveler.songs = playsSongs(library.station(mediaItem?.mediaId))
            }
        })
        exo.addListener(tuner)
        exo.addListener(backWatch)
        // Media3 reconnects a broken stream by itself and tells only its analytics listeners (task 249).
        exo.addAnalyticsListener(breaks)

        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        shuffleOn = Shuffle.on(library.settings.value)
        stationPlayer = StationPlayer()
        // On a car's screen: the car's buttons and a little more static (batch 24). Ready before any controller connects.
        carLink = CarLink(this) { on -> handler.post { carChanged(on) } }
        carLink.start()
        session = MediaLibrarySession.Builder(this, stationPlayer, Callback())
            .setSessionActivity(open)
            .build()
        setMediaNotificationProvider(
            AccentNotifications(
                DefaultMediaNotificationProvider.Builder(this).build().apply {
                    setSmallIcon(R.drawable.ic_stat_frequencies)
                },
            ) { accent.color.value },
        )
        // The car's Favorite and Song ID buttons follow the song, its star and the frequency's Song ID switch.
        scope.launch {
            combine(SongNow.song, library.history, library.stations) { _, _, _ -> buttonState() }
                .distinctUntilChanged().drop(1).collect { refreshButtons() }
        }
        // Android Auto's Recent tab is the song history.
        scope.launch { library.history.drop(1).collect { notifyBrowsers(library.stations.value) } }
        // Shuffle switched in the app, by a restore or a sync: the notification and Android Auto follow.
        scope.launch {
            library.settings.map { Shuffle.on(it) }.distinctUntilChanged().collect { on -> if (on != shuffleOn) applyShuffle(on) }
        }
        // Saved's order in the car changed in Settings: Android Auto lists it again.
        scope.launch { library.settings.map { Browse.carOrder(it) }.distinctUntilChanged().drop(1).collect { notifyBrowsers(library.stations.value) } }
        // Android Auto shows the change at once: added, removed, renamed, retagged, reordered or played.
        scope.launch { library.stations.drop(1).collect { stations -> notifyBrowsers(stations) } }
        // One tag filter for the phone and the car (user, build 189): a tag changed on either side shows on the other at once.
        scope.launch { TagFilter.query.drop(1).collect { tagFilterChanged() } }

        songWatch = SongWatch(library, tap, scope) { refreshLine() }
        artistWatch = ArtistWatch(library, artists, scope)
        // A new photo pack or bio fixes: the Artist tab looks again.
        scope.launch { artists.packChanged.drop(1).collect { artistWatch.refresh() } }
        // Station settings saved (EQ, trim, song text, art) or Settings' ten-band switch: applied at once.
        scope.launch {
            library.stations.combine(library.settings) { _, _ -> }.drop(1).collect {
                applyEq()
                refreshLine()
            }
        }
        scope.launch { SoundPreview.current.collect { applyEq() } }
        // Settings' "Even out loudness between stations" (synced `normalize`, on unless turned off): heard at once.
        scope.launch {
            library.settings.map { it.bool("normalize", true) }.distinctUntilChanged().collect { on ->
                if (on != leveler.on) Log.i(TAG, "[level] even out loudness ${if (on) "on" else "off"}")
                leveler.on = on
                ad.leveling(on)
            }
        }
        // Android Auto's Live now tab follows the Schedule, minute by minute.
        scope.launch {
            applicationContext.schedule.live.map { live -> live.map { it.station.id } }.distinctUntilChanged().drop(1).collect { notifyBrowsers(library.stations.value) }
        }
        // Started for Android Auto or a media button, the launch check runs here too (once per launch).
        applicationContext.health.launchCheckOnce()
        // The internet went or came back (task 214).
        scope.launch { Connectivity.offline.drop(1).collect { offlineChanged(it) } }
        handler.post(everySecond)
    }

    /** Song ID's clock and the sleep timer. */
    private val everySecond = object : Runnable {
        override fun run() {
            if (exo.isPlaying) songWatch.tick()
            tuner.watchStream()
            checkSleep()
            handler.postDelayed(this, 1000)
        }
    }

    private fun applyEq() {
        val station = library.station(exo.currentMediaItem?.mediaId)
        if (station == null) { eq.set(emptyList(), 0.0); return }
        SoundPreview.current.value?.takeIf { it.stationId == station.id }?.let { eq.set(it.bands, it.gainDb); return }
        eq.set(Equalizer.bands(station, library.settings.value.bool("eq_advanced")), Equalizer.gainDb(station))
    }

    // -- sleep timer ---------------------------------------------------------

    private var sleepFade: Runnable? = null

    private fun checkSleep() {
        val ends = SleepTimer.endsAt.value ?: return
        if (SystemClock.elapsedRealtime() < ends) return
        SleepTimer.cancel()
        if (!exo.playWhenReady) return
        sleepNow()
    }

    /** desktop._sleep_now: fade out, or over to a frequency chosen for the small hours. */
    private fun sleepNow() {
        val settings = library.settings.value
        val action = settings.string("sleep_action", "fade")
        val target = when (action) {
            "switch" -> library.station(settings.string("sleep_wake_station_id").ifEmpty { null })
            "tag" -> {
                val tag = settings.string("sleep_wake_tag").trim()
                val current = exo.currentMediaItem?.mediaId
                val pool = library.stations.value.filter { s -> tag.isNotEmpty() && s.tags.any { it.equals(tag, ignoreCase = true) } }
                (pool.filter { it.id != current }.ifEmpty { pool }).randomOrNull()
            }
            else -> null
        }
        val seconds = settings.double("sleep_fade_seconds", 20.0).toInt().coerceAtLeast(if (target != null) 4 else 1)
        val steps = seconds * 4
        var i = 0
        Log.i(TAG, "[sleep] timer ended: ${if (target != null) "over to ${target.displayName}" else "fading out"} over $seconds s")
        sleepFade?.let(handler::removeCallbacks)
        val fade = object : Runnable {
            override fun run() {
                i++
                if (target == null) {
                    level = (1f - i.toFloat() / steps).coerceIn(0f, 1f)
                    if (i >= steps) { exo.pause(); level = 1f; sleepFade = null; return }
                } else {
                    val half = steps / 2
                    if (i <= half) level = (1f - i.toFloat() / half).coerceIn(0f, 1f)
                    if (i == half) tuneTo(target)
                    if (i > half) level = ((i - half).toFloat() / (steps - half)).coerceIn(0f, 1f)
                    if (i >= steps) { level = 1f; sleepFade = null; return }
                }
                handler.postDelayed(this, 250)
            }
        }
        sleepFade = fade
        handler.postDelayed(fade, 250)
    }

    /** Over to [station]: in the queue if it is there, else with the saved list queued. */
    private fun tuneTo(station: com.frequencies.core.Station) {
        val index = (0 until exo.mediaItemCount).firstOrNull { exo.getMediaItemAt(it).mediaId == station.id }
        if (index != null) {
            exo.seekToDefaultPosition(index)
        } else {
            val list = library.sorted()
            exo.setMediaItems(list.map { library.mediaItem(it) }, list.indexOfFirst { it.id == station.id }.coerceAtLeast(0), C.TIME_UNSET)
            exo.prepare()
        }
        exo.play()
    }

    private fun notifyBrowsers(@Suppress("UNUSED_PARAMETER") stations: List<com.frequencies.core.Station>) {
        handler.removeCallbacks(notifyLater)
        handler.postDelayed(notifyLater, NOTIFY_SETTLE_MS)
    }

    /** Counted on the browse thread, then told to the car from the main thread. */
    private fun notifyBrowsersNow(stations: List<com.frequencies.core.Station>) {
        if (session == null || browseThread.isShutdown) return
        browseThread.execute {
            val counts = LinkedHashMap<String, Int>()
            counts[ROOT_ID] = library.tabs().size
            counts[Browse.SAVED] = stations.size
            counts[Browse.RECENT] = Browse.recentSongs(stations, library.history.value).size
            // Saved with a tag filter and the A-Z parts the car has opened.
            browsed.forEach { counts[it] = 1 }
            counts[Browse.LIVE] = Browse.live(stations).size
            val tags = Browse.tags(stations)
            counts[Browse.TAGS] = tags.size
            tags.forEach { counts[Browse.tagListId(it)] = Browse.tagged(stations, it).size }
            handler.post {
                val s = session ?: return@post
                counts.forEach { (id, n) -> s.notifyChildrenChanged(id, n, null) }
            }
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaLibrarySession? = session

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        browseThread.shutdownNow()
        if (::carLink.isInitialized) carLink.stop()
        talkFocus.shutdown()
        ad.release()
        songWatch.release()
        artistWatch.release()
        scope.cancel()
        static.release()
        session?.run {
            player.release()
            release()
        }
        session = null
        super.onDestroy()
    }

    /**
     * The song line, the artist and the picture, for the notification, the
     * lock screen, Bluetooth, Android Auto and the app: the station's own
     * choice of Song ID's answer, its custom text and the stream's title
     * (mainwindow._readout_choice), and of the song's cover, its own
     * picture and the stream's logo (feeds.refresh_artwork). The item's own
     * metadata wins over the stream's in ExoPlayer, so the current item is
     * replaced in place (same address: playback goes on undisturbed).
     */
    private fun refreshLine() {
        val index = exo.currentMediaItemIndex
        val current = exo.currentMediaItem ?: return
        val station = library.station(current.mediaId) ?: return
        val base = library.mediaItem(station)
        val name = station.displayName
        val song = SongNow.song.value?.takeIf { it.stationId == station.id }
        val stream = streamLine?.takeUnless { it.isBlank() || it.equals(name, ignoreCase = true) }
        val split = { line: String -> splits[line] }
        val readout = NowPlayingRules.readout(station, song?.let { it.title to it.artist }, stream, split)
        // The Artist tab follows Song ID's artist, else the stream's own once MusicBrainz has said which half it is.
        artistWatch.artist(song?.artist ?: stream?.let(split)?.first, song?.coverUrl)
        val builder = base.mediaMetadata.buildUpon()
        if (readout != null && !(readout.from == "custom" && readout.title == name)) {
            val artist = readout.artist.ifEmpty { name }
            // A new song starts its turns with the artist.
            if ("$artist|$name" != carTurnKey) carShowsStation = false
            builder.setTitle(readout.title)
                .setArtist(artist)
                .setAlbumTitle(song?.album?.takeIf { it.isNotEmpty() && readout.from == "shazam" } ?: name)
                // Android Auto shows a title and one line under it: the artist and always the station (user, batch 24).
                .setDisplayTitle(readout.title)
                .setSubtitle(carSubtitle(artist, name))
            carParts = artist to name
        } else carParts = null
        restartCarTurns()
        builder.setArtworkUri(artFor(station, song))
        val meta = builder.build()
        // Follow the artwork: the picture showing now picks the accent.
        accent.artwork(meta.artworkUri)
        val old = current.mediaMetadata
        if (old.title == meta.title && old.artist == meta.artist && old.artworkUri == meta.artworkUri && old.albumTitle == meta.albumTitle &&
            old.displayTitle == meta.displayTitle) {
            // Only the car's line differs: the session's metadata alone, never a new item (task 240).
            if (old.subtitle != meta.subtitle && ::stationPlayer.isInitialized) stationPlayer.showCarLine(current.mediaId, meta.subtitle?.toString())
            return
        }
        if (::stationPlayer.isInitialized) stationPlayer.setCarLine(current.mediaId, meta.subtitle?.toString())
        // Same address as playing now (a local files station's song number included): playback goes on undisturbed.
        exo.replaceMediaItem(index, current.buildUpon().setMediaMetadata(meta).build())
    }

    /** artistinfo.split_artist_title, once per line: MusicBrainz says which half is the artist. */
    private fun checkSplit(line: String) {
        if (line.isBlank() || StreamTitle.split(line) == null || !splitDone.add(line)) return
        if (splitDone.size > 300) { splitDone.clear(); splits.clear(); splitDone.add(line) }
        scope.launch {
            val found = kotlinx.coroutines.withContext(Dispatchers.IO) { artists.info.splitArtistTitle(line) }
            if (found != null) splits[line] = found
            Log.i(TAG, "[artist] stream title '$line': " + if (found == null) "neither half is an artist MusicBrainz knows; shown whole" else "artist '${found.first}', title '${found.second}'")
            if (streamLine == line) refreshLine()
        }
    }

    /** Android Auto's one-tap thumbs down: files "Both" (the Bio/Photo question stays on the phone). */
    private fun thumbsDownFromCar(): Boolean {
        val panel = ArtistNow.state.value.panel?.takeIf { it.canJudge && ArtistNow.state.value.stationId == exo.currentMediaItem?.mediaId }
        if (panel == null) {
            Log.i(TAG, "[auto] thumbs down: nothing to report (no artist biography showing)")
            return false
        }
        Log.i(TAG, "[auto] thumbs down for '${panel.name}' (both)")
        ArtistNow.verdict.value = "down"
        // One tap in the car files "Both" at once, from what is showing now (queued on disk like the phone's).
        artists.report(com.frequencies.core.ThumbsSnapshot.of(panel, artists.stationName()), "both", holdSeconds = 0, canOpenBrowser = false)
        return true
    }

    /** feeds.refresh_artwork: the first source in the station's order that has a picture. */
    private fun artFor(station: com.frequencies.core.Station, song: SongNow.Song?): Uri? {
        val order = NowPlayingRules.artOrder(station)
        for (source in order) {
            when (source) {
                "shazam" -> song?.coverUrl?.takeIf { it.startsWith("https://") || it.startsWith("http://") }?.let { return Uri.parse(it) }
                "custom" -> if (station.customArtPath.isNotEmpty()) library.sharedArtUri(station)?.let { return it }
                "stream" -> (svcThumb[station.id] ?: station.favicon).takeIf { it.startsWith("https://") }?.let { return Uri.parse(it) }
            }
        }
        // Fallback off and the chosen source empty: the station's tile, as the desktop shows.
        return if (order.size == 1) null else library.sharedArtUri(station)
    }

    private fun resolve(spec: DataSpec): DataSpec {
        if (spec.uri.scheme == LOCAL_SCHEME) return spec.withUri(localSongFor(localStationId(spec.uri)))
        if (spec.uri.scheme == SERVICE_SCHEME) return serviceSpec(spec)
        val url = spec.uri.toString()
        // A station's own stream and playlist: marked for the router (StationTls), never any other request.
        // Every station's since build 170 (StationTls.EVERY_STATION).
        val flagged = StationTls.EVERY_STATION || url in insecureUrls
        fun marked(s: DataSpec) = if (flagged) s.withAdditionalHeaders(mapOf(StationTls.MARK to "1")) else s
        if (!Playlists.looksLikePlaylist(url)) return marked(spec)
        val list = candidates.getOrPut(url) { Playlists.resolve(url, if (flagged) tls.insecure else Playlists.defaultClient) }
        val pick = list[(tryIndex[url] ?: 0) % list.size]
        Log.i(TAG, "[stream] playlist $url -> $pick (${list.size} listed)${if (flagged) ", certificate not checked" else ""}")
        return marked(spec.withUri(Uri.parse(pick)))
    }

    /**
     * bridge._source_for: the song a local files station is on, picked at
     * random from its folder (never the one just played) when it starts or
     * a song ends. Runs on the loading thread: a big folder takes a moment.
     */
    private fun localSongFor(id: String): Uri {
        localSong[id]?.let { return it }
        val station = library.station(id) ?: throw NoSongs("no such station")
        val pick = com.frequencies.core.LocalStations.pick(localMusic.tracks(station), localLast[id])
            ?: throw NoSongs("no playable audio files")
        localSong[id] = pick
        localLast[id] = pick
        Log.i(TAG, "[local] '${station.displayName}': ${pick.lastPathSegment?.substringAfterLast('/')}")
        return pick
    }

    // -- streaming services ---------------------------------------------------------

    /**
     * A service station's link, made now on the loading thread: an
     * iBroadcast track signed with the sign-in as it is right now
     * (ibroadcast.current_token: every open signs again, so a renewed
     * token is used at once), a YouTube video resolved by NewPipeExtractor,
     * a Twitch channel's audio-only stream from its playback token.
     */
    private fun serviceSpec(spec: DataSpec): DataSpec {
        val key = spec.uri.toString()
        val id = localStationId(spec.uri)
        val station = library.station(id) ?: throw ServiceError("That frequency is gone.")
        return when {
            IBroadcast.isIBroadcast(station) -> {
                val pick = svcPick.getOrPut(key) { pickIBroadcast(station, key) }
                val token = applicationContext.ibroadcast.account.currentToken()
                if (token.isEmpty()) throw ServiceError("Sign in to iBroadcast in Settings to play ${station.displayName}.", signedOut = true)
                spec.withUri(Uri.parse(IBroadcast.fresh(pick.url, station, token)))
            }
            YouTube.isYouTube(station) -> {
                val pick = svcPick.getOrPut(key) { pickYouTube(station, key) }
                spec.withUri(Uri.parse(pick.url)).withAdditionalHeaders(pick.headers)
            }
            Twitch.isTwitch(station) -> {
                val pick = svcPick.getOrPut(key) { pickTwitch(station) }
                spec.withUri(Uri.parse(pick.url)).withAdditionalHeaders(pick.headers)
            }
            else -> throw ServiceError("${station.displayName} isn't a streaming service frequency.")
        }
    }

    /** bridge._next_ibroadcast_url: the next track (at random when it shuffles), named from the saved rows. */
    private fun pickIBroadcast(station: com.frequencies.core.Station, key: String): Picked {
        val urls = IBroadcast.urls(station)
        if (urls.isEmpty()) throw ServiceError("${station.displayName} has no tracks saved. Add it again from iBroadcast.")
        val shuffle = IBroadcast.shuffles(station, library.settings.value)
        val at = IBroadcast.nextIndex(urls.size, ibAt[station.id] ?: -1, shuffle)
        ibAt[station.id] = at
        Log.i(TAG, "[ibroadcast] '${station.displayName}': track ${at + 1} of ${urls.size}${if (shuffle) " (shuffled)" else ""}: ${IBroadcast.shown(urls[at])}")
        IBroadcast.track(station, at)?.let { t -> handler.post { announceTrack(key, station.id, t) } }
        return Picked(station.id, url = urls[at], index = at)
    }

    /** workers.YouTubeResolver: any video but the last one, resolved now (its link expires). */
    private fun pickYouTube(station: com.frequencies.core.Station, key: String): Picked {
        val playlist = YouTube.playlistOf(station)
        try {
            val source = applicationContext.youtube
            val (entries, _) = source.entries(playlist)
            val entry = YouTube.pick(entries, ytLast[station.id]) ?: throw ServiceError("No playable videos in that playlist.")
            ytLast[station.id] = entry.id
            val audio = source.resolve(entry.id)
            Log.i(TAG, "[youtube] '${station.displayName}': ${entry.title.take(60)} (${entries.size} in the playlist)")
            handler.post {
                if (exo.currentMediaItem?.localConfiguration?.uri?.toString() != key) return@post
                if (audio.thumbnail.isNotEmpty()) svcThumb[station.id] = audio.thumbnail
                // A video's title is the best "now playing" a YouTube station has (no ICY to read).
                streamLine = StreamTitle.clean(audio.title.ifEmpty { entry.title })
                refreshLine()
                streamLine?.let { checkSplit(it) }
            }
            return Picked(station.id, url = audio.url, headers = audio.headers.agentMarked(), title = audio.title, durationS = audio.duration, video = entry.id)
        } catch (e: YouTube.YouTubeError) {
            throw ServiceError(e.message ?: "Couldn't play that playlist.")
        } catch (e: ServiceError) {
            throw e
        } catch (e: Exception) {
            throw ServiceError("Couldn't reach YouTube: ${e.message ?: e.javaClass.simpleName}")
        }
    }

    /** twitch.resolve: the channel's audio-only stream (the best one when it offers none). */
    private fun pickTwitch(station: com.frequencies.core.Station): Picked {
        val channel = Twitch.channelOf(station)
        val found = try {
            TwitchSource.resolver.resolve(channel)
        } catch (e: Twitch.TwitchError) {
            Log.i(TAG, "[twitch] $channel: ${e.message}")
            throw ServiceError(if (e.notLive) "${station.displayName} isn't live right now." else "${station.displayName}: ${e.message}", notLive = e.notLive)
        } catch (e: Exception) {
            throw ServiceError("Couldn't reach Twitch: ${e.message ?: e.javaClass.simpleName}")
        }
        Log.i(TAG, "[twitch] $channel: live, playing '${found.rendition}' (${found.renditions} offered)")
        return Picked(station.id, url = found.url, headers = Twitch.HEADERS.agentMarked(Twitch.BROWSER_AGENT))
    }

    private fun Map<String, String>.agentMarked(agent: String? = null): Map<String, String> {
        val ua = agent ?: this["User-Agent"] ?: return this
        return (this - "User-Agent") + (AGENT_HEADER to ua)
    }

    /** bridge._announce_ibroadcast: the track's names, as the station saved them. */
    private fun announceTrack(key: String, id: String, t: IBroadcast.Track) {
        if (exo.currentMediaItem?.localConfiguration?.uri?.toString() != key) return
        songWatch.known(id, t.title, t.artist, t.album, t.cover, exo.currentPosition.coerceAtLeast(0))
    }

    /** Forget what a station's links became, so the next play picks again. */
    private fun forgetPicks(id: String) {
        svcPick.entries.removeIf { it.value.station == id }
        svcThumb.remove(id)
    }

    /** A service track ended (or failed): the next one, from the top, no static (as a folder's next song). */
    private fun nextServiceTrack(index: Int, id: String) {
        forgetPicks(id)
        svcJoin.remove(id)
        val n = (svcCount[id] ?: 0) + 1
        svcCount[id] = n
        val item = exo.getMediaItemAt(index)
        exo.replaceMediaItem(index, item.buildUpon().setUri(serviceUri(id, n)).build())
        goOn(id, index)
        // The last track's song, lyrics, artist and picture go now; the next one's come as it is named (task 244).
        newTrack(id)
        exo.prepare()
        exo.play()
    }

    /**
     * A service station's failure, the desktop's way: an iBroadcast track
     * refused renews the sign-in and is tried once more before the station
     * is marked down; a YouTube video that won't play makes way for
     * another (three in a row and the station is down); a Twitch channel
     * that isn't live says so; a Twitch stream that drops is looked up
     * again once. Returns false when it isn't one.
     */
    private fun serviceFailed(station: com.frequencies.core.Station, error: PlaybackException): Boolean {
        if (!isService(station)) return false
        val said = generateSequence(error as Throwable) { it.cause }.filterIsInstance<ServiceError>().firstOrNull()
        val http = generateSequence(error.cause) { it.cause }.filterIsInstance<HttpDataSource.InvalidResponseCodeException>().firstOrNull()
        val id = station.id
        val name = station.displayName
        fun down(reason: String, notice: String) {
            // The screens show this rather than the player's own words (build 176).
            ServiceProblems.set(id, notice)
            tuner.stopTune()
            if (!library.isPreview(id)) library.markFailed(id, reason)
            Notices.say(notice)
            // Never left on its error (task 239): the next station takes over, or a clean stop.
            if (library.isPreview(id)) ServiceProblems.playerError = exo.playerError
            else if (exo.currentMediaItem?.mediaId == id) moveOn(id)
        }
        when {
            said?.signedOut == true -> {
                val notice = said?.message ?: "Sign in to iBroadcast in Settings."
                ServiceProblems.set(id, notice)
                tuner.stopTune()
                Notices.say(notice)
                if (library.isPreview(id)) ServiceProblems.playerError = exo.playerError else moveOn(id)
            }
            said?.notLive == true -> down("not live", said?.message ?: "$name isn't live right now.")
            IBroadcast.isIBroadcast(station) -> {
                // Build 176: iBroadcast's own reason, logged and shown, and one refused track no longer takes the station down.
                val body = http?.responseBody?.let { String(it, Charsets.UTF_8).take(400) }.orEmpty()
                val reason = http?.let { IBroadcast.refusal(it.responseCode, body) }
                    ?: "iBroadcast's track wouldn't play (${error.errorCodeName})."
                val track = ibAt[id]?.let { "track ${it + 1}" } ?: "a track"
                // Shown at once, while the retry runs, in place of "The station's server said no."
                ServiceProblems.set(id, reason)
                Log.i(TAG, "[ibroadcast] '$name': $track refused (${http?.let { "HTTP ${it.responseCode}" + IBroadcast.said(body).let { s -> if (s.isEmpty()) "" else ": $s" } } ?: error.errorCodeName})")
                when {
                    // The sign-in may be why: renew it and try the same track once more (the desktop's retry).
                    (http == null || IBroadcast.signInRefusal(http.responseCode)) && retried.add(id) -> {
                        Log.i(TAG, "[ibroadcast] '$name': renewing the sign-in and trying once more")
                        scope.launch {
                            val renewal = applicationContext.ibroadcast.renew(force = true)
                            if (renewal.token.isEmpty()) { down("sign-in", IBroadcast.renewalProblem(renewal.problem)); return@launch }
                            if (exo.currentMediaItem?.mediaId != id) return@launch
                            exo.prepare()
                            exo.play()
                        }
                    }
                    else -> {
                        // That track (a file iBroadcast can't send, or gone): another one, up to TRACK_TRIES in a row.
                        val tries = (ibTries[id] ?: 0) + 1
                        ibTries[id] = tries
                        val index = exo.currentMediaItemIndex
                        if (tries < IBroadcast.TRACK_TRIES && index >= 0) {
                            Log.i(TAG, "[ibroadcast] '$name': trying another track ($tries of ${IBroadcast.TRACK_TRIES} refused)")
                            nextServiceTrack(index, id)
                        } else {
                            down(http?.let { "HTTP ${it.responseCode}" } ?: "refused", if (tries > 1) "$name: ${IBroadcast.TRACK_TRIES} tracks in a row wouldn't play. $reason" else reason)
                        }
                    }
                }
            }
            YouTube.isYouTube(station) -> {
                val tries = (ytTries[id] ?: 0) + 1
                ytTries[id] = tries
                val index = exo.currentMediaItemIndex
                if (tries < YT_TRIES && index >= 0) {
                    Log.i(TAG, "[youtube] '$name': ${said?.message ?: error.errorCodeName}; trying another video")
                    nextServiceTrack(index, id)
                } else {
                    down("no audio", said?.message?.let { "$name: $it" } ?: "$name: YouTube wouldn't play these videos.")
                }
            }
            Twitch.isTwitch(station) -> {
                if (said == null && retried.add(id)) {
                    Log.i(TAG, "[twitch] '$name': the stream dropped (${error.errorCodeName}); looking it up again")
                    forgetPicks(id)
                    exo.prepare()
                    exo.play()
                } else {
                    down("could not connect", said?.message ?: "$name: Twitch stopped sending the stream.")
                }
            }
        }
        return true
    }

    /** The station [id] goes on with another track: what the last one showed is forgotten (task 244). */
    private fun newTrack(id: String) {
        songWatch.newTrack(id)
        if (exo.currentMediaItem?.mediaId != id) return
        streamLine = null
        refreshLine()
    }

    /** Back to the station's own item after the list moved on by itself, marked as the same station going on. */
    private fun goOn(id: String, index: Int) {
        continuing = id
        exo.seekToDefaultPosition(index)
        // The seek's transition has been handled by now (or there was none).
        handler.post { if (continuing == id) continuing = null }
    }

    /**
     * A station stopped by itself (tasks 239 and 240): one that was playing (or is being reconnected)
     * gets its reconnects, a little apart, while the car and the phone show it loading, and so does one
     * the network failed; one that never played, or a refusal that asking again won't change, moves on
     * at once. Never left on its error.
     */
    private fun recover(id: String, error: PlaybackException?, wasPlaying: Boolean) {
        handler.removeCallbacks(reconnect)
        val network = error?.errorCode in NETWORK_TROUBLE
        val wait = Recovery.nextWait(tries.of(id), error?.let(::httpCode), wasPlaying, network)
        if (wait != null && exo.playWhenReady) {
            tries.use(id, SystemClock.elapsedRealtime())
            Log.i(TAG, "[stream] dropped: reconnecting in ${wait / 1000} s (try ${tries.count} of ${Recovery.WAITS_MS.size})")
            stationPlayer.recovering = true
            handler.postDelayed(reconnect, wait)
            return
        }
        moveOn(id)
    }

    /** The wait is over: the same station, connected afresh (the first listed server again). */
    private fun reconnectNow() {
        val id = tries.stationId
        if (id == null || exo.currentMediaItem?.mediaId != id || !exo.playWhenReady || exo.isPlaying) {
            // Gone to another station, paused, or it came back by itself.
            stationPlayer.recovering = false
            return
        }
        Log.i(TAG, "[stream] reconnecting to '${library.station(id)?.displayName ?: id}' (try ${tries.count} of ${Recovery.WAITS_MS.size})")
        exo.currentMediaItem?.localConfiguration?.uri?.toString()?.let { url ->
            tryIndex.remove(url)
            candidates.remove(url)
        }
        if (exo.playbackState != Player.STATE_IDLE) exo.stop()
        exo.prepare()
        tuner.resetWatch()
        stationPlayer.recovering = false
    }

    /**
     * A station that can't go on: the next one that can play takes over, the way a stream that never
     * started always has ("Skip broken stations"). With that off, nothing else to go to, or a full lap
     * of stations failing in a row, it stops cleanly and says so: Play tries again.
     */
    private fun moveOn(id: String) {
        handler.removeCallbacks(reconnect)
        stationPlayer.recovering = false
        tries.forget()
        failedInARow++
        val count = exo.mediaItemCount
        val skips = library.settings.value.bool("auto_skip_broken", true) && Health.skippedWhenDown(library.station(id))
        if (exo.playWhenReady && skips && count > 1 && Recovery.keepMovingOn(failedInARow, count)) {
            val before = exo.currentMediaItemIndex
            step(+1, "Next")
            if (exo.currentMediaItemIndex != before) {
                exo.prepare()
                exo.play()
                return
            }
        }
        val lap = count > 1 && !Recovery.keepMovingOn(failedInARow, count)
        failedInARow = 0
        // The phone's own screens say what went wrong; the car sees the station paused.
        ServiceProblems.playerError = exo.playerError
        if (exo.playWhenReady) {
            Log.i(TAG, "[stream] stopped: ${if (lap) "every station failed in a row" else "nothing to move on to"}")
            Notices.say(Recovery.stoppedNotice(library.station(id)?.displayName ?: "The station", lap))
        }
        exo.pause()
    }

    private fun httpCode(error: PlaybackException): Int? =
        generateSequence(error.cause) { it.cause }.filterIsInstance<HttpDataSource.InvalidResponseCodeException>().firstOrNull()?.responseCode

    /** A local files station's song ended: the next one, from the top (desktop task 22), no static. */
    private fun nextLocalSong(index: Int, id: String) {
        localSong.remove(id)
        localJoin.remove(id)
        val n = (localCount[id] ?: 0) + 1
        localCount[id] = n
        val item = exo.getMediaItemAt(index)
        exo.replaceMediaItem(index, item.buildUpon().setUri(localUri(id, n)).build())
        goOn(id, index)
        exo.prepare()
        exo.play()
    }

    /** A talk station: the shipped list's (dev builds mark them) or the user's own "Talk radio" switch. */
    private fun isTalk(s: com.frequencies.core.Station): Boolean = s.talkRadio || artists.overrides.isTalkStation(s)

    /** Songs that end and go on to the next: a local files station, iBroadcast or YouTube (not leveled, task 250). */
    private fun playsSongs(s: com.frequencies.core.Station?): Boolean =
        s != null && (com.frequencies.core.LocalStations.isLocal(s) || playsTracks(s))

    /** Why a station can't play now (offline, or its music isn't on this phone), or null. */
    private fun blocked(id: String?): String? = localMusic.blocked(library.station(id))

    /**
     * mainwindow._offline_changed: offline, a station that needs the
     * internet moves over to a local files station (the next one in the
     * list, else any saved one on this phone) or stops; back online,
     * everything is available again and a stopped station plays again.
     */
    private fun offlineChanged(offline: Boolean) {
        notifyBrowsers(library.stations.value)
        val current = library.station(exo.currentMediaItem?.mediaId)
        if (!offline) {
            // No "Back online" message (user, build 170): the grayed-out rows coming back say it.
            val stopped = offlineStopped
            offlineStopped = null
            if (stopped != null && current?.id == stopped && !exo.playWhenReady) {
                exo.prepare()
                exo.play()
            }
            return
        }
        // The failures just before were the connection going, not the stations.
        library.forgetRecentFailures(60.0)
        val playing = exo.playWhenReady && current != null && com.frequencies.core.OfflineRules.needsInternet(current)
        if (!playing) {
            Notices.say(com.frequencies.core.OfflineRules.BANNER)
            return
        }
        val count = exo.mediaItemCount
        val here = exo.currentMediaItemIndex
        val inQueue = (1 until count).map { (here + it) % count }.firstOrNull { blocked(exo.getMediaItemAt(it).mediaId) == null }
        val local = inQueue?.let { library.station(exo.getMediaItemAt(it).mediaId) }
            ?: library.sorted().firstOrNull { it.id != current!!.id && com.frequencies.core.LocalStations.isLocal(it) && localMusic.onThisPhone(it) }
        if (local != null) {
            Notices.say(com.frequencies.core.OfflineRules.switched(local.displayName))
            if (inQueue != null) exo.seekToDefaultPosition(inQueue) else tuneTo(local)
            exo.play()
            return
        }
        offlineStopped = current!!.id
        exo.pause()
        Notices.say(com.frequencies.core.OfflineRules.stopped(current.displayName))
    }

    /** When the streams went quiet (elapsedRealtime), 0 while one plays (user, build 197). */
    private var weakSince = 0L
    private val weakCheck = Runnable { weakSwitch() }

    /**
     * A stream just failed or stalled, quiet since [silentSince]. If no
     * stream plays again within [WEAK_SILENCE_MS] (15 s, user) while the
     * phone still says it's online, that's most likely the reception, not
     * the stations: a local files station takes over ([weakSwitch]).
     */
    private fun weakReception(failedId: String, silentSince: Long) {
        if (!com.frequencies.core.OfflineRules.needsInternet(library.station(failedId))) return
        if (weakSince != 0L) return
        weakSince = silentSince
        handler.removeCallbacks(weakCheck)
        handler.postDelayed(weakCheck, (weakSince + WEAK_SILENCE_MS - SystemClock.elapsedRealtime()).coerceAtLeast(0))
    }

    /** A stream plays again: no switch. */
    private fun weakClear() {
        weakSince = 0L
        handler.removeCallbacks(weakCheck)
    }

    /**
     * 15 s without a stream playing: as when the internet goes, those
     * failures are forgotten and a local files station plays (the next one
     * in the list, else the first saved one whose music is on this phone);
     * it stays on when reception is back.
     */
    private fun weakSwitch() {
        weakSince = 0L
        if (!exo.playWhenReady) return
        val current = library.station(exo.currentMediaItem?.mediaId) ?: return
        if (!com.frequencies.core.OfflineRules.needsInternet(current)) return
        fun playsHere(s: com.frequencies.core.Station?) =
            s != null && s.id != current.id && com.frequencies.core.LocalStations.isLocal(s) && localMusic.onThisPhone(s)
        val count = exo.mediaItemCount
        val here = exo.currentMediaItemIndex
        val inQueue = (1 until count).map { (here + it) % count }.firstOrNull { playsHere(library.station(exo.getMediaItemAt(it).mediaId)) }
        val local = inQueue?.let { library.station(exo.getMediaItemAt(it).mediaId) } ?: library.sorted().firstOrNull { playsHere(it) }
        if (local == null) {
            Log.i(TAG, "[offline] no stream played for ${WEAK_SILENCE_MS / 1000}s, and no music folder station is on this phone")
            return
        }
        library.forgetRecentFailures(WEAK_SILENCE_MS / 1000.0 + 15)
        Log.i(TAG, "[offline] no stream played for ${WEAK_SILENCE_MS / 1000}s: weak reception, switching to '${local.displayName}'")
        Notices.say("Weak signal: switched to ${local.displayName}.")
        if (inQueue != null) exo.seekToDefaultPosition(inQueue) else tuneTo(local)
        exo.prepare()
        exo.play()
    }

    private inner class Callback : MediaLibrarySession.Callback {
        /** The car's and the notification's own buttons (Favorite, Song ID, Shuffle); thumbs down stays a command the car may still send. */
        override fun onConnect(session: MediaSession, controller: MediaSession.ControllerInfo): MediaSession.ConnectionResult {
            val car = isCar(session, controller)
            // Android Auto on a phone reaches us as an old-style controller and sees the notification's buttons,
            // so those become the car's while the phone is on a car's screen (batch 24). Every controller may
            // press them, since the system's own controls reach us as controllers of their own.
            if (session.isAutoCompanionController(controller)) carLink.check()
            carTags = Browse.tags(library.stations.value)
            val result = MediaSession.ConnectionResult.AcceptedResultBuilder(session)
                .setAvailableSessionCommands(sessionCommands())
                .setAvailablePlayerCommands(playerCommandsFor(session, controller))
            if (car || session.isMediaNotificationController(controller)) result.setMediaButtonPreferences(buttonsFor(car || inCar))
            return result.build()
        }

        override fun onCustomCommand(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
            customCommand: SessionCommand,
            args: Bundle,
        ): ListenableFuture<SessionResult> {
            when (customCommand.customAction) {
                SHUFFLE.customAction -> setShuffle(!shuffleOn)
                THUMBS_DOWN.customAction -> thumbsDownFromCar()
                FAVORITE.customAction -> favoriteFromCar()
                SONG_ID.customAction -> songIdFromCar()
                else -> {
                    val tag = customCommand.customAction.takeIf { it.startsWith(TAG_ACTION) }?.removePrefix(TAG_ACTION)
                        ?: return super.onCustomCommand(session, controller, customCommand, args)
                    tagFromCar(tag)
                }
            }
            return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
        }

        override fun onGetLibraryRoot(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            params: LibraryParams?,
        ): ListenableFuture<LibraryResult<MediaItem>> = Futures.immediateFuture(
            LibraryResult.ofItem(rootItem(), LibraryParams.Builder().setExtras(rootExtras()).build()),
        )

        override fun onGetChildren(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            parentId: String,
            page: Int,
            pageSize: Int,
            params: LibraryParams?,
        ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> = browseThread.submit(java.util.concurrent.Callable<LibraryResult<ImmutableList<MediaItem>>> {
            val items = library.children(parentId)
            if (items == null) LibraryResult.ofError(LibraryResult.RESULT_ERROR_BAD_VALUE)
            else {
                // A tag filter or an A-Z part: told when it changes, like the tabs (the last few the car opened).
                if (Browse.filterOf(parentId) != null || Browse.partOf(parentId) != null) {
                    if (browsed.size > 20) browsed.clear()
                    browsed.add(parentId)
                }
                LibraryResult.ofItemList(items.page(page, pageSize), params)
            }
        })

        override fun onGetItem(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            mediaId: String,
        ): ListenableFuture<LibraryResult<MediaItem>> = browseThread.submit(java.util.concurrent.Callable<LibraryResult<MediaItem>> {
            val item = if (mediaId == ROOT_ID) rootItem() else {
                library.tabs().firstOrNull { it.mediaId == mediaId }
                    ?: Browse.tagOf(mediaId)?.let { tagFolder(it) }
                    ?: library.listItem(mediaId)
                    ?: library.station(mediaId)?.let { library.mediaItem(it) }
                    ?: Browse.splitItemId(mediaId)?.let { (list, id) -> library.station(id)?.let { library.mediaItem(it, list) } }
            }
            item?.let { LibraryResult.ofItem(it, null) } ?: LibraryResult.ofError(LibraryResult.RESULT_ERROR_BAD_VALUE)
        })

        /** Search typed or said in Android Auto: the results are worked out again when asked for. */
        override fun onSearch(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            query: String,
            params: LibraryParams?,
        ): ListenableFuture<LibraryResult<Void>> = browseThread.submit(java.util.concurrent.Callable<LibraryResult<Void>> {
            val count = library.searchResults(query).size
            Log.i(TAG, "[auto] search '$query': $count found")
            handler.post { session.notifySearchResultChanged(browser, query, count, params) }
            LibraryResult.ofVoid()
        })

        override fun onGetSearchResult(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            query: String,
            page: Int,
            pageSize: Int,
            params: LibraryParams?,
        ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> = browseThread.submit(java.util.concurrent.Callable<LibraryResult<ImmutableList<MediaItem>>> {
            LibraryResult.ofItemList(library.searchResults(query).page(page, pageSize), params)
        })

        /**
         * A station tapped in Android Auto queues the list it was in; a
         * spoken "play … on Frequencies" picks a tag or a station. The
         * phone's own screens send the whole list and go the usual way.
         */
        override fun onSetMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>,
            startIndex: Int,
            startPositionMs: Long,
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> {
            // A tag tile tapped in the car's Tags tab: flip the tag; what plays goes on as it is (build 189).
            mediaItems.singleOrNull()?.let { Browse.tagFlipOf(it.mediaId) }?.let { tag ->
                flipCarTag(tag)
                return answerTagTap(tag)
            }
            // Played from Saved (or Saved with tags) in the car: the queue keeps following the car's tag filter.
            val savedFilter = mediaItems.singleOrNull()?.let { Browse.splitItemId(it.mediaId)?.first }?.let { Browse.filterOf(it) }
            carSavedQueue = savedFilter != null
            // Played from a filtered Saved: that is the filter now, in the car and on the phone (the Tags tab keeps it otherwise).
            if (savedFilter != null && carQuery != savedFilter) carQuery = savedFilter
            val one = mediaItems.singleOrNull()
                ?: return super.onSetMediaItems(mediaSession, controller, mediaItems, startIndex, startPositionMs)
            val query = one.requestMetadata.searchQuery
            val pick = pickFor(one)
            if (pick == null) {
                if (query == null) return super.onSetMediaItems(mediaSession, controller, mediaItems, startIndex, startPositionMs)
                Log.i(TAG, "[auto] play from search '$query': nothing matches")
                mediaSession.sendError(SessionError(SessionError.ERROR_BAD_VALUE, "No saved frequency matches \"$query\"."))
                return Futures.immediateFailedFuture(IllegalArgumentException("nothing matches '$query'"))
            }
            blocked(pick.stations[pick.index].id)?.let { why ->
                Log.i(TAG, "[auto] ${pick.stations[pick.index].displayName}: $why")
                mediaSession.sendError(SessionError(SessionError.ERROR_NOT_SUPPORTED, why))
                return Futures.immediateFailedFuture(IllegalStateException(why))
            }
            Log.i(TAG, "[auto] ${if (query != null) "play from search '$query'" else "play ${one.mediaId}"}: ${pick.what}, starting at ${pick.stations[pick.index].displayName}")
            return Futures.immediateFuture(
                MediaSession.MediaItemsWithStartPosition(pick.stations.map { library.mediaItem(it) }, pick.index, C.TIME_UNSET),
            )
        }

        /** Controllers send only ids; the stream address and art are filled in here. */
        override fun onAddMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>,
        ): ListenableFuture<MutableList<MediaItem>> =
            Futures.immediateFuture(
                mediaItems.mapNotNull { item ->
                    val id = Browse.splitItemId(item.mediaId)?.let { (list, raw) -> if (list == Browse.RECENT) raw.substringAfter(':') else raw } ?: item.mediaId
                    (library.station(item.mediaId) ?: library.station(id))?.let { library.mediaItem(it) }
                }.toMutableList(),
            )

        /** Play pressed on headphones or the notification after the app was closed: the last frequency. */
        override fun onPlaybackResumption(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> {
            val stations = library.sorted()
            val last = stations.indexOfFirst { it.id == library.settings.value.lastStationId }.coerceAtLeast(0)
            return Futures.immediateFuture(
                MediaSession.MediaItemsWithStartPosition(stations.map { library.mediaItem(it) }, last, C.TIME_UNSET),
            )
        }
    }

    /** What a single item from Android Auto plays: a spoken query, a station in a list, or a whole tag or tab. */
    private fun pickFor(item: MediaItem): Browse.Pick? {
        val all = library.stations.value
        item.requestMetadata.searchQuery?.let { query ->
            if (query.isNotBlank()) return Browse.playFromSearch(all, query)
            // "Play Frequencies": the last frequency, inside Saved.
            val saved = Browse.saved(all, Browse.carOrder(library.settings.value)).takeIf { it.isNotEmpty() } ?: return null
            val last = saved.indexOfFirst { it.id == library.settings.value.lastStationId }.coerceAtLeast(0)
            return Browse.Pick(saved, last, "the last frequency")
        }
        val history = library.history.value
        Browse.queueFor(all, item.mediaId, history, Browse.carOrder(library.settings.value))?.let { return it }
        return Browse.list(all, item.mediaId, history = history, order = Browse.carOrder(library.settings.value))?.takeIf { it.isNotEmpty() }?.let { Browse.Pick(it, 0, item.mediaId) }
    }

    private fun <T> List<T>.page(page: Int, size: Int): List<T> {
        if (size <= 0) return this
        val from = page.toLong() * size
        if (from >= this.size) return emptyList()
        return subList(from.toInt(), minOf(this.size.toLong(), from + size).toInt())
    }

    /**
     * Next and previous always move one frequency along the list (it
     * wraps around), never back to the start of the stream, which a live
     * HLS stream with a rewind window would otherwise do. Frequencies
     * marked down are passed over (bridge._step_past_down), from the app,
     * the notification, headphones and Android Auto alike; an iBroadcast
     * one never is. With shuffle on, Next picks one at random; Previous is
     * always the one played before.
     */
    private inner class StationPlayer : ForwardingPlayer(exo) {
        private val listeners = java.util.concurrent.CopyOnWriteArraySet<Player.Listener>()

        override fun seekToNext() = step(+1, "Next")
        override fun seekToPrevious() = step(-1, "Previous")
        override fun seekToNextMediaItem() = step(+1, "Next")
        override fun seekToPreviousMediaItem() = step(-1, "Previous")

        // Shuffle lives in the settings, not in ExoPlayer's own shuffle order (which would reorder the list's wrap-around).
        override fun addListener(listener: Player.Listener) {
            super.addListener(listener)
            listeners += listener
        }

        override fun removeListener(listener: Player.Listener) {
            super.removeListener(listener)
            listeners -= listener
        }

        // Radio has no length: no progress bar or times in the car, the notification or the lock screen (user, batch 24),
        // even for an iBroadcast or YouTube track. The service itself reads exo's own duration.
        override fun getDuration(): Long = C.TIME_UNSET
        override fun getContentDuration(): Long = C.TIME_UNSET

        // A tag tile's tap reaches Media3 as "play this" (build 189): the answer is the frequency already
        // there, so the car's play request ends at once; nothing is replaced or restarted, and a tag tap
        // never starts the sound (Media3's play() after the answer is skipped).
        override fun setMediaItems(mediaItems: MutableList<MediaItem>, startIndex: Int, startPositionMs: Long) {
            if (keepPlaying && mediaItems.singleOrNull()?.mediaId == KEEP_ID) return
            super.setMediaItems(mediaItems, startIndex, startPositionMs)
        }

        override fun play() {
            if (keepPlaying) return
            // Waiting to reconnect: Play reconnects now.
            if (recovering) {
                handler.removeCallbacks(reconnect)
                reconnectNow()
            }
            // Stopped after a failure (task 239), or a stream whose connection kept breaking (task 249):
            // Play tries it again, the lap of stations from the start.
            if (exo.playbackState == Player.STATE_IDLE && exo.mediaItemCount > 0) {
                failedInARow = 0
                exo.prepare()
            }
            super.play()
        }

        // While answering a tag tile's tap, the session (and through it the car) sees a short "loading" and then
        // the frequency as it is: the answer a car waits for after a play request. ExoPlayer and the service's own
        // listeners (static, Song ID, ad cover) listen to ExoPlayer itself and never see it.
        private var answering = false

        override fun getPlaybackState(): Int = when {
            answering || recovering -> Player.STATE_BUFFERING
            stuck() -> Player.STATE_READY
            else -> super.getPlaybackState()
        }
        override fun isPlaying(): Boolean = if (answering || recovering || stuck()) false else super.isPlaying()

        // "Source error" in Android Auto (task 239, build 198): a station that failed and wasn't moved on from kept
        // ExoPlayer's error (it stays until the player is prepared again), and Media3 handed it to the car as
        // STATE_ERROR, so Auto put up its error screen and the car's card looked crashed until Back. Now the service
        // recovers from every failure itself (reconnects, the next server, the next station, the music folder; a
        // clean stop only when nothing can play: PlaybackService.recover / moveOn), and the session never carries
        // the error at all. While a reconnect waits, the car and the phone see the station loading; after a clean
        // stop, paused, and Play tries again. The app's own screens still say what went wrong
        // (ServiceProblems.playerError).
        private fun stuck(): Boolean = exo.playerError != null
        override fun getPlayerError(): PlaybackException? = null
        override fun getPlayWhenReady(): Boolean = when {
            recovering -> true
            stuck() -> false
            else -> super.getPlayWhenReady()
        }

        /** The service is waiting to reconnect a station that dropped: shown loading, not paused or failed. */
        var recovering = false
            set(value) {
                if (field == value) return
                field = value
                stateChanged()
            }

        // The car's line under the song taking turns (build 197) changed the current item every 7 s, and each
        // change of the playlist reached every media controller as a new queue. Android's Bluetooth service on the
        // phone then crashed over and over ("Can't compare to a recycled bitmap!", 6 times between 01:12 and 01:22
        // on the 2026-11-11 drive), which dropped the car's connection each time (task 240). Now the turn changes
        // only the metadata the session reads; the playlist stays as it is.
        @Volatile private var carLine: Pair<String, String>? = null

        override fun getMediaMetadata(): androidx.media3.common.MediaMetadata {
            val meta = super.getMediaMetadata()
            val (id, line) = carLine ?: return meta
            if (id != exo.currentMediaItem?.mediaId || meta.subtitle?.toString() == line) return meta
            return meta.buildUpon().setSubtitle(line).build()
        }

        /** The car line for [id], quietly (the item's own metadata is about to change and says it). */
        fun setCarLine(id: String, line: String?) {
            carLine = line?.let { id to it }
        }

        /** The car line for [id] now: only the session's metadata changes. */
        fun showCarLine(id: String, line: String?) {
            val next = line?.let { id to it }
            if (carLine == next) return
            carLine = next
            val meta = mediaMetadata
            for (l in listeners) l.onMediaMetadataChanged(meta)
        }

        fun answerTap() {
            if (answering) return
            answering = true
            stateChanged()
            handler.postDelayed({
                answering = false
                stateChanged()
            }, TAP_ANSWER_MS)
        }

        private fun stateChanged() {
            val state = playbackState
            val playing = isPlaying
            for (l in listeners) {
                l.onPlaybackStateChanged(state)
                l.onIsPlayingChanged(playing)
            }
        }

        override fun getShuffleModeEnabled(): Boolean = shuffleOn
        override fun setShuffleModeEnabled(shuffleModeEnabled: Boolean) = setShuffle(shuffleModeEnabled)
        override fun isCommandAvailable(command: Int): Boolean = command == Player.COMMAND_SET_SHUFFLE_MODE || super.isCommandAvailable(command)
        override fun getAvailableCommands(): Player.Commands = super.getAvailableCommands().buildUpon().add(Player.COMMAND_SET_SHUFFLE_MODE).build()

        fun shuffleChanged(on: Boolean) {
            for (l in listeners) l.onShuffleModeEnabledChanged(on)
        }
    }

    /** Shuffle switched from the notification, the car or a Media3 controller: saved like the app's own button does it. */
    private fun setShuffle(on: Boolean) {
        if (on != shuffleOn) applyShuffle(on)
        library.updateSettings { it[Shuffle.SETTING] = JsonPrimitive(on) }
    }

    private fun applyShuffle(on: Boolean) {
        shuffleOn = on
        Log.i(TAG, "[shuffle] ${if (on) "on" else "off"}")
        if (::stationPlayer.isInitialized) stationPlayer.shuffleChanged(on)
        refreshButtons()
    }

    /** The notification's and the car's buttons again (shuffle, the song's star, Song ID, in or out of the car). */
    private fun refreshButtons() {
        val s = session ?: return
        // New or renamed tags: every controller may press their buttons, so the commands go to all of them first.
        val tags = Browse.tags(library.stations.value)
        val tagsChanged = tags != carTags
        carTags = tags
        for (c in s.connectedControllers) {
            if (tagsChanged) s.setAvailableCommands(c, sessionCommands(), playerCommandsFor(s, c))
            val car = isCar(s, c)
            if (car || s.isMediaNotificationController(c)) s.setMediaButtonPreferences(c, buttonsFor(car || inCar))
        }
    }

    /** Every controller's session commands: the defaults, the car's buttons and one per tag. */
    private fun sessionCommands(): SessionCommands {
        val b = MediaSession.ConnectionResult.DEFAULT_SESSION_AND_LIBRARY_COMMANDS.buildUpon()
            .add(SHUFFLE)
            .add(THUMBS_DOWN)
            .add(FAVORITE)
            .add(SONG_ID)
        for (tag in carTags) b.add(tagCommand(tag))
        return b.build()
    }

    /**
     * No queue button in Android Auto (user, build 186): Media3 publishes the
     * platform session's queue, which Auto shows as that button, only when the
     * notification controller may read the timeline. So the notification's and
     * the old-style controllers' (Auto on a phone) go without it; Next, Previous,
     * Shuffle and the title and art have commands of their own and stay. The
     * app's own controller keeps the timeline.
     */
    private fun playerCommandsFor(s: MediaSession, c: MediaSession.ControllerInfo): Player.Commands {
        val all = MediaSession.ConnectionResult.DEFAULT_PLAYER_COMMANDS
        val noQueue = s.isMediaNotificationController(c) || c.controllerVersion == MediaSession.ControllerInfo.LEGACY_CONTROLLER_VERSION
        return if (noQueue) all.buildUpon().remove(Player.COMMAND_GET_TIMELINE).build() else all
    }

    /**
     * A tag tile tapped in the car (build 188): that tag's next state (off,
     * only these, hidden). The Tags tab is told at once so the tile changes
     * in place; Saved a moment later; the queue follows while playing from Saved.
     */
    private fun flipCarTag(tag: String) {
        val next = Browse.nextTagState(com.frequencies.core.Search.tagState(carQuery, tag))
        carQuery = com.frequencies.core.Search.setTag(carQuery, tag, next)
        Log.i(TAG, "[auto] tag tile '$tag' -> $next: filter '$carQuery'")
    }

    /**
     * Auto answers a tap on a playable item by opening its Now Playing and
     * waiting ("Getting your selection…", 5 s, then "Could not load your
     * selection") until the session's state is something other than none,
     * with a title or picture (read from Android Auto 17.9 itself, build 189).
     * Build 188 answered nothing, so the car waited. So the tap is answered
     * with the frequency already there, which goes on undisturbed
     * ([keepPlaying]), and the session shows the car a short "loading" and
     * then the frequency as it is ([StationPlayer.answerTap]). A stopped
     * frequency is made ready again, and with nothing loaded Saved as
     * filtered now is: paused either way, as a tag tap never starts the sound.
     */
    private fun answerTagTap(tag: String): ListenableFuture<MediaSession.MediaItemsWithStartPosition> {
        keepPlaying = true
        // Media3 sets, prepares and plays in one go on this thread once the answer is in; then the player is ours again.
        handler.post {
            keepPlaying = false
            stationPlayer.answerTap()
        }
        if (exo.currentMediaItem == null) {
            val list = Browse.list(library.stations.value, Browse.filterId(carQuery), order = Browse.carOrder(library.settings.value)).orEmpty()
            if (list.isEmpty()) {
                keepPlaying = false
                return Futures.immediateFailedFuture(IllegalStateException("tag tile '$tag': nothing to show"))
            }
            carSavedQueue = true
            Log.i(TAG, "[auto] tag tile '$tag' with nothing loaded: Saved as filtered, ${list.size} frequencies, paused")
            return Futures.immediateFuture(MediaSession.MediaItemsWithStartPosition(list.map { library.mediaItem(it) }, 0, C.TIME_UNSET))
        }
        // Stopped or failed (Auto would read "none" and wait): Media3 prepares it again, still paused.
        if (exo.playbackState == Player.STATE_IDLE) Log.i(TAG, "[auto] tag tile '$tag': the frequency was stopped, made ready (paused)")
        return Futures.immediateFuture(MediaSession.MediaItemsWithStartPosition(listOf(KEEP_ITEM), 0, C.TIME_UNSET))
    }

    /**
     * The tag filter changed, in the car or on the phone (build 189): the
     * Tags tab's tiles at once, Saved a moment later, and the queue follows
     * while playing from Saved in the car.
     */
    private fun tagFilterChanged() {
        val s = session ?: return
        if (browseThread.isShutdown) return
        val query = carQuery
        Log.i(TAG, "[auto] tag filter now '$query'")
        browseThread.execute {
            val stations = library.stations.value
            val tags = Browse.tags(stations).size
            val ids = if (carSavedQueue) Browse.list(stations, Browse.filterId(query), order = Browse.carOrder(library.settings.value)).orEmpty().map { it.id } else null
            handler.post {
                s.notifyChildrenChanged(Browse.TAGS, tags, null)
                if (ids != null && carSavedQueue && query == carQuery) followQueue(ids)
            }
        }
        notifyBrowsers(library.stations.value)
    }

    private fun tagCommand(tag: String) = SessionCommand(TAG_ACTION + tag, Bundle.EMPTY)

    /**
     * A tag button under the car's ⋯ (user, build 186: tags on Now Playing):
     * off -> only these -> hide these -> off, as the Saved tiles go, and the
     * queue follows Saved filtered that way at once.
     */
    private fun tagFromCar(tag: String): Boolean {
        val next = Browse.nextTagState(com.frequencies.core.Search.tagState(carQuery, tag))
        carSavedQueue = true
        // The queue, the Tags tab and the phone follow through tagFilterChanged.
        carQuery = com.frequencies.core.Search.setTag(carQuery, tag, next)
        Log.i(TAG, "[auto] tag '$tag' -> ${next}: filter '$carQuery'")
        refreshButtons()
        return true
    }

    /** One button per tag, all under the car's ⋯, showing the tag's state in [carQuery]. */
    private fun tagButtons(): List<CommandButton> = carTags.map { tag ->
        val state = com.frequencies.core.Search.tagState(carQuery, tag)
        val (icon, word) = when (state) {
            com.frequencies.core.Search.INCLUDED -> R.drawable.ic_tag_only to "only"
            com.frequencies.core.Search.EXCLUDED -> R.drawable.ic_tag_hidden to "hidden"
            else -> R.drawable.ic_tag_off to "off"
        }
        CommandButton.Builder(CommandButton.ICON_UNDEFINED)
            .setCustomIconResId(icon)
            .setDisplayName("$tag: $word")
            .setSessionCommand(tagCommand(tag))
            .setSlots(CommandButton.SLOT_OVERFLOW)
            .build()
    }

    /**
     * The queue becomes [ids] (Saved as filtered now) around the frequency
     * playing, which goes on undisturbed; one the filter hides stays first,
     * so Next goes to the first one shown (the desktop's filtered list).
     */
    private fun followQueue(ids: List<String>) {
        if (ids.isEmpty() || exo.mediaItemCount == 0) return
        val current = exo.currentMediaItem?.mediaId ?: return
        val want = if (current in ids) ids else listOf(current) + ids
        val now = (0 until exo.mediaItemCount).map { exo.getMediaItemAt(it).mediaId }
        if (want == now) return
        val at = want.indexOf(current)
        val index = exo.currentMediaItemIndex
        fun items(part: List<String>) = part.mapNotNull { id -> library.station(id)?.let { library.mediaItem(it) } }
        if (index + 1 < exo.mediaItemCount) exo.removeMediaItems(index + 1, exo.mediaItemCount)
        if (index > 0) exo.removeMediaItems(0, index)
        if (at > 0) exo.addMediaItems(0, items(want.subList(0, at)))
        if (at + 1 < want.size) exo.addMediaItems(items(want.subList(at + 1, want.size)))
        Log.i(TAG, "[auto] the queue follows the car's tag filter: ${ids.size} frequencies")
    }

    /** The line under the song: in the car the artist and the station take turns, elsewhere both ("artist · station"). */
    private fun carSubtitle(artist: String, station: String): String =
        if (inCar) NowPlayingRules.carTurn(artist, station, carShowsStation) else NowPlayingRules.carLine(artist, station)

    /** Turns start over with the artist when the song changes; none when there's nothing to take turns with. */
    private fun restartCarTurns() {
        val parts = carParts
        val key = parts?.let { "${it.first}|${it.second}" }
        if (key == carTurnKey) return
        carTurnKey = key
        handler.removeCallbacks(carTurn)
        carShowsStation = false
        if (inCar && parts != null && NowPlayingRules.carTakesTurns(parts.first, parts.second)) handler.postDelayed(carTurn, NowPlayingRules.CAR_TURN_MS)
    }
    private var carTurnKey: String? = null

    /** Only the car line changes: the current item's subtitle, in place (playback goes on undisturbed). */
    private fun swapCarLine() {
        val (artist, station) = carParts ?: return
        val current = exo.currentMediaItem ?: return
        if (!::stationPlayer.isInitialized) return
        // Never a new item (build 198, task 240): see StationPlayer.showCarLine.
        stationPlayer.showCarLine(current.mediaId, carSubtitle(artist, station))
    }

    /** On or off a car's screen: its buttons, and the static a little louder there (user, batch 24). */
    private fun carChanged(on: Boolean) {
        if (on == inCar) return
        inCar = on
        static.boostDb = if (on) CAR_STATIC_DB else 0.0
        // The line under the song: taking turns in the car, both together elsewhere.
        handler.removeCallbacks(carTurn)
        carTurnKey = null
        carShowsStation = false
        swapCarLine()
        restartCarTurns()
        Log.i(TAG, "[auto] ${if (on) "on the car's screen: Favorite in Previous's place, Song ID, static +$CAR_STATIC_DB dB" else "off the car's screen: the phone's buttons, static as set"}")
        refreshButtons()
    }

    /** The song playing now on the current frequency, if one is known. */
    private fun currentSong(): SongNow.Song? = SongNow.song.value?.takeIf { it.stationId == exo.currentMediaItem?.mediaId }

    /** What the car's buttons show: is there a song, is it starred, is Song ID on, can it be starred at all (task 241). */
    private data class Buttons(val hasSong: Boolean, val starred: Boolean, val songId: Boolean, val canStar: Boolean)

    private fun buttonState(): Buttons {
        val song = currentSong()
        val station = library.station(exo.currentMediaItem?.mediaId)
        val starred = song != null && song.key in com.frequencies.core.SongHistory.favoriteKeys(library.history.value)
        val songId = station?.let { com.frequencies.core.Comfort.songIdOn(it) } ?: false
        return Buttons(song != null, starred, songId, com.frequencies.core.SongHistory.canFavorite(station))
    }

    /** The car's star: favorites the song playing now, as the phone's star does; a second press unstars it. */
    private fun favoriteFromCar(): Boolean {
        val song = currentSong()
        if (song == null) {
            Log.i(TAG, "[auto] favorite: no song known yet")
            return false
        }
        if (!com.frequencies.core.SongHistory.canFavorite(library.station(song.stationId))) {
            Log.i(TAG, "[auto] favorite: not for a music folder or iBroadcast song")
            return false
        }
        val station = library.station(song.stationId)?.displayName.orEmpty()
        val on = song.key !in com.frequencies.core.SongHistory.favoriteKeys(library.history.value)
        library.changeHistory {
            com.frequencies.core.SongHistory.setFavorite(it, song.key, on, library.now(), song.title, song.artist, station, song.appleMusicUrl, song.coverUrl)
        }
        Log.i(TAG, "[auto] favorite ${if (on) "on" else "off"}: ${song.artist} - ${song.title}")
        return true
    }

    /** The car's Song ID: switches song identifying on this frequency, as the phone's button does. */
    private fun songIdFromCar(): Boolean {
        val station = library.station(exo.currentMediaItem?.mediaId) ?: return false
        val on = !com.frequencies.core.Comfort.songIdOn(station)
        library.update(station.id) {
            it["shazam_auto"] = JsonPrimitive(on)
            it["shazam_auto_set"] = JsonPrimitive(true)
        }
        Log.i(TAG, "[auto] Song ID ${if (on) "on" else "off"} for '${station.displayName}'")
        return true
    }

    private fun isCar(s: MediaSession, c: MediaSession.ControllerInfo) = s.isAutoCompanionController(c) || s.isAutomotiveController(c)

    /**
     * The car: the star where Previous was (the dashboard player's small
     * card shows it instead of Previous, user, batch 24), then Song ID and
     * Shuffle (no Thumbs down in the car since build 186, user); Auto puts
     * what doesn't fit under its "more" button. Elsewhere: Shuffle. No star
     * for a music folder or iBroadcast song (task 241): Previous is back there.
     */
    private fun buttonsFor(car: Boolean): ImmutableList<CommandButton> =
        if (car) ImmutableList.builder<CommandButton>().apply { if (buttonState().canStar) add(favoriteButton()) }.add(songIdButton(), shuffleButton()).build()
        else ImmutableList.of(shuffleButton())

    /** Which frequency played before, for Previous (bridge.play pushes the one it leaves). */
    private val backWatch = object : Player.Listener {
        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
            if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO || reason == Player.MEDIA_ITEM_TRANSITION_REASON_REPEAT) return
            val id = mediaItem?.mediaId ?: return
            val last = lastId
            if (last != null && last != id && !goingBack) back.push(last)
            goingBack = false
            lastId = id
        }
    }

    private fun step(direction: Int, button: String) {
        val count = exo.mediaItemCount
        if (count == 0) return
        val here = exo.currentMediaItemIndex.takeIf { it in 0 until count }
        if (count == 1) { exo.seekToDefaultPosition(0); return }      // the only one: start it again
        val ids = (0 until count).map { exo.getMediaItemAt(it).mediaId }
        val health = library.health.value
        // Offline (task 214), only what plays without the internet is in the rotation; a music folder not on this phone never is.
        val offline = Connectivity.offline.value
        fun down(i: Int): Boolean {
            val station = library.station(ids[i]) ?: return false
            if (blocked(ids[i]) != null) return true
            return Health.skippedWhenDown(station) && Health.isDown(health, station.id)
        }
        if (direction < 0 && previousPlayed(ids, here, health)) return
        if (shuffleOn) {
            shuffleNext(ids, here, health, offline)
            return
        }
        val result = Health.stepPastDown(count, here, direction, ::down)
        val passed = result.passed.map { library.station(ids[it])?.displayName ?: ids[it] }
        val target = result.index
        if (target == null && offline) {
            Notices.say(com.frequencies.core.OfflineRules.NONE_LOCAL)
            return
        }
        if (target == null) {
            Log.i(TAG, Health.skipLog(button, passed, null))
            Notices.say(Health.allDownNotice(button, here == null))
            return
        }
        if (passed.isNotEmpty()) Log.i(TAG, Health.skipLog(button, passed, library.station(ids[target])?.displayName ?: ids[target]))
        exo.seekToDefaultPosition(target)
    }

    /**
     * bridge.previous: the frequency played before this one, shuffle or
     * not; with shuffle off, one marked down since is passed over for the
     * one before it. False when none is left (shuffle off: the one above
     * in the list, as before; on: a random one, as the desktop does).
     */
    private fun previousPlayed(ids: List<String>, here: Int?, health: kotlinx.serialization.json.JsonObject): Boolean {
        val current = here?.let { ids[it] }
        val (id, passed) = back.pop { id ->
            val station = library.station(id)
            val down = station != null && Health.skippedWhenDown(station) && Health.isDown(health, id)
            Shuffle.usableBack(id in ids && id != current, blocked(id) != null, down, shuffleOn)
        }
        val names = passed.map { library.station(it)?.displayName ?: it }
        if (id == null) {
            if (names.isNotEmpty()) Log.i(TAG, Health.skipLog("Previous", names, "the list's next station"))
            return false
        }
        if (names.isNotEmpty()) Log.i(TAG, Health.skipLog("Previous", names, library.station(id)?.displayName ?: id))
        goingBack = true
        exo.seekToDefaultPosition(ids.indexOf(id))
        return true
    }

    /** bridge.next with shuffle on: any other frequency at random (Shuffle.next), never one that can't play here and now. */
    private fun shuffleNext(ids: List<String>, here: Int?, health: kotlinx.serialization.json.JsonObject, offline: Boolean) {
        val stations = ids.map { library.station(it) }
        val pick = Shuffle.next(
            ids.size, here,
            unavailable = { blocked(ids[it]) != null },
            down = { i -> stations[i]?.let { Health.skippedWhenDown(it) && Health.isDown(health, it.id) } == true },
        )
        when {
            pick != null -> {
                Log.i(TAG, "[shuffle] Next: '${stations[pick]?.displayName ?: ids[pick]}' at random")
                exo.seekToDefaultPosition(pick)
            }
            // The only one that can play: start it again (bridge._online_only).
            here != null && blocked(ids[here]) == null -> exo.seekToDefaultPosition(here)
            offline -> Notices.say(com.frequencies.core.OfflineRules.NONE_LOCAL)
        }
    }

    /**
     * Static while a station tunes in, the desktop's way: the station's
     * player stays silent under the static until its stream really moves,
     * then the two cross-fade. Only when tuning to a station, never on a
     * resume of the same one.
     */
    private inner class Tuner : Player.Listener {
        private var tuning = false
        private var tunedId: String? = null
        private var startedAt = 0L
        private var firstPos = -1L
        /** When the stream's position first moved, and since when the level meter has heard it (0: not yet). */
        private var movedAt = 0L
        private var audibleSince = 0L
        private var recorded: String? = null

        /**
         * The static stays until the station can really be heard
         * (Player._sound_ready): its position moves AND the level meter
         * reads above -55 dB for 0.4 s. A stream that moves but stays
         * silent (a Twitch pre-roll arrives as silence) is handed over
         * after 45 s anyway. Without a meter reading (a stream the tap
         * can't hear) the position alone decides, as before.
         */
        private val watch = object : Runnable {
            override fun run() {
                if (!tuning) return
                val now = SystemClock.elapsedRealtime()
                val waited = now - startedAt
                if (exo.isPlaying) {
                    val pos = exo.currentPosition
                    if (firstPos < 0) firstPos = pos
                    if (movedAt == 0L && pos - firstPos >= 200) movedAt = now
                }
                val db = tap.levelDb()
                if (db != null && db > AUDIBLE_DB && exo.isPlaying) { if (audibleSince == 0L) audibleSince = now } else audibleSince = 0L
                // While a station's opening ad is covered, the cover decides (bridge's ad cover, player._tick_ad).
                if (ad.active) {
                    ad.tick(now, moved = movedAt != 0L)
                    handler.postDelayed(this, 200)
                    return
                }
                when {
                    movedAt != 0L && db == null -> { handOver("audio flowing after ${waited / 1000.0} s (no level meter)"); return }
                    movedAt != 0L && audibleSince != 0L && now - audibleSince >= 400 -> {
                        handOver("sound at ${"%.0f".format(db)} dB after ${waited / 1000.0} s"); return
                    }
                    movedAt != 0L && now - movedAt > SILENT_LIMIT_MS -> { handOver("still silent ${SILENT_LIMIT_MS / 1000} s after it started, handing over anyway"); return }
                    movedAt == 0L && waited > silentLimit() -> { handOver("still silent after ${silentLimit() / 1000} s, handing over anyway"); return }
                }
                handler.postDelayed(this, 200)
            }
        }

        /** A station that pre-buffers stays silent while it fills its buffer: the static waits that long too. */
        private fun silentLimit(): Long = maxOf(SILENT_LIMIT_MS, (prebuffer[exo.currentMediaItem?.mediaId] ?: 0L) + PREBUFFER_GRACE_MS + 2_000)

        /** A service failed for good: the static and any ad cover stop now. */
        fun stopTune() = endTune(fade = false)

        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
            if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO || reason == Player.MEDIA_ITEM_TRANSITION_REASON_REPEAT) {
                // The list repeats so next and previous wrap around, but a
                // stream that ends by itself stops on its station, as before.
                val count = exo.mediaItemCount
                val ended = if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_REPEAT) exo.currentMediaItemIndex
                else (exo.currentMediaItemIndex - 1 + count) % count
                val endedId = exo.getMediaItemAt(ended).mediaId
                if (com.frequencies.core.LocalStations.isLocal(library.station(endedId))) {
                    // A local files station goes on with another song.
                    nextLocalSong(ended, endedId)
                    return
                }
                if (playsTracks(library.station(endedId))) {
                    // An iBroadcast library or a YouTube playlist goes on with the next track, no static.
                    ytTries.remove(endedId)
                    ibTries.remove(endedId)
                    retried.remove(endedId)
                    nextServiceTrack(ended, endedId)
                    return
                }
                Log.i(TAG, "[stream] the station's stream ended")
                if (library.isPreview(endedId)) {
                    exo.pause()
                    exo.seekToDefaultPosition(ended)
                    return
                }
                // The server let go of a station that was playing: a drop, reconnected at once (build 198, task 240);
                // a stream that keeps ending right after it starts gets the same few tries before the next station.
                goOn(endedId, ended)
                if (tries.left(endedId)) {
                    tries.use(endedId, SystemClock.elapsedRealtime())
                    Log.i(TAG, "[stream] reconnecting at once (try ${tries.count} of ${Recovery.WAITS_MS.size})")
                    resetWatch()
                    return
                }
                moveOn(endedId)
                return
            }
            val id = mediaItem?.mediaId ?: return
            // The seek back from nextServiceTrack / nextLocalSong: the same station going on, not a tune-in (task 242).
            val goingOn = continuing
            continuing = null
            if (goingOn == id && id == tunedId) return
            if (id == tunedId && reason != Player.MEDIA_ITEM_TRANSITION_REASON_SEEK) return
            tunedId = id
            // A new tune-in: the last station's reconnect is off (tasks 239 and 240).
            handler.removeCallbacks(reconnect)
            stationPlayer.recovering = false
            tries.forget()
            // Tuning in to a local files station: a new song, joined partway through.
            if (com.frequencies.core.LocalStations.isLocal(library.station(id))) {
                localSong.remove(id)
                localJoin.add(id)
            }
            // A service station tuned in: a new track (or a fresh Twitch token), joined partway through.
            if (isService(library.station(id))) {
                forgetPicks(id)
                retried.remove(id)
                ytTries.remove(id)
                ibTries.remove(id)
                ServiceProblems.clear(id)
                if (playsTracks(library.station(id))) svcJoin.add(id)
            }
            blocked(id)?.let { why ->
                // Offline (or its music isn't here): it can't start (bridge.play's notice).
                Notices.say(why)
                handler.post { exo.pause() }
            }
            library.station(id).let { s ->
                router.tunedTo(StationTls.skipsCheck(s), s?.url.orEmpty())
                talkFocus.station(s != null && isTalk(s), s?.displayName.orEmpty())
            }
            // A station tuned again starts without the last song it played.
            songWatch.tuned(id)
            artistWatch.tuned(library.station(id))
            streamLine = null
            applyEq()
            refreshLine()
            recorded = null
            tryIndex.remove(mediaItem.localConfiguration?.uri?.toString() ?: "")
            resetWatch()
            startTune()
        }

        override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
            talkFocus.playing(playWhenReady)
            if (playWhenReady) blocked(exo.currentMediaItem?.mediaId)?.let { why ->
                Notices.say(why)
                handler.post { exo.pause() }
                return
            }
            if (playWhenReady) offlineStopped = null
            // Play again after a pause: the stream gets its connect time again.
            if (playWhenReady) resetWatch()
            if (!playWhenReady) {
                // Paused: no reconnect behind the user's back.
                handler.removeCallbacks(reconnect)
                stationPlayer.recovering = false
                endTune(fade = false)
            } else if (tuning && !static.playing && library.settings.value.staticOn) {
                // The station was picked first and Play came after it.
                static.start(library.settings.value.staticVolume / 100f)
            }
        }

        override fun onIsPlayingChanged(isPlaying: Boolean) {
            SongNow.hold(!isPlaying)
            val id = exo.currentMediaItem?.mediaId ?: return
            if (isPlaying && recorded != id) {
                recorded = id
                // Android Auto's Recent tab follows through the stations collector.
                library.recordPlay(id)
            }
        }

        override fun onPlaybackStateChanged(playbackState: Int) {
            val id = exo.currentMediaItem?.mediaId
            if (playbackState == Player.STATE_READY && id != null && localJoin.remove(id)) {
                // bridge._source_for: join the song 5-60% of the way in, so it sounds like it was already on air.
                val length = exo.duration
                if (length != C.TIME_UNSET && length > 0) exo.seekTo((length * com.frequencies.core.LocalStations.joinFraction()).toLong())
            }
            if (playbackState == Player.STATE_READY && id != null && svcJoin.remove(id)) {
                // bridge._ib_join (shuffled libraries, up to 60%) and YouTubeResolver (videos over 30 s, up to 66%).
                val station = library.station(id)
                val length = exo.duration
                val fraction = when {
                    IBroadcast.isIBroadcast(station) -> if (IBroadcast.shuffles(station!!, library.settings.value)) kotlin.random.Random.nextDouble(0.0, IBroadcast.JOIN_MAX) else 0.0
                    YouTube.isYouTube(station) -> YouTube.joinFraction(length / 1000.0)
                    else -> 0.0
                }
                if (fraction > 0 && length != C.TIME_UNSET && length > 0) {
                    val at = (length * fraction).toLong()
                    exo.seekTo(at)
                    songWatch.movedTo(id, at)
                }
            }
            if (playbackState == Player.STATE_IDLE || playbackState == Player.STATE_ENDED) endTune(fade = false)
        }

        override fun onMetadata(metadata: Metadata) {
            for (i in 0 until metadata.length()) {
                val entry = metadata.get(i)
                if (entry is IcyInfo) {
                    Log.i(TAG, "[stream] title: ${entry.title}")
                    val line = StreamTitle.clean(entry.title)
                    streamLine = line
                    refreshLine()
                    checkSplit(line)
                }
            }
        }

        override fun onPlayerErrorChanged(error: PlaybackException?) {
            // The session never carries the error (StationPlayer, task 239). The app's screens get it from the
            // service only when it stops for good (moveOn) or a preview fails; a reconnect shows "Tuning in…".
            if (error == null) ServiceProblems.playerError = null
        }

        override fun onPlayerError(error: PlaybackException) {
            val url = exo.currentMediaItem?.localConfiguration?.uri?.toString().orEmpty()
            val failed = library.station(exo.currentMediaItem?.mediaId)
            if (com.frequencies.core.LocalStations.isLocal(failed)) {
                // bridge.play: no playable audio in the folder (or a file that won't play).
                Log.w(TAG, "[local] '${failed!!.displayName}' failed: ${error.errorCodeName} ${error.message}")
                endTune(fade = false)
                localSong.remove(failed.id)
                // One song that won't play is not the whole folder (build 198): another one, a few in a row.
                val empty = generateSequence(error as Throwable) { it.cause }.any { it is NoSongs }
                val tries = (localTries[failed.id] ?: 0) + 1
                localTries[failed.id] = tries
                val index = exo.currentMediaItemIndex
                if (!empty && tries < LOCAL_TRIES && index >= 0 && exo.playWhenReady) {
                    Log.i(TAG, "[local] '${failed.displayName}': trying another song ($tries of $LOCAL_TRIES wouldn't play)")
                    nextLocalSong(index, failed.id)
                    return
                }
                localTries.remove(failed.id)
                Notices.say("${failed.displayName}: no playable audio files in ${com.frequencies.core.LocalStations.localPath(failed).ifEmpty { "that folder" }}.")
                // Never left on its error (task 239): the next station that can play, or a clean stop.
                if (library.isPreview(failed.id)) ServiceProblems.playerError = error else moveOn(failed.id)
                return
            }
            if (failed != null && serviceFailed(failed, error)) {
                Log.w(TAG, "[stream] '${failed.displayName}' failed: ${error.errorCodeName} ${error.message}")
                return
            }
            Log.w(TAG, "[stream] failed: ${error.errorCodeName} ${error.message} ($url)")
            if (error.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW) {
                exo.seekToDefaultPosition()
                exo.prepare()
                return
            }
            // A .pls lists the same station on several servers: try the next.
            val listed = candidates[url]
            val next = (tryIndex[url] ?: 0) + 1
            if (listed != null && next < listed.size) {
                tryIndex[url] = next
                exo.prepare()
                exo.play()
                return
            }
            endTune(fade = false)
            // Down on the first failure you saw yourself (mainwindow._handle_state).
            val id = exo.currentMediaItem?.mediaId ?: return
            if (library.isPreview(id)) {
                ServiceProblems.playerError = error
                return
            }
            library.markFailed(id, failureReason(error))
            weakReception(id, SystemClock.elapsedRealtime())
            // Reconnected, or moved on from (tasks 239 and 240): never left on its error.
            recover(id, error, wasPlaying = started)
        }

        /**
         * A load of the stream failed (task 249, build 206). When a live stream's connection breaks while it
         * plays, Media3 connects again by itself, and the app's listeners hear nothing: no error, no end. Media3
         * gives up only after several failures in a row that bring no sound, and a new connection that brings
         * a second or two of sound starts its count again, so a stream that keeps breaking that way was
         * reconnected for ever, "Tuning in…" over and over, with not a line in the log: the likely loop behind
         * KNS (2026-11-11, 1:25) and KUOI (8:26), whose servers stream steadily from a PC. Now every break after the sound started is one of the station's reconnects,
         * like the app's own ([recover]): logged, counted in [tries], and once they are used up the next
         * station takes over (or a clean stop). Music folders and services (YouTube, iBroadcast, Twitch)
         * have their own tries per song.
         */
        fun loadFailed(windowIndex: Int, error: IOException) {
            val id = exo.currentMediaItem?.mediaId ?: return
            if (windowIndex != exo.currentMediaItemIndex || !exo.playWhenReady || library.isPreview(id)) return
            val station = library.station(id)
            if (station == null || isService(station) || com.frequencies.core.LocalStations.isLocal(station)) return
            val why = generateSequence(error as Throwable) { it.cause }.last().let { "${it.javaClass.simpleName}${it.message?.let { m -> ": $m" } ?: ""}" }
            if (!started) {
                // Still connecting: Media3's own tries, then the player's error or the connect timeout (both bounded).
                Log.i(TAG, "[tune] '${station.displayName}': a connection failed ($why); Media3 tries again")
                return
            }
            if (tries.left(id)) {
                tries.use(id, SystemClock.elapsedRealtime())
                Log.i(TAG, "[stream] '${station.displayName}': the connection broke ($why); Media3 reconnects (try ${tries.count} of ${Recovery.WAITS_MS.size})")
                return
            }
            Log.i(TAG, "[stream] '${station.displayName}': the connection keeps breaking ($why), ${Recovery.WAITS_MS.size} times since it last played steadily: moving on")
            endTune(fade = false)
            // Media3's own reconnect stops here; the next station (or Play) starts afresh.
            exo.stop()
            moveOn(id)
        }

        private fun failureReason(error: PlaybackException): String {
            val http = generateSequence(error.cause) { it.cause }.filterIsInstance<HttpDataSource.InvalidResponseCodeException>().firstOrNull()
            return if (http != null) "HTTP ${http.responseCode}" else "could not connect"
        }

        // -- is it really playing? (player.Player._tick) ----------------------------

        /** Audio has flowed since this station was tuned. */
        private var started = false
        private var lastPos = -1L
        private var lastProgress = 0L
        /** When this station was tuned: how long it took to start becomes its connect_worst. */
        private var watchFrom = 0L
        /** When its sound last started flowing (after a tune-in or a reconnect). */
        private var playingFrom = 0L

        fun resetWatch() {
            started = false
            lastPos = -1L
            lastProgress = SystemClock.elapsedRealtime()
            watchFrom = lastProgress
        }

        /**
         * Once a second: a stream whose position never moves within
         * CONNECT_TIMEOUT (10 s) never started; one that started and then
         * stops moving for stall_timeout (6 s) has stalled. Either marks the
         * frequency down; a stall moves on when Settings says so, and a
         * stream that never started moves on too (desktop _handle_stalled).
         */
        fun watchStream() {
            val now = SystemClock.elapsedRealtime()
            val id = exo.currentMediaItem?.mediaId
            if (id == null || !exo.playWhenReady || exo.playbackState == Player.STATE_IDLE || exo.playbackState == Player.STATE_ENDED) {
                lastProgress = now
                return
            }
            val pos = exo.currentPosition
            if (stationPlayer.recovering) {
                // Waiting to reconnect a stream that stalled: it came back by itself, or the wait goes on.
                if (exo.isPlaying && lastPos >= 0 && pos > lastPos + 50) {
                    handler.removeCallbacks(reconnect)
                    stationPlayer.recovering = false
                } else {
                    if (lastPos < 0 || pos < lastPos) lastPos = pos
                    lastProgress = now
                    return
                }
            }
            if (exo.isPlaying && lastPos >= 0 && pos > lastPos + 50) {
                lastPos = pos
                lastProgress = now
                // Steady for a minute since it started and since its last drop: its reconnects start over
                // (the loop guard, task 240; build 206 counts from the last drop too, task 249).
                tries.playing(id, now, playingFrom)
                if (!started) {
                    started = true
                    playingFrom = now
                    failedInARow = 0
                    localTries.remove(id)
                    weakClear()
                    // Connecting is the best evidence there is that it is up.
                    if (!library.isPreview(id)) library.markConnected(id)
                    // The slowest start so far: Station settings' guide for pre-buffering (desktop _handle_connected).
                    val took = (now - watchFrom) / 1000.0
                    library.station(id)?.let { s -> Prebuffer.newWorst(s, took) }?.let { worst ->
                        library.update(id) { it["connect_worst"] = JsonPrimitive(worst) }
                    }
                    // It plays: a later refusal gets its retry again.
                    retried.remove(id)
                    ytTries.remove(id)
                    ibTries.remove(id)
                    ServiceProblems.clear(id)
                }
                return
            }
            if (lastPos < 0 || pos < lastPos) lastPos = pos
            // A YouTube station looks its playlist and video up first: it gets longer to start.
            val connect = maxOf(
                if (YouTube.isYouTube(library.station(id))) YOUTUBE_CONNECT_MS else CONNECT_TIMEOUT_MS,
                // Pre-buffering holds the start back on purpose: it gets that long plus the desktop's 12 s.
                (prebuffer[id] ?: 0L).let { if (it > 0) it + PREBUFFER_GRACE_MS + 2_000 else 0L },
            )
            val limit = if (started) library.settings.value.double("stall_timeout", 6.0).toLong().coerceAtLeast(2) * 1000 else connect
            if (now - lastProgress <= limit) return
            // Quiet since the sound last moved (or since tuning began).
            val silentSince = lastProgress
            lastProgress = now
            val url = exo.currentMediaItem?.localConfiguration?.uri?.toString().orEmpty()
            if (!started) {
                // A .pls lists the same station on several servers: the next one gets the same time.
                val listed = candidates[url]
                val next = (tryIndex[url] ?: 0) + 1
                if (listed != null && next < listed.size) {
                    Log.i(TAG, "[tune] no sound in ${CONNECT_TIMEOUT_MS / 1000}s, trying the next listed stream")
                    tryIndex[url] = next
                    exo.stop()
                    exo.prepare()
                    exo.play()
                    return
                }
                Log.i(TAG, "[tune] gave up: the position ${if (lastPos > 0) "stopped moving" else "never moved"} for ${connect / 1000}s")
                Notices.say("That stream never started playing.")
            } else {
                Log.i(TAG, "[stream] stalled: ${url.substringBefore('?')}")
            }
            endTune(fade = false)
            if (library.isPreview(id)) { if (!started) exo.stop(); return }
            library.markFailed(id, "no audio")
            weakReception(id, silentSince)
            // A stream that stalled is reconnected first; one that never started moves on, as before (tasks 239 and 240).
            recover(id, null, wasPlaying = started)
        }

        private fun startTune() {
            val settings = library.settings.value
            ad.cancel()
            // A covered opening ad (any station with the cover on) is covered even with the static off: the station stays silent under it.
            val covering = exo.playWhenReady && ad.begin(library.station(exo.currentMediaItem?.mediaId))
            if (!settings.staticOn && !covering) { endTune(fade = false); return }
            tuning = true
            startedAt = SystemClock.elapsedRealtime()
            firstPos = -1
            movedAt = 0L
            audibleSince = 0L
            level = 0f
            if (exo.playWhenReady && settings.staticOn) static.start(settings.staticVolume / 100f)
            handler.removeCallbacks(watch)
            handler.postDelayed(watch, 200)
        }

        private fun handOver(why: String) {
            Log.i(TAG, "[static] hand-over: $why")
            tuning = false
            handler.removeCallbacks(watch)
            val ms = library.settings.value.crossfadeMs
            static.fadeOut(ms)
            fadeIn(ms)
            levelLog()
        }

        /** 15 s after a hand-over the leveling has settled: its gain goes in the log (task 250). */
        private fun levelLog() {
            val id = tunedId
            handler.postDelayed({
                if (tunedId != id || !exo.isPlaying) return@postDelayed
                val name = library.station(id)?.displayName ?: id
                val why = when {
                    leveler.songs -> "songs play as they are"
                    !leveler.on -> "even out loudness is off"
                    else -> "gain ${"%+.1f".format(leveler.gainDb())} dB"
                }
                Log.i(TAG, "[level] '$name': $why")
            }, 15_000)
        }

        private fun endTune(fade: Boolean) {
            ad.cancel()
            if (!tuning && !static.playing) { level = 1f; return }
            tuning = false
            handler.removeCallbacks(watch)
            if (fade) static.fadeOut(library.settings.value.crossfadeMs) else static.stop()
            level = 1f
        }

        private fun fadeIn(ms: Int) {
            val started = System.currentTimeMillis()
            handler.post(object : Runnable {
                override fun run() {
                    if (tuning) return
                    val t = if (ms <= 0) 1f else ((System.currentTimeMillis() - started).toFloat() / ms).coerceIn(0f, 1f)
                    level = t
                    if (t < 1f) handler.postDelayed(this, 30)
                }
            })
        }
    }

    /**
     * The ad cover (player.FillerPlayer and Player._tick_ad): while a
     * station (any kind since build 184) plays its opening ad to nobody (the station's player
     * at volume 0), a second player plays another saved frequency, the one
     * chosen in Station settings first, else any without ads of its own
     * (up to three tried until one sounds). The static fills any moment the
     * cover can't be heard; with nothing to cover it, the static covers
     * the ad. The ad's time counts from when the channel starts sending;
     * when it is up the static comes in, the cover goes, and the station is
     * handed over as after any tune-in.
     */
    private inner class AdCover {
        private var state = "none"          // "filler": covering; "static": the static between cover and station
        private var deadline = 0L
        private var seconds = 0.0
        private var covers: List<com.frequencies.core.Station> = emptyList()
        private var tryAt = 0
        private var staticOn = false
        private var player: ExoPlayer? = null
        /** The cover is leveled too, as on the desktop (its chain is dynaudnorm and the meter). */
        private val coverLevel = LevelProcessor()   // its switch follows Settings through leveling()

        fun leveling(on: Boolean) { coverLevel.on = on }
        private var coverFirst = -1L
        private var coverHeardAt = 0L

        val active: Boolean get() = state != "none"

        /** Tuned in: cover this station's ad? Starts the cover if so. */
        fun begin(station: com.frequencies.core.Station?): Boolean {
            if (station == null || !Twitch.coversAd(station)) return false
            seconds = Twitch.adCoverSeconds(station)
            if (seconds <= 0) return false
            val health = library.health.value
            covers = Twitch.coverCandidates(station, library.stations.value, { Health.isDown(health, it) || blocked(it) != null })
            state = "filler"
            deadline = 0L
            tryAt = 0
            staticOn = library.settings.value.staticOn
            // The hourglass follows this clock (player.ad_time_left).
            AdNow.start(seconds, ::timeLeft)
            Log.i(TAG, "[ad] covering the opening ad: ${seconds.toInt()}s, " +
                if (covers.isEmpty()) "no saved frequency can cover it (none without ads that plays straight away): the static covers it"
                else "${covers.size} cover(s) to try: ${covers.joinToString { "'${it.displayName}'" }}")
            playCover()
            return true
        }

        private fun playCover() {
            val cover = covers.getOrNull(tryAt) ?: return
            val renderers = object : DefaultRenderersFactory(this@PlaybackService) {
                override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioTrackPlaybackParams: Boolean): AudioSink =
                    DefaultAudioSink.Builder(context).setAudioProcessors(arrayOf(coverLevel)).build()
            }
            val p = player ?: ExoPlayer.Builder(this@PlaybackService, renderers)
                .setMediaSourceFactory(DefaultMediaSourceFactory(resolving))
                .setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(), false)
                .build().also { p ->
                    player = p
                    p.addListener(object : Player.Listener {
                        override fun onPlayerError(error: PlaybackException) {
                            Log.i(TAG, "[ad] cover '${covers.getOrNull(tryAt)?.displayName}' failed: ${error.errorCodeName}")
                            tryAt++
                            if (state == "filler" && tryAt < covers.size) playCover()
                            else if (state == "filler") Log.i(TAG, "[ad] no cover could be heard: the static covers the ad")
                        }
                    })
                }
            coverFirst = -1L
            coverHeardAt = 0L
            p.setMediaItem(library.mediaItem(cover))
            p.volume = 1f
            p.prepare()
            p.playWhenReady = true
        }

        /**
         * player.ad_time_left: seconds of the covered ad still to run. The
         * whole ad while the channel is still connecting (the ad clock
         * starts when it sends), 0 once the ad time is up, null when no
         * covered ad is running.
         */
        fun timeLeft(): Double? = when (state) {
            "none" -> null
            "static" -> 0.0
            else -> if (deadline == 0L) seconds else maxOf(0.0, (deadline - SystemClock.elapsedRealtime()) / 1000.0)
        }

        /** The cover can be heard: playing, and its position has moved 0.4 s. */
        private fun heard(now: Long): Boolean {
            val p = player ?: return false
            if (!p.isPlaying) { coverFirst = -1L; coverHeardAt = 0L; return false }
            val pos = p.currentPosition
            if (coverFirst < 0) coverFirst = pos
            if (coverHeardAt == 0L && pos - coverFirst >= 400) coverHeardAt = now
            return coverHeardAt != 0L
        }

        /** Every 200 ms while it covers ([moved]: the channel has started sending). */
        fun tick(now: Long, moved: Boolean) {
            val crossfade = library.settings.value.crossfadeMs
            if (state == "filler") {
                // The static fills any moment the cover can't be heard (task 50).
                if (library.settings.value.staticOn) {
                    val h = heard(now)
                    if (h && staticOn) { staticOn = false; static.fadeOut(crossfade) }
                    else if (!h && !staticOn) { staticOn = true; static.start(library.settings.value.staticVolume / 100f) }
                }
                // Counted from when the channel sends: one that took six seconds to buffer hasn't played six seconds of ad.
                if (deadline == 0L && moved) deadline = now + (seconds * 1000).toLong()
                if (deadline != 0L && now >= deadline) {
                    Log.i(TAG, "[ad] ad time is up: static, then the station")
                    state = "static"
                    if (library.settings.value.staticOn) {
                        staticOn = true
                        static.start(library.settings.value.staticVolume / 100f)
                        fadeCover(AD_COVER_OUT_MS)
                    } else {
                        fadeCover(crossfade)
                    }
                    deadline = now + crossfade + 600
                }
                return
            }
            if (state == "static" && now >= deadline) {
                // Over to the station: handed over as after any tune-in, once it can be heard.
                state = "none"
                stopCover()
            }
        }

        private fun fadeCover(ms: Int) {
            val p = player ?: return
            val from = p.volume
            val started = SystemClock.elapsedRealtime()
            handler.post(object : Runnable {
                override fun run() {
                    if (player !== p) return
                    val t = if (ms <= 0) 1f else ((SystemClock.elapsedRealtime() - started).toFloat() / ms).coerceIn(0f, 1f)
                    p.volume = from * (1f - t)
                    if (t < 1f) handler.postDelayed(this, 30) else { p.stop(); p.clearMediaItems() }
                }
            })
        }

        private fun stopCover() {
            player?.run { stop(); clearMediaItems() }
        }

        /** Another station, a pause or a failure: the cover stops. */
        fun cancel() {
            if (state != "none") Log.i(TAG, "[ad] cover stopped")
            state = "none"
            deadline = 0L
            stopCover()
            AdNow.stop()
        }

        fun release() {
            AdNow.stop()
            state = "none"
            player?.release()
            player = null
        }
    }

    /** Shuffle in the notification and the car: lit when on, as the app's button is. */
    private fun shuffleButton(): CommandButton =
        CommandButton.Builder(if (shuffleOn) CommandButton.ICON_SHUFFLE_ON else CommandButton.ICON_SHUFFLE_OFF)
            .setDisplayName(if (shuffleOn) "Shuffle on" else "Shuffle off")
            .setSessionCommand(SHUFFLE)
            .setSlots(CommandButton.SLOT_OVERFLOW)
            .build()

    /** The phone's star (its own glyph), filled when the song playing now is a favorite. */
    private fun favoriteButton(): CommandButton {
        val (hasSong, starred) = buttonState()
        return CommandButton.Builder(if (starred) CommandButton.ICON_STAR_FILLED else CommandButton.ICON_STAR_UNFILLED)
            .setCustomIconResId(if (starred) R.drawable.ic_auto_star_on else R.drawable.ic_auto_star)
            .setDisplayName(if (!hasSong) "Favorite (no song yet)" else if (starred) "Unfavorite" else "Favorite")
            .setSessionCommand(FAVORITE)
            .setSlots(CommandButton.SLOT_BACK, CommandButton.SLOT_OVERFLOW)
            .build()
    }

    /** Song ID's mark, as on the phone's Now Playing. */
    private fun songIdButton(): CommandButton {
        val on = buttonState().songId
        return CommandButton.Builder(CommandButton.ICON_UNDEFINED)
            // Auto paints every button one color, so off is told by its shape: dimmed, with a slash (user, build 186).
            .setCustomIconResId(if (on) R.drawable.song_id_mark else R.drawable.song_id_mark_off)
            .setDisplayName(if (on) "Song ID on" else "Song ID off")
            .setSessionCommand(SONG_ID)
            .setSlots(CommandButton.SLOT_OVERFLOW)
            .build()
    }

    companion object {
        private const val TAG = "Frequencies"
        /** player.CONNECT_TIMEOUT: a stream that makes no sound in 10 s never started. */
        /** Shared with the health probe (core Playlists, build 205). */
        private const val CONNECT_TIMEOUT_MS = Playlists.CONNECT_TIMEOUT_MS
        /** This long without a stream playing reads as bad reception, and a music folder takes over (user, build 197: "make it 15"). */
        private const val WEAK_SILENCE_MS = 15_000L
        private const val YOUTUBE_CONNECT_MS = 30_000L
        /** YouTube videos tried in a row before the station is marked down. */
        private const val YT_TRIES = 3
        /** Songs of a music folder station tried in a row before it moves on (build 198). */
        private const val LOCAL_TRIES = 3
        /** The network failed, not the station: reconnected even if it never played (Recovery.nextWait). */
        private val NETWORK_TROUBLE = setOf(
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
            PlaybackException.ERROR_CODE_TIMEOUT,
        )
        /** Marks a request's own User-Agent for the HTTP client (OkHttpDataSource would add the stream agent beside it). */
        private const val AGENT_HEADER = "X-Frequencies-Agent"
        /** player.AUDIBLE_DB / Player.SILENT_LIMIT: the level meter's hand-over. */
        private const val AUDIBLE_DB = -55.0
        private const val SILENT_LIMIT_MS = 45_000L
        /** player._handle_connected: a pre-buffering station starts anyway this long past its target. */
        private val PREBUFFER_GRACE_MS = (Prebuffer.GRACE_S * 1000).toLong()
        /** player.AD_COVER_OUT_MS: the cover goes in a quick fade under the static. */
        private const val AD_COVER_OUT_MS = 150
        private val THUMBS_DOWN = SessionCommand("com.frequencies.THUMBS_DOWN", Bundle.EMPTY)
        private val SHUFFLE = SessionCommand("com.frequencies.SHUFFLE", Bundle.EMPTY)
        private val FAVORITE = SessionCommand("com.frequencies.FAVORITE", Bundle.EMPTY)
        private val SONG_ID = SessionCommand("com.frequencies.SONG_ID", Bundle.EMPTY)
        /** A ⋯ tag button's command: this, then the tag. */
        private const val TAG_ACTION = "com.frequencies.TAG:"
        /** The static's extra loudness on a car's screen (user, batch 24: "just slightly too quiet"). */
        private const val CAR_STATIC_DB = 3.0
        /** Android Auto is told about changes once they have settled this long. */
        private const val NOTIFY_SETTLE_MS = 1_500L
        /** A tag tile's answer to the car: "the frequency playing now" ([answerTagTap]); never reaches ExoPlayer. */
        private const val KEEP_ID = "com.frequencies.KEEP_PLAYING"
        private val KEEP_ITEM = MediaItem.Builder().setMediaId(KEEP_ID).build()
        /** How long the session shows "loading" when answering a tag tile's tap. */
        private const val TAP_ANSWER_MS = 400L
    }
}
