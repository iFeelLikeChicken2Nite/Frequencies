package com.frequencies.core

import java.io.File
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * Band logos in place of the artist's name (the desktop's bandlogo.py,
 * build 141). The developer's logos come in a pack of their own beside
 * the photo pack (logo-pack.json, `<data>/logo_pack/`), fetched whether
 * or not the photos are switched on, unless Settings' "band_logos" is off
 * (on by default). The logo is never recolored; [needsOutline] says when
 * it would be lost against what is behind it.
 */
object BandLogos {
    const val SETTING = "band_logos"
    const val FOLDER = "logo_pack"
    const val MANIFEST = "logo-pack.json"
    const val SCALE_MIN = 0.3
    const val SCALE_MAX = 2.0
    /** bandlogo.BOX: the name's area, made room for a logo (dp on the phone). */
    const val BOX = 96
    /** bandlogo.FADE_MS / SLIDE_PX */
    const val FADE_MS = 350
    const val SLIDE = 16

    data class Logo(val file: File, val scale: Double, val x: Double, val y: Double)

    /** bandlogo.placement: (scale, x, y) within bounds. */
    fun placement(entry: Map<String, JsonElement>): Triple<Double, Double, Double> {
        fun number(name: String, default: Double, low: Double, high: Double) =
            (entry[name].num() ?: default).coerceIn(low, high)
        return Triple(number("logo_scale", 1.0, SCALE_MIN, SCALE_MAX), number("logo_x", 0.0, -1.0, 1.0), number("logo_y", 0.0, -1.0, 1.0))
    }

    /**
     * bandlogo._match: this artist's own entry (its name, or the same name
     * spelled differently), or one shown under that name ("Show as").
     * Never a collaboration's first artist: the logo would hide the others.
     */
    fun matchKey(table: Map<String, JsonObject>, artist: String): String? {
        val (key, how) = ArtistOverrides.findArtist(table, artist)
        if (key.isNotEmpty() && (how.isEmpty() || how == "spelled differently")) return key
        val loose = ArtistOverrides.looseArtist(artist)
        return table.entries.firstOrNull { (_, e) -> e["name"].str()?.let { ArtistOverrides.looseArtist(it) == loose } == true }?.key
    }

    /** bandlogo.find: the logo pack's logo for [artist], or null (off, none, or its file isn't here). */
    fun find(store: PhotoPackStore, artist: String?, on: Boolean): Logo? {
        val name = artist.orEmpty().trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
        if (name.isEmpty() || !on) return null
        val table = store.packNames()
        val key = matchKey(table, name) ?: return null
        val entry = store.logoEntry(key).takeIf { it.isNotEmpty() } ?: return null
        val path = entry["logo"].str().orEmpty()
        if (path.isEmpty() || !File(path).isFile) return null
        val (scale, x, y) = placement(entry)
        return Logo(File(path), scale, x, y)
    }

    /** A rectangle in any unit. */
    data class Box(val left: Double, val top: Double, val width: Double, val height: Double) {
        val right get() = left + width
        val bottom get() = top + height
    }

    /**
     * bandlogo.place: the logo fitted into [box] (keeping its shape), times
     * scale, at the box's bottom left (where the name sits), moved by x / y
     * box widths / heights; then kept inside [bounds], smaller if it must.
     */
    fun place(box: Box, imageW: Double, imageH: Double, scale: Double, x: Double, y: Double, bounds: Box? = null): Box? {
        if (imageW <= 0 || imageH <= 0 || box.width <= 0) return null
        val fit = minOf(box.width / imageW, box.height / imageH)
        var w = imageW * fit * scale
        var h = imageH * fit * scale
        var left = box.left + x * box.width
        var top = box.bottom - h + y * box.height
        if (bounds == null) return Box(left, top, w, h)
        val shrink = minOf(1.0, bounds.width / w, bounds.height / h)
        if (shrink < 1.0) { w *= shrink; h *= shrink }
        left = minOf(maxOf(left, bounds.left), bounds.right - w)
        top = minOf(maxOf(top, bounds.top), bounds.bottom - h)
        return Box(left, top, w, h)
    }

    /** bandlogo.COMFORT_BOX: the logo's box at the top of Comfort View's artist column. */
    const val COMFORT_BOX = 110

    /** bandlogo.header_box: the logo's area in the artist photo's band, where the name sits, taller. */
    fun headerBox(width: Double, height: Double): Box {
        val box = minOf(BOX.toDouble(), height * 0.5)
        return Box(12.0, height - 8 - box, maxOf(0.0, width - 24), box)
    }

    /** LogoMark.paintEvent: the logo on a [width] x [height] artist photo, kept 4 inside its edges. */
    fun onPhoto(width: Double, height: Double, imageW: Double, imageH: Double, scale: Double, x: Double, y: Double): Box? =
        place(headerBox(width, height), imageW, imageH, scale, x, y, Box(4.0, 4.0, width - 8, height - 8))

    /**
     * LogoName._rect: the logo heading a [width] wide column (Comfort View's
     * artist column). Only the size and left-right count there; its top is 0.
     */
    fun inColumn(width: Double, imageW: Double, imageH: Double, scale: Double, x: Double): Box? {
        val c = COMFORT_BOX.toDouble()
        return place(Box(0.0, 0.0, width, c), imageW, imageH, scale, x, 0.0, Box(0.0, -c, width, 2 * c))?.copy(top = 0.0)
    }

    /**
     * bandlogo.outline_for: given the luminance (0-1) of the logo's solid
     * pixels and of what is behind it, the outline wanted: null when 40%
     * or more stand apart from the panel, else black for a light logo and
     * white for a dark one.
     */
    fun needsOutline(solid: List<Double>, behind: Double): Boolean? {
        if (solid.isEmpty()) return null
        val apart = solid.count { kotlin.math.abs(it - behind) >= 0.35 }
        if (apart.toDouble() / solid.size >= 0.4) return null
        return solid.average() > 0.5          // true: a dark outline (the logo is light)
    }

    fun luminance(r: Int, g: Int, b: Int): Double = 0.2126 * r / 255.0 + 0.7152 * g / 255.0 + 0.0722 * b / 255.0
}
