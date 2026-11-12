package com.frequencies.songid

import com.frequencies.core.FreqJson
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ShazamTest {
    // The parts of a real answer shazam.py reads (trimmed).
    private val answer = """
        {"matches":[{"id":"1","offset":83.21,"timeskew":0.0001,"frequencyskew":0}],
         "track":{"title":"Pink + White","subtitle":"Frank Ocean","url":"https://www.shazam.com/track/1",
           "images":{"coverart":"https://is1.example/400x400cc.jpg","coverarthq":"https://is1.example/hq.jpg"},
           "sections":[{"type":"SONG","metadata":[{"title":"Album","text":"Blonde"},{"title":"Released","text":"2016"}]}],
           "hub":{"options":[{"actions":[{"uri":"https://play.google.com/store/apps/details?id=com.apple.android.music&referrer=music.apple.com"},
             {"uri":"https://geo.music.apple.com/us/album/_/1146195596?i=1146195719&mttnagencyid=s2n&app=music&itscg=30201"}]}]}}}
    """.trimIndent()

    @Test
    fun readsTheSongAsTheDesktopDoes() {
        val t = Shazam.parse(FreqJson.parseToJsonElement(answer) as JsonObject)!!
        assertEquals("Pink + White", t.title)
        assertEquals("Frank Ocean", t.artist)
        assertEquals("https://is1.example/hq.jpg", t.coverUrl)
        assertEquals(83.21, t.matchOffset!!, 1e-9)
        assertEquals("Blonde", t.album)
        assertEquals("2016", t.releaseDate)
        // The Play Store link mentioning music.apple.com is skipped; geo. and app= go.
        assertEquals("https://music.apple.com/us/album/_/1146195596?i=1146195719&mttnagencyid=s2n", t.appleMusicUrl)
    }

    @Test
    fun noTrackIsNoMatch() = assertNull(Shazam.parse(FreqJson.parseToJsonElement("""{"matches":[]}""") as JsonObject))

    @Test
    fun postsTheSignatureLikeShazamio() {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody(answer))
        server.start()
        val samples = ShortArray(SongId.SAMPLE_RATE * 6) { (Math.sin(it * 0.05) * 8000 + Math.sin(it * 0.31) * 3000).toInt().toShort() }
        val sig = Signature.make(samples)
        val track = Shazam(endpoint = "http://${server.hostName}:${server.port}/tag/{uuid_1}/{uuid_2}?sync=true").identify(sig, 1234L)
        val request = server.takeRequest()
        server.shutdown()
        assertEquals("Frank Ocean", track?.artist)
        assertTrue(request.path!!.matches(Regex("/tag/[0-9A-F-]{36}/[0-9A-F-]{36}\\?sync=true")))
        val body = FreqJson.parseToJsonElement(request.body.readUtf8()).jsonObject
        assertEquals(6000, body["signature"]!!.jsonObject["samplems"]!!.jsonPrimitive.content.toInt())
        assertEquals(sig.uri, body["signature"]!!.jsonObject["uri"]!!.jsonPrimitive.content)
        assertEquals("1234", body["timestamp"]!!.jsonPrimitive.content)
    }
}
