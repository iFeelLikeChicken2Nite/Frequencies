package com.frequencies.core

import java.io.File
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.file.Files
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test

/**
 * The phone's sync against the desktop's own code: tools/sync_desktop_peer.py
 * runs desktop build 160's sync_core and storage (pairing, crypto, merge,
 * files) as the other device, over real TCP on this machine. Only when
 * given the desktop's source:
 *
 *     gradlew :core:test --tests '*SyncDesktopTest*' -Pfrequencies.desktop=<desktop>/src
 */
class SyncDesktopTest {
    private val src = System.getProperty("frequencies.desktop").orEmpty()
    private val tools = System.getProperty("frequencies.tools").orEmpty()
    private val code = "ABCDE12345"
    private val procs = ArrayList<Process>()

    @After fun stop() = procs.forEach { it.destroy() }

    /** A desktop: the peer script with its own config folder. */
    inner class Desktop(emptyLibrary: Boolean) {
        val work: File = Files.createTempDirectory("desk").toFile()
        val config = File(work, "config/frequencies")
        val port: Int
        val id: String

        init {
            assumeTrue("needs -Pfrequencies.desktop=<desktop>/src", src.isNotEmpty() && File(src, "sync_core.py").isFile)
            config.mkdirs()
            if (emptyLibrary) File(config, "stations.json").writeText("[]")
            val p = ProcessBuilder("python3", "-I", File(tools, "sync_desktop_peer.py").path, src, work.path, code)
                .redirectErrorStream(true).redirectOutput(File(work, "out.txt")).start()
            procs += p
            val portFile = File(work, "port")
            val until = System.currentTimeMillis() + 20_000
            while (!portFile.isFile && System.currentTimeMillis() < until) {
                assertTrue("desktop peer died: " + File(work, "out.txt").readText(), p.isAlive)
                Thread.sleep(50)
            }
            val (portText, idText) = portFile.readText().trim().split(" ")
            port = portText.toInt(); id = idText
        }

        val addr get() = listOf(InetSocketAddress("127.0.0.1", port))
        fun log(): List<String> = File(work, "sessions.log").takeIf { it.isFile }?.readLines().orEmpty()
        fun stations(): List<JsonObject> = (readJson(File(config, "stations.json")) as JsonArray).map { it as JsonObject }
        fun settings(): JsonObject = readJson(File(config, "settings.json")) as JsonObject
        fun history(): List<JsonObject> = (readJson(File(config, "history.json")) as? JsonArray)?.map { it as JsonObject }.orEmpty()
        fun writeStations(list: List<JsonObject>) = File(config, "stations.json").writeText(encodeJson(JsonArray(list)))
        fun writeSettings(o: JsonObject) = File(config, "settings.json").writeText(encodeJson(o))
    }

    /** A phone: a plain library in memory and the app's own SyncPhone. */
    class Phone(val me: String, clock: () -> Long = System::currentTimeMillis) {
        val dir: File = Files.createTempDirectory("phone").toFile()
        val artDir = File(dir, "artwork")
        @Volatile var stations: List<Station> = emptyList()
        @Volatile var settings: Settings = Settings.from(null)
        @Volatile var history: JsonArray = JsonArray(emptyList())
        val store = SyncData.Store()
        var key = ByteArray(0)
        val taken = ArrayList<SyncData.Merged>()
        /** This phone's what-to-sync choice for the desktop (section 6.4). */
        @Volatile var what = SyncData.What.ALL
        private val lib = object : SyncLibrary {
            override fun <T> edit(block: (SyncEdit) -> T): T = synchronized(this) {
                val e = SyncEdit(stations.map { it.copy() }.toMutableList(), Settings(LinkedHashMap(settings.data)),
                    SongHistory.entries(history).toMutableList())
                val r = block(e)
                if (e.stationsChanged) stations = e.stations.toList()
                if (e.settingsChanged) settings = e.settings
                if (e.historyChanged) history = JsonArray(e.history)
                r
            }
        }
        val phone = SyncPhone(me, store, {}, artDir, lib, taken = { _, m -> synchronized(taken) { taken += m } },
            clock = clock, what = { what })
    }

