package com.frequencies.core

import java.io.File
import java.security.MessageDigest
import java.util.Base64
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray

/**
 * Featured entries: the shape the desktop's default_stations.json, share
 * zips and QR codes carry a station in (storage.station_from_featured).
 */
object Featured {
    private val SKIP = setOf("id", "local_path", "sort_index", "connect_worst", "custom_art_path")
    private val CORE = setOf(
        "name", "url", "artist_lookup", "playlist_url", "kind",
        "card_title", "card_subtitle", "card_body", "twitch_channel", "shows",
    )

    /**
     * A station from a featured entry, keeping its settings. Unknown keys
     * are dropped, as on the desktop: a list from a newer version must not
     * smuggle in fields this one can't honor. Art is written to [artDir].
     */
    fun toStation(entry: JsonObject, artDir: File, newId: () -> String): Station {
        val raw = (entry["settings"] as? JsonObject) ?: entry
        val d = LinkedHashMap<String, JsonElement>(Defaults.station)
        val id = newId()
        d["id"] = JsonPrimitive(id)
        val allowed = Defaults.station.keys + CORE
        for ((key, value) in raw) {
            if (key in SKIP || key !in allowed) continue
            d[key] = value
        }
        val station = Station(d)

        raw["art_data"].str()?.takeIf { it.isNotEmpty() }?.let { blob ->
            val path = writeArt(artDir, id, raw["art_name"].str(), blob)
            if (path != null) {
                d["custom_art_path"] = JsonPrimitive(path.absolutePath)
                if (raw["art_name"].str() == "shared.jpg" || ImageSize.isSmall(path)) {
                    d["art_from_code"] = JsonPrimitive(true)
                }
                val link = raw["art_url"].str().orEmpty().trim()
                if (link.startsWith("http://") || link.startsWith("https://")) {
                    d["art_url"] = JsonPrimitive(link)
                    d["art_url_sha"] = JsonPrimitive(if (truthy(d["art_from_code"])) "" else sha1(path))
                }
            }
        }
        for (key in listOf("name", "url", "favicon", "homepage")) {
            if (!truthy(d[key]) && truthy(entry[key])) d[key] = entry[key]!!
        }
        if (truthy(entry["uuid"]) && !truthy(d["rb_uuid"])) d["rb_uuid"] = entry["uuid"]!!

        d["eq_bands"] = JsonArray(normalizeEqBands(d["eq_bands"]).map { JsonPrimitive(it) })
        val tags = d["tags"] as? JsonArray
        d["tags"] = stringArray(tags?.mapNotNull { it.str()?.trim()?.takeIf(String::isNotEmpty) } ?: emptyList())
        return station
    }

    /**
     * storage.featured_from_station: a station as a featured entry (what
     * a share zip or code carries). Ids, paths and measurements stay
     * behind, values equal to their default too; both names travel, and
     * the picture goes embedded, with its link while it is still the
     * picture the link gave.
     */
    fun fromStation(station: Station): JsonObject {
        val out = LinkedHashMap<String, JsonElement>()
        val allowed = Defaults.station.keys + CORE
        for ((key, value) in station.data) {
            if (key in SKIP || key !in allowed) continue
            if (value == Defaults.station[key]) continue
            out[key] = value
        }
        out["name"] = JsonPrimitive(station.name)
        out["url"] = JsonPrimitive(station.url)
        val path = station.customArtPath
        if (path.isNotEmpty()) {
            val file = File(path)
            if (file.isFile) {
                runCatching { Base64.getEncoder().encodeToString(file.readBytes()) }.getOrNull()?.let {
                    out["art_data"] = JsonPrimitive(it)
                    out["art_name"] = JsonPrimitive(file.name)
                    artLink(station).takeIf(String::isNotEmpty)?.let { link -> out["art_url"] = JsonPrimitive(link) }
                }
            }
        }
        return JsonObject(out)
    }

