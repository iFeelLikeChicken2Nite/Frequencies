package com.frequencies.core

import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.URLDecoder
import java.security.interfaces.ECPublicKey
import java.util.Base64
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Sync on the wire (notes/sync-protocol.md sections 2 to 5 and 10):
 * frames, pairing codes and the QR text, the handshakes, the encrypted
 * channel, and the UDP probe. Plain blocking streams, so the same code
 * runs on the phone and in the unit tests against the desktop's own
 * Python (SyncDesktopTest).
 */
object SyncWire {
    const val PROTOCOL = 1
    const val TCP_PORT = 47614
    const val UDP_PORT = 47615
    const val SERVICE_TYPE = "_frequencies._tcp"
    const val APP_TAG = "frequencies-sync"
    const val MAX_FRAME = 16 * 1024 * 1024
    const val ART_BATCH = 4 * 1024 * 1024
    const val STEP_TIMEOUT_MS = 15_000
    const val PING_EVERY_MS = 30_000L
    const val IDLE_LIMIT_MS = 90_000L

    const val CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    const val CODE_LENGTH = 10

    class SyncError(message: String) : Exception(message)

    /** A device that speaks another version: the connection closes and the log says so. */
    class OtherVersion(val version: String) : Exception("other device speaks version $version")

    /** Where `[sync] …` lines go (the app's log; nothing in the tests). */
    @Volatile var log: (String) -> Unit = {}

    fun b64(data: ByteArray): String = Base64.getEncoder().encodeToString(data)
    fun unb64(text: String?): ByteArray = try {
        Base64.getDecoder().decode(text.orEmpty())
    } catch (e: IllegalArgumentException) {
        throw SyncError("not base64")
    }

    fun isDeviceId(id: String?): Boolean = id != null && id.length == 32 && id.all { it in "0123456789abcdef" }

    fun newDeviceId(): String = SyncCrypto.randomBytes(16).joinToString("") { "%02x".format(it) }

    // -- frames (section 3) ---------------------------------------------------

    fun sendFrame(out: OutputStream, data: ByteArray) {
        if (data.isEmpty() || data.size > MAX_FRAME) throw SyncError("frame too big")
        val d = DataOutputStream(out)
        d.writeInt(data.size)
        d.write(data)
        d.flush()
    }

    fun recvFrame(input: InputStream): ByteArray {
        val d = DataInputStream(input)
        val n = try { d.readInt() } catch (e: java.io.EOFException) { throw SyncError("the other device hung up") }
        if (n <= 0 || n > MAX_FRAME) throw SyncError("bad frame length")
        val buf = ByteArray(n)
        try { d.readFully(buf) } catch (e: java.io.EOFException) { throw SyncError("the other device hung up") }
        return buf
    }

    fun encode(msg: JsonObject): ByteArray = encodeJsonCompact(msg).toByteArray(Charsets.UTF_8)

    fun decode(data: ByteArray): JsonObject {
        val e = try { FreqJson.parseToJsonElement(String(data, Charsets.UTF_8)) } catch (e: Exception) { throw SyncError("not a message") }
        return e as? JsonObject ?: throw SyncError("not a message")
    }

    fun sendJson(out: OutputStream, msg: JsonObject) = sendFrame(out, encode(msg))
    fun recvJson(input: InputStream): JsonObject = decode(recvFrame(input))

    fun msg(vararg pairs: Pair<String, Any?>): JsonObject = JsonObject(pairs.associate { (k, v) ->
        k to when (v) {
            is JsonElement -> v
            is String -> JsonPrimitive(v)
            is Number -> JsonPrimitive(v)
            is Boolean -> JsonPrimitive(v)
            null -> kotlinx.serialization.json.JsonNull
            else -> JsonPrimitive(v.toString())
        }
    })

    // -- codes and the QR text (section 4) ------------------------------------

    /** A typed code as the protocol wants it, or "" if it can't be one. */
    fun cleanCode(text: String?): String {
        val t = text.orEmpty().uppercase().replace("-", "").replace(" ", "")
            .replace('O', '0').replace('I', '1').replace('L', '1')
        return if (t.length == CODE_LENGTH && t.all { it in CODE_ALPHABET }) t else ""
    }

    fun showCode(code: String): String = code.take(5) + "-" + code.drop(5)

    /** What the desktop's QR code says. */
    data class QrPair(val v: String, val id: String, val name: String, val code: String, val port: Int, val ips: List<String>)

    /** `frequencies-sync:?v=1&id=…&name=…&code=…&port=…&ip=a,b`, or null when [text] isn't one. */
    fun parseQr(text: String?): QrPair? {
        val t = text.orEmpty().trim()
        if (!t.startsWith("frequencies-sync:", ignoreCase = true)) return null
        val query = t.substringAfter(':').removePrefix("?")
        val params = HashMap<String, String>()
        for (part in query.split('&')) {
            if (part.isEmpty()) continue
            val k = part.substringBefore('=')
            val v = part.substringAfter('=', "")
            params[k] = try { URLDecoder.decode(v.replace("+", "%2B"), "UTF-8") } catch (e: Exception) { v }
        }
        val code = cleanCode(params["code"])
        val id = params["id"].orEmpty().lowercase()
        if (code.isEmpty() || !isDeviceId(id)) return null
        val port = params["port"]?.toIntOrNull()?.takeIf { it in 1..65535 } ?: TCP_PORT
        val ips = params["ip"].orEmpty().split(',').map { it.trim() }.filter { it.isNotEmpty() }
        return QrPair(params["v"].orEmpty(), id, params["name"].orEmpty().ifBlank { "another device" }, code, port, ips)
    }

    // -- handshakes (sections 4, 5 and 10) ------------------------------------