    private fun pair(p: Phone, d: Desktop) {
        // Wait for this pairing's own line (a second phone's pairing used to see the first one's).
        val pairedBefore = d.log().count { it.startsWith("paired") }
        Socket("127.0.0.1", d.port).use { s ->
            s.soTimeout = 15_000
            val paired = SyncWire.clientPair(s.getInputStream().buffered(), s.getOutputStream(), p.me, "Test Phone", code)
            assertEquals(d.id, paired.id)
            assertEquals("Test Desktop", paired.name)
            p.key = paired.key
        }
        waitFor { d.log().count { it.startsWith("paired") } > pairedBefore }
    }

    /** One short session, as the app runs it: open, exchange, settle, bye. */
    private fun session(p: Phone, d: Desktop): List<SyncData.Merged> {
        val before = d.log().size
        synchronized(p.taken) { p.taken.clear() }
        val s = SyncSession(p.phone, d.id)
        var reason = ""
        val t = thread { reason = s.run(d.addr, p.me, "Test Phone", p.key) }
        waitFor { s.settled(1_200) }
        s.close("bye")
        t.join(10_000)
        assertEquals("closed", reason)
        waitFor { d.log().size > before }
        assertTrue(d.log().last(), d.log().last().startsWith("session"))
        return synchronized(p.taken) { p.taken.toList() }
    }

    private fun waitFor(ms: Long = 20_000, ok: () -> Boolean) {
        val until = System.currentTimeMillis() + ms
        while (!ok()) {
            if (System.currentTimeMillis() > until) throw AssertionError("timed out")
            Thread.sleep(50)
        }
    }

    private fun phoneFromBackup(me: String): Phone {
        val p = Phone(me)
        val text = javaClass.getResource("/desktop/desktop-backup.json")!!.readText()
        val c = Backup.read(text, p.artDir)
        p.stations = c.stations
        p.settings = c.settings
        p.history = c.history
        // Things only this phone has.
        p.settings.accentMode = Settings.ACCENT_MATERIAL_YOU
        p.settings["android_moving_bg"] = JsonPrimitive("off")
        p.settings["theme"] = JsonPrimitive("vapor")
        p.stations.first { it["local_path"].str().orEmpty().isNotEmpty() }[LocalStations.ANDROID_FOLDER] =
            JsonPrimitive("content://com.android.externalstorage.documents/tree/primary%3AMusic")
        return p
    }

    private fun picSha(path: String): String? = File(path).takeIf { it.isFile }?.let { SyncCrypto.sha256Hex(it.readBytes()) }

    /** Every field that travels (section 6.1), by meaning (700 is 700.0). */
    private fun travelling(o: Map<String, kotlinx.serialization.json.JsonElement>) =
        o.filterKeys { it !in SyncData.NEVER_SENT }.mapValues { SyncData.fingerprint(it.value) }

