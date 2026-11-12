package com.frequencies.core

import java.math.BigInteger
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPrivateKeySpec
import java.security.spec.ECPublicKeySpec
import javax.crypto.Cipher
import javax.crypto.KeyAgreement
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * The sync protocol's crypto (notes/sync-protocol.md sections 4 and 5),
 * all from the platform (java.security / javax.crypto, no Google Play
 * services): P-256 ECDH, HKDF-SHA256 (RFC 5869 with HmacSHA256),
 * HMAC-SHA256 and AES-256-GCM with a 128-bit tag. Checked byte for byte
 * against the desktop's tools/sync_vectors.py (SyncCryptoTest).
 */
object SyncCrypto {
    private val curve: ECParameterSpec by lazy {
        AlgorithmParameters.getInstance("EC").run {
            init(ECGenParameterSpec("secp256r1"))
            getParameterSpec(ECParameterSpec::class.java)
        }
    }

    fun hmac(key: ByteArray, data: ByteArray): ByteArray =
        Mac.getInstance("HmacSHA256").run {
            init(SecretKeySpec(key, "HmacSHA256"))
            doFinal(data)
        }

    /** RFC 5869: extract with [salt], expand with [info] to [length] bytes. */
    fun hkdf(ikm: ByteArray, salt: ByteArray, info: ByteArray, length: Int = 64): ByteArray {
        val prk = hmac(if (salt.isEmpty()) ByteArray(32) else salt, ikm)
        val out = java.io.ByteArrayOutputStream()
        var block = ByteArray(0)
        var n = 1
        while (out.size() < length) {
            block = hmac(prk, block + info + byteArrayOf(n.toByte()))
            out.write(block)
            n++
        }
        return out.toByteArray().copyOf(length)
    }

    fun newKeyPair(): KeyPair = KeyPairGenerator.getInstance("EC").run {
        initialize(ECGenParameterSpec("secp256r1"))
        generateKeyPair()
    }

    /** A private key from its number (only the tests' fixed keys). */
    fun privateKey(scalar: BigInteger): PrivateKey =
        KeyFactory.getInstance("EC").generatePrivate(ECPrivateKeySpec(scalar, curve))

    /** The 65-byte uncompressed point 04 || X || Y. */
    fun publicBytes(key: ECPublicKey): ByteArray =
        byteArrayOf(4) + fixed32(key.w.affineX) + fixed32(key.w.affineY)

    fun publicKey(point: ByteArray): ECPublicKey {
        require(point.size == 65 && point[0] == 4.toByte()) { "not an uncompressed P-256 point" }
        val x = BigInteger(1, point.copyOfRange(1, 33))
        val y = BigInteger(1, point.copyOfRange(33, 65))
        return KeyFactory.getInstance("EC").generatePublic(ECPublicKeySpec(ECPoint(x, y), curve)) as ECPublicKey
    }

    /** The ECDH shared secret: the 32-byte X coordinate. */
    fun shared(mine: PrivateKey, theirPoint: ByteArray): ByteArray =
        KeyAgreement.getInstance("ECDH").run {
            init(mine)
            doPhase(publicKey(theirPoint), true)
            generateSecret()
        }

    /** (confirm_key, pair key), section 4 step 3. */
    fun pairKeys(z: ByteArray, code: String, clientPub: ByteArray, serverPub: ByteArray,
                 clientId: String, serverId: String): Pair<ByteArray, ByteArray> {
        val info = "frequencies-pair v1".toByteArray(Charsets.US_ASCII) + clientPub + serverPub +
            clientId.toByteArray(Charsets.US_ASCII) + serverId.toByteArray(Charsets.US_ASCII)
        val okm = hkdf(z, code.toByteArray(Charsets.US_ASCII), info)
        return okm.copyOfRange(0, 32) to okm.copyOfRange(32, 64)
    }

    /** (k_cs, k_sc), section 5 step 3. */
    fun sessionKeys(pairKey: ByteArray, clientNonce: ByteArray, serverNonce: ByteArray): Pair<ByteArray, ByteArray> {
        val okm = hkdf(pairKey, clientNonce + serverNonce, "frequencies-sync v1".toByteArray(Charsets.US_ASCII))
        return okm.copyOfRange(0, 32) to okm.copyOfRange(32, 64)
    }

    fun mac(key: ByteArray, label: String): ByteArray = hmac(key, label.toByteArray(Charsets.US_ASCII))

    fun sameBytes(a: ByteArray, b: ByteArray): Boolean = MessageDigest.isEqual(a, b)

    /** 4 zero bytes, then the frame's counter as 8 bytes big-endian. */
    fun nonce(counter: Long): ByteArray {
        val out = ByteArray(12)
        for (i in 0 until 8) out[11 - i] = (counter ushr (8 * i)).toByte()
        return out
    }

    fun seal(key: ByteArray, counter: Long, plain: ByteArray): ByteArray =
        Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce(counter)))
            doFinal(plain)
        }

    /** Throws when the frame doesn't decrypt (another key, or tampered with). */
    fun open(key: ByteArray, counter: Long, box: ByteArray): ByteArray =
        Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce(counter)))
            doFinal(box)
        }

    fun sha256Hex(data: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }

    fun randomBytes(n: Int): ByteArray = ByteArray(n).also { java.security.SecureRandom().nextBytes(it) }

    private fun fixed32(v: BigInteger): ByteArray {
        val raw = v.toByteArray()
        return when {
            raw.size == 32 -> raw
            raw.size > 32 -> raw.copyOfRange(raw.size - 32, raw.size)
            else -> ByteArray(32 - raw.size) + raw
        }
    }
}
