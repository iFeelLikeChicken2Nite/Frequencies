package com.frequencies.songid

import kotlin.math.PI
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Mono audio at any rate to the 16 kHz 16-bit samples a signature is made
 * from: a windowed-sinc low-pass at 7.6 kHz and resampling in one step, so
 * nothing above 8 kHz folds back into the band Shazam listens to (simply
 * keeping every third sample of a 48 kHz stream would).
 *
 * The desktop gets its 16 kHz audio from mpv, then shazamio_core's own
 * resampler; this is not those bit for bit (nothing on a phone could be),
 * so SignatureTest checks the signature bytes from the 16 kHz samples, and
 * DownsamplerTest checks this gives nearly the same peaks from the same WAV.
 */
class Downsampler(private val inputRate: Int) {
    private val ratio = inputRate.toDouble() / SongId.SAMPLE_RATE
    private val cutoff = 0.95 * (SongId.SAMPLE_RATE / 2.0) / inputRate    // cycles per input sample
    private val half = ceil(ZERO_CROSSINGS * ratio).toInt()                 // taps each side, in input samples

    // h(u) for u = i / PHASES input samples, i = 0 .. half * PHASES (it is even).
    private val table = DoubleArray(half * PHASES + 2) { i ->
        val u = i.toDouble() / PHASES
        if (u > half) 0.0 else {
            val x = 2.0 * cutoff * u
            val sinc = if (x == 0.0) 1.0 else sin(PI * x) / (PI * x)
            2.0 * cutoff * sinc * kaiser(u / half)
        }
    }

    /** [mono] at the input rate, -1..1, to 16 kHz samples. */
    fun process(mono: FloatArray, count: Int = mono.size): ShortArray {
        if (inputRate == SongId.SAMPLE_RATE) return ShortArray(count) { toShort(mono[it].toDouble()) }
        val out = ShortArray(floor((count - 1) / ratio).toInt() + 1)
        for (n in out.indices) {
            val t = n * ratio
            val center = floor(t).toInt()
            var sum = 0.0
            for (k in center - half + 1..center + half) {
                if (k < 0 || k >= count) continue
                val u = kotlin.math.abs(t - k) * PHASES
                val i = u.toInt()
                val f = u - i
                val h = table[i] + (table[i + 1] - table[i]) * f
                sum += mono[k] * h
            }
            out[n] = toShort(sum)
        }
        return out
    }

    private fun toShort(v: Double): Short = (v * 32767.0).coerceIn(-32768.0, 32767.0).toInt().toShort()

    companion object {
        private const val ZERO_CROSSINGS = 16
        private const val PHASES = 256
        private const val BETA = 8.6

        /** Kaiser window at x in -1..1. */
        private fun kaiser(x: Double): Double {
            if (x <= -1.0 || x >= 1.0) return 0.0
            return bessel0(BETA * sqrt(1 - x * x)) / bessel0(BETA)
        }

        private fun bessel0(x: Double): Double {
            var sum = 1.0
            var term = 1.0
            var k = 1
            while (term > 1e-12 * sum) {
                term *= (x / (2 * k)) * (x / (2 * k))
                sum += term
                k++
            }
            return sum
        }

        /** Interleaved 16-bit PCM to mono floats, the channels averaged (as shazamio_core mixes down). */
        fun mixDown(pcm: ShortArray, channels: Int): FloatArray {
            val frames = pcm.size / channels
            return FloatArray(frames) { f ->
                var s = 0f
                for (c in 0 until channels) s += pcm[f * channels + c]
                s / channels / 32768f
            }
        }
    }
}