    @Test fun phoneToDesktopToPhoneLosesNothing() {
        val d = Desktop(emptyLibrary = true)
        val a = phoneFromBackup("a1".repeat(16))
        pair(a, d)
        session(a, d)

        // The desktop has every station whole: same ids, fields, play counts, pictures.
        val desk = d.stations().associateBy { it["id"].str()!! }
        assertEquals(a.stations.map { it.id }.toSet(), desk.keys)
        for (s in a.stations) {
            val o = desk.getValue(s.id)
            assertEquals(s.name, travelling(s.data) - LocalStations.ANDROID_FOLDER, travelling(o).filterKeys { it in s.data })
            assertEquals(s.playCount, o["play_count"].num()!!.toLong())
            assertEquals(s.name, picSha(s.customArtPath), picSha(o["custom_art_path"].str().orEmpty()))
            assertFalse(LocalStations.ANDROID_FOLDER in o)
        }
        assertTrue(a.stations.sumOf { it.playCount } > 0)
        assertEquals(13, a.stations.count { picSha(it.customArtPath) != null })   // the backup phone's 13
        // Shared settings came over; the phone's own didn't.
        val ds = d.settings()
        assertEquals("vapor", ds["theme"].str())
        assertEquals("desktop", ds["accent_source"].str())      // Material You is "Match my desktop" there
        assertNull(ds[Settings.ANDROID_ACCENT]); assertNull(ds["android_moving_bg"])
        // History and its star.
        assertEquals(SongHistory.entries(a.history).map { SongHistory.entryKey(it) to SongHistory.isFavorite(it) },
            d.history().map { SongHistory.entryKey(it) to SongHistory.isFavorite(it) })

        // A second, fresh phone gets it all back from the desktop.
        val b = Phone("b2".repeat(16))
        b.stations = emptyList()
        pair(b, d)
        session(b, d)
        assertEquals(a.stations.map { it.id }.sorted(), b.stations.map { it.id }.sorted())
        val bById = b.stations.associateBy { it.id }
        for (s in a.stations) {
            val o = bById.getValue(s.id)
            assertEquals(s.name, travelling(s.data) - LocalStations.ANDROID_FOLDER, travelling(o.data).filterKeys { it in s.data } - LocalStations.ANDROID_FOLDER)
            assertEquals(s.name, picSha(s.customArtPath), picSha(o.customArtPath))
            assertTrue(o.customArtPath.isEmpty() || o.customArtPath.startsWith(b.artDir.path))
            // One picture file per station (build 167), even when two share a picture.
            if (o.customArtPath.isNotEmpty()) assertTrue(o.customArtPath, File(o.customArtPath).name.startsWith("sync-" + o.id.filter { it.isLetterOrDigit() || it == '-' }.take(40) + "-"))
        }
        // The music folder station keeps the desktop's folder ("Not on this phone" on this one).
        val local = a.stations.first { it["local_path"].str().orEmpty().isNotEmpty() }
        assertEquals("/home/user/Music", bById.getValue(local.id)["local_path"].str())
        assertEquals("", bById.getValue(local.id)[LocalStations.ANDROID_FOLDER].str().orEmpty())
        assertEquals("vapor", b.settings.string("theme"))
        assertEquals(SongHistory.entries(a.history).map { SongHistory.entryKey(it) to SongHistory.isFavorite(it) },
            SongHistory.entries(b.history).map { SongHistory.entryKey(it) to SongHistory.isFavorite(it) })

        // Back on the first phone: nothing new, nothing lost, nothing sent back and forth.
        val stationsBefore = a.stations.map { travelling(it.data) }
        val again = session(a, d)
        assertTrue(again.isNotEmpty())
        assertTrue(again.none { it.changed })
        assertEquals(stationsBefore, a.stations.map { travelling(it.data) })
        assertEquals("session []", d.log().last())
        assertEquals(local["android_folder"], a.stations.first { it.id == local.id }[LocalStations.ANDROID_FOLDER])

        // The 10-band switch travels since build 167, both ways.
        a.settings = Settings(LinkedHashMap(a.settings.data)).also { it["eq_advanced"] = JsonPrimitive(true) }
        session(a, d)
        assertEquals("true", d.settings()["eq_advanced"].str())
        session(b, d)
        assertEquals("true", b.settings.data["eq_advanced"].str())
    }

