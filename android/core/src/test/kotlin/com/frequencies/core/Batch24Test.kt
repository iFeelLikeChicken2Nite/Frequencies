package com.frequencies.core

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Android batch 24: Auto's tag pills, A-Z parts, Recent songs, the car's song line, and the title fit. */
class Batch24Test {
    private fun station(id: String, name: String, sort: Int, tags: List<String> = emptyList()): Station {
        val s = Station(LinkedHashMap(Defaults.station))
        s["id"] = JsonPrimitive(id)
        s["name"] = JsonPrimitive(name)
        s["url"] = JsonPrimitive("http://example.com/$id")
        s["sort_index"] = JsonPrimitive(sort)
        s["tags"] = stringArray(tags)
        return s
    }

    private val list = listOf(
        station("kbem", "Jazz 88.5 FM - KBEM", 0, listOf("jazz", "80s")),
        station("neo", "80s80s Neo", 1, listOf("80s", "synth")),
        station("jack", "KLKO 93.7 Jack FM", 2, listOf("Rock")),
        station("talk", "Talk 1500", 3, listOf("talk", "80s")),
        station("lush", "SomaFM Lush", 4),
    )

    private fun ids(stations: List<Station>?) = stations.orEmpty().map { it.id }

    @Test
    fun tagPillsWalkOnlyHideOff() {
        val pills = Browse.pills(list, "")
        assertEquals(listOf("80s", "jazz", "Rock", "synth", "talk"), pills.map { it.tag })
        assertTrue(pills.all { it.state == Search.NEUTRAL })
        // Tap 80s: only 80s.
        val only = pills.first { it.tag == "80s" }.target
        assertEquals("tag:\"80s\"", Browse.filterOf(only))
        assertEquals(listOf("kbem", "neo", "talk"), ids(Browse.list(list, only)))
        // Then tap talk: 80s, talk only (ORed, like the desktop).
        val both = Browse.pills(list, Browse.filterOf(only)!!).first { it.tag == "talk" }.target
        assertEquals(listOf("kbem", "neo", "talk"), ids(Browse.list(list, both)))
        // Tap talk again: 80s without talk (the long press's "hide").
        val hide = Browse.pills(list, Browse.filterOf(both)!!).first { it.tag == "talk" }
        assertEquals(Search.INCLUDED, hide.state)
        assertEquals(listOf("kbem", "neo"), ids(Browse.list(list, hide.target)))
        val hidden = Browse.pills(list, Browse.filterOf(hide.target)!!).first { it.tag == "talk" }
        assertEquals(Search.EXCLUDED, hidden.state)
        // A third tap clears it.
        assertEquals(Search.NEUTRAL, Browse.pills(list, Browse.filterOf(hidden.target)!!).first { it.tag == "talk" }.state)
        // Every tag off again is plain Saved.
        val off = Browse.pills(list, "tag:\"80s\"").first { it.tag == "80s" }.target
        assertEquals(Browse.SAVED, Browse.pills(list, Browse.filterOf(off)!!).first { it.tag == "80s" }.target.let { Browse.filterId(Search.setTag(Browse.filterOf(it)!!, "80s", Search.NEUTRAL)) })
        assertEquals(Browse.SAVED, Browse.filterId(""))
    }

    @Test
    fun aStationInAFilteredListQueuesThatList() {
        val only = Browse.filterId("tag:\"80s\"")
        val pick = Browse.queueFor(list, Browse.itemId(only, list[3]))!!
        assertEquals(listOf("kbem", "neo", "talk"), ids(pick.stations))
        assertEquals(2, pick.index)
        // A station inside the list is not the list.
        assertNull(Browse.filterOf(Browse.itemId(only, list[3])))
        // Tag names with odd characters survive.
        val odd = Browse.filterId(Search.setTag("", "rock & roll/90s @home", Search.INCLUDED))
        assertTrue('/' !in odd && '@' !in odd)
        assertEquals("tag:\"rock & roll/90s @home\"", Browse.filterOf(odd))
    }

