package com.frequencies.core

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * The desktop's loudness leveling, Settings' "Even out loudness between
 * stations" (the synced `normalize` setting, on by default). The desktop
 * runs mpv's `dynaudnorm=f=200:g=15:p=0.95` ahead of the EQ and the
 * station's gain trim (player.build_filter_chain); these are the same
 * steps as ffmpeg's dynaudnorm with those numbers and its other defaults
 * (channels coupled, at most 10x gain, no RMS target, no compression):
 *
 *  1. The sound is cut into 200 ms frames. Each frame's gain is what
 *     brings its loudest sample to 0.95, softly capped at 10x (+20 dB).
 *  2. Each frame's gain becomes the smallest gain of the 15 frames around
 *     it (a loud moment coming up pulls the gain down before it arrives),
 *     then a gaussian average over those 15 frames smooths it, never above
 *     the next frame's own gain (so the glide below can't overshoot).
 *  3. Inside a frame the gain glides from the last frame's to this one's,
 *     so there are no steps to hear.
 *
 * Looking 7 frames ahead and smoothing over 15 means a frame leaves 14
 * frames (2.8 s) after it came in, as on the desktop (dynaudnorm holds
 * about 3 s back). With [on] false every new frame gets gain 1.0, so the
 * gain glides back to exactly 1 and the sound comes out unchanged; turning
 * it back on glides up again, with no gap either way.
 *
 * [lookAhead] false lets the sound through at once, unleveled: songs from
 * a music folder, iBroadcast or YouTube, which end and go on to the next
 * song (holding 2.8 s back would cut or hold up each song's end on
 * Android). Switching it off hands out what is held first, so nothing is
 * lost. Not thread-safe: one audio thread pushes and reads.
 */
