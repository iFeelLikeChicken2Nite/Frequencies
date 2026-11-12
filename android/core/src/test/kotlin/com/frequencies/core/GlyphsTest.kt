package com.frequencies.core

import java.awt.AlphaComposite
import java.awt.BasicStroke
import java.awt.Color
import java.awt.RenderingHints
import java.awt.geom.AffineTransform
import java.awt.geom.Arc2D
import java.awt.geom.Ellipse2D
import java.awt.geom.Path2D
import java.awt.geom.RoundRectangle2D
import java.awt.image.BufferedImage
import java.io.File
import javax.imageio.ImageIO
import kotlin.math.abs
import kotlin.math.max
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone's glyphs against the desktop's own pictures of them
 * (tools/make_glyph_fixtures.py drew every glyph and every animation frame
 * with shell.py), and the animations' frame counts and timings.
 */
class GlyphsTest {
    private val size = 88

    /** A fixture name as the phone draws it: (glyph, progress). */
    private fun phoneName(fixture: String): Pair<String, Float> {
        fun tail(prefix: String) = fixture.removePrefix(prefix).trimStart('_')
        return when {
            fixture.startsWith("next_shuffle") -> "next_shuffle" to tail("next_shuffle").toInt() / 4f
            fixture.startsWith("shuffle_") -> "shuffle" to tail("shuffle").toInt() / 4f
            fixture.startsWith("playpause") -> "playpause" to tail("playpause").toInt() / 4f
            fixture.startsWith("star_shine") -> "star_shine" to tail("star_shine").toInt() / 4f
            fixture.startsWith("shazam_fill") -> "shazam_fill" to tail("shazam_fill").toInt() / 4f
            fixture == "volume3" -> "volume" to 1f
            fixture.startsWith("eq") -> "eq" to (tail("eq").toIntOrNull() ?: 0) / 100f
            fixture.startsWith("timer") -> "timer" to (tail("timer").toIntOrNull() ?: 0) / 100f
            fixture.startsWith("fullscreen") -> "fullscreen" to (tail("fullscreen").toIntOrNull() ?: 0) / 2f
            else -> fixture to 0f
        }
    }