    @Test
    fun longListsComeInAToZParts() {
        val many = (0 until 820).map { n -> station("s$n", "Station ${"%03d".format(n)} " + ('A' + n % 26), n) }
        val parts = Browse.parts(many, Browse.SAVED)
        assertEquals(6, parts.size)
        assertTrue(parts.all { it.stations.size <= Browse.PAGE_MOST })
        // Every frequency is in exactly one part, A to Z.
        val all = parts.flatMap { it.stations }
        assertEquals(820, all.size)
        assertEquals(820, all.map { it.id }.toSet().size)
        assertEquals(all.map { Browse.loose(it.displayName) }.sorted(), all.map { Browse.loose(it.displayName) })
        // Parts have ids the tree can open, and a tap inside one plays that part.
        assertEquals(Browse.SAVED to 2, Browse.partOf(parts[2].id))
        assertEquals(ids(parts[2].stations), ids(Browse.list(many, parts[2].id)))
        val pick = Browse.queueFor(many, Browse.itemId(parts[2].id, parts[2].stations[5]))!!
        assertEquals(5, pick.index)
        // Short lists have none.
        assertTrue(Browse.parts(list, Browse.SAVED).isEmpty())
        assertTrue(Browse.parts(many.take(150), Browse.SAVED).isEmpty())
        assertEquals(2, Browse.parts(many.take(151), Browse.SAVED).size)
        // Labels by letter, longer when two parts meet on one.
        val abc = (0 until 288).map { n -> station("x$n", ('A' + n / 12).toString() + "%03d".format(n), n) }
        assertEquals(listOf("A – L", "M – X"), Browse.parts(abc, Browse.SAVED).map { it.label })
        assertTrue(Browse.parts(many, Browse.SAVED).zipWithNext().all { (a, b) -> a.label != b.label })
        // A tag's long list has parts too, under the tag's own id.
        val tagged = many.map { s -> s.copy().apply { this["tags"] = stringArray(listOf("big")) } }
        val tagParts = Browse.parts(Browse.tagged(tagged, "big"), Browse.tagListId("big"))
        assertEquals(Browse.tagListId("big") to 0, Browse.partOf(tagParts[0].id))
        assertEquals(ids(tagParts[0].stations), ids(Browse.list(tagged, tagParts[0].id)))
    }

    private fun song(title: String, artist: String, station: String, at: Double, favorite: Boolean = false) =
        kotlinx.serialization.json.JsonObject(mapOf(
            "title" to JsonPrimitive(title), "artist" to JsonPrimitive(artist), "station" to JsonPrimitive(station),
            "at" to JsonPrimitive(at), "favorite" to JsonPrimitive(favorite), "cover_url" to JsonPrimitive(""),
        ))

    @Test
    fun recentIsSongHistory() {
        val history = JsonArray(listOf(
            song("Folded", "Kehlani", "KLKO 93.7 Jack FM", 300.0),
            song("When the World Is Running Down", "The Police", "80s80s Neo", 500.0, favorite = true),
            song("Gone Song", "Someone", "A station I removed", 400.0),
            song("Take On Me", "a-ha", "80s80s neo", 200.0),
        ))
        val songs = Browse.recentSongs(list, history)
        // Newest first, favorites included where they fall.
        assertEquals(listOf("When the World Is Running Down", "Gone Song", "Folded", "Take On Me"), songs.map { it.title })
        assertEquals(listOf("neo", null, "jack", "neo"), songs.map { it.station?.id })
        assertTrue(songs[0].favorite)
        assertEquals(songs.size, songs.map { it.id }.toSet().size)
        // A tap plays the frequency it was heard on, inside the recent frequencies.
        val pick = Browse.queueFor(list, songs[2].id, history)!!
        assertEquals(listOf("neo", "jack"), ids(pick.stations))
        assertEquals(1, pick.index)
        assertNull(Browse.queueFor(list, songs[1].id, history))
        assertTrue(Browse.recentSongs(list, JsonArray(emptyList())).isEmpty())
        // At most RECENT_SONGS.
        val lots = JsonArray((0 until 100).map { song("Song $it", "A", "Talk 1500", it.toDouble()) })
        assertEquals(Browse.RECENT_SONGS, Browse.recentSongs(list, lots).size)
    }

    @Test
    fun carLineAlwaysHasTheStation() {
        assertEquals("Charli XCX · WITR 89.7 FM", NowPlayingRules.carLine("Charli XCX", "WITR 89.7 FM"))
        assertEquals("WITR 89.7 FM", NowPlayingRules.carLine("WITR 89.7 FM", "WITR 89.7 FM"))
        assertEquals("WITR 89.7 FM", NowPlayingRules.carLine("", "WITR 89.7 FM"))
        assertEquals("Kehlani", NowPlayingRules.carLine("Kehlani", ""))
    }

    @Test
    fun titleFitShrinksTheLogoBeforeTheTitle() {
        val fits = Comfort.TITLE_FITS
        assertEquals(Comfort.TitleFit(2, 1f, 1f), fits[0])
        // The logo gives way first; the title's size only after that.
        assertEquals(1f, fits[1].textScale)
        assertTrue(fits[1].logoScale < 1f)
        assertTrue(fits.zipWithNext().all { (a, b) -> b.logoScale <= a.logoScale && b.textScale <= a.textScale && b.lines >= a.lines })
        var step = 0
        repeat(10) { step = Comfort.nextTitleFit(step, cutOff = true) }
        assertEquals(fits.lastIndex, step)
        assertEquals(1, Comfort.nextTitleFit(1, cutOff = false))
        assertFalse(Comfort.nextTitleFit(0, cutOff = false) != 0)
    }
}