    /**
     * storage.art_link: the picture's address online, while the picture
     * is still the one it gave (a QR thumbnail passes its link on unchecked).
     */
    fun artLink(station: Station): String {
        val url = station.artUrl.trim()
        val path = station.customArtPath
        if (!(url.startsWith("http://") || url.startsWith("https://")) || path.isEmpty()) return ""
        if (truthy(station["art_from_code"])) return url
        val sha = station["art_url_sha"].str().orEmpty()
        return if (sha.isNotEmpty() && sha == sha1(File(path))) url else ""
    }

    /** Every entry of a featured list (default_stations.json) that has an address. */
    fun readList(text: String): List<JsonObject> =
        try {
            FreqJson.parseToJsonElement(text).jsonArray.mapNotNull { it as? JsonObject }
                .filter { truthy(it["url"]) }
        } catch (e: Exception) {
            emptyList()
        }

    fun normalizeEqBands(value: JsonElement?): List<Int> {
        val list = (value as? JsonArray)?.take(10) ?: emptyList()
        return (0 until 10).map { i ->
            val v = list.getOrNull(i).num()
            if (v == null) 0 else Math.round(v).toInt().coerceIn(-12, 12)
        }
    }

    fun writeArt(artDir: File, stationId: String, name: String?, blob: String): File? = try {
        val bytes = Base64.getMimeDecoder().decode(blob)
        artDir.mkdirs()
        File(artDir, "$stationId-${safeFileName(name)}").also { it.writeBytes(bytes) }
    } catch (e: Exception) {
        null
    }

    fun sha1(file: File): String = try {
        MessageDigest.getInstance("SHA-1").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
    } catch (e: Exception) {
        ""
    }
}

private val RESERVED = setOf("CON", "PRN", "AUX", "NUL") +
    (1..9).flatMap { listOf("COM$it", "LPT$it") }

/** storage.safe_file_name: a file name every OS takes. */
fun safeFileName(name: String?, fallback: String = "cover.png", most: Int = 80): String {
    var n = (name ?: "").split('\\', '/').last()
    n = n.replace(Regex("[<>:\"|?*\\x00-\\x1f]"), "_").trim().trimEnd('.', ' ')
    val dot = n.lastIndexOf('.')
    var stem: String
    var ext: String
    if (dot < 0 || !Regex("[A-Za-z0-9]{1,8}").matches(n.substring(dot + 1))) {
        stem = n; ext = ""
    } else {
        stem = n.substring(0, dot); ext = n.substring(dot + 1)
    }
    if (stem.split('.')[0].uppercase() in RESERVED) stem = "_$stem"
    stem = stem.take(maxOf(1, most - ext.length - 1))
    val out = if (ext.isNotEmpty()) "$stem.$ext" else stem
    return if (stem.trim('_', ' ').isNotEmpty()) out else fallback
}

/**
 * radiobrowser.featured_stations: today's featured frequencies, the
 * curator's hand-made list, fetched once a day (cached by date). A failed
 * fetch falls back to the cached copy; only an empty cache gives nothing.
 */
