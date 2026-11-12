package com.frequencies.core

import java.io.File
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * One short sync session from the phone to a paired desktop (section 5):
 * connect, say hello, send this phone's state at once (and ask for any
 * picture still missing), take the desktop's, answer its `art?`, send a
 * fresh state when the library changes, ping every 30 s, and close with
 * `bye` when the app goes to the background. The phone always calls; it
 * never listens.
 *
 * Reads on the session's thread, writes on its own "sync-write" thread
 * (sync-protocol.md section 3, "Never stop reading while sending"; desktop
 * build 178): with ~800 stations both devices send multi-MB states at the
 * same moment, and a side that only reads between its own sends fills the
 * socket buffers both ways and hangs. Everything sent goes through [outbox]
 * in order; pictures asked for are read and sent by the writer, batch by
 * batch, so they never pile up in memory.
 */
class SyncSession(private val host: Host, val peerId: String) {

    /** What the app gives a session. Called on the session's threads. */
    interface Host {
        /** Observe this phone's own edits and build its state message for [peerId] (its what-to-sync choice). */
        fun stateNow(peerId: String): JsonObject
        /** Merge another device's state; the picture shas to ask for. */
        fun takeState(peerId: String, state: JsonObject): Collection<String>
        fun takeArt(items: JsonArray)
        /** Shas of pictures this phone is still waiting for from [peerId] (none when its Station pictures are off). */
        fun waitingArt(peerId: String): Collection<String>
        /** A picture to send [peerId] (null when its Station pictures are off). */
        fun artFile(peerId: String, sha: String): File?
        /** The other device forgot this pair. */
        fun forgotten(peerId: String)
    }

    @Volatile private var channel: SyncWire.Channel? = null
    @Volatile private var socket: Socket? = null
    @Volatile private var lastSent: String? = null
    @Volatile private var lastSeen: String? = null
    /** The last state the other device sent (desktop build 171: a part turned on is taken from it at once). */
    @Volatile private var lastState: JsonObject? = null
    @Volatile var gotState = false
        private set
    @Volatile var closing = false
        private set
    /** When the last message came in (System.currentTimeMillis). */
    @Volatile var lastHeard = 0L
        private set
    /** What the writer sends, in order: a message, or the pictures for an `art?`. */
    private sealed class Out {
        class Msg(val msg: JsonObject) : Out()
        class Art(val sha: List<String>) : Out()
    }
    private val outbox = LinkedBlockingQueue<Out>()
    /** Why the writer stopped, when it failed (the session ends with it). */
    @Volatile private var writeError: Exception? = null
    @Volatile private var writer: Thread? = null

    val peerName: String get() = channel?.peerName.orEmpty()
    val open: Boolean get() = channel != null && !closing

    /** Connect to the first address that answers (3 s each). */
    fun connect(addrs: List<InetSocketAddress>): Socket {
        var last: Exception? = null
        for (a in addrs) {
            val s = Socket()
            try {
                if (testBuffers > 0) { s.sendBufferSize = testBuffers; s.receiveBufferSize = testBuffers }
                s.connect(a, 3_000)
                s.soTimeout = SyncWire.STEP_TIMEOUT_MS
                s.tcpNoDelay = true
                return s
            } catch (e: Exception) {
                last = e
                runCatching { s.close() }
            }
        }
        throw SyncWire.SyncError("couldn't connect (${last?.message ?: "no address"})")
    }

    /**
     * The whole session, blocking until it ends; the reason it ended
     * ("closed" when either side said goodbye).
     */
    fun run(addrs: List<InetSocketAddress>, me: String, name: String, key: ByteArray, onReady: (SyncSession) -> Unit = {}): String {
        try {
            val s = connect(addrs)
            socket = s
            if (closing) return "closed"
            val chan = SyncWire.clientSession(s.getInputStream().buffered(), s.getOutputStream(), me, name, key, peerId)
            channel = chan
            s.soTimeout = SyncWire.IDLE_LIMIT_MS.toInt()
            writer = thread(name = "sync-write", isDaemon = true) { writeLoop(chan, s) }
            onReady(this)
            push(force = true)
            askArt()
            loop(chan)
            return "closed"
        } catch (e: SocketTimeoutException) {
            return if (closing) "closed" else "the other device went quiet"
        } catch (e: SyncWire.OtherVersion) {
            return e.message ?: "other version"
        } catch (e: SyncWire.SyncError) {
            return if (closing) "closed" else (writeError?.message ?: e.message ?: "error")
        } catch (e: java.io.IOException) {
            return if (closing) "closed" else (writeError?.let { "couldn't send: ${it.message ?: it.javaClass.simpleName}" } ?: e.message ?: e.javaClass.simpleName)
        } finally {
            closing = true
            writer?.interrupt()
            runCatching { socket?.close() }
            writer?.join(2_000)
            channel = null
            outbox.clear()
        }
    }