    private fun render(parts: List<Glyphs.Part>): BufferedImage {
        val img = BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB)
        val g = img.createGraphics()
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
        g.setRenderingHint(RenderingHints.KEY_STROKE_CONTROL, RenderingHints.VALUE_STROKE_PURE)
        val s = size / 24.0
        for (p in parts) {
            val shape: java.awt.Shape = when (val sh = p.shape) {
                is Glyphs.Poly -> Path2D.Double().apply {
                    moveTo(sh.points[0] * s, sh.points[1] * s)
                    for (i in 2 until sh.points.size step 2) lineTo(sh.points[i] * s, sh.points[i + 1] * s)
                    if (sh.closed) closePath()
                }
                is Glyphs.Oval -> Ellipse2D.Double(sh.x * s, sh.y * s, sh.w * s, sh.h * s)
                is Glyphs.Arc -> Arc2D.Double(sh.x * s, sh.y * s, sh.w * s, sh.h * s, sh.start.toDouble(), sh.sweep.toDouble(), Arc2D.OPEN)
                is Glyphs.RRect -> AffineTransform.getRotateInstance(Math.toRadians(sh.turn.toDouble()), 12 * s, 12 * s)
                    .createTransformedShape(RoundRectangle2D.Double(sh.x * s, sh.y * s, sh.w * s, sh.h * s, 2 * sh.r * s, 2 * sh.r * s))
            }
            val saved = g.clip
            p.clip?.let { (cx, cy, r) -> g.clip(Ellipse2D.Double((cx - r) * s, (cy - r) * s, 2 * r * s, 2 * r * s)) }
            g.composite = AlphaComposite.getInstance(AlphaComposite.SRC_OVER, p.alpha)
            g.color = when (p.ink) { Glyphs.Ink.MAIN -> Color.WHITE; Glyphs.Ink.BASE -> Color(0x80, 0x80, 0x80); Glyphs.Ink.GAP -> Color.BLACK }
            if (p.fill) g.fill(shape)
            if (p.stroke > 0f) {
                g.stroke = BasicStroke((p.stroke * s).toFloat(), if (p.squareCaps) BasicStroke.CAP_SQUARE else BasicStroke.CAP_ROUND, BasicStroke.JOIN_ROUND)
                g.draw(shape)
            }
            g.clip = saved
        }
        g.dispose()
        return img
    }

    /** Light seen through the glyph's own transparency, 0..1 per pixel. */
    private fun ink(img: BufferedImage): DoubleArray = DoubleArray(size * size) { i ->
        val argb = img.getRGB(i % size, i / size)
        val a = (argb ushr 24) / 255.0
        val lum = (((argb shr 16) and 0xFF) + ((argb shr 8) and 0xFF) + (argb and 0xFF)) / (3 * 255.0)
        a * lum
    }

    /** How far apart two drawings are: the ink that differs over the ink either has (0 = the same). */
    private fun difference(a: DoubleArray, b: DoubleArray): Double {
        var diff = 0.0; var total = 0.0
        for (i in a.indices) { diff += abs(a[i] - b[i]); total += max(a[i], b[i]) }
        return if (total == 0.0) 0.0 else diff / total
    }

    private val dir = File(javaClass.getResource("/glyphs/index.json")!!.toURI()).parentFile
    private val fixtures: List<String> = Regex("\"([a-z_0-9]+)\"").findAll(File(dir, "index.json").readText())
        .map { it.groupValues[1] }.filter { it !in setOf("size", "scale", "names") }.toList()

    @Test
    fun everyGlyphAndFrameMatchesTheDesktops() {
        val out = System.getenv("GLYPHS_OUT")?.let { File(it).apply { mkdirs() } }
        val worst = mutableListOf<Pair<String, Double>>()
        for (fixture in fixtures) {
            val (name, t) = phoneName(fixture)
            val parts = Glyphs.draw(name, t)
            assertTrue("$fixture: the phone has no $name", parts.isNotEmpty())
            val desktop = ImageIO.read(File(dir, "$fixture.png"))
            val phone = render(parts)
            if (out != null) ImageIO.write(phone, "png", File(out, "$fixture.png"))
            worst += fixture to difference(ink(desktop), ink(phone))
        }
        val report = worst.sortedByDescending { it.second }.joinToString("\n") { (n, d) -> "  %-16s %.3f".format(n, d) }
        println("glyph differences (0 = identical):\n$report")
        for ((n, d) in worst) assertTrue("$n differs from the desktop's by %.3f\n$report".format(d), d < LIMIT)
        // And the test can tell frames apart: each desktop frame is nearest the phone's same frame.
        for (fixture in fixtures) {
            val (name, t) = phoneName(fixture)
            val desktop = ink(ImageIO.read(File(dir, "$fixture.png")))
            val siblings = fixtures.filter { phoneName(it).first == name && it != fixture }
            val mine = difference(desktop, ink(render(Glyphs.draw(name, t))))
            for (other in siblings) {
                val (_, u) = phoneName(other)
                val theirs = difference(desktop, ink(render(Glyphs.draw(name, u))))
                // (shazam_fill3 and 4 are the same picture on the desktop too: the circle is lit by then)
                assertTrue("$fixture looks more like the phone's $other (%.3f) than its own frame (%.3f)".format(theirs, mine), mine <= theirs)
            }
        }
        assertEquals(76, fixtures.size)
    }

    @Test
    fun animationsStartAndEndOnTheRestingGlyphs() {
        fun same(a: List<Glyphs.Part>, b: List<Glyphs.Part>, what: String, limit: Double = 0.02) =
            assertTrue(what, difference(ink(render(a)), ink(render(b))) < limit)
        // Play/pause: frame 0 is the triangle (the desktop's playpause0), the last the two bars.
        same(Glyphs.draw("playpause", 0f), listOf(Glyphs.draw("playpause", 0f).first()), "playpause 0 is one triangle")
        assertEquals(1, Glyphs.draw("playpause", 0f).size)
        assertEquals(2, Glyphs.draw("playpause", 1f).size)
        // Timer, equalizer and Comfort View come back to their resting picture.
        same(Glyphs.draw("timer", 0f), Glyphs.draw("timer"), "timer 0")
        same(Glyphs.draw("eq", 0f), Glyphs.draw("eq"), "eq 0")
        same(Glyphs.draw("fullscreen", 0f), Glyphs.draw("fullscreen"), "fullscreen 0")
        // A full turn of the long hand ends where it began; only the button stays pressed.
        val hands = { t: Float -> Glyphs.draw("timer", t).takeLast(2) }
        same(hands(0f).take(1), hands(1f).take(1), "the long hand's full turn")
        // Song ID's fill: nothing lit at 0, the whole mark at the end.
        assertEquals(1, Glyphs.draw("shazam_fill", 0f).size)
        same(Glyphs.draw("shazam_fill", 1f).drop(1).map { it.copy(clip = null) }, Glyphs.draw("shazam_on"), "shazam fill 4 = shazam_on")
        // Next with shuffle off is the plain Next (the desktop shows "next" at step 0).
        assertEquals(Glyphs.draw("next"), Glyphs.draw("next_shuffle", 0f))
        // The shine ends on the lit star.
        // (the desktop's shine star is a hair smaller than star_on, as there)
        same(listOf(Glyphs.draw("star_shine", 1f).first()), Glyphs.draw("star_on"), "star_shine 4 is the lit star under its rays", 0.05)
    }

    @Test
    fun framesAndTimingsAreTheDesktops() {
        assertEquals(4, GlyphMotion.PLAY_PAUSE.frames); assertEquals(26, GlyphMotion.PLAY_PAUSE.ms)
        assertEquals(4, GlyphMotion.STAR_SHINE.frames); assertEquals(55, GlyphMotion.STAR_SHINE.ms)
        assertEquals(14, GlyphMotion.SONG_ID.frames); assertEquals(40, GlyphMotion.SONG_ID.ms)
        assertEquals(10, GlyphMotion.TIMER.frames); assertEquals(24, GlyphMotion.TIMER.ms)
        assertEquals(4, GlyphMotion.EQ.frames); assertEquals(28, GlyphMotion.EQ.ms)
        assertEquals(2, GlyphMotion.FULLSCREEN.frames); assertEquals(28, GlyphMotion.FULLSCREEN.ms)
        assertEquals(4, GlyphMotion.SHUFFLE.frames); assertEquals(30, GlyphMotion.SHUFFLE.ms)
        assertEquals(4, GlyphMotion.NEXT_SHUFFLE.frames); assertEquals(30, GlyphMotion.NEXT_SHUFFLE.ms)
        assertEquals(560, GlyphMotion.RIPPLE_MS); assertEquals(180, GlyphMotion.STRIKE_MS); assertEquals(130, GlyphMotion.MENU_MS)
        // timer_0, timer_10 ... timer_100
        assertEquals((0..100 step 10).map { it / 100f }, (0..10).map { GlyphMotion.TIMER.progress(it) })
        assertEquals(0.5f, GlyphMotion.EQ.snap(0.6f))
        assertEquals(3, GlyphMotion.PLAY_PAUSE.frameAt(80))
        // Song ID: shazam_fill0..4 over the first 45% (steps 0..6), then the lit mark settling from 150% to 100%.
        assertEquals(listOf(0f, 0f, 0.25f, 0.25f, 0.5f, 0.75f, 0.75f), (0..6).map { GlyphMotion.songIdFill(it) })
        assertEquals(null, GlyphMotion.songIdFill(7))
        assertEquals(137, GlyphMotion.settleLighter(7))     // int(150 - 50 * eased) at t = 0.5
        assertEquals(100, GlyphMotion.settleLighter(14))
        // What PyQt5 5.15's QColor(...).lighter(...).name() gives.
        assertEquals(0x73a2ff, GlyphMotion.lighter(0x3366cc, 150))
        assertEquals(0x5e94ff, GlyphMotion.lighter(0x3366cc, 140))
        assertEquals(0xffe57f, GlyphMotion.lighter(0xffcc00, 150))
        assertEquals(0xffcf79, GlyphMotion.lighter(0xd99a2b, 150))
        assertEquals(0xa6a6a6, GlyphMotion.lighter(0x808080, 130))
        assertEquals(0x3366cc, GlyphMotion.lighter(0x3366cc, 100))
        assertEquals(1f, GlyphMotion.outCubic(1f)); assertEquals(0.875f, GlyphMotion.outCubic(0.5f))
    }

    @Test
    fun liveBarsAreTheDesktops() {
        // _draw_live: base + span * (0.5 + 0.5 * sin(t * speed + offset)).
        val zero = LiveBars.heights(0.0, moving = true)
        assertEquals(3.0f + 5.5f * 0.5f, zero[0], 0.001f)
        assertEquals((4.5 + 7.5 * (0.5 + 0.5 * Math.sin(1.9))).toFloat(), zero[1], 0.001f)
        assertEquals((3.5 + 6.0 * (0.5 + 0.5 * Math.sin(3.7))).toFloat(), zero[2], 0.001f)
        val half = LiveBars.heights(0.5, moving = true)
        assertEquals((3.0 + 5.5 * (0.5 + 0.5 * Math.sin(2.55))).toFloat(), half[0], 0.001f)
        assertEquals((4.5 + 7.5 * (0.5 + 0.5 * Math.sin(0.5 * 6.7 + 1.9))).toFloat(), half[1], 0.001f)
        assertEquals((3.5 + 6.0 * (0.5 + 0.5 * Math.sin(0.5 * 5.9 + 3.7))).toFloat(), half[2], 0.001f)
        assertEquals(listOf(7f, 4f, 9f), LiveBars.heights(12.3, moving = false).toList())
        assertEquals(3f, LiveBars.BAR); assertEquals(3f, LiveBars.GAP); assertEquals(30, LiveBars.FPS)
    }

    companion object {
        /** Antialiasing differs between Qt and Java2D; a wrong shape is far above this. */
        const val LIMIT = 0.12
    }
}
