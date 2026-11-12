package com.frequencies.core

import java.io.File
import java.security.MessageDigest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/**
 * Sync's data side (notes/sync-protocol.md sections 6 and 7), the same
 * rules as the desktop's sync_core.observe / build_state / merge:
 * every station field, shared setting and star carries a stamp
 * [t ms, device id], the newest edit wins per item, a deleted station
 * stays deleted, and History is a union.
 *
 * Nothing else in the app has to know about sync: [observe] finds this
 * phone's own edits by comparing each value with its fingerprint.
 */
object SyncData {
    const val PROTOCOL = 1
    const val TOMBSTONE_DAYS = 365L

    /**
     * Station keys that belong to one device (section 6.1). The phone's
     * own folder link ([LocalStations.ANDROID_FOLDER], a content:// link
     * only this phone can open) is this phone's too: never sent, never
     * taken.
     */
    val NEVER_SENT = setOf("id", "custom_art_path", "connect_worst", "art_checked", LocalStations.ANDROID_FOLDER)

    /** Section 6.2's first list: the only settings a phone sends or takes. */
    val SHARED_SETTINGS = listOf(
        "theme", "accent_source", "accent_fixed", "photo_pack", "band_logos",
        "lyrics_on", "artist_on", "clock_12h", "sort_mode", "sort_desc",
        "tag_colors", "spare_tags", "shuffle", "noise_between_stations",
        "noise_volume", "crossfade_ms", "auto_skip_broken",
        "auto_skip_after_tries", "stall_timeout", "health_recheck",
        "health_recheck_minutes", "normalize", "lyric_size",
        "shazam_history_limit", "sleep_minutes", "sleep_fade_seconds",
        // Whether stations use their 10-band equalizer values (desktop
        // build 167): the values travel with each station, so the switch does too.
        "eq_advanced",
    )
    // "local_station_ratio" (Custom station frequency) is gone from every
    // platform (section 6.2, user 2026-11-04): never sent, never taken.

    /** "Clear history" on this phone, in seconds (local only, like the desktop's key). */
    const val CLEARED_KEY = "history_cleared_at"

    // -- what to sync (section 6.4, desktop build 169) ---------------------

    /** sync_core.WHAT_PARTS: what this phone sends to and takes from one paired device. */
    val WHAT_PARTS = listOf("stations", "art", "settings", "favorites", "history")

    /** sync_core.SETTING_GROUPS: the Settings part in groups (the phone never has the desktop-only keys). */
    val SETTING_GROUPS: List<Pair<String, List<String>>> = listOf(
        "look" to listOf("theme", "accent_source", "accent_fixed", "clock_12h"),
        "playback" to listOf(
            "shuffle", "noise_between_stations", "noise_volume", "crossfade_ms", "normalize", "auto_skip_broken",
            "auto_skip_after_tries", "stall_timeout", "health_recheck", "health_recheck_minutes",
            "sleep_minutes", "sleep_fade_seconds", "eq_advanced",
        ),
        "lyrics" to listOf("lyrics_on", "artist_on", "photo_pack", "band_logos", "lyric_size", "shazam_history_limit"),
        "list" to listOf("sort_mode", "sort_desc", "tag_colors", "spare_tags", "jump_on_change"),
        "desktop" to listOf("companion_closed", "show_reminders", "artist_panel", "open_in_apps"),
    )
    private val GROUP_OF: Map<String, String> = SETTING_GROUPS.flatMap { (g, keys) -> keys.map { it to g } }.toMap()

    /**
     * One paired device's what-to-sync choice (sync_core.clean_what): every
     * part and group on or off; missing = on. Kept in sync-peers.json as the
     * desktop keeps it (`"what"`: the parts, plus `"groups"`).
     */
    data class What(val parts: Map<String, Boolean>, val groups: Map<String, Boolean>) {
        fun on(part: String): Boolean = parts[part] ?: true
        fun group(g: String): Boolean = groups[g] ?: true
        val stations get() = on("stations")
        val art get() = on("art")
        val settings get() = on("settings")
        val favorites get() = on("favorites")
        val history get() = on("history")

        fun settingAllowed(key: String): Boolean = settings && group(GROUP_OF[key] ?: "")

        fun toJson(): JsonObject = JsonObject(LinkedHashMap<String, JsonElement>().apply {
            WHAT_PARTS.forEach { put(it, JsonPrimitive(on(it))) }
            put("groups", JsonObject(SETTING_GROUPS.associate { (g, _) -> g to JsonPrimitive(group(g)) }))
        })

        /** The parts and groups that are off, for the log. */
        fun offList(): List<String> = WHAT_PARTS.filter { !on(it) } + SETTING_GROUPS.map { it.first }.filter { !group(it) }

        companion object {
            val ALL: What get() = cleanWhat(null)
        }
    }

    /** sync_core.clean_what: pictures need Saved Frequencies; Settings is off when every group is. */
    fun cleanWhat(raw: JsonElement?): What {
        val o = raw as? JsonObject ?: JsonObject(emptyMap())
        val groupsRaw = o["groups"] as? JsonObject ?: JsonObject(emptyMap())
        fun off(e: JsonElement?) = e is JsonPrimitive && !e.isString && e.content == "false"
        val parts = LinkedHashMap<String, Boolean>()
        WHAT_PARTS.forEach { parts[it] = !off(o[it]) }
        val groups = LinkedHashMap<String, Boolean>()
        SETTING_GROUPS.forEach { (g, _) -> groups[g] = !off(groupsRaw[g]) }
        if (parts["stations"] == false) parts["art"] = false
        if (groups.values.none { it }) parts["settings"] = false
        return What(parts, groups)
    }

    /** sync_core.nothing_what: sends nothing and takes nothing (a pairing waiting for its choice). */
    fun nothingWhat(): What = What(WHAT_PARTS.associateWith { false }, SETTING_GROUPS.associate { it.first to false })

    // -- untouched values (section 7, desktop build 169) ------------------

    /**
     * Shared keys a fresh phone doesn't write into settings.json, with the
     * values that mean "never set" (sync_core._EXTRA_DEFAULTS; a fresh
     * phone's theme is the shell default, which is OG = "midnight").
     */
    private val EXTRA_DEFAULTS: Map<String, List<JsonElement>> by lazy {
        mapOf(
            "theme" to listOf(JsonPrimitive("midnight"), JsonPrimitive(ShellThemes.DEFAULT)),
            "accent_source" to listOf(JsonPrimitive("artwork")),
            "accent_fixed" to listOf(JsonNull, JsonPrimitive("")),
            "photo_pack" to listOf(JsonPrimitive(false), JsonNull),
            "spare_tags" to listOf(JsonArray(emptyList()), JsonNull),
            "sort_desc" to listOf(JsonPrimitive(false), JsonNull),
            "companion_closed" to listOf(JsonArray(emptyList()), JsonNull),
            "show_reminders" to listOf(JsonPrimitive(true)),
            "open_in_apps" to listOf(JsonNull, JsonPrimitive("")),
        )
    }

