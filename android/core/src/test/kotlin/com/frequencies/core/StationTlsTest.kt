package com.frequencies.core

import java.io.IOException
import java.nio.file.Files
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * Broken-certificate stations (the desktop's `insecure` flag): a flagged
 * station's server with a self-signed certificate is reached; an
 * unflagged one is still checked and refused, even on the same server.
 */
class StationTlsTest {
    private lateinit var server: MockWebServer
    private val base = OkHttpClient.Builder().connectTimeout(5, TimeUnit.SECONDS).readTimeout(5, TimeUnit.SECONDS).build()

    @Before
    fun start() {
        // Self-signed, made out for "localhost" only: no phone would trust it.
        val cert = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
        val handshake = HandshakeCertificates.Builder().heldCertificate(cert).build()
        server = MockWebServer()
        server.useHttps(handshake.sslSocketFactory(), false)
        server.start()
    }

    @After
    fun stop() = server.shutdown()

    private fun url(host: String) = "https://$host:${server.port}/stream"
    private fun ok(n: Int = 1) = repeat(n) { server.enqueue(MockResponse().setBody("x".repeat(4096))) }

    private fun fetch(client: okhttp3.Call.Factory, url: String, marked: Boolean = false): Int {
        val request = Request.Builder().url(url).apply { if (marked) header(StationTls.MARK, "1") }.build()
        return client.newCall(request).execute().use { it.code }
    }

    private fun refused(block: () -> Unit) {
        try {
            block()
            fail("a broken certificate was accepted")
        } catch (e: IOException) {
            // javax.net.ssl.SSLHandshakeException: the check is on.
        }
    }

    @Test
    fun unflaggedStationsStillCheckCertificates() {
        refused { fetch(base, url("localhost")) }
        val router = StationTls.Router(StationTls.Clients(base))
        refused { fetch(router, url("localhost")) }
        assertFalse(router.trusts("localhost"))
    }

    @Test
    fun flaggedStationPassesOthersDont() {
        ok(3)
        val router = StationTls.Router(StationTls.Clients(base))
        // The flagged station's own stream (the service marks it).
        assertEquals(200, fetch(router, url("localhost"), marked = true))
        // Its HLS pieces come from the same server without the mark.
        assertEquals(200, fetch(router, url("localhost")))
        // Another name for the same server, never marked: still checked.
        refused { fetch(router, url("127.0.0.1")) }
        // Another station starts: nothing is trusted any more.
        router.tunedTo(insecure = false, url = url("127.0.0.1"))
        refused { fetch(router, url("localhost")) }
        // A flagged station started straight away: its server is trusted from the first byte.
        router.tunedTo(insecure = true, url = url("localhost"))
        assertEquals(200, fetch(router, url("localhost")))
        // The mark never reaches the server.
        repeat(server.requestCount) { assertEquals(null, server.takeRequest().getHeader(StationTls.MARK)) }
    }

    @Test
    fun healthCheckFollowsTheFlag() {
        ok(1)
        assertEquals("SSLError", Health.probe(url("localhost")).second)
        assertEquals(true to "", Health.probe(url("localhost"), insecure = true))
        val flagged = Station(JsonObject(mapOf("url" to JsonPrimitive(url("localhost")), "insecure" to JsonPrimitive(true))))
        val plain = Station(JsonObject(mapOf("url" to JsonPrimitive(url("127.0.0.1")))))
        assertEquals(setOf(url("localhost")), Health.insecureUrls(listOf(flagged, plain)))
        val clients = StationTls.Clients(base)
        assertSame(clients.insecure, clients.forStation(flagged))
        // Build 170: no station's stream is checked, flag or not (StationTls.EVERY_STATION).
        assertTrue(StationTls.EVERY_STATION)
        assertSame(clients.insecure, clients.forStation(plain))
        assertSame(clients.insecure, clients.forStation(null))
        assertTrue(StationTls.skipsCheck(plain))
    }

    @Test
    fun playlistOfAFlaggedStationIsRead() {
        server.enqueue(MockResponse().setBody("[playlist]\nFile1=https://example.org/live\n"))
        val pls = "https://localhost:${server.port}/listen.pls"
        assertEquals(listOf(pls), Playlists.resolve(pls, base))          // checked: refused, the address kept
        server.enqueue(MockResponse().setBody("[playlist]\nFile1=https://example.org/live\n"))
        assertEquals(listOf("https://example.org/live"), Playlists.resolve(pls, StationTls.Clients(base).forFlag(true)))
    }

    @Test
    fun flagSurvivesBackupRoundTrip() {
        val station = JsonObject(linkedMapOf(
            "id" to JsonPrimitive("a"), "name" to JsonPrimitive("Old Cert FM"),
            "url" to JsonPrimitive("https://old.example/stream"), "insecure" to JsonPrimitive(true),
        ))
        val desktop = JsonObject(linkedMapOf(
            "format" to JsonPrimitive(Backup.FORMAT), "version" to JsonPrimitive(2), "stations" to JsonArray(listOf(station)),
        ))
        val dir = Files.createTempDirectory("tls").toFile()
        val read = Backup.read(encodeJson(desktop), dir)
        assertTrue(read.stations[0].insecure)
        val out = Backup.export(read)
        assertEquals(JsonPrimitive(true), out["stations"]!!.jsonArray[0].jsonObject["insecure"])
        // And a station without it stays checked.
        val plain = Backup.read(encodeJson(JsonObject(desktop + ("stations" to JsonArray(listOf(JsonObject(station - "insecure")))))), dir)
        assertFalse(plain.stations[0].insecure)
    }
}
