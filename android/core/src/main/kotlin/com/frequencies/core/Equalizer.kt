package com.frequencies.core

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.ln
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sinh

/**
 * The desktop's per-station sound (player.py's filter chain): a ten-band
 * EQ (one-octave peaking filters at the ISO centers) when Settings' Ten-band
 * equalizer is on and a band is set, otherwise bass/mid/treble (two-octave
 * peaking filters at 100 Hz, 1 kHz and 8 kHz), then the station's gain.
 * Each filter is ffmpeg's `equalizer` (RBJ's peaking biquad), so a curve
 * set on one sounds the same on the other.
 */
object Equalizer {
    /** storage.EQ_BAND_FREQUENCIES */
    val BANDS = intArrayOf(31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
    val BAND_LABELS = BANDS.map { if (it >= 1000) "${it / 1000}k" else "$it" }
    const val LIMIT_DB = 12
    const val GAIN_LIMIT_DB = 20.0

    /** One peaking filter: center Hz, gain dB, width in octaves. */
    data class Band(val hz: Double, val gainDb: Double, val octaves: Double)

    fun threeBand(station: Station): IntArray = intArrayOf(
        clampBand(station["eq_bass"]), clampBand(station["eq_mid"]), clampBand(station["eq_treble"]),
    )

    fun tenBand(station: Station): List<Int> = Featured.normalizeEqBands(station["eq_bands"])

    private fun clampBand(e: kotlinx.serialization.json.JsonElement?): Int =
        (e.num()?.toInt() ?: 0).coerceIn(-LIMIT_DB, LIMIT_DB)

    /** player.py: the filters for this station, none when everything is flat. */
    fun bands(station: Station, advanced: Boolean): List<Band> {
        val ten = tenBand(station)
        if (advanced && ten.any { it != 0 }) {
            return BANDS.indices.filter { ten[it] != 0 }.map { Band(BANDS[it].toDouble(), ten[it].toDouble(), 1.0) }
        }
        val (bass, mid, treble) = threeBand(station).toList()
        if (bass == 0 && mid == 0 && treble == 0) return emptyList()
        // All three, as the desktop's filter string has all three.
        return listOf(Band(100.0, bass.toDouble(), 2.0), Band(1000.0, mid.toDouble(), 2.0), Band(8000.0, treble.toDouble(), 2.0))
    }

    fun gainDb(station: Station): Double = (station.gainDb).coerceIn(-GAIN_LIMIT_DB, GAIN_LIMIT_DB)

    /** Biquad coefficients, normalized by a0: b0, b1, b2, a1, a2. */
    fun peaking(band: Band, sampleRate: Int): DoubleArray? {
        // A band at or past Nyquist can't be built (a 16 kHz band at 22 kHz).
        if (band.hz >= sampleRate * 0.49 || band.gainDb == 0.0) return null
        val w0 = 2 * PI * band.hz / sampleRate
        val alpha = sin(w0) * sinh(ln(2.0) / 2 * band.octaves * w0 / sin(w0))
        val a = 10.0.pow(band.gainDb / 40)
        val cosW = cos(w0)
        val a0 = 1 + alpha / a
        return doubleArrayOf(
            (1 + alpha * a) / a0, (-2 * cosW) / a0, (1 - alpha * a) / a0,
            (-2 * cosW) / a0, (1 - alpha / a) / a0,
        )
    }

    /** The response of [coefficients] at [hz], in dB (for tests and the curve). */
    fun responseDb(coefficients: DoubleArray, hz: Double, sampleRate: Int): Double {
        val w = 2 * PI * hz / sampleRate
        val (b0, b1, b2, a1, a2) = coefficients.toList()
        fun mag(c0: Double, c1: Double, c2: Double): Double {
            val re = c0 + c1 * cos(w) + c2 * cos(2 * w)
            val im = -(c1 * sin(w) + c2 * sin(2 * w))
            return re * re + im * im
        }
        return 10 * kotlin.math.log10(mag(b0, b1, b2) / mag(1.0, a1, a2))
    }

    /**
     * The filters and gain running over interleaved float samples, each
     * channel with its own state. Rebuilt (cheaply) when the station, a
     * band or the sample rate changes; state carries over when the shape
     * stays the same, so moving a slider doesn't click.
     */
    class Chain(bands: List<Band>, gainDb: Double, val sampleRate: Int, val channels: Int) {
        private var coefficients: List<DoubleArray> = bands.mapNotNull { peaking(it, sampleRate) }
        private var state = Array(coefficients.size * channels) { DoubleArray(2) }
        private var gain = 10.0.pow(gainDb / 20)

        val isFlat: Boolean get() = coefficients.isEmpty() && gain == 1.0

        fun update(bands: List<Band>, gainDb: Double) {
            val next = bands.mapNotNull { peaking(it, sampleRate) }
            if (next.size != coefficients.size) state = Array(next.size * channels) { DoubleArray(2) }
            coefficients = next
            gain = 10.0.pow(gainDb / 20)
        }

        /** In place over [frames] frames of [samples]. */
        fun process(samples: FloatArray, frames: Int) {
            val coefs = coefficients
            for (f in coefs.indices) {
                val c = coefs[f]
                val b0 = c[0]; val b1 = c[1]; val b2 = c[2]; val a1 = c[3]; val a2 = c[4]
                for (ch in 0 until channels) {
                    val s = state[f * channels + ch]
                    var z1 = s[0]
                    var z2 = s[1]
                    var i = ch
                    repeat(frames) {
                        val x = samples[i].toDouble()
                        // Transposed direct form II.
                        val y = b0 * x + z1
                        z1 = b1 * x - a1 * y + z2
                        z2 = b2 * x - a2 * y
                        samples[i] = y.toFloat()
                        i += channels
                    }
                    s[0] = z1
                    s[1] = z2
                }
            }
            if (gain != 1.0) {
                val g = gain.toFloat()
                for (i in 0 until frames * channels) samples[i] *= g
            }
        }
    }
}
