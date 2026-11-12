package com.frequencies.core

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

/**
 * Batch 17 (build 170), desktop Batch CN (build 169, tasks 224 and 225):
 * untouched values are "never edited" ([0, me]) so a fresh device's
 * defaults never beat real changes, the one-time repair of old first-look
 * stamps, and "What to sync" (section 6.4). SyncDesktopTest runs the same
 * against the desktop's own sync_core.
 */
class Batch17Test {
    private val A = "a".repeat(32)
    private val B = "b".repeat(32)

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
            return SyncData.merge(stations, settings, history, store, me, state, 300, now, newId = { "new-$me" }, what = what)
        }
        /** A fresh profile: the default frequencies as the phone ships them, ids of its own. */
        fun fresh(): Dev {
            var n = 0
            stations += LibraryFiles(dir, clock = { now / 1000.0 }, newId = { "$me-${n++}".take(40) }).defaultStations()
            return this
        }
        fun byUrl(url: String) = stations.first { SyncData.normUrl(it.url) == SyncData.normUrl(url) }
    }

    /** A session: both states cross, then each merges (as SyncSession and the desktop do). */
    private fun sync(x: Dev, y: Dev) {
        val sx = x.state(); val sy = y.state()
        y.take(sx); x.take(sy)
        // And once more, as a session settles.
        val sx2 = x.state(); val sy2 = y.state()
        y.take(sx2); x.take(sy2)
    }

    private val firstUrl by lazy { Featured.readList(Defaults.defaultStationsText()).first()["url"].str()!! }

    /** The phone the user set up long ago: changed settings and a default station tuned. */
    private fun setUp(me: String, now: Long): Dev = Dev(me, now).fresh().apply {
        observe()                                            // its first look (paired with something earlier)
        this.now += 60_000
        settings["theme"] = JsonPrimitive("vapor")
        settings["crossfade_ms"] = intJson(1500)
        settings["clock_12h"] = JsonPrimitive(false)
        settings["noise_volume"] = intJson(40)
        val s = byUrl(firstUrl)
        s["eq_bass"] = intJson(5)
        s["gain_db"] = floatJson(3.0)
        s["custom_name"] = JsonPrimitive("My name")
        s["tags"] = stringArray(listOf("mine"))
        observe()
    }

    private fun assertSetUpValues(d: Dev) {
        assertEquals("vapor", d.settings.string("theme"))
        assertEquals(1500.0, d.settings.double("crossfade_ms", 0.0), 0.0)
        assertFalse(d.settings.bool("clock_12h", true))
        assertEquals(40.0, d.settings.double("noise_volume", 0.0), 0.0)
        val s = d.byUrl(firstUrl)
        assertEquals(5.0, s["eq_bass"].num()!!, 0.0)
        assertEquals(3.0, s["gain_db"].num()!!, 0.0)
        assertEquals("My name", s.customName)
        assertEquals(listOf("mine"), s.tags)
    }

    @Test fun untouchedValuesAreStampedNeverEdited() {
        val d = Dev(A, 5_000_000).fresh()
        d.stations += Station.normalize(Station(linkedMapOf("id" to JsonPrimitive("own"), "url" to JsonPrimitive("http://mine/"),
            "name" to JsonPrimitive("Mine"))), 1.0) { "x" }
        d.observe()
        for (key in SyncData.SHARED_SETTINGS) {
            val r = d.store.set[key] ?: continue
            assertEquals(key, 0L, r.stamp.t)
        }
        assertTrue(d.store.set.size >= 20)
        val shipped = d.byUrl(firstUrl)
        for ((field, r) in d.store.st.getValue(shipped.id)) assertEquals(field, 0L, r.stamp.t)
        // A station of the user's own: its name and address are real choices, its defaults aren't.
        val own = d.store.st.getValue("own")
        assertEquals(5_000_000L, own.getValue("url").stamp.t)
        assertEquals(5_000_000L, own.getValue("name").stamp.t)
        assertEquals(0L, own.getValue("eq_bass").stamp.t)
        assertEquals(0L, own.getValue("art").stamp.t)
        // A later real edit gets an ordinary stamp, above the old one.
        d.now += 10
        d.settings["theme"] = JsonPrimitive("vapor")
        d.observe()
        assertEquals(5_000_010L, d.store.set.getValue("theme").stamp.t)
    }

    @Test fun aFreshDevicesDefaultsNeverBeatTheSetUpPhone() {
        // The build 168 bug: the fresh computer looked last, so its defaults won. Both orders.
        for (freshFirst in listOf(true, false)) {
            val phone = setUp(A, 1_000_000)
            val fresh = Dev(B, 9_000_000).fresh()             // made later, looks last
            if (freshFirst) sync(fresh, phone) else sync(phone, fresh)
            assertSetUpValues(phone)
            assertSetUpValues(fresh)
            // Twins joined into one station each, with the smaller id.
            assertEquals(phone.stations.map { it.id }.sorted(), fresh.stations.map { it.id }.sorted())
        }
    }

    @Test fun theSetUpDeviceWithTheBiggerOrSmallerIdKeepsItsChanges() {
        // Ties between two untouched [0, …] stamps go to the bigger id; real stamps always win.
        for ((setUpId, freshId) in listOf(A to B, B to A)) {
            val phone = setUp(setUpId, 1_000_000)
            val fresh = Dev(freshId, 9_000_000).fresh()
            sync(fresh, phone)
            assertSetUpValues(fresh)
        }
    }

    @Test fun laterEditsOnEitherSideStillWin() {
        val phone = setUp(A, 1_000_000)
        val desk = Dev(B, 9_000_000).fresh()
        sync(desk, phone)
        // The fresh one edits later: it wins.
        desk.now += 1_000
        desk.settings["theme"] = JsonPrimitive("paper")
        desk.byUrl(firstUrl)["eq_bass"] = intJson(-3)
        sync(desk, phone)
        assertEquals("paper", phone.settings.string("theme"))
        assertEquals(-3.0, phone.byUrl(firstUrl)["eq_bass"].num()!!, 0.0)
        // Then the phone again, even with its clock behind: an edit is dated after the stamp it replaces.
        phone.settings["theme"] = JsonPrimitive("vapor")
        sync(phone, desk)
        assertEquals("vapor", desk.settings.string("theme"))
        // Setting a value back to its default is a real edit too.
        phone.now = 9_500_000
        phone.settings["crossfade_ms"] = intJson(700)
        sync(phone, desk)
        assertEquals(700.0, desk.settings.double("crossfade_ms", 0.0), 0.0)
    }

    @Test fun oldFirstLookStampsAreRepairedOnce() {
        // A store as builds 164-168 left it: every value dated [first look, me].
        val d = Dev(A, 2_000_000).fresh()
        d.store.untouchedFix = true                           // pretend: old build, no repair, no [0]
        SyncData.observe(d.stations, d.settings, d.history, d.store, A, 2_000_000)
        val firstLook = 2_000_000L
        for ((k, r) in d.store.set) d.store.set[k] = r.copy(stamp = SyncData.Stamp(firstLook, A))
        for (rec in d.store.st.values) for ((f, r) in rec.entries.toList()) rec[f] = r.copy(stamp = SyncData.Stamp(firstLook, A))
        // Later the user changed one setting and one station field.
        d.now = 3_000_000
        d.settings["theme"] = JsonPrimitive("vapor")
        d.byUrl(firstUrl)["eq_mid"] = intJson(4)
        d.observe()
        // A value changed at the first look (not a default) keeps its stamp.
        d.settings["crossfade_ms"] = intJson(900)
        d.store.set["crossfade_ms"] = SyncData.Rec(SyncData.Stamp(firstLook, A), SyncData.fingerprint(intJson(900)))
        // Now the new build looks: the repair runs once.
        d.store.untouchedFix = false
        val json = d.store.toJson()
        assertEquals(JsonPrimitive(0), json["untouched_fix"])
        val again = SyncData.Store.from(json)
        assertTrue(SyncData.observe(d.stations, d.settings, d.history, again, A, 4_000_000))
        assertTrue(again.untouchedFix)
        assertEquals(0L, again.set.getValue("noise_volume").stamp.t)
        assertEquals(0L, again.set.getValue("lyrics_on").stamp.t)
        assertEquals(3_000_000L, again.set.getValue("theme").stamp.t)
        assertEquals(firstLook, again.set.getValue("crossfade_ms").stamp.t)
        val rec = again.st.getValue(d.byUrl(firstUrl).id)
        assertEquals(0L, rec.getValue("eq_bass").stamp.t)
        assertEquals(0L, rec.getValue("added_at").stamp.t)
        assertEquals(0L, rec.getValue("art").stamp.t)
        assertEquals(3_000_000L, rec.getValue("eq_mid").stamp.t)
        // Once only: a value set back to its default later isn't touched by a second look.
        assertEquals(1, SyncData.Store.from(again.toJson()).let { if (it.untouchedFix) 1 else 0 })
        d.settings["theme"] = JsonPrimitive("midnight")
        SyncData.observe(d.stations, d.settings, d.history, again, A, 5_000_000)
        assertEquals(5_000_000L, again.set.getValue("theme").stamp.t)
    }

    @Test fun afterTheRepairAnOldFirstLookNoLongerBeatsARealChange() {
        // A phone on an old build dated its untouched defaults [first look] at 2_000_000; the other
        // device changed crossfade at 1_500_000 (a real edit, older). Unrepaired the phone's 700 wins.
        fun phone(repair: Boolean) = Dev(A, 2_000_000).fresh().apply {
            store.untouchedFix = true
            observe()
            for ((k, r) in store.set) store.set[k] = r.copy(stamp = SyncData.Stamp(2_000_000, A))
            for (rec in store.st.values) for ((f, r) in rec.entries.toList()) rec[f] = r.copy(stamp = SyncData.Stamp(2_000_000, A))
            store.untouchedFix = !repair
            now = 3_000_000
        }
        fun other() = Dev(B, 1_500_000).apply { settings["crossfade_ms"] = intJson(1500); observe(); now = 3_000_000 }
        val old = phone(repair = false); val o1 = other()
        sync(old, o1)
        assertEquals(700.0, o1.settings.double("crossfade_ms", 0.0), 0.0)
        val fixed = phone(repair = true); val o2 = other()
        sync(fixed, o2)
        assertEquals(1500.0, fixed.settings.double("crossfade_ms", 0.0), 0.0)
        assertEquals(1500.0, o2.settings.double("crossfade_ms", 0.0), 0.0)
    }

    // -- what to sync (section 6.4) --------------------------------------------

    @Test fun cleanWhatMatchesTheDesktop() {
        val all = SyncData.cleanWhat(null)
        assertTrue(SyncData.WHAT_PARTS.all { all.on(it) })
        assertEquals(listOf("look", "playback", "lyrics", "list", "desktop"), SyncData.SETTING_GROUPS.map { it.first })
        // Pictures need Saved Frequencies; Settings is off when every group is.
        val noStations = SyncData.cleanWhat(JsonObject(mapOf("stations" to JsonPrimitive(false))))
        assertFalse(noStations.art)
        val noGroups = SyncData.cleanWhat(JsonObject(mapOf("groups" to JsonObject(SyncData.SETTING_GROUPS.associate { it.first to JsonPrimitive(false) }))))
        assertFalse(noGroups.settings)
        // The desktop's own form, round trip.
        val desk = FreqJson.parseToJsonElement("""{"stations":true,"art":false,"settings":true,"favorites":false,"history":true,
            "groups":{"look":false,"playback":true,"lyrics":true,"list":true,"desktop":true}}""")
        val w = SyncData.cleanWhat(desk)
        assertEquals(desk, w.toJson())
        assertFalse(w.settingAllowed("theme")); assertTrue(w.settingAllowed("crossfade_ms"))
        assertEquals(listOf("art", "favorites", "look"), w.offList())
        // Every shared key is in one group, Android has no desktop-only keys.
        val grouped = SyncData.SETTING_GROUPS.flatMap { it.second }
        assertTrue(SyncData.SHARED_SETTINGS.all { it in grouped })
        assertTrue(SyncData.SETTING_GROUPS.first { it.first == "desktop" }.second.none { it in SyncData.SHARED_SETTINGS })
        assertFalse(SyncData.nothingWhat().let { n -> SyncData.WHAT_PARTS.any { n.on(it) } })
    }

    private fun two(): Pair<Dev, Dev> {
        val a = Dev(A, 1_000_000); val b = Dev(B, 1_000_000)
        a.stations += Station.normalize(Station(linkedMapOf("id" to JsonPrimitive("s1"), "url" to JsonPrimitive("http://one/"),
            "name" to JsonPrimitive("One"))), 1.0) { "x" }
        sync(a, b)
        a.now += 100; b.now += 100
        return a to b
    }

    private fun editBoth(a: Dev, b: Dev) {
        val art = java.io.File(a.dir, "pic.png").apply { writeBytes(byteArrayOf(1, 2, 3, 4)) }
        a.stations[0]["custom_name"] = JsonPrimitive("A name")
        a.stations[0]["custom_art_path"] = JsonPrimitive(art.path)
        a.stations += Station.normalize(Station(linkedMapOf("id" to JsonPrimitive("s2"), "url" to JsonPrimitive("http://two/"),
            "name" to JsonPrimitive("Two"))), 1.0) { "x" }
        a.settings["theme"] = JsonPrimitive("vapor")          // Look
        a.settings["crossfade_ms"] = intJson(1200)            // Playback
        a.settings["lyric_size"] = intJson(20)                // Lyrics and artists
        a.settings["sort_mode"] = JsonPrimitive("name")       // Saved Frequencies list
        a.history += JsonObject(linkedMapOf("title" to JsonPrimitive("Song"), "artist" to JsonPrimitive("Band"),
            "station" to JsonPrimitive("One"), "at" to floatJson(1_700_000_000.0), "favorite" to JsonPrimitive(true)))
        b.settings["noise_volume"] = intJson(55)
    }

    @Test fun eachPartOffIsNeitherSentNorTaken() {
        val cases = listOf("stations", "art", "settings", "favorites", "history") + SyncData.SETTING_GROUPS.map { "group:" + it.first }
        for (off in cases) {
            val (a, b) = two()
            val choice = if (off.startsWith("group:")) JsonObject(mapOf("groups" to JsonObject(mapOf(off.drop(6) to JsonPrimitive(false)))))
            else JsonObject(mapOf(off to JsonPrimitive(false)))
            // The phone (a) made the choice for b; b, on an older build, just merges what it gets.
            a.what = SyncData.cleanWhat(choice)
            editBoth(a, b)
            val sent = a.state()
            sync(a, b)
            val stationsOn = off != "stations"
            assertEquals(off, stationsOn, b.stations.any { it.id == "s2" })
            assertEquals(off, if (stationsOn) "A name" else null, b.stations.firstOrNull { it.id == "s1" }?.customName)
            val artSent = (sent["stations"] as JsonArray).any { (it as JsonObject)["f"]?.let { f -> "art" in (f as JsonObject) } == true }
            assertEquals(off, off !in listOf("stations", "art"), artSent)
            assertEquals(off, off !in listOf("settings", "group:look"), b.settings.string("theme") == "vapor")
            assertEquals(off, off !in listOf("settings", "group:playback"), b.settings.double("crossfade_ms", 0.0) == 1200.0)
            assertEquals(off, off !in listOf("settings", "group:lyrics"), b.settings.double("lyric_size", 0.0) == 20.0)
            assertEquals(off, off !in listOf("settings", "group:list"), b.settings.string("sort_mode") == "name")
            // Taken from b only when on (a merges b's noise_volume, Playback).
            assertEquals(off, off !in listOf("settings", "group:playback"), a.settings.double("noise_volume", 0.0) == 55.0)
            assertEquals(off, off != "favorites", b.store.fav.containsKey("band|song"))
            assertEquals(off, off != "history", b.history.isNotEmpty())
            if (off == "history") assertEquals(JsonNull, sent["history_cleared"])
            // Stamps keep being made, so turning it back on merges as usual.
            a.what = SyncData.What.ALL
            sync(a, b)
            assertTrue(off, b.stations.any { it.id == "s2" })
            assertEquals(off, "vapor", b.settings.string("theme"))
            assertEquals(off, 55.0, a.settings.double("noise_volume", 0.0), 0.0)
            assertTrue(off, b.history.isNotEmpty())
        }
    }

    @Test fun nothingWhatSendsAnEmptyState() {
        val (a, b) = two()
        a.what = SyncData.nothingWhat()
        editBoth(a, b)
        val s = a.state()
        assertEquals(JsonArray(emptyList()), s["stations"])
        assertEquals(JsonObject(emptyMap()), s["settings"])
        assertEquals(JsonObject(emptyMap()), s["favorites"])
        assertEquals(JsonArray(emptyList()), s["history"])
        assertFalse(a.take(b.state()).changed)
    }

    @Test fun aTwinDeletedByTheSameStateKeepsThisPhonesPicture() {
        // The other device joined the twins first: its state tombstones this phone's id and brings the kept one.
        val a = Dev(A, 1_000_000)
        val pic = java.io.File(a.dir, "mine.png").apply { writeBytes(byteArrayOf(9, 8, 7)) }
        a.stations += Station.normalize(Station(linkedMapOf("id" to JsonPrimitive("zzz"), "url" to JsonPrimitive("http://same/"),
            "name" to JsonPrimitive("Same"), "custom_art_path" to JsonPrimitive(pic.path))), 1.0) { "x" }
        a.observe()
        val other = Dev(B, 2_000_000)
        // It joined the twins and took this phone's picture (same file, so the same sha).
        val copy = java.io.File(other.dir, "sync-aaa.png").apply { writeBytes(pic.readBytes()) }
        other.stations += Station.normalize(Station(linkedMapOf("id" to JsonPrimitive("aaa"), "url" to JsonPrimitive("http://same"),
            "name" to JsonPrimitive("Same"), "custom_art_path" to JsonPrimitive(copy.path))), 1.0) { "x" }
        other.observe()
        val state = other.state().let { st ->
            JsonObject(st + ("stations" to JsonArray((st["stations"] as JsonArray) + JsonObject(mapOf(
                "id" to JsonPrimitive("zzz"), "del" to SyncData.Stamp(2_000_000, B).json())))))
        }
        a.take(state)
        val kept = a.stations.single()
        assertEquals("aaa", kept.id)
        assertEquals(pic.path, kept.customArtPath)
        assertTrue(a.store.pendingArt.isEmpty())             // not asked for again
        assertNull(a.stations.firstOrNull { it.id == "zzz" })
    }
}