    /**
     * The writer: sends what is queued, and a ping when nothing went out for
     * 30 s, until the session ends. A failed send closes the socket, which
     * ends the reading loop too. After `bye` or `forget` nothing more goes.
     */
    private fun writeLoop(chan: SyncWire.Channel, s: Socket) {
        var lastOut = System.currentTimeMillis()
        try {
            while (true) {
                val next = outbox.poll(250, TimeUnit.MILLISECONDS)
                if (next != null) {
                    when (next) {
                        is Out.Msg -> {
                            val kind = next.msg["t"].str()
                            // Closing: only the goodbye still goes.
                            if (closing && kind != "bye" && kind != "forget") continue
                            chan.send(next.msg)
                            if (kind == "bye" || kind == "forget") return
                        }
                        is Out.Art -> if (!closing) sendArt(chan, next.sha)
                    }
                    lastOut = System.currentTimeMillis()
                } else if (closing && outbox.isEmpty()) {
                    return
                }
                if (System.currentTimeMillis() - lastOut >= SyncWire.PING_EVERY_MS) {
                    chan.send(SyncWire.msg("t" to "ping"))
                    lastOut = System.currentTimeMillis()
                }
            }
        } catch (e: InterruptedException) {
            // The session ended.
        } catch (e: Exception) {
            if (!closing) writeError = e
            runCatching { s.shutdownInput() }
            runCatching { s.close() }
        }
    }

    private fun loop(chan: SyncWire.Channel) {
        while (!closing) {
            val msg = chan.recv()
            lastHeard = System.currentTimeMillis()
            when (msg["t"].str()) {
                "ping" -> Unit
                "bye" -> return
                "forget" -> { host.forgotten(peerId); return }
                // Answered by the writer, after what is queued before it.
                "art?" -> outbox.add(Out.Art((msg["sha"] as? JsonArray).orEmpty().mapNotNull { it.str() }))
                "art" -> host.takeArt(msg["items"] as? JsonArray ?: JsonArray(emptyList()))
                "state" -> {
                    lastSeen = SyncData.digest(msg)
                    lastState = msg
                    val need = host.takeState(peerId, msg)
                    gotState = true
                    if (need.isNotEmpty()) send(SyncWire.msg("t" to "art?", "sha" to JsonArray(need.sorted().map { JsonPrimitive(it) })))
                    // What this phone has that the other one didn't.
                    push()
                }
                else -> Unit            // unknown messages are ignored (section 1)
            }
        }
    }

    /** Queue [msg] for the writer; never waits for the other device to read. */
    fun send(msg: JsonObject) {
        if (channel == null) return
        outbox.add(Out.Msg(msg))
    }

    /**
     * A part was just turned on (sync-protocol.md 6.4, "A part turned on"): the
     * other device already sent everything and won't send an unchanged state
     * again, so take the last one again now. As in any session, this phone's
     * state goes first, then the merge, then every picture it waits for.
     */
    fun retake() {
        if (channel == null || closing) return
        val held = lastState ?: return
        push(force = true)
        host.takeState(peerId, held)
        askArt()
        push()
    }

    /** Ask for every picture this phone is still waiting for (`art?`). */
    fun askArt() {
        if (channel == null || closing) return
        val waiting = host.waitingArt(peerId).sorted()
        if (waiting.isNotEmpty()) send(SyncWire.msg("t" to "art?", "sha" to JsonArray(waiting.map { JsonPrimitive(it) })))
    }

    /** Send this phone's state if it says something new (or always, [force]). */
    fun push(force: Boolean = false) {
        if (channel == null || closing) return
        val state = host.stateNow(peerId)
        val mine = SyncData.digest(state)
        if (!force && (mine == lastSent || mine == lastSeen)) return
        lastSent = mine
        send(state)
    }

    /** On the writer: the pictures asked for, a few MB per message (desktop build 177). */
    private fun sendArt(chan: SyncWire.Channel, wanted: List<String>) {
        val items = ArrayList<JsonElement>()
        var size = 0
        var sent = 0
        for (sha in wanted.take(500)) {
            if (closing) return
            val file = host.artFile(peerId, sha) ?: continue
            val data = runCatching { file.readBytes() }.getOrNull() ?: continue
            val text = SyncWire.b64(data)
            if (items.isNotEmpty() && size + text.length > SyncWire.ART_BATCH) {
                chan.send(SyncWire.msg("t" to "art", "items" to JsonArray(items.toList())))
                sent += items.size
                items.clear()
                size = 0
            }
            if (text.length > SyncWire.ART_BATCH) continue
            items.add(SyncWire.msg("sha" to sha, "ext" to SyncData.artExt(file.name), "data" to text))
            size += text.length
        }
        if (items.isNotEmpty()) {
            chan.send(SyncWire.msg("t" to "art", "items" to JsonArray(items)))
            sent += items.size
        }
        if (sent > 0) SyncWire.log("sent $sent picture(s)")
    }

    /**
     * Has this session got what it came for: the other side's state and
     * every picture asked for, and nothing heard for [quietMs]?
     */
    fun settled(quietMs: Long = 1_500): Boolean =
        gotState && host.waitingArt(peerId).isEmpty() && System.currentTimeMillis() - lastHeard >= quietMs

    companion object {
        /** Tests only: small socket buffers, as on a slow link (SyncWriterTest). 0 = the system's. */
        @Volatile internal var testBuffers = 0
    }

    /** End the session: [message] (bye or forget) first when it is open. */
    fun close(message: String? = "bye") {
        if (closing) return
        val w = writer
        if (message != null && channel != null && w != null && w.isAlive) {
            // The writer drops what is still queued and sends the goodbye; the
            // socket closes once it has gone, or after 3 s at the most.
            outbox.add(Out.Msg(SyncWire.msg("t" to message)))
            closing = true
            thread(name = "sync-close", isDaemon = true) {
                runCatching { w.join(3_000) }
                runCatching { socket?.shutdownInput() }
                runCatching { socket?.close() }
            }
            return
        }
        closing = true
        writer?.interrupt()
        runCatching { socket?.shutdownInput() }
        runCatching { socket?.close() }
    }
}