    /** sync_core.setting_is_default: is this the value a fresh phone starts with? */
    fun settingIsDefault(key: String, value: JsonElement?): Boolean {
        val fp = fingerprint(value)
        Defaults.settings[key]?.let { if (fingerprint(it) == fp) return true }
        return EXTRA_DEFAULTS[key].orEmpty().any { fingerprint(it) == fp }
    }

    /**
     * The phone's default stations as a fresh profile starts with them
     * (LibraryFiles.defaultStations), {address: fields}, as [stationFields]
     * gives them, picture included. Made once, pictures in a scratch folder.
     * Tests can set their own.
     */
    @Volatile var shippedStations: () -> Map<String, Map<String, JsonElement>> = ::makeShipped
    private var shippedCache: Map<String, Map<String, JsonElement>>? = null

    @Synchronized
    private fun makeShipped(): Map<String, Map<String, JsonElement>> {
        shippedCache?.let { return it }
        val out = HashMap<String, Map<String, JsonElement>>()
        val scratch = java.nio.file.Files.createTempDirectory("freq-shipped-").toFile()
        try {
            var n = 0
            for (entry in Featured.readList(Defaults.defaultStationsText())) {
                try {
                    val s = Featured.toStation(entry, scratch) { "shipped-${n++}" }
                    Station.normalize(s, 0.0) { "shipped-${n++}" }
                    out[normUrl(s.url)] = stationFields(s)
                } catch (e: Exception) {
                    SyncWire.log("default station ${entry["name"].str()}: $e")
                }
            }
        } finally {
            scratch.deleteRecursively()
        }
        shippedCache = out
        return out
    }

    /**
     * sync_core.station_field_is_default: a value nobody chose? A default
     * station (same address as one a fresh profile starts with) is
     * compared with how it ships, its added_at always counts; any other
     * station with the station defaults. Names, addresses and the like never are.
     */
    fun stationFieldIsDefault(station: Station, field: String, value: JsonElement?): Boolean {
        val fp = fingerprint(value)
        val shipped = shippedStations()[normUrl(station.url)]
        if (shipped != null) {
            if (field == "added_at") return true
            shipped[field]?.let { return fingerprint(it) == fp }
        }
        if (field == "art") return value == null || value is JsonNull
        val d = Defaults.station[field] ?: return false
        return fingerprint(d) == fp
    }

    // -- stamps -------------------------------------------------------------

    /** [t ms, device]. A is newer than B by t, then by device id. */
    data class Stamp(val t: Long, val dev: String) {
        fun newerThan(other: Stamp?): Boolean =
            other == null || t > other.t || (t == other.t && dev > other.dev)

        fun json(): JsonArray = JsonArray(listOf(JsonPrimitive(t), JsonPrimitive(dev)))
    }

    /** A stamp and the fingerprint of the value it dates. */
    data class Rec(val stamp: Stamp, val fp: String)

    data class Fav(val starred: Boolean, val stamp: Stamp)

    /** sync-state.json: the stamps beside the data. */
    class Store {
        var clock = 0L
        val st = LinkedHashMap<String, LinkedHashMap<String, Rec>>()
        val set = LinkedHashMap<String, Rec>()
        val fav = LinkedHashMap<String, Fav>()
        val tomb = LinkedHashMap<String, Stamp>()
        var cleared: Stamp? = null
        /** Station id -> the {"sha","ext"} it is waiting for. */
        val pendingArt = LinkedHashMap<String, JsonObject>()
        /** The one-time repair of first-look stamps is done (sync_core `untouched_fix`, build 169). */
        var untouchedFix = false
        /** The one-time repair of pictures dated while they weren't synced is done (sync_core `filled_fix`, build 171). */
        var filledFix = false

        fun tick(now: Long): Long {
            clock = maxOf(now, clock + 1)
            return clock
        }

        fun toJson(): JsonObject {
            fun rec(r: Rec) = JsonArray(listOf(JsonPrimitive(r.stamp.t), JsonPrimitive(r.stamp.dev), JsonPrimitive(r.fp)))
            return JsonObject(linkedMapOf(
                "v" to JsonPrimitive(1),
                "clock" to JsonPrimitive(clock),
                "st" to JsonObject(st.mapValues { (_, f) -> JsonObject(f.mapValues { rec(it.value) }) }),
                "set" to JsonObject(set.mapValues { rec(it.value) }),
                "fav" to JsonObject(fav.mapValues { (_, f) ->
                    JsonArray(listOf(JsonPrimitive(f.starred), JsonPrimitive(f.stamp.t), JsonPrimitive(f.stamp.dev)))
                }),
                "tomb" to JsonObject(tomb.mapValues { it.value.json() }),
                "cleared" to (cleared?.json() ?: JsonNull),
                "pending_art" to JsonObject(pendingArt),
                "untouched_fix" to JsonPrimitive(if (untouchedFix) 1 else 0),
                "filled_fix" to JsonPrimitive(if (filledFix) 1 else 0),
            ))
        }

        companion object {
            fun from(json: JsonElement?): Store {
                val s = Store()
                val o = json as? JsonObject ?: return s
                s.clock = o["clock"].num()?.toLong() ?: 0
                fun rec(e: JsonElement?): Rec? {
                    val a = e as? JsonArray ?: return null
                    if (a.size < 3) return null
                    val t = a[0].num()?.toLong() ?: return null
                    return Rec(Stamp(t, a[1].str() ?: return null), a[2].str() ?: return null)
                }
                (o["st"] as? JsonObject)?.forEach { (sid, fields) ->
                    val m = LinkedHashMap<String, Rec>()
                    (fields as? JsonObject)?.forEach { (k, v) -> rec(v)?.let { m[k] = it } }
                    s.st[sid] = m
                }
                (o["set"] as? JsonObject)?.forEach { (k, v) -> rec(v)?.let { s.set[k] = it } }
                (o["fav"] as? JsonObject)?.forEach { (k, v) ->
                    val a = v as? JsonArray ?: return@forEach
                    stampOf(JsonArray(a.drop(1)))?.let { s.fav[k] = Fav(truthy(a.firstOrNull()), it) }
                }
                (o["tomb"] as? JsonObject)?.forEach { (k, v) -> stampOf(v)?.let { s.tomb[k] = it } }
                s.cleared = stampOf(o["cleared"])
                (o["pending_art"] as? JsonObject)?.forEach { (k, v) -> (v as? JsonObject)?.let { s.pendingArt[k] = it } }
                s.untouchedFix = truthy(o["untouched_fix"])
                s.filledFix = truthy(o["filled_fix"])
                return s
            }
        }
    }