    @Test fun editsDeletesAndStarsGoBothWays() {
        val d = Desktop(emptyLibrary = true)
        val a = phoneFromBackup("a1".repeat(16))
        pair(a, d)
        session(a, d)
        a.phone.observeNow()

        // On the desktop: a rename and a delete (the desktop dates them itself).
        Thread.sleep(20)
        val list = d.stations().toMutableList()
        val renamedId = list[0]["id"].str()!!
        val deletedId = list[1]["id"].str()!!
        list[0] = JsonObject(list[0] + ("custom_name" to JsonPrimitive("Desk name")))
        list.removeAt(1)
        d.writeStations(list)
        // On the phone: a tag on another station and a star.
        val taggedId = a.stations[3].id
        a.stations = a.stations.map { if (it.id == taggedId) it.copy().apply { this["tags"] = stringArray(listOf("synced")) } else it }
        a.history = SongHistory.setFavorite(a.history, SongHistory.trackKey("New Artist", "New Song"), true, 1_800_000_000.0,
            title = "New Song", artist = "New Artist")!!
        session(a, d)

        assertEquals("Desk name", a.stations.first { it.id == renamedId }.customName)
        assertNull(a.stations.firstOrNull { it.id == deletedId })
        val desk = d.stations().associateBy { it["id"].str()!! }
        assertNull(desk[deletedId])
        assertEquals(listOf("synced"), (desk.getValue(taggedId)["tags"] as JsonArray).map { it.str() })
        assertTrue(d.history().any { SongHistory.entryKey(it) == "new artist|new song" && SongHistory.isFavorite(it) })

        // The phone unstars it; the desktop follows and it doesn't come back.
        a.history = SongHistory.setFavorite(a.history, "new artist|new song", false, 0.0)!!
        session(a, d)
        assertFalse(d.history().any { SongHistory.entryKey(it) == "new artist|new song" && SongHistory.isFavorite(it) })
        session(a, d)
        assertFalse(SongHistory.entries(a.history).any { SongHistory.entryKey(it) == "new artist|new song" && SongHistory.isFavorite(it) })
    }

    @Test fun bothStartedWithTheDefaultStations() {
        // The desktop makes its default frequencies on first start; the phone restored the
        // same stations from a backup under other ids. Each pair becomes one station.
        val d = Desktop(emptyLibrary = false)
        val a = phoneFromBackup("a1".repeat(16))
        val urls = a.stations.map { SyncData.normUrl(it.url) }.toSet()
        pair(a, d)
        session(a, d)
        session(a, d)
        val desk = d.stations()
        assertEquals(desk.map { it["id"].str().orEmpty() }.sorted(), a.stations.map { it.id }.sorted())
        assertEquals(desk.size, desk.map { SyncData.normUrl(it["url"].str()) }.toSet().size)
        assertTrue(urls.all { u -> desk.any { SyncData.normUrl(it["url"].str()) == u } })
        // Whose fields won per station is the protocol's newest-stamp rule; report it.
        val counts = a.stations.sumOf { it.playCount }
        println("[twins] ${desk.size} stations, phone play counts after: $counts")
    }

    @Test fun twinsKeepBothSidesDetailsWithTheDesktop() {
        // Build 164's first-sync bug against the desktop's own _join_twins (build 165): the same
        // station on both, other play counts, tags and times; both end up with the same, whole station.
        val d = Desktop(emptyLibrary = true)
        d.writeStations(listOf(JsonObject(linkedMapOf(
            "id" to JsonPrimitive("zzzz-desk"), "url" to JsonPrimitive("http://twin.example/stream/"),
            "name" to JsonPrimitive("Twin FM"), "play_count" to intJson(12), "last_played" to floatJson(1_700_000_000.5),
            "tags" to stringArray(listOf("Jazz", "Desk only")),
        ))))
        val a = Phone("a1".repeat(16))
        a.stations = listOf(Station.normalize(Station(linkedMapOf(
            "id" to JsonPrimitive("aaaa-phone"), "url" to JsonPrimitive("HTTP://twin.example/stream"),
            "name" to JsonPrimitive("Twin FM"), "play_count" to intJson(4), "last_played" to floatJson(1_800_000_000.25),
            "tags" to stringArray(listOf("jazz", "Phone only")),
        )), 1.0) { "x" })
        pair(a, d)
        session(a, d)
        session(a, d)
        val desk = d.stations().single()
        val phone = a.stations.single()
        assertEquals("aaaa-phone", desk["id"].str()); assertEquals("aaaa-phone", phone.id)
        assertEquals(12L, phone.playCount); assertEquals(12L, desk["play_count"].num()!!.toLong())
        assertEquals(1_800_000_000.25, phone["last_played"].num()!!, 0.0)
        assertEquals(1_800_000_000.25, desk["last_played"].num()!!, 0.0)
        assertEquals(listOf("jazz", "Phone only", "Desk only"), phone.tags)
        assertEquals(listOf("jazz", "Phone only", "Desk only"), (desk["tags"] as JsonArray).map { it.str() })
        assertEquals(travelling(phone.data), travelling(desk).filterKeys { it in phone.data })
        // Settled: one more session sends nothing either way.
        val again = session(a, d)
        assertTrue(again.none { it.changed })
        assertEquals("session []", d.log().last())
    }

