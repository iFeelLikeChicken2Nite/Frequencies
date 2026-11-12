package com.frequencies.core

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.file.Files
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Share codes, share zips, search, sort, tags and other apps' formats
 * (AP4), checked against what desktop build 144 wrote (share-code.txt,
 * share.zip). What the phone writes is left in build/ for
 * tools/check_share_on_desktop.py.
 */
class ShareTest {
    private fun fixture(name: String): ByteArray =
        ShareTest::class.java.getResourceAsStream("/desktop/$name")!!.use { it.readBytes() }

    private var n = 0
    private val newId = { "id${++n}" }
    private fun dir(): File = Files.createTempDirectory("share").toFile()

    private fun station(id: String, name: String, url: String, tags: List<String> = emptyList()): Station {
        val s = Station(LinkedHashMap(Defaults.station))
        s["id"] = JsonPrimitive(id)
        s["name"] = JsonPrimitive(name)
        s["url"] = JsonPrimitive(url)
        s["tags"] = stringArray(tags)
        return s
    }

    @Test
    fun desktopCodeAndZip() {
        val fromCode = ShareCode.toStation(fixture("share-code.txt").toString(Charsets.UTF_8), dir(), newId)!!
        assertEquals("KXCI 91.3", fromCode.displayName)
        assertEquals("KXCI Community Radio", fromCode.name)
        assertEquals(listOf("community", "Jazz"), fromCode.tags)
        assertEquals(3L, fromCode.playCount)
        assertEquals(2.5, fromCode.gainDb, 0.0)

        val art = dir()
        val zipped = ShareZip.read(ByteArrayInputStream(fixture("share.zip")), art, newId)
        assertEquals(listOf("KXCI 91.3", "Two"), zipped.map { it.displayName })
        assertTrue(File(zipped[0].customArtPath).isFile)
        assertEquals(art.absolutePath, File(zipped[0].customArtPath).parent)
        assertEquals("", zipped[1].customArtPath)

        // Phone -> desktop: written for the desktop check script.
        val out = File(System.getProperty("user.dir"), "build").apply { mkdirs() }
        File(out, "phone-share.zip").outputStream().use { ShareZip.write(zipped, it) }
        val (code, withArt) = ShareCode.fromStation(fromCode, thumbnailer = null)
        assertFalse(withArt)
        File(out, "phone-share-code.txt").writeText(code)
        // And back on the phone.
        val again = ShareCode.toStation(code, dir(), newId)!!
        assertEquals(fromCode.displayName, again.displayName)
        assertEquals(fromCode.tags, again.tags)
    }

    @Test
    fun codeWithThumbnail() {
        val art = File(dir(), "big.png").apply { writeBytes(ByteArray(40_000) { (it % 251).toByte() }) }
        val s = station("a", "Big Art FM", "https://big.example/live")
        s["custom_art_path"] = JsonPrimitive(art.absolutePath)
        val (code, withArt) = ShareCode.fromStation(s) { _, _, _ -> ByteArray(900) { 7 } }
        assertTrue(withArt)
        assertTrue(code.length <= ShareCode.LIMIT)
        assertEquals("shared.jpg", ShareCode.entry(code)!!["art_name"].str())
        // A thumbnail that never fits: the code goes without the picture.
        val (plain, none) = ShareCode.fromStation(s) { _, _, _ -> java.util.Random(1).let { r -> ByteArray(4000).also(r::nextBytes) } }
        assertFalse(none)
        assertNull(ShareCode.entry(plain)!!["art_data"])
        assertNull(ShareCode.entry("FREQ1:not-a-code"))
        assertNull(ShareCode.entry("hello"))
    }

