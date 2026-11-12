package com.frequencies.core

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Pebble watchapp's messages (build 190, task 234). */
class PebbleTest {
    @Test fun rowsRoundTripThroughTheWatchDecoder() {
        val rows = listOf(
            Pebble.Row(0, 0xFFFFFFF0L, "Nightwave Plaza", "", Pebble.RowFlag.PLAYING, 0xC3, 0, listOf(0, 1)),
            Pebble.Row(1, 7, "Dublab", "", Pebble.RowFlag.DOWN, 0, 1_762_700_000L),
            Pebble.Row(2, 8, "Café Ünïcode ☕", "FR · jazz · 128 kbps"),
            Pebble.Row(3, 9, "Today", flags = Pebble.RowFlag.HEADING),
        )
        assertEquals(rows, Pebble.unpackRows(Pebble.packRows(rows)))
    }

    @Test fun longTextIsCutOnACharacterBoundary() {
        val name = "é".repeat(40)  // 80 bytes
        val r = Pebble.unpackRows(Pebble.packRow(Pebble.Row(0, 1, name, "☕".repeat(30)))).single()
        assertEquals("é".repeat(21), r.name)          // 42 bytes, under the watch's 43
        assertEquals("☕".repeat(17), r.sub)           // 51 bytes
        assertEquals("ab", Pebble.fit("ab☕", 4))
        assertEquals(2, Pebble.utf8("ab☕", 4).size)
    }

    @Test fun moreThanFourTagsAreDropped() {
        val r = Pebble.unpackRows(Pebble.packRow(Pebble.Row(0, 1, "x", tags = listOf(1, 2, 3, 4, 5, 6)))).single()
        assertEquals(listOf(1, 2, 3, 4), r.tags)
    }

    @Test fun listsSplitIntoMessagesUnderTheBudget() {
        val rows = (0 until 40).map { Pebble.Row(it, it.toLong(), "Station number $it with a long name", "US · synthwave · 128 kbps") }
        val msgs = Pebble.rowMessages(rows)
        assertTrue(msgs.size > 1)
        assertTrue(msgs.all { it.size <= Pebble.ROWS_BUDGET })
        assertEquals(rows, msgs.flatMap { Pebble.unpackRows(it) })
    }

    @Test fun tagsPackAsTheWatchReadsThem() {
        val b = Pebble.packTags(listOf(Pebble.Tag("Synthwave", 0xF3, 1, 4), Pebble.Tag("Chill", 0xCF, 2, 300)))
        assertEquals(2, b[0].toInt())
        assertEquals(0xF3, b[1].toInt() and 0xFF)
        assertEquals(1, b[2].toInt())
        assertEquals(4, (b[3].toInt() and 0xFF) or ((b[4].toInt() and 0xFF) shl 8))
        assertEquals(9, b[5].toInt())
        assertEquals("Synthwave", String(b, 6, 9))
        val at = 6 + 9
        assertEquals(300, (b[at + 2].toInt() and 0xFF) or ((b[at + 3].toInt() and 0xFF) shl 8))
        assertEquals(24, Pebble.packTags((0 until 30).map { Pebble.Tag("t$it", 0, 0, 0) })[0].toInt())
    }

    @Test fun tagFilterStatesGoBothWays() {
        var q = ""
        q = Pebble.setTag(q, "80s", 1)
        q = Pebble.setTag(q, "Talk", 2)
        assertEquals(1, Pebble.tagState(q, "80s"))
        assertEquals(2, Pebble.tagState(q, "talk"))
        assertEquals(0, Pebble.tagState(q, "jazz"))
        q = Pebble.setTag(q, "80s", 0)
        assertEquals(0, Pebble.tagState(q, "80s"))
    }

