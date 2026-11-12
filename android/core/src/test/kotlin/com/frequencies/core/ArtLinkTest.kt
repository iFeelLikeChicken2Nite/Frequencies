package com.frequencies.core

import java.io.File
import java.nio.file.Files
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Picture links on share (desktop build 128, artlink.py + cloudflare/art-worker.js). */
class ArtLinkTest {
    private val png = byteArrayOf(0x89.toByte(), 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a) + ByteArray(600) { it.toByte() }
    private val noShrink = ArtLink.Shrinker { null }

    private fun station(art: File?): Station {
        val s = Station(LinkedHashMap(Defaults.station))
        s["id"] = JsonPrimitive("s1")
        s["name"] = JsonPrimitive("KXCI")
        s["url"] = JsonPrimitive("https://stream.example/kxci")
        if (art != null) s["custom_art_path"] = JsonPrimitive(art.absolutePath)
        return s
    }

    private fun picture(bytes: ByteArray = png): File =
        File(Files.createTempDirectory("art").toFile(), "mine.png").also { it.writeBytes(bytes) }

    @Test
    fun whichPicturesNeedAnUpload() {
        val service = "https://frequencies-art.example.workers.dev"
        val file = picture()
        assertTrue(ArtLink.needsUpload(station(file), service))
        assertFalse("no service in this build", ArtLink.needsUpload(station(file), ""))
        assertFalse("no picture", ArtLink.needsUpload(station(null), service))
        // A thumbnail from a code passes its own link on (or has none to give).
        assertFalse(ArtLink.needsUpload(station(file).also { it["art_from_code"] = JsonPrimitive(true) }, service))
        // Already linked, and still the same picture: nothing to upload.
        val linked = station(file).also {
            it["art_url"] = JsonPrimitive("https://x.example/a.png")
            it["art_url_sha"] = JsonPrimitive(Featured.sha1(file))
        }
        assertFalse(ArtLink.needsUpload(linked, service))
        // The picture changed since (a crop, another file): the link no longer counts.
        file.appendBytes(byteArrayOf(1))
        assertTrue(ArtLink.needsUpload(linked, service))
        assertEquals(service, ArtLink.service(PackAddresses.read("""{"art": "$service/"}""")))
        assertEquals("", ArtLink.service(PackAddresses.read("""{"art": "not an address"}""")))
    }

    @Test
    fun uploadsLikeTheDesktop() {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody("""{"url": "https://art.example/a/abc.png"}"""))
        server.start()
        val service = server.url("/").toString().trimEnd('/')
        val file = picture()
        val result = ArtLink.upload(OkHttpClient(), service, file, noShrink)
        assertEquals(ArtLink.Result("https://art.example/a/abc.png"), result)
        val sent = server.takeRequest()
        assertEquals("POST", sent.method)
        assertEquals("/upload", sent.path)
        assertEquals("Frequencies/1.5", sent.getHeader("User-Agent"))
        assertEquals("application/octet-stream", sent.getHeader("Content-Type"))
        assertArrayEquals(png, sent.body.readByteArray())
        assertEquals("[art] shared picture uploaded: https://art.example/a/abc.png", ArtLink.logLine(result))

        // The link goes into the code, and the other end gets it back.
        val s = station(file).also {
            it["art_url"] = JsonPrimitive(result.link)
            it["art_url_sha"] = JsonPrimitive(Featured.sha1(file))
        }
        val (code, _) = ShareCode.fromStation(s, thumbnailer = null)
        assertEquals(result.link, ShareCode.entry(code)!!["art_url"].str())

        // The Worker's refusals and nonsense answers: no link, the desktop's reasons.
        server.enqueue(MockResponse().setResponseCode(413).setBody("""{"error": "picture too big (2 MB at most)"}"""))
        assertEquals(ArtLink.Result("", "the picture service said 413"), ArtLink.upload(OkHttpClient(), service, file, noShrink))
        server.enqueue(MockResponse().setBody("""{"url": "ftp://nope"}"""))
        assertEquals(ArtLink.Result("", "the picture service gave no link"), ArtLink.upload(OkHttpClient(), service, file, noShrink))
        server.shutdown()
        val down = ArtLink.upload(OkHttpClient(), service, file, noShrink)
        assertEquals("", down.link)
        assertEquals("[art] shared picture not uploaded (${down.reason})", ArtLink.logLine(down))
    }

    @Test
    fun bigPicturesAreShrunkFirst() {
        val big = picture(png + ByteArray(ArtLink.MAX_UPLOAD))
        var asked = 0
        val small = byteArrayOf(1, 2, 3)
        assertArrayEquals(small, ArtLink.pictureBytes(big, { asked++; small }))
        assertEquals(1, asked)
        assertArrayEquals(png, ArtLink.pictureBytes(picture(), { error("not needed") }))
        // Unreadable, or still too big: not uploaded.
        assertEquals("IOException", ArtLink.upload(OkHttpClient(), "http://127.0.0.1:9", big, noShrink).reason)
    }
}
