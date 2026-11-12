package com.frequencies.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Every shell theme, with its own accent and with accents from all round
 * the color wheel (the artwork can pick any of them), reads at WCAG AA:
 * 4.5:1 for text, 3:1 for icons and borders, on every surface it sits on.
 */
class ThemeSchemeTest {
    private val accents: List<Int> by lazy {
        val wheel = (0 until 360 step 15).flatMap { h -> listOf(120, 150, 175).map { l -> ArtAccent.fromHsl(h, 200, l) } }
        wheel + listOf(0x000000, 0xFFFFFF, 0x808080, 0xFFFF00, 0x0000A0) + ShellThemes.all.map { it.accent }
    }

    @Test
    fun theTableIsTheDesktops() {
        val all = ShellThemes.all
        assertEquals(22, all.size)
        assertEquals("midnight", ShellThemes.DEFAULT)
        assertEquals("OG", all.first().name)
        assertEquals(all.size, all.map { it.key }.toSet().size)
        val keys = listOf("bg0", "bg1", "bg2", "bg3", "line", "row_alt", "bar_bg", "bar_edge",
            "text", "text2", "text3", "down", "playing", "danger", "accent")
        for (t in all) assertEquals(t.key, keys.toSet(), t.tokens.keys)
        assertEquals(listOf("Flashbang", "Cassette"), all.filter { it.light }.map { it.name })
        assertEquals("OG", ShellThemes.find("graphite").name)     // removed in build 130
        assertEquals("OG", ShellThemes.find(null).name)
        assertEquals("Tron", ShellThemes.find("tron").name)
    }

    private fun check(what: String, fg: Int, bgs: List<Int>, ratio: Double) {
        for (bg in bgs) {
            val c = ThemeScheme.contrast(fg, bg)
            assertTrue("$what #%06x on #%06x is %.2f:1".format(fg, bg, c), c >= ratio)
        }
    }

    @Test
    fun everyThemeReadsWithEveryAccent() {
        var schemes = 0
        for (theme in ShellThemes.all) for (accent in accents) {
            val s = ThemeScheme.of(theme, accent)
            val n = "${theme.name} / accent #%06x:".format(accent)
            val surfaces = listOf(s.surface, s.surfaceContainerLow, s.surfaceContainer, s.surfaceContainerHigh)
            check("$n onSurface", s.onSurface, surfaces + s.surfaceContainerHighest, ThemeScheme.TEXT)
            check("$n onSurfaceVariant", s.onSurfaceVariant, surfaces + s.surfaceContainerHighest, ThemeScheme.TEXT)
            check("$n primary", s.primary, surfaces, ThemeScheme.TEXT)
            check("$n onPrimary", s.onPrimary, listOf(s.primary), ThemeScheme.TEXT)
            check("$n onPrimaryContainer", s.onPrimaryContainer, listOf(s.primaryContainer), ThemeScheme.TEXT)
            check("$n secondary", s.secondary, surfaces, ThemeScheme.TEXT)
            check("$n onSecondary", s.onSecondary, listOf(s.secondary), ThemeScheme.TEXT)
            check("$n onSecondaryContainer", s.onSecondaryContainer, listOf(s.secondaryContainer), ThemeScheme.TEXT)
            check("$n tertiary", s.tertiary, surfaces, ThemeScheme.TEXT)
            check("$n onTertiary", s.onTertiary, listOf(s.tertiary), ThemeScheme.TEXT)
            check("$n onTertiaryContainer", s.onTertiaryContainer, listOf(s.tertiaryContainer), ThemeScheme.TEXT)
            check("$n error", s.error, surfaces, ThemeScheme.TEXT)
            check("$n onError", s.onError, listOf(s.error), ThemeScheme.TEXT)
            check("$n onErrorContainer", s.onErrorContainer, listOf(s.errorContainer), ThemeScheme.TEXT)
            check("$n inverseOnSurface", s.inverseOnSurface, listOf(s.inverseSurface), ThemeScheme.TEXT)
            check("$n inversePrimary", s.inversePrimary, listOf(s.inverseSurface), ThemeScheme.GRAPHIC)
            check("$n outline", s.outline, surfaces, ThemeScheme.GRAPHIC)
            check("$n playing", s.playing, surfaces, ThemeScheme.TEXT)
            check("$n down", s.down, surfaces, ThemeScheme.TEXT)
            check("$n onBar", s.onBar, listOf(s.barBg), ThemeScheme.TEXT)
            schemes++
        }
        assertEquals(22 * accents.size, schemes)
    }

    @Test
    fun aThemesOwnColorsStayWhenTheyRead() {
        // OG's tokens already read: nothing is moved.
        val og = ShellThemes.find("midnight")
        val s = ThemeScheme.of(og, og.accent)
        assertEquals(0x131315, s.surface)
        assertEquals(0x1a1a1d, s.surfaceContainer)
        assertEquals(0xececee, s.onSurface)
        assertEquals(0x9a9aa2, s.onSurfaceVariant)
        assertEquals(0xd99a2b, s.primary)
        // OG's danger red is a touch dark for small text on a raised row:
        // a lighter shade of the same red.
        assertEquals(ArtAccent.hsl(0xe2564b).h.toDouble(), ArtAccent.hsl(s.error).h.toDouble(), 2.0)
        assertTrue(ArtAccent.distance(0xe2564b, s.error) < 40)
        assertFalse(s.light)
        assertTrue(ThemeScheme.of(ShellThemes.find("paper"), og.accent).light)
    }

    @Test
    fun darkAndLightThemesFollowTheMode() {
        val s = Settings.from(null)
        assertEquals("OG", s.shownTheme(systemDark = true).name)
        assertEquals("Flashbang", s.shownTheme(systemDark = false).name)
        s.androidTheme = "dark"
        assertEquals("OG", s.shownTheme(systemDark = false).name)
        s.androidTheme = "light"
        assertEquals("Flashbang", s.shownTheme(systemDark = true).name)
        // A dark theme picked (or synced as "theme") is the dark one; the light one stays.
        s.shellTheme = "tron"
        assertEquals("Flashbang", s.shownTheme(systemDark = false).name)
        s.androidTheme = "system"
        assertEquals("Tron", s.shownTheme(systemDark = true).name)
        assertEquals("Flashbang", s.shownTheme(systemDark = false).name)
        // The light one chosen: "theme" (shared with the desktop) keeps the dark one.
        s.lightTheme = "cassette"
        assertEquals("Cassette", s.shownTheme(systemDark = false).name)
        assertEquals("tron", s.shellTheme)
        // The pickers only take their own kind.
        s.lightTheme = "outrun"
        assertEquals("cassette", s.lightTheme)
        s.darkTheme = "paper"
        assertEquals("tron", s.darkTheme)
        s.darkTheme = "outrun"
        assertEquals("outrun", s.shellTheme)
        // A light "theme" from the desktop fills the light slot; the dark one is remembered.
        s.shellTheme = "paper"
        assertEquals("Flashbang", s.shownTheme(systemDark = false).name)
        assertEquals("Outrun", s.shownTheme(systemDark = true).name)
        s.darkTheme = "tron"
        assertEquals("paper", s.shellTheme)
        assertEquals("Tron", s.shownTheme(systemDark = true).name)
        s.shellTheme = "oxide"                                    // gone since build 130
        assertEquals("midnight", s.shellTheme)
    }
}
