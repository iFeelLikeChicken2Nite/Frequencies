package com.frequencies.core

import java.io.File
import java.nio.file.Files
import java.util.Base64
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Desktop -> phone -> desktop with a backup the desktop wrote itself.
 * The phone's backup is also left in build/phone-backup.json, where
 * tools/check_backup_on_desktop.py reads it back with the desktop's own
 * read_backup.
 */
class BackupTest {
    private fun fixture(name: String): String =
        BackupTest::class.java.getResourceAsStream("/desktop/$name")!!.use { it.readBytes().toString(Charsets.UTF_8) }

    @Test
    fun desktopToPhoneToDesktop() {
        val text = fixture("desktop-backup.json")
        val desktop = FreqJson.parseToJsonElement(text).jsonObject
        val phoneDir = Files.createTempDirectory("phone").toFile()
        val artDir = File(phoneDir, "artwork")

        val contents = Backup.read(text, artDir)
        assertEquals(13, contents.stations.size)
        assertEquals(7L, contents.stations[0].playCount)
        assertEquals(listOf("ambient", "Chill"), contents.stations[0].tags)
        // Paths now point at this device's copies, holding the same bytes.
        val deskArt = desktop["artwork"]!!.jsonObject
        for (s in contents.stations) {
            assertTrue(s.customArtPath, s.customArtPath.startsWith(artDir.absolutePath))
            val want = Base64.getDecoder().decode(deskArt[s.id]!!.jsonObject["data"].str())
            assertTrue(File(s.customArtPath).readBytes().contentEquals(want))
        }
        assertEquals("#d96f9a", contents.settings.tagColor("80s"))
        assertEquals(JsonPrimitive(1), contents.health[contents.stations[0].id]!!.jsonObject["fails"])

        val out = Backup.export(contents, exportedAt = 1.0)
        // Same stations apart from where the pictures live.
        val a = desktop["stations"]!!.jsonArray
        val b = out["stations"]!!.jsonArray
        assertEquals(a.size, b.size)
        for (i in a.indices) {
            assertEquals(JsonObject(a[i].jsonObject - "custom_art_path"), JsonObject(b[i].jsonObject - "custom_art_path"))
        }
        assertEquals(deskArt.mapValues { it.value.jsonObject["data"] }, out["artwork"]!!.jsonObject.mapValues { it.value.jsonObject["data"] })
        for (key in listOf("health", "history", "artist_overrides", "station_overrides", "format", "version")) {
            assertEquals(key, desktop[key], out[key])
        }
        // Settings too, apart from the removed ones (storage.REMOVED_SETTINGS: "Custom station frequency").
        assertEquals(JsonObject(desktop["settings"]!!.jsonObject - Settings.REMOVED.toSet()), out["settings"])
        val build = File(System.getProperty("user.dir"), "build").apply { mkdirs() }
        writeJsonAtomic(File(build, "phone-backup.json"), out)
    }

    @Test(expected = Backup.BackupError::class)
    fun notABackup() {
        Backup.read("{\"format\": \"something\"}", Files.createTempDirectory("x").toFile())
    }
}
