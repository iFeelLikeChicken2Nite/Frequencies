package com.frequencies.app.playback

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * The covered opening ad, for the player bar's hourglass (the desktop's
 * mainwindow.ad_cover_started / PlayerBar.set_ad_cover): how long the ad
 * is ([cover], null when none is covered) and the player's own ad clock.
 * The playback service sets it; the screens only read it.
 */
object AdNow {
    /** One covered ad: its length, and a number of its own so the same length twice is two ads. */
    data class Cover(val seconds: Double, val serial: Long)

    private val _cover = MutableStateFlow<Cover?>(null)
    val cover: StateFlow<Cover?> = _cover.asStateFlow()
    private var serial = 0L

    @Volatile private var clock: () -> Double? = { null }

    /** A covered ad of [total] seconds began; [timeLeft] is the player's ad clock. */
    fun start(total: Double, timeLeft: () -> Double?) {
        clock = timeLeft
        _cover.value = Cover(total, ++serial)
    }

    /**
     * The cover stopped (another station, a pause, a failure). The glass
     * goes now; when the ad simply ran out, the glass sees 0 from the
     * clock first and shows itself empty for a moment.
     */
    fun stop() {
        clock = { null }
        _cover.value = null
    }

    /** Seconds of the ad still to run, 0 once its time is up, null when none is covered. */
    fun timeLeft(): Double? = runCatching { clock() }.getOrNull()
}