    /** A stamp from the wire: [t, "dev"], or null when it isn't one. */
    fun stampOf(e: JsonElement?): Stamp? {
        val a = e as? JsonArray ?: return null
        if (a.size < 2) return null
        val t = a[0] as? JsonPrimitive ?: return null
        if (t.isString || t is JsonNull || t.booleanOrNull != null) return null
        val dev = a[1] as? JsonPrimitive ?: return null
        if (!dev.isString) return null
        return Stamp(t.content.toDoubleOrNull()?.toLong() ?: return null, dev.content)
    }

    /**
     * A stamp another device may send: [t >= 0, device]. [-1, dev] is never
     * sent (section 7, "Filled in"); one that arrives anyway is ignored
     * (sync_core._valid_stamp, desktop build 171).
     */
    fun wireStampOf(e: JsonElement?): Stamp? = stampOf(e)?.takeIf { it.t >= 0 }

    // -- fingerprints -----------------------------------------------------

    /**
     * A short hash of a value, the same for the same meaning: object keys
     * sorted, and 700 and 700.0 one number, so a value written back in
     * another spelling isn't taken for an edit.
     */
    fun fingerprint(value: JsonElement?): String {
        val sb = StringBuilder()
        canonical(value ?: JsonNull, sb)
        // The first 8 bytes as hex: String.format per byte was most of a look's time on a phone (build 188).
        val digest = MessageDigest.getInstance("SHA-1").digest(sb.toString().toByteArray(Charsets.UTF_8))
        val out = CharArray(16)
        for (i in 0 until 8) {
            val b = digest[i].toInt() and 0xff
            out[2 * i] = HEX[b ushr 4]
            out[2 * i + 1] = HEX[b and 0x0f]
        }
        return String(out)
    }

    private val HEX = "0123456789abcdef".toCharArray()

    private fun canonical(e: JsonElement, sb: StringBuilder) {
        when (e) {
            is JsonNull -> sb.append("null")
            is JsonObject -> {
                sb.append('{')
                e.keys.sorted().forEachIndexed { i, k ->
                    if (i > 0) sb.append(',')
                    sb.append(JsonPrimitive(k).toString()).append(':')
                    canonical(e.getValue(k), sb)
                }
                sb.append('}')
            }
            is JsonArray -> {
                sb.append('[')
                e.forEachIndexed { i, v -> if (i > 0) sb.append(','); canonical(v, sb) }
                sb.append(']')
            }
            is JsonPrimitive -> when {
                e.isString -> sb.append(e.toString())
                e.booleanOrNull != null -> sb.append(e.content)
                else -> {
                    val d = e.content.toDoubleOrNull()
                    if (d == null) sb.append(e.content)
                    else if (d == Math.floor(d) && !d.isInfinite() && Math.abs(d) < 1e15) sb.append(d.toLong())
                    else sb.append(d)
                }
            }
        }
    }

    fun normUrl(url: String?): String {
        val u = url.orEmpty().trim().lowercase()
        return if (u.endsWith("/")) u.dropLast(1) else u
    }

    // -- the made-up "art" field ------------------------------------------

    private val artCache = HashMap<Triple<String, Long, Long>, String>()

    /** A picture file bigger than this is sent as a smaller copy (section 6.1, desktop build 167). */
    const val ART_SEND_MAX = 8L * 1024 * 1024
    /** The copy's long side at most. */
    const val ART_SHRINK_PX = 2048

    /**
     * Makes the smaller copy of a big picture: (the big file, its sha) ->
     * the copy, or null when it can't. The app sets one that uses
     * Android's own image decoder (JPEG quality 90, PNG when the picture
     * has transparency, named after the big file's sha so it is made once
     * and keeps its sha); without one a big picture isn't sent.
     */
    @Volatile var shrink: (File, String) -> File? = { _, _ -> null }

