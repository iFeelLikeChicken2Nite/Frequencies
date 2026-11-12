package com.frequencies.songid

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Base64
import java.util.zip.CRC32

/**
 * Shazam's audio signature, as the desktop's shazamio_core makes it (SongRec's
 * algorithm, GPL v3 like Frequencies): FFTs of 128-sample steps over 16 kHz
 * mono audio, peaks spread across frequency and time, and the peaks between
 * 250 Hz and 5.5 kHz written in Shazam's binary format.
 *
 * From the same 16 kHz samples this gives the very bytes shazamio_core 2.0.0
 * gives (SignatureTest compares them on recordings the desktop fingerprinted).
 */
class Signature private constructor(
    /** How many 16 kHz samples went in. */
    val sampleCount: Int,
    private val bands: Map<Int, List<Peak>>,
) {
    internal class Peak(val pass: Int, val magnitude: Int, val bin: Int)

    /** What Shazam's request calls `samplems`: the audio's length in milliseconds. */
    val durationMs: Int get() = (sampleCount.toFloat() / SongId.SAMPLE_RATE.toFloat() * 1000f).toInt()

    /** The `uri` field of Shazam's request. */
    val uri: String get() = "data:audio/vnd.shazam.sig;base64," + Base64.getEncoder().encodeToString(encode())

    val peakCount: Int get() = bands.values.sumOf { it.size }

    /** signature_format.rs encode_to_binary. */
    fun encode(): ByteArray {
        val body = ByteArrayOutputStream()
        for (band in bands.keys.sorted()) {
            val peaks = ByteArrayOutputStream()
            var pass = 0
            for (peak in bands.getValue(band)) {
                if (peak.pass - pass >= 255) {
                    peaks.write(0xff)
                    peaks.write(le32(peak.pass))
                    pass = peak.pass
                }
                peaks.write(peak.pass - pass)
                peaks.write(le16(peak.magnitude))
                peaks.write(le16(peak.bin))
                pass = peak.pass
            }
            val bytes = peaks.toByteArray()
            body.write(le32(0x60030040 + band))
            body.write(le32(bytes.size))
            body.write(bytes)
            repeat((4 - bytes.size % 4) % 4) { body.write(0) }
        }

        val content = body.toByteArray()
        val total = 48 + 8 + content.size
        val out = ByteBuffer.allocate(total).order(ByteOrder.LITTLE_ENDIAN)
        out.putInt(0xcafe2580.toInt())
        out.putInt(0)                                   // crc32, below
        out.putInt(total - 48)
        out.putInt(0x94119c00.toInt())
        out.putInt(0); out.putInt(0); out.putInt(0)
        out.putInt(3 shl 27)                            // 16 kHz
        out.putInt(0); out.putInt(0)
        out.putInt(sampleCount + (SongId.SAMPLE_RATE.toFloat() * 0.24f).toInt())
        out.putInt((15 shl 19) + 0x40000)
        out.putInt(0x40000000)
        out.putInt(total - 48)
        out.put(content)
        val bytes = out.array()
        val crc = CRC32().apply { update(bytes, 8, bytes.size - 8) }.value
        ByteBuffer.wrap(bytes, 4, 4).order(ByteOrder.LITTLE_ENDIAN).putInt(crc.toInt())
        return bytes
    }

    companion object {
        /** algorithm.rs make_signature_from_buffer: 16 kHz mono 16-bit samples in. */
        fun make(samples: ShortArray): Signature = Generator(samples.size).run {
            var at = 0
            while (at + 128 <= samples.size) {
                step(samples, at)
                at += 128
            }
            Signature(samples.size, bands)
        }

        private fun le32(v: Int) = byteArrayOf(v.toByte(), (v shr 8).toByte(), (v shr 16).toByte(), (v shr 24).toByte())
        private fun le16(v: Int) = byteArrayOf(v.toByte(), (v shr 8).toByte())
    }

    private class Generator(@Suppress("unused") val count: Int) {
        val ring = ShortArray(2048)
        var ringIndex = 0
        val frame = FloatArray(2048)
        val fftOut = FloatArray(2050)
        val ffts = Array(256) { FloatArray(1025) }
        var fftIndex = 0
        val spread = Array(256) { FloatArray(1025) }
        var spreadIndex = 0
        var spreadDone = 0
        val fft = RealFft2048()
        val bands = HashMap<Int, MutableList<Peak>>()

        fun step(samples: ShortArray, from: Int) {
            doFft(samples, from)
            doPeakSpreading()
            spreadDone++
            if (spreadDone >= 46) doPeakRecognition()
        }

        private fun doFft(samples: ShortArray, from: Int) {
            System.arraycopy(samples, from, ring, ringIndex, 128)
            ringIndex = (ringIndex + 128) and 2047
            val window = Hanning.WINDOW
            for (i in 0 until 2048) frame[i] = ring[(i + ringIndex) and 2047].toFloat() * window[i]
            fft.forward(frame, fftOut)
            val out = ffts[fftIndex]
            for (i in 0..1024) {
                val re = fftOut[2 * i]
                val im = fftOut[2 * i + 1]
                out[i] = maxOf((re * re + im * im) / (1 shl 17).toFloat(), 0.0000000001f)
            }
            fftIndex = (fftIndex + 1) and 255
        }

        private fun doPeakSpreading() {
            val real = ffts[(fftIndex - 1) and 255]
            val current = spread[spreadIndex]
            System.arraycopy(real, 0, current, 0, 1025)
            for (p in 0..1022) current[p] = maxOf(maxOf(current[p], current[p + 1]), current[p + 2])
            val copy = current.copyOf()
            for (p in 0..1024) {
                for (former in FORMER) {
                    val f = spread[(spreadIndex - former) and 255]
                    f[p] = maxOf(f[p], copy[p])
                }
            }
            spreadIndex = (spreadIndex + 1) and 255
        }

        private fun doPeakRecognition() {
            val f46 = ffts[(fftIndex - 46) and 255]
            val f49 = spread[(spreadIndex - 49) and 255]
            for (bin in 10..1014) {
                val v = f46[bin]
                if (v < 1f / 64f || v < f49[bin - 1]) continue
                var maxNeighbor = 0f
                for (o in NEIGHBORS) maxNeighbor = maxOf(maxNeighbor, f49[bin + o])
                if (v <= maxNeighbor) continue
                var maxOther = maxNeighbor
                for (o in OTHERS) maxOther = maxOf(maxOther, spread[(spreadIndex + o) and 255][bin - 1])
                if (v <= maxOther) continue

                val pass = spreadDone - 46
                val mag = magnitude(v)
                val before = magnitude(f46[bin - 1])
                val after = magnitude(f46[bin + 1])
                val variation1 = mag * 2f - before - after
                val variation2 = (after - before) * 32f / variation1
                val corrected = ((bin * 64) + variation2.toInt()) and 0xffff
                val hz = corrected.toFloat() * (16000f / 2f / 1024f / 64f)
                val band = when (hz.toInt()) {
                    in 250..519 -> 0
                    in 520..1449 -> 1
                    in 1450..3499 -> 2
                    in 3500..5500 -> 3
                    else -> continue
                }
                bands.getOrPut(band) { ArrayList() }.add(Peak(pass, mag.toUShortSaturated(), corrected))
            }
        }

        // ln(x).max(1/64) * 1477.3 + 6144, in f32.
        private fun magnitude(x: Float): Float = maxOf(lnf(x), 1f / 64f) * 1477.3f + 6144f

        companion object {
            val FORMER = intArrayOf(1, 3, 6)
            val NEIGHBORS = intArrayOf(-10, -7, -4, -3, 1, 2, 5, 8)
            val OTHERS = intArrayOf(-53, -45, 165, 172, 179, 186, 193, 200, 214, 221, 228, 235, 242, 249)
        }
    }
}