    @Test fun customStationFrequencyNeverTravels() {
        // Removed everywhere (user, 2026-11-04): a desktop file that still has it sends nothing, the phone neither.
        val d = Desktop(emptyLibrary = true)
        val a = phoneFromBackup("a1".repeat(16))
        a.settings["local_station_ratio"] = intJson(40)       // as an old file would hold it
        pair(a, d)
        session(a, d)
        assertNull(d.settings()["local_station_ratio"])
        assertEquals(JsonPrimitive(40), a.settings["local_station_ratio"])    // never taken or sent, untouched
    }

    @Test fun anUnpairedPhoneGetsNothingAndAnotherVersionIsRefused() {
        val d = Desktop(emptyLibrary = true)
        // Not paired: the desktop closes without a word.
        Socket("127.0.0.1", d.port).use { s ->
            s.soTimeout = 10_000
            SyncWire.sendJson(s.getOutputStream(), SyncWire.msg("t" to "hello", "v" to 1, "id" to "c3".repeat(16),
                "name" to "Stranger", "nonce" to SyncWire.b64(ByteArray(16))))
            assertEquals(-1, s.getInputStream().read())
        }
        // A pairing with the wrong code fails on both sides.
        Socket("127.0.0.1", d.port).use { s ->
            s.soTimeout = 15_000
            try {
                SyncWire.clientPair(s.getInputStream().buffered(), s.getOutputStream(), "c3".repeat(16), "Phone", "ZZZZZ99999")
                throw AssertionError("paired with a wrong code")
            } catch (e: SyncWire.SyncError) {
                assertEquals("wrong code", e.message)
            }
        }
        // A pair message in version 2: the desktop closes the connection.
        Socket("127.0.0.1", d.port).use { s ->
            s.soTimeout = 10_000
            SyncWire.sendJson(s.getOutputStream(), SyncWire.msg("t" to "pair", "v" to 2, "id" to "c3".repeat(16),
                "name" to "Future", "pub" to SyncWire.b64(ByteArray(65))))
            assertEquals(-1, s.getInputStream().read())
        }
        assertNotNull(d.log())
        TimeUnit.MILLISECONDS.sleep(100)
    }

    // -- desktop build 169 (Batch CN): untouched values and What to sync ---------

    private val firstUrl by lazy { Featured.readList(Defaults.defaultStationsText()).first()["url"].str()!! }

    /** A phone with the default frequencies as it ships them. */
    private fun freshPhone(me: String, clock: () -> Long = System::currentTimeMillis): Phone {
        val p = Phone(me, clock)
        var n = 0
        p.stations = LibraryFiles(p.dir, newId = { "${me.take(4)}-phone-${n++}" }).defaultStations()
        return p
    }

    /** The phone the user set up: theme, crossfade, clock, a default frequency tuned and renamed. */
    private fun setUpPhone(me: String, clock: () -> Long): Phone = freshPhone(me, clock).apply {
        settings = Settings(LinkedHashMap(settings.data)).also {
            it["theme"] = JsonPrimitive("vapor")
            it["crossfade_ms"] = intJson(1500)
            it["clock_12h"] = JsonPrimitive(false)
        }
        stations = stations.map { s ->
            if (SyncData.normUrl(s.url) != SyncData.normUrl(firstUrl)) s
            else s.copy().apply {
                this["eq_bass"] = intJson(5); this["gain_db"] = floatJson(3.0)
                this["custom_name"] = JsonPrimitive("My name"); this["tags"] = stringArray(listOf("mine"))
            }
        }
        phone.observeNow()
    }

    private fun deskStation(d: Desktop, url: String) = d.stations().first { SyncData.normUrl(it["url"].str()) == SyncData.normUrl(url) }

