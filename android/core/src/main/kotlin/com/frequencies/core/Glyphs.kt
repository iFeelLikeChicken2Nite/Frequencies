package com.frequencies.core

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * The desktop's drawn icons (src/shell.py `_make_icon`), as shapes on its
 * 24-unit grid, so the phone draws the same glyphs. Animated glyphs take
 * the frame's progress (0..1) where the desktop asks for a frame name
 * (`playpause0..4`, `timer_0..100`, `eq_25..100`, `star_shine1..4`,
 * `shazam_fill0..4`, `fullscreen2/3`). The app turns [Part]s into Compose
 * paths (ui/Glyphs.kt); tests draw them with Java2D against the desktop's
 * own pictures.
 *
 * Qt conventions are kept: arcs in degrees, counter-clockwise from three
 * o'clock; a pen width of [PEN] is the desktop's 1.6 px at its 22 px size.
 */
object Glyphs {
    /** The desktop's 1.6 px pen at 22 px, in grid units. */
    const val PEN = 1.6f * 24f / 22f

    enum class Ink { MAIN, BASE, GAP }

    sealed interface Shape
    data class Poly(val points: List<Float>, val closed: Boolean) : Shape
    data class Oval(val x: Float, val y: Float, val w: Float, val h: Float) : Shape
    data class Arc(val x: Float, val y: Float, val w: Float, val h: Float, val start: Float, val sweep: Float) : Shape
    /** A rounded rectangle, turned [turn] degrees (clockwise) about the grid's middle. */
    data class RRect(val x: Float, val y: Float, val w: Float, val h: Float, val r: Float, val turn: Float = 0f) : Shape

    /**
     * One mark: [fill] and/or a stroke of [stroke] units (0 = none), at
     * [alpha], in [ink] (MAIN = the glyph's color; BASE = the resting gray
     * under shazam_fill; GAP = the bar's own color). [clip] = a circle
     * (cx, cy, r) the mark is drawn inside.
     */
    data class Part(
        val shape: Shape,
        val fill: Boolean = false,
        val stroke: Float = 0f,
        val alpha: Float = 1f,
        val ink: Ink = Ink.MAIN,
        val clip: Triple<Float, Float, Float>? = null,
        val squareCaps: Boolean = false,
    )

    /** Every name [draw] knows (the resting ones; frames are progress values). */
    val NAMES = listOf(
        "play", "playpause", "pause", "stop", "prev", "next", "star", "star_shine", "star_on", "volume", "eq", "timer",
        "search", "stations", "discover", "history", "trash", "info", "menu", "expand", "collapse", "shazam",
        "shazam_fill", "shazam_on", "gear", "schedule", "help", "tag", "back", "folder", "link", "youtube", "twitch",
        "shared", "thumb_up", "thumb_down", "thumb_up_on", "thumb_down_on", "fullscreen", "plus", "edit", "shuffle", "next_shuffle",
    )

    private fun poly(closed: Boolean, vararg xy: Number) = Poly(xy.map { it.toFloat() }, closed)
    private fun line(x1: Number, y1: Number, x2: Number, y2: Number) = poly(false, x1, y1, x2, y2)
    private fun stroke(s: Shape, w: Float = PEN, alpha: Float = 1f) = Part(s, stroke = w, alpha = alpha)
    private fun fill(s: Shape, alpha: Float = 1f) = Part(s, fill = true, alpha = alpha)
    private fun both(s: Shape) = Part(s, fill = true, stroke = PEN)

    private val STAR = listOf(12f, 3.5f, 14.6f, 9.2f, 20.8f, 10f, 16.3f, 14.3f, 17.5f, 20.4f, 12f, 17.4f, 6.5f, 20.4f, 7.7f, 14.3f, 3.2f, 10f, 9.4f, 9.2f)
    private val STAR_SHINE = listOf(12f, 3.5f, 14.6f, 9.2f, 20.8f, 9.9f, 16.2f, 14.1f, 17.5f, 20.2f, 12f, 17.1f, 6.5f, 20.2f, 7.8f, 14.1f, 3.2f, 9.9f, 9.4f, 9.2f)

