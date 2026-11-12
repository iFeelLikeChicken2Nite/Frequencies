package com.frequencies.core

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * One saved frequency, kept as the desktop's JSON object.
 *
 * Only the keys the phone uses get typed accessors; every other key
 * (skins, ad filler, local folder, buffering, keys a newer desktop adds)
 * stays in [data] untouched, in its place, so a station that visits the
 * phone goes back to the desktop the same.
 */
class Station(val data: LinkedHashMap<String, JsonElement>) {

    constructor(obj: JsonObject) : this(LinkedHashMap(obj))

    fun toJson(): JsonObject = JsonObject(LinkedHashMap(data))

    fun copy(): Station = Station(LinkedHashMap(data))

    operator fun get(key: String): JsonElement? = data[key]
    operator fun set(key: String, value: JsonElement) { data[key] = value }

    val id: String get() = data["id"].str().orEmpty()
    val name: String get() = data["name"].str().orEmpty()
    val url: String get() = data["url"].str().orEmpty()
    val customName: String? get() = data["custom_name"].str()?.takeIf { it.isNotEmpty() }

    /** storage.station_display_name */
    val displayName: String get() = customName ?: name.ifEmpty { url }

    val kind: String get() = data["kind"].str() ?: "stream"
    val tags: List<String>
        get() = (data["tags"] as? JsonArray)?.mapNotNull { it.str()?.trim()?.takeIf(String::isNotEmpty) }
            ?: emptyList()

    val playCount: Long get() = data["play_count"].num()?.toLong() ?: 0
    val lastPlayed: Double get() = data["last_played"].num() ?: 0.0
    val addedAt: Double get() = data["added_at"].num() ?: 0.0
    val sortIndex: Long get() = data["sort_index"].num()?.toLong() ?: 0
    val isNew: Boolean get() = truthy(data["is_new"])
    val insecure: Boolean get() = truthy(data["insecure"])
    /** Station settings > Identify > "Talk radio" (Android, build 170): spoken prompts mute it instead of pausing it. */
    val talkRadio: Boolean get() = truthy(data["talk_radio"])
    val gainDb: Double get() = data["gain_db"].num() ?: 0.0
    val customArtPath: String get() = data["custom_art_path"].str().orEmpty().trim()
    val favicon: String get() = data["favicon"].str().orEmpty()
    val artUrl: String get() = data["art_url"].str().orEmpty()

    /** storage.record_play: bumps the numbers the "played" sorts use. */
    fun recordPlay(nowSeconds: Double) {
        data["last_played"] = floatJson(nowSeconds)
        data["play_count"] = intJson(playCount + 1)
        if (isNew) data["is_new"] = JsonPrimitive(false)
    }

    override fun toString(): String = "Station($displayName)"

    companion object {
        /** Keys removed from the desktop over time, cleared on load. */
        private val RETIRED = listOf(
            "vocal_clarity", "enhance_quality", "safe_enhance", "custom_song", "custom_artist",
            "ad_handling", "ad_patterns", "preroll_mute_secs", "transcribe",
        )
        private val BOOLS = listOf(
            "is_new", "shazam_auto", "shazam_auto_set", "art_fallback",
            "readout_fallback", "lyrics_enabled", "insecure",
        )
        // key -> (low, high, isInt)
        private val RANGES = mapOf(
            "gain_db" to Triple(-20.0, 20.0, false),
            "eq_bass" to Triple(-12.0, 12.0, true),
            "eq_mid" to Triple(-12.0, 12.0, true),
            "eq_treble" to Triple(-12.0, 12.0, true),
            "buffer_seconds" to Triple(0.0, 60.0, false),
            "connect_worst" to Triple(0.0, 300.0, false),
            "shazam_interval" to Triple(30.0, 900.0, true),
            "play_count" to Triple(0.0, 1e9, true),
            "sort_index" to Triple(0.0, 1e9, true),
        )
        val ART_SOURCES = listOf("auto", "custom", "shazam", "stream")
        val READOUT_SOURCES = listOf("auto", "auto_stream", "custom", "shazam", "stream")

        /**
         * storage._normalize_station: fill in missing keys so the rest of
         * the app can assume they exist. Values already in range are left
         * exactly as written.
         */
        fun normalize(s: Station, nowSeconds: Double, newId: () -> String): Station {
            val d = s.data
            for ((key, default) in Defaults.station) {
                val have = d[key]
                if (have == null || (have is JsonNull && default !is JsonNull)) d[key] = default
            }
            RETIRED.forEach { d.remove(it) }
            if (!truthy(d["id"])) d["id"] = JsonPrimitive(newId())
            if (!truthy(d["added_at"])) d["added_at"] = floatJson(nowSeconds)

            for ((key, range) in RANGES) {
                val (low, high, isInt) = range
                val fallback = Defaults.station[key]!!
                val raw = d[key].num()
                if (raw == null) { d[key] = fallback; continue }
                val v = if (isInt) raw.toLong().toDouble() else raw
                val clamped = v.coerceIn(low, high)
                if (clamped != raw) d[key] = if (isInt) intJson(clamped.toLong()) else floatJson(clamped)
            }
            for (key in BOOLS) {
                val e = d[key]
                if (!(e is JsonPrimitive && !e.isString && (e.content == "true" || e.content == "false"))) {
                    d[key] = JsonPrimitive(truthy(e))
                }
            }
            if (d["art_source"].str() !in ART_SOURCES) d["art_source"] = JsonPrimitive("auto")
            if (d["readout_source"].str() !in READOUT_SOURCES) d["readout_source"] = JsonPrimitive("auto")

            val tags = d["tags"]
            if (tags !is JsonArray) {
                d["tags"] = JsonArray(emptyList())
            } else {
                val seen = HashSet<String>()
                val clean = ArrayList<String>()
                for (t in tags) {
                    val name = (t.str() ?: t.toString()).trim()
                    if (name.isNotEmpty() && seen.add(name.lowercase())) clean.add(name)
                }
                val already = tags.size == clean.size && tags.zip(clean).all { (a, b) -> a.str() == b }
                if (!already) d["tags"] = stringArray(clean)
            }
            return s
        }
    }
}

