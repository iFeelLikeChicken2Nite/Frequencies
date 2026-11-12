package com.frequencies.core

import java.io.File
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * health.json, the launch sweep and skipping down stations, against the
 * desktop's storage.py (answers from tools/make_schedule_fixtures.py) and
 * its workers.py / bridge.py rules.
 */
class HealthTest {
    private val answers: JsonObject = FreqJson.parseToJsonElement(
        javaClass.getResourceAsStream("/schedule/answers.json")!!.use { it.readBytes().toString(Charsets.UTF_8) },
    ).jsonObject

    /** Numbers compared as numbers (the desktop's 3000.0 and an int 0 read the same). */
    private fun plain(e: JsonElement): Any? = when (e) {
        is JsonObject -> e.mapValues { plain(it.value) }
        is JsonArray -> e.map { plain(it) }
        is JsonNull -> null
        is JsonPrimitive -> if (e.isString) e.content else e.content.toBooleanStrictOrNull() ?: e.content.toDouble()
    }

    @Test
    fun markingMatchesTheDesktop() {
        val steps = (answers["health_steps"] as JsonArray).map { it.jsonObject }
        var h = JsonObject(mapOf("a" to JsonObject(Health.blank() + ("fails" to JsonPrimitive(1)))))
        h = Health.markFailed(h, "a", "HTTP 404", 1000.5)
        assertEquals(plain(steps[0]), plain(h))
        h = Health.markFailed(h, "a", "", 2000.25)
        assertEquals(plain(steps[1]), plain(h))
        h = Health.markOk(h, "a", 3000.0)
        assertEquals(plain(steps[2]), plain(h))
        h = Health.markFailed(h, "b", "ReadTimeout", 4000.75, downAfter = 3)
        assertEquals(plain(steps[3]), plain(h))
        // Written the way Python writes its floats.
        assertTrue(encodeJson(h).contains("\"last_check\": 4000.75"))
        assertTrue(encodeJson(h).contains("\"last_ok\": 3000.0"))
    }

    @Test
    fun wordsMatchTheDesktop() {
        for ((input, want) in answers["ages"]!!.jsonObject) {
            assertEquals(input, want.str(), Health.humanizeAge(input.toDoubleOrNull()))
        }
        val d = answers["describe"]!!.jsonObject
        val now = 100000.0
        fun one(entry: Map<String, JsonElement>) = Health.describe(JsonObject(mapOf("x" to JsonObject(entry))), "x", now)
        assertEquals(d["up"].str(), one(mapOf("down" to JsonPrimitive(false), "last_ok" to JsonPrimitive(5.0))))
        assertEquals(d["never"].str(), one(mapOf("down" to JsonPrimitive(true), "last_ok" to JsonNull)))
        assertEquals(d["hours"].str(), one(mapOf("down" to JsonPrimitive(true), "last_ok" to JsonPrimitive(now - 3 * 3600 - 5))))
        assertEquals(d["empty"].str(), one(emptyMap()))
        assertEquals("", Health.describe(JsonObject(emptyMap()), "nobody", now))

        val skipped = answers["skipped_when_down"]!!.jsonObject
        fun st(vararg pairs: Pair<String, JsonElement>) = Station(LinkedHashMap(mapOf(*pairs)))
        assertEquals(truthy(skipped["stream"]), Health.skippedWhenDown(st("kind" to JsonPrimitive("stream"))))
        assertEquals(truthy(skipped["ibroadcast_kind"]), Health.skippedWhenDown(st("kind" to JsonPrimitive("ibroadcast"))))
        assertEquals(truthy(skipped["ibroadcast_urls"]), Health.skippedWhenDown(st("ibroadcast_urls" to JsonArray(listOf(JsonPrimitive("http://y"))))))
        assertEquals(truthy(skipped["twitch"]), Health.skippedWhenDown(st("kind" to JsonPrimitive("twitch"))))
    }

    @Test
    fun desktopHealthFileRoundTrips() {
        val text = BackupTest::class.java.getResourceAsStream("/desktop/desktop-backup.json")!!.use { it.readBytes().toString(Charsets.UTF_8) }
        val health = Backup.cleanHealth(FreqJson.parseToJsonElement(text).jsonObject["health"])
        val dir = File(System.getProperty("java.io.tmpdir"), "health-${System.nanoTime()}").apply { mkdirs() }
        val files = LibraryFiles(dir)
        val id = health.keys.first()
        val marked = Health.markFailed(health, id, "HTTP 503", 1792536316.53)
        files.saveHealth(marked)
        val back = files.loadHealth()
        assertEquals(plain(marked), plain(back))
        assertEquals(1, ((back[id] as JsonObject)["fails"].num() ?: 0.0).toInt())   // a key the phone doesn't know, kept
        assertTrue(files.healthFile.readText().contains("1792536316.53"))
        dir.deleteRecursively()
    }

    private fun station(id: String, url: String, kind: String = "stream", extra: Map<String, JsonElement> = emptyMap()) =
        Station(LinkedHashMap(mapOf("id" to JsonPrimitive(id), "url" to JsonPrimitive(url), "kind" to JsonPrimitive(kind)) + extra))

