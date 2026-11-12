package com.frequencies.core

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.sin
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Batch 31 (build 207, task 250): the desktop's loudness leveling
 * (mpv's dynaudnorm=f=200:g=15:p=0.95) on Android. The fixture is
 * ffmpeg's own dynaudnorm (tools/make_leveler_fixtures.py).
 */
class LevelerTest {
    /** Everything [lv] gives back for [input], fed in [chunk]-sample pieces, then the end. */
    private fun run(lv: Leveler, input: FloatArray, chunk: Int = 1024, end: Boolean = true): FloatArray {
        val out = ArrayList<Float>(input.size)
        var at = 0
        val buf = FloatArray(1 shl 20)
        fun collect() { val n = lv.read(buf, minOf(buf.size, lv.available())); for (i in 0 until n) out += buf[i] }
        while (at < input.size) {
            val n = minOf(chunk, input.size - at)
            lv.push(input.copyOfRange(at, at + n), n)
            at += n
            collect()
        }
        if (end) { lv.drain(); collect() }
        return out.toFloatArray()
    }

    private fun tone(rate: Int, seconds: Double, amp: Double, hz: Double = 440.0, channels: Int = 2): FloatArray {
        val n = (rate * seconds).toInt()
        return FloatArray(n * channels) { i -> (amp * sin(2 * PI * hz * (i / channels) / rate)).toFloat() }
    }

    private fun peak(a: FloatArray, from: Int = 0, to: Int = a.size): Double {
        var p = 0.0
        for (i in from until to) p = maxOf(p, abs(a[i]).toDouble())
        return p
    }

    private fun db(x: Double) = 20 * log10(x)

    /** The signal tools/make_leveler_fixtures.py gives ffmpeg. */
    private fun signal(): FloatArray {
        val rate = 8000
        val out = FloatArray(10 * rate * 2)
        for (n in 0 until 10 * rate) {
            val t = n.toDouble() / rate
            val left = when {
                t < 3 -> 0.1 * sin(2 * PI * 440 * t)
                t < 6 -> 0.8 * sin(2 * PI * 300 * t)
                t < 7 -> 0.0
                else -> 0.05 * sin(2 * PI * 1000 * t)
            }
            out[2 * n] = left.toFloat()
            out[2 * n + 1] = (0.5 * left).toFloat()
        }
        return out
    }

    @Test fun matchesTheDesktopsDynaudnorm() {
        val text = javaClass.getResourceAsStream("/leveler/ffmpeg-peaks.json")!!.bufferedReader().readText()
        val want = Json.parseToJsonElement(text).jsonObject["peaks"]!!.jsonArray.map { it.jsonPrimitive.double }
        val out = run(Leveler(8000, 2), signal(), chunk = 777)
        assertEquals(signal().size, out.size)
        val step = 8000 / 20 * 2
        assertEquals(want.size, out.size / step)
        for (k in want.indices) {
            val got = peak(out, k * step, (k + 1) * step)
            assertEquals("50 ms window $k (${k * 0.05} s)", want[k], got, 2e-4)
        }
    }

    @Test fun loudAndQuietStationsSettleToTheSameLevel() {
        val quiet = run(Leveler(48000, 2), tone(48000, 10.0, 0.25))
        val loud = run(Leveler(48000, 2), tone(48000, 10.0, 0.9, 300.0))
        // The last 3 s: 11 dB apart going in, within 1 dB coming out, both just under the 0.95 target.
        val from = 7 * 48000 * 2
        val q = peak(quiet, from, quiet.size - 48000 * 2)
        val l = peak(loud, from, loud.size - 48000 * 2)
        assertTrue("quiet $q loud $l", abs(db(q) - db(l)) < 1.0)
        assertTrue("loud $l", l in 0.94..0.951)
        assertTrue("quiet $q", q in 0.88..0.951)
    }

    @Test fun limits() {
        // At most 10x (+20 dB), softly: a whisper at 0.001 comes up to under 0.01, never past it.
        val whisper = run(Leveler(48000, 2), tone(48000, 8.0, 0.001))
        val w = peak(whisper, 5 * 48000 * 2, 7 * 48000 * 2)
        assertTrue("whisper $w", w in 0.0075..0.0100001)
        // A loud part right after a quiet one: the gain comes down before it, so nothing passes the target.
        val step = tone(48000, 5.0, 0.1) + tone(48000, 5.0, 0.9, 300.0)
        val out = run(Leveler(48000, 2), step)
        assertTrue("loudest ${peak(out)}", peak(out) <= 0.9501)
        // Silence stays silence.
        val silence = run(Leveler(48000, 2), FloatArray(48000 * 2 * 4))
        assertEquals(0.0, peak(silence), 0.0)
    }

    @Test fun theGainGlides() {
        val rate = 8000
        val input = tone(rate, 6.0, 0.1) + tone(rate, 6.0, 0.8, 300.0)
        val out = run(Leveler(rate, 2), input)
        val frame = rate / 5 * 2
        val gains = (0 until input.size / frame).map { k -> peak(out, k * frame, (k + 1) * frame) / peak(input, k * frame, (k + 1) * frame) }
        // No jumps: frame to frame the gain moves by at most 3 dB, even at the step.
        for (k in 1 until gains.size) assertTrue("frame $k: ${db(gains[k - 1])} -> ${db(gains[k])} dB", abs(db(gains[k]) - db(gains[k - 1])) < 3.0)
        // It looks ahead: the gain is already coming down a second before the loud part (frame 30).
        assertTrue("1 s before ${gains[25]}, 3 s before ${gains[15]}", gains[25] < gains[15] * 0.8)
    }

    @Test fun switchedOffChangesNothing() {
        val input = FloatArray(48000 * 2 * 5) { i -> ((i * 7919 % 2000) - 1000) / 1500f }
        val lv = Leveler(48000, 2).apply { on = false }
        val out = run(lv, input)
        assertArrayEquals(input, out, 0f)
    }

    @Test fun switchingOffMidwayGlidesBackToUnchanged() {
        val input = tone(8000, 12.0, 0.2)
        val lv = Leveler(8000, 2)
        val half = input.size / 2
        val first = run(lv, input.copyOfRange(0, half), end = false)
        lv.on = false
        val second = run(lv, input.copyOfRange(half, input.size))
        val out = first + second
        assertEquals(input.size, out.size)
        // Leveled before (0.2 comes up), unchanged by the end, and no step in between.
        assertTrue(peak(out, 3 * 16000, 4 * 16000) > 0.8)
        val tail = out.size - 2 * 16000
        for (i in tail until out.size) assertEquals(input[i], out[i], 0f)
        val frame = 1600 * 2
        for (k in 1 until out.size / frame) {
            val a = peak(out, (k - 1) * frame, k * frame); val b = peak(out, k * frame, (k + 1) * frame)
            assertTrue("frame $k: $a -> $b", abs(db(b) - db(a)) < 3.0)
        }
    }

    @Test fun holdsTwoPointEightSecondsBack() {
        val lv = Leveler(48000, 2)
        assertEquals(9600, lv.frameLen)
        val frame = lv.frameLen * 2
        // 14 frames in: nothing out yet; the 15th lets the first one go.
        lv.push(tone(48000, 2.8, 0.3), 14 * frame)
        assertEquals(0, lv.available())
        assertEquals(2800L, lv.heldMs())
        lv.push(tone(48000, 0.2, 0.3), frame)
        assertEquals(frame, lv.available())
    }

    @Test fun songsPassStraightThroughAndNothingIsLost() {
        val ramp = FloatArray(8000 * 2 * 6) { i -> (i % 30000) / 40000f }
        // A song: out at once, untouched.
        val song = Leveler(8000, 2).apply { lookAhead = false }
        song.push(ramp, 1000)
        assertEquals(1000, song.available())
        // A station that turns into a song midway: what was held comes out first, in order, then the rest.
        val lv = Leveler(8000, 2).apply { on = false }
        val first = run(lv, ramp.copyOfRange(0, 50000), end = false)
        lv.lookAhead = false
        val second = run(lv, ramp.copyOfRange(50000, ramp.size), end = false)
        assertArrayEquals(ramp, first + second, 0f)
        // The end of the sound: everything held comes out.
        val ended = run(Leveler(8000, 2), ramp)
        assertEquals(ramp.size, ended.size)
    }

    @Test fun aNewStationDropsWhatWasHeld() {
        val lv = Leveler(8000, 2)
        lv.push(tone(8000, 2.0, 0.3), 32000)
        lv.flush()
        assertEquals(0L, lv.heldMs())
        assertEquals(0, lv.available())
        // And it starts over: 2.8 s again before anything comes out.
        lv.push(tone(8000, 2.0, 0.3), 32000)
        assertEquals(0, lv.available())
    }

    @Test fun matchesDynaudnormsHelpers() {
        // bound(m, x) = erf(sqrt(pi)/2 * x/m) * m.
        assertEquals(0.99739, Leveler.bound(10.0, 1.0), 1e-5)
        assertEquals(7.8991, Leveler.bound(10.0, 10.0), 1e-3)
        val w = Leveler.weights(15)
        assertEquals(1.0, w.sum(), 1e-12)
        assertEquals(w[0], w[14], 1e-15)
        assertTrue(w[7] > w[6] && w[6] > w[0])
    }
}
