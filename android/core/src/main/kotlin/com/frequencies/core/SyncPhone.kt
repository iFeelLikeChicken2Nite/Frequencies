package com.frequencies.core

import java.io.File
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Copies of the library a sync step may change, and what it says it changed. */
class SyncEdit(val stations: MutableList<Station>, val settings: Settings, val history: MutableList<JsonObject>) {
    var stationsChanged = false
    var settingsChanged = false
    var historyChanged = false
    /** Old station id -> the one kept (one station under two ids). */
    val renamed = LinkedHashMap<String, String>()
    val removed = LinkedHashSet<String>()
}

/** The phone's library as sync sees it (the app's Library; a plain one in the tests). */
interface SyncLibrary {
    /**
     * Run [block] with copies of the stations, settings and history, under
     * the library's lock; afterwards save and show the parts it marked
     * changed.
     */
    fun <T> edit(block: (SyncEdit) -> T): T

    /**
     * Run [block] with copies taken without the library's lock, for a look
     * that changes nothing in the library (dating this phone's own edits):
     * the screens' edits never wait for it (build 188: a Song ID tap froze
     * behind it). Changes [block] marks are not saved.
     */
    fun <T> peek(block: (SyncEdit) -> T): T = edit(block)
}

/**
 * The phone's side of every session: dates its own edits, builds its
 * state, merges the desktop's, keeps pictures. One per phone, shared by
 * its sessions (desktops), so one stamp store. The app and the tests use
 * the same class.
 */
class SyncPhone(
    val me: String,
    private val store: SyncData.Store,
    private val saveStore: (SyncData.Store) -> Unit,
    private val artDir: File,
    private val library: SyncLibrary,
    private val pathHere: (Station) -> Boolean = { s -> s["local_path"].str().orEmpty().trim().let { it.isNotEmpty() && File(it).exists() } },
    private val historyLimit: (Settings) -> Int = SongHistory::limit,
    private val taken: (peerId: String, merged: SyncData.Merged) -> Unit = { _, _ -> },
    private val forgot: (peerId: String) -> Unit = {},
    private val clock: () -> Long = System::currentTimeMillis,
    /** This phone's what-to-sync choice for each paired device (section 6.4; everything by default). */
    private val what: (peerId: String) -> SyncData.What = { SyncData.What.ALL },
) : SyncSession.Host {

    @Volatile private var artIndex: Map<String, File> = emptyMap()

    /** Date this phone's own edits now (also without a session, so edits are dated when made). */
    fun observeNow(): Boolean = synchronized(store) {
        library.peek { e ->
            SyncData.observe(e.stations, e.settings, e.history, store, me, clock()).also { if (it) saveStore(store) }
        }
    }

    override fun stateNow(peerId: String): JsonObject = synchronized(store) {
        library.edit { e ->
            if (SyncData.observe(e.stations, e.settings, e.history, store, me, clock())) saveStore(store)
            artIndex = SyncData.artIndex(e.stations)
            SyncData.buildState(e.stations, e.settings, e.history, store, clock(), what = what(peerId))
        }
    }

    override fun takeState(peerId: String, state: JsonObject): Collection<String> {
        val res = synchronized(store) { library.edit { e ->
            // Own edits first, so they're dated before anything is taken.
            SyncData.observe(e.stations, e.settings, e.history, store, me, clock())
            val limit = historyLimit(e.settings)
            val res = SyncData.merge(e.stations, e.settings, e.history, store, me, state, limit, clock(),
                pathHere = pathHere, what = what(peerId))
            // Two default stations whose old address lacked its query string (desktop build 171,
            // task 227): one station under two ids, joined here.
            val joined = SyncData.joinOldAddresses(e.stations, e.settings, store, me, clock())
            if (joined.stations) {
                res.stations = true
                res.renamed.putAll(joined.renamed)
                res.removed.addAll(joined.removed)
                res.settings.addAll(joined.settings)
            }
            saveStore(store)
            e.stationsChanged = res.stations
            e.settingsChanged = res.settings.isNotEmpty() || res.renamed.isNotEmpty()
            e.historyChanged = res.history
            e.renamed.putAll(res.renamed)
            e.removed.addAll(res.removed)
            res
        } }
        if (res.clockAheadMs > 5 * 60_000) SyncWire.log("clock: the other device is ${res.clockAheadMs / 1000} s ahead")
        // Outside the store's lock (desktop build 178 notes): the app's callback takes the app's
        // own lock, and the app asks waitingArt (the store's lock) while holding that one, so
        // calling it here under the store's lock froze both for good (a lock-order deadlock).
        taken(peerId, res)
        return res.needArt.keys
    }

    override fun takeArt(items: JsonArray) = synchronized(store) {
        var changed = false
        library.edit { e ->
            for (raw in items) {
                val item = raw as? JsonObject ?: continue
                val sha = item["sha"].str().orEmpty()
                val waiting = store.pendingArt.filterValues { it["sha"].str() == sha }.keys.toList()
                if (waiting.isEmpty()) continue
                val data = try {
                    SyncWire.unb64(item["data"].str()).also { require(SyncCrypto.sha256Hex(it) == sha) { "picture doesn't match its sha" } }
                } catch (ex: Exception) {
                    SyncWire.log("a picture didn't arrive whole: ${ex.message}")
                    continue
                }
                for (sid in waiting) {
                    val station = e.stations.firstOrNull { it.id == sid }
                    if (station == null) { store.pendingArt.remove(sid); continue }
                    // A file of its own for each station (section 6.1, desktop build 167).
                    val file = try {
                        SyncData.saveArt(artDir, sha, item["ext"].str() ?: "png", data, sid)
                    } catch (ex: Exception) {
                        SyncWire.log("couldn't save a picture: ${ex.message}")
                        continue
                    }
                    val pend = store.pendingArt.remove(sid) ?: continue
                    station["custom_art_path"] = JsonPrimitive(file.absolutePath)
                    val art = SyncData.artOf(station)
                    store.st.getOrPut(sid) { LinkedHashMap() }["art"]?.let { rec ->
                        store.st.getValue(sid)["art"] = rec.copy(fp = SyncData.fingerprint(if (art is JsonObject) art else pend))
                    }
                    changed = true
                }
            }
            if (changed) {
                saveStore(store)
                e.stationsChanged = true
            }
        }
    }

    /** Every picture this phone is still waiting for, from anyone. */
    fun waitingArt(): Collection<String> = synchronized(store) {
        store.pendingArt.values.mapNotNull { it["sha"].str() }.toSortedSet()
    }

    override fun waitingArt(peerId: String): Collection<String> =
        if (what(peerId).art) waitingArt() else emptyList()

    override fun artFile(peerId: String, sha: String): File? =
        if (what(peerId).art) artIndex[sha]?.takeIf { it.isFile } else null

    override fun forgotten(peerId: String) = forgot(peerId)
}
