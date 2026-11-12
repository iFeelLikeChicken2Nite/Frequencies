package com.frequencies.core

import java.io.File
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Android batch 22 (desktop build 178's hang): with ~800 stations both
 * devices send multi-MB states at the same moment. A side that only reads
 * between its own sends fills the socket buffers both ways and hangs. The
 * other side here is the old desktop (177): it sends its whole state first
 * and only then reads, with small socket buffers. The phone reads on its
 * own thread while its writer sends, so both states get through.
 */
class SyncWriterTest {
    private val me = "ab".repeat(16)
    private val desk = "cd".repeat(16)
    private val key = ByteArray(32) { it.toByte() }
    private fun bigState(who: String) = SyncWire.msg("t" to "state", "who" to who, "pad" to "x".repeat(4 * 1024 * 1024))

    @Test fun bothSendBigStatesAtOnce() {
        val phoneGot = CountDownLatch(1)
        val deskGot = CountDownLatch(1)
        var deskSaw = ""
        ServerSocket().use { server ->
            server.receiveBufferSize = 16 * 1024
            server.bind(InetSocketAddress("127.0.0.1", 0))
            val desktop = thread(isDaemon = true) {
                runCatching {
                    server.accept().use { c ->
                        c.sendBufferSize = 16 * 1024
                        val input = c.getInputStream().buffered()
                        val out = c.getOutputStream()
                        val hello = SyncWire.recvJson(input)
                        val nonce = SyncCrypto.randomBytes(16)
                        SyncWire.sendJson(out, SyncWire.msg("t" to "hello", "v" to SyncWire.PROTOCOL, "id" to desk, "name" to "Old desktop", "nonce" to SyncWire.b64(nonce)))
                        val (kcs, ksc) = SyncCrypto.sessionKeys(key, SyncWire.unb64(hello["nonce"].str()), nonce)
                        val chan = SyncWire.Channel(input, out, ksc, kcs, me, "Phone")
                        // Desktop 177: its whole state first, reading only after.
                        chan.send(bigState("desk"))
                        while (true) {
                            val m = chan.recv()
                            if (m["t"].str() == "state") {
                                deskSaw = m["who"].str().orEmpty()
                                deskGot.countDown()
                                chan.send(SyncWire.msg("t" to "bye"))
                                break
                            }
                        }
                        Thread.sleep(200)
                    }
                }
            }
            val host = object : SyncSession.Host {
                override fun stateNow(peerId: String) = bigState("phone")
                override fun takeState(peerId: String, state: JsonObject): Collection<String> {
                    if (state["who"].str() == "desk") phoneGot.countDown()
                    return emptyList()
                }
                override fun takeArt(items: JsonArray) {}
                override fun waitingArt(peerId: String) = emptyList<String>()
                override fun artFile(peerId: String, sha: String): File? = null
                override fun forgotten(peerId: String) {}
            }
            SyncSession.testBuffers = 16 * 1024
            val session = SyncSession(host, desk)
            var reason = ""
            val phone = thread(isDaemon = true) {
                reason = session.run(listOf(InetSocketAddress("127.0.0.1", server.localPort)), me, "Phone", key)
            }
            val bothGot = phoneGot.await(20, TimeUnit.SECONDS) && deskGot.await(20, TimeUnit.SECONDS)
            if (!bothGot) session.close(null)
            phone.join(5_000)
            desktop.join(2_000)
            SyncSession.testBuffers = 0
            assertTrue("both big states got through (no send/send hang)", bothGot)
            assertEquals("phone", deskSaw)
            assertEquals("closed", reason)
        }
    }
}
