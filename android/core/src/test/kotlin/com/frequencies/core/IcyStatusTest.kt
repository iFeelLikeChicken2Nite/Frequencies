package com.frequencies.core

import java.net.ServerSocket
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Old SHOUTcast (v1) servers answer "ICY 200 OK" instead of
 * "HTTP/1.0 200 OK" (desktop task 220, build 159: FFmpeg and libcurl
 * refuse it, so the desktop relays such stations). The app's streams go
 * through OkHttp (Media3's OkHttpDataSource), whose status-line parser
 * takes "ICY " as HTTP/1.0. This keeps it that way: a tiny server that
 * answers exactly like Radio Caprice's :9003 one, read the way the
 * player reads a stream, headers and ICY metadata included.
 */
class IcyStatusTest {
    private fun icyServer(metaint: Int, audio: ByteArray, title: String): ServerSocket {
        val server = ServerSocket(0)
        thread(isDaemon = true) {
            server.accept().use { socket ->
                val input = socket.getInputStream().bufferedReader(Charsets.ISO_8859_1)
                while (true) { val line = input.readLine() ?: break; if (line.isEmpty()) break }
                val out = socket.getOutputStream()
                out.write(
                    ("ICY 200 OK\r\nicy-notice1:<BR>This stream requires Winamp<BR>\r\n" +
                        "icy-name:Caprice test\r\nicy-genre:Test\r\ncontent-type:audio/mpeg\r\n" +
                        "icy-pub:1\r\nicy-metaint:$metaint\r\nicy-br:320\r\n\r\n").toByteArray(Charsets.ISO_8859_1),
                )
                // The audio, then one metadata block (length byte × 16), as SHOUTcast sends it.
                out.write(audio, 0, metaint)
                val meta = "StreamTitle='$title';".toByteArray(Charsets.ISO_8859_1)
                val blocks = (meta.size + 15) / 16
                out.write(blocks)
                out.write(meta.copyOf(blocks * 16))
                out.write(audio, metaint, audio.size - metaint)
                out.flush()
            }
        }
        return server
    }

    @Test
    fun okHttpTakesAnIcyStatusLine() {
        val audio = ByteArray(64) { (it * 7).toByte() }
        val server = icyServer(16, audio, "Artist - Song")
        val http = OkHttpClient.Builder().readTimeout(5, TimeUnit.SECONDS).build()
        val request = Request.Builder().url("http://127.0.0.1:${server.localPort}/")
            .header("User-Agent", Playlists.STREAM_AGENT).header("Icy-MetaData", "1").build()
        http.newCall(request).execute().use { response ->
            assertTrue("an ICY answer is a success", response.isSuccessful)
            assertEquals(200, response.code)
            assertEquals("Caprice test", response.header("icy-name"))
            assertEquals("16", response.header("icy-metaint"))
            assertEquals("audio/mpeg", response.header("content-type"))
            val body = response.body!!.bytes()
            // Everything after the headers arrives untouched: audio, the title block, more audio.
            val meta = body.copyOfRange(17, 17 + body[16] * 16).toString(Charsets.ISO_8859_1)
            assertTrue(meta, meta.startsWith("StreamTitle='Artist - Song';"))
            assertEquals(audio.toList(), (body.copyOfRange(0, 16) + body.copyOfRange(17 + body[16] * 16, body.size)).toList())
        }
        server.close()
    }

    @Test
    fun healthCheckCountsAnIcyServerAsUp() {
        // The desktop's station check marked these down before build 159; the phone's must not.
        val server = icyServer(16, ByteArray(4096) { 1 }, "x")
        assertEquals(true to "", Health.probe("http://127.0.0.1:${server.localPort}/"))
        server.close()
    }
}
