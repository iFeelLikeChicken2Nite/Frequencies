package com.frequencies.app.playback

import android.os.SystemClock
import android.util.Log
import androidx.media3.common.Timeline
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.LoadControl
import com.frequencies.core.Prebuffer

/**
 * Per-station pre-buffering (desktop "Pre-buffer this station"): Media3's
 * normal buffer for every station, except that a flagged one starts (and
 * restarts after a drop-out) only once [targetMs] of audio is buffered, and
 * keeps loading at least that far ahead. Like the desktop's player it
 * starts anyway after the target plus 12 s. Stations without the setting
 * get DefaultLoadControl's answers untouched.
 */
@UnstableApi
class PrebufferLoadControl(private val targetMs: (String) -> Long) : DefaultLoadControl() {
    private val period = Timeline.Period()
    private val window = Timeline.Window()
    private var waitingFor: Any? = null
    private var waiting = false
    private var waitingSince = 0L
    private var said: Any? = null

    /** The pre-buffer for the station this media period plays (0: none). Playback thread. */
    private fun target(parameters: LoadControl.Parameters): Long = runCatching {
        val timeline = parameters.timeline
        if (timeline.isEmpty) return 0L
        timeline.getPeriodByUid(parameters.mediaPeriodId.periodUid, period)
        timeline.getWindow(period.windowIndex, window).mediaItem.mediaId.let(targetMs)
    }.getOrDefault(0L)

    override fun shouldContinueLoading(parameters: LoadControl.Parameters): Boolean {
        val target = target(parameters)
        if (target > 0 && parameters.bufferedDurationUs < (target + 2_000) * 1000) return true
        return super.shouldContinueLoading(parameters)
    }

    override fun shouldStartPlayback(parameters: LoadControl.Parameters): Boolean {
        val target = target(parameters)
        if (target <= 0) return super.shouldStartPlayback(parameters)
        val key = parameters.mediaPeriodId.periodUid
        val now = SystemClock.elapsedRealtime()
        // A new station, or the same one waiting again after a drop-out: the clock starts now.
        if (waitingFor != key || !waiting) { waitingFor = key; waiting = true; waitingSince = now }
        val buffered = parameters.bufferedDurationUs / 1000
        if (buffered >= target) {
            if (said != key) { said = key; Log.i(TAG, "[stream] pre-buffered ${buffered / 1000.0} s (target ${target / 1000.0} s)") }
            waiting = false
            return true
        }
        // Started anyway: the stream can't fill the buffer that fast (desktop: target + 12 s).
        if (now - waitingSince > target + (Prebuffer.GRACE_S * 1000).toLong()) {
            if (said != key) { said = key; Log.i(TAG, "[stream] pre-buffer: only ${buffered / 1000.0} of ${target / 1000.0} s after waiting, starting anyway") }
            val start = super.shouldStartPlayback(parameters)
            if (start) waiting = false
            return start
        }
        return false
    }

    private companion object { const val TAG = "Frequencies" }
}
