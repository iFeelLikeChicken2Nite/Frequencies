package com.frequencies.core

import kotlin.math.sqrt

/**
 * The ad hourglass (the desktop's shell.Hourglass, builds 71/78/85): how
 * much of a covered opening ad is left, as sand. It follows the player's
 * own ad clock (player.ad_time_left: the whole ad while the station is
 * still connecting, 0 once the ad time is up), the empty glass stays
 * [EMPTY_HOLD_MS] and then goes. The sand is drawn by area, as real sand
 * falls: half the ad gone, half the sand down.
 *
 * Shapes are in the glyphs' 24-unit grid ([Glyphs]).
 */
object AdGlass {
    const val EMPTY_HOLD_MS = 1_500L
    const val TICK_MS = 200L

    // The glass: two triangles meeting at the waist, capped top and bottom.
    const val TOP = 4f
    const val BOTTOM = 20f
    const val WAIST = 12f
    const val LEFT = 6f
    const val RIGHT = 18f
    const val MID = 12f

    /** The frame's lines, each as x1, y1, x2, y2. */
    val FRAME: List<List<Float>> = listOf(
        listOf(LEFT, TOP, RIGHT, TOP),
        listOf(LEFT, BOTTOM, RIGHT, BOTTOM),
        listOf(LEFT, TOP, MID, WAIST), listOf(MID, WAIST, LEFT, BOTTOM),
        listOf(RIGHT, TOP, MID, WAIST), listOf(MID, WAIST, RIGHT, BOTTOM),
    )

    /**
     * The sand at [run] (0 = the ad just began, 1 = over): the upper cone
     * and the lower pile as polygons (x, y, x, y, …), and the falling
     * stream as a line (x1, y1, x2, y2), each null when there is none.
     */
    data class Sand(val upper: List<Float>?, val lower: List<Float>?, val stream: List<Float>?)

    fun sand(run: Float): Sand {
        val r = run.coerceIn(0f, 1f)
        val remaining = 1f - r
        val bulb = WAIST - TOP
        val halfCap = (RIGHT - LEFT) / 2
        var upper: List<Float>? = null
        var lower: List<Float>? = null
        var stream: List<Float>? = null
        if (remaining > 0.02f) {
            // A cone on the waist; its area goes with its height squared.
            val scale = sqrt(remaining)
            val y = WAIST - bulb * scale
            val half = halfCap * scale
            upper = listOf(MID - half, y, MID + half, y, MID, WAIST)
        }
        if (r > 0.02f) {
            // A pile from the bottom up: the bulb narrows toward the waist, so the first sand barely raises it.
            val narrow = sqrt(remaining)
            val y = BOTTOM - bulb * (1f - narrow)
            val half = halfCap * narrow
            lower = if (narrow > 0.02f) listOf(MID - half, y, MID + half, y, MID + halfCap, BOTTOM, MID - halfCap, BOTTOM)
            else listOf(MID, WAIST, MID + halfCap, BOTTOM, MID - halfCap, BOTTOM)
            if (remaining > 0.02f) stream = listOf(MID, WAIST, MID, y)
        }
        return Sand(upper, lower, stream)
    }

    /** How much of the ad has run, from the seconds [left] of [total]. */
    fun run(total: Double, left: Double): Float =
        if (total <= 0) 1f else (1.0 - (left / total)).coerceIn(0.0, 1.0).toFloat()

    /** The tooltip's words (Hourglass._say_time), for TalkBack. */
    fun timeText(left: Double?): String {
        if (left == null || left <= 0) return "Ad over"
        val s = (left + 0.5).toInt()
        return "Ad playing. Time remaining: ${s / 60}:%02d".format(s % 60)
    }

    /** Polygon area (shoelace), for the tests: the sand by area. */
    fun area(xy: List<Float>?): Double {
        if (xy == null || xy.size < 6) return 0.0
        var a = 0.0
        val n = xy.size / 2
        for (i in 0 until n) {
            val j = (i + 1) % n
            a += xy[2 * i].toDouble() * xy[2 * j + 1] - xy[2 * j].toDouble() * xy[2 * i + 1]
        }
        return kotlin.math.abs(a) / 2
    }
}
