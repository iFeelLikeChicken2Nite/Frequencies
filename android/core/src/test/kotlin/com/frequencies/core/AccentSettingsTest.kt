package com.frequencies.core

import java.io.File
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test

/**
 * The accent choice, phone -> desktop -> phone. The desktop (build 144)
 * only knows accent_source "artwork", "desktop" and "fixed"; the phone
 * never writes anything else there.
 *
 * tools/check_accent_on_desktop.py reads build/accent-<mode>.json with the
 * desktop's own storage, checks the desktop can show its accent_source,
 * and writes the desktop's own backups back (build/desktop-accent-*.json);
 * desktopBackupsComeBack reads those when they are there.
 */
class AccentSettingsTest {
    private val build = File(System.getProperty("user.dir"), "build").apply { mkdirs() }

    private fun backup(settings: Settings) = Backup.export(
        Backup.Contents(emptyList(), settings, JsonObject(emptyMap()), JsonArray(emptyList()), JsonObject(emptyMap()), JsonObject(emptyMap())),
        exportedAt = 1.0,
    )

    private fun reread(settings: Settings) = Settings.from(FreqJson.parseToJsonElement(encodeJson(settings.toJson())).jsonObject)

    @Test
    fun theDefaultFollowsTheArtwork() {
        assertEquals(Settings.ACCENT_ARTWORK, Settings.from(null).accentMode)
    }

    @Test
    fun eachChoiceWritesOnlyWhatTheDesktopKnows() {
        val want = mapOf(
            Settings.ACCENT_ARTWORK to "artwork", Settings.ACCENT_MATERIAL_YOU to "desktop",
            Settings.ACCENT_FIXED to "fixed", Settings.ACCENT_THEME to "theme",   // the desktop's own "Match the theme" (build 151+)
        )
        val s = Settings.from(null)
        s.accentFixed = 0x3366FF
        for ((mode, shared) in want) {
            s.accentMode = mode
            assertEquals(mode, shared, s.string("accent_source"))
            assertEquals(mode, reread(s).accentMode)
            assertEquals("#3366ff", s.string("accent_fixed"))
            writeJsonAtomic(File(build, "accent-$mode.json"), backup(s))
        }
    }

    @Test
    fun theDesktopsChangeWins() {
        val s = Settings.from(null)
        s.accentMode = Settings.ACCENT_THEME
        assertEquals(Settings.ACCENT_THEME, reread(s).accentMode)
        // On the desktop: Accent color > A color I choose.
        s["accent_source"] = JsonPrimitive("fixed")
        s["accent_fixed"] = JsonPrimitive("#12ab34")
        assertEquals(Settings.ACCENT_FIXED, s.accentMode)
        assertEquals(0x12ab34, s.accentFixed)
        // Match my desktop is Material You on the phone.
        s["accent_source"] = JsonPrimitive("desktop")
        assertEquals(Settings.ACCENT_MATERIAL_YOU, s.accentMode)
        // A value from a later desktop (task 208's "theme") or none at all.
        s["accent_source"] = JsonPrimitive("theme")
        assertEquals(Settings.ACCENT_THEME, s.accentMode)
        s["accent_source"] = JsonPrimitive("something new")
        assertEquals(Settings.ACCENT_ARTWORK, s.accentMode)
        // "fixed" without a color is the artwork's.
        s.accentFixed = null
        s["accent_source"] = JsonPrimitive("fixed")
        assertEquals(Settings.ACCENT_ARTWORK, s.accentMode)
    }

    @Test
    fun desktopBackupsComeBack() {
        val files = Settings.ACCENT_MODES.map { File(build, "desktop-accent-$it.json") }
        assumeTrue("run tools/check_accent_on_desktop.py first", files.all { it.isFile })
        for ((mode, file) in Settings.ACCENT_MODES.zip(files)) {
            val contents = Backup.read(file.readText(), File(build, "accent-art"))
            assertEquals(file.name, mode, contents.settings.accentMode)
        }
        val changed = Backup.read(File(build, "desktop-accent-changed.json").readText(), File(build, "accent-art"))
        assertEquals(Settings.ACCENT_FIXED, changed.settings.accentMode)
        assertEquals(0xc0ffee, changed.settings.accentFixed)
    }
}