    @Test fun aFreshDesktopsDefaultsDontBeatThePhonesChanges() {
        // Build 168's bug: the phone's changes were dated an hour before the new computer first looked,
        // and the computer's defaults won. With desktop 169 and this phone, the changes win.
        val d = Desktop(emptyLibrary = false)
        val a = setUpPhone("a1".repeat(16)) { System.currentTimeMillis() - 3_600_000 }
        pair(a, d)
        session(a, d)
        session(a, d)
        val ds = d.settings()
        assertEquals("vapor", ds["theme"].str())
        assertEquals(1500L, ds["crossfade_ms"].num()!!.toLong())
        assertEquals("false", ds["clock_12h"].str())
        val desk = deskStation(d, firstUrl)
        assertEquals(5L, desk["eq_bass"].num()!!.toLong())
        assertEquals(3.0, desk["gain_db"].num()!!, 0.0)
        assertEquals("My name", desk["custom_name"].str())
        assertTrue((desk["tags"] as JsonArray).any { it.str() == "mine" })
        // The phone kept them too.
        assertEquals("vapor", a.settings.string("theme"))
        assertEquals("My name", a.stations.first { SyncData.normUrl(it.url) == SyncData.normUrl(firstUrl) }.customName)
    }

    @Test fun aFreshPhoneTakesTheSetUpDesktopsChanges() {
        val d = Desktop(emptyLibrary = false)
        d.writeSettings(JsonObject(mapOf("theme" to JsonPrimitive("paper"), "crossfade_ms" to intJson(1200), "noise_volume" to intJson(35))))
        // The desktop's frequencies: the defaults, one tuned there.
        val shipped = freshPhone("d0".repeat(16)).stations
        d.writeStations(shipped.mapIndexed { i, s ->
            val o = LinkedHashMap(s.data); o["id"] = JsonPrimitive("desk-$i")
            if (SyncData.normUrl(s.url) == SyncData.normUrl(firstUrl)) { o["eq_treble"] = intJson(-4); o["custom_name"] = JsonPrimitive("Desk name") }
            JsonObject(o)
        })
        // A brand-new phone, its clock an hour ahead: its defaults still don't win.
        val b = freshPhone("b2".repeat(16)) { System.currentTimeMillis() + 3_600_000 }
        pair(b, d)
        session(b, d)
        session(b, d)
        assertEquals("paper", b.settings.string("theme"))
        assertEquals(1200.0, b.settings.double("crossfade_ms", 0.0), 0.0)
        assertEquals(35.0, b.settings.double("noise_volume", 0.0), 0.0)
        val s = b.stations.first { SyncData.normUrl(it.url) == SyncData.normUrl(firstUrl) }
        assertEquals(-4.0, s["eq_treble"].num()!!, 0.0)
        assertEquals("Desk name", s.customName)
        assertEquals("paper", d.settings()["theme"].str())
    }

    @Test fun whatToSyncLeavesTheUncheckedPartsAlone() {
        val d = Desktop(emptyLibrary = true)
        d.writeSettings(JsonObject(mapOf("theme" to JsonPrimitive("paper"))))
        val a = phoneFromBackup("a1".repeat(16))
        a.settings["crossfade_ms"] = intJson(1300)
        // Look and History off for this desktop: neither sent nor taken.
        a.what = SyncData.cleanWhat(FreqJson.parseToJsonElement("""{"history":false,"groups":{"look":false}}"""))
        pair(a, d)
        session(a, d)
        assertEquals("paper", d.settings()["theme"].str())
        assertEquals("vapor", a.settings.string("theme"))
        assertEquals(1300L, d.settings()["crossfade_ms"].num()!!.toLong())
        assertTrue(d.history().isEmpty())
        assertEquals(a.stations.size, d.stations().size)
        // Station pictures off too: the stations come, their pictures don't.
        val b = Phone("b2".repeat(16))
        b.stations = emptyList()
        b.what = SyncData.cleanWhat(JsonObject(mapOf("art" to JsonPrimitive(false))))
        pair(b, d)
        session(b, d)
        assertEquals(a.stations.size, b.stations.size)
        assertTrue(b.stations.all { it.customArtPath.isEmpty() })
        assertTrue(b.phone.waitingArt().isEmpty())
        // Turned on again: the pictures follow.
        b.what = SyncData.What.ALL
        session(b, d)
        assertEquals(13, b.stations.count { picSha(it.customArtPath) != null })   // the backup phone's 13
    }

