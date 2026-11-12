package com.frequencies.core

/**
 * How a tag pill looks, the desktop's numbers (build 167): the tag's own
 * color at a given alpha (0-255) for the fill, and the text in the tag's
 * color or the theme's text. One table so every place a tag shows on the
 * phone looks like its desktop twin.
 *
 * - [ROW]: a Saved row's pills (stationlist.StationItemDelegate._draw_tags):
 *   18 px tall, fully round, fill at 34, no border, text in the tag's
 *   color at 10 px, 9 px padding each side, 6 px apart, at most two,
 *   right-aligned.
 * - [OFF] / [INCLUDE] / [EXCLUDE]: the sidebar and filter chips
 *   (shell._paint_tag + theme's QPushButton[pill]): 10 px corners, 20 px
 *   tall, 2 px 10 px padding, 11 px text. Off: fill 34, text in the tag's
 *   color. Include: fill 80, the theme's text. Exclude: fill 18, the
 *   theme's faint text, struck through.
 * - [PICK] / [PICKED]: the tag pickers in dialogs (widgets.draw_tag_pill):
 *   fill 52 with a 1 px border at 150 and text in the tag's color; picked:
 *   fill 230, border 255, text #101010.
 */
object TagLook {
    enum class Text { TAG, THEME, FAINT, DARK }

    data class Style(
        val fillAlpha: Int,
        val borderAlpha: Int,
        val text: Text,
        val heightPx: Int,
        val radiusPx: Int,
        val padPx: Int,
        val fontPx: Int,
        val struck: Boolean = false,
        val bold: Boolean = false,
    )

    val ROW = Style(fillAlpha = 34, borderAlpha = 0, text = Text.TAG, heightPx = 18, radiusPx = 9, padPx = 9, fontPx = 10)
    val OFF = Style(fillAlpha = 34, borderAlpha = 0, text = Text.TAG, heightPx = 20, radiusPx = 10, padPx = 10, fontPx = 11)
    val INCLUDE = OFF.copy(fillAlpha = 80, text = Text.THEME)
    val EXCLUDE = OFF.copy(fillAlpha = 18, text = Text.FAINT, struck = true)
    val PICK = Style(fillAlpha = 52, borderAlpha = 150, text = Text.TAG, heightPx = 20, radiusPx = 10, padPx = 10, fontPx = 11)
    val PICKED = PICK.copy(fillAlpha = 230, borderAlpha = 255, text = Text.DARK)
    /**
     * A tag filter chip that is on, on the phone (user, build 188: on and
     * off were too hard to tell apart at the desktop's 80 vs 34 tint):
     * solid in the tag's color with dark bold text.
     */
    val FILTER_ON = PICKED.copy(bold = true)

    /** A tag filter chip's look for its state: off (light tint), on ([FILTER_ON]) or hidden (faint, struck). */
    fun filterStyle(state: Int): Style = when (state) {
        Search.INCLUDED -> FILTER_ON
        Search.EXCLUDED -> EXCLUDE
        else -> OFF
    }

    /** The pressed (hover) fill of a chip in each state (shell._paint_tag's hover rule). */
    fun pressedAlpha(style: Style): Int = when (style) {
        INCLUDE -> 108
        FILTER_ON -> 200
        EXCLUDE -> 44
        else -> 62
    }

    /** At most this many pills on a Saved row. */
    const val ROW_MAX = 2
    const val ROW_GAP_PX = 6

    /** The dark text on a picked pill. */
    const val DARK = 0xFF101010.toInt()

    /** A tag color as ARGB with [alpha] (0-255); an unreadable color is the palette's first. */
    fun argb(hex: String, alpha: Int): Int {
        val rgb = parse(hex) ?: parse(Settings.TAG_PALETTE[0])!!
        return (alpha.coerceIn(0, 255) shl 24) or rgb
    }

    /** "#rrggbb" (or "rrggbb", "#rgb") as 0xRRGGBB, or null. */
    fun parse(hex: String?): Int? {
        var h = hex.orEmpty().trim().removePrefix("#")
        if (h.length == 3) h = h.map { "$it$it" }.joinToString("")
        if (h.length != 6 || h.any { it.lowercaseChar() !in "0123456789abcdef" }) return null
        return h.toInt(16)
    }

    /**
     * The color each tag shows in, as the desktop would: its saved color
     * (an old Tags-dialog color moved to the app's palette); a tag with
     * none yet gets the next palette color, in A-to-Z order, the way
     * storage.tag_color hands them out. Nothing is saved: the computer's
     * own choices arrive with sync (`tag_colors`), and a tag made on the
     * phone saves its color when it is made.
     */
    fun colors(tags: List<String>, settings: Settings): Map<String, String> {
        val saved = (settings.data["tag_colors"] as? kotlinx.serialization.json.JsonObject)?.keys.orEmpty()
        var next = saved.size
        val out = LinkedHashMap<String, String>()
        for (tag in tags.sortedBy { it.lowercase() }) {
            val key = tag.trim().lowercase()
            if (key in out) continue
            out[key] = if (key in saved) settings.tagColor(tag) else Settings.TAG_PALETTE[next++ % Settings.TAG_PALETTE.size]
        }
        return out
    }

    fun colorOf(tag: String, colors: Map<String, String>, settings: Settings): String =
        colors[tag.trim().lowercase()] ?: settings.tagColor(tag)
}
