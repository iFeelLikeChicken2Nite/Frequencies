package com.frequencies.core

import java.security.MessageDigest

/**
 * stationlist.station_hue / station_color: a station's own color when it
 * has no artwork, from its name. The desktop frames a shared QR code in
 * it, so the phone's code for the same station is the same color.
 */
object StationColor {
    /** stationlist.MONOGRAM_HUES */
    val HUES = intArrayOf(12, 32, 48, 96, 152, 188, 210, 262, 292, 330)

    /** stationlist._display_name: custom name, then name, then "?". */
    fun nameOf(station: Station): String = station.customName ?: station.name.ifEmpty { "?" }

    fun hue(name: String): Int {
        val digest = MessageDigest.getInstance("MD5").digest(name.ifEmpty { "?" }.toByteArray(Charsets.UTF_8))
        return HUES[(digest[0].toInt() and 0xFF) % HUES.size]
    }

    /** QColor.setHsl(hue, 130, 150): lighter than the tile so a border shows on a dark window. */
    fun fromName(name: String): Int = hsl(hue(name), 130, 150)

    /** Qt's HSL, all three out of 0..359 / 0..255, as opaque ARGB. */
    fun hsl(h: Int, s: Int, l: Int): Int {
        val hh = h / 360.0
        val ss = s / 255.0
        val ll = l / 255.0
        if (ss == 0.0) { val v = Math.round(ll * 255).toInt(); return argb(v, v, v) }
        val q = if (ll < 0.5) ll * (1 + ss) else ll + ss - ll * ss
        val p = 2 * ll - q
        fun ch(t0: Double): Int {
            var t = t0
            if (t < 0) t += 1.0
            if (t > 1) t -= 1.0
            val v = when {
                t < 1.0 / 6 -> p + (q - p) * 6 * t
                t < 0.5 -> q
                t < 2.0 / 3 -> p + (q - p) * (2.0 / 3 - t) * 6
                else -> p
            }
            return Math.round(v * 255).toInt().coerceIn(0, 255)
        }
        return argb(ch(hh + 1.0 / 3), ch(hh), ch(hh - 1.0 / 3))
    }

    private fun argb(r: Int, g: Int, b: Int) = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
}
