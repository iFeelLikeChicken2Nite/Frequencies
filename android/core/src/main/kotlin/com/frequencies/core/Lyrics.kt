package com.frequencies.core

import java.io.IOException
import javax.net.ssl.SSLException
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

/** One line of lyrics; [ms] is when it starts, or -1 for plain (untimed) lyrics. */
data class LyricLine(val ms: Long, val text: String)

sealed interface LyricsResult {
    /** [source] is the credit: "LRCLIB", "lrcmux" or "lyrics.ovh". */
    data class Found(val lines: List<LyricLine>, val source: String, val instrumental: Boolean = false) : LyricsResult {
        val timed: Boolean get() = lines.isNotEmpty() && lines[0].ms >= 0
    }
    data object None : LyricsResult
    data class Failed(val message: String) : LyricsResult
}

/**
 * The desktop's lyrics lookup (workers.LyricsWorker): timed lyrics from
 * LRCLIB first (lrclib.py: the lrcmux mirror, then lrclib.net), then
 * lyrics.py's plain lookups (lrclib get and search, then lyrics.ovh).
 * Answers are kept for a while, and the service is rested after a run of
 * failures or a 429, as lrclib.py does.
 */
class LyricsClient(
    private val http: OkHttpClient,
    private val hosts: List<String> = HOSTS,
    private val lrclib: String = LRCLIB,
    private val ovh: String = LYRICS_OVH,
    private val now: () -> Long = System::currentTimeMillis,
    private val retryWaitsMs: List<Long> = listOf(1000, 2500),
) {
    private val cache = HashMap<String, Pair<LyricsResult, Long>>()
    private var failures = 0
    private var quietUntil = 0L

    fun fetch(artist: String, title: String, durationSeconds: Int = 0): LyricsResult {
        val a = artist.trim()
        val t = title.trim()
        if (t.isEmpty()) return LyricsResult.None
        val key = "$a|$t|$durationSeconds".lowercase()
        synchronized(cache) {
            cache[key]?.let { (value, until) -> if (now() < until) return value else cache.remove(key) }
        }
        val result = timed(a, t, durationSeconds) ?: plain(a, t)
        val keep = if (result is LyricsResult.Found) CACHE_MS else MISS_MS
        if (result !is LyricsResult.Failed) synchronized(cache) {
            if (cache.size > 500) cache.clear()
            cache[key] = result to now() + keep
        }
        return result
    }

    // lrclib.fetch: the exact match when the length is known, else search.
    private fun timed(artist: String, title: String, duration: Int): LyricsResult.Found? {
        if (artist.isEmpty() || now() < quietUntil) return null
        var found: JsonObject? = null
        var host = ""
        if (duration > 0) {
            ask("get", mapOf("artist_name" to artist, "track_name" to title, "duration" to duration.toString()))?.let { (body, h) ->
                (body as? JsonObject)?.let { found = it; host = h }
            }
        }
        if (found == null) {
            ask("search", mapOf("artist_name" to artist, "track_name" to title))?.let { (body, h) ->
                val rows = (body as? JsonArray)?.filterIsInstance<JsonObject>().orEmpty()
                if (rows.isNotEmpty()) {
                    found = rows.firstOrNull { !it["syncedLyrics"].str().isNullOrBlank() } ?: rows[0]
                    host = h
                }
            }
        }
        val row = found
        if (row == null) { noteResult(false); return null }
        noteResult(true)
        val source = if ("lrcmux" in host) "lrcmux" else "LRCLIB"
        val synced = parseLrc(row["syncedLyrics"].str().orEmpty())
        if (synced.isNotEmpty()) return LyricsResult.Found(synced, source)
        val plain = row["plainLyrics"].str().orEmpty().trim()
        if (plain.isNotEmpty()) return LyricsResult.Found(plain.lines().map { LyricLine(-1, it) }, source)
        if (row.flag("instrumental")) return LyricsResult.Found(emptyList(), source, instrumental = true)
        return null
    }

    // lrclib._ask: each host in turn, until one answers with something.
    private fun ask(path: String, params: Map<String, String>): Pair<Any, String>? {
        for (host in hosts) {
            val url = "$host/$path".toHttpUrl().newBuilder().apply {
                params.forEach { (k, v) -> if (v.isNotEmpty()) addQueryParameter(k, v) }
            }.build()
            try {
                http.newCall(request(url)).execute().use { response ->
                    if (response.code == 429) rest(response.header("Retry-After"))
                    if (!response.isSuccessful) return@use
                    val body = FreqJson.parseToJsonElement(response.body!!.string())
                    val empty = (body is JsonArray && body.isEmpty()) || (body is JsonObject && body.isEmpty())
                    if (!empty) return body to host
                }
            } catch (e: Exception) {
                // the next host
            }
        }
        return null
    }

    private fun noteResult(ok: Boolean) = synchronized(this) {
        if (ok) { failures = 0; return }
        if (++failures >= 3) { quietUntil = now() + 5 * 60_000; failures = 0 }
    }

    private fun rest(retryAfter: String?) = synchronized(this) {
        val seconds = retryAfter?.trim()?.toDoubleOrNull() ?: 60.0
        quietUntil = maxOf(quietUntil, now() + (seconds.coerceIn(1.0, 600.0) * 1000).toLong())
    }

    // lyrics.fetch: plain lyrics, lrclib then lyrics.ovh.
    private fun plain(artist: String, title: String): LyricsResult {
        val a = clean(artist)
        val t = clean(title)
        if (t.isEmpty()) return LyricsResult.None
        var primaryFailed = false
        try {
            lrclibPlain(a, t)?.let { return it }
        } catch (e: IOException) {
            primaryFailed = true
        }
        var fallbackFailed = false
        try {
            lyricsOvh(a, t)?.let { return it }
        } catch (e: IOException) {
            fallbackFailed = true
        }
        return if (primaryFailed || fallbackFailed) LyricsResult.Failed(BUSY) else LyricsResult.None
    }

    private fun lrclibPlain(artist: String, title: String): LyricsResult.Found? {
        val get = "$lrclib/get".toHttpUrl().newBuilder()
            .addQueryParameter("artist_name", artist).addQueryParameter("track_name", title).build()
        getWithRetries(get).use { response ->
            if (response.code == 200) {
                (parseOrThrow(response.body!!.string()) as? JsonObject)?.let { resultFound(it)?.let { found -> return found } }
            } else if (response.code != 404) {
                throw IOException("lrclib ${response.code}")
            }
        }
        val query = listOf(artist, title).filter { it.isNotEmpty() }.joinToString(" ")
        val search = "$lrclib/search".toHttpUrl().newBuilder().addQueryParameter("q", query).build()
        getWithRetries(search).use { response ->
            if (!response.isSuccessful) throw IOException("lrclib ${response.code}")
            val rows = parseOrThrow(response.body!!.string()) as? JsonArray ?: return null
            for (row in rows.take(5)) {
                (row as? JsonObject)?.let { resultFound(it) }?.let { return it }
            }
        }
        return null
    }

    // lyrics._result_to_dict: plain text (or the timed lines' words), else nothing.
    private fun resultFound(item: JsonObject): LyricsResult.Found? {
        val synced = parseLrc(item["syncedLyrics"].str().orEmpty())
        val plain = item["plainLyrics"].str().orEmpty().trim()
        if (item.flag("instrumental") && plain.isEmpty() && synced.isEmpty()) {
            return LyricsResult.Found(emptyList(), "LRCLIB", instrumental = true)
        }
        if (synced.isNotEmpty()) return LyricsResult.Found(synced, "LRCLIB")
        if (plain.isEmpty()) return null
        return LyricsResult.Found(plain.lines().map { LyricLine(-1, it) }, "LRCLIB")
    }

    // lyrics._fallback_lyrics: a different service, for when lrclib can't be reached.
    private fun lyricsOvh(artist: String, title: String): LyricsResult.Found? {
        val url = ovh.toHttpUrl().newBuilder().addPathSegment(artist).addPathSegment(title).build()
        http.newCall(request(url)).execute().use { response ->
            if (response.code == 404) return null
            if (!response.isSuccessful) throw IOException("lyrics.ovh ${response.code}")
            val body = parseOrThrow(response.body!!.string()) as? JsonObject ?: return null
            val text = body["lyrics"].str().orEmpty().trim()
            if (text.isEmpty()) return null
            return LyricsResult.Found(text.lines().map { LyricLine(-1, it) }, "lyrics.ovh")
        }
    }

    // lyrics._get: a 5xx, a timeout or a dropped connection is tried again; a 4xx is final.
    private fun getWithRetries(url: HttpUrl): okhttp3.Response {
        var last: IOException? = null
        for (attempt in 0..retryWaitsMs.size) {
            try {
                val response = http.newCall(request(url)).execute()
                if (response.code < 500 || attempt == retryWaitsMs.size) return response
                response.close()
                last = IOException("${response.code} Server Error")
            } catch (e: SSLException) {
                throw e
            } catch (e: IOException) {
                last = e
            }
            if (attempt < retryWaitsMs.size) Thread.sleep(retryWaitsMs[attempt])
        }
        throw last ?: IOException("no answer")
    }

    private fun parseOrThrow(text: String) = try {
        FreqJson.parseToJsonElement(text)
    } catch (e: Exception) {
        throw IOException("unreadable answer", e)
    }

    private fun request(url: HttpUrl) = Request.Builder().url(url).header("User-Agent", USER_AGENT).build()

    private fun JsonObject.flag(key: String) = (this[key] as? kotlinx.serialization.json.JsonPrimitive)?.booleanOrNull == true

    companion object {
        val HOSTS = listOf("https://api.lrcmux.dev/compat/lrclib/api", "https://lrclib.net/api")
        const val LRCLIB = "https://lrclib.net/api"
        const val LYRICS_OVH = "https://api.lyrics.ovh/v1"
        const val USER_AGENT = "Frequencies/1.5 (https://github.com/iFeelLikeChicken2Nite)"
        const val BUSY = "The lyrics service is busy right now. Try again in a little while."
        private const val CACHE_MS = 6 * 3600_000L
        private const val MISS_MS = 30 * 60_000L

        private val STAMP = Regex("""\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?]""")

        /** lrclib.parse_lrc: stamped lines in order, a stamped blank line kept (it is a pause). */
        fun parseLrc(text: String): List<LyricLine> {
            val out = ArrayList<LyricLine>()
            for (raw in text.lines()) {
                val stamps = STAMP.findAll(raw).toList()
                if (stamps.isEmpty()) continue
                val words = raw.substring(stamps.last().range.last + 1).trim()
                for (stamp in stamps) {
                    val minutes = stamp.groupValues[1].toLong()
                    val seconds = stamp.groupValues[2].toLong()
                    val fraction = stamp.groupValues[3].ifEmpty { "0" }
                    // Hundredths as a rule, thousandths with three digits; one digit is tenths.
                    val ms = when (fraction.length) { 3 -> fraction.toLong(); 1 -> fraction.toLong() * 100; else -> fraction.toLong() * 10 }
                    out.add(LyricLine(minutes * 60_000 + seconds * 1000 + ms, words))
                }
            }
            return out.sortedBy { it.ms }
        }

        /** lrclib.line_at: the line being sung at [ms], or -1 before the first. */
        fun lineAt(lines: List<LyricLine>, ms: Long): Int {
            var found = -1
            for ((i, line) in lines.withIndex()) {
                if (line.ms <= ms) found = i else break
            }
            return found
        }

        private val CLEANERS = listOf(
            Regex("""\((?:official|lyric|audio|hd|remaster).*?\)""", RegexOption.IGNORE_CASE),
            Regex("""\[(?:official|lyric|audio|hd|remaster).*?]""", RegexOption.IGNORE_CASE),
            Regex("""\s*[-–]\s*(?:\d{4}\s*)?(?:digital\s+)?(?:re)?master(?:ed)?.*$""", RegexOption.IGNORE_CASE),
            Regex("""\s*[-–]\s*official.*$""", RegexOption.IGNORE_CASE),
        )

        /** lyrics._clean: radio's "(Official Video)", "- 2011 Remaster" and the like. */
        fun clean(text: String): String {
            var s = text
            for (r in CLEANERS) s = r.replace(s, "")
            s = Regex("""\s+""").replace(s, " ")
            return s.trim(' ', '-', '–', '—', '·', '|')
        }
    }
}

/**
 * lyrics.search_url (desktop build 151, task 213): when the lyrics service
 * is busy or has nothing for the song, the Lyrics tab offers a web search
 * for them instead. The browser's own search engine can't be asked for,
 * so a neutral one, as on the desktop.
 */
object LyricsSearch {
    const val WEB_SEARCH = "https://duckduckgo.com/?q="
    /** nowplaying.SEARCH_TEXT */
    const val TEXT = "Search the web for these lyrics"

    /** A web search for "<artist> <title> lyrics", or "" with no song. */
    fun url(artist: String?, title: String?): String {
        val t = title.orEmpty().trim()
        if (t.isEmpty()) return ""
        val words = listOf(artist.orEmpty().trim(), t, "lyrics").filter { it.isNotEmpty() }.joinToString(" ")
        return WEB_SEARCH + pyQuote(words, safe = "").replace("%20", "+")
    }
}