    private fun shaOf(file: File): String? {
        val key = Triple(file.path, file.length(), file.lastModified())
        synchronized(artCache) { artCache[key] }?.let { return it }
        val digest = MessageDigest.getInstance("SHA-256")
        try {
            file.inputStream().use { input ->
                val buf = ByteArray(1 shl 16)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    digest.update(buf, 0, n)
                }
            }
        } catch (e: Exception) {
            return null
        }
        return digest.digest().joinToString("") { "%02x".format(it) }.also { synchronized(artCache) { artCache[key] = it } }
    }

    /** What this phone sends for the station's own picture (sync_core.art_file): the file, its sha, its extension. */
    data class ArtFile(val file: File, val sha: String, val ext: String)

    /**
     * sync_core.art_file: the station's own picture, or the smaller copy
     * when the file is over [ART_SEND_MAX] (one `art` message holds at
     * most 12 MB of base64, so a big photo never arrived). Null when there
     * is none to send.
     */
    fun artFile(station: Station): ArtFile? {
        val path = station.customArtPath
        if (path.isEmpty()) return null
        val file = File(path)
        if (!file.isFile) return null
        val sha = shaOf(file) ?: return null
        if (file.length() > ART_SEND_MAX) {
            val small = runCatching { shrink(file, sha) }.getOrNull()?.takeIf { it.isFile && it.length() <= ART_SEND_MAX } ?: return null
            val smallSha = shaOf(small) ?: return null
            return ArtFile(small, smallSha, artExt(small.path))
        }
        return ArtFile(file, sha, artExt(path))
    }

    /** {"sha","ext"} of the station's own picture (or its smaller copy), or null (JSON null). */
    fun artOf(station: Station): JsonElement {
        val found = artFile(station) ?: return JsonNull
        return JsonObject(linkedMapOf("sha" to JsonPrimitive(found.sha), "ext" to JsonPrimitive(found.ext)))
    }

    /** The smaller copy's file name for a big picture with [sha] (sync_core._smaller_copy). */
    fun smallCopyName(sha: String, ext: String): String = "sync-small-${sha.take(16)}.$ext"

    /** sync_core.art_of's extension: the file's, lowercase, else "png". */
    fun artExt(path: String): String {
        val name = Backup.baseName(path)
        val dot = name.lastIndexOf('.')
        val ext = if (dot > 0) name.substring(dot + 1).lowercase() else ""
        return if (ext.isNotEmpty() && ext.length <= 5 && ext.all { it.isLetterOrDigit() }) ext else "png"
    }

    /**
     * sync_core.save_art: a picture from another device into [artDir]; its
     * file. One file per station (desktop build 167): two stations with the
     * same picture get a file each, so cropping one never changes the other.
     */
    fun saveArt(artDir: File, sha: String, ext: String, data: ByteArray, stationId: String = ""): File {
        require(SyncCrypto.sha256Hex(data) == sha) { "picture doesn't match its sha" }
        val e = ext.lowercase().takeIf { it.isNotEmpty() && it.length <= 5 && it.all(Char::isLetterOrDigit) } ?: "png"
        val sid = stationId.filter { it.isLetterOrDigit() || it == '-' }.take(40)
        artDir.mkdirs()
        val file = File(artDir, if (sid.isNotEmpty()) "sync-$sid-${sha.take(16)}.$e" else "sync-${sha.take(16)}.$e")
        val tmp = File(file.path + ".tmp")
        tmp.writeBytes(data)
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
        return file
    }

    /** {sha: file} for every station picture this phone can send (a big one's smaller copy). */
    fun artIndex(stations: List<Station>): Map<String, File> {
        val out = HashMap<String, File>()
        for (s in stations) {
            val found = artFile(s) ?: continue
            out[found.sha] = found.file
        }
        return out
    }

    fun stationFields(s: Station, art: (Station) -> JsonElement = ::artOf): LinkedHashMap<String, JsonElement> {
        val out = LinkedHashMap<String, JsonElement>()
        for ((k, v) in s.data) if (k !in NEVER_SENT && !k.startsWith("_")) out[k] = v
        out["art"] = art(s)
        return out
    }

    private fun favoritesNow(history: List<JsonObject>): Map<String, Boolean> {
        val favs = LinkedHashMap<String, Boolean>()
        for (e in history) {
            val key = SongHistory.entryKey(e)
            if (key == "|") continue
            favs[key] = (favs[key] ?: false) || SongHistory.isFavorite(e)
        }
        return favs
    }

    // -- section 7: this phone's own edits ---------------------------------

    /**
     * sync_core.repair_first_scan, once (desktop build 169): builds 164-168
     * dated every value [now, me] the first time they looked at it, so a
     * value nobody ever changed looked like a fresh edit. A stamp of this
     * phone's from that first look (its oldest own stamp, and what follows
     * within two minutes), on a value that is still the default and
     * unchanged since, becomes [0, me]. True if the store changed.
     */
    fun repairFirstScan(stations: List<Station>, settings: Settings, store: Store, me: String,
                        art: (Station) -> JsonElement = ::artOf): Boolean {
        if (store.untouchedFix) return false
        store.untouchedFix = true
        val own = store.st.values.flatMap { it.values }.filter { it.stamp.dev == me && it.stamp.t > 0L }.map { it.stamp.t } +
            store.set.values.filter { it.stamp.dev == me && it.stamp.t > 0L }.map { it.stamp.t }
        if (own.isEmpty()) return true
        val limit = own.min() + 120_000 + own.size
        var fixed = 0
        val byId = stations.associateBy { it.id }
        for ((sid, rec) in store.st) {
            val station = byId[sid] ?: continue
            val fields = stationFields(station, art)
            for ((field, r) in rec.entries.toList()) {
                if (r.stamp.dev != me || r.stamp.t <= 0 || r.stamp.t > limit) continue
                val value = fields[field] ?: continue
                if (r.fp == fingerprint(value) && stationFieldIsDefault(station, field, value)) {
                    rec[field] = Rec(Stamp(0, me), r.fp)
                    fixed++
                }
            }
        }
        for ((key, r) in store.set.entries.toList()) {
            if (r.stamp.dev != me || r.stamp.t <= 0 || r.stamp.t > limit) continue
            val value = settings.data[key] ?: continue
            if (r.fp == fingerprint(value) && settingIsDefault(key, value)) {
                store.set[key] = Rec(Stamp(0, me), r.fp)
                fixed++
            }
        }
        SyncWire.log("untouched values from the first look marked never edited: $fixed")
        return true
    }

    /** A stamp's t for a value only filled in here, never chosen (section 7, "Filled in"). */
    const val FILLED = -1L

    /**
     * sync_core.repair_filled_in, once (desktop build 171, task 226): a
     * station from another device (its address stamp isn't this phone's)
     * with no picture here and a picture stamp of this phone's gets
     * [-1, me]: never sent, and any picture stamp from another device wins,
     * so a picture that never came (it was sent without one) comes later.
     */
    fun repairFilledIn(stations: List<Station>, store: Store, me: String,
                       art: (Station) -> JsonElement = ::artOf): Boolean {
        if (store.filledFix) return false
        store.filledFix = true
        var fixed = 0
        val byId = stations.associateBy { it.id }
        for ((sid, rec) in store.st) {
            val station = byId[sid] ?: continue
            val a = rec["art"] ?: continue
            val u = rec["url"] ?: continue
            if (a.stamp.dev == me && a.stamp.t >= 0 && u.stamp.dev != me && !truthy(art(station))) {
                rec["art"] = Rec(Stamp(FILLED, me), fingerprint(art(station)))
                fixed++
            }
        }
        SyncWire.log("pictures dated while they weren't synced, now left to the other device: $fixed")
        return true
    }

    /** Stamp this phone's edits since the last look. True if there were any. */
    fun observe(stations: List<Station>, settings: Settings, history: List<JsonObject>, store: Store, me: String,
                nowMs: Long = System.currentTimeMillis(), art: (Station) -> JsonElement = ::artOf): Boolean {
        var changed = repairFirstScan(stations, settings, store, me, art)
        changed = repairFilledIn(stations, store, me, art) || changed
        // One stamp for the whole look (the desktop's fix: one per field ran the clock ahead).
        val stamp = Stamp(store.tick(nowMs), me)
        // First look at a value still at the phone's default: "never edited" (section 7).
        val never = Stamp(0, me)
        // An edit is never dated before the stamp it replaces (desktop build 169).
        fun edited(old: Rec): Stamp {
            if (old.stamp.t + 1 <= stamp.t) return stamp
            store.clock = maxOf(store.clock, old.stamp.t + 1)
            return Stamp(old.stamp.t + 1, me)
        }
        val seen = HashSet<String>()
        for (s in stations) {
            val sid = s.id
            if (sid.isEmpty()) continue
            seen.add(sid)
            val rec = store.st.getOrPut(sid) { LinkedHashMap() }
            val pending = sid in store.pendingArt
            for ((field, value) in stationFields(s, art)) {
                if (field == "art" && pending) continue
                val fp = fingerprint(value)
                val old = rec[field]
                if (old == null) {
                    rec[field] = Rec(if (stationFieldIsDefault(s, field, value)) never else stamp, fp)
                    changed = true
                } else if (old.fp != fp) {
                    rec[field] = Rec(edited(old), fp)
                    changed = true
                }
            }
        }
        for (sid in store.st.keys.filter { it !in seen }) {
            store.st.remove(sid)
            store.pendingArt.remove(sid)
            if (sid !in store.tomb) store.tomb[sid] = stamp
            changed = true
        }
        for (key in SHARED_SETTINGS) {
            val value = settings.data[key] ?: continue
            val fp = fingerprint(value)
            val old = store.set[key]
            if (old == null) {
                store.set[key] = Rec(if (settingIsDefault(key, value)) never else stamp, fp)
                changed = true
            } else if (old.fp != fp) {
                store.set[key] = Rec(edited(old), fp)
                changed = true
            } else if (old.stamp.t == 0L && old.stamp.dev == me && !settingIsDefault(key, value)) {
                // The phone's default changed under an untouched value
                // (shuffle, build 182 / desktop 181): it is still what this
                // profile had, so it beats a fresh device's untouched default
                // ([1, me] > [0, …]) but loses to any real edit. Section 7.
                store.set[key] = Rec(Stamp(1, me), fp)
                changed = true
            }
        }
        for ((key, starred) in favoritesNow(history)) {
            val old = store.fav[key]
            if (old == null) {
                if (starred) { store.fav[key] = Fav(true, stamp); changed = true }
            } else if (old.starred != starred) {
                store.fav[key] = Fav(starred, stamp)
                changed = true
            }
        }
        val cleared = ((settings.data[CLEARED_KEY].num() ?: 0.0) * 1000).toLong()
        if (cleared > 0 && (store.cleared == null || cleared > store.cleared!!.t)) {
            store.cleared = Stamp(cleared, me)
            changed = true
        }
        val cutoff = nowMs - TOMBSTONE_DAYS * 86_400_000L
        store.tomb.entries.removeAll { it.value.t < cutoff }
        return changed
    }

    // -- section 6: the state message ---------------------------------------

    /**
     * The state message. Call [observe] first. [what]: this phone's
     * what-to-sync choice for the device it goes to; a part that is off is
     * left out (section 6.4).
     */
    fun buildState(stations: List<Station>, settings: Settings, history: List<JsonObject>, store: Store,
                   nowMs: Long = System.currentTimeMillis(), art: (Station) -> JsonElement = ::artOf,
                   what: What = What.ALL): JsonObject {
        val out = ArrayList<JsonElement>()
        for (s in if (what.stations) stations.sortedBy { it.id } else emptyList()) {
            val sid = s.id
            val rec = store.st[sid] ?: emptyMap<String, Rec>()
            val fields = LinkedHashMap<String, JsonElement>()
            for ((field, v) in stationFields(s, art)) {
                val r = rec[field] ?: continue
                // Below "never edited": a picture this phone hasn't taken yet (see merge).
                if ((field == "art" && !what.art) || r.stamp.t < 0) continue
                val value = if (field == "art" && sid in store.pendingArt) store.pendingArt.getValue(sid) else v
                fields[field] = JsonArray(listOf(value, JsonPrimitive(r.stamp.t), JsonPrimitive(r.stamp.dev)))
            }
            out.add(JsonObject(linkedMapOf("id" to JsonPrimitive(sid), "f" to JsonObject(fields))))
        }
        for ((sid, stamp) in if (what.stations) store.tomb.entries.sortedBy { it.key } else emptyList()) {
            out.add(JsonObject(linkedMapOf("id" to JsonPrimitive(sid), "del" to stamp.json())))
        }
        val set = LinkedHashMap<String, JsonElement>()
        for (key in SHARED_SETTINGS) {
            if (!what.settingAllowed(key)) continue
            val value = settings.data[key] ?: continue
            val r = store.set[key] ?: continue
            set[key] = JsonArray(listOf(value, JsonPrimitive(r.stamp.t), JsonPrimitive(r.stamp.dev)))
        }
        val favs = if (!what.favorites) emptyMap() else store.fav.mapValues { (_, f) ->
            JsonArray(listOf(JsonPrimitive(f.starred), JsonPrimitive(f.stamp.t), JsonPrimitive(f.stamp.dev)))
        }
        val rows = (if (what.history) history else emptyList()).filter { SongHistory.title(it).isNotEmpty() || SongHistory.artist(it).isNotEmpty() }.map {
            JsonObject(linkedMapOf(
                "title" to JsonPrimitive(SongHistory.title(it)),
                "artist" to JsonPrimitive(SongHistory.artist(it)),
                "station" to JsonPrimitive(SongHistory.station(it)),
                "at" to floatJson(SongHistory.at(it)),
                "apple_music_url" to JsonPrimitive(SongHistory.appleMusicUrl(it)),
                "cover_url" to JsonPrimitive(SongHistory.coverUrl(it)),
            ))
        }
        return JsonObject(linkedMapOf(
            "t" to JsonPrimitive("state"),
            "v" to JsonPrimitive(PROTOCOL),
            "platform" to JsonPrimitive("android"),
            "now" to JsonPrimitive(nowMs),
            "stations" to JsonArray(out),
            "settings" to JsonObject(set),
            "favorites" to JsonObject(favs),
            "history" to JsonArray(rows),
            "history_cleared" to (store.cleared?.takeIf { what.history }?.json() ?: JsonNull),
        ))
    }

    /** What a state says, without when it was sent. */
    fun digest(state: JsonObject): String = fingerprint(JsonObject(state.filterKeys { it != "now" }))

    // -- section 7: taking another device's state --------------------------

    /** This phone's copy before the merge, the incoming id, this phone's id. */
    private class Twins(val mine: Station, val remoteId: String, val localId: String)

    /** sync_core._tag_union: both lists, first spelling kept, empty ones dropped. */
    fun tagUnion(first: List<JsonElement>, second: List<JsonElement>): List<JsonElement> {
        val out = ArrayList<JsonElement>()
        val seen = HashSet<String>()
        for (tag in first + second) {
            val p = tag as? JsonPrimitive ?: continue
            if (!p.isString || p.content.isBlank()) continue
            if (seen.add(p.content.trim().lowercase())) out.add(p)
        }
        return out
    }

    /**
     * sync_core._join_twins (section 6.1 "Joining twins", desktop build
     * 165): one station under two ids just became one. Fields went to the
     * newest stamp; now play_count and last_played take the larger of the
     * two copies, and tags both lists (the smaller id's copy first, so
     * both devices get the same list). A joined value that differs from
     * what the stamps chose gets a fresh stamp of this phone's, so it wins
     * on the other device too.
     */
    private fun joinTwins(station: Station, rec: MutableMap<String, Rec>, twins: Twins, fields: JsonObject,
                          store: Store, me: String, nowMs: Long, out: Merged) {
        val mine = twins.mine
        fun theirs(field: String): Pair<Boolean, JsonElement?> {
            val triple = fields[field] as? JsonArray ?: return false to null
            if (triple.size < 3 || stampOf(JsonArray(triple.drop(1))) == null) return false to null
            return true to triple[0]
        }
        val joined = LinkedHashMap<String, JsonElement>()
        val (pcHas, pc) = theirs("play_count")
        if (pcHas || "play_count" in mine.data) {
            joined["play_count"] = intJson(maxOf(intOf(mine["play_count"]), intOf(pc)))
        }
        val (lpHas, lp) = theirs("last_played")
        if (lpHas || "last_played" in mine.data) {
            joined["last_played"] = floatJson(maxOf(mine["last_played"].num() ?: 0.0, lp.num() ?: 0.0))
        }
        val (tagsHas, tags) = theirs("tags")
        if (tagsHas || "tags" in mine.data) {
            val own = (mine["tags"] as? JsonArray).orEmpty()
            val other = (tags as? JsonArray).orEmpty()
            joined["tags"] = JsonArray(if (twins.localId < twins.remoteId) tagUnion(own, other) else tagUnion(other, own))
        }
        for ((field, value) in joined) {
            if (fingerprint(station[field]) == fingerprint(value) && field in station.data) continue
            station[field] = value
            var t = store.tick(nowMs)
            rec[field]?.let { old -> if (old.stamp.t + 1 > t) { t = old.stamp.t + 1; store.clock = maxOf(store.clock, t) } }
            rec[field] = Rec(Stamp(t, me), fingerprint(value))
            out.stations = true
        }
    }

    /** sync_core._number(value, int): a count, 0 when it isn't one. */
    private fun intOf(e: JsonElement?): Long = e.num()?.toLong() ?: 0L

    class Merged {
        var stations = false
        val settings = LinkedHashSet<String>()
        var history = false
        /** sha -> station ids waiting for that picture. */
        val needArt = LinkedHashMap<String, MutableList<String>>()
        /** old station id -> the id kept. */
        val renamed = LinkedHashMap<String, String>()
        val removed = LinkedHashSet<String>()
        var clockAheadMs = 0L
        val changed get() = stations || settings.isNotEmpty() || history
    }

    /**
     * Take what's newer in another device's [state]. Edits [stations],
     * [settings] and [history] in place. [pathHere] says whether a music
     * folder station's own folder is on this device (then an incoming
     * `local_path` doesn't replace it).
     */
    fun merge(
        stations: MutableList<Station>,
        settings: Settings,
        history: MutableList<JsonObject>,
        store: Store,
        me: String,
        state: JsonObject,
        historyLimit: Int = SongHistory.HARD_CAP,
        nowMs: Long = System.currentTimeMillis(),
        art: (Station) -> JsonElement = ::artOf,
        pathHere: (Station) -> Boolean = { s -> s["local_path"].str().orEmpty().trim().let { it.isNotEmpty() && File(it).exists() } },
        newId: () -> String = { java.util.UUID.randomUUID().toString() },
        /** This phone's what-to-sync choice for the sender: a part that is off is not taken (section 6.4). */
        what: What = What.ALL,
    ): Merged {
        val out = Merged()
        (state["now"].num()?.toLong())?.let { out.clockAheadMs = it - nowMs }

        val remote = (if (what.stations) state["stations"] as? JsonArray else null).orEmpty().mapNotNull { it as? JsonObject }
            .filter { it["id"].str().orEmpty().isNotEmpty() }
        val remoteIds = remote.map { it["id"].str()!! }.toSet()
        val byId = LinkedHashMap<String, Station>()
        stations.forEach { byId[it.id] = it }

        // Deletes first: a tombstone beats everything, for good.
        val goneUrls = LinkedHashMap<String, String>()
        val goneHere = HashMap<String, Station>()      // address -> a station deleted just now
        for (r in remote) {
            val sid = r["id"].str()!!
            val stamp = wireStampOf(r["del"]) ?: continue
            if (sid !in store.tomb || stamp.newerThan(store.tomb[sid])) store.tomb[sid] = stamp
            byId.remove(sid)?.let { gone ->
                goneUrls[sid] = normUrl(gone.url)
                goneHere[normUrl(gone.url)] = gone
                stations.remove(gone)
                out.stations = true
                out.removed.add(sid)
            }
            store.st.remove(sid)
            store.pendingArt.remove(sid)
        }

        for (r in remote) {
            val sid = r["id"].str()!!
            if (wireStampOf(r["del"]) != null || sid in store.tomb) continue
            val fields = r["f"] as? JsonObject ?: JsonObject(emptyMap())
            var target = sid
            var twins: Twins? = null
            if (sid !in byId) {
                // One station under two ids (section 6.1): keep the smaller.
                val url = normUrl(((fields["url"] as? JsonArray)?.firstOrNull()).str())
                val twin = if (url.isEmpty()) null
                else stations.firstOrNull { normUrl(it.url) == url && it.id !in remoteIds }
                if (twin != null) {
                    val tid = twin.id
                    twins = Twins(twin.copy(), sid, tid)
                    if (sid < tid) {
                        twin["id"] = JsonPrimitive(sid)
                        byId.remove(tid)
                        byId[sid] = twin
                        store.st[sid] = store.st.remove(tid) ?: LinkedHashMap()
                        store.pendingArt.remove(tid)?.let { store.pendingArt[sid] = it }
                        store.tomb[tid] = Stamp(store.tick(nowMs), me)
                        out.renamed[tid] = sid
                        out.stations = true
                    } else {
                        store.tomb[sid] = Stamp(store.tick(nowMs), me)
                        target = tid
                    }
                } else {
                    val fresh = Station(linkedMapOf("id" to JsonPrimitive(sid)))
                    // Its twin here was deleted by this same state (the other
                    // device joined them first): keep this phone's own keys,
                    // so its picture file isn't lost and asked for again (desktop build 169).
                    val was = if (url.isNotEmpty()) goneHere[url] else null
                    if (was != null) {
                        for (key in NEVER_SENT - "id" + "local_path") {
                            val v = was.data[key]
                            if (v != null && truthy(v)) fresh[key] = v
                        }
                    }
                    stations.add(fresh)
                    byId[sid] = fresh
                    store.st[sid] = LinkedHashMap()
                    out.stations = true
                }
            }
            val station = byId.getValue(target)
            val rec = store.st.getOrPut(target) { LinkedHashMap() }
            for ((field, raw) in fields) {
                if (field in NEVER_SENT || (field == "art" && !what.art)) continue
                val triple = raw as? JsonArray ?: continue
                if (triple.size < 3) continue
                val stamp = wireStampOf(JsonArray(triple.drop(1))) ?: continue
                val value = triple[0]
                if (!stamp.newerThan(rec[field]?.stamp)) continue
                if (field == "art") {
                    rec["art"] = Rec(stamp, fingerprint(value))
                    val have = art(station) as? JsonObject
                    val obj = value as? JsonObject
                    if (!truthy(value)) {
                        store.pendingArt.remove(target)
                        if (station.customArtPath.isNotEmpty()) {
                            station["custom_art_path"] = JsonPrimitive("")
                            out.stations = true
                        }
                    } else if (obj == null || obj["sha"].str().isNullOrEmpty()) {
                        continue
                    } else if (have != null && have["sha"].str() == obj["sha"].str()) {
                        store.pendingArt.remove(target)
                    } else {
                        val sha = obj["sha"].str()!!
                        store.pendingArt[target] = JsonObject(linkedMapOf(
                            "sha" to JsonPrimitive(sha),
                            "ext" to JsonPrimitive(obj["ext"].str()?.ifEmpty { null } ?: "png"),
                        ))
                        out.needArt.getOrPut(sha) { ArrayList() }.add(target)
                    }
                    continue
                }
                if (field == "local_path") {
                    val mine = station["local_path"].str().orEmpty().trim()
                    if (mine.isNotEmpty() && pathHere(station) && mine != value.str()) {
                        // Each device keeps its own music folder.
                        rec[field] = Rec(stamp, fingerprint(station["local_path"]))
                        continue
                    }
                }
                station[field] = value
                rec[field] = Rec(stamp, fingerprint(value))
                out.stations = true
            }
            // A station whose picture didn't come with it (Station pictures are off, or the
            // sender has none to send because it got the station without one): its "no
            // picture" is nobody's choice, so it is dated below never edited ([-1, me], never
            // sent) and the picture comes once it is sent. Dated [0, me], the bigger device id
            // would keep "no picture" and clear the other's. Build 170 did it only with
            // pictures off; desktop 171 (task 226) for every station.
            if ("art" !in rec) {
                rec["art"] = Rec(Stamp(FILLED, me), fingerprint(art(station)))
            }
            twins?.let { joinTwins(station, rec, it, fields, store, me, nowMs, out) }
        }

        // The other side kept a twin under the smaller id and sent this
        // one's tombstone: what named the gone id names the kept one.
        for ((gone, url) in goneUrls) {
            if (url.isEmpty() || gone in out.renamed) continue
            stations.firstOrNull { normUrl(it.url) == url && it.id != gone }?.let { out.renamed[gone] = it.id }
        }

        // New stations get the keys the rest of the app expects.
        if (out.stations) stations.forEach { Station.normalize(it, nowMs / 1000.0, newId) }
        if (out.renamed.isNotEmpty()) {
            for (s in stations) {
                val old = s["ad_filler_station_id"].str()
                if (old != null && old in out.renamed) s["ad_filler_station_id"] = JsonPrimitive(out.renamed.getValue(old))
            }
            for (key in listOf("last_station_id", "sleep_wake_station_id")) {
                val old = settings.data[key].str()
                if (old != null && old in out.renamed) settings.data[key] = JsonPrimitive(out.renamed.getValue(old))
            }
        }

        val remoteSet = state["settings"] as? JsonObject ?: JsonObject(emptyMap())
        for (key in SHARED_SETTINGS) {
            if (!what.settingAllowed(key)) continue
            val triple = remoteSet[key] as? JsonArray ?: continue
            if (triple.size < 3) continue
            val stamp = wireStampOf(JsonArray(triple.drop(1))) ?: continue
            if (!stamp.newerThan(store.set[key]?.stamp)) continue
            store.set[key] = Rec(stamp, fingerprint(triple[0]))
            if (fingerprint(settings.data[key]) != fingerprint(triple[0]) || key !in settings.data) {
                settings.data[key] = triple[0]
                out.settings.add(key)
            }
        }

        // Stars, newest per song.
        val favs = (if (what.favorites) state["favorites"] as? JsonObject else null) ?: JsonObject(emptyMap())
        for ((key, raw) in favs) {
            val triple = raw as? JsonArray ?: continue
            if (triple.size < 3) continue
            val stamp = wireStampOf(JsonArray(triple.drop(1))) ?: continue
            val old = store.fav[key]
            if (old == null || stamp.newerThan(old.stamp)) store.fav[key] = Fav(truthy(triple[0]), stamp)
        }
        if (what.history) wireStampOf(state["history_cleared"])?.let { if (it.newerThan(store.cleared)) store.cleared = it }

        // History: the union, then the rules of section 6.3.
        fun rowId(e: JsonObject) = SongHistory.entryKey(e) + "|" + Math.floor(SongHistory.at(e)).toLong()
        fun shape(rows: List<JsonObject>) = rows.map { rowId(it) to SongHistory.isFavorite(it) }
        val before = shape(history)
        val known = history.map(::rowId).toMutableSet()
        val merged = ArrayList<JsonObject>(history)
        for (e in (if (what.history) state["history"] as? JsonArray else null).orEmpty().mapNotNull { it as? JsonObject }) {
            if (e["title"].str().isNullOrEmpty() && e["artist"].str().isNullOrEmpty()) continue
            val rid = rowId(e)
            if (!known.add(rid)) continue
            merged.add(JsonObject(linkedMapOf(
                "title" to JsonPrimitive(e["title"].str().orEmpty()),
                "artist" to JsonPrimitive(e["artist"].str().orEmpty()),
                "station" to JsonPrimitive(e["station"].str().orEmpty()),
                "at" to floatJson(e["at"].num() ?: 0.0),
                "apple_music_url" to JsonPrimitive(e["apple_music_url"].str().orEmpty()),
                "cover_url" to JsonPrimitive(e["cover_url"].str().orEmpty()),
                "favorite" to JsonPrimitive(false),
            )))
        }
        for (i in merged.indices) {
            val f = store.fav[SongHistory.entryKey(merged[i])] ?: continue
            if (SongHistory.isFavorite(merged[i]) != f.starred || merged[i]["favorite"] !is JsonPrimitive) {
                merged[i] = JsonObject(LinkedHashMap(merged[i]).apply { put("favorite", JsonPrimitive(f.starred)) })
            }
        }
        val cut = store.cleared?.t ?: 0L
        merged.sortByDescending { SongHistory.at(it) }
        val kept = ArrayList<JsonObject>()
        val starredSeen = HashSet<String>()
        for (e in merged) {
            val key = SongHistory.entryKey(e)
            if (SongHistory.isFavorite(e)) {
                if (!starredSeen.add(key)) continue
            } else if (cut > 0 && SongHistory.at(e) * 1000 < cut) {
                continue
            }
            kept.add(e)
        }
        val trimmed = SongHistory.trim(kept, historyLimit)
        if (shape(trimmed) != before) {
            history.clear()
            history.addAll(trimmed)
            out.history = true
        }
        return out
    }

    // -- section 6.1: two default stations' old addresses (desktop build 171, task 227) --

    /**
     * {address without the query string: the address the phone ships}, for
     * the default stations whose shipped address has a query string (New Age
     * Radio and Dare-FM). Builds 145-172 shipped them without it, so the same
     * station on a phone and a computer never joined as twins. Tests can set their own.
     */
    @Volatile var addressFixes: () -> Map<String, String> = ::makeAddressFixes
    private var fixesCache: Map<String, String>? = null

    @Synchronized
    private fun makeAddressFixes(): Map<String, String> {
        fixesCache?.let { return it }
        val out = HashMap<String, String>()
        runCatching {
            for (entry in Featured.readList(Defaults.defaultStationsText())) {
                val url = entry["url"].str().orEmpty().trim()
                // Only stream addresses: the YouTube defaults (desktop 174) have a query string
                // too, and all six share one base ("…/playlist"), so they are left out.
                if ("?" in url && !url.startsWith("youtube://")) out[normUrl(url.substringBefore("?"))] = url
            }
        }
        fixesCache = out
        return out
    }

    /**
     * sync_core.join_old_addresses: one station under two ids whose addresses
     * differ only by the query string of a default station's shipped address:
     * joined as rule 6.1 does (smaller id kept, fields by stamp, play count,
     * last played and tags joined, the other id tombstoned), and the kept one
     * gets the shipped address. Only for those default stations; the general
     * twin rule still compares whole addresses.
     */
    fun joinOldAddresses(stations: MutableList<Station>, settings: Settings, store: Store, me: String,
                         nowMs: Long = System.currentTimeMillis()): Merged {
        val out = Merged()
        val fixes = addressFixes()
        if (fixes.isEmpty()) return out
        val groups = LinkedHashMap<String, MutableList<Station>>()
        for (s in stations) {
            val url = normUrl(s.url)
            val base = normUrl(url.substringBefore("?"))
            val right = fixes[base] ?: continue
            if (url == base || url == normUrl(right)) groups.getOrPut(base) { ArrayList() }.add(s)
        }
        for ((base, group) in groups) {
            if (group.map { normUrl(it.url) }.toSet().size < 2) continue   // same address twice: rule 6.1's own case
            group.sortBy { it.id }
            val keep = group[0]
            val kid = keep.id
            val krec = store.st.getOrPut(kid) { LinkedHashMap() }
            for (other in group.drop(1)) {
                val oid = other.id
                val orec = store.st[oid] ?: LinkedHashMap()
                val before = keep.copy()
                val joined = LinkedHashMap<String, JsonElement>()
                for ((field, r) in orec) {
                    if (field in NEVER_SENT || !r.stamp.newerThan(krec[field]?.stamp)) continue
                    if (field == "art") {
                        krec["art"] = r
                        keep["custom_art_path"] = other["custom_art_path"] ?: JsonPrimitive("")
                        store.pendingArt.remove(kid)
                        store.pendingArt[oid]?.let { store.pendingArt[kid] = it }
                        continue
                    }
                    other[field]?.let { keep[field] = it; krec[field] = r }
                }
                for (field in listOf("play_count", "last_played", "tags")) {
                    val r = orec[field] ?: continue
                    val v = other[field] ?: continue
                    joined[field] = JsonArray(listOf(v) + r.stamp.json())
                }
                joinTwins(keep, krec, Twins(before, oid, kid), JsonObject(joined), store, me, nowMs, out)
                stations.remove(other)
                store.st.remove(oid)
                store.pendingArt.remove(oid)
                store.tomb[oid] = Stamp(store.tick(nowMs), me)
                out.renamed[oid] = kid
                out.removed.add(oid)
                out.stations = true
                SyncWire.log("joined '${keep.name}': its old address without the query string was the same station")
            }
            val right = fixes.getValue(base)
            if (keep.url != right) {
                keep["url"] = JsonPrimitive(right)
                var t = store.tick(nowMs)
                krec["url"]?.let { old -> if (old.stamp.t + 1 > t) { t = old.stamp.t + 1; store.clock = maxOf(store.clock, t) } }
                krec["url"] = Rec(Stamp(t, me), fingerprint(JsonPrimitive(right)))
                out.stations = true
            }
        }
        if (out.renamed.isNotEmpty()) {
            for (s in stations) {
                val old = s["ad_filler_station_id"].str()
                if (old != null && old in out.renamed) s["ad_filler_station_id"] = JsonPrimitive(out.renamed.getValue(old))
            }
            for (key in listOf("last_station_id", "sleep_wake_station_id")) {
                val old = settings.data[key].str()
                if (old != null && old in out.renamed) {
                    settings.data[key] = JsonPrimitive(out.renamed.getValue(old))
                    out.settings.add(key)
                }
            }
        }
        return out
    }
}
