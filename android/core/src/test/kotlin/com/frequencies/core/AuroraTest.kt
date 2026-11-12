package com.frequencies.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Comfort spec 8 (the moving background) and 6 (the band logo on the artist photo). */
class AuroraTest {
    /** The theme's own accent, six artwork colors, a Material You sample, custom white and yellow. */
    private fun accents(theme: ShellTheme) = listOf(
        theme.accent, 0xD94A3A, 0x2E86DE, 0x27AE60, 0x8E44AD, 0xF39C12, 0x1ABC9C, 0x6750A4, 0xFFFFFF, 0xFFFF00,
    )

    @Test
    fun everyThemeAndAccentReadsOverTheBrightestPool() {
        var looks = 0
        for (theme in ShellThemes.all) for (accent in accents(theme)) {
            val scheme = ThemeScheme.of(theme, accent)
            val wash = ComfortWash.of(theme, scheme)
            val look = Aurora.look(scheme, wash)
            val spots = Aurora.spots(look.base, look.pools, look.screen, look.poolAlpha, look.veil)
            // Body text at 4.5:1; the accent colors only the sung line (large, semibold): 3:1.
            for ((name, fg, ratio) in listOf(Triple("text", wash.text, ThemeScheme.TEXT), Triple("text2", wash.text2, ThemeScheme.TEXT), Triple("accent", wash.accent, ThemeScheme.GRAPHIC))) for (bg in spots) {
                val c = ThemeScheme.contrast(fg, bg)
                assertTrue("${theme.name} / #%06x: %s #%06x on #%06x is %.2f:1 (veil %.2f)".format(accent, name, fg, bg, c, look.veil), c >= ratio)
            }
            assertTrue(look.veil in 0.0..1.0)
            looks++
        }
        assertEquals(22 * 10, looks)
    }

    @Test
    fun theVeilStartsAtTheMockUpsNumbersAndOnlyRises() {
        val og = ShellThemes.find("midnight")
        val dark = Aurora.look(ThemeScheme.of(og, og.accent), ComfortWash.of(og, ThemeScheme.of(og, og.accent)))
        assertTrue(dark.screen)
        assertTrue(dark.veil >= Aurora.DARK_VEIL)
        val flash = ShellThemes.all.first { it.light }
        val s = ThemeScheme.of(flash, flash.accent)
        val light = Aurora.look(s, ComfortWash.of(flash, s))
        assertTrue(!light.screen)
        assertTrue(light.veil >= Aurora.LIGHT_VEIL)
        assertEquals(Aurora.LIGHT_ALPHA * 0.9, light.poolAlpha[0], 1e-9)
    }

    @Test
    fun poolColorsFollowTheAccentsHue() {
        val og = ShellThemes.find("midnight")
        val s = ThemeScheme.of(og, 0x2E86DE)
        val look = Aurora.look(s, ComfortWash.of(og, s))
        val hue = ArtAccent.hsl(s.primary).h
        val shifts = Aurora.POOLS.map { it.shift }
        look.pools.forEachIndexed { i, c ->
            val h = ArtAccent.hsl(c)
            val want = Math.floorMod(hue + shifts[i], 360)
            val diff = minOf(Math.floorMod(h.h - want, 360), Math.floorMod(want - h.h, 360))
            assertTrue("pool $i hue ${h.h} want $want", diff <= 2)
            assertTrue(h.s in Aurora.SAT_MIN - 3..Aurora.SAT_MAX + 3)
        }
        // A gray accent gets the desktop's orange (hue 30).
        val gray = ThemeScheme.of(og, 0x808080)
        assertTrue(Aurora.look(gray, ComfortWash.of(og, gray)).pools.isNotEmpty())
    }

    @Test
    fun poolPathsAreTheDesktops() {
        // nowplaying.py at t = 0 and t = 14, on a 100 x 200 canvas (worked out by hand from the formula).
        val (x0, y0, r0) = Aurora.position(0, 0.0, 100.0, 200.0)
        assertEquals(50.0, x0, 1e-9)
        assertEquals(200 * (0.5 + 0.38), y0, 1e-9)
        assertEquals(62.0, r0, 1e-9)
        val (x1, y1, _) = Aurora.position(1, 14.0, 100.0, 200.0)
        assertEquals(100 * (0.5 + 0.38 * Math.sin(14 * 0.07 * 2 * Math.PI + 1.7)), x1, 1e-9)
        assertEquals(200 * (0.5 + 0.38 * Math.cos(14 * 0.13 * 2 * Math.PI + 1.7 * 1.3)), y1, 1e-9)
        // The same time gives the same frame, every time.
        assertEquals(Aurora.position(3, 7.25, 360.0, 780.0), Aurora.position(3, 7.25, 360.0, 780.0))
        // Every pool stays within 12% of the edges.
        for (i in 0..3) for (k in 0..400) {
            val (x, y, _) = Aurora.position(i, k * 0.37, 360.0, 780.0)
            assertTrue(x in 360 * 0.12..360 * 0.88 && y in 780 * 0.12..780 * 0.88)
        }
    }