    /**
     * [name] at [t]: the play/pause morph (0 = triangle, 1 = two bars), the
     * timer's wind (0..1), the equalizer's knobs (0..1), star_shine's frame
     * (1..4 as 0.25..1), shazam_fill's fill (0..1), fullscreen's push (0..1 =
     * frames 0..2), volume's arcs (0..3 as 0..1), shuffle's arrows crossing
     * (shuffle_0..4 as 0..1) and Next growing its crossing tail (next, then
     * next_shuffle1..4 as 0.25..1). Unknown names draw nothing.
     */
    fun draw(name: String, t: Float = 0f): List<Part> {
        val f = t.coerceIn(0f, 1f)
        return when (name) {
            "play" -> listOf(fill(poly(true, 8, 5, 19, 12, 8, 19)))
            "playpause" -> {
                val top = 5f; val bottom = 19f; val middle = 12f
                val spread = (bottom - top) / 2 * f
                val tip = 19f - 7.5f * f
                buildList {
                    add(fill(poly(true, 7f, top, 7f, bottom, tip, middle + spread, tip, middle - spread)))
                    if (f > 0.05f) {
                        val x = 13.6f
                        add(fill(poly(true, x, middle - spread, x + 4.4f * f, middle - spread, x + 4.4f * f, middle + spread, x, middle + spread)))
                    }
                }
            }
            "pause" -> listOf(fill(RRect(7f, 5f, 3.5f, 14f, 1.5f)), fill(RRect(13.5f, 5f, 3.5f, 14f, 1.5f)))
            "stop" -> listOf(fill(RRect(6.5f, 6.5f, 11f, 11f, 2f)))
            "next" -> listOf(fill(poly(true, 6, 5, 16, 12, 6, 19)), fill(RRect(17f, 5f, 2.2f, 14f, 1f)))
            "prev" -> listOf(fill(poly(true, 18, 5, 8, 12, 18, 19)), fill(RRect(4.8f, 5f, 2.2f, 14f, 1f)))
            "star" -> listOf(stroke(Poly(STAR, true)))
            "star_on" -> listOf(fill(Poly(STAR, true)))
            "star_shine" -> {
                // Frames 1..4: rays thrown further out on each, fading as they go.
                val step = max(1, (f * 4 + 0.001f).toInt())
                val ray = max(0, 220 - step * 55) / 255f
                val inner = 10.5f + step * 1.1f
                val outer = inner + 2.4f
                buildList {
                    add(fill(Poly(STAR_SHINE, true)))
                    for (i in 0 until 8) {
                        val a = (i * 45 + 22) * PI / 180
                        add(stroke(line(12 + cos(a) * inner, 12 + sin(a) * inner, 12 + cos(a) * outer, 12 + sin(a) * outer), alpha = ray))
                    }
                }
            }
            "volume" -> {
                val waves = (f * 3 + 0.001f).toInt()
                buildList {
                    add(fill(poly(true, 4, 9, 8, 9, 12.5, 5, 12.5, 19, 8, 15, 4, 15)))
                    listOf(floatArrayOf(14.5f, 9.5f, 3f, 5f), floatArrayOf(16.5f, 7f, 5f, 10f), floatArrayOf(18.5f, 4.5f, 7f, 15f))
                        .take(waves).forEach { (x, y, w, h) -> add(stroke(Arc(x - w / 2, y, w, h, -70f, 140f))) }
                }
            }
            "eq" -> buildList {
                // The outer two knobs go down, the middle one up (eq_25..eq_100).
                for ((x, knob0, target) in listOf(Triple(5f, 15f, 7f), Triple(12f, 8f, 16f), Triple(19f, 13f, 6f))) {
                    val knob = knob0 + (target - knob0) * f
                    add(stroke(line(x, 4, x, 20)))
                    add(fill(RRect(x - 2.6f, knob - 1.4f, 5.2f, 2.8f, 1.2f)))
                }
            }
            "timer" -> {
                // timer_0..100: the button presses down in the first half, the hands sweep round.
                val lift = 1.5f
                val press = min(1f, f * 2f) * 2.4f
                val cx = 12f; val cy = 15f - lift
                buildList {
                    add(stroke(Oval(4f, 7f - lift, 16f, 16f)))
                    add(stroke(line(9, 3.5f + press - lift, 15, 3.5f + press - lift)))
                    add(stroke(line(12, 3.5f + press - lift, 12, 7f - lift)))
                    for ((length, start, travel) in listOf(Triple(4f, -90f, 360f), Triple(3f, 0f, 30f))) {
                        val angle = (start + travel * f) * PI / 180
                        add(stroke(line(cx, cy, cx + cos(angle) * length, cy + sin(angle) * length)))
                    }
                }
            }
            "search" -> listOf(stroke(Oval(4f, 4f, 12f, 12f)), stroke(line(15, 15, 20, 20)))
            "stations" -> listOf(
                stroke(Oval(9.5f, 9.5f, 5f, 5f)),
                stroke(Arc(5f, 5f, 14f, 14f, 120f, 120f)), stroke(Arc(5f, 5f, 14f, 14f, 300f, 120f)),
                stroke(Arc(1.5f, 1.5f, 21f, 21f, 120f, 120f)), stroke(Arc(1.5f, 1.5f, 21f, 21f, 300f, 120f)),
            )
            "discover" -> listOf(stroke(Oval(3f, 3f, 18f, 18f)), fill(poly(true, 15.5, 8.5, 13, 13, 8.5, 15.5, 11, 11)))
            "history" -> listOf(stroke(Oval(3.5f, 3.5f, 17f, 17f)), stroke(poly(false, 12, 7, 12, 12, 16, 14.5)))
            "trash" -> listOf(
                stroke(line(4, 6.5, 20, 6.5)),
                stroke(poly(false, 9, 6.5, 9, 4, 15, 4, 15, 6.5)),
                stroke(poly(false, 6, 6.5, 7, 20, 17, 20, 18, 6.5)),
                stroke(line(10.5, 10, 10.5, 17)), stroke(line(13.5, 10, 13.5, 17)),
            )
            "info" -> listOf(stroke(Oval(4f, 4f, 16f, 16f)), stroke(line(12, 11, 12, 16.5)), fill(Oval(11f, 7f, 2f, 2f)))
            "menu" -> listOf(8, 12, 16).map { y -> stroke(line(5, y, 19, y)) }
            "expand" -> listOf(stroke(poly(false, 8, 14, 12, 10, 16, 14)))
            // Not a desktop name: expand turned over, for combo boxes and Now Playing's collapse.
            "collapse" -> listOf(stroke(poly(false, 8, 10, 12, 14, 16, 10)))
            "shazam" -> listOf(stroke(Oval(4f, 4f, 16f, 16f)), stroke(Arc(8f, 8f, 8f, 8f, 30f, 300f)))
            "shazam_on" -> listOf(both(Oval(4f, 4f, 16f, 16f)))
            "shazam_fill" -> buildList {
                // The mark twice: gray, then in the color, clipped to a circle growing from the middle.
                add(both(Oval(4f, 4f, 16f, 16f)).copy(ink = Ink.BASE))
                if (f > 0.02f) add(both(Oval(4f, 4f, 16f, 16f)).copy(clip = Triple(12f, 12f, 12f * f * 1.15f)))
            }
            "gear" -> buildList {
                // Teeth as short broad blocks round the rim (filled and outlined), the rim, the hub.
                for (i in 0 until 8) add(both(RRect(12f - 1.9f, 12f - 10.2f, 3.8f, 3.6f, 0.8f, turn = i * 45f)))
                add(stroke(Oval(4.6f, 4.6f, 14.8f, 14.8f), w = 2.2f))
                add(stroke(Oval(9.2f, 9.2f, 5.6f, 5.6f)))
            }
            "schedule" -> listOf(
                stroke(RRect(3.5f, 5f, 17f, 15f, 2f)),
                stroke(line(3.5, 9.5, 20.5, 9.5)), stroke(line(3.5, 13.2, 20.5, 13.2)), stroke(line(3.5, 16.6, 20.5, 16.6)),
                fill(RRect(6f, 10.6f, 5.4f, 1.9f, 0.8f)), fill(RRect(12.6f, 14f, 5.4f, 1.9f, 0.8f)),
            )
            "help" -> listOf(
                stroke(Oval(3.5f, 3.5f, 17f, 17f)),
                stroke(Arc(8.6f, 7f, 6.8f, 6.4f, 200f, -250f)),
                stroke(line(12, 13.2, 12, 15)),
                fill(Oval(11f, 16.6f, 2f, 2f)),
            )
            "tag" -> listOf(stroke(poly(false, 11.5, 3.5, 20.5, 3.5, 20.5, 12.5, 11.5, 21, 3.5, 12.5, 11.5, 3.5)), fill(Oval(15.4f, 6.4f, 3.1f, 3.1f)))
            "back" -> listOf(stroke(poly(false, 13, 5, 6, 12, 13, 19)), stroke(line(6, 12, 20, 12)))
            "folder" -> listOf(stroke(poly(false, 3, 19, 3, 6, 9, 6, 11, 8.5, 21, 8.5, 21, 19, 3, 19)))
            "link" -> listOf(stroke(Arc(3f, 9f, 10f, 6f, 90f, 180f)), stroke(Arc(11f, 9f, 10f, 6f, 270f, 180f)), stroke(line(8, 12, 16, 12)))
            "youtube" -> listOf(stroke(RRect(3f, 6f, 18f, 12f, 3f)), both(poly(true, 10, 9, 16, 12, 10, 15)))
            "twitch" -> listOf(
                stroke(poly(false, 4, 6, 4, 17, 8.5, 17, 11.5, 20.5, 14.5, 17, 20, 17, 20, 6, 4, 6)),
                fill(RRect(9f, 9f, 1.9f, 5f, 0.9f)), fill(RRect(13.5f, 9f, 1.9f, 5f, 0.9f)),
            )
            "shared" -> buildList {
                add(stroke(RRect(3f, 5f, 18f, 14f, 2f)))
                for (row in 0 until 3) for (col in 0 until 3) {
                    if ((row + col) % 2 == 0) add(fill(RRect(6.4f + col * 2.6f, 8.2f + row * 2.6f, 1.8f, 1.8f, 0f)))
                }
                add(fill(RRect(14.4f, 13.4f, 1.8f, 1.8f, 0f)))
            }
            "thumb_up", "thumb_down", "thumb_up_on", "thumb_down_on" -> {
                val down = "down" in name
                val filled = name.endsWith("_on")
                fun pt(vararg xy: Number) = xy.map { it.toFloat() }.let { if (down) it.mapIndexed { _, v -> 24f - v } else it }
                val cuff = if (down) RRect(24f - 3.5f - 4.5f, 24f - 10.5f - 9.5f, 4.5f, 9.5f, 1.2f) else RRect(3.5f, 10.5f, 4.5f, 9.5f, 1.2f)
                val hand = Poly(pt(9, 20, 9, 10.5, 13, 3.6, 14.6, 3.6, 15.2, 5.2, 14.2, 9.2, 19.6, 9.2, 20.6, 10.6, 19.2, 19, 17.6, 20), true)
                listOf(Part(cuff, fill = filled, stroke = PEN), Part(hand, fill = filled, stroke = PEN))
            }
            "fullscreen" -> {
                // The corners pushed outward: t 0, 0.5, 1 = the desktop's steps 0, 1, 2 (d = step × 1.1).
                val d = f * 2 * 1.1f
                listOf(
                    stroke(poly(false, 9 - d, 4 - d, 4 - d, 4 - d, 4 - d, 9 - d)),
                    stroke(poly(false, 15 + d, 4 - d, 20 + d, 4 - d, 20 + d, 9 - d)),
                    stroke(poly(false, 9 - d, 20 + d, 4 - d, 20 + d, 4 - d, 15 + d)),
                    stroke(poly(false, 15 + d, 20 + d, 20 + d, 20 + d, 20 + d, 15 + d)),
                )
            }
            "plus" -> listOf(stroke(line(12, 6, 12, 18)), stroke(line(6, 12, 18, 12)))
            "edit" -> listOf(
                stroke(poly(true, 15.5, 4.5, 19.5, 8.5, 9, 19, 5, 19, 5, 15)),
                stroke(line(13, 7, 17, 11)),
                stroke(line(12, 20.5, 20, 20.5)),
            )
            "shuffle" -> {
                // shuffle_0 (parallel) through shuffle_4 (crossed): the two paths swap ends.
                val topOut = 8 + 8 * f
                val bottomOut = 16 - 8 * f
                listOf(
                    stroke(poly(false, 3, 8, 8, 8, 15, topOut, 18, topOut)),
                    stroke(poly(false, 3, 16, 8, 16, 15, bottomOut, 18, bottomOut)),
                    fill(poly(true, 17, topOut - 3, 21, topOut, 17, topOut + 3)),
                    fill(poly(true, 17, bottomOut - 3, 21, bottomOut, 17, bottomOut + 3)),
                )
            }
            "next_shuffle" -> if (f <= 0f) draw("next") else buildList {
                // next_shuffle1..4: the triangle fades into two crossing paths at the arrow's own weight.
                add(fill(RRect(19f, 5f, 2.4f, 14f, 1f)))
                if (f < 0.999f) add(fill(poly(true, 7, 5, 17.5, 12, 7, 19), alpha = 1f - f))
                if (f > 0.001f) {
                    val w = 1.9f
                    add(Part(poly(false, 3, 8, 6.5, 8, 12, 16, 14.5, 16), stroke = w, alpha = f))
                    add(Part(poly(false, 3, 16, 6.5, 16, 8.8, 12.6), stroke = w, alpha = f))
                    add(Part(poly(false, 10.6, 10.2, 12, 8, 14.5, 8), stroke = w, alpha = f))
                    add(fill(poly(true, 14, 5, 17.5, 8, 14, 11), alpha = f))
                    add(fill(poly(true, 14, 13, 17.5, 16, 14, 19), alpha = f))
                }
            }
            else -> emptyList()
        }
    }
}

