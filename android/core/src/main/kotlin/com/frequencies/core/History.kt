package com.frequencies.core

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * history.json, the songs Song ID has named, as the desktop keeps it
 * (storage.add_history_entry and its neighbors): newest first, entries
 * {title, artist, station, at, apple_music_url, cover_url, favorite}.
 * A starred song is permanent: outside the cap, and "clear" keeps it.
 */
object SongHistory {
    const val HARD_CAP = 300

    /** storage.history_limit: shazam_history_limit, 10 to 300. */
    fun limit(settings: Settings): Int =
        (settings["shazam_history_limit"].num()?.toInt() ?: 300).coerceIn(10, HARD_CAP)

    /** storage.track_key: case and spacing don't make two songs. */
    fun trackKey(artist: String?, title: String?): String =
        "${artist.orEmpty().trim().lowercase()}|${title.orEmpty().trim().lowercase()}"

    fun entryKey(entry: JsonObject): String =
        trackKey(entry["artist"].str(), entry["title"].str()?.takeIf { it.isNotEmpty() } ?: entry["song"].str())

    fun isFavorite(entry: JsonObject): Boolean = truthy(entry["favorite"])

    fun title(entry: JsonObject): String = entry["title"].str().orEmpty()
    fun artist(entry: JsonObject): String = entry["artist"].str().orEmpty()
    fun station(entry: JsonObject): String = entry["station"].str().orEmpty()
    fun coverUrl(entry: JsonObject): String = entry["cover_url"].str().orEmpty()
    fun appleMusicUrl(entry: JsonObject): String = entry["apple_music_url"].str().orEmpty()
    /** Seconds since 1970, as the desktop's time.time(). */
    fun at(entry: JsonObject): Double = entry["at"].num() ?: 0.0

    fun entries(history: JsonArray): List<JsonObject> = history.filterIsInstance<JsonObject>()

    private fun newEntry(title: String, artist: String, station: String, now: Double, apple: String, cover: String, favorite: Boolean) =
        JsonObject(linkedMapOf<String, JsonElement>(
            "title" to JsonPrimitive(title),
            "artist" to JsonPrimitive(artist),
            "station" to JsonPrimitive(station),
            "at" to floatJson(now),
            "apple_music_url" to JsonPrimitive(apple),
            "cover_url" to JsonPrimitive(cover),
            "favorite" to JsonPrimitive(favorite),
        ))

    private fun JsonObject.with(vararg pairs: Pair<String, JsonElement>) =
        JsonObject(LinkedHashMap(this).apply { pairs.forEach { (k, v) -> put(k, v) } })

    /**
     * storage.add_history_entry: the same song again on the same station
     * isn't a new play; a song already starred moves to the top with its
     * star rather than getting a second row. Returns null when nothing changed.
     */
    fun add(history: JsonArray, title: String, artist: String, station: String, now: Double,
            appleMusicUrl: String = "", coverUrl: String = "", limit: Int = HARD_CAP): JsonArray? {
        val t = title.trim()
        val a = artist.trim()
        if (t.isEmpty() && a.isEmpty()) return null
        val list = entries(history).toMutableList()
        list.firstOrNull()?.let { last ->
            if (title(last) == t && artist(last) == a && station(last) == station) return null
        }
        val key = trackKey(a, t)
        val starred = list.indexOfFirst { isFavorite(it) && entryKey(it) == key }
        if (starred >= 0) {
            val moved = list.removeAt(starred).with("at" to floatJson(now), "station" to JsonPrimitive(station))
            list.add(0, moved)
            return JsonArray(list)
        }
        list.add(0, newEntry(t, a, station, now, appleMusicUrl, coverUrl, false))
        return JsonArray(trim(list, limit))
    }

    /** storage.trim_history: the cap counts ordinary songs only. */
    fun trim(history: List<JsonObject>, limit: Int): List<JsonObject> {
        var ordinary = 0
        return history.filter { if (isFavorite(it)) true else ordinary++ < limit }
    }

    /** storage.sort_history: favorites first, newest first within each. */
    fun sorted(history: JsonArray): List<JsonObject> {
        val all = entries(history)
        return all.filter(::isFavorite).sortedByDescending(::at) + all.filterNot(::isFavorite).sortedByDescending(::at)
    }

    /**
     * storage.set_favorite: star or unstar by key; starring a song that
     * isn't in the history yet adds it. Returns null when nothing changed.
     */
    fun setFavorite(history: JsonArray, key: String, favorite: Boolean, now: Double,
                    title: String = "", artist: String = "", station: String = "",
                    appleMusicUrl: String = "", coverUrl: String = ""): JsonArray? {
        val list = entries(history).toMutableList()
        val i = list.indexOfFirst { entryKey(it) == key }
        if (i >= 0) {
            list[i] = list[i].with("favorite" to JsonPrimitive(favorite))
            return JsonArray(list)
        }
        if (favorite && (title.isNotBlank() || artist.isNotBlank())) {
            list.add(0, newEntry(title.trim(), artist.trim(), station, now, appleMusicUrl, coverUrl, true))
            return JsonArray(list)
        }
        return null
    }

    fun favoriteKeys(history: JsonArray): Set<String> = entries(history).filter(::isFavorite).map(::entryKey).toSet()

    /**
     * Task 241 (user, 2026-11-11; Android build 198, desktop Batch CT): only songs from radio
     * stations can be favorited, never one from a music folder station or played on iBroadcast.
     * Favorites saved before stay as they are. A station not known (a preview) is radio.
     */
    fun canFavorite(station: Station?): Boolean =
        station == null || (!LocalStations.isLocal(station) && !IBroadcast.isIBroadcast(station))

    /** storage.clear_history: favorites stay. */
    fun clear(history: JsonArray): JsonArray = JsonArray(entries(history).filter(::isFavorite))

    /** services.SERVICES: (label, link) searching each service for "artist song". */
    fun searchLinks(artist: String, title: String): List<Pair<String, String>> {
        val q = quote(listOf(artist, title).filter { it.isNotBlank() }.joinToString(" "))
        return listOf(
            "Apple Music" to "https://music.apple.com/us/search?term=$q",
            "YouTube Music" to "https://music.youtube.com/search?q=$q",
            "Spotify" to "https://open.spotify.com/search/$q",
            "Deezer" to "https://www.deezer.com/search/$q",
            "Tidal" to "https://listen.tidal.com/search?q=$q",
        )
    }

    /** Python's urllib.parse.quote: %20 for a space, "/" left alone. */
    fun quote(text: String): String =
        java.net.URLEncoder.encode(text, "UTF-8").replace("+", "%20").replace("%2F", "/")
            .replace("%7E", "~").replace("*", "%2A")
}
