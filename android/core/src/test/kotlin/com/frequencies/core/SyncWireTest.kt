package com.frequencies.core

import java.io.ByteArrayInputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import kotlin.concurrent.thread
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class SyncWireTest {
    private val me = "ab".repeat(16)
    private val desk = "cd".repeat(16)

    @Test fun typedCodes() {
        assertEquals("ABCDE12345", SyncWire.cleanCode("abcde-12345"))
        assertEquals("0BCDE11345", SyncWire.cleanCode(" OBCDE 1L345 "))
        assertEquals("", SyncWire.cleanCode("ABCDE-1234"))
        assertEquals("", SyncWire.cleanCode("ABCDE-1234U"))    // U isn't in Crockford's alphabet
        assertEquals("ABCDE-12345", SyncWire.showCode("ABCDE12345"))
    }

    @Test fun theDesktopsQrCode() {
        // sync_core.qr_text: name through urllib.parse.quote.
        val qr = SyncWire.parseQr("frequencies-sync:?v=1&id=$desk&name=Kitchen%20PC%20%2B%20%C3%A9&code=ABCDE12345&port=47620&ip=192.168.1.5,10.0.0.2")
        assertNotNull(qr)
        assertEquals(desk, qr!!.id)
        assertEquals("Kitchen PC + é", qr.name)
        assertEquals("ABCDE12345", qr.code)
        assertEquals(47620, qr.port)
        assertEquals(listOf("192.168.1.5", "10.0.0.2"), qr.ips)
        assertNull(SyncWire.parseQr("FREQ1:abc"))
        assertNull(SyncWire.parseQr("frequencies-sync:?v=1&id=short&code=ABCDE12345"))
    }

    @Test fun announcementsAndTxt() {
        fun dgram(s: String) = s.toByteArray()
        val ok = dgram("""{"app":"frequencies-sync","v":1,"id":"$desk","name":"Kitchen PC","port":47614,"pair":1}""")
        assertEquals(SyncWire.Found(desk, "Kitchen PC", 47614, true), SyncWire.parseAnnouncement(ok, ok.size, me))
        for (bad in listOf(
            """{"app":"other","v":1,"id":"$desk","port":47614}""",
            """{"app":"frequencies-sync","v":2,"id":"$desk","port":47614}""",
            """{"app":"frequencies-sync","v":1,"id":"$me","port":47614}""",
            """{"app":"frequencies-sync","v":1,"probe":true,"id":"$desk"}""",
            """{"app":"frequencies-sync","v":1,"id":"$desk","port":0}""",
            "not json",
        )) assertNull(bad, SyncWire.parseAnnouncement(dgram(bad), bad.length, me))
        val big = ByteArray(1100) { ' '.code.toByte() }
        assertNull(SyncWire.parseAnnouncement(big, big.size, me))
        assertEquals("""{"app":"frequencies-sync","v":1,"probe":true,"id":"$me"}""", String(SyncWire.probe(me)))
        assertEquals(desk to true, SyncWire.parseTxt(mapOf("v" to "1".toByteArray(), "id" to desk.toByteArray(), "pair" to "1".toByteArray()), me))
        assertNull(SyncWire.parseTxt(mapOf("v" to "2".toByteArray(), "id" to desk.toByteArray()), me))
        assertEquals("Kitchen PC", SyncWire.nameOfInstance("Kitchen PC ${desk.take(6)}", desk))
    }

    @Test fun badFramesClose() {
        for (bytes in listOf(byteArrayOf(0, 0, 0, 0), byteArrayOf(0x7f, 0, 0, 0), byteArrayOf(0, 0, 0, 5, 1))) {
            try { SyncWire.recvFrame(ByteArrayInputStream(bytes)); fail() } catch (e: SyncWire.SyncError) { }
        }
    }

    @Test fun anotherVersionClosesAndIsLogged() {
        val lines = ArrayList<String>()
        SyncWire.log = { synchronized(lines) { lines += it } }
        try {
            ServerSocket(0).use { server ->
                val t = thread {
                    server.accept().use { c ->
                        SyncWire.recvJson(c.getInputStream())
                        SyncWire.sendJson(c.getOutputStream(), SyncWire.msg("t" to "hello", "v" to 2, "id" to desk, "name" to "Future", "nonce" to "AAAAAAAAAAAAAAAAAAAAAA=="))
                        c.getInputStream().read()
                    }
                }
                val host = object : SyncSession.Host {
                    override fun stateNow(peerId: String) = SyncWire.msg("t" to "state")
                    override fun takeState(peerId: String, state: kotlinx.serialization.json.JsonObject) = emptyList<String>()
                    override fun takeArt(items: kotlinx.serialization.json.JsonArray) {}
                    override fun waitingArt(peerId: String) = emptyList<String>()
                    override fun artFile(peerId: String, sha: String) = null
                    override fun forgotten(peerId: String) {}
                }
                val reason = SyncSession(host, desk).run(listOf(InetSocketAddress("127.0.0.1", server.localPort)), me, "Phone", ByteArray(32))
                assertEquals("other device speaks version 2", reason)
                t.join(5_000)
            }
        } finally {
            SyncWire.log = {}
        }
        assertTrue(lines.toString(), "other device speaks version 2" in lines)
    }
}