/**
 * The desktop's glyph animations, frame for frame: how many frames, how
 * long each lasts, and what progress each frame draws (shell.py PlayerBar,
 * _animate_glyph, TagPill, open_menu).
 */
object GlyphMotion {
    data class Frames(val name: String, val frames: Int, val ms: Int) {
        val durationMs: Int get() = frames * ms
        /** The progress [Glyphs.draw] takes at frame [i] (0..frames). */
        fun progress(i: Int): Float = i.coerceIn(0, frames).toFloat() / frames
        /** The frame showing [elapsedMs] into a run from frame 0. */
        fun frameAt(elapsedMs: Long): Int = (elapsedMs / ms).toInt().coerceIn(0, frames)
        /** A smooth 0..1 value snapped to this animation's frames, as the desktop steps them. */
        fun snap(value: Float): Float = (value * frames + 0.0001f).toInt().coerceIn(0, frames).toFloat() / frames
    }

    /** Play/pause: playpause0..4, 26 ms a step (MORPH_FRAMES, _play_timer). */
    val PLAY_PAUSE = Frames("playpause", 4, 26)
    /** Star shine: star_shine1..4, 55 ms each, then the lit star (SHINE_FRAMES, _shine_timer). */
    val STAR_SHINE = Frames("star_shine", 4, 55)
    /** Song ID: 14 steps of 40 ms; the fill over the first 45%, then an eased settle (_pulse_tick). */
    val SONG_ID = Frames("shazam", 14, 40)
    const val SONG_ID_FILL = 0.45f
    /** Sleep timer: timer_0..100 in tens, 24 ms a step. */
    val TIMER = Frames("timer", 10, 24)
    /** Equalizer: eq, eq_25, eq_50, eq_75, eq_100, 28 ms a step. */
    val EQ = Frames("eq", 4, 28)
    /** Comfort View: fullscreen, then the corners pushed out in two steps (fullscreen1, fullscreen2), 28 ms. */
    val FULLSCREEN = Frames("fullscreen", 2, 28)
    /** Shuffle: shuffle_0..4, the arrows walking across, 30 ms a step (_animate_shuffle). */
    val SHUFFLE = Frames("shuffle", 4, 30)
    /** Next while shuffle is on: next, next_shuffle1..4, 30 ms a step (_animate_next). */
    val NEXT_SHUFFLE = Frames("next_shuffle", 4, 30)
    /** The ripple over the Song ID button (Ripple.DURATION, out-cubic). */
    const val RIPPLE_MS = 560
    /** TagPill.STRIKE_MS, out-cubic. */
    const val STRIKE_MS = 180
    /** open_menu: MENU_GROW_MS from MENU_FROM, out-cubic. */
    const val MENU_MS = 130
    const val MENU_FROM = 0.86f