    @Test
    fun launchTargetsFollowTheDesktopsRules() {
        val ibDown = "ib"
        val list = listOf(
            station("plain", "https://a.example/stream"),
            station("local", "/music", kind = "local"),
            station("yt", "https://www.youtube.com/playlist?list=1", kind = "youtube"),
            station("yturl", "https://youtu.be/abc"),
            station(ibDown, "https://ib.example/x", kind = "ibroadcast", extra = mapOf("ibroadcast_urls" to JsonArray(listOf(JsonPrimitive("u"))))),
            station("ibsigned", "https://ib.example/y", kind = "ibroadcast"),
            station("twitch", "https://twitch.tv/x", kind = "twitch"),
            station("ftp", "ftp://old.example/x"),
        )
        var h = JsonObject(emptyMap())
        h = Health.markFailed(h, ibDown, "ConnectionError", 1.0)
        h = Health.markFailed(h, "ibsigned", "signed out of iBroadcast", 1.0)
        h = Health.markFailed(h, "yturl", "HTTP 404", 1.0)
        val sweep = Health.launchTargets(list, h)
        assertEquals(listOf("plain"), sweep.targets.map { it.first })
        // A stale mark from an old check is cleared; one for the sign-in stays.
        assertEquals(listOf("yturl", ibDown), sweep.cleared)
        h = Health.markFailed(h, "plain", "HTTP 500", 2.0)
        assertEquals(listOf("plain"), Health.downTargets(list, h).map { it.first })
    }

    @Test
    fun sweepReportsInOrderAndHoldsFailuresUntilOneAnswers() {
        val targets = (1..6).map { "s$it" to "u$it" }
        val good = setOf("u3", "u5")
        val got = ArrayList<Pair<String, Boolean>>()
        val sum = Health.sweep(targets, probe = { url -> (url in good) to (if (url in good) "" else "ConnectionError") }) { id, ok, _ -> got += id to ok }
        assertEquals(targets.map { it.first to (it.second in good) }, got)
        assertEquals(6, sum.checked)
        assertEquals(2, sum.answered)
        assertFalse(sum.offline)
    }

    @Test
    fun sweepCallsItOfflineAfterEightFailures() {
        val targets = (1..12).map { "s$it" to "u$it" }
        val got = ArrayList<String>()
        val logs = ArrayList<String>()
        val sum = Health.sweep(targets, probe = { url -> (url == "u10") to "" }, log = { logs += it }) { id, _, _ -> got += id }
        assertTrue(sum.offline)
        assertTrue(got.isEmpty())
        assertTrue(logs.last().contains("looks offline, nothing marked"))
        // Fewer than eight, none answering: nothing reported either (the desktop's held results).
        val few = ArrayList<String>()
        Health.sweep(targets.take(3), probe = { false to "x" }) { id, _, _ -> few += id }
        assertTrue(few.isEmpty())
    }

    @Test
    fun sweepStopsWhenAsked() {
        var stop = false
        val got = ArrayList<String>()
        val sum = Health.sweep((1..5).map { "s$it" to "u$it" }, probe = { true to "" }, stopped = { stop }) { id, _, _ ->
            got += id
            if (id == "s2") stop = true
        }
        assertTrue(sum.stopped)
        assertEquals(listOf("s1", "s2"), got)
    }

    @Test
    fun probeWaitsForRealBytes() {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody("ID3 audio bytes"))
        server.enqueue(MockResponse().setResponseCode(404))
        server.enqueue(MockResponse().setBody(""))
        // Since build 205 the probe waits the player's 10 s: 8 s to the first byte is up.
        server.enqueue(MockResponse().setBody("late").setHeadersDelay(8, TimeUnit.SECONDS))
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        server.start()
        try {
            val url = server.url("/stream").toString()
            assertEquals(true to "", Health.probe(url))
            val req = server.takeRequest()
            assertEquals(Playlists.STREAM_AGENT, req.getHeader("User-Agent"))
            assertEquals("1", req.getHeader("Icy-MetaData"))
            assertEquals(false to "HTTP 404", Health.probe(url))
            assertEquals(false to "no audio data", Health.probe(url, retry = false))
            assertEquals(true to "", Health.probe(url, retry = false))
            assertFalse(Health.probe(url, retry = false).first)
        } finally {
            server.shutdown()
        }
        // Nothing listening.
        val closed = MockWebServer().apply { start() }
        val dead = closed.url("/").toString()
        closed.shutdown()
        assertEquals(false to "ConnectionError", Health.probe(dead, retry = false))
        assertEquals(false to "InvalidURL", Health.probe("not a url", retry = false))
    }

    @Test
    fun stepPastDownMatchesBridge() {
        val down = setOf(1, 2)
        // From 0: 1 and 2 are down, lands on 3.
        assertEquals(Health.Step(3, listOf(1, 2)), Health.stepPastDown(5, 0, +1) { it in down })
        // Backwards from 3 wraps past 2 and 1 to 0.
        assertEquals(Health.Step(0, listOf(2, 1)), Health.stepPastDown(5, 3, -1) { it in down })
        // Wraps around the end.
        assertEquals(Health.Step(0, emptyList()), Health.stepPastDown(5, 4, +1) { it in down })
        // Everything else down: stays put.
        val all = Health.stepPastDown(3, 0, +1) { it != 0 }
        assertNull(all.index)
        assertEquals(listOf(1, 2), all.passed)
        // Nothing playing: from the top, one full lap.
        assertEquals(Health.Step(0, emptyList()), Health.stepPastDown(3, null, +1) { false })
        assertEquals(Health.Step(2, emptyList()), Health.stepPastDown(3, null, -1) { false })
        assertNull(Health.stepPastDown(3, null, +1) { true }.index)
        assertEquals("Every other station is marked down, so Next stayed where it was.", Health.allDownNotice("Next", false))
        assertEquals("[skip] Next: passed 2 marked down ('A', 'B') -> 'C'", Health.skipLog("Next", listOf("A", "B"), "C"))
    }
}