    @Test
    fun whenItMovesAndWhenItStandsStill() {
        fun run(setting: String = Aurora.AUTO, calm: Boolean = false, saver: Boolean = false, battery: Int? = 80, charging: Boolean = false, front: Boolean = true, idle: Long = 0) =
            Aurora.run(setting, calm, saver, battery, charging, front, idle)
        assertEquals(Aurora.Run.FULL, run())
        assertEquals(Aurora.Run.OFF, run(setting = Aurora.OFF))
        assertEquals(Aurora.Run.STILL, run(calm = true))
        assertEquals(Aurora.Run.STILL, run(setting = Aurora.ALWAYS, calm = true))
        assertEquals(Aurora.Run.HOLD, run(front = false))
        assertEquals(Aurora.Run.STILL, run(calm = true, front = false))
        assertEquals(Aurora.Run.STILL, run(saver = true))
        assertEquals(Aurora.Run.FULL, run(setting = Aurora.ALWAYS, saver = true))
        assertEquals(Aurora.Run.STILL, run(battery = 19))
        assertEquals(Aurora.Run.FULL, run(battery = 19, charging = true))
        assertEquals(Aurora.Run.FULL, run(battery = 20))
        assertEquals(Aurora.Run.FULL, run(setting = Aurora.ALWAYS, battery = 5))
        assertEquals(Aurora.Run.FULL, run(battery = null))
        assertEquals(Aurora.Run.FULL, run(idle = Aurora.SLOW_AFTER_MS - 1))
        assertEquals(Aurora.Run.SLOW, run(idle = Aurora.SLOW_AFTER_MS))
        assertEquals(30, Aurora.fps(Aurora.Run.FULL))
        assertEquals(15, Aurora.fps(Aurora.Run.SLOW))
        assertEquals(0, Aurora.fps(Aurora.Run.STILL))
        // Play and pause ease over 450 ms, never past 0 or 1.
        assertEquals(0.5, Aurora.ease(1.0, false, 225.0), 1e-9)
        assertEquals(0.0, Aurora.ease(0.1, false, 450.0), 1e-9)
        assertEquals(1.0, Aurora.ease(0.9, true, 100.0), 1e-9)
        assertEquals(Aurora.AUTO, Aurora.setting(null))
        val s = Settings(linkedMapOf<String, kotlinx.serialization.json.JsonElement>(Aurora.SETTING to kotlinx.serialization.json.JsonPrimitive("nonsense")))
        assertEquals(Aurora.AUTO, Aurora.setting(s))
    }

    @Test
    fun logoPlacementMatchesTheDesktop() {
        val text = javaClass.getResource("/logo/placement.json")!!.readText()
        val root = Json.parseToJsonElement(text).jsonObject
        fun box(b: BandLogos.Box?, want: List<Double>, what: String) {
            requireNotNull(b) { what }
            assertEquals("$what left", want[0], b.left, 1e-6)
            assertEquals("$what top", want[1], b.top, 1e-6)
            assertEquals("$what width", want[2], b.width, 1e-6)
            assertEquals("$what height", want[3], b.height, 1e-6)
        }
        val photo = root["photo"]!!.jsonArray
        for (e in photo) {
            val o = e.jsonObject
            fun d(k: String) = o[k]!!.jsonPrimitive.double
            box(
                BandLogos.onPhoto(d("w"), d("h"), d("iw"), d("ih"), d("scale"), d("x"), d("y")),
                o["box"]!!.jsonArray.map { it.jsonPrimitive.double }, o.toString(),
            )
        }
        val column = root["column"]!!.jsonArray
        for (e in column) {
            val o = e.jsonObject
            fun d(k: String) = o[k]!!.jsonPrimitive.double
            box(BandLogos.inColumn(d("width"), d("iw"), d("ih"), d("scale"), d("x")), o["box"]!!.jsonArray.map { it.jsonPrimitive.double }, o.toString())
        }
        assertEquals(80, photo.size)
        assertEquals(64, column.size)
        // The header box itself: 290 square photo, box 96 tall ending 8 above the bottom.
        assertEquals(BandLogos.Box(12.0, 290.0 - 8 - 96, 266.0, 96.0), BandLogos.headerBox(290.0, 290.0))
        assertEquals(BandLogos.Box(12.0, 52.0 - 8 - 26, 28.0, 26.0), BandLogos.headerBox(52.0, 52.0))
    }
}
