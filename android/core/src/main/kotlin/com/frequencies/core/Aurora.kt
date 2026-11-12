package com.frequencies.core

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * Now Playing's moving background (comfort spec 8): the desktop's
 * NowPlaying._paint_aurora ported. Four soft pools of the accent's hue
 * drift past each other over the canvas, under a veil of the canvas color
 * that is made thick enough for every text color to read on the brightest
 * spot a pool can make. The phone draws it; this decides the numbers.
 */
object Aurora {
    /** "Moving background": Auto (stops for Battery Saver and a low battery), Always, Off (the still wash). */
    const val SETTING = "android_moving_bg"
    const val AUTO = "auto"
    const val ALWAYS = "always"
    const val OFF = "off"
    val CHOICES = listOf(AUTO to "Auto", ALWAYS to "Always", OFF to "Off")

    fun setting(settings: Settings?): String = settings?.string(SETTING, AUTO)?.takeIf { v -> CHOICES.any { it.first == v } } ?: AUTO

    /** desktop AURORA_SPEED is 0.55; the mock-up's calmer 0.5. */
    const val SPEED = 0.5
    const val FPS = 30
    /** After two minutes without a touch the background draws half as often. */
    const val SLOW_FPS = 15
    const val SLOW_AFTER_MS = 120_000L
    /** Below this battery percentage (not charging), Auto stands still. */
    const val LOW_BATTERY = 20
    /** The pools' time while standing still: the mock-up's "Still" frame. */
    const val STILL_T = 14.0
    /** Play and pause ease the drift's speed in and out over this long. */
    const val EASE_MS = 450
    /** A new accent cross-fades the pools over this long. */
    const val COLOR_MS = 1000

    /** One pool: hue shift (degrees), x and y frequency, size (share of the width), strength. */
    data class Pool(val shift: Int, val fx: Double, val fy: Double, val size: Double, val strength: Double)

    /** nowplaying.py _paint_aurora's four pools, unchanged. */
    val POOLS = listOf(
        Pool(0, 0.11, 0.07, 0.62, 0.9),
        Pool(28, 0.07, 0.13, 0.55, 0.8),
        Pool(-26, 0.09, 0.05, 0.60, 0.75),
        Pool(12, 0.05, 0.09, 0.48, 0.7),
    )

    /** Where pool [index] is at time [t] (already scaled by SPEED) on a [w] x [h] canvas: center x, y and radius. */
    fun position(index: Int, t: Double, w: Double, h: Double): Triple<Double, Double, Double> {
        val p = POOLS[index]
        val phase = index * 1.7
        val cx = w * (0.5 + 0.38 * sin(t * p.fx * 2 * PI + phase))
        val cy = h * (0.5 + 0.38 * cos(t * p.fy * 2 * PI + phase * 1.3))
        return Triple(cx, cy, w * p.size)
    }

    /**
     * How the background runs now. [setting] is [SETTING]'s value;
     * [removeAnimations]: animator duration scale 0; [saver]: Battery
     * Saver; [battery] percent or null when unknown; [inFront]: the app
     * resumed (the screen is on and Now Playing shows); [idleMs] since the
     * last touch. OFF: the still wash instead. STILL: the still frame
     * ([STILL_T]), no clock. HOLD: nobody sees it, the clock stops where
     * it is. SLOW / FULL: moving at [SLOW_FPS] / [FPS].
     */
    enum class Run { OFF, STILL, HOLD, SLOW, FULL }

    fun run(setting: String, removeAnimations: Boolean, saver: Boolean, battery: Int?, charging: Boolean, inFront: Boolean, idleMs: Long): Run = when {
        setting == OFF -> Run.OFF
        removeAnimations -> Run.STILL
        !inFront -> Run.HOLD
        setting != ALWAYS && (saver || (battery != null && battery < LOW_BATTERY && !charging)) -> Run.STILL
        idleMs >= SLOW_AFTER_MS -> Run.SLOW
        else -> Run.FULL
    }

    /** Frames a second for [run] (0: no clock). */
    fun fps(run: Run): Int = when (run) {
        Run.FULL -> FPS
        Run.SLOW -> SLOW_FPS
        else -> 0
    }

