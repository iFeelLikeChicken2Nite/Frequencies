package com.frequencies.core

import java.io.File
import java.nio.file.Files
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Section 7's merge rules between two devices (both this code; SyncDesktopTest pairs it with the desktop's). */
class SyncDataTest {
    private val A = "a".repeat(32)
    private val B = "b".repeat(32)

    /** One device: its library and its stamps, with a hand-turned clock. */
    inner class Dev(val me: String) {
        val stations = ArrayList<Station>()
        var settings = Settings.from(null)
        val history = ArrayList<JsonObject>()
        val store = SyncData.Store()
        var now = 1_000_000L
        val art = Files.createTempDirectory("art").toFile()

        fun observe() = SyncData.observe(stations, settings, history, store, me, now)
        fun state(): JsonObject { observe(); return SyncData.buildState(stations, settings, history, store, now) }
        fun take(state: JsonObject): SyncData.Merged {
            observe()
            return SyncData.merge(stations, settings, history, store, me, state, 300, now, newId = { "new" })
        }
        fun station(id: String) = stations.firstOrNull { it.id == id }
    }

    private fun st(id: String, url: String, name: String = id) =
        Station.normalize(Station(linkedMapOf("id" to JsonPrimitive(id), "url" to JsonPrimitive(url), "name" to JsonPrimitive(name))), 1.0) { "x" }

    private fun song(title: String, artist: String, at: Double, fav: Boolean = false) = JsonObject(linkedMapOf(
        "title" to JsonPrimitive(title), "artist" to JsonPrimitive(artist), "station" to JsonPrimitive("S"),
        "at" to floatJson(at), "apple_music_url" to JsonPrimitive(""), "cover_url" to JsonPrimitive(""),
        "favorite" to JsonPrimitive(fav),
    ))

    /** Both ways, as a session does. */
    private fun sync(x: Dev, y: Dev) {
        y.take(x.state())
        x.take(y.state())
    }

    private fun pair(): Pair<Dev, Dev> {
        val a = Dev(A); val b = Dev(B)
        a.stations += st("s1", "http://one/"); a.stations += st("s2", "http://two")
        sync(a, b)
        return a to b
    }

    @Test fun newStationsArriveWithEveryField() {
        val (a, b) = pair()
        assertEquals(listOf("s1", "s2"), b.stations.map { it.id })
        assertEquals("http://one/", b.station("s1")!!.url)
        assertEquals(a.station("s1")!!.data.keys - "custom_art_path", b.station("s1")!!.data.keys - "custom_art_path")
    }

    @Test fun editsToDifferentFieldsBothSurvive() {
        val (a, b) = pair()
        a.now += 10; b.now += 20
        a.station("s1")!!["custom_name"] = JsonPrimitive("Renamed on A")
        b.station("s1")!!["tags"] = stringArray(listOf("jazz"))
        sync(a, b)
        for (d in listOf(a, b)) {
            assertEquals("Renamed on A", d.station("s1")!!.customName)
            assertEquals(listOf("jazz"), d.station("s1")!!.tags)
        }
    }

    @Test fun sameFieldNewestWinsAndTiesGoToTheBiggerId() {
        val (a, b) = pair()
        a.now += 10; b.now += 20
        a.station("s1")!!["custom_name"] = JsonPrimitive("A's name")
        b.station("s1")!!["custom_name"] = JsonPrimitive("B's name")
        sync(a, b)
        assertEquals("B's name", a.station("s1")!!.customName)
        assertEquals("B's name", b.station("s1")!!.customName)
        // Same millisecond: the bigger device id wins, on both sides.
        a.now = 5_000_000; b.now = 5_000_000
        a.store.clock = 0; b.store.clock = 0
        a.station("s2")!!["custom_name"] = JsonPrimitive("A")
        b.station("s2")!!["custom_name"] = JsonPrimitive("B")
        sync(a, b)
        assertEquals("B", a.station("s2")!!.customName)
        assertEquals("B", b.station("s2")!!.customName)
    }

