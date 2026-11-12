package com.frequencies.core

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

/** The song line: what is shown as the title, and the artist under it. */
data class Readout(val title: String, val artist: String, val from: String)

/**
 * The desktop's rules for what the now-playing line and picture show
 * (mainwindow._readout_choice, feeds.resolve_order).
 */
object NowPlayingRules {
    /**
     * The line under the song in Android Auto: the artist and the station
     * it plays on ("Charli XCX · WITR 89.7 FM"), or the station alone when
     * the artist is the station's own name (user, batch 24).
     */
    fun carLine(artist: String, station: String): String {
        val a = artist.trim()
        val s = station.trim()
        return when {
            a.isEmpty() || a.equals(s, ignoreCase = true) -> s
            s.isEmpty() -> a
            else -> "$a · $s"
        }
    }

    /**
     * The line under the song on the car's screen (user, build 197): the
     * artist and the station take turns ([showStation] says whose turn it
     * is, every [CAR_TURN_MS]); the station alone when the artist is
     * missing or is the station's own name.
     */
    fun carTurn(artist: String, station: String, showStation: Boolean): String {
        val a = artist.trim()
        val s = station.trim()
        return when {
            a.isEmpty() || a.equals(s, ignoreCase = true) -> s
            s.isEmpty() -> a
            showStation -> s
            else -> a
        }
    }

    /** Whether the car line has two things to take turns with. */
    fun carTakesTurns(artist: String, station: String): Boolean =
        carTurn(artist, station, true) != carTurn(artist, station, false)

    const val CAR_TURN_MS = 7_000L

    /** feeds.ART_ORDER */
    val ART_ORDER = listOf("shazam", "custom", "stream")

    /**
     * mainwindow._readout_choice, per the station's readout_source:
     *  - auto: the identified song, else the custom text (the station's
     *    name when it has none), else the stream's title;
     *  - auto_stream: the song, else the stream's title, else the custom text;
     *  - custom / shazam / stream: only that one.
     * [stream] is the stream's own title, cleaned; [split] says which half
     * of it is the artist (the phone asks MusicBrainz, ArtistInfo.splitArtistTitle),
     * or null to show the whole line as the title.
     */
    fun readout(
        station: Station,
        song: Pair<String, String>?,
        stream: String?,
        split: (String) -> Pair<String, String>? = StreamTitle::split,
    ): Readout? {
        val custom = station["custom_readout"].str().orEmpty().trim().ifEmpty { station.displayName.trim() }
        val found = song?.takeIf { it.first.isNotBlank() }?.let { Readout(it.first, it.second, "shazam") }
        val fromStream = stream?.takeIf { it.isNotBlank() }?.let { line ->
            split(line)?.let { (artist, title) -> Readout(title, artist, "stream") } ?: Readout(line, "", "stream")
        }
        val written = custom.takeIf { it.isNotEmpty() }?.let { Readout(it, "", "custom") }
        val order = when (station["readout_source"].str() ?: "auto") {
            "custom" -> listOf(written)
            "shazam" -> listOf(found)
            "stream" -> listOf(fromStream)
            "auto_stream" -> listOf(found, fromStream, written)
            else -> listOf(found, written, fromStream)
        }
        return order.firstOrNull { it != null }
    }

    /** feeds.resolve_order: the art sources to try, in order. */
    fun artOrder(station: Station): List<String> {
        val preferred = station["art_source"].str() ?: "auto"
        if (preferred == "auto" || preferred == "auto_stream" || preferred !in ART_ORDER) return ART_ORDER
        if (!truthy(station["art_fallback"] ?: kotlinx.serialization.json.JsonPrimitive(true))) return listOf(preferred)
        return listOf(preferred) + ART_ORDER.filter { it != preferred }
    }

    /** Song checks run for this station (shazam_auto, on by default). */
    fun identifies(station: Station): Boolean = truthy(station["shazam_auto"] ?: kotlinx.serialization.json.JsonPrimitive(true))

    /** Lyrics are looked up for this station: Settings' switch and the station's own. */
    fun wantsLyrics(settings: Settings, station: Station): Boolean =
        settings.bool("lyrics_on", true) && truthy(station["lyrics_enabled"] ?: kotlinx.serialization.json.JsonPrimitive(true))
}

/**
 * feeds._search_cover: a song's cover from Apple's search, the album when
 * it is known, the song otherwise; only taken when the artist matches.
 */
class CoverSearch(
    private val http: OkHttpClient,
    private val endpoint: String = "https://itunes.apple.com/search",
) {
    fun find(artist: String, title: String, album: String = ""): String? {
        val wanted = artist.trim().lowercase()
        if (wanted.isEmpty() || title.isBlank()) return null
        val tries = buildList {
            if (album.isNotBlank()) add("album" to "$artist $album")
            add("song" to "$artist $title")
        }
        for ((entity, term) in tries) {
            val url = endpoint.toHttpUrl().newBuilder()
                .addQueryParameter("term", term).addQueryParameter("entity", entity).addQueryParameter("limit", "5").build()
            val rows = try {
                http.newCall(Request.Builder().url(url).header("User-Agent", "Frequencies/1.5").build()).execute().use { r ->
                    if (!r.isSuccessful) return null
                    ((FreqJson.parseToJsonElement(r.body!!.string()) as? JsonObject)?.get("results") as? JsonArray)
                        ?.filterIsInstance<JsonObject>().orEmpty()
                }
            } catch (e: Exception) {
                return null
            }
            for (item in rows) {
                val name = item["artistName"].str().orEmpty().lowercase()
                if (name.isEmpty() || (wanted !in name && name !in wanted)) continue
                val art = item["artworkUrl100"].str().orEmpty().trim()
                if (art.isNotEmpty()) return art.replace("100x100bb", "600x600bb")
            }
        }
        return null
    }
}
