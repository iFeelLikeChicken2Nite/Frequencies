package com.frequencies.core

import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test

/**
 * The Twitch resolver against the real Twitch (the unit tests use
 * fixtures in Twitch's shape). Skipped unless asked for on a PC with the
 * internet:
 *   gradlew :core:test --tests '*LiveCheckTest*' -Pfrequencies.live=true
 * Nothing is saved: a playback token carries the caller's address.
 */
class LiveCheckTest {
    private val live = System.getProperty("frequencies.live") == "true"

    /** Channels that are usually live around the clock; one is enough. */
    private val channels = listOf("monstercat", "lofigirl", "chillhopmusic", "relaxbeats")

    @Test
    fun twitchResolvesALiveChannel() {
        assumeTrue("live check not asked for", live)
        val resolver = Twitch.Resolver()
        val outcomes = ArrayList<String>()
        for (channel in channels) {
            try {
                val found = resolver.resolve(channel)
                val text = resolver.read(found.url)
                println("[live] $channel: '${found.rendition}' of ${found.renditions}; media list: ${text?.let { Twitch.summarize(it) } ?: "unreadable"}")
                assertTrue("the media list should be an HLS list", text?.trimStart()?.startsWith("#EXTM3U") == true)
                return
            } catch (e: Twitch.TwitchError) {
                outcomes += "$channel: ${e.message}"
                println("[live] $channel: ${e.message}")
            }
        }
        // Every channel offline says only that they're offline; anything else is a failure.
        assertTrue("no channel resolved: $outcomes", outcomes.all { "not live" in it })
        println("[live] none of the channels is live right now; the resolver got as far as the stream list")
    }

    /**
     * Radio Caprice's :9003 server answers "ICY 200 OK" (desktop task 220);
     * the phone's player reads streams with this same OkHttp. Prints the
     * status line the server really sent.
     */
    @Test
    fun oldShoutcastServerPlays() {
        assumeTrue("live check not asked for", live)
        val url = "http://79.120.39.202:9003/"
        java.net.Socket().use { socket ->
            socket.connect(java.net.InetSocketAddress("79.120.39.202", 9003), 8000)
            socket.soTimeout = 8000
            socket.getOutputStream().write("GET / HTTP/1.0\r\nHost: 79.120.39.202:9003\r\nUser-Agent: ${Playlists.STREAM_AGENT}\r\nIcy-MetaData: 1\r\n\r\n".toByteArray())
            println("[live] $url first line: " + socket.getInputStream().bufferedReader(Charsets.ISO_8859_1).readLine())
        }
        val http = okhttp3.OkHttpClient.Builder().readTimeout(15, java.util.concurrent.TimeUnit.SECONDS).build()
        val request = okhttp3.Request.Builder().url(url).header("User-Agent", Playlists.STREAM_AGENT).header("Icy-MetaData", "1").build()
        http.newCall(request).execute().use { response ->
            val got = response.body!!.source().let { src -> var n = 0L; while (n < 32_768) { val r = src.read(okio.Buffer(), 8192); if (r < 0) break; n += r }; n }
            println("[live] OkHttp: ${response.code} ${response.protocol}, icy-name '${response.header("icy-name")}', icy-metaint ${response.header("icy-metaint")}, $got bytes")
            assertTrue(response.isSuccessful && got >= 32_768)
        }
        assertTrue(Health.probe(url).first)
    }
}