    @Test fun aDeleteWinsForGoodAndTheSameAddressComesBackAsANewStation() {
        val (a, b) = pair()
        a.now += 10; b.now += 1_000
        a.stations.removeAll { it.id == "s1" }
        b.station("s1")!!["custom_name"] = JsonPrimitive("edited later")   // newer, still loses
        sync(a, b)
        assertNull(a.station("s1")); assertNull(b.station("s1"))
        assertTrue("s1" in b.store.tomb)
        // Merging an old state that still has it doesn't bring it back.
        val old = Dev(A).apply { stations += st("s1", "http://one/") }.state()
        assertFalse(b.take(old).stations)
        assertNull(b.station("s1"))
        // Adding the address again makes a new station.
        a.now += 10
        a.stations += st("s9", "http://one/")
        sync(a, b)
        assertEquals("http://one/", b.station("s9")!!.url)
    }

    @Test fun oneStationUnderTwoIdsKeepsTheSmallerOnBothSides() {
        val a = Dev(A); val b = Dev(B)
        a.stations += st("zzz", "HTTP://Same/"); a.settings.lastStationId = "zzz"
        b.stations += st("aaa", "http://same")
        a.stations += st("other", "http://other")
        a.stations.first()["ad_filler_station_id"] = JsonPrimitive("zzz")
        b.now += 5
        sync(a, b)
        assertEquals(listOf("aaa", "other"), a.stations.map { it.id }.sorted())
        assertEquals(listOf("aaa", "other"), b.stations.map { it.id }.sorted())
        assertEquals("aaa", a.settings.lastStationId)
        assertTrue("zzz" in a.store.tomb)
        sync(a, b)
        assertEquals(2, b.stations.size)
    }

    /** The same station on two devices: one of each's own details (build 164's first-sync bug). */
    private fun twinPair(): Pair<Dev, Dev> {
        val a = Dev(A); val b = Dev(B)
        a.stations += st("zzz", "http://same/").apply {
            this["play_count"] = intJson(9); this["last_played"] = floatJson(2_000.5)
            this["tags"] = stringArray(listOf("Jazz", "Late night"))
        }
        b.stations += st("aaa", "HTTP://Same").apply {
            this["play_count"] = intJson(3); this["last_played"] = floatJson(3_000.25)
            this["tags"] = stringArray(listOf("jazz", " ", "Rock"))
            this["custom_name"] = JsonPrimitive("Named on B")
        }
        a.now += 10; b.now += 50                                // B's copy is the newer one
        return a to b
    }

    @Test fun twinStationsKeepTheLargerCountsAndBothTagLists() {
        // Section 6.1 "Joining twins" (desktop build 165): the regression for 164's first sync.
        val (a, b) = twinPair()
        sync(a, b)
        for (d in listOf(a, b)) {
            val s = d.stations.single()
            assertEquals("aaa", s.id)                           // the smaller id stays
            assertEquals(9L, s.playCount)                       // the larger count (A's)
            assertEquals(3_000.25, s["last_played"].num()!!, 0.0)  // the later time (B's)
            assertEquals(listOf("jazz", "Rock", "Late night"), s.tags)  // the smaller id's copy first, A's spelling of "jazz" dropped
            assertEquals("Named on B", s.customName)           // everything else: newest stamp
        }
        // Both sides hold the same station, field for field.
        fun fields(d: Dev) = d.stations.single().data.filterKeys { it !in SyncData.NEVER_SENT }.mapValues { SyncData.fingerprint(it.value) }
        assertEquals(fields(a), fields(b))
        // The stamps settle: another round changes nothing on either side.
        sync(a, b)
        assertFalse(b.take(a.state()).changed)
        assertFalse(a.take(b.state()).changed)
        assertEquals(fields(a), fields(b))
    }

