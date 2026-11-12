package com.frequencies.core

import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Build 185: Saved's tag strip in the car, eight tags at a time between arrows (tests use STRIP_TAGS). */
class Build185Test {
    private fun station(id: String, sort: Int, tags: List<String>): Station {
        val s = Station(LinkedHashMap(Defaults.station))
        s["id"] = JsonPrimitive(id)
        s["name"] = JsonPrimitive("Station $id")
        s["url"] = JsonPrimitive("http://example.com/$id")
        s["sort_index"] = JsonPrimitive(sort)
        s["tags"] = stringArray(tags)
        return s
    }

    // 80s, jazz, Rock, synth, t05..t16, zz talk: 17 tags, pages of 8.
    private val list = listOf(
        station("a", 0, listOf("80s", "jazz")),
        station("b", 1, listOf("Rock", "synth") + (5..16).map { "t%02d".format(it) }),
        station("c", 2, listOf("zz talk")),
        station("d", 3, emptyList()),
    )

    @Test
    fun theStripShowsEightTagsBetweenArrows() {
        assertEquals(8, Browse.STRIP_TAGS)
        val all = Browse.tags(list)
        assertEquals(17, all.size)
        val first = Browse.strip(list, Browse.SAVED)!!
        // More than 8 tags: both arrows, wrapping around.
        assertEquals(listOf("zz talk"), Browse.strip(list, first.back!!)!!.pills.map { it.tag })
        assertEquals(all.take(8), first.pills.map { it.tag })
        val second = Browse.strip(list, first.next!!)!!
        assertEquals(all.subList(8, 16), second.pills.map { it.tag })
        assertEquals(Browse.SAVED, second.back)
        val third = Browse.strip(list, second.next!!)!!
        assertEquals(all.drop(16), third.pills.map { it.tag })
        assertEquals(Browse.SAVED, third.next)
        // Eight tags or fewer: no arrows.
        val few = Browse.strip(list.filter { it.id != "b" }, Browse.SAVED)!!
        assertEquals(listOf("80s", "jazz", "zz talk"), few.pills.map { it.tag })
        assertNull(few.back)
        assertNull(few.next)
        // Moving the strip doesn't filter anything.
        assertEquals("", Browse.filterOf(second.next!!))
        assertEquals(4, Browse.list(list, second.next!!)!!.size)
    }

    @Test
    fun aTapKeepsTheStripWhereItWas() {
        val second = Browse.strip(list, Browse.strip(list, Browse.SAVED)!!.next!!)!!
        val rock = second.pills.first { it.tag == "t09" }
        assertEquals("tag:\"t09\"", Browse.filterOf(rock.target))
        assertEquals(listOf("b"), Browse.list(list, rock.target)!!.map { it.id })
        val after = Browse.strip(list, rock.target)!!
        assertEquals(second.pills.map { it.tag }, after.pills.map { it.tag })
        assertEquals(Search.INCLUDED, after.pills.first { it.tag == "t09" }.state)
        // A station played from there queues that filtered list.
        val pick = Browse.queueFor(list, Browse.itemId(rock.target, list[1]))!!
        assertEquals(listOf("b"), pick.stations.map { it.id })
        // An A-Z part id of a strip id is a part, not a filter.
        assertNull(Browse.filterOf(Browse.partId(rock.target, 0)))
    }

    @Test
    fun aStripPastTheEndComesBack() {
        val far = Browse.filterId("", 40)
        assertEquals(listOf("zz talk"), Browse.strip(list, far)!!.pills.map { it.tag })
        assertNull(Browse.strip(list.takeLast(1), Browse.SAVED))
    }
}
