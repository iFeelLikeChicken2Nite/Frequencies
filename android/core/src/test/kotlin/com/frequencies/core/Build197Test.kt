package com.frequencies.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Build 197: the line under the song on the car's screen takes turns, artist then station. */
class Build197Test {
    @Test
    fun artistAndStationTakeTurns() {
        assertEquals("Charli XCX", NowPlayingRules.carTurn("Charli XCX", "WITR 89.7 FM", showStation = false))
        assertEquals("WITR 89.7 FM", NowPlayingRules.carTurn("Charli XCX", "WITR 89.7 FM", showStation = true))
        assertTrue(NowPlayingRules.carTakesTurns("Charli XCX", "WITR 89.7 FM"))
    }

    @Test
    fun theStationAloneNeverTakesTurns() {
        for (show in listOf(false, true)) {
            assertEquals("WITR", NowPlayingRules.carTurn("", "WITR", show))
            assertEquals("WITR", NowPlayingRules.carTurn("witr", "WITR", show))
        }
        assertFalse(NowPlayingRules.carTakesTurns("", "WITR"))
        assertFalse(NowPlayingRules.carTakesTurns("WITR ", "witr"))
        assertEquals(7_000L, NowPlayingRules.CAR_TURN_MS)
        // Off the car's screen both stay together.
        assertEquals("Charli XCX · WITR", NowPlayingRules.carLine("Charli XCX", "WITR"))
    }

    private fun station(id: String, sort: Int, plays: Int, name: String): Station {
        val s = Station(LinkedHashMap(Defaults.station))
        s["id"] = kotlinx.serialization.json.JsonPrimitive(id)
        s["name"] = kotlinx.serialization.json.JsonPrimitive(name)
        s["sort_index"] = kotlinx.serialization.json.JsonPrimitive(sort)
        s["play_count"] = kotlinx.serialization.json.JsonPrimitive(plays)
        return s
    }

    @Test
    fun theCarHasItsOwnSavedOrder() {
        val all = listOf(station("a", 0, 1, "Zed"), station("b", 1, 9, "Alpha"), station("c", 2, 4, "Mid"))
        // Most played first unless chosen; a bad value falls back to it.
        assertEquals("play_count", Browse.carOrder(Settings(LinkedHashMap())))
        assertEquals("play_count", Browse.carOrder(Settings(linkedMapOf("car_sort_mode" to kotlinx.serialization.json.JsonPrimitive("nonsense")))))
        assertEquals("alphabetical", Browse.carOrder(Settings(linkedMapOf("car_sort_mode" to kotlinx.serialization.json.JsonPrimitive("alphabetical")))))
        assertEquals(listOf("b", "c", "a"), Browse.list(all, Browse.SAVED, order = "play_count")!!.map { it.id })
        assertEquals(listOf("b", "c", "a"), Browse.list(all, Browse.SAVED, order = "alphabetical")!!.map { it.id })
        assertEquals(listOf("a", "b", "c"), Browse.list(all, Browse.SAVED)!!.map { it.id })
        // A tap plays the list in the car's order, from the tapped one.
        val pick = Browse.queueFor(all, Browse.itemId(Browse.SAVED, all[2]), order = "play_count")!!
        assertEquals(listOf("b", "c", "a"), pick.stations.map { it.id })
        assertEquals(1, pick.index)
    }
}