    fun outCubic(t: Float): Float { val u = 1f - t.coerceIn(0f, 1f); return 1f - u * u * u }

    /**
     * Song ID at step [step] (0..14): the shazam_fill progress while
     * filling, else null with [settleLighter] giving QColor.lighter's
     * factor for the lit mark.
     */
    fun songIdFill(step: Int): Float? {
        val t = step.toFloat() / SONG_ID.frames
        return if (t <= SONG_ID_FILL) (t / SONG_ID_FILL * 4).toInt() / 4f else null
    }

    fun settleLighter(step: Int): Int {
        val t = step.toFloat() / SONG_ID.frames
        val eased = 1f - (1f - (t - SONG_ID_FILL) / (1f - SONG_ID_FILL)).let { it * it * it }
        return (150 - 50 * eased).toInt()
    }

    /**
     * QColor.lighter(factor) on 0xRRGGBB, as Qt 5.15 does it: in its 16-bit
     * HSV (value up; past white, saturation down instead), then back to
     * 8-bit channels by round(x / 257).
     */
    fun lighter(rgb: Int, factor: Int): Int {
        if (factor <= 0 || factor == 100) return rgb
        val r = ((rgb shr 16) and 0xFF) * 0x101; val g = ((rgb shr 8) and 0xFF) * 0x101; val b = (rgb and 0xFF) * 0x101
        val mx = max(r, max(g, b)); val mn = min(r, min(g, b))
        val delta = (mx - mn).toFloat()
        var sat: Int
        var hue: Int                               // hundredths of a degree, -1 = none
        if (delta == 0f) { sat = 0; hue = -1 } else {
            sat = Math.round(delta / mx * 65535f)
            var h = when (mx) {
                r -> (g - b) / delta
                g -> 2f + (b - r) / delta
                else -> 4f + (r - g) / delta
            } * 60f
            if (h < 0f) h += 360f
            hue = Math.round(h * 100f)
        }
        var v = factor.toLong() * mx / 100
        if (v > 65535) {
            sat -= (v - 65535).toInt()
            if (sat < 0) sat = 0
            v = 65535
        }
        val h = if (hue < 0) 0f else hue / 6000f
        val sf = sat / 65535f; val vf = v / 65535f
        val i = h.toInt(); val f = h - i
        val p = vf * (1f - sf)
        val (rr, gg, bb) = if (i and 1 != 0) {
            val q = vf * (1f - sf * f)
            when (i) { 1 -> Triple(q, vf, p); 3 -> Triple(p, q, vf); else -> Triple(vf, p, q) }
        } else {
            val t = vf * (1f - sf * (1f - f))
            when (i) { 0, 6 -> Triple(vf, t, p); 2 -> Triple(p, vf, t); else -> Triple(t, p, vf) }
        }
        fun ch(x: Float): Int { val w = Math.round(x * 65535f); return ((w + 128) / 257).coerceIn(0, 255) }
        return (ch(rr) shl 16) or (ch(gg) shl 8) or ch(bb)
    }
}

/** The playing row's three bars (desktop stationlist._draw_live): heights in dp. */
object LiveBars {
    const val BAR = 3f
    const val GAP = 3f
    const val FPS = 30
    /** (speed, offset, span, base) per bar. */
    val WAVES = listOf(
        floatArrayOf(5.1f, 0.0f, 5.5f, 3.0f),
        floatArrayOf(6.7f, 1.9f, 7.5f, 4.5f),
        floatArrayOf(5.9f, 3.7f, 6.0f, 3.5f),
    )
    /** Paused (or reduced motion): uneven bars standing still, not three dots. */
    val STILL = floatArrayOf(7f, 4f, 9f)

    fun heights(seconds: Double, moving: Boolean): FloatArray =
        if (!moving) STILL.copyOf()
        else FloatArray(3) { i ->
            val (speed, offset, span, base) = WAVES[i]
            (base + span * (0.5 + 0.5 * sin(seconds * speed + offset))).toFloat()
        }
}
