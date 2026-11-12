package com.frequencies.core

import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/**
 * The desktop's shell themes (src/themes.py), read from themes.json,
 * which tools/export_themes.py writes from the desktop's own file: never
 * edit it by hand. Same keys, names and order as the desktop's Theme list;
 * a saved key that isn't there any more falls back to the default (OG).
 */
class ShellTheme(val key: String, val name: String, val note: String, val light: Boolean, val tokens: Map<String, Int>) {
    fun token(name: String): Int = tokens.getValue(name)
    val accent: Int get() = token("accent")
}

object ShellThemes {
    val all: List<ShellTheme> by lazy {
        val root = FreqJson.parseToJsonElement(
            ShellThemes::class.java.getResourceAsStream("themes.json")!!.use { it.readBytes().toString(Charsets.UTF_8) },
        ).jsonObject
        root.getValue("themes").jsonArray.map { e ->
            val o = e.jsonObject
            ShellTheme(
                key = o.getValue("key").jsonPrimitive.content,
                name = o.getValue("name").jsonPrimitive.content,
                note = o.getValue("note").jsonPrimitive.content,
                light = o.getValue("light").jsonPrimitive.boolean,
                tokens = o.getValue("tokens").jsonObject.mapValues { (_, v) -> parseHex(v.jsonPrimitive.content) },
            )
        }
    }

    val DEFAULT: String by lazy {
        FreqJson.parseToJsonElement(
            ShellThemes::class.java.getResourceAsStream("themes.json")!!.use { it.readBytes().toString(Charsets.UTF_8) },
        ).jsonObject.getValue("default").jsonPrimitive.content
    }

    /** themes.tokens(key): the theme, or the default's for a name not here. */
    fun find(key: String?): ShellTheme = all.firstOrNull { it.key == key } ?: all.first { it.key == DEFAULT }

    /** The light theme a dark-themed phone in light mode shows (Flashbang). */
    const val LIGHT_DEFAULT = "paper"

    fun parseHex(s: String): Int = s.trim().removePrefix("#").toInt(16) and 0xFFFFFF
}

/**
 * A theme's tokens laid onto Material 3's color roles, every text color
 * checked for WCAG AA against what it sits on (ThemeSchemeTest goes
 * through all themes with many accents). Surface levels come from bg0-bg3
 * (canvas, panel, hover, pressed: each further from the canvas, so light
 * themes darken going up), text from text/text2/text3, primary from the
 * accent, error from danger. Colors are 0xRRGGBB.
 */
