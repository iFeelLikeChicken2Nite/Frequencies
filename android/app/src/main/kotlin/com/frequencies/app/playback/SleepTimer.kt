package com.frequencies.app.playback

import android.os.SystemClock
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * The sleep timer (desktop.start_sleep_timer): set from Now Playing, run
 * by the playback service, which fades out or hands over to another
 * frequency when it ends (Settings > Sound > When the sleep timer ends).
 */
object SleepTimer {
    /** mainwindow.SLEEP_CHOICES */
    val CHOICES = listOf(15, 30, 45, 60, 90, 120)

    /** elapsedRealtime when it ends, or null when it isn't running. */
    private val _endsAt = MutableStateFlow<Long?>(null)
    val endsAt: StateFlow<Long?> = _endsAt.asStateFlow()

    fun start(minutes: Int) { _endsAt.value = SystemClock.elapsedRealtime() + maxOf(1, minutes) * 60_000L }
    fun cancel() { _endsAt.value = null }

    fun remainingMs(): Long? = _endsAt.value?.let { (it - SystemClock.elapsedRealtime()).coerceAtLeast(0) }

    /** "15 minutes", "1 hour", "1 hour 30 minutes", "2 hours" (mainwindow._sleep_menu). */
    fun label(minutes: Int): String =
        if (minutes == 1) "1 minute"
        else if (minutes < 60) "$minutes minutes"
        else "${minutes / 60} hour" + (if (minutes >= 120) "s" else "") + (if (minutes % 60 != 0) " ${minutes % 60} minutes" else "")

    val ACTIONS = listOf(
        "fade" to "Fade to silence",
        "switch" to "Switch to another frequency",
        "tag" to "Switch to a frequency tagged…",
    )
}