    @Test fun commandsParseFromTheWatchDictionary() {
        val c = Pebble.parse(mapOf(Pebble.Key.CMD to 26.toUByte(), Pebble.Key.ARG to 0, Pebble.Key.ARG2 to 0, Pebble.Key.TEXT to "lofi\u0000"))!!
        assertEquals(Pebble.Cmd.SEARCH, c.cmd)
        assertEquals("lofi", c.text)
        val h = Pebble.parse(mapOf(Pebble.Key.CMD to 1, Pebble.Key.ARG to 1, Pebble.Key.WATCH to 29, Pebble.Key.PROTOCOL to 1))!!
        assertEquals(Pebble.Caps.COLOR or Pebble.Caps.LARGE or Pebble.Caps.TOUCH or Pebble.Caps.MIC, h.caps)
        assertNull(Pebble.parse(mapOf(Pebble.Key.ARG to 1)))
    }

    @Test fun colorsSnapToPebbles64() {
        assertEquals(0xC0, Pebble.gcolor8(0x000000))
        assertEquals(0xFF, Pebble.gcolor8(0xFFFFFF))
        assertEquals(0xF8, Pebble.gcolor8(0xFFAA00))   // ChromeYellow
        assertEquals(0xF8, Pebble.gcolor8(0xF0A010))
        assertEquals(0xFFAA00, Pebble.rgbOf(0xF8))
    }

    @Test fun themeKeepsTheStripeApartFromTheBackground() {
        val og = ShellThemes.find("midnight")
        val b = Pebble.themeBytes(og.tokens)
        assertEquals(9, b.size)
        assertTrue("stripe must differ from the background", b[0] != b[7])
        assertEquals(Pebble.gcolor8(og.accent), b[4].toInt() and 0xFF)
        ShellThemes.all.forEach { t -> assertTrue(t.key, Pebble.themeBytes(t.tokens, light = t.light).let { it[0] != it[7] }) }
    }

    @Test fun picturesDitherToColor8() {
        val w = 8; val h = 8
        val px = IntArray(w * h) { 0xFF000000.toInt() or 0x7F7F7F }  // mid grey: between two levels, dithered
        val b = Pebble.color8(px, w, h)
        assertEquals(64, b.size)
        assertTrue(b.all { (it.toInt() and 0xC0) == 0xC0 })
        assertTrue("mid grey dithers between two greys", b.toSet().size == 2)
        val flat = Pebble.color8(IntArray(4) { 0xFFFFAA00.toInt() }, 2, 2)
        assertTrue(flat.all { (it.toInt() and 0xFF) == 0xF8 })
    }

    @Test fun stationSettingsRoundTrip() {
        val st = Station(LinkedHashMap(Defaults.station)).also { it["url"] = kotlinx.serialization.json.JsonPrimitive("http://example.com/stream") }
        val S = Pebble.StationSet
        S.apply(st, S.GAIN, 3)            // +1.5 dB
        S.apply(st, S.AD_ON, 1)
        S.apply(st, S.AD_SECONDS, 22)
        S.apply(st, S.BUFFER_ON, 1)
        S.apply(st, S.BUFFER_SECONDS, 9)  // 4.5 s
        S.apply(st, S.TALK, 1)
        S.apply(st, S.EQ3 + 2, -3)
        S.apply(st, S.EQ10 + 9, 5)
        S.apply(st, S.AD_COVER, 1, "abc")
        val b = S.bytes(st, saved = true, advanced = false)
        assertEquals(S.SIZE, b.size)
        val f = b[0].toInt()
        assertTrue(f and S.SAVED != 0 && f and S.AD_ON_FLAG != 0 && f and S.AD_CHOSEN != 0 && f and S.BUFFER_ON_FLAG != 0 && f and S.TALK_FLAG != 0)
        assertEquals(43, b[1].toInt())
        assertEquals(22, b[2].toInt())
        assertEquals(9, b[3].toInt())
        assertEquals(9, b[7].toInt())
        assertEquals(17, b[17].toInt())
        S.apply(st, S.AD_ANY, 0)
        S.apply(st, S.EQ_RESET, 0)
        val c = S.bytes(st, saved = true, advanced = true)
        assertEquals(0, c[0].toInt() and S.AD_CHOSEN)
        assertTrue(c[0].toInt() and S.EQ_ADVANCED != 0)
        assertTrue((5 until 18).all { c[it].toInt() == 12 })
        S.apply(st, S.GAIN, 99)           // out of range: clamped to +20 dB
        assertEquals(80, S.bytes(st, true, false)[1].toInt())
    }

