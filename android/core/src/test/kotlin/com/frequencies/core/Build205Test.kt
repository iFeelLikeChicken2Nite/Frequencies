package com.frequencies.core

import java.util.concurrent.TimeUnit
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Android build 205, batch 29 (desktop build 204, task 247): the health
 * probe asks the player's question. CJLM (StreamTheWorld) sends its first
 * byte after about 5.5 s; the probe gave up at 5 s and marked it down while
 * it played. Now: the player's 10 s, .pls/.m3u opened like the player, and
 * one retry before "down". Fake servers only; nothing station-specific.
 */
class Build205Test {
    private fun audio() = MockResponse().setHeader("Content-Type", "audio/mpeg").setBody("ID3 audio bytes")

    private fun <T> serve(routes: Map<String, () -> MockResponse>, body: (MockWebServer) -> T): T {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse =
                routes[request.path]?.invoke() ?: MockResponse().setResponseCode(404)
        }
        server.start()
        try { return body(server) } finally { server.shutdown() }
    }

    private fun seconds(block: () -> Unit): Double {
        val t = System.nanoTime(); block(); return (System.nanoTime() - t) / 1e9
    }

    @Test fun theProbeWaitsAsLongAsThePlayer() {
        assertEquals(10_000L, Playlists.CONNECT_TIMEOUT_MS)
        assertEquals(Playlists.CONNECT_TIMEOUT_MS, Health.PROBE_TIMEOUT_MS)
        assertEquals(Playlists.CONNECT_TIMEOUT_MS.toInt(), Health.client.connectTimeoutMillis)
        assertEquals(Playlists.CONNECT_TIMEOUT_MS.toInt(), Health.client.readTimeoutMillis)
        assertTrue(Health.client.callTimeoutMillis >= 2 * Playlists.CONNECT_TIMEOUT_MS)
    }

    @Test fun firstByteAfterFiveAndAHalfSecondsIsUp() {
        val server = MockWebServer()
        // Nothing at all for 5.6 s (CJLM's mirrors), then the stream.
        server.enqueue(audio().setHeadersDelay(5600, TimeUnit.MILLISECONDS))
        // Headers at once, the first audio byte 5.6 s later.
        server.enqueue(audio().setBodyDelay(5600, TimeUnit.MILLISECONDS))
        server.start()
        try {
            val url = server.url("/CJLM_FM_SC").toString()
            val took = seconds { assertEquals(true to "", Health.probe(url)) }
            assertTrue("took $took s", took in 5.5..8.0)
            assertEquals(true to "", Health.probe(url))
            assertEquals(2, server.requestCount)   // up on the first try, no retry
        } finally {
            server.shutdown()
        }
    }

    @Test fun aSilentDeadMountIsStillDownAfterOneRetry() {
        val server = MockWebServer()
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        server.start()
        try {
            var result: Pair<Boolean, String>? = null
            val took = seconds { result = Health.probe(server.url("/dead").toString()) }
            assertEquals(false to "ReadTimeout", result)
            assertEquals(2, server.requestCount)
            // 10 s, the 2 s pause, 10 s again.
            assertTrue("took $took s", took in 21.5..26.0)
        } finally {
            server.shutdown()
        }
    }

    @Test fun aRefusedConnectionIsDownAfterOneRetry() {
        val closed = MockWebServer().apply { start() }
        val dead = closed.url("/").toString()
        closed.shutdown()
        val took = seconds { assertEquals(false to "ConnectionError", Health.probe(dead)) }
        assertTrue("took $took s", took >= 2.0)
        assertEquals(false to "ConnectionError", Health.probe("http://no-such-host.invalid/stream"))
    }

    @Test fun oneRetryBeforeDownButNotAfterAnHttp4xx() {
        val server = MockWebServer()
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(audio())
        server.enqueue(MockResponse().setResponseCode(500))
        server.enqueue(MockResponse().setResponseCode(502))
        server.enqueue(MockResponse().setResponseCode(404))
        server.enqueue(MockResponse().setResponseCode(403))
        server.start()
        try {
            val url = server.url("/stream").toString()
            val lines = ArrayList<String>()
            assertEquals(true to "", Health.probe(url, log = { lines += it }))
            assertEquals(2, server.requestCount)
            assertEquals(1, lines.size)
            assertTrue(lines[0], lines[0].endsWith(": answered on the second try (first: HTTP 503)"))
            assertTrue(lines[0].startsWith("[health] "))
            // Failed both times: the second answer is the reason.
            assertEquals(false to "HTTP 502", Health.probe(url))
            assertEquals(4, server.requestCount)
            // A 4xx is the server's own answer: no second try.
            assertEquals(false to "HTTP 404", Health.probe(url))
            assertEquals(false to "HTTP 403", Health.probe(url))
            assertEquals(6, server.requestCount)
        } finally {
            server.shutdown()
        }
    }

    @Test fun aPlsIsUpOnlyWhenAListedStreamSendsAudio() = serve(
        mapOf(
            "/live" to ::audio,
            "/slow" to { audio().setHeadersDelay(5600, TimeUnit.MILLISECONDS) },
            "/gone" to { MockResponse().setResponseCode(404) },
        ),
    ) { server ->
        fun pls(vararg paths: String) = MockResponse().setHeader("Content-Type", "audio/x-scpls").setBody(
            "[playlist]\nNumberOfEntries=${paths.size}\n" +
                paths.withIndex().joinToString("") { (i, p) -> "File${i + 1}=${server.url(p)}\nTitle${i + 1}=x\n" } + "Version=2\n",
        )
        // A dead one first, then a slow one (StreamTheWorld's own .pls lists its mirrors like this).
        val routes = mapOf(
            "/station.pls" to { pls("/gone", "/slow") },
            "/dead.pls" to { pls("/gone", "/gone2") },
        )
        val inner = server.dispatcher
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = routes[request.path]?.invoke() ?: inner.dispatch(request)
        }
        assertEquals(true to "", Health.probe(server.url("/station.pls").toString()))
        assertEquals(listOf("/station.pls", "/gone", "/slow"), (1..3).map { server.takeRequest().path })
        // Before build 205 the file's own text counted as sound, so this was "up".
        assertEquals(false to "no listed stream answered (HTTP 404)", Health.probe(server.url("/dead.pls").toString()))
        assertEquals(3 + 6, server.requestCount)   // tried again after 2 s: the file, then both streams, twice
    }

    @Test fun anM3uIsOpenedByItsNameTypeOrFirstLine() = serve(
        mapOf("/live" to ::audio, "/gone" to { MockResponse().setResponseCode(404) }),
    ) { server ->
        val live = server.url("/live").toString()
        val gone = server.url("/gone").toString()
        val routes = mapOf(
            // By name only (text/plain), as the player picks one.
            "/a.m3u" to { MockResponse().setHeader("Content-Type", "text/plain").setBody("#EXTINF:-1,Test\n$gone\n$live\n") },
            // By type, no telling name.
            "/listen" to { MockResponse().setHeader("Content-Type", "audio/x-mpegurl").setBody("$gone\n") },
            // By first line, served as octet-stream.
            "/tune" to { MockResponse().setHeader("Content-Type", "application/octet-stream").setBody("#EXTM3U\n$live\n") },
            // An HLS manifest is played by the player itself: it answering is enough.
            "/hls" to { MockResponse().setHeader("Content-Type", "application/vnd.apple.mpegurl").setBody("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\nseg1.ts\n") },
            // Named .pls but the server sends audio: the player plays the address as it is.
            "/fake.pls" to ::audio,
            // A playlist that lists nothing.
            "/empty.pls" to { MockResponse().setHeader("Content-Type", "audio/x-scpls").setBody("[playlist]\nNumberOfEntries=0\n") },
        )
        val inner = server.dispatcher
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = routes[request.path]?.invoke() ?: inner.dispatch(request)
        }
        fun probe(path: String) = Health.probe(server.url(path).toString(), retry = false)
        assertEquals(true to "", probe("/a.m3u"))
        assertEquals(false to "no listed stream answered (HTTP 404)", probe("/listen"))
        assertEquals(true to "", probe("/tune"))
        assertEquals(true to "", probe("/hls"))
        assertEquals(true to "", probe("/fake.pls"))
        assertEquals(false to "playlist lists no streams", probe("/empty.pls"))
    }

    @Test fun onlyTheFirstFourListedStreamsAndTwoLevelsDeep() = serve(mapOf("/live" to ::audio)) { server ->
        val gone = (1..4).map { server.url("/gone$it").toString() }
        val routes = mapOf(
            "/five.m3u" to { MockResponse().setBody((gone + server.url("/live").toString()).joinToString("\n")) },
            "/outer.m3u" to { MockResponse().setBody(server.url("/inner.m3u").toString()) },
            "/inner.m3u" to { MockResponse().setBody(server.url("/live").toString()) },
        )
        val inner = server.dispatcher
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = routes[request.path]?.invoke() ?: inner.dispatch(request)
        }
        assertFalse(Health.probe(server.url("/five.m3u").toString(), retry = false).first)
        assertEquals(1 + 4, server.requestCount)
        assertEquals(true to "", Health.probe(server.url("/outer.m3u").toString(), retry = false))
    }

    @Test fun theSweepStillMarksOnlyWhatFailedTwice() {
        // A slow station and a dead one in one launch sweep, through the real probe.
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                "/slow" -> audio().setHeadersDelay(5600, TimeUnit.MILLISECONDS)
                "/live" -> audio()
                else -> MockResponse().setResponseCode(404)
            }
        }
        server.start()
        try {
            val got = LinkedHashMap<String, Pair<Boolean, String>>()
            val summary = Health.sweep(
                listOf("live" to server.url("/live").toString(), "cjlm" to server.url("/slow").toString(), "gone" to server.url("/gone").toString()),
            ) { id, ok, reason -> got[id] = ok to reason }
            assertEquals(Health.Summary(checked = 3, answered = 2, offline = false, stopped = false), summary)
            assertEquals(mapOf("live" to (true to ""), "cjlm" to (true to ""), "gone" to (false to "HTTP 404")), got)
        } finally {
            server.shutdown()
        }
    }
}
