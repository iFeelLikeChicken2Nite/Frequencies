package com.frequencies.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * The desktop's accent-from-artwork pick (theme.py: dominant_color,
 * readable_accent, _blend, AccentController's fade), with Qt's own integer
 * color math and its nearest-pixel 24x24 scale, so one station's art gives
 * the same accent on the phone and the desktop (ArtAccentTest checks the
 * desktop's answers for real station art).
 *
 * Colors are 0xRRGGBB ints (alpha ignored).
 */
object ArtAccent {
    /** theme.DEFAULT_ACCENT: OG's dial amber. */
    const val DEFAULT_ACCENT = 0xD99A2B

    /** theme.MIN_CHROMA: brightest channel minus darkest, for a pixel to count. */
    private const val MIN_CHROMA = 28

    /** AccentController.MIN_SHIFT: changes smaller than this (RGB sum) are ignored. */
    const val MIN_SHIFT = 14
    /** AccentController.FADE_MS / FADE_STEP: a second, about 30 frames. */
    const val FADE_MS = 1000
    const val FADE_STEP_MS = 33

    /**
     * theme.dominant_color(image, fallback=None): a vivid color from the
     * art, or null when it has none (white, gray, black, pale tints, a speck).
     * [argb] is the decoded picture, row by row, [width] x [height].
     */
    fun dominant(argb: IntArray, width: Int, height: Int): Int? {
        if (width <= 0 || height <= 0) return null
        val small = scaled24(argb, width, height)
        // hue bucket -> population, saturation sum, r, g, b sums
        // Insertion order, as a Python dict keeps it: max() keeps the first of equal scores.
        val buckets = LinkedHashMap<Int, LongArray>()
        for (px in small) {
            val r = (px shr 16) and 0xFF
            val g = (px shr 8) and 0xFF
            val b = px and 0xFF
            val (h, s, l) = hsl(r, g, b)
            if (s < 40 || l < 35 || l > 225) continue
            if (max(r, max(g, b)) - min(r, min(g, b)) < MIN_CHROMA) continue
            val v = buckets.getOrPut(h / 20) { LongArray(5) }
            v[0]++; v[1] += s.toLong(); v[2] += r.toLong(); v[3] += g.toLong(); v[4] += b.toLong()
        }
        val need = max(4, (SIZE * SIZE) / 100)
        var best: LongArray? = null
        for (v in buckets.values) {
            if (v[0] < need) continue
            if (best == null || v[0] * v[1] > best[0] * best[1]) best = v
        }
        if (best == null) return null
        val v = best!!
        val pop = v[0]
        return readable(rgb((v[2] / pop).toInt(), (v[3] / pop).toInt(), (v[4] / pop).toInt()))
    }

    /** theme.readable_accent: clamp any color into one usable as the accent. */
    fun readable(color: Int): Int {
        var (h, s, l) = hsl(color)
        if (h < 0) h = 40
        s = s.coerceIn(90, 200)
        l = l.coerceIn(120, 175)
        return fromHsl(h, s, l)
    }

    /** theme._blend: mix through hue, the short way round, not through gray. */
    fun blend(a: Int, b: Int, t: Double): Int {
        var (ah, aS, al) = hsl(a)
        var (bh, bS, bl) = hsl(b)
        if (ah < 0) ah = bh
        if (bh < 0) bh = ah
        if (ah < 0 && bh < 0) { ah = 0; bh = 0 }
        val delta = Math.floorMod(bh - ah + 540, 360) - 180
        // Python's % is a floor modulo, int() truncates.
        val hue = floorModD(ah + delta * t, 360.0)
        return fromHsl(hue.toInt(), (aS + (bS - aS) * t).toInt(), (al + (bl - al) * t).toInt())
    }

    private fun floorModD(x: Double, m: Double): Double = x - Math.floor(x / m) * m

    /** AccentController._fade_tick: the color at [frame] of a fade from [from] to [to]. */
    fun fadeStep(from: Int, to: Int, frame: Int): Int {
        val frames = max(1, FADE_MS / FADE_STEP_MS)
        val t = if (frame >= frames) 1.0 else frame.toDouble() / frames
        val eased = 1.0 - Math.pow(1.0 - t, 3.0)
        return blend(from, to, eased)
    }

    val fadeFrames: Int get() = max(1, FADE_MS / FADE_STEP_MS)

    /** AccentController._distance. */
    fun distance(a: Int, b: Int): Int =
        abs(((a shr 16) and 0xFF) - ((b shr 16) and 0xFF)) +
            abs(((a shr 8) and 0xFF) - ((b shr 8) and 0xFF)) + abs((a and 0xFF) - (b and 0xFF))

    // --- Qt -----------------------------------------------------------------

    private const val SIZE = 24
    private const val USHRT_MAX = 65535

    /**
     * QImage.scaled(24, 24) (IgnoreAspectRatio, FastTransformation): the
     * nearest pixel, picked the way Qt 5.15 picks it. Measured against
     * PyQt5 on pictures from 25 to 4096 px (every pixel told apart): along
     * a side that is a whole multiple of 24 Qt takes pixel x*k + (k-1)/2,
     * else qt_xForm_helper's 4096ths fixed point, (m/2 + x*m) >> 12 with
     * m = int(1 / (24 / side) * 4096).
     */
    fun scaled24(argb: IntArray, ws: Int, hs: Int): IntArray {
        val xs = IntArray(SIZE) { qtPick(it, ws) }
        val ys = IntArray(SIZE) { qtPick(it, hs) }
        return IntArray(SIZE * SIZE) { i -> argb[ys[i / SIZE] * ws + xs[i % SIZE]] }
    }

    private fun qtPick(x: Int, side: Int): Int {
        if (side % SIZE == 0) {
            val k = side / SIZE
            return x * k + (k - 1) / 2
        }
        val m = ((1.0 / (SIZE.toDouble() / side)) * 4096.0).toLong()
        return ((m / 2 + x * m) shr 12).toInt().coerceIn(0, side - 1)
    }

    data class Hsl(val h: Int, val s: Int, val l: Int)

    fun hsl(color: Int): Hsl = hsl((color shr 16) and 0xFF, (color shr 8) and 0xFF, color and 0xFF)

    /** QColor::toHsl + getHsl: hue -1 for grays, s and l 0-255. */
    fun hsl(r8: Int, g8: Int, b8: Int): Hsl {
        val r = (r8 * 0x101) / USHRT_MAX.toDouble()
        val g = (g8 * 0x101) / USHRT_MAX.toDouble()
        val b = (b8 * 0x101) / USHRT_MAX.toDouble()
        val mx = max(r, max(g, b))
        val mn = min(r, min(g, b))
        val delta = mx - mn
        val delta2 = mx + mn
        val lightness = 0.5 * delta2
        val l16 = qRound(lightness * USHRT_MAX)
        if (abs(delta) <= 0.000000000001) return Hsl(-1, 0, div257(l16))
        val s16 = if (lightness < 0.5) qRound((delta / delta2) * USHRT_MAX)
        else qRound((delta / (2.0 - delta2)) * USHRT_MAX)
        var hue = when {
            fuzzy(r, mx) -> (g - b) / delta
            fuzzy(g, mx) -> 2.0 + (b - r) / delta
            else -> 4.0 + (r - g) / delta
        }
        hue *= 60.0
        if (hue < 0.0) hue += 360.0
        val h100 = qRound(hue * 100)
        return Hsl(h100 / 100, div257(s16), div257(l16))
    }

    /** QColor::setHsl(h, s, l) then the RGB it shows. */
    fun fromHsl(h: Int, s: Int, l: Int): Int {
        val hue16 = if (h == -1) USHRT_MAX else Math.floorMod(h, 360) * 100
        val s16 = s * 0x101
        val l16 = l * 0x101
        if (s16 == 0 || hue16 == USHRT_MAX) return rgb(div257(l16), div257(l16), div257(l16))
        if (l16 == 0) return 0
        val hh = if (hue16 == 36000) 0.0 else hue16 / 36000.0
        val ss = s16 / USHRT_MAX.toDouble()
        val ll = l16 / USHRT_MAX.toDouble()
        val temp2 = if (ll < 0.5) ll * (1.0 + ss) else ll + ss - (ll * ss)
        val temp1 = (2.0 * ll) - temp2
        val temp3 = doubleArrayOf(hh + (1.0 / 3.0), hh, hh - (1.0 / 3.0))
        val c = IntArray(3)
        for (i in 0..2) {
            if (temp3[i] < 0.0) temp3[i] += 1.0 else if (temp3[i] > 1.0) temp3[i] -= 1.0
            val six = temp3[i] * 6.0
            c[i] = when {
                six < 1.0 -> qRound((temp1 + (temp2 - temp1) * six) * USHRT_MAX)
                temp3[i] * 2.0 < 1.0 -> qRound(temp2 * USHRT_MAX)
                temp3[i] * 3.0 < 2.0 -> qRound((temp1 + (temp2 - temp1) * (2.0 / 3.0 - temp3[i]) * 6.0) * USHRT_MAX)
                else -> qRound(temp1 * USHRT_MAX)
            }
            if (c[i] == 1) c[i] = 0
        }
        return rgb(div257(c[0]), div257(c[1]), div257(c[2]))
    }

    private fun qRound(d: Double): Int =
        if (d >= 0.0) (d + 0.5).toInt() else (d - (d - 1).toInt().toDouble() + 0.5).toInt() + (d - 1).toInt()

    // Qt 5.15 reads a 16-bit channel back as the nearest 8-bit value (checked against PyQt5).
    private fun div257(x: Int): Int = qRound(x / 257.0)

    private fun fuzzy(p1: Double, p2: Double): Boolean = abs(p1 - p2) * 1000000000000.0 <= min(abs(p1), abs(p2))

    fun rgb(r: Int, g: Int, b: Int): Int = (r shl 16) or (g shl 8) or b
}