    @Test fun aPhoneWithTheOldAddressesJoinsTheDesktopsDefaults() {
        // Builds 145-172 shipped New Age Radio and Dare-FM without their query strings; desktop 171
        // and this phone (build 173) join each pair once, and both keep the shipped address.
        val d = Desktop(emptyLibrary = false)
        val a = freshPhone("a1".repeat(16))
        a.stations = a.stations.map { st -> if ("?" in st.url && !st.url.startsWith("youtube://")) st.copy().apply { this["url"] = JsonPrimitive(st.url.substringBefore("?")) } else st }
        a.phone.observeNow()
        pair(a, d)
        session(a, d)
        session(a, d)
        val desk = d.stations()
        assertEquals(desk.map { it["id"].str().orEmpty() }.sorted(), a.stations.map { it.id }.sorted())
        // A fresh desktop 174 and this phone: the 19 defaults once each, the six YouTube ones joined as twins.
        assertEquals(19, a.stations.size)
        assertEquals(19, a.stations.map { SyncData.normUrl(it.url) }.toSet().size)
        assertEquals(6, a.stations.count { it.url.startsWith("youtube://") })
        // Only New Age Radio and Dare-FM: the YouTube defaults' "?" addresses are not fixes.
        assertEquals(2, SyncData.addressFixes().size)
        for (right in SyncData.addressFixes().values) {
            assertEquals(1, a.stations.count { SyncData.normUrl(it.url).substringBefore("?") == SyncData.normUrl(right).substringBefore("?") })
            assertTrue(a.stations.any { it.url == right })
            assertTrue(desk.any { it["url"].str() == right })
        }
    }
    @Test fun shuffleIsNeverFlippedByAFreshDevice() {
        // Shuffle on by default since desktop 181 / phone 182 (task 232), new profiles only.
        // 1. Fresh desktop, fresh phone: on, and it stays on.
        val d1 = Desktop(emptyLibrary = false)
        val a = freshPhone("a1".repeat(16))
        assertTrue(Shuffle.on(a.settings))
        pair(a, d1)
        session(a, d1)
        session(a, d1)
        assertTrue(Shuffle.on(a.settings))
        assertEquals("true", d1.settings()["shuffle"].str())

        // 2. A desktop profile from before 181 (its settings.json never saved shuffle) and a fresh phone: off on both.
        val d2 = Desktop(emptyLibrary = false)
        d2.writeSettings(JsonObject(mapOf("theme" to JsonPrimitive("paper"))))
        val b = freshPhone("b2".repeat(16)) { System.currentTimeMillis() + 3_600_000 }
        pair(b, d2)
        session(b, d2)
        session(b, d2)
        assertFalse(Shuffle.on(b.settings))
        assertEquals("false", d2.settings()["shuffle"].str())

        // 3. A phone from before 182: shuffle off, first looked at when off was the default ([0, me]).
        //    A fresh desktop's untouched "on" doesn't flip it, and the desktop takes the phone's off.
        val d3 = Desktop(emptyLibrary = false)
        val c = freshPhone("c3".repeat(16))
        c.settings = Settings(LinkedHashMap(c.settings.data)).also { it[Shuffle.SETTING] = JsonPrimitive(false) }
        c.store.set[Shuffle.SETTING] = SyncData.Rec(SyncData.Stamp(0, c.me), SyncData.fingerprint(JsonPrimitive(false)))
        c.phone.observeNow()
        assertEquals(SyncData.Stamp(1, c.me), c.store.set[Shuffle.SETTING]!!.stamp)
        pair(c, d3)
        session(c, d3)
        session(c, d3)
        assertFalse(Shuffle.on(c.settings))
        assertEquals("false", d3.settings()["shuffle"].str())

        // 4. A real edit on the phone still travels.
        c.settings = Settings(LinkedHashMap(c.settings.data)).also { it[Shuffle.SETTING] = JsonPrimitive(true) }
        session(c, d3)
        session(c, d3)
        assertEquals("true", d3.settings()["shuffle"].str())
        assertTrue(Shuffle.on(c.settings))
    }

