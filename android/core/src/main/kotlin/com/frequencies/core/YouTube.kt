package com.frequencies.core

import java.io.File
import java.security.MessageDigest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * YouTube playlists as stations (the desktop's youtube.py): a random
 * entry, joined partway through, like a folder of music. The playlist is
 * listed once a day (cached); each video is resolved when it plays,
 * never cached, because YouTube's links expire. The phone resolves with
 * NewPipeExtractor (the app does that part); the rules live here.
 */
object YouTube {
    private val HOSTS = listOf("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com")
    const val CACHE_TTL_SECONDS = 24 * 3600.0
    /** workers.YouTubeResolver: tuned in, a video longer than 30 s is joined 0-66% of the way in. */
    const val JOIN_MAX = 0.66
    /** bridge.MIN_TUNE_MS: the static plays at least this long when tuning in. */
    const val MIN_TUNE_MS = 700L

    class YouTubeError(message: String) : Exception(message)

    data class Entry(val id: String, val title: String, val duration: Double)

    /** youtube.looks_like_playlist: a YouTube link that names a playlist, channel or user. */
    fun looksLikePlaylist(url: String?): Boolean {
        val t = url.orEmpty().trim()
        if (t.isEmpty() || HOSTS.none { it in t }) return false
        return Regex("[?&]list=").containsMatchIn(t) || "/playlist" in t || "/channel/" in t || "/@" in t || "/user/" in t || "/c/" in t
    }

    fun isYouTube(s: Station?): Boolean = s?.kind == "youtube"
    fun playlistOf(s: Station): String = s["playlist_url"].str()?.trim()?.ifEmpty { null } ?: s.url.removePrefix("youtube://")

    /** mainwindow._add_from_source("youtube"): named after the playlist, its picture from the stream. */
    fun stationFor(playlist: String, title: String, newId: () -> String, now: Double): Station {
        val d = LinkedHashMap<String, JsonElement>()
        d["name"] = JsonPrimitive(title.trim().ifEmpty { "YouTube" })
        d["kind"] = JsonPrimitive("youtube")
        d["playlist_url"] = JsonPrimitive(playlist)
        d["art_source"] = JsonPrimitive("stream")
        d["url"] = JsonPrimitive("youtube://$playlist")
        return Station.normalize(Station(d), now, newId)
    }

    /** youtube.playlist_entries' filter: private and deleted videos (no title) are dropped. */
    fun keep(entries: List<Entry>): List<Entry> = entries.filter {
        it.id.isNotEmpty() && it.title.isNotBlank() && it.title.trim().lowercase() !in setOf("[private video]", "[deleted video]")
    }

    /** workers.YouTubeResolver: any entry but the one that just played. */
    fun pick(entries: List<Entry>, exclude: String?, random: kotlin.random.Random = kotlin.random.Random): Entry? =
        (entries.filter { it.id != exclude }.ifEmpty { entries }).randomOrNull(random)

    fun joinFraction(durationSeconds: Double, random: kotlin.random.Random = kotlin.random.Random): Double =
        if (durationSeconds > 30) random.nextDouble(0.0, JOIN_MAX) else 0.0

    fun videoUrl(id: String) = "https://www.youtube.com/watch?v=$id"

    // -- the listing cache (youtube._read_cache / _write_cache) -------------------

    class Cache(private val dir: File, private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 }) {
        private fun file(url: String): File {
            val digest = MessageDigest.getInstance("SHA-1").digest(url.toByteArray()).joinToString("") { "%02x".format(it) }.take(16)
            return File(dir, "youtube-$digest.json")
        }

        /** The cached list (and the playlist's name) if it is less than a day old. */
        fun read(url: String, ttl: Double = CACHE_TTL_SECONDS): Pair<List<Entry>, String>? {
            val data = readJson(file(url)) as? JsonObject ?: return null
            if (clock() - (data["fetched_at"].num() ?: 0.0) > ttl) return null
            val entries = (data["entries"] as? JsonArray)?.mapNotNull { e ->
                val o = e as? JsonObject ?: return@mapNotNull null
                Entry(o["id"].str() ?: return@mapNotNull null, o["title"].str().orEmpty(), o["duration"].num() ?: 0.0)
            }.orEmpty()
            return if (entries.isEmpty()) null else entries to data["title"].str().orEmpty()
        }

        fun write(url: String, entries: List<Entry>, title: String) = runCatching {
            writeJsonAtomic(
                file(url),
                buildJsonObject {
                    put("fetched_at", clock())
                    put("title", title)
                    put("entries", buildJsonArray { entries.forEach { e -> add(buildJsonObject { put("id", e.id); put("title", e.title); put("duration", e.duration) }) } })
                },
            )
        }
    }
}
