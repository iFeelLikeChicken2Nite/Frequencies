package com.frequencies.app

import android.app.Application
import kotlinx.coroutines.launch

class FrequenciesApp : Application() {
    lateinit var library: Library
        private set
    lateinit var artUpgrader: ArtUpgrader
        private set
    lateinit var accent: Accent
        private set
    lateinit var artists: ArtistKit
        private set
    lateinit var health: HealthChecks
        private set
    lateinit var schedule: ScheduleWatch
        private set
    lateinit var localMusic: LocalMusic
        private set
    lateinit var ibroadcast: IBroadcastKeeper
        private set
    lateinit var sync: SyncKeeper
        private set
    /** YouTube through NewPipeExtractor, made on first use (only YouTube stations need it). */
    val youtube: YouTubeSource by lazy { YouTubeSource(this, artists.http) }
    /** Work that outlives a screen: pack downloads, thumbs-down reports. */
    val scope = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.SupervisorJob() + kotlinx.coroutines.Dispatchers.Main)

    override fun onCreate() {
        super.onCreate()
        library = Library(this)
        localMusic = LocalMusic(this)
        // Offline mode (task 214): one answer for the whole app.
        Connectivity.start(this, scope)
        library.offline = { Connectivity.offline.value }
        library.unavailable = { s ->
            when {
                !localMusic.onThisPhone(s) -> com.frequencies.core.OfflineRules.NOT_HERE
                Connectivity.offline.value && com.frequencies.core.OfflineRules.needsInternet(s) -> "Needs the internet"
                else -> null
            }
        }
        artUpgrader = ArtUpgrader(library).also { it.start() }
        accent = Accent(this, library)
        artists = ArtistKit(this, library, scope).also { it.keeper.start(); it.checkLogos() }
        ibroadcast = IBroadcastKeeper(this, scope).also { it.start() }
        // Thumbs-down reports an earlier run left waiting go out once online.
        if (artists.queueWaiting()) ReportWorker.enqueue(this, 0)
        scope.launch(kotlinx.coroutines.Dispatchers.IO) { runCatching { localMusic.cleanUnpacked(library.stations.value) } }
        health = HealthChecks(this, library, scope)
        schedule = ScheduleWatch(this, library, scope).also { it.start() }
        HealthWorker.schedule(this, library.settings.value.bool("health_recheck", true))
        // Sync with a paired desktop (task 126): only while the app is on screen (MainActivity).
        sync = SyncKeeper(this, library, localMusic, scope) { keys ->
            val settings = library.settings.value
            if (com.frequencies.core.PhotoPackStore.SETTING in keys) artists.keeper.setEnabled(settings.bool(com.frequencies.core.PhotoPackStore.SETTING))
            if ("health_recheck" in keys) HealthWorker.schedule(this, settings.bool("health_recheck", true))
        }
    }
}

val android.content.Context.library: Library
    get() = (applicationContext as FrequenciesApp).library

val android.content.Context.artUpgrader: ArtUpgrader
    get() = (applicationContext as FrequenciesApp).artUpgrader

val android.content.Context.accent: Accent
    get() = (applicationContext as FrequenciesApp).accent

val android.content.Context.artists: ArtistKit
    get() = (applicationContext as FrequenciesApp).artists

val android.content.Context.health: HealthChecks
    get() = (applicationContext as FrequenciesApp).health

val android.content.Context.localMusic: LocalMusic
    get() = (applicationContext as FrequenciesApp).localMusic

val android.content.Context.schedule: ScheduleWatch
    get() = (applicationContext as FrequenciesApp).schedule

val android.content.Context.ibroadcast: IBroadcastKeeper
    get() = (applicationContext as FrequenciesApp).ibroadcast

val android.content.Context.sync: SyncKeeper
    get() = (applicationContext as FrequenciesApp).sync

val android.content.Context.youtube: YouTubeSource
    get() = (applicationContext as FrequenciesApp).youtube