data class ThemeScheme(
    val light: Boolean,
    val primary: Int, val onPrimary: Int, val primaryContainer: Int, val onPrimaryContainer: Int, val inversePrimary: Int,
    val secondary: Int, val onSecondary: Int, val secondaryContainer: Int, val onSecondaryContainer: Int,
    val tertiary: Int, val onTertiary: Int, val tertiaryContainer: Int, val onTertiaryContainer: Int,
    val background: Int, val onBackground: Int,
    val surface: Int, val onSurface: Int, val surfaceVariant: Int, val onSurfaceVariant: Int,
    val surfaceTint: Int, val inverseSurface: Int, val inverseOnSurface: Int,
    val error: Int, val onError: Int, val errorContainer: Int, val onErrorContainer: Int,
    val outline: Int, val outlineVariant: Int, val scrim: Int,
    val surfaceBright: Int, val surfaceDim: Int,
    val surfaceContainerLowest: Int, val surfaceContainerLow: Int, val surfaceContainer: Int,
    val surfaceContainerHigh: Int, val surfaceContainerHighest: Int,
    // The desktop's own tokens Material has no role for.
    val playing: Int, val down: Int, val barBg: Int, val onBar: Int, val barEdge: Int, val line: Int,
    // The overhaul's (VO1): faint text (a frequency that isn't answering),
    // secondary text and the accent as they read on the player bar.
    val text3: Int = 0, val onBar2: Int = 0, val barAccent: Int = 0,
) {
    companion object {
        const val TEXT = 4.5      // WCAG AA, normal text
        const val GRAPHIC = 3.0   // WCAG AA, icons, borders, large text

        /** [accent] is the accent to show (already picked: artwork, theme, Material You or custom). */
        fun of(theme: ShellTheme, accent: Int): ThemeScheme {
            val t = theme.tokens
            val bg0 = t.getValue("bg0"); val bg1 = t.getValue("bg1"); val bg2 = t.getValue("bg2"); val bg3 = t.getValue("bg3")
            val texts = listOf(bg0, bg1, bg2)            // where text sits: canvas, panels, raised rows
            val text = readable(t.getValue("text"), texts + bg3, TEXT)
            val text2 = readable(t.getValue("text2"), texts + bg3, TEXT)
            val primary = readable(accent, texts, TEXT)
            val primaryContainer = mix(primary, bg2, 0.30)
            val secondary = readable(mix(accent, t.getValue("text2"), 0.5), texts, TEXT)
            val secondaryContainer = mix(accent, bg2, 0.22)
            val playing = readable(t.getValue("playing"), texts, TEXT)
            val tertiaryContainer = mix(playing, bg2, 0.25)
            val errorContainer = mix(t.getValue("danger"), bg2, 0.25)
            val danger = readable(t.getValue("danger"), texts, TEXT)
            val barBg = t.getValue("bar_bg")
            val inverseSurface = t.getValue("text")
            return ThemeScheme(
                light = theme.light,
                primary = primary, onPrimary = onColor(primary),
                primaryContainer = primaryContainer, onPrimaryContainer = readable(text, listOf(primaryContainer), TEXT),
                inversePrimary = readable(accent, listOf(inverseSurface), GRAPHIC),
                secondary = secondary, onSecondary = onColor(secondary),
                secondaryContainer = secondaryContainer, onSecondaryContainer = readable(text, listOf(secondaryContainer), TEXT),
                tertiary = playing, onTertiary = onColor(playing),
                tertiaryContainer = tertiaryContainer, onTertiaryContainer = readable(text, listOf(tertiaryContainer), TEXT),
                background = bg0, onBackground = text,
                surface = bg0, onSurface = text, surfaceVariant = bg2, onSurfaceVariant = text2,
                surfaceTint = primary,
                inverseSurface = inverseSurface, inverseOnSurface = readable(bg0, listOf(inverseSurface), TEXT),
                error = danger, onError = onColor(danger),
                errorContainer = errorContainer, onErrorContainer = readable(text, listOf(errorContainer), TEXT),
                outline = readable(t.getValue("text3"), texts, GRAPHIC), outlineVariant = t.getValue("line"),
                scrim = 0x000000,
                surfaceBright = bg3, surfaceDim = bg0,
                surfaceContainerLowest = bg0, surfaceContainerLow = t.getValue("row_alt"), surfaceContainer = bg1,
                surfaceContainerHigh = bg2, surfaceContainerHighest = bg3,
                playing = playing, down = readable(t.getValue("down"), texts, TEXT),
                barBg = barBg, onBar = readable(text, listOf(barBg), TEXT), barEdge = t.getValue("bar_edge"), line = t.getValue("line"),
                text3 = readable(t.getValue("text3"), texts, GRAPHIC),
                onBar2 = readable(text2, listOf(barBg), TEXT),
                barAccent = readable(primary, listOf(barBg), GRAPHIC),
            )
        }

        // --- color helpers ------------------------------------------------

        fun luminance(c: Int): Double {
            fun ch(v: Int): Double { val s = v / 255.0; return if (s <= 0.04045) s / 12.92 else ((s + 0.055) / 1.055).pow(2.4) }
            return 0.2126 * ch((c shr 16) and 0xFF) + 0.7152 * ch((c shr 8) and 0xFF) + 0.0722 * ch(c and 0xFF)
        }

        fun contrast(a: Int, b: Int): Double {
            val la = luminance(a); val lb = luminance(b)
            return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
        }

        /** Black-ish or white text on [c], whichever reads better (always at least 4.58:1). */
        fun onColor(c: Int): Int = if (contrast(c, 0xFFFFFF) >= contrast(c, 0x14110B)) 0xFFFFFF else 0x14110B

        fun mix(a: Int, b: Int, t: Double): Int {
            fun ch(s: Int) = (((a shr s) and 0xFF) * t + ((b shr s) and 0xFF) * (1 - t) + 0.5).toInt().coerceIn(0, 255)
            return (ch(16) shl 16) or (ch(8) shl 8) or ch(0)
        }

        /**
         * [fg] as it is when it reads on every one of [backgrounds]; else the
         * nearest lighter or darker shade of it that does (same hue), else
         * white or near-black.
         */
        fun readable(fg: Int, backgrounds: List<Int>, ratio: Double): Int {
            fun ok(c: Int) = backgrounds.all { contrast(c, it) >= ratio }
            if (ok(fg)) return fg
            for (step in 1..100) {
                val t = step / 100.0
                val lighter = mix(0xFFFFFF, fg, t)
                val darker = mix(0x000000, fg, t)
                val candidates = listOf(lighter, darker).filter(::ok)
                if (candidates.isNotEmpty()) return candidates.first()
            }
            return listOf(0xFFFFFF, 0x000000).maxBy { c -> backgrounds.minOf { contrast(c, it) } }
        }
    }
}