    @Test fun twinsJoinTheSameWhicheverSideGoesFirst() {
        val (a1, b1) = twinPair()
        sync(a1, b1)
        val (a2, b2) = twinPair()
        sync(b2, a2)
        fun fields(d: Dev) = d.stations.single().data.filterKeys { it !in SyncData.NEVER_SENT }.mapValues { SyncData.fingerprint(it.value) }
        assertEquals(fields(a1), fields(a2))
        assertEquals(fields(b1), fields(b2))
    }

    @Test fun aJoinedValueWinsOnADeviceThatDoesntJoinTwins() {
        // Desktop 160 (before twins were joined) takes the joined values by their fresh stamps.
        val (a, b) = twinPair()
        b.take(a.state())                                       // B joins: count 9, both tag lists
        val joined = b.station("aaa")!!
        val rec = b.store.st.getValue("aaa")
        assertEquals(B, rec.getValue("play_count").stamp.dev)
        assertTrue(rec.getValue("play_count").stamp.t >= b.now)
        // A device without the join, holding the same id, simply takes the newer stamps.
        val old = Dev(A).apply { stations += st("aaa", "http://same/").apply { this["play_count"] = intJson(1) } }
        old.take(b.state())
        assertEquals(joined.playCount, old.station("aaa")!!.playCount)
        assertEquals(joined.tags, old.station("aaa")!!.tags)
    }

    @Test fun aLoneStationIsNotJoined() {
        // No twin: an incoming station's own fields, by stamp, nothing added.
        val a = Dev(A); val b = Dev(B)
        a.stations += st("s1", "http://one").apply { this["play_count"] = intJson(4); this["tags"] = stringArray(listOf("x")) }
        b.stations += st("s2", "http://two").apply { this["play_count"] = intJson(8) }
        a.now += 10
        sync(a, b)
        assertEquals(4L, b.station("s1")!!.playCount)
        assertEquals(listOf("x"), b.station("s1")!!.tags)
        assertEquals(8L, a.station("s2")!!.playCount)
    }

    @Test fun tagUnionIsTheDesktops() {
        val t = SyncData.tagUnion(stringArray(listOf("Jazz", "", " rock ")).toList(), stringArray(listOf("jazz", "ROCK", "Blues")).toList())
        assertEquals(listOf("Jazz", " rock ", "Blues"), t.map { it.str() })
    }

    @Test fun mergingTheSameStateTwiceChangesNothing() {
        val (a, b) = pair()
        a.history += song("One", "Band", 100.0, fav = true)
        a.settings["theme"] = JsonPrimitive("vapor")
        a.now += 10
        val state = a.state()
        assertTrue(b.take(state).changed)
        assertFalse(b.take(state).changed)
        // And after both have everything, a whole round says nothing new.
        sync(a, b)
        assertEquals(SyncData.digest(a.state()).length, 16)
        val before = SyncData.buildState(b.stations, b.settings, b.history, b.store, b.now)
        assertFalse(b.take(a.state()).changed)
        assertFalse(b.observe())
        assertEquals(SyncData.digest(before), SyncData.digest(SyncData.buildState(b.stations, b.settings, b.history, b.store, b.now)))
    }

    @Test fun onlySharedSettingsTravelAndPhoneKeysStayPut() {
        val (a, b) = pair()
        a.now += 10
        a.settings["theme"] = JsonPrimitive("vapor")
        a.settings[Settings.ANDROID_ACCENT] = JsonPrimitive("material_you")
        a.settings["android_moving_bg"] = JsonPrimitive("off")
        a.settings["volume"] = intJson(12)
        val state = a.state()
        val sent = (state["settings"] as JsonObject).keys
        assertTrue("theme" in sent)
        assertFalse(sent.any { it.startsWith("android_") || it == "volume" || it == "companion_closed" })
        // A desktop's state that (wrongly) carries them changes nothing here.
        b.settings["android_moving_bg"] = JsonPrimitive("always")
        val forged = JsonObject(state + ("settings" to JsonObject((state["settings"] as JsonObject) + mapOf(
            "android_moving_bg" to JsonArray(listOf(JsonPrimitive("off"), JsonPrimitive(9_999_999), JsonPrimitive(A))),
            "companion_closed" to JsonArray(listOf(JsonPrimitive(true), JsonPrimitive(9_999_999), JsonPrimitive(A))),
        ))))
        val res = b.take(forged)
        assertEquals(setOf("theme"), res.settings)
        assertEquals("always", b.settings.string("android_moving_bg"))
        assertNull(b.settings["companion_closed"])
    }

