package com.frequencies.core

import java.io.File
import java.text.Normalizer
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Corrections for artists and stations, layered as the desktop does
 * (storage.artist_override, storage.station_info):
 *
 *   artist   shipped artist_overrides.json < the developer's bio fixes
 *            < the photo pack's entry < the user's own corrections
 *   station  a shipped talk station (matched by stream address, then
 *            name) or a shipped station correction, with the user's own
 *            station correction on top
 *
 * The user's own files come with a full backup from the desktop. Android
 * only reads them; the editors are desktop dev-build tools.
 */
class ArtistOverrides(
    /** The build's artist_overrides.json (ship tools). */
    private val shippedArtists: () -> JsonObject,
    /** The build's station_overrides.json (talk stations). */
    private val shippedStations: () -> JsonObject,
    /** The user's own artist_overrides.json. */
    private val ownArtists: () -> JsonObject,
    /** The user's own station_overrides.json. */
    private val ownStations: () -> JsonObject,
    private val pack: PhotoPackStore,
) {
    /** storage.artist_override: the merged correction for this artist (empty when none). */
    fun artistOverride(artist: String): Map<String, JsonElement> {
        if (artist.isBlank()) return emptyMap()
        val merged = LinkedHashMap(entryFor(lowerKeys(shippedArtists()), artist))
        for ((names, entryOf) in listOf<Pair<Map<String, JsonObject>, (String) -> Map<String, JsonElement>>>(
            pack.bioNames() to pack::bioEntry,
            pack.packNames() to pack::artistEntry,
        )) {
            val (key, how) = findArtist(names, artist)
            if (key.isNotEmpty()) {
                val entry = LinkedHashMap(entryOf(key))
                if (how.isNotEmpty() && how != SPELLED) entry.remove("name")
                merged.putAll(entry)
            }
        }
        merged.putAll(entryFor(lowerKeys(ownArtists()), artist))
        return merged
    }

    private fun entryFor(table: Map<String, JsonObject>, artist: String): Map<String, JsonElement> {
        val (key, how) = findArtist(table, artist)
        val entry = LinkedHashMap<String, JsonElement>(table[key] ?: emptyMap())
        if (how.isNotEmpty() && how != SPELLED) entry.remove("name")
        // A photo whose file isn't on this phone (corrections from a desktop backup) goes, with its credit.
        if (missingPhoto(entry["photo"].str())) listOf("photo", "photo_source", "photo_url").forEach { entry.remove(it) }
        return entry
    }

    // -- stations (talk radio) -------------------------------------------------

    /** storage.talk_entries: the shipped talk stations. */
    fun talkEntries(): Map<String, JsonObject> = lowerKeys(shippedStations()).filterValues { "talk" in it }

    /** storage.talk_entry: (key, entry, how) for this station, matched by stream address, then by name. */
    fun talkEntry(station: Station): Triple<String, JsonObject, String>? {
        val entries = talkEntries()
        val url = normStream(station.url)
        if (url.isNotEmpty()) {
            for ((key, entry) in entries) if (normStream(entry["stream"].str()) == url) return Triple(key, entry, "stream address")
        }
        for (name in listOf(station.customName, station.name)) {
            val key = name.orEmpty().trim().lowercase()
            if (key.isNotEmpty()) entries[key]?.let { return Triple(key, it, "name") }
        }
        return null
    }

    fun isTalkStation(station: Station): Boolean = talkEntry(station)?.second?.get("talk")?.let { truthy(it) } == true

    /**
     * A talk radio station the dev marked (the shipped talk list, or the
     * station's own talk radio switch) that has its own typed-in bio, not
     * one looked up online (Android batch 22: Comfort comes in on its Artist page).
     */
    fun talkWithBio(station: Station): Boolean =
        (station.talkRadio || isTalkStation(station)) && stationInfo(station)["bio"].str().orEmpty().isNotBlank()

    /** storage.station_override: a shipped correction that isn't a talk entry, the user's own on top. */
    fun stationOverride(name: String): Map<String, JsonElement> {
        val key = name.trim().lowercase()
        if (key.isEmpty()) return emptyMap()
        val bundled = lowerKeys(shippedStations())[key] ?: JsonObject(emptyMap())
        val merged = if ("talk" in bundled) LinkedHashMap() else LinkedHashMap<String, JsonElement>(bundled)
        merged.putAll(lowerKeys(ownStations())[key] ?: emptyMap())
        return merged
    }

    /**
     * storage.station_info: what the Artist tab shows for this station
     * while it plays, or empty. A talk station gets "talk": true and
     * "_matched" (how it was found).
     */
    fun stationInfo(station: Station): Map<String, JsonElement> {
        val name = station.displayName
        val own = lowerKeys(ownStations())[name.trim().lowercase()] ?: emptyMap()
        val talk = talkEntry(station)
        if (talk != null && truthy(talk.second["talk"])) {
            val merged = LinkedHashMap<String, JsonElement>(talk.second)
            merged.putAll(own)
            merged["talk"] = JsonPrimitive(true)
            merged["_matched"] = JsonPrimitive(talk.third)
            merged["_key"] = JsonPrimitive(talk.first)
            return merged
        }
        return stationOverride(name)
    }

    /** storage.talk_photo_path: a talk station's photo as a file here (from the photo pack), or null. */
    fun talkPhotoPath(photo: String?): File? {
        val p = photo.orEmpty().trim()
        if (p.isEmpty() || p.startsWith("http://") || p.startsWith("https://")) return null
        val file = File(p)
        if (file.isAbsolute) return file.takeIf { it.exists() }
        return pack.talkPhoto(file.name)
    }

    companion object {
        const val SPELLED = "spelled differently"

        fun lowerKeys(obj: JsonObject): Map<String, JsonObject> {
            val out = LinkedHashMap<String, JsonObject>()
            for ((k, v) in obj) if (v is JsonObject) out[k.trim().lowercase()] = v
            return out
        }

        /** storage.missing_photo: a photo that is a file this phone doesn't have. */
        fun missingPhoto(photo: String?): Boolean {
            val p = photo.orEmpty().trim()
            return p.isNotEmpty() && !p.startsWith("http://") && !p.startsWith("https://") && !File(p).isFile
        }

        /** storage._norm_stream: scheme dropped, host lowercase, no trailing slash or semicolon. */
        fun normStream(url: String?): String {
            var u = url.orEmpty().trim()
            if (u.isEmpty()) return ""
            if ("://" in u) u = u.substringAfter("://")
            val slash = u.indexOf('/')
            u = if (slash < 0) u.lowercase() else u.substring(0, slash).lowercase() + u.substring(slash)
            return u.trimEnd('/', ';').trimEnd('/')
        }

        // Python's str patterns: \w, \s and \b are Unicode. Spelled out here
        // because Android's regex engine (ICU) rejects Java's inline U flag, and desktop Java
        // without it is ASCII only. AndroidRegexTest keeps that flag out.
        private const val WORD = "[\\p{L}\\p{N}_]"
        private const val SPACE = "[\\s\\p{Z}]"
        private const val START = "(?<!" + WORD + ")"   // \b before a word
        private const val END = "(?!" + WORD + ")"      // \b after a word

        private val FEATURING = Regex(
            SPACE + "*(?:[(\\[]" + SPACE + "*(?:feat\\.?|ft\\.?|featuring|with)" + SPACE +
                "|" + START + "(?:feat\\.?|ft\\.?|featuring)" + SPACE + ").*$",
            RegexOption.IGNORE_CASE,
        )
        private val SEVERAL = Regex(
            SPACE + "*(?:,|&|\\+|/|;|" + START + "x" + END + "|" + START + "and" + END +
                "|" + START + "vs\\.?(?=" + SPACE + ")|" + START + "meets" + END + ")" + SPACE + "*",
            RegexOption.IGNORE_CASE,
        )
        private val AND = Regex(SPACE + "*(?:&|\\+)" + SPACE + "*")
        private val PUNCT = Regex("[^\\p{L}\\p{N}_\\s\\p{Z}]")
        private val SPACES = Regex(SPACE + "+")

        /** storage._loose_artist: casefold, no accents or punctuation, "&" = "and", no leading "The". */
        fun looseArtist(text: String): String {
            var t = Normalizer.normalize(text, Normalizer.Form.NFKD).lowercase()
            t = t.filter { Character.getType(it) != Character.NON_SPACING_MARK.toInt() && Character.getType(it) != Character.COMBINING_SPACING_MARK.toInt() && Character.getType(it) != Character.ENCLOSING_MARK.toInt() }
            t = AND.replace(t, " and ")
            t = PUNCT.replace(t, "")
            t = t.trim().split(SPACES).filter { it.isNotEmpty() }.joinToString(" ")
            return if (t.startsWith("the ")) t.substring(4) else t
        }

        /** storage.artist_keys: the names to look an artist up by, most exact first: (key, how). */
        fun artistKeys(artist: String): List<Pair<String, String>> {
            val exact = artist.trim().split(SPACES).filter { it.isNotEmpty() }.joinToString(" ").lowercase()
            if (exact.isEmpty()) return emptyList()
            val keys = mutableListOf(exact to "")
            val main = FEATURING.replace(exact, "").trim()
            if (main.isNotEmpty() && main != exact) keys += main to "without the featured artists"
            val first = SEVERAL.split(main.ifEmpty { exact })[0].trim()
            if (first.isNotEmpty() && first != exact && first != main) keys += first to "first artist named"
            return keys
        }

        /** storage.find_artist: (key, how) of this artist's entry in [table] (lowercase keys), or ("", ""). */
        fun findArtist(table: Map<String, *>, artist: String): Pair<String, String> {
            var loose: Map<String, String>? = null
            for ((key, how) in artistKeys(artist)) {
                if (key in table) return key to how
                if (loose == null) {
                    val m = LinkedHashMap<String, String>()
                    for (name in table.keys) m.putIfAbsent(looseArtist(name), name)
                    m.remove("")
                    loose = m
                }
                loose[looseArtist(key)]?.let { name ->
                    return name to listOf(how, SPELLED).filter { it.isNotEmpty() }.joinToString(", ")
                }
            }
            return "" to ""
        }
    }
}