    @Test
    fun search() {
        val jazz = station("1", "KBEM Jazz", "https://kbem.example", listOf("jazz", "hi energy"))
        val talk = station("2", "News Talk", "https://talk.example", listOf("talk"))
        val rock = station("3", "Rock 101", "https://rock.example", listOf("rock", "live"))
        val all = listOf(jazz, talk, rock)
        fun ids(q: String) = Search.filter(all, q).map { it.id }
        assertEquals(listOf("1", "2", "3"), ids(""))
        assertEquals(listOf("1"), ids("kbem"))
        assertEquals(listOf("1", "3"), ids("tag:\"jazz\" tag:\"rock\""))
        assertEquals(listOf("1"), ids("tag:\"jazz\" tag:\"rock\" -tag:\"live\""))
        assertEquals(listOf("1"), ids("tag:energy"))
        assertEquals(emptyList<String>(), ids("tag:\"energy\""))
        assertEquals(listOf("2"), ids("talk"))
        var q = Search.cycleTag("kbem", "hi energy")
        assertEquals("kbem tag:\"hi energy\"", q)
        assertEquals(Search.INCLUDED, Search.tagState(q, "Hi Energy"))
        q = Search.cycleTag(q, "hi energy", exclude = true)
        assertEquals(Search.EXCLUDED, Search.tagState(q, "hi energy"))
        q = Search.cycleTag(q, "hi energy", exclude = true)
        assertEquals("kbem", q)
        assertEquals("rock tag:\"jazz\"", Search.withText("old tag:\"jazz\"", "rock"))
        assertEquals("old", Search.text("old tag:\"jazz\""))
    }

    @Test
    fun sortAndAdd() {
        val a = station("a", "beta", "https://a.example").apply { this["added_at"] = floatJson(1.0); this["sort_index"] = intJson(1) }
        val b = station("b", "Alpha", "https://b.example").apply { this["added_at"] = floatJson(2.0); this["sort_index"] = intJson(0) }
        assertEquals(listOf("b", "a"), sortStations(listOf(a, b), "alphabetical").map { it.id })
        assertEquals(listOf("a", "b"), sortStations(listOf(a, b), "alphabetical", descending = true).map { it.id })
        assertEquals(listOf("b", "a"), sortStations(listOf(a, b), "added").map { it.id })
        assertEquals(listOf("a", "b"), sortStations(listOf(a, b), "added", descending = false).map { it.id })
        assertEquals(listOf("b", "a"), sortStations(listOf(a, b), "manual", descending = false).map { it.id })

        val (list, added) = addStations(
            listOf(a, b), listOf(station("x", "Dup", "HTTPS://A.EXAMPLE"), station("y", "New", "https://new.example")), newId, 5.0,
        )
        assertEquals(1, added.size)
        assertEquals(3, list.size)
        assertTrue(added[0].isNew)
        assertEquals(2L, added[0].sortIndex)
        assertEquals(5.0, added[0].addedAt, 0.0)
    }

    @Test
    fun tags() {
        val settings = Settings.from(null)
        val list = listOf(
            station("1", "One", "https://1.example", listOf("Rock", "pop")),
            station("2", "Two", "https://2.example", listOf("pop")),
        )
        Tags.setColor(settings, "Rock", "#d96c6c")
        Tags.setSpare(settings, listOf("Later"))
        assertEquals(listOf("Later", "pop", "Rock"), Tags.library(list, settings))
        // A swap happens all at once.
        val swapped = Tags.rename(list, settings, linkedMapOf("Rock" to "Old", "pop" to "Rock"))
        assertEquals(listOf("Old", "Rock"), swapped[0].tags)
        assertEquals(listOf("Rock"), swapped[1].tags)
        assertEquals("#d96c6c", settings.tagColor("Old"))
        assertEquals(listOf("Rock", "pop"), list[0].tags)
        val removed = Tags.remove(swapped, settings, "rock")
        assertEquals(listOf("Old"), removed[0].tags)
        assertEquals(emptyList<String>(), removed[1].tags)
        val tagged = Tags.set(removed, setOf("2"), "Jazz", true)
        assertEquals(listOf("Jazz"), tagged[1].tags)
        assertEquals("Red", Tags.colorName("#D96C6C"))
    }

