package com.frequencies.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The overhaul's look in core (VO1, spec section 8): which Settings tab
 * each setting lands on, and that the desktop tokens the new screens use
 * (rows, player bar, Now Playing's wash) read in all 22 themes.
 */
class LookTest {
    // --- Settings tabs ------------------------------------------------

    @Test
    fun theTabsAreTheDesktops() {
        assertEquals(
            listOf("Appearance", "Sound", "Frequencies", "Lyrics and artists", "Connections", "Library and tools"),
            SettingsTabs.ALL,
        )
        assertEquals(SettingsTabs.ALL.toSet(), SettingsTabs.KEYS.keys)
        assertEquals(4, SettingsTabs.indexOf(SettingsTabs.ACCOUNTS))
        assertEquals(0, SettingsTabs.indexOf("Keyboard"))      // no Keyboard tab on a phone
        assertEquals(0, SettingsTabs.indexOf(null))
    }

    @Test
    fun everyKeyIsOnExactlyOneTab() {
        val all = SettingsTabs.KEYS.values.flatten()
        assertEquals("a key listed on two tabs: $all", all.size, all.toSet().size)
        for (key in all) assertNotNull(key, SettingsTabs.tabOf(key))
    }

    /** Every setting the Settings screen writes, read from its source, lives on one tab. */
    @Test
    fun everySettingTheScreenWritesHasATab() {
        val ui = File("../app/src/main/kotlin/com/frequencies/app/ui")
        if (!ui.isDirectory) return      // core on its own (no app sources beside it)
        val files = listOf("SettingsScreen.kt", "AppearanceSettings.kt").map { File(ui, it).readText() }
        val keys = sortedSetOf<String>()
        for (text in files) {
            // it["key"] = …, and the sleep timer's choices (it[when (what) { … -> "sleep_…" }]).
            Regex("""it\["([a-z0-9_]+)"]""").findAll(text).forEach { keys += it.groupValues[1] }
            Regex("""-> "(sleep_[a-z_]+)"""").findAll(text).forEach { keys += it.groupValues[1] }
        }
        // The typed setters Appearance uses (Settings.kt's properties).
        val setters = mapOf("it.shellTheme" to "theme", "it.androidTheme" to Settings.ANDROID_THEME,
            "it.darkTheme" to Settings.ANDROID_THEME_DARK, "it.lightTheme" to Settings.ANDROID_THEME_LIGHT,
            "it.accentMode" to Settings.ANDROID_ACCENT, "it.accentFixed" to "accent_fixed")
        for ((setter, key) in setters) if (files.any { setter in it }) keys += key
        assertTrue("found too few keys: $keys", keys.size >= 15)
        for (key in keys) {
            val tabs = SettingsTabs.KEYS.filterValues { key in it }.keys
            assertEquals("$key is on $tabs", 1, tabs.size)
        }
        // And each page writes only its own tab's keys: Appearance's file holds no Sound key, and so on.
        val appearance = files[1]
        for (key in SettingsTabs.KEYS.getValue(SettingsTabs.SOUND)) assertTrue(key, "\"$key\"" !in appearance)
    }

    // --- Contrast -----------------------------------------------------

    private fun check(what: String, fg: Int, bgs: List<Int>, ratio: Double) {
        for (bg in bgs) {
            val c = ThemeScheme.contrast(fg, bg)
            assertTrue("$what #%06x on #%06x is %.2f:1".format(fg, bg, c), c >= ratio)
        }
    }

    private val accents: List<Int> by lazy {
        val wheel = (0 until 360 step 30).flatMap { h -> listOf(120, 175).map { l -> ArtAccent.fromHsl(h, 200, l) } }
        wheel + listOf(0x000000, 0xFFFFFF, 0xD81B1B) + ShellThemes.all.map { it.accent }
    }

    @Test
    fun theNewScreensReadInEveryTheme() {
        var n = 0
        for (theme in ShellThemes.all) for (accent in accents) {
            val s = ThemeScheme.of(theme, accent)
            val name = "${theme.name} / #%06x:".format(accent)
            // Saved rows: canvas, stripes, the lit playing row.
            val rows = listOf(s.surface, s.surfaceContainerLow, s.surfaceContainerHigh)
            val panels = listOf(s.surface, s.surfaceContainer, s.barBg)
            check("$name text", s.onSurface, rows + s.surfaceContainer, ThemeScheme.TEXT)
            check("$name text2", s.onSurfaceVariant, rows + s.surfaceContainer, ThemeScheme.TEXT)
            check("$name text3 (not answering)", s.text3, rows, ThemeScheme.GRAPHIC)
            check("$name playing song line", s.playing, rows, ThemeScheme.TEXT)
            check("$name not answering line", s.down, rows, ThemeScheme.TEXT)
            // Player bar.
            check("$name bar text", s.onBar, listOf(s.barBg), ThemeScheme.TEXT)
            check("$name bar text2", s.onBar2, listOf(s.barBg), ThemeScheme.TEXT)
            check("$name bar accent", s.barAccent, listOf(s.barBg), ThemeScheme.GRAPHIC)
            // Accent buttons and chips/tabs on bg0/bg1.
            check("$name onPrimary", s.onPrimary, listOf(s.primary), ThemeScheme.TEXT)
            check("$name accent", s.primary, panels.dropLast(1), ThemeScheme.GRAPHIC)
            // Now Playing's wash: every part of the gradient.
            val w = ComfortWash.of(theme, s)
            val wash = listOf(w.glow, w.top, w.bottom)
            check("$name wash text", w.text, wash, ThemeScheme.TEXT)
            check("$name wash text2", w.text2, wash, ThemeScheme.TEXT)
            check("$name wash lyric", w.accent, wash, ThemeScheme.TEXT)
            n++
        }
        assertEquals(22 * accents.size, n)
    }

    @Test
    fun theWashIsTheSpecs() {
        val og = ShellThemes.find("midnight")
        val s = ThemeScheme.of(og, og.accent)
        val w = ComfortWash.of(og, s)
        assertEquals(ThemeScheme.mix(s.primary, s.surface, 0.32), w.glow)
        assertEquals(ThemeScheme.mix(s.playing, s.surface, 0.14), w.top)
        assertEquals(s.surface, w.bottom)
        val flash = ShellThemes.all.first { it.name == "Flashbang" }
        val fs = ThemeScheme.of(flash, flash.accent)
        assertEquals(ThemeScheme.mix(fs.primary, fs.surface, 0.22), ComfortWash.of(flash, fs).glow)
    }
}
