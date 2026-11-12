package com.frequencies.core

import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Android Auto's tabs and voice search (AP3). */
class BrowseTest {
    private fun station(id: String, name: String, sort: Int, tags: List<String> = emptyList(), played: Double = 0.0, custom: String = ""): Station {
        val s = Station(LinkedHashMap(Defaults.station))
        s["id"] = JsonPrimitive(id)
        s["name"] = JsonPrimitive(name)
        s["url"] = JsonPrimitive("http://example.com/$id")
        s["sort_index"] = JsonPrimitive(sort)
        s["tags"] = stringArray(tags)
        s["last_played"] = JsonPrimitive(played)
        if (custom.isNotEmpty()) s["custom_name"] = JsonPrimitive(custom)
        return s
    }

    private val list = listOf(
        station("jack", "KLKO 93.7 Jack FM", 2, listOf("Rock"), played = 50.0),
        station("neo", "80s80s Neo", 1, listOf("80s", "synth")),
        station("lush", "SomaFM Lush", 3, listOf("chill"), played = 90.0),
        station("kbem", "Jazz 88.5 FM - KBEM", 0, listOf("jazz", "80S")),
        station("dump", "The Dump", 4, custom = "Dump Radio"),
    )

    private fun names(stations: List<Station>) = stations.map { it.id }

    @Test
    fun tabs() {
        assertEquals(listOf("kbem", "neo", "jack", "lush", "dump"), names(Browse.saved(list)))
        assertEquals(listOf("lush", "jack"), names(Browse.recent(list)))
        // One "80s" for "80s" and "80S"; first spelling wins.
        assertEquals(listOf("80s", "chill", "jazz", "Rock", "synth"), Browse.tags(list))
        assertEquals(listOf("kbem", "neo"), names(Browse.tagged(list, "80s")))
    }

    @Test
    fun itemIdsQueueTheirList() {
        val tagId = Browse.tagListId("rock & roll/90s")
        assertEquals("rock & roll/90s", Browse.tagOf(tagId))
        assertTrue('/' !in tagId)
        val pick = Browse.queueFor(list, Browse.itemId(Browse.SAVED, list[0]))!!
        assertEquals(listOf("kbem", "neo", "jack", "lush", "dump"), names(pick.stations))
        assertEquals(2, pick.index)
        // Recent is song history since batch 24 (Batch24Test).
        val tag = Browse.queueFor(list, Browse.tagListId("80s") + "/neo")!!
        assertEquals(listOf("kbem", "neo"), names(tag.stations))
        assertEquals(1, tag.index)
        assertNull(Browse.queueFor(list, "jack"))
        assertNull(Browse.queueFor(list, "saved/gone"))
    }

    @Test
    fun playFromSearch() {
        // A tag's name plays the tag, from its first frequency.
        val tag = Browse.playFromSearch(list, "80s")!!
        assertEquals(listOf("kbem", "neo"), names(tag.stations))
        assertEquals(0, tag.index)
        assertEquals(tag.stations, Browse.playFromSearch(list, "the 80's station")!!.stations)
        // Names play inside Saved.
        fun said(q: String) = Browse.playFromSearch(list, q)?.let { it.stations[it.index].id }
        assertEquals("jack", said("jack fm"))
        assertEquals("jack", said("93.7"))
        assertEquals("lush", said("Lush"))
        assertEquals("kbem", said("kbem"))
        assertEquals("kbem", said("jazz 88.5"))
        assertEquals("neo", said("80s 80s neo"))
        // Custom name and real name both count.
        assertEquals("dump", said("dump radio"))
        assertEquals("dump", said("the dump"))
        assertNull(said("polka"))
        assertNull(said(""))
    }

    @Test
    fun searchResults() {
        val r = Browse.search(list, "jazz")
        assertEquals(listOf("jazz"), r.tags)
        assertEquals(listOf("kbem"), names(r.stations))
        val chill = Browse.search(list, "chill")
        assertEquals(listOf("chill"), chill.tags)
        assertEquals(listOf("lush"), names(chill.stations))
        assertTrue(Browse.search(list, "polka").stations.isEmpty())
    }

    @Test
    fun loose() {
        assertEquals("beyonce and jay z", Browse.loose("Beyoncé & Jay-Z"))
        assertEquals("88.5 fm kbem", Browse.loose("88.5 FM - KBEM"))
        assertEquals("80s", Browse.loose("80's"))
        assertEquals("dare fm", Browse.loose("Dare-FM."))
    }
}