    @Test
    fun otherAppsFormats() {
        val m3u = "#EXTM3U\n#EXTINF:-1 tvg-logo=\"https://x/logo.png\" group-title=\"jazz, smooth\",Smooth Jazz\nhttps://sj.example/live\nhttps://bare.example/s\n"
        val (f1, e1) = Imports.read(m3u)
        assertEquals("m3u", f1)
        assertEquals(listOf("Smooth Jazz", "https://bare.example/s"), e1.map { it.name })
        assertEquals(listOf("jazz", "smooth"), e1[0].tags)
        assertEquals("https://x/logo.png", e1[0].favicon)

        val pls = "[playlist]\nFile1=http://a.example/1\nTitle1=One\nFile2=http://a.example/2\nNumberOfEntries=2\n"
        assertEquals(listOf("One", "http://a.example/2"), Imports.read(pls).second.map { it.name })

        val opml = """<?xml version="1.0"?><opml version="2.0"><body>
            <outline text="Jazz"><outline type="audio" text="KJZZ" URL="https://kjzz.example/s"/></outline>
            <outline type="link" text="TuneIn" URL="http://opml.radiotime.com/Tune.ashx?id=1"/>
            </body></opml>"""
        val (f3, e3) = Imports.read(opml)
        assertEquals("opml", f3)
        assertEquals(1, e3.size)
        assertEquals(listOf("jazz"), e3[0].tags)

        val json = """[{"name":"RD","url":"http://rd.example","url_resolved":"https://rd.example/r","stationuuid":"96062a7b-0601-11e8-ae97-52543be04c81","tags":"rock,pop","favicon":"https://rd/f.png"}]"""
        val e4 = Imports.read(json).second.single()
        assertEquals("https://rd.example/r", e4.url)
        assertEquals("96062a7b-0601-11e8-ae97-52543be04c81", e4.rbUuid)

        val xspf = """<?xml version="1.0"?><playlist version="1" xmlns="http://xspf.org/ns/0/"><trackList>
            <track><title>X</title><location>https://x.example/s</location><meta rel="http://frequencies.invalid/ns/tags">a, b</meta></track>
            </trackList></playlist>"""
        assertEquals(listOf("a", "b"), Imports.read(xspf).second.single().tags)

        val gv = "<Stations><Station><uri>https://g.example</uri><name>G</name><insecure>true</insecure></Station></Stations>"
        assertTrue(Imports.read(gv).second.single().insecure)

        // Written out and read back the same.
        val stations = e1.map { Imports.toStation(it, newId, 1.0) }
        for (format in Imports.EXPORT_FORMATS) {
            val back = Imports.read(Imports.write(stations, format)).second
            assertEquals(format, stations.map { it.url }, back.map { it.url })
            assertEquals(format, stations.map { it.displayName }, back.map { it.name })
            if (format != "pls" && format != "goodvibes") assertEquals(format, stations.map { it.tags }, back.map { it.tags })
        }
        try { Imports.read("   "); error("no") } catch (e: Imports.ImportError) { }
    }

    @Test
    fun directoryResult() {
        val item = FreqJson.parseToJsonElement(
            """{"stationuuid":"u1","name":" Name ","url":"http://u","url_resolved":"https://r","tags":"Jazz,Blues","bitrate":128,"clickcount":5}""",
        ) as kotlinx.serialization.json.JsonObject
        val r = RadioBrowser.parse(item)
        assertEquals("Name", r.name)
        assertEquals("https://r", r.url)
        val s = RadioBrowser.toStation(r, newId, 3.0)
        assertEquals(emptyList<String>(), s.tags)
        assertEquals("u1", s["rb_uuid"].str())
    }

    @Test
    fun backupWritesAndReads() {
        val art = dir()
        val s = station("1", "One", "https://1.example")
        val out = ByteArrayOutputStream()
        val contents = Backup.Contents(listOf(s), Settings.from(null), kotlinx.serialization.json.JsonObject(emptyMap()),
            kotlinx.serialization.json.JsonArray(emptyList()), kotlinx.serialization.json.JsonObject(emptyMap()), kotlinx.serialization.json.JsonObject(emptyMap()))
        out.write(encodeJson(Backup.export(contents)).toByteArray())
        val back = Backup.read(out.toString(Charsets.UTF_8.name()), art)
        assertEquals("One", back.stations.single().name)
    }
}