    @Test fun aNumberWrittenAnotherWayIsNotAnEdit() {
        val a = Dev(A)
        a.settings["crossfade_ms"] = intJson(700)
        a.observe()
        a.now += 10
        a.settings["crossfade_ms"] = JsonUnquoted("700.0")
        assertFalse(a.observe())
    }

    @Suppress("FunctionName")
    private fun JsonUnquoted(text: String) = kotlinx.serialization.json.JsonUnquotedLiteral(text)

    @Test fun starsNewestWinsAndUnstarringSticks() {
        val (a, b) = pair()
        a.history += song("One", "Band", 100.0, fav = true)
        a.now += 10
        sync(a, b)
        assertTrue(SongHistory.isFavorite(b.history.single()))
        b.now = a.now + 50
        b.history[0] = JsonObject(b.history[0] + ("favorite" to JsonPrimitive(false)))
        sync(b, a)
        assertFalse(SongHistory.isFavorite(a.history.single()))
        sync(a, b)
        assertFalse(SongHistory.isFavorite(b.history.single()))
    }

    @Test fun historyIsAUnionWithClearAndTheLimit() {
        val (a, b) = pair()
        a.history += song("A1", "x", 300.0); a.history += song("Star", "x", 200.0, fav = true)
        b.history += song("B1", "y", 250.0); b.history += song("Star", "x", 150.0, fav = true)
        a.now += 10; b.now += 10
        sync(a, b)
        val titles = b.history.map { SongHistory.title(it) }
        assertEquals(listOf("A1", "B1", "Star"), titles)            // the star keeps its newest row only
        assertEquals(200.0, SongHistory.at(b.history.last()), 0.0)
        // Clear history on A at t=260 s: older ordinary rows go everywhere, stars stay.
        a.now = 2_000_000
        a.history.clear(); a.history += song("Star", "x", 200.0, fav = true)
        a.history.add(0, song("A1", "x", 300.0))
        a.settings[SyncData.CLEARED_KEY] = floatJson(260.0)
        sync(a, b)
        assertEquals(listOf("A1", "Star"), b.history.map { SongHistory.title(it) })
        // The limit counts ordinary rows only.
        val many = (1..20).map { song("S$it", "z", 1000.0 + it) }
        a.history.addAll(0, many.reversed())
        a.now += 10
        b.take(a.state())
        val c = Dev("c".repeat(32))
        val res = SyncData.merge(c.stations, c.settings, c.history, c.store, c.me, a.state(), historyLimit = 10, nowMs = a.now)
        assertTrue(res.history)
        assertEquals(10, c.history.count { !SongHistory.isFavorite(it) })
        assertEquals(1, c.history.count { SongHistory.isFavorite(it) })
    }

    @Test fun eachDeviceKeepsItsOwnMusicFolder() {
        val (a, b) = pair()
        val here = Files.createTempDirectory("music").toFile()
        b.station("s2")!!["local_path"] = JsonPrimitive(here.path)
        b.now += 5
        b.observe()
        a.now += 50
        a.station("s2")!!["local_path"] = JsonPrimitive("/home/user/Music")
        sync(a, b)
        assertEquals(here.path, b.station("s2")!!["local_path"].str())
        // A folder that isn't on this device is replaced by the incoming one.
        b.station("s1")!!["local_path"] = JsonPrimitive("/nowhere/at/all")
        b.now += 5; b.observe()
        a.now += 50
        a.station("s1")!!["local_path"] = JsonPrimitive("/home/user/Other")
        sync(a, b)
        assertEquals("/home/user/Other", b.station("s1")!!["local_path"].str())
    }

