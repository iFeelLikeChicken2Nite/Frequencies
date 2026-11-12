package com.frequencies.songid

import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Base64

/**
 * The phone's own way to 16 kHz against the desktop's (mpv + rubato), on the
 * same 44.1 and 48 kHz stereo WAVs: not the same samples, so not the same
 * bytes, but the same peaks, which is what Shazam matches on. In AP5 every
 * peak had a twin within one step and one bin; 96-98% were exactly equal.
 */
class DownsamplerTest {
    private fun resource(name: String) = javaClass.getResourceAsStream("/desktop/$name")!!.use { it.readBytes() }

    private class Wav(val rate: Int, val channels: Int, val pcm: ShortArray)

    private fun wav(name: String): Wav {
        val b = ByteBuffer.wrap(resource(name)).order(ByteOrder.LITTLE_ENDIAN)
        b.position(12)
        var rate = 0
        var channels = 0
        while (b.remaining() >= 8) {
            val id = ByteArray(4).also { b.get(it) }.decodeToString()
            val size = b.int
            if (id == "fmt ") {
                val at = b.position()
                b.short
                channels = b.short.toInt()
                rate = b.int
                b.position(at + size)
            } else if (id == "data") {
                val pcm = ShortArray(size / 2) { b.short }
                return Wav(rate, channels, pcm)
            } else {
                b.position(b.position() + size)
            }
        }
        error("no data in $name")
    }

    private data class P(val band: Int, val pass: Int, val bin: Int)

    private fun peaks(uri: String): List<P> {
        val b = ByteBuffer.wrap(Base64.getDecoder().decode(uri.substringAfter(','))).order(ByteOrder.LITTLE_ENDIAN)
        val out = ArrayList<P>()
        var off = 56
        while (off < b.limit()) {
            val band = b.getInt(off) - 0x60030040
            val len = b.getInt(off + 4)
            var i = off + 8
            var pass = 0
            while (i < off + 8 + len) {
                var d = b.get(i++).toInt() and 0xff
                if (d == 0xff) { pass = b.getInt(i); i += 4; d = b.get(i++).toInt() and 0xff }
                pass += d
                val bin = b.getShort(i + 2).toInt() and 0xffff
                i += 4
                out += P(band, pass, bin)
            }
            off += 8 + len + (4 - len % 4) % 4
        }
        return out
    }

    private fun shared(name: String): Double {
        val w = wav("$name.wav")
        val mine = Signature.make(Downsampler(w.rate).process(Downsampler.mixDown(w.pcm, w.channels)))
        val theirs = peaks(resource("$name.uri").decodeToString().trim())
        val ours = peaks(mine.uri)
        val found = theirs.count { t -> ours.any { it.band == t.band && kotlin.math.abs(it.pass - t.pass) <= 1 && kotlin.math.abs(it.bin - t.bin) <= 64 } }
        val share = found.toDouble() / theirs.size
        println("$name: ${theirs.size} desktop peaks, ${ours.size} phone peaks, ${"%.1f".format(share * 100)}% shared")
        return share
    }

    @Test fun at44kHz() = assertTrue(shared("llama44") >= 0.85)
    @Test fun at48kHz() = assertTrue(shared("search48") >= 0.85)
}