/**
 * Rust's f32::ln, which is the C library's logf: glibc's (and musl's) logf from
 * Arm's optimized routines, ported line for line. It is within 0.82 of a last
 * bit, not exact, so rounding the true logarithm gave a peak one step louder
 * now and then; this gives the same float glibc does.
 */
internal fun lnf(x: Float): Float {
    var ix = x.toRawBits()
    if (ix == 0x3f800000) return 0f
    if ((ix - 0x00800000).toUInt() >= (0x7f800000 - 0x00800000).toUInt()) {
        if (ix * 2 == 0) return Float.NEGATIVE_INFINITY
        if (ix == 0x7f800000) return x
        if ((ix and 0x80000000.toInt()) != 0 || (ix * 2).toUInt() >= 0xff000000u) return Float.NaN
        ix = (x * 8388608f).toRawBits()          // 0x1p23f: subnormal, normalize it
        ix -= 23 shl 23
    }
    val tmp = ix - 0x3f330000
    val i = (tmp ushr (23 - 4)) % 16
    val k = tmp shr 23
    val iz = ix - (tmp and 0xff800000.toInt())
    val invc = LOGF_INVC[i]
    val logc = LOGF_LOGC[i]
    val z = Float.fromBits(iz).toDouble()
    val r = z * invc - 1
    val y0 = logc + k.toDouble() * LOGF_LN2
    val r2 = r * r
    var y = LOGF_A1 * r + LOGF_A2
    y = LOGF_A0 * r2 + y
    y = y * r2 + (y0 + r)
    return y.toFloat()
}

