package com.frequencies.core

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.file.Files
import kotlin.random.Random
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Local files stations (task 214's groundwork) and offline mode's rules. */
class LocalStationsTest {
    private var n = 0
    private val newId = { "id${n++}" }
    private fun fixture(name: String): ByteArray =
        LocalStationsTest::class.java.getResourceAsStream("/desktop/$name")!!.use { it.readBytes() }

    @Test
    fun aPhoneFolderStationInTheDesktopFormat() {
        val path = LocalStations.pathOfTreeId("primary:Music/Lo-fi")
        assertEquals("/storage/emulated/0/Music/Lo-fi", path)
        assertEquals("/storage/1A2B-3C4D/Albums", LocalStations.pathOfTreeId("1A2B-3C4D:Albums"))
        assertEquals("/storage/emulated/0", LocalStations.pathOfTreeId("primary:"))
        val s = LocalStations.create(LocalStations.folderName(path), path, "content://com.android.externalstorage.documents/tree/primary%3AMusic%2FLo-fi", 1.0, newId)
        assertEquals("Lo-fi", s.displayName)
        assertEquals("local", s.kind)
        assertEquals("local://$path", s.url)
        assertEquals(path, LocalStations.localPath(s))
        assertEquals("Local · Lo-fi", LocalStations.subtitle(s))
        // Through a backup and back: every key kept, the phone's folder link too.
        val dir = Files.createTempDirectory("local").toFile()
        val backup = Backup.export(Backup.Contents(listOf(s), Settings(LinkedHashMap()), JsonObject(emptyMap()), kotlinx.serialization.json.JsonArray(emptyList()), JsonObject(emptyMap()), JsonObject(emptyMap())))
        val back = Backup.read(encodeJson(backup), dir).stations.single()
        assertEquals(s.toJson(), back.toJson())
    }

    @Test
    fun offlineRulesMatchTheDesktop() {
        val stream = Station(JsonObject(mapOf("url" to JsonPrimitive("https://x/stream"))))
        val local = Station(JsonObject(mapOf("kind" to JsonPrimitive("local"), "local_path" to JsonPrimitive("/home/me/Music"))))
        val twitch = Station(JsonObject(mapOf("kind" to JsonPrimitive("twitch"), "url" to JsonPrimitive("https://twitch.tv/x"))))
        assertTrue(OfflineRules.needsInternet(stream))
        assertTrue(OfflineRules.needsInternet(twitch))
        assertFalse(OfflineRules.needsInternet(local))
        assertEquals("You're offline. Frequencies that need the internet are grayed out until it's back.", OfflineRules.BANNER)
        assertEquals("You're offline. KXCI needs the internet; local files still play.", OfflineRules.cantPlay("KXCI"))
    }

    @Test
    fun pickingTracks() {
        val tracks = listOf("a", "b", "c")
        val rng = Random(7)
        repeat(50) { assertNotEquals("b", LocalStations.pick(tracks, "b", rng)) }
        assertEquals("a", LocalStations.pick(listOf("a"), "a", rng))
        assertEquals(null, LocalStations.pick(emptyList<String>(), null, rng))
        repeat(50) { val f = LocalStations.joinFraction(rng); assertTrue(f in 0.05..0.6) }
        assertTrue(LocalStations.isAudio("Song.FLAC"))
        assertFalse(LocalStations.isAudio("cover.jpg"))
    }

    @Test
    fun offlineNeedsToHoldBothWays() {
        val d = OfflineDecider()
        assertFalse(d.look(false, 0))
        assertFalse(d.look(false, 5_000))
        assertFalse(d.offline)
        assertTrue(d.look(false, 6_000))           // six seconds of bad looks
        assertTrue(d.offline)
        assertFalse(d.look(true, 7_000))
        assertFalse(d.look(false, 8_000))          // a blip resets the count
        assertFalse(d.look(true, 9_000))
        assertFalse(d.look(true, 12_000))
        assertTrue(d.look(true, 13_000))           // four seconds of good ones
        assertFalse(d.offline)
    }

    @Test
    fun desktopShareZipWithMusicUnpacks() {
        val root = Files.createTempDirectory("music").toFile()
        val art = File(root, "art")
        val stations = ShareZip.read(ByteArrayInputStream(fixture("share-audio.zip")), art, newId, audioRoot = root)
        val s = stations.single()
        assertEquals("local", s.kind)
        val folder = File(LocalStations.localPath(s))
        assertTrue(folder.isDirectory)
        assertEquals("ID3fakeaudio1", File(folder, "a.mp3").readText())
        assertEquals("OggSfake2", File(folder, "sub/b.ogg").readText())
        assertEquals("local://" + folder.absolutePath, s.url)
        assertTrue("staging cleaned up", root.listFiles()!!.none { it.name.startsWith(".incoming") })
        // Without a place for music: a station with no folder, as before.
        assertEquals("", LocalStations.localPath(ShareZip.read(ByteArrayInputStream(fixture("share-audio.zip")), art, newId).single()))
    }

    @Test
    fun phoneShareZipCarriesTheMusic() {
        val music = Files.createTempDirectory("phone-music").toFile()
        File(music, "Disc 1").mkdirs()
        File(music, "Disc 1/01 Song.mp3").writeText("mp3data")
        File(music, "02 Other.flac").writeText("flacdata")
        val s = LocalStations.create("Lo-fi", music.absolutePath, "", 1.0, newId)
        val out = ByteArrayOutputStream()
        val tracks = music.walkTopDown().filter { it.isFile }.sortedBy { it.path }
            .map { f -> ShareZip.Track(f.relativeTo(music).invariantSeparatorsPath) { f.inputStream() } }.toList()
        assertEquals(2, ShareZip.write(listOf(s), out) { tracks })
        val build = File(System.getProperty("user.dir"), "build").apply { mkdirs() }
        File(build, "phone-share-audio.zip").writeBytes(out.toByteArray())
        val root = Files.createTempDirectory("again").toFile()
        val back = ShareZip.read(ByteArrayInputStream(out.toByteArray()), File(root, "art"), newId, audioRoot = root).single()
        assertEquals("mp3data", File(LocalStations.localPath(back), "Disc 1/01 Song.mp3").readText())
        assertFalse("the phone's folder link never leaves the phone", String(out.toByteArray(), Charsets.ISO_8859_1).contains(LocalStations.ANDROID_FOLDER))
    }
}
