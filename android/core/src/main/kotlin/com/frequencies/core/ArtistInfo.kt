package com.frequencies.core

import java.net.URLDecoder
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * Who you're listening to (the desktop's artistinfo.py): a biography from
 * MusicBrainz + Wikipedia, Wikipedia alone, or Discogs; a station's own
 * article for talk radio; and which half of "A - B" names the artist.
 * Every call blocks: run it off the main thread.
 */
data class ArtistBio(
    val name: String,
    val summary: String,
    val tags: List<String> = emptyList(),
    val area: String = "",
    val years: String = "",
    val url: String = "",
    val photo: String = "",
    /** "Wikipedia", "Discogs" or "" (MusicBrainz's route and typed-in bios name none, as on the desktop). */
    val source: String = "",
)

class ArtistInfo(
    private val http: OkHttpClient,
    private val musicbrainz: String = "https://musicbrainz.org/ws/2",
    private val wikipedia: String = "https://en.wikipedia.org/api/rest_v1/page/summary",
    private val wikidata: String = "https://www.wikidata.org/w/api.php",
    private val discogs: String = "https://api.discogs.com",
    private val now: () -> Long = System::currentTimeMillis,
    /** MusicBrainz asks for one request a second. */
    private val musicbrainzGapMs: Long = 1100,
) {
    private val cache = HashMap<String, Pair<Long, Any?>>()

    /** Why the last biography lookup found nothing, when it was a failure rather than a genuine absence. */
    @Volatile var lastBioError: String = ""
        private set

    /** Drop cached biographies so a new correction takes effect at once. */
    fun forgetCachedBios() = synchronized(cache) { cache.keys.removeAll { it.startsWith("bio|") } }

    @Suppress("UNCHECKED_CAST")
    private fun <T> cached(key: String, produce: () -> T): T {
        synchronized(cache) {
            cache[key]?.let { (at, value) -> if (now() - at < CACHE_TTL_MS) return value as T }
        }
        val value = produce()
        synchronized(cache) {
            if (cache.size > 2000) cache.clear()
            cache[key] = now() to value
        }
        return value
    }

    private var lastMusicbrainz = 0L
    private val musicbrainzLock = Any()

    private fun get(url: okhttp3.HttpUrl, agent: String = USER_AGENT): JsonObject {
        if (url.host == musicbrainzHost) synchronized(musicbrainzLock) {
            val wait = lastMusicbrainz + musicbrainzGapMs - now()
            if (wait > 0) Thread.sleep(wait)
            lastMusicbrainz = now()
        }
        val request = Request.Builder().url(url).header("User-Agent", agent).build()
        http.newCall(request).execute().use { r ->
            if (!r.isSuccessful) throw java.io.IOException("HTTP ${r.code}")
            return FreqJson.parseToJsonElement(r.body!!.string()).jsonObject
        }
    }

    private val musicbrainzHost = musicbrainz.toHttpUrl().host

    private fun mb(path: String, vararg params: Pair<String, String>) =
        "$musicbrainz/$path".toHttpUrl().newBuilder().apply { params.forEach { (k, v) -> addQueryParameter(k, v) } }.build()

    // -- which half is the artist ---------------------------------------------

    /**
     * artistinfo.split_artist_title: both readings of "A - B" are checked
     * against MusicBrainz and the one that names a real artist wins.
     * (artist, title), or null when neither half looks like an artist (a
     * station's own name or a show title) or the line has no spaced dash.
     */
    fun splitArtistTitle(text: String?): Pair<String, String>? {
        val (left, right) = StreamTitle.split(text ?: return null) ?: return null
        val leftScore = artistConfidence(left)
        val rightScore = artistConfidence(right)
        if (maxOf(leftScore, rightScore) < 70) return null
        return if (leftScore >= rightScore) left to right else right to left
    }

    /** How sure MusicBrainz is that this names an artist, 0-100 (an exact name match only). */
    fun artistConfidence(name: String): Int = cached("artist-confidence|${name.lowercase()}") {
        try {
            val data = get(mb("artist", "query" to "artist:\"$name\"", "fmt" to "json", "limit" to "1"))
            val best = (data["artists"] as? JsonArray)?.firstOrNull() as? JsonObject ?: return@cached 0
            val exact = best["name"].str().orEmpty().trim().lowercase() == name.trim().lowercase()
            if (exact) best["score"].num()?.toInt() ?: 0 else 0
        } catch (e: Exception) {
            0
        }
    }

    // -- biography ------------------------------------------------------------

    /**
     * artistinfo.biography: [override] is the merged correction
     * (ArtistOverrides.artistOverride): an mbid pins the MusicBrainz artist,
     * a wikipedia title forces one article, a bio replaces the text.
     */
    fun biography(artist: String, override: Map<String, JsonElement> = emptyMap()): ArtistBio? {
        if (artist.isBlank()) return null
        val key = "bio|${artist.lowercase()}|${override.toSortedMap()}"
        return cached(key) { lookUpBiography(artist, override) }?.let { it.copy(summary = Bios.clean(it.summary)) }
    }

    private fun lookUpBiography(artist: String, override: Map<String, JsonElement>): ArtistBio? {
        lastBioError = ""
        val problems = ArrayList<String>()
        fun o(k: String) = override[k].str().orEmpty()
        fun oTags(): List<String> = (override["tags"] as? JsonArray)?.mapNotNull { it.str() }.orEmpty()

        if (o("bio").isNotEmpty()) {
            return ArtistBio(o("name").ifEmpty { artist }, o("bio"), oTags(), o("area"), o("years"), o("url"), o("photo"))
        }
        val forced = o("wikipedia")
        if (forced.isNotEmpty()) {
            val w = wikipediaSummary(forced)
            if (w.summary.isNotEmpty()) {
                return ArtistBio(
                    o("name").ifEmpty { w.article.ifEmpty { artist } }, w.summary, oTags(), o("area"), o("years"),
                    w.page, o("photo").ifEmpty { w.photo },
                )
            }
        }
        val found = try {
            musicbrainzBiography(artist, o("mbid"))
        } catch (e: Exception) {
            problems += "musicbrainz: ${e.javaClass.simpleName}"
            null
        }
        if (found != null) return found

        // Disambiguated titles first: "Prince" alone is an article about princes.
        var hit: Wiki? = null
        for (candidate in listOf("$artist (band)", "$artist (musician)", "$artist (singer)", "$artist (rapper)", artist)) {
            val w = try { wikipediaSummary(candidate) } catch (e: Exception) { problems += "wikipedia: ${e.javaClass.simpleName}"; continue }
            if (w.summary.isEmpty() || !looksMusical(w.description, w.summary)) continue
            hit = w
            break
        }
        if (hit == null) {
            val fallback = try { discogsBiography(artist) } catch (e: Exception) { problems += "discogs: ${e.javaClass.simpleName}"; null }
            if (fallback != null) return fallback
            lastBioError = problems.joinToString("; ")
            return null
        }
        // The article's own title, only when it is recognisably the same act.
        var display = artist
        if (hit.article.isNotEmpty()) {
            val bare = hit.article.substringBefore("(").trim()
            if (bare.lowercase() == artist.lowercase()) display = bare
        }
        return ArtistBio(display, hit.summary, url = hit.page, photo = hit.photo, source = "Wikipedia")
    }

    private fun musicbrainzBiography(artist: String, mbid: String): ArtistBio? {
        val match: JsonObject = try {
            if (mbid.isNotEmpty()) {
                val one = get(mb("artist/$mbid", "fmt" to "json"))
                if ("score" in one) one else JsonObject(one + ("score" to JsonPrimitive(100)))
            } else {
                val data = get(mb("artist", "query" to artist, "fmt" to "json", "limit" to "1"))
                (data["artists"] as? JsonArray)?.firstOrNull() as? JsonObject ?: return null
            }
        } catch (e: Exception) {
            return null
        }
        // A weak match is worse than none.
        if ((match["score"].num()?.toInt() ?: 0) < 85) return null
        val name = match["name"].str().orEmpty().ifEmpty { artist }
        val area = (match["area"] as? JsonObject)?.get("name").str().orEmpty()
        val tags = (match["tags"] as? JsonArray).orEmpty().take(6).mapNotNull { (it as? JsonObject)?.get("name").str() }
        val span = match["life-span"] as? JsonObject
        val begin = formatSpan(span?.get("begin").str())
        val end = formatSpan(span?.get("end").str())
        val years = if (begin.isEmpty()) "" else if (end.isNotEmpty()) "$begin - $end" else "$begin - present"
        val title = musicbrainzArticle(match["id"].str().orEmpty()).ifEmpty { name }
        var w = wikipediaSummary(title)
        if (!looksMusical(w.description, w.summary)) w = Wiki()
        return ArtistBio(name, w.summary, tags, area, years, w.page, w.photo)
    }

    /** The Wikipedia article MusicBrainz links to for this artist (directly, else through Wikidata). */
    private fun musicbrainzArticle(mbid: String): String {
        val data = try { get(mb("artist/$mbid", "fmt" to "json", "inc" to "url-rels")) } catch (e: Exception) { return "" }
        val relations = (data["relations"] as? JsonArray).orEmpty().filterIsInstance<JsonObject>()
        fun target(r: JsonObject) = (r["url"] as? JsonObject)?.get("resource").str().orEmpty()
        for (r in relations) {
            val t = target(r)
            if (r["type"].str() == "wikipedia" && "wikipedia.org" in t) return URLDecoder.decode(t.substringAfterLast("/"), "UTF-8")
        }
        for (r in relations) {
            val t = target(r)
            if (r["type"].str() == "wikidata" && "wikidata.org" in t) return wikidataArticle(t.substringAfterLast("/"))
        }
        return ""
    }

    private fun wikidataArticle(entity: String): String = try {
        val url = wikidata.toHttpUrl().newBuilder()
            .addQueryParameter("action", "wbgetentities").addQueryParameter("ids", entity)
            .addQueryParameter("props", "sitelinks").addQueryParameter("format", "json").build()
        val e = ((get(url)["entities"] as? JsonObject)?.get(entity) as? JsonObject)
        ((e?.get("sitelinks") as? JsonObject)?.get("enwiki") as? JsonObject)?.get("title").str().orEmpty()
    } catch (e: Exception) {
        ""
    }

    private data class Wiki(
        val summary: String = "",
        val page: String = "",
        val article: String = "",
        val photo: String = "",
        val description: String = "",
    )

    private fun wikipediaSummary(name: String): Wiki {
        val data = try {
            get(("$wikipedia/" + pyQuote(name.replace(" ", "_"), safe = "/")).toHttpUrl())
        } catch (e: Exception) {
            return Wiki()
        }
        if (data["type"].str() == "disambiguation") return Wiki()
        val page = ((data["content_urls"] as? JsonObject)?.get("desktop") as? JsonObject)?.get("page").str().orEmpty()
        val photo = (data["thumbnail"] as? JsonObject)?.get("source").str()?.ifEmpty { null }
            ?: (data["originalimage"] as? JsonObject)?.get("source").str().orEmpty()
        return Wiki(
            data["extract"].str().orEmpty().trim(), page, data["title"].str().orEmpty().trim(), photo,
            data["description"].str().orEmpty().trim(),
        )
    }

    /** artistinfo.discogs_biography: a profile, as the last resort. */
    fun discogsBiography(artist: String): ArtistBio? {
        if (artist.isBlank()) return null
        return cached("discogs|${artist.lowercase()}") {
            val found = try {
                get(
                    "$discogs/database/search".toHttpUrl().newBuilder().addQueryParameter("q", artist)
                        .addQueryParameter("type", "artist").addQueryParameter("per_page", "1").build(),
                    DISCOGS_AGENT,
                )
            } catch (e: Exception) {
                return@cached null
            }
            val best = (found["results"] as? JsonArray)?.firstOrNull() as? JsonObject ?: return@cached null
            if (best["title"].str().orEmpty().trim().lowercase() != artist.trim().lowercase()) return@cached null
            val page = try { get(best["resource_url"].str().orEmpty().toHttpUrl(), DISCOGS_AGENT) } catch (e: Exception) { return@cached null }
            var profile = page["profile"].str().orEmpty().trim()
            if (profile.isEmpty()) return@cached null
            profile = profile.replace(Regex("\\[/?[abilu]]"), "")
                .replace(Regex("\\[[auml]=?\\d+]"), "")
                .replace(Regex("\\[url=[^]]*]"), "")
                .replace("[/url]", "").trim()
            val images = page["images"] as? JsonArray
            val photo = (images?.firstOrNull() as? JsonObject)?.get("uri").str().orEmpty()
            ArtistBio(page["name"].str().orEmpty().ifEmpty { artist }, profile, url = page["uri"].str().orEmpty(), photo = photo, source = "Discogs")
        }
    }

    /**
     * artistinfo.station_article: a talk station's own article. [override]
     * is what ArtistOverrides.stationInfo gave; a typed-in bio wins.
     */
    fun stationArticle(stationName: String, override: Map<String, JsonElement> = emptyMap()): ArtistBio? =
        lookUpStation(stationName, override)?.let { it.copy(summary = Bios.clean(it.summary)) }

    private fun lookUpStation(stationName: String, override: Map<String, JsonElement>): ArtistBio? {
        if (stationName.isEmpty()) return null
        fun o(k: String) = override[k].str().orEmpty()
        if (o("bio").isNotEmpty()) return ArtistBio(o("name").ifEmpty { stationName }, o("bio"), url = o("url"), photo = o("photo"))
        return cached("station|${stationName.lowercase()}|${o("wikipedia").lowercase()}") {
            val w = wikipediaSummary(o("wikipedia").ifEmpty { stationName })
            if (w.summary.isEmpty()) return@cached null
            val text = "${w.description} ${w.summary}".lowercase()
            if (listOf("radio", "station", "broadcast", "fm", "am ", "network").none { it in text }) return@cached null
            ArtistBio(w.article.ifEmpty { stationName }, w.summary, url = w.page, photo = w.photo)
        }
    }

    companion object {
        const val USER_AGENT = "Frequencies/1.5 (personal internet radio player)"
        const val DISCOGS_AGENT = "Frequencies/1.0 +https://github.com/frequencies-radio"
        const val CACHE_TTL_MS = 6 * 3600 * 1000L

        private val MUSIC_WORDS = listOf(
            "band", "singer", "musician", "rapper", "songwriter", "composer",
            "guitarist", "drummer", "bassist", "vocalist", "producer", "dj",
            "duo", "trio", "quartet", "orchestra", "album", "music", "rock",
            "hip hop", "hip-hop", "pop ", "jazz", "metal", "punk", "rapper",
            "recording artist", "record label", "discography",
        )

        /** artistinfo.looks_musical: is this article about a musician at all? */
        fun looksMusical(description: String, summary: String): Boolean {
            val haystack = (description + " " + summary.take(400)).lowercase()
            return MUSIC_WORDS.any { it in haystack }
        }

        private val MONTHS = listOf(
            "January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December",
        )

        /** artistinfo.format_span: "1979-02-15" -> "February 1979"; "1979" -> "1979". */
        fun formatSpan(raw: String?): String {
            if (raw.isNullOrEmpty()) return ""
            val parts = raw.split("-")
            val year = parts[0]
            if (year.length != 4 || !year.all { it.isDigit() }) return raw
            if (parts.size < 2 || parts[1].isEmpty() || !parts[1].all { it.isDigit() }) return year
            val month = parts[1].toInt()
            if (month !in 1..12) return year
            return "${MONTHS[month - 1]} $year"
        }
    }
}

/** Python's urllib.parse.quote: UTF-8, letters, digits and "_.-~" kept, plus [safe]. */
fun pyQuote(text: String, safe: String = "/"): String {
    val out = StringBuilder()
    for (b in text.toByteArray(Charsets.UTF_8)) {
        val c = b.toInt() and 0xff
        val ch = c.toChar()
        if (c < 0x80 && (ch.isLetterOrDigit() || ch in "_.-~" || ch in safe)) out.append(ch)
        else out.append('%').append("0123456789ABCDEF"[c shr 4]).append("0123456789ABCDEF"[c and 15])
    }
    return out.toString()
}
