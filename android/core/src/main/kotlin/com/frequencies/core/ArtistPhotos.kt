package com.frequencies.core

import java.io.File
import java.security.MessageDigest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * feeds.ArtistPhotoWorker: a picture of an artist when the biography had
 * none. Wikidata's "image" (a portrait, freely licensed) first, then
 * Deezer's press shot, then Apple's artist image or an album cover.
 * Answers are kept as files (feeds._photo_cache_path, workers._cache_image)
 * so the same act three times an evening is asked once.
 */
class ArtistPhotos(
    private val http: OkHttpClient,
    private val cacheDir: File,
    private val wikidataApi: String = "https://www.wikidata.org/w/api.php",
    private val wikidataEntity: String = "https://www.wikidata.org/wiki/Special:EntityData",
    private val deezer: String = "https://api.deezer.com/search/artist",
    private val itunes: String = "https://itunes.apple.com/search",
) {
    /** feeds._cached_photo: a photo already found for this artist (kept for good). */
    fun cachedFor(artist: String): File? = photoFile(artist).takeIf { it.isFile }

    private fun photoFile(artist: String) = File(File(cacheDir, "artist-photos"), sha1(artist.trim().lowercase()).take(16) + ".img")

    /** The first address the sources give that isn't in [skip], or null. */
    fun find(artist: String, skip: Set<String> = emptySet()): String? {
        for (lookup in listOf(::wikidata, ::deezer, ::itunes)) {
            val url = try { lookup(artist) } catch (e: Exception) { "" }
            if (url in skip) continue
            if (url.isNotEmpty()) return url
        }
        return null
    }

    /**
     * feeds._on_photo_loaded: download a found photo and keep it as this
     * artist's. Null when it fails or is under [MIN_PHOTO_PX] on its short
     * side (a blurred face is worse than the empty frame).
     */
    fun download(artist: String, url: String): File? {
        val bytes = fetch(url) ?: return null
        val (w, h) = ImageSize.of(bytes) ?: return null
        val file = photoFile(artist)
        file.parentFile.mkdirs()
        file.writeBytes(bytes)
        return if (minOf(w, h) >= MIN_PHOTO_PX) file else null
    }

    /** workers._cache_image: a biography's photo, kept under its address. */
    fun cacheImage(url: String): File? {
        val dir = File(cacheDir, "artist-art")
        val file = File(dir, sha1(url).take(20) + if (url.lowercase().endsWith(".png")) ".png" else ".jpg")
        if (file.isFile && file.length() > 0) return file
        val bytes = fetch(url) ?: return null
        dir.mkdirs()
        file.writeBytes(bytes)
        return file
    }

    private fun fetch(url: String): ByteArray? = try {
        http.newCall(Request.Builder().url(url).header("User-Agent", "Frequencies/1.0").build()).execute().use { r ->
            if (r.isSuccessful) r.body?.bytes()?.takeIf { it.isNotEmpty() } else null
        }
    } catch (e: Exception) {
        null
    }

    private fun json(url: okhttp3.HttpUrl, agent: String? = ArtistInfo.USER_AGENT): JsonObject =
        http.newCall(Request.Builder().url(url).apply { if (agent != null) header("User-Agent", agent) }.build()).execute().use { r ->
            if (!r.isSuccessful) throw java.io.IOException("HTTP ${r.code}")
            FreqJson.parseToJsonElement(r.body!!.string()) as JsonObject
        }

    /** P18, the "image" property, of the first plainly musical match. */
    private fun wikidata(artist: String): String {
        val search = json(
            wikidataApi.toHttpUrl().newBuilder().addQueryParameter("action", "wbsearchentities")
                .addQueryParameter("search", artist).addQueryParameter("language", "en").addQueryParameter("format", "json")
                .addQueryParameter("limit", "5").addQueryParameter("type", "item").build(),
        )
        for (hit in (search["search"] as? JsonArray).orEmpty().filterIsInstance<JsonObject>()) {
            val description = hit["description"].str().orEmpty().lowercase()
            if (listOf("music", "band", "singer", "rapper", "composer", "duo", "dj", "producer", "songwriter").none { it in description }) continue
            val id = hit["id"].str() ?: continue
            val entity = json("$wikidataEntity/$id.json".toHttpUrl())
            val claims = ((entity["entities"] as? JsonObject)?.get(id) as? JsonObject)?.get("claims") as? JsonObject
            for (claim in (claims?.get("P18") as? JsonArray).orEmpty().filterIsInstance<JsonObject>()) {
                val name = ((claim["mainsnak"] as? JsonObject)?.get("datavalue") as? JsonObject)?.get("value").str().orEmpty()
                if (name.isNotEmpty()) return "https://commons.wikimedia.org/wiki/Special:FilePath/" + pyQuote(name) + "?width=900"
            }
        }
        return ""
    }

    /** Deezer's artist picture, an exact name first; never its gray silhouette. */
    private fun deezer(artist: String): String {
        val data = json(deezer.toHttpUrl().newBuilder().addQueryParameter("q", artist).addQueryParameter("limit", "5").build(), null)
        val wanted = artist.trim().lowercase()
        val results = (data["data"] as? JsonArray).orEmpty().filterIsInstance<JsonObject>()
        for (item in results.sortedBy { it["name"].str().orEmpty().lowercase() != wanted }) {
            val url = item["picture_xl"].str()?.ifEmpty { null } ?: item["picture_big"].str().orEmpty()
            if (url.isNotEmpty() && "1000x1000-000000-80-0-0" !in url) return url
        }
        return ""
    }

    /** Apple's artist image, then one of their album covers. */
    private fun itunes(artist: String): String {
        for (entity in listOf("musicArtist", "album")) {
            val data = json(
                itunes.toHttpUrl().newBuilder().addQueryParameter("term", artist).addQueryParameter("entity", entity)
                    .addQueryParameter("limit", "1").build(),
                null,
            )
            for (item in (data["results"] as? JsonArray).orEmpty().filterIsInstance<JsonObject>()) {
                val art = item["artworkUrl100"].str().orEmpty().trim()
                if (art.isNotEmpty()) return art.replace("100x100bb", "600x600bb")
            }
        }
        return ""
    }

    companion object {
        const val MIN_PHOTO_PX = 400

        fun sha1(text: String): String =
            MessageDigest.getInstance("SHA-1").digest(text.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }

        /** feeds._service_name: the name people use, not the host it lives on. */
        private val SERVICE_NAMES = linkedMapOf(
            "deezer" to "Deezer", "dzcdn" to "Deezer", "apple" to "Apple Music", "mzstatic" to "Apple Music",
            "wikipedia" to "Wikipedia", "wikimedia" to "Wikimedia", "musicbrainz" to "MusicBrainz",
            "coverartarchive" to "Cover Art Archive", "lrclib" to "lrclib", "lyrics.ovh" to "lyrics.ovh",
            "shazam" to "Shazam", "twitch" to "Twitch",
        )

        fun serviceName(urlOrHost: String): String {
            var host = urlOrHost
            if ("//" in host) host = host.split("/").getOrElse(2) { host }
            host = host.lowercase().trim()
            for ((fragment, name) in SERVICE_NAMES) if (fragment in host) return name
            for (prefix in listOf("www.", "api.", "cdn.", "images.")) if (host.startsWith(prefix)) host = host.removePrefix(prefix)
            return host
        }
    }
}