    @Test fun adCoverOnPlainStationsTravelsBothWays() {
        // Android batch 25: the ad cover on any station (desktop 181), the desktop's keys in the station's fields.
        val d = Desktop(emptyLibrary = true)
        val a = phoneFromBackup("a1".repeat(16))
        pair(a, d)
        session(a, d)
        a.phone.observeNow()
        val plain = a.stations.filter { Twitch.adCoverOffered(it) && !Twitch.isTwitch(it) && it.url.startsWith("http") }
        assertTrue("needs three plain streams in the backup", plain.size >= 3)
        val phoneEdited = plain[0].id
        val coverId = plain[1].id
        val deskEdited = plain[2].id

        // On the phone: Station settings > Sound turns the cover on, 22 s, covered by a chosen frequency.
        Thread.sleep(20)
        a.stations = a.stations.map { s ->
            if (s.id != phoneEdited) s else s.copy().apply {
                this["ad_override"] = JsonPrimitive(true)
                this["ad_seconds"] = floatJson(22.0)
                this["ad_filler_kind"] = JsonPrimitive("chosen")
                this["ad_filler_station_id"] = JsonPrimitive(coverId)
                this["ad_filler_url"] = JsonPrimitive("")
            }
        }
        // On the desktop: its dialog's values for another plain station.
        val list = d.stations().map { o ->
            if (o["id"].str() != deskEdited) o
            else JsonObject(o + mapOf("ad_override" to JsonPrimitive(true), "ad_seconds" to floatJson(9.0),
                "ad_filler_kind" to JsonPrimitive("any"), "ad_filler_station_id" to JsonPrimitive("")))
        }
        d.writeStations(list)
        session(a, d)
        session(a, d)

        val desk = d.stations().associateBy { it["id"].str()!! }
        val dp = desk.getValue(phoneEdited)
        assertEquals("true", dp["ad_override"].str())
        assertEquals(22.0, dp["ad_seconds"].num()!!, 0.0)
        assertEquals("chosen", dp["ad_filler_kind"].str())
        assertEquals(coverId, dp["ad_filler_station_id"].str())
        val pd = a.stations.first { it.id == deskEdited }
        assertTrue(Twitch.coversAd(pd))
        assertEquals(9.0, Twitch.adCoverSeconds(pd), 0.0)
        assertEquals("any", pd["ad_filler_kind"].str())
        assertTrue(Twitch.coversAd(a.stations.first { it.id == phoneEdited }))

        // Turning it off on the phone wins over the older "on" and doesn't come back.
        Thread.sleep(20)
        a.stations = a.stations.map { s -> if (s.id != deskEdited) s else s.copy().apply { this["ad_override"] = JsonPrimitive(false) } }
        session(a, d)
        session(a, d)
        assertEquals("false", d.stations().first { it["id"].str() == deskEdited }["ad_override"].str())
        assertFalse(Twitch.coversAd(a.stations.first { it.id == deskEdited }))

        // A second phone gets the same ad fields as the first: nothing lost on the way through the desktop.
        val b = Phone("b2".repeat(16))
        pair(b, d)
        session(b, d)
        session(b, d)
        val adKeys = listOf("ad_override", "ad_seconds", "ad_filler_kind", "ad_filler_station_id", "ad_filler_url")
        for (id in listOf(phoneEdited, deskEdited)) {
            val sa = a.stations.first { it.id == id }
            val sb = b.stations.first { it.id == id }
            for (k in adKeys) assertEquals("$id $k", SyncData.fingerprint(sa[k]), SyncData.fingerprint(sb[k]))
        }
    }
}
