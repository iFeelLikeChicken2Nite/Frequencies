package com.frequencies.core

import java.io.File
import java.nio.file.Files
import java.util.Base64
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Checked against files the desktop (build 131) wrote itself: src/test/resources/desktop. */
class LibraryTest {
    private fun fixture(name: String): String =
        LibraryTest::class.java.getResourceAsStream("/desktop/$name")!!.use { it.readBytes().toString(Charsets.UTF_8) }

    private fun tempDir(): File = Files.createTempDirectory("freq").toFile()

    @Test
    fun freshProfileMatchesTheDesktops() {
        val dir = tempDir()
        val files = LibraryFiles(dir)
        val stations = files.loadStations()
        assertTrue(files.stationsFile.exists())
        val desktop = FreqJson.parseToJsonElement(fixture("fresh-stations.json")).jsonArray.map { it.jsonObject }
        assertEquals(19, stations.size)   // the 13, then desktop 174's six YouTube stations
        assertEquals(desktop.size, stations.size)
        // These differ by nature, or on purpose (task 182: tracking bits taken out of two addresses).
        val differ = setOf("id", "added_at", "custom_art_path", "url")
        val cleaned = mapOf(
            "New Age Radio" to "http://a7.radioheart.ru:8044/newageradio?_ga=2.255684622.769977034.1646991967-126118534.1646991967",
            "Dare-FM" to "http://hydra.cdnstream.com/1552_128?listenerId=874e5aaea897e6a11bc60184f95f9946&aw_0_1st.playerid=esPlayer&aw_0_1st.skey=1689285807",
        )
        val featured = Featured.readList(Defaults.defaultStationsText())
        for ((i, phone) in stations.withIndex()) {
            val desk = desktop[i]
            assertEquals("same keys for ${phone.displayName}", desk.keys, phone.data.keys)
            for (key in desk.keys - differ) assertEquals("${phone.displayName} $key", desk[key], phone[key])
            assertEquals(cleaned[phone.name] ?: desk["url"].str(), phone.url)
            assertTrue(phone.tags.isEmpty())
            assertEquals(0L, phone.playCount)
            // Full-size art, the very bytes the desktop ships.
            val art = File(phone.customArtPath)
            assertTrue(art.isFile && art.parentFile == files.artDir)
            val shipped = Base64.getDecoder().decode(featured[i]["art_data"].str())
            assertTrue(art.readBytes().contentEquals(shipped))
            assertFalse(ImageSize.isSmall(art))
            assertFalse(phone.data.containsKey("art_from_code"))
        }
        assertEquals("New Age Radio", stations[0].displayName)
        assertEquals("Jazz 88.5 FM - KBEM", stations[12].displayName)
        // Desktop build 174: six YouTube stations after the 13, in its order, same addresses (twins in sync).
        assertEquals(
            listOf("AUX AUTHORITY", "SYNTH HAPPENS", "NOISE COMPLAINT", "FRONT ROW", "CONTEXT CUT", "COLLISION COURSE").map { "Frequencies $it" },
            stations.drop(13).map { it.displayName },
        )
        for (s in stations.drop(13)) {
            assertEquals("youtube", s["kind"].str())
            assertEquals("youtube://" + s["playlist_url"].str(), s.url)
            assertTrue(s.url.contains("&si="))
            assertEquals(false, s["ad_override"]?.let { truthy(it) })
        }
        assertEquals(3.8, stations[16]["buffer_seconds"].num()!!, 1e-9)
        assertEquals(false, truthy(stations[17]["lyrics_enabled"]))
        assertEquals(false, truthy(stations[18]["shazam_auto"]))
        // Manual order keeps the shipped order.
        assertEquals(stations.map { it.id }, sortStations(stations, "manual").map { it.id })
    }

    @Test
    fun stationsRoundTripUntouched() {
        val dir = tempDir()
        val text = fixture("used-stations.json")
        File(dir, "stations.json").writeText(text)
        val files = LibraryFiles(dir)
        val stations = files.loadStations()
        assertEquals(listOf("ambient", "Chill"), stations[0].tags)
        assertEquals(7L, stations[0].playCount)
        // Desktop-only and unknown keys survive.
        assertEquals(JsonPrimitive("/home/user/Music"), stations[2]["local_path"])
        assertTrue(stations[2]["future_key"] is JsonObject)
        files.saveStations(stations)
        val back = files.stationsFile.readText()
        assertEquals(FreqJson.parseToJsonElement(text), FreqJson.parseToJsonElement(back))
        assertEquals("the text is the same too", text.trimEnd(), back.trimEnd())
    }

