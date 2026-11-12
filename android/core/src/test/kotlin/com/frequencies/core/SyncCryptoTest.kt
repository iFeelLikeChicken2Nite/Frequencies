package com.frequencies.core

import java.math.BigInteger
import java.security.interfaces.ECPublicKey
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * The phone's crypto against the desktop's own numbers: the output of
 * desktop build 160's `python3 tools/sync_vectors.py`, kept as it printed
 * in resources/sync/desktop-vectors.txt (fixed keys 0xC1 and 0x5E, code
 * ABCDE12345, ids 11…/22…, nonces 00..0f / 10..1f).
 */
class SyncCryptoTest {
    private val v: Map<String, String> = run {
        val text = javaClass.getResource("/sync/desktop-vectors.txt")!!.readText()
        val out = LinkedHashMap<String, String>()
        val lines = text.lines()
        for ((i, line) in lines.withIndex()) {
            val t = line.trim()
            fun after(label: String) = t.removePrefix(label).trim()
            when {
                t.startsWith("client point") -> out["cpub"] = after("client point")
                t.startsWith("server point") -> out["spub"] = after("server point")
                t.startsWith("z (ECDH)") -> out["z"] = after("z (ECDH)")
                t.startsWith("confirm_key") -> out["confirm"] = after("confirm_key")
                t.startsWith("pair key") -> out["pair"] = after("pair key")
                t.startsWith("client mac (b64)") -> out["cmac"] = after("client mac (b64)")
                t.startsWith("server mac (b64)") -> out["smac"] = after("server mac (b64)")
                t.startsWith("k_cs") -> out["kcs"] = after("k_cs")
                t.startsWith("k_sc") -> out["ksc"] = after("k_sc")
                t.startsWith("frame 0") -> out["f0"] = lines[i + 1].trim()
                t.startsWith("frame 1") -> out["f1"] = lines[i + 1].trim()
            }
        }
        out
    }

    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(2 * it, 2 * it + 2).toInt(16).toByte() }
    private fun ByteArray.hex() = joinToString("") { "%02x".format(it) }

    @Test fun ecdhMatchesTheDesktop() {
        val client = SyncCrypto.privateKey(BigInteger.valueOf(0xC1))
        val server = SyncCrypto.privateKey(BigInteger.valueOf(0x5E))
        val cpub = hex(v.getValue("cpub"))
        val spub = hex(v.getValue("spub"))
        assertEquals(v["z"], SyncCrypto.shared(client, spub).hex())
        assertEquals(v["z"], SyncCrypto.shared(server, cpub).hex())
        // A point read and written back is the same 65 bytes.
        assertArrayEquals(cpub, SyncCrypto.publicBytes(SyncCrypto.publicKey(cpub)))
        assertArrayEquals(spub, SyncCrypto.publicBytes(SyncCrypto.publicKey(spub)))
    }

    @Test fun pairingKeysAndMacsMatchTheDesktop() {
        val (confirm, pair) = SyncCrypto.pairKeys(hex(v.getValue("z")), "ABCDE12345", hex(v.getValue("cpub")), hex(v.getValue("spub")),
            "11".repeat(16), "22".repeat(16))
        assertEquals(v["confirm"], confirm.hex())
        assertEquals(v["pair"], pair.hex())
        assertEquals(v["cmac"], SyncWire.b64(SyncCrypto.mac(confirm, "client")))
        assertEquals(v["smac"], SyncWire.b64(SyncCrypto.mac(confirm, "server")))
    }

    @Test fun sessionKeysAndFramesMatchTheDesktop() {
        val cn = ByteArray(16) { it.toByte() }
        val sn = ByteArray(16) { (it + 16).toByte() }
        val (kcs, ksc) = SyncCrypto.sessionKeys(hex(v.getValue("pair")), cn, sn)
        assertEquals(v["kcs"], kcs.hex())
        assertEquals(v["ksc"], ksc.hex())
        val ping = """{"t":"ping"}""".toByteArray()
        assertEquals(v["f0"], SyncCrypto.seal(kcs, 0, ping).hex())
        assertEquals(v["f1"], SyncCrypto.seal(kcs, 1, ping).hex())
        assertArrayEquals(ping, SyncCrypto.open(kcs, 1, hex(v.getValue("f1"))))
        // The other direction's key, or the wrong counter, doesn't open it.
        for (attempt in listOf({ SyncCrypto.open(ksc, 0, hex(v.getValue("f0"))) }, { SyncCrypto.open(kcs, 1, hex(v.getValue("f0"))) })) {
            try { attempt(); fail("opened with the wrong key or counter") } catch (e: java.security.GeneralSecurityException) { }
        }
        // Our compact JSON writes a ping exactly as the desktop does.
        assertEquals("""{"t":"ping"}""", String(SyncWire.encode(SyncWire.msg("t" to "ping"))))
    }

    @Test fun freshKeysAgree() {
        val a = SyncCrypto.newKeyPair()
        val b = SyncCrypto.newKeyPair()
        val ap = SyncCrypto.publicBytes(a.public as ECPublicKey)
        val bp = SyncCrypto.publicBytes(b.public as ECPublicKey)
        assertEquals(65, ap.size)
        assertEquals(4.toByte(), ap[0])
        assertArrayEquals(SyncCrypto.shared(a.private, bp), SyncCrypto.shared(b.private, ap))
    }

    @Test fun hkdfIsRfc5869() {
        // RFC 5869 test case 1.
        val okm = SyncCrypto.hkdf(hex("0b".repeat(22)), hex("000102030405060708090a0b0c"), hex("f0f1f2f3f4f5f6f7f8f9"), 42)
        assertEquals("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865", okm.hex())
        assertTrue(v.size == 11)
    }
}