    @Test fun thisDevicesOwnKeysAreNeverSent() {
        val a = Dev(A)
        val s = st("s1", "http://one")
        s["custom_art_path"] = JsonPrimitive("/x.png"); s["connect_worst"] = floatJson(3.0)
        s["art_checked"] = JsonPrimitive(true); s[LocalStations.ANDROID_FOLDER] = JsonPrimitive("content://tree")
        a.stations += s
        val f = ((a.state()["stations"] as JsonArray)[0] as JsonObject)["f"] as JsonObject
        for (k in SyncData.NEVER_SENT) assertFalse(k, k in f)
        assertEquals(JsonNull, (f["art"] as JsonArray)[0])        // no file at /x.png
        // An incoming phone folder link is never taken.
        val b = Dev(B)
        val forged = JsonObject(mapOf("stations" to JsonArray(listOf(JsonObject(mapOf("id" to JsonPrimitive("s1"), "f" to JsonObject(mapOf(
            "url" to JsonArray(listOf(JsonPrimitive("http://one"), JsonPrimitive(5), JsonPrimitive(A))),
            LocalStations.ANDROID_FOLDER to JsonArray(listOf(JsonPrimitive("content://x"), JsonPrimitive(5), JsonPrimitive(A))),
        ))))))))
        b.take(forged)
        assertEquals("", b.station("s1")!![LocalStations.ANDROID_FOLDER].str().orEmpty())
    }

    @Test fun picturesTravelBySha() {
        val (a, b) = pair()
        val pic = File(a.art, "s1-1-cover.JPG").apply { writeBytes(byteArrayOf(1, 2, 3, 4)) }
        a.station("s1")!!["custom_art_path"] = JsonPrimitive(pic.path)
        a.now += 10
        val res = b.take(a.state())
        val sha = SyncCrypto.sha256Hex(byteArrayOf(1, 2, 3, 4))
        assertEquals(listOf("s1"), res.needArt[sha])
        assertEquals("jpg", b.store.pendingArt["s1"]!!["ext"].str())
        // While it waits, B sends A's art value, not its own (none).
        val bArt = (((b.state()["stations"] as JsonArray).first { (it as JsonObject)["id"].str() == "s1" } as JsonObject)["f"] as JsonObject)["art"] as JsonArray
        assertEquals(sha, (bArt[0] as JsonObject)["sha"].str())
        // The picture arrives.
        val saved = SyncData.saveArt(b.art, sha, "jpg", pic.readBytes())
        b.station("s1")!!["custom_art_path"] = JsonPrimitive(saved.path)
        b.store.pendingArt.remove("s1")
        assertFalse(b.observe())               // same sha and ext as the stamp says
        // A removes it: B drops its copy too.
        a.now += 10
        a.station("s1")!!["custom_art_path"] = JsonPrimitive("")
        b.take(a.state())
        assertEquals("", b.station("s1")!!.customArtPath)
    }

    @Test fun aGoneStationLeavesATombstoneAndOldOnesExpire() {
        val a = Dev(A)
        a.stations += st("s1", "http://one")
        a.observe()
        a.stations.clear()
        a.now += 1
        assertTrue(a.observe())
        assertTrue("s1" in a.store.tomb)
        a.now += 366L * 86_400_000L
        a.observe()
        assertFalse("s1" in a.store.tomb)
    }

    @Test fun theStoreSurvivesItsFile() {
        val (a, _) = pair()
        a.store.pendingArt["s1"] = JsonObject(mapOf("sha" to JsonPrimitive("ab"), "ext" to JsonPrimitive("png")))
        a.store.tomb["gone"] = SyncData.Stamp(5, B)
        a.store.cleared = SyncData.Stamp(7, A)
        val back = SyncData.Store.from(FreqJson.parseToJsonElement(encodeJson(a.store.toJson())))
        assertEquals(encodeJson(a.store.toJson()), encodeJson(back.toJson()))
    }
}
