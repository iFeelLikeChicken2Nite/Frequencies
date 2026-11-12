package com.frequencies.core

import java.nio.file.Files
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Batch 19 (build 173), desktop Batch CO (build 171): New Age Radio and
 * Dare-FM ship with the desktop's addresses and an old pair joins once
 * (task 227), a station that arrives without its picture is dated
 * [-1, me] whatever Station pictures says (task 226), incoming t < 0
 * stamps are ignored, and the one-time `filled_fix` repair.
 */
class Batch19Test {
    private val A = "a".repeat(32)
    private val B = "b".repeat(32)
    private val NEW_AGE = "http://a7.radioheart.ru:8044/newageradio?_ga=2.255684622.769977034.1646991967-126118534.1646991967"
    private val DARE = "http://hydra.cdnstream.com/1552_128?listenerId=874e5aaea897e6a11bc60184f95f9946&aw_0_1st.playerid=esPlayer&aw_0_1st.skey=1689285807"

    inner class Dev(val me: String, var now: Long) {
        val dir = Files.createTempDirectory("dev").toFile()
        val stations = ArrayList<Station>()
        var settings = Settings.from(null)
        val history = ArrayList<JsonObject>()
        val store = SyncData.Store()
        var what = SyncData.What.ALL

        fun observe() = SyncData.observe(stations, settings, history, store, me, now)
        fun state(): JsonObject { observe(); return SyncData.buildState(stations, settings, history, store, now, what = what) }
        fun take(state: JsonObject): SyncData.Merged {
            observe()
            val res = SyncData.merge(stations, settings, history, store, me, state, 300, now, newId = { "new-$me" }, what = what)
            SyncData.joinOldAddresses(stations, settings, store, me, now)
            return res
        }
        fun add(id: String, url: String, name: String, plays: Int = 0, tags: List<String> = emptyList()): Station {
            val s = Station(linkedMapOf("id" to JsonPrimitive(id), "url" to JsonPrimitive(url), "name" to JsonPrimitive(name)))
            Station.normalize(s, now / 1000.0) { id }
            s["play_count"] = JsonPrimitive(plays)
            s["tags"] = JsonArray(tags.map { JsonPrimitive(it) })
            stations += s
            return s
        }
    }

    private fun sync(x: Dev, y: Dev) {
        repeat(2) {
            val sx = x.state(); val sy = y.state()
            y.take(sx); x.take(sy)
        }
    }

    @Test fun theTwoDefaultsShipWithTheDesktopsAddresses() {
        val urls = Featured.readList(Defaults.defaultStationsText()).map { it["url"].str() }
        assertTrue(NEW_AGE in urls)
        assertTrue(DARE in urls)
        val fixes = SyncData.addressFixes()
        assertEquals(NEW_AGE, fixes["http://a7.radioheart.ru:8044/newageradio"])
        assertEquals(DARE, fixes["http://hydra.cdnstream.com/1552_128"])
    }

    @Test fun anOldPhoneAddressAndTheDesktopsJoinAsOneStation() {
        val phone = Dev(B, 1_000_000)
        phone.add("zz-phone", "http://a7.radioheart.ru:8044/newageradio", "New Age Radio", plays = 7, tags = listOf("Chill"))
        phone.observe()
        val desk = Dev(A, 1_000_500)
        desk.add("aa-desk", NEW_AGE, "New Age Radio", plays = 3, tags = listOf("Ambient"))
        desk.settings.data["last_station_id"] = JsonPrimitive("aa-desk")
        phone.settings.data["last_station_id"] = JsonPrimitive("zz-phone")
        desk.observe()
        phone.now = 2_000_000; desk.now = 2_000_000
        sync(phone, desk)
        for (d in listOf(phone, desk)) {
            assertEquals(1, d.stations.size)
            val s = d.stations.single()
            assertEquals("aa-desk", s.id)
            assertEquals(NEW_AGE, s.url)
            assertEquals(7, s["play_count"].num()!!.toInt())
            assertEquals(setOf("Chill", "Ambient"), (s["tags"] as JsonArray).map { it.str() }.toSet())
        }
        assertTrue("zz-phone" in phone.store.tomb)
        assertEquals("aa-desk", phone.settings.data["last_station_id"].str())
    }

    @Test fun aLoneOldAddressIsLeftAlone() {
        val phone = Dev(B, 1_000_000)
        phone.add("p1", "http://hydra.cdnstream.com/1552_128", "Dare-FM")
        val res = SyncData.joinOldAddresses(phone.stations, phone.settings, phone.store, B, 1_000_000)
        assertFalse(res.stations)
        assertEquals("http://hydra.cdnstream.com/1552_128", phone.stations.single().url)
    }

    @Test fun aStationWithoutItsPictureIsFilledInWithPicturesOn() {
        val desk = Dev(A, 1_000_000)
        desk.add("s1", "http://example.com/one", "One")
        // The sender doesn't send the picture (its own Station pictures are off for this phone,
        // or it got the station without one); this phone has pictures on.
        desk.what = SyncData.cleanWhat(JsonObject(mapOf("art" to JsonPrimitive(false))))
        val phone = Dev(B, 1_000_000)
        phone.take(desk.state())
        val art = phone.store.st.getValue("s1").getValue("art")
        assertEquals(SyncData.Stamp(-1, B), art.stamp)
        // Never sent: the phone's state carries no art for it.
        val sent = (phone.state()["stations"] as JsonArray).map { it as JsonObject }.first { it["id"].str() == "s1" }
        assertFalse("art" in (sent["f"] as JsonObject))
    }

    @Test fun incomingNegativeStampsAreIgnored() {
        val phone = Dev(B, 1_000_000)
        phone.add("s1", "http://example.com/one", "One")
        phone.observe()
        val state = JsonObject(mapOf(
            "stations" to JsonArray(listOf(JsonObject(mapOf(
                "id" to JsonPrimitive("s1"),
                "f" to JsonObject(mapOf("name" to JsonArray(listOf(JsonPrimitive("Hacked"), JsonPrimitive(-1), JsonPrimitive("z".repeat(32)))))),
            )))),
            "settings" to JsonObject(mapOf("crossfade_ms" to JsonArray(listOf(JsonPrimitive(1), JsonPrimitive(-1), JsonPrimitive("z".repeat(32)))))),
        ))
        // Make the phone's own stamps [0, me] so a -1 would otherwise be "older" but a tie rule can't matter.
        SyncData.merge(phone.stations, phone.settings, phone.history, phone.store, B, state, 300, 1_000_000)
        assertEquals("One", phone.stations.single().name)
        assertTrue(phone.settings.data["crossfade_ms"]?.num()?.toInt() != 1)
    }

    @Test fun picturesDatedWhileNotSyncedAreRepairedOnce() {
        val phone = Dev(B, 1_000_000)
        phone.add("s1", "http://example.com/one", "One")
        phone.store.st["s1"] = linkedMapOf(
            "url" to SyncData.Rec(SyncData.Stamp(500, A), SyncData.fingerprint(JsonPrimitive("http://example.com/one"))),
            "art" to SyncData.Rec(SyncData.Stamp(900, B), SyncData.fingerprint(null)),
        )
        assertTrue(SyncData.repairFilledIn(phone.stations, phone.store, B))
        assertEquals(SyncData.Stamp(-1, B), phone.store.st.getValue("s1").getValue("art").stamp)
        assertFalse(SyncData.repairFilledIn(phone.stations, phone.store, B))
        val again = SyncData.Store.from(phone.store.toJson())
        assertTrue(again.filledFix)
    }
}