    @Test fun namesSettingReadsSafely() {
        assertEquals(0, Pebble.Names.mode("both"))
        assertEquals(1, Pebble.Names.mode("artist"))
        assertEquals(2, Pebble.Names.mode("station"))
        assertEquals(0, Pebble.Names.mode("nonsense"))
        assertEquals(3, Pebble.Names.clampSeconds(0))
        assertEquals(60, Pebble.Names.clampSeconds(500))
        assertEquals(7, Pebble.Names.clampSeconds(Pebble.Names.SECONDS_DEFAULT))
    }

    @Test fun ditheringKeepsTheBrightness() {
        // a left-to-right grey ramp: every column's average stays close to the real grey (error diffusion, build 191)
        val w = 64; val h = 32
        val px = IntArray(w * h) { i -> val g = (i % w) * 255 / (w - 1); 0xFF000000.toInt() or (g shl 16) or (g shl 8) or g }
        val b = Pebble.color8(px, w, h)
        for (x in 0 until w) {
            val want = x * 255.0 / (w - 1)
            val got = (0 until h).map { y -> (b[y * w + x].toInt() and 3) * 85 }.average()
            assertTrue("column $x: $got vs $want", kotlin.math.abs(got - want) < 22)
        }
        // pure colors stay pure: no stray specks in a flat red
        assertTrue(Pebble.color8(IntArray(100) { 0xFFFF0000.toInt() }, 10, 10).all { (it.toInt() and 0xFF) == 0xF0 })
    }

    @Test fun logosAreOneBitTopBitFirst() {
        // 10×2, transparent except x=0 and x=9 on row 0
        val w = 10; val h = 2
        val px = IntArray(w * h)
        px[0] = 0xFFFFFFFF.toInt(); px[9] = 0xFF000000.toInt()
        val b = Pebble.mono(px, w, h, logo = true)
        assertEquals(4, b.size)  // 2 bytes a row
        assertArrayEquals(byteArrayOf(0x80.toByte(), 0x40, 0, 0), b)
        // an opaque logo: the shape is what differs from the corner
        val solid = IntArray(16) { 0xFFFFFFFF.toInt() }.also { it[5] = 0xFF000000.toInt() }
        val m = Pebble.mono(solid, 4, 4, logo = true)
        assertArrayEquals(byteArrayOf(0, 0x40, 0, 0), m)
    }

    @Test fun picturesFitTheWatchLimit() {
        assertEquals(120 to 120, Pebble.fitSize(600, 600, 120, 120))
        val (w, h) = Pebble.fitSize(1000, 500, 300, 300)
        assertTrue(w * h <= Pebble.ART_MAX && w <= 300)
        val c = Pebble.chunks(ByteArray(4500))
        assertEquals(listOf(0, 2000, 4000), c.map { it.first })
        assertEquals(500, c.last().second.size)
    }

    @Test fun smallThings() {
        assertEquals(0x83, Pebble.sortByte("played", true))
        assertEquals(1, Pebble.sortByte("alphabetical", false))
        assertEquals(0, Pebble.secondsLeft(null))
        assertEquals(2, Pebble.secondsLeft(1001))
        assertEquals(Pebble.id32("abc"), Pebble.id32("abc"))
        assertTrue(Pebble.id32("abc") != Pebble.id32("abd"))
        assertTrue(Pebble.id32("x") in 1..0xFFFFFFFFL)
    }
}
