package com.frequencies.core

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StreamsTest {
    @Test
    fun titles() {
        assertEquals("TAME IMPALA - Dracula",
            StreamTitle.clean("Dracula\" artist=\"TAME IMPALA\" {=\"0\" itunesTrackId=\" )\" amgTrackI"))
        assertEquals("Enya - Orinoco Flow", StreamTitle.clean("  Enya - Orinoco Flow "))
        assertEquals("", StreamTitle.clean(""))
        assertEquals("Enya" to "Orinoco Flow", StreamTitle.split("Enya - Orinoco Flow"))
        assertEquals("A-ha" to "Take On Me", StreamTitle.split("A-ha – Take On Me"))
        assertNull(StreamTitle.split("Jazz88"))
    }

    @Test
    fun playlists() {
        assertTrue(Playlists.looksLikePlaylist("https://somafm.com/lush130.pls"))
        assertTrue(Playlists.looksLikePlaylist("http://x/a.M3U?x=1"))
        assertFalse(Playlists.looksLikePlaylist("http://x/a.m3u8"))
        assertTrue(Playlists.isHls("http://x/a.m3u8"))
        val pls = "[playlist]\nnumberofentries=2\nFile1=https://ice1.somafm.com/lush-128-aac\nTitle1=Lush\n" +
            "File2=https://ice2.somafm.com/lush-128-aac\nLength1=-1\nVersion=2\n"
        assertEquals(listOf("https://ice1.somafm.com/lush-128-aac", "https://ice2.somafm.com/lush-128-aac"),
            Playlists.streamsIn(pls))
        assertEquals(listOf("http://a/1", "http://b/2"), Playlists.streamsIn("#EXTM3U\n#EXTINF:-1,One\nhttp://a/1\nhttp://b/2\n"))
        assertEquals(emptyList<String>(), Playlists.streamsIn("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n"))
    }

    @Test
    fun resolveFetchesWithTheAppsAgent() {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody("[playlist]\nFile1=http://stream.example/live\n"))
        server.start()
        val url = server.url("/radio.pls").toString()
        assertEquals(listOf("http://stream.example/live"), Playlists.resolve(url))
        assertEquals("Frequencies/1.5", server.takeRequest().getHeader("User-Agent"))
        // Fails open.
        server.enqueue(MockResponse().setResponseCode(500))
        assertEquals(listOf(url), Playlists.resolve(url))
        server.shutdown()
        assertEquals(listOf("http://x/live"), Playlists.resolve("http://x/live"))
    }
}