class Leveler(
    val rate: Int,
    val channels: Int,
    frameMs: Int = FRAME_MS,
    private val window: Int = WINDOW,
    private val peak: Double = PEAK,
    private val maxGain: Double = MAX_GAIN,
) {
    companion object {
        /** dynaudnorm f= (ms), g= (frames, odd), p=, and m= (its default). */
        const val FRAME_MS = 200
        const val WINDOW = 15
        const val PEAK = 0.95
        const val MAX_GAIN = 10.0
        private const val EPSILON = 2.220446049250313e-16   // DBL_EPSILON

        /** dynaudnorm's soft cap: about [value] when small, never above [limit]. */
        fun bound(limit: Double, value: Double): Double = erf(0.8862269254527580 * (value / limit)) * limit

        /** A frame's own gain: its loudest sample to [peak], softly capped at [maxGain]. */
        fun frameGain(loudest: Double, peak: Double = PEAK, maxGain: Double = MAX_GAIN): Double =
            bound(maxGain, peak / max(loudest, EPSILON))

        /** The gaussian weights dynaudnorm smooths with (sum 1). */
        fun weights(size: Int): DoubleArray {
            val sigma = ((size / 2.0) - 1.0) / 3.0 + 1.0 / 3.0
            val offset = size / 2
            val c1 = 1.0 / (sigma * sqrt(2.0 * Math.PI))
            val c2 = 2.0 * sigma * sigma
            val w = DoubleArray(size) { i -> val x = (i - offset).toDouble(); c1 * exp(-(x * x) / c2) }
            val total = w.sum()
            for (i in w.indices) w[i] /= total
            return w
        }

        /** erf, Abramowitz and Stegun 7.1.26 (within 1.5e-7). */
        fun erf(x: Double): Double {
            val t = 1.0 / (1.0 + 0.3275911 * abs(x))
            val y = 1.0 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * exp(-x * x)
            return if (x >= 0) y else -y
        }
    }

    /** Samples per channel in a frame (ffmpeg rounds it up to even). */
    val frameLen: Int = (rate * frameMs / 1000.0).roundToInt().let { it + it % 2 }.coerceAtLeast(2)

    /** Settings' switch; read as each frame is measured. */
    var on = true

    /** False: no leveling and no holding back (a song); see the class notes. Turning it off hands out what is held. */
    var lookAhead = true
        set(value) {
            if (field == value) return
            field = value
            if (!value) drain()
        }

    /** The gain now going out, in dB (0 while nothing has been leveled). Safe to read from any thread. */
    @Volatile var gainDb = 0.0
        private set

    private val weights = weights(window)
    private val half = window / 2
    private val original = ArrayDeque<Double>()
    private val minimum = ArrayDeque<Double>()
    private val smoothed = ArrayDeque<Double>()
    /** Measured frames waiting for their smoothed gain, and their lengths. */
    private val held = ArrayDeque<FloatArray>()
    private val heldLen = ArrayDeque<Int>()
    private var fill = FloatArray(frameLen * channels)
    private var filled = 0          // samples (all channels) in [fill]
    private var prev = 1.0
    private var started = false

    /** Leveled sound ready to go, oldest first. */
    private val ready = ArrayDeque<FloatArray>()
    private val readyLen = ArrayDeque<Int>()
    private var readAt = 0
    private var readyCount = 0
    private val spare = ArrayDeque<FloatArray>()

    /** Samples (all channels) ready to [read]. */
    fun available(): Int = readyCount

    /** How much sound is held back now, in milliseconds. */
    fun heldMs(): Long = (heldLen.sum().toLong() + filled / channels) * 1000L / rate

    /** Takes [count] interleaved samples (-1..1). */
    fun push(samples: FloatArray, count: Int) {
        if (!lookAhead) {
            emit(samples, 0, count)
            return
        }
        var at = 0
        while (at < count) {
            val n = min(count - at, fill.size - filled)
            System.arraycopy(samples, at, fill, filled, n)
            filled += n
            at += n
            if (filled == fill.size) {
                measure(fill, frameLen)
                fill = take()
                filled = 0
            }
        }
    }

    /** Copies up to [dst].size ready samples into [dst]; returns how many. */
    fun read(dst: FloatArray, count: Int = dst.size): Int {
        var done = 0
        while (done < count && ready.isNotEmpty()) {
            val buf = ready.first()
            val len = readyLen.first()
            val n = min(count - done, len - readAt)
            System.arraycopy(buf, readAt, dst, done, n)
            done += n
            readAt += n
            if (readAt == len) {
                ready.removeFirst(); readyLen.removeFirst(); readAt = 0
                if (buf.size == frameLen * channels) spare.addLast(buf)
            }
        }
        readyCount -= done
        return done
    }

    /** The end of the sound: everything held is leveled and made ready (dynaudnorm's flush at the end). */
    fun drain() {
        if (filled > 0) {
            measure(fill, filled / channels)
            fill = take()
            filled = 0
        }
        // ffmpeg ends by feeding frames at exactly the target peak (gain 1) until every real frame is out.
        val flushGain = if (on) frameGain(peak, peak, maxGain) else 1.0
        var guard = held.size + 2 * window + 2
        while (held.isNotEmpty() && guard-- > 0) {
            addGain(flushGain)
            release()
        }
        restart()
    }

    /** A new station or a seek: what is held belongs to the old sound. */
    fun flush() {
        while (held.isNotEmpty()) { spare(held.removeFirst()) }
        heldLen.clear()
        while (ready.isNotEmpty()) { spare(ready.removeFirst()) }
        readyLen.clear(); readAt = 0; readyCount = 0
        filled = 0
        restart()
    }

    private fun restart() {
        original.clear(); minimum.clear(); smoothed.clear()
        prev = 1.0
        started = false
        gainDb = 0.0
    }

    private fun take(): FloatArray = spare.removeLastOrNull() ?: FloatArray(frameLen * channels)
    private fun spare(buf: FloatArray) { if (buf.size == frameLen * channels) spare.addLast(buf) }

    /** One frame in: its gain joins the history and it waits for its smoothed gain. */
    private fun measure(frame: FloatArray, len: Int) {
        val gain = if (on) {
            var loudest = EPSILON
            for (i in 0 until len * channels) { val v = abs(frame[i]).toDouble(); if (v > loudest) loudest = v }
            frameGain(loudest, peak, maxGain)
        } else 1.0
        held.addLast(frame); heldLen.addLast(len)
        addGain(gain)
        release()
    }

    /** dynaudnorm's update_gain_history: the minimum filter, then the gaussian. */
    private fun addGain(gain: Double) {
        if (!started) {
            started = true
            val initial = min(1.0, gain)
            prev = initial
            repeat(half) { original.addLast(initial) }
        }
        original.addLast(gain)
        while (original.size >= window) {
            if (minimum.isEmpty()) {
                var initial = 1.0
                var input = half
                while (minimum.size < half) {
                    input++
                    initial = min(initial, original[input])
                    minimum.addLast(initial)
                }
            }
            var low = Double.MAX_VALUE
            for (g in original) if (g < low) low = g
            minimum.addLast(low)
            original.removeFirst()
        }
        while (minimum.size >= window) {
            var sum = 0.0
            for (i in 0 until window) sum += minimum[i] * weights[i]
            // Never above the oldest gain still in the history (the next frame's): the glide into a
            // loud frame then can't overshoot (ffmpeg: limit = gain_history_original[0]).
            smoothed.addLast(min(sum, original.first()))
            minimum.removeFirst()
        }
    }

    /** Frames whose smoothed gain is known go out, the gain gliding from the last frame's. */
    private fun release() {
        while (smoothed.isNotEmpty() && held.isNotEmpty()) {
            val frame = held.removeFirst()
            val len = heldLen.removeFirst()
            var gain = smoothed.removeFirst()
            if (abs(gain - 1.0) < 1e-9) gain = 1.0
            val from = prev
            if (from != 1.0 || gain != 1.0) {
                val step = 1.0 / len
                var k = 0
                for (i in 0 until len) {
                    val f0 = 1.0 - step * (i + 1.0)
                    val g = (f0 * from + (1.0 - f0) * gain).toFloat()
                    for (c in 0 until channels) { frame[k] *= g; k++ }
                }
            }
            prev = gain
            gainDb = 20 * log10(max(gain, 1e-9))
            ready.addLast(frame); readyLen.addLast(len * channels); readyCount += len * channels
        }
        // Smoothed gains past the last real frame (the end's flush frames) are not needed.
        if (held.isEmpty()) smoothed.clear()
    }

    private fun emit(samples: FloatArray, from: Int, count: Int) {
        if (count <= 0) return
        val buf = FloatArray(count)
        System.arraycopy(samples, from, buf, 0, count)
        ready.addLast(buf); readyLen.addLast(count); readyCount += count
    }
}
