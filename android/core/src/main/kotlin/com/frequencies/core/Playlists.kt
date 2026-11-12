package com.frequencies.core

import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * .pls and .m3u addresses are text files listing streams, not audio
 * (playlists.resolve_stream_url). The phone's player can't read them, so
 * they are fetched here and the streams inside are tried in turn.
 */
object Playlists {
    /** "Frequencies/1.5": some servers treat other agents specially (build 117). */
    const val STREAM_AGENT = "Frequencies/1.5"

    /**
     * How long the player gives a stream to start sounding before it moves
     * on (desktop player.CONNECT_TIMEOUT). The health probe waits the same
     * (build 205): one number, so a check never gives up sooner than playing would.
     */
    const val CONNECT_TIMEOUT_MS = 10_000L

    private val URL_RE = Regex("^[a-z][a-z0-9+.\\-]*://", RegexOption.IGNORE_CASE)

    fun isUrl(text: String?): Boolean = text != null && URL_RE.containsMatchIn(text.trim())

    /** True for a playlist file; .m3u8 is HLS, which the player plays itself. */
    fun looksLikePlaylist(url: String): Boolean {
        val path = url.substringBefore('#').substringBefore('?').lowercase()
        return path.endsWith(".pls") || path.endsWith(".m3u")
    }

    fun isHls(url: String): Boolean =
        url.substringBefore('#').substringBefore('?').lowercase().endsWith(".m3u8")

    /** Every stream a playlist's text lists, in order, without repeats. */
    fun streamsIn(text: String): List<String> {
        if ("#EXT-X-" in text) return emptyList()   // an HLS manifest named .m3u
        val head = text.trimStart().take(400).lowercase()
        val listed = if ("[playlist]" in head) readPls(text) else readM3u(text)
        val out = LinkedHashSet<String>()
        listed.filter(::isUrl).forEach { out.add(it.trim()) }
        if (out.isEmpty()) text.lines().map { it.trim() }.filter(::isUrl).forEach { out.add(it) }
        return out.toList()
    }

    private fun readPls(text: String): List<String> {
        val files = sortedMapOf<Int, String>()
        var inSection = false
        for (raw in text.lines()) {
            val line = raw.trim()
            if (line.startsWith("[")) {
                inSection = line.equals("[playlist]", ignoreCase = true)
                continue
            }
            if (!inSection) continue
            val m = Regex("^file(\\d+)\\s*[=:]\\s*(.*)$", RegexOption.IGNORE_CASE).find(line) ?: continue
            files[m.groupValues[1].toInt()] = m.groupValues[2].trim()
        }
        return files.values.toList()
    }

    private fun readM3u(text: String): List<String> =
        text.lines().map { it.trim() }.filter { it.isNotEmpty() && !it.startsWith("#") }

    /**
     * The streams behind [url]: itself when it isn't a playlist, else the
     * ones it lists. Fails open, like the desktop: any error gives the
     * address back unchanged, so a working station never stops working
     * because a fetch went wrong.
     */
    fun resolve(url: String, client: OkHttpClient = defaultClient): List<String> {
        if (!looksLikePlaylist(url)) return listOf(url)
        return try {
            val request = Request.Builder().url(url).header("User-Agent", STREAM_AGENT).build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return listOf(url)
                val body = response.body ?: return listOf(url)
                val text = body.source().let { src ->
                    src.request(64_000)
                    src.buffer.clone().readUtf8(minOf(src.buffer.size, 64_000))
                }
                streamsIn(text).ifEmpty { listOf(url) }
            }
        } catch (e: Exception) {
            listOf(url)
        }
    }

    val defaultClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(8, TimeUnit.SECONDS)
            .build()
    }
}