    /**
     * The drift's speed (0 standing, 1 full) [dtMs] later, easing toward
     * 1 while playing and 0 when paused over [EASE_MS], so pausing settles
     * the pools instead of freezing them.
     */
    fun ease(speed: Double, playing: Boolean, dtMs: Double): Double {
        val step = dtMs / EASE_MS
        return if (playing) minOf(1.0, speed + step) else maxOf(0.0, speed - step)
    }

    /**
     * The colors for a theme and accent: the canvas, the four pools (at
     * their centers; they fade to nothing at their edge), how they are
     * laid on ([screen] for dark themes, plain painting with [poolAlpha]
     * for light ones) and the veil of [base] over them.
     */
    data class Look(val base: Int, val pools: List<Int>, val screen: Boolean, val poolAlpha: List<Double>, val veil: Double)

    const val DARK_LIGHTNESS = 96          // of 255 (37.6%), times the pool's strength: the desktop's
    const val LIGHT_LIGHTNESS = 133        // 52%
    const val LIGHT_ALPHA = 0.34
    const val DARK_VEIL = 0.24
    const val LIGHT_VEIL = 0.30
    const val VEIL_STEP = 0.04
    const val SAT_MIN = 166                // 65%
    const val SAT_MAX = 242                // 95%

    fun look(scheme: ThemeScheme, wash: ComfortWash): Look {
        val (h, s, _) = ArtAccent.hsl(scheme.primary)
        val hue = if (h >= 0) h else 30
        val sat = s.coerceIn(SAT_MIN, SAT_MAX)
        val light = scheme.light
        val pools = POOLS.map { p ->
            val l = if (light) LIGHT_LIGHTNESS else (DARK_LIGHTNESS * p.strength).toInt()
            ArtAccent.fromHsl(Math.floorMod(hue + p.shift, 360), sat, l)
        }
        val alpha = POOLS.map { if (light) LIGHT_ALPHA * it.strength else 1.0 }
        var veil = if (light) LIGHT_VEIL else DARK_VEIL
        while (veil < 1.0) {
            val reads = spots(scheme.background, pools, !light, alpha, veil).all { spot -> reads(wash, spot) }
            if (reads) break
            veil = minOf(1.0, veil + VEIL_STEP)
        }
        return Look(scheme.background, pools, !light, alpha, (veil * 100).roundToInt() / 100.0)
    }

    /**
     * Body text (text, text2) at 4.5:1; the accent at 3:1, as it only
     * colors the line being sung, which is large and semibold.
     */
    fun reads(wash: ComfortWash, spot: Int): Boolean =
        ThemeScheme.contrast(wash.text, spot) >= ThemeScheme.TEXT &&
            ThemeScheme.contrast(wash.text2, spot) >= ThemeScheme.TEXT &&
            ThemeScheme.contrast(wash.accent, spot) >= ThemeScheme.GRAPHIC

    /**
     * Every color the background can show at a pool's center: each mix of
     * overlapping pools (none to all four) over [base], under the veil.
     */
    fun spots(base: Int, pools: List<Int>, screen: Boolean, alpha: List<Double>, veil: Double): List<Int> =
        (0 until (1 shl pools.size)).map { mask ->
            var c = base
            pools.forEachIndexed { i, pool ->
                if (mask and (1 shl i) != 0) c = if (screen) screen(c, pool, alpha[i]) else ThemeScheme.mix(pool, c, alpha[i])
            }
            ThemeScheme.mix(base, c, veil)
        }

    /** Screen blend of [src] at [a] over [dst]. */
    fun screen(dst: Int, src: Int, a: Double): Int {
        fun ch(s: Int): Int {
            val d = ((dst shr s) and 0xFF) / 255.0
            val v = ((src shr s) and 0xFF) / 255.0
            val out = d + (1 - (1 - d) * (1 - v) - d) * a
            return (out * 255 + 0.5).toInt().coerceIn(0, 255)
        }
        return (ch(16) shl 16) or (ch(8) shl 8) or ch(0)
    }
}