/** storage.SORT_LABELS, in the menu's order. */
val SORT_LABELS = linkedMapOf(
    "manual" to "Manual (drag to reorder)",
    "alphabetical" to "Alphabetical",
    "added" to "Recently added",
    "played" to "Recently played",
    "play_count" to "Most played",
)

/** Which way a mode runs when the user never flipped it: A to Z, newest and most played first. */
fun defaultDescending(mode: String): Boolean = mode != "alphabetical"

/** storage.sort_stations; [descending] null = the mode's own default. Manual has no direction. */
fun sortStations(stations: List<Station>, mode: String, descending: Boolean? = null): List<Station> {
    val down = descending ?: defaultDescending(mode)
    fun <T : Comparable<T>> by(key: (Station) -> T) =
        if (down) stations.sortedByDescending(key) else stations.sortedBy(key)
    return when (mode) {
        "alphabetical" -> by { it.displayName.lowercase() }
        "added" -> by { it.addedAt }
        "played" -> by { it.lastPlayed }
        "play_count" -> {
            val c = compareBy<Station>({ it.playCount }, { it.lastPlayed })
            stations.sortedWith(if (down) c.reversed() else c)
        }
        else -> stations.sortedBy { it.sortIndex }
    }
}

/** storage.renumber_manual_order: sort_index follows the list's order. */
fun renumberManualOrder(stations: List<Station>): List<Station> =
    stations.mapIndexed { i, s -> if (s.sortIndex == i.toLong()) s else s.copy().apply { this["sort_index"] = intJson(i.toLong()) } }

/**
 * New stations into the list, the desktop's way (mainwindow's import,
 * storage.merge_stations): an address already saved (any case) is
 * skipped, the rest get a fresh id, go to the end of the manual order
 * and, when [markNew], wear the New! badge. Returns the whole list and
 * the stations that were added.
 */
fun addStations(
    existing: List<Station>,
    incoming: List<Station>,
    newId: () -> String,
    now: Double,
    markNew: Boolean = true,
): Pair<List<Station>, List<Station>> {
    val known = existing.map { it.url.trim().lowercase() }.toMutableSet()
    var next = (existing.maxOfOrNull { it.sortIndex } ?: -1) + 1
    val added = ArrayList<Station>()
    for (s in incoming) {
        val url = s.url.trim().lowercase()
        if (url.isEmpty() || !known.add(url)) continue
        val copy = s.copy()
        copy["id"] = kotlinx.serialization.json.JsonPrimitive(newId())
        if (markNew) copy["is_new"] = kotlinx.serialization.json.JsonPrimitive(true)
        if (copy.addedAt <= 0) copy["added_at"] = floatJson(now)
        copy["sort_index"] = intJson(next++)
        added.add(Station.normalize(copy, now, newId))
    }
    return (existing + added) to added
}