    @Test
    fun settingsRoundTripKeepsDesktopKeys() {
        val dir = tempDir()
        val text = fixture("settings.json")
        File(dir, "settings.json").writeText(text)
        val files = LibraryFiles(dir)
        val settings = files.loadSettings()
        assertEquals("#d96f9a", settings.tagColor("80s"))
        assertEquals("#4ebf9e", settings.tagColor("Ambient"))
        assertTrue(settings.staticOn)
        assertEquals(100, settings.staticVolume)
        files.saveSettings(settings)
        // Every desktop key comes back as it was, apart from the removed ones (desktop storage.REMOVED_SETTINGS).
        assertEquals(JsonObject(FreqJson.parseToJsonElement(text).jsonObject - Settings.REMOVED.toSet()),
            FreqJson.parseToJsonElement(files.settingsFile.readText()))
        // Android's own keys are added beside the desktop's, nothing else changes.
        settings.androidTheme = "dark"
        files.saveSettings(settings)
        val saved = FreqJson.parseToJsonElement(files.settingsFile.readText()).jsonObject
        assertEquals(JsonPrimitive("/home/user/skins/FreqAmp.wsz"), saved["skin_path"])
        assertEquals(JsonPrimitive("dark"), saved["android_theme"])
        val original = FreqJson.parseToJsonElement(text).jsonObject
        assertEquals(JsonObject(original - Settings.REMOVED.toSet()), JsonObject(saved - "android_theme"))
    }

    @Test
    fun newTagGetsThePaletteColor() {
        val settings = Settings.from(null)
        assertEquals(Settings.TAG_PALETTE[0], settings.tagColor("jazz", assign = true))
        assertEquals(Settings.TAG_PALETTE[1], settings.tagColor("rock", assign = true))
        assertEquals(Settings.TAG_PALETTE[0], settings.tagColor("JAZZ"))
    }

    @Test
    fun normalizeFillsAndCleans() {
        val s = Station(JsonObject(mapOf(
            "name" to JsonPrimitive("X"),
            "url" to JsonPrimitive("http://x"),
            "gain_db" to JsonPrimitive(99),
            "tags" to JsonArray(listOf(JsonPrimitive(" a "), JsonPrimitive("A"), JsonPrimitive("b"))),
            "transcribe" to JsonPrimitive(true),
            "is_new" to JsonPrimitive(1),
        )))
        Station.normalize(s, 100.0) { "new-id" }
        assertEquals("new-id", s.id)
        assertEquals(20.0, s.gainDb, 0.0)
        assertEquals(listOf("a", "b"), s.tags)
        assertFalse(s.data.containsKey("transcribe"))
        assertEquals(JsonPrimitive(true), s["is_new"])
        assertEquals(Defaults.station.keys, s.data.keys - setOf("id", "name", "url"))
        s.recordPlay(1760000000.25)
        assertEquals(1L, s.playCount)
        assertEquals("1760000000.25", s["last_played"].toString())
        assertFalse(s.isNew)
    }

    @Test
    fun safeNamesAndSizes() {
        assertEquals("KXCI_ 91.3_.png", safeFileName("KXCI: 91.3?.png"))
        assertEquals("KSIT 99.7 Jack FM", safeFileName("KSIT 99.7 Jack FM"))
        assertEquals("_CON.png", safeFileName("CON.png"))
        assertEquals("cover.png", safeFileName(""))
        val png = byteArrayOf(0x89.toByte(), 'P'.code.toByte(), 'N'.code.toByte(), 'G'.code.toByte(), 13, 10, 26, 10,
            0, 0, 0, 13, 'I'.code.toByte(), 'H'.code.toByte(), 'D'.code.toByte(), 'R'.code.toByte(),
            0, 0, 0, 64, 0, 0, 0, 48)
        assertEquals(64 to 48, ImageSize.of(png))
        assertNotEquals(null, ImageSize.of(png))
    }
}