    /** The other side's id and name from a hello or pair message. */
    fun checkHello(msg: JsonObject, want: String): Pair<String, String> {
        if (msg["t"].str() != want) throw SyncError("expected $want, got ${msg["t"].str()}")
        val v = msg["v"]
        if (v.num() != PROTOCOL.toDouble() || (v as? JsonPrimitive)?.isString == true) {
            val shown = (v as? JsonPrimitive)?.content ?: v.toString()
            log("other device speaks version $shown")
            throw OtherVersion(shown)
        }
        val pid = msg["id"].str().orEmpty()
        if (!isDeviceId(pid)) throw SyncError("bad device id")
        return pid to msg["name"].str().orEmpty().ifEmpty { "another device" }.take(80)
    }

    data class Paired(val id: String, val name: String, val key: ByteArray)

    /** Pair with a device showing [code] (this device enters it). Throws SyncError("wrong code") on a wrong one. */
    fun clientPair(input: InputStream, out: OutputStream, me: String, name: String, code: String): Paired {
        val pair = SyncCrypto.newKeyPair()
        val pub = SyncCrypto.publicBytes(pair.public as ECPublicKey)
        sendJson(out, msg("t" to "pair", "v" to PROTOCOL, "id" to me, "name" to name, "pub" to b64(pub)))
        val answer = recvJson(input)
        val (pid, pname) = checkHello(answer, "pair")
        val serverPub = unb64(answer["pub"].str())
        val z = try { SyncCrypto.shared(pair.private, serverPub) } catch (e: Exception) { throw SyncError("bad key from the other device") }
        val (confirm, key) = SyncCrypto.pairKeys(z, code, pub, serverPub, me, pid)
        sendJson(out, msg("t" to "confirm", "mac" to b64(SyncCrypto.mac(confirm, "client"))))
        val proof = recvJson(input)
        if (proof["t"].str() == "pair-failed") throw SyncError("wrong code")
        if (proof["t"].str() != "confirm" || !SyncCrypto.sameBytes(unb64(proof["mac"].str()), SyncCrypto.mac(confirm, "server"))) {
            throw SyncError("the other device didn't prove the code")
        }
        return Paired(pid, pname, key)
    }

    /** Open a sync session to a paired device (this device calls). */
    fun clientSession(input: InputStream, out: OutputStream, me: String, name: String, pairKey: ByteArray, peerId: String): Channel {
        val nonce = SyncCrypto.randomBytes(16)
        sendJson(out, msg("t" to "hello", "v" to PROTOCOL, "id" to me, "name" to name, "nonce" to b64(nonce)))
        val answer = recvJson(input)
        val (pid, pname) = checkHello(answer, "hello")
        if (pid != peerId) throw SyncError("a different device answered")
        val theirs = unb64(answer["nonce"].str())
        if (theirs.size != 16) throw SyncError("bad nonce")
        val (kcs, ksc) = SyncCrypto.sessionKeys(pairKey, nonce, theirs)
        return Channel(input, out, kcs, ksc, pid, pname)
    }

    /** An encrypted session (section 5 step 4): one counter each way, starting at 0. */
    class Channel(
        private val input: InputStream,
        private val out: OutputStream,
        private val sendKey: ByteArray,
        private val recvKey: ByteArray,
        val peerId: String,
        val peerName: String,
    ) {
        private var sendN = 0L
        private var recvN = 0L
        private val sendLock = Any()

        fun send(msg: JsonObject) {
            val data = encode(msg)
            synchronized(sendLock) {
                val box = SyncCrypto.seal(sendKey, sendN, data)
                sendN++
                sendFrame(out, box)
            }
        }

        /** The next message. Throws SyncError("wrong key") when it doesn't decrypt. */
        fun recv(): JsonObject {
            val box = recvFrame(input)
            val data = try { SyncCrypto.open(recvKey, recvN, box) } catch (e: java.security.GeneralSecurityException) {
                throw SyncError("wrong key")
            }
            recvN++
            return decode(data)
        }
    }

    // -- the UDP fallback (section 2) -----------------------------------------

    data class Found(val id: String, val name: String, val port: Int, val pair: Boolean)

    fun probe(me: String): ByteArray =
        encode(msg("app" to APP_TAG, "v" to PROTOCOL, "probe" to true, "id" to me))

    /** A desktop's announcement, or null for anything else (a probe, another app, ourselves, another version). */
    fun parseAnnouncement(data: ByteArray, length: Int, me: String): Found? {
        if (length > 1024) return null
        val m = try { decode(data.copyOf(length)) } catch (e: SyncError) { return null }
        if (m["app"].str() != APP_TAG || m["v"].num() != PROTOCOL.toDouble()) return null
        if (truthy(m["probe"])) return null
        val id = m["id"].str().orEmpty()
        if (id == me || !isDeviceId(id)) return null
        val port = m["port"].num()?.toInt() ?: return null
        if (port !in 1..65535) return null
        return Found(id, m["name"].str().orEmpty().take(80), port, truthy(m["pair"]))
    }

    /** DNS-SD TXT records (NsdServiceInfo.attributes): the same checks as the desktop's browser. */
    fun parseTxt(attrs: Map<String, ByteArray?>, me: String): Pair<String, Boolean>? {
        val get = { k: String -> attrs[k]?.toString(Charsets.UTF_8).orEmpty() }
        val id = get("id")
        if (get("v") != PROTOCOL.toString() || !isDeviceId(id) || id == me) return null
        return id to (get("pair") == "1")
    }

    /** The name part of an instance name "Kitchen PC 1a2b3c". */
    fun nameOfInstance(instance: String, id: String): String =
        if (instance.endsWith(id.take(6))) instance.dropLast(6).trimEnd() else instance
}
