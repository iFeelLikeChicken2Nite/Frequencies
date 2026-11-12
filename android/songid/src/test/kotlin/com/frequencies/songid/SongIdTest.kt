package com.frequencies.songid

import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * The Kotlin signature against the desktop's shazamio_core 2.0.0, byte for byte.
 *
 * Each `<name>.uri` is what shazamio_core's Recognizer.recognize_path gave for
 * a WAV (`tools/make_songid_fixtures.sh` says how), and `<name>.raw` is the
 * 16 kHz audio that call fingerprinted, written by shazamio_core's own decoder
 * and resampler (the same Rust source, built as a small harness). Android
 * hands the signature its own 16 kHz audio, so what has to match is the step
 * from those samples to the bytes Shazam receives. 16 recordings were checked
 * this way in AP5 (all the same bytes); these six are kept.
 */
class SongIdTest {
    private fun resource(name: String) = javaClass.getResourceAsStream("/desktop/$name")!!.use { it.readBytes() }

    private fun samples(name: String): ShortArray {
        val bytes = ByteBuffer.wrap(resource("$name.raw")).order(ByteOrder.LITTLE_ENDIAN)
        return ShortArray(bytes.remaining() / 2) { bytes.short }
    }

    private fun check(name: String) {
        val expected = resource("$name.uri").decodeToString().trim()
        val signature = Signature.make(samples(name))
        assertEquals("$name: signature bytes", expected, signature.uri)
    }

    @Test fun llamaClip() = check("llama")
    @Test fun stationStatic() = check("tuning6")
    @Test fun tenSecondsFromTheMiddleOfFourteen() = check("synth14")
    @Test fun stereoAt44kHz() = check("synth8_44k_stereo")

    // These two caught the last-bit differences (cos/sin twiddles) before the
    // twiddles were taken from chfft itself: one peak each, one step louder.
    @Test fun dialStatic() = check("dial")
    @Test fun noiseAndTonesAt32kHz() = check("rand4")

    @Test
    fun lnMatchesGlibcLogf() {
        // 20 000 floats between 1e-10 and 1e9 with glibc's logf of each.
        val b = ByteBuffer.wrap(resource("logf-glibc.bin")).order(ByteOrder.LITTLE_ENDIAN)
        var checked = 0
        while (b.remaining() >= 8) {
            val x = b.float
            val want = b.float
            assertEquals("ln($x)", want.toRawBits(), lnf(x).toRawBits())
            checked++
        }
        assertEquals(20_000, checked)
    }

    @Test
    fun durationIsInMilliseconds() {
        // shazamio_core's `samples` field: 8 s of audio is 8000, not 128000.
        assertEquals(8000, Signature.make(samples("synth8_44k_stereo")).durationMs)
    }
}
