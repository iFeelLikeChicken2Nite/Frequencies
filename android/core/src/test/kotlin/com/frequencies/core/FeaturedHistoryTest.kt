package com.frequencies.core

import java.nio.file.Files
import java.time.LocalDate
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Previously featured frequencies, as desktop featuredhistory.py (build 172). */
class FeaturedHistoryTest {
    private fun st(name: String) = JsonObject(mapOf("name" to JsonPrimitive(name), "url" to JsonPrimitive("https://$name.example/live/")))

    @Test fun foldsEveryVersionOnceAndListsWhatIsNotFeaturedNow() {
        val server = MockWebServer()
        var commitCalls = 0
        val reads = mutableListOf<String>()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                return when {
                    path.startsWith("/repos/o/r/commits") -> {
                        commitCalls++
                        assertTrue(path.contains("path=featured.json") && path.contains("sha=main"))
                        MockResponse().setBody(
                            """[{"sha":"s2","commit":{"committer":{"date":"2026-10-15T10:00:00Z"}}},
                                {"sha":"s1","commit":{"committer":{"date":"2026-10-01T10:00:00Z"}}}]""",
                        )
                    }
                    path == "/o/r/s1/featured.json" -> { reads += "s1"; MockResponse().setBody("""{"stations":[{"name":"a","url":"https://a.example/live"},{"name":"b","url":"https://b.example/live"}]}""") }
                    path == "/o/r/s2/featured.json" -> { reads += "s2"; MockResponse().setBody("""[{"name":"b","url":"https://b.example/live"},{"name":"c","url":"https://c.example/live"}]""") }
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        val base = server.url("/").toString().trimEnd('/')
        val dir = Files.createTempDirectory("fh").toFile()
        var now = 100_000L
        val h = FeaturedHistory(dir, OkHttpClient(), "https://raw.githubusercontent.com/o/r/main/featured.json", base, base, clock = { now }, today = { "2026-11-05" })
        assertEquals(listOf("o", "r", "main", "featured.json"), h.source())
        assertEquals("", h.refresh())
        assertEquals(listOf("s1", "s2"), reads)                       // oldest first

        // Today's list holds b and c: only a was featured before.
        val prev = h.previous(listOf(st("b"), st("c")).map { JsonObject(it + ("url" to JsonPrimitive(it["url"].str()!!.replace("/live/", "/live")))) })
        assertEquals(listOf("a"), prev.map { it.item["name"].str() })
        assertEquals("2026-10-01", prev[0].first)
        assertEquals("2026-10-15", prev[0].last)

        // Once a day: no second listing, and a version is never read twice.
        assertEquals("", h.refresh())
        assertEquals(1, commitCalls)
        now += FeaturedHistory.REFRESH_EVERY + 1
        assertEquals("", h.refresh())
        assertEquals(2, commitCalls)
        assertEquals(listOf("s1", "s2"), reads)

        // What this install was shown counts too, offline included.
        h.noteSeen(listOf(st("d")), day = "2026-11-04")
        val all = h.previous(emptyList())
        assertEquals(listOf("d", "c", "a", "b"), all.map { it.item["name"].str() })
        assertEquals("2026-11-04", all[0].first)
        server.shutdown()
    }

    @Test fun failuresWaitAnHour() {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = MockResponse().setResponseCode(403).addHeader("X-RateLimit-Remaining", "0")
        }
        server.start()
        val base = server.url("/").toString().trimEnd('/')
        val dir = Files.createTempDirectory("fh").toFile()
        var now = 100_000L
        val h = FeaturedHistory(dir, OkHttpClient(), "https://raw.githubusercontent.com/o/r/main/featured.json", base, base, clock = { now })
        assertEquals("GitHub's limit for now (403, 0 left)", h.refresh())
        assertEquals("GitHub couldn't be reached a little while ago", h.refresh())
        assertEquals("offline", FeaturedHistory(dir, OkHttpClient(), clock = { now + 99_999 }).refresh(offline = true))
        server.shutdown()
    }

    @Test fun whenTextLikeTheDesktop() {
        val today = LocalDate.parse("2026-11-05")
        assertEquals("Sep 3 – Oct 18", FeaturedHistory.whenText("2026-09-03", "2026-10-18", today))
        assertEquals("Sep 3, 2025 – Jan 4, 2026", FeaturedHistory.whenText("2025-09-03", "2026-01-04", today))
        assertEquals("Oct 2", FeaturedHistory.whenText("2026-10-02", "2026-10-02", today))
        assertEquals("Oct 2, 2025", FeaturedHistory.whenText("2025-10-02", "", today))
        assertEquals("Featured before", FeaturedHistory.whenText("", "", today))
    }
}