private fun hex(s: String) = java.lang.Double.parseDouble(s)

private val LOGF_INVC = doubleArrayOf(
    hex("0x1.661ec79f8f3bep+0"), hex("0x1.571ed4aaf883dp+0"), hex("0x1.49539f0f010bp+0"), hex("0x1.3c995b0b80385p+0"),
    hex("0x1.30d190c8864a5p+0"), hex("0x1.25e227b0b8eap+0"), hex("0x1.1bb4a4a1a343fp+0"), hex("0x1.12358f08ae5bap+0"),
    hex("0x1.0953f419900a7p+0"), hex("0x1p+0"), hex("0x1.e608cfd9a47acp-1"), hex("0x1.ca4b31f026aap-1"),
    hex("0x1.b2036576afce6p-1"), hex("0x1.9c2d163a1aa2dp-1"), hex("0x1.886e6037841edp-1"), hex("0x1.767dcf5534862p-1"),
)
private val LOGF_LOGC = doubleArrayOf(
    hex("-0x1.57bf7808caadep-2"), hex("-0x1.2bef0a7c06ddbp-2"), hex("-0x1.01eae7f513a67p-2"), hex("-0x1.b31d8a68224e9p-3"),
    hex("-0x1.6574f0ac07758p-3"), hex("-0x1.1aa2bc79c81p-3"), hex("-0x1.a4e76ce8c0e5ep-4"), hex("-0x1.1973c5a611cccp-4"),
    hex("-0x1.252f438e10c1ep-5"), hex("0x0p+0"), hex("0x1.aa5aa5df25984p-5"), hex("0x1.c5e53aa362eb4p-4"),
    hex("0x1.526e57720db08p-3"), hex("0x1.bc2860d22477p-3"), hex("0x1.1058bc8a07ee1p-2"), hex("0x1.4043057b6ee09p-2"),
)
private val LOGF_LN2 = hex("0x1.62e42fefa39efp-1")
private val LOGF_A0 = hex("-0x1.00ea348b88334p-2")
private val LOGF_A1 = hex("0x1.5575b0be00b6ap-2")
private val LOGF_A2 = hex("-0x1.ffffef20a4123p-2")

// Rust's `as u16` from f32: toward zero, clamped to 0..65535.
private fun Float.toUShortSaturated(): Int = when {
    isNaN() -> 0
    this <= 0f -> 0
    this >= 65535f -> 65535
    else -> toInt()
}