class FeaturedList(
    private val dir: File,
    private val http: okhttp3.OkHttpClient,
    private val address: String = URL,
    private val today: () -> String = { java.time.LocalDate.now().toString() },
) {
    private val file get() = File(dir, "featured.json")

    /** The entries as the list has them (each a whole station, art and blurb included). */
    fun load(force: Boolean = false): List<JsonObject> {
        val cached = readJson(file) as? JsonObject
        if (!force && cached != null && cached["date"].str() == today()) return entries(cached["stations"])
        val fresh = runCatching {
            http.newCall(okhttp3.Request.Builder().url(address).header("User-Agent", RadioBrowser.AGENT).build()).execute().use { r ->
                if (!r.isSuccessful) null else FreqJson.parseToJsonElement(r.body!!.string())
            }
        }.getOrNull()
        val stations = when (fresh) {
            is JsonArray -> fresh
            is JsonObject -> fresh["stations"] as? JsonArray
            else -> null
        } ?: return entries(cached?.get("stations"))
        runCatching { writeJsonAtomic(file, JsonObject(linkedMapOf("date" to JsonPrimitive(today()), "stations" to stations))) }
        return entries(stations)
    }

    private fun entries(e: JsonElement?): List<JsonObject> = (e as? JsonArray)?.filterIsInstance<JsonObject>().orEmpty()

    companion object {
        const val URL = "https://raw.githubusercontent.com/iFeelLikeChicken2Nite/featured/main/featured.json"
        /** radiobrowser.FEATURED_EMPTY, word for word. */
        const val EMPTY = "more like uh, uncurated frequencies am i right? (there seems to be no curated frequency data today, sorry!)"

        /** stationcard._written: the first text the curator wrote under any of [keys]. */
        fun written(entry: JsonObject, vararg keys: String): String {
            val raw = entry["settings"] as? JsonObject
            for (source in listOfNotNull(entry, raw)) for (key in keys) {
                source[key].str()?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
            }
            return ""
        }

        /**
         * stationcard: the card's words. The curator's heading and blurb
         * when there are any; with no blurb, what is known about the stream
         * (format, bitrate; never where it streams from, build 172), never anything made up.
         */
        data class Card(val name: String, val subtitle: String, val heading: String, val body: String, val bodyIsBlurb: Boolean)

        fun card(entry: JsonObject): Card {
            val raw = entry["settings"] as? JsonObject ?: entry
            val name = written(entry, "custom_name", "name").ifEmpty { "Unknown station" }
            val tags = (raw["tags"] as? JsonArray ?: entry["tags"] as? JsonArray)?.mapNotNull { it.str() ?: (it as? JsonArray)?.firstOrNull().str() }
                ?: (raw["tags"].str() ?: entry["tags"].str())?.split(',')?.map { it.trim() }?.filter { it.isNotEmpty() }.orEmpty()
            val known = listOf(written(entry, "country"), written(entry, "language"), tags.joinToString(", ")).filter { it.isNotEmpty() }.joinToString(" · ")
            val blurb = written(entry, "blurb", "card_body")
            return Card(name, known.ifEmpty { written(entry, "card_subtitle") }, written(entry, "card_title"), blurb.ifEmpty { streamInfo(entry) }, blurb.isNotEmpty())
        }

        /** stationcard._stream_info */
        fun streamInfo(entry: JsonObject): String {
            val raw = entry["settings"] as? JsonObject ?: entry
            val codec = (entry["codec"].str() ?: raw["codec"].str()).orEmpty().trim()
            val bitrate = (entry["bitrate"].num() ?: raw["bitrate"].num() ?: 0.0).toInt()
            // Never where it streams from (user, 2026-11-05, all platforms: task 228).
            return listOf(codec.uppercase(), if (bitrate > 0) "$bitrate kbps" else "")
                .filter { it.isNotEmpty() }.joinToString(" · ")
        }

        /** discover._featured_art: the curator's picture (embedded in the entry) written out once; null for none. */
        fun artFile(dir: File, entry: JsonObject): File? {
            val raw = entry["settings"] as? JsonObject ?: entry
            val blob = raw["art_data"].str()?.takeIf { it.isNotEmpty() } ?: return null
            val name = MessageDigest.getInstance("SHA-1").digest(blob.take(512).toByteArray()).joinToString("") { "%02x".format(it) }.take(16)
            val file = File(dir, "art/$name.png")
            if (file.isFile) return file
            return runCatching {
                file.parentFile.mkdirs()
                file.writeBytes(Base64.getMimeDecoder().decode(blob))
                file
            }.getOrNull()
        }

        /** The address to compare with Saved Frequencies (whatever the entry calls it). */
        fun url(entry: JsonObject): String = written(entry, "url")
    }
}
