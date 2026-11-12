package com.frequencies.core

import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * The radio-browser.info directory behind Add Frequency (radiobrowser.py).
 * A search looks for the words in names AND tags (two requests, merged),
 * most listened first.
 */
class RadioBrowser(
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build(),
    private val hosts: List<String> = HOSTS,
) {
    class RadioBrowserError(message: String) : Exception(message)

    data class Result(
        val uuid: String,
        val name: String,
        val url: String,
        val country: String,
        val countryCode: String,
        val language: String,
        val tags: String,
        val favicon: String,
        val homepage: String,
        val codec: String,
        val bitrate: Int,
        val votes: Long,
        val clickCount: Long,
    )

    @Volatile private var base: String? = null

    private fun get(path: String, params: Map<String, String>): JsonArray {
        var last: Exception? = null
        val order = listOfNotNull(base) + hosts.map { if ("://" in it) it else "https://$it" }.filter { it != base }
        for (b in order) {
            val url = "$b$path".toHttpUrl().newBuilder().apply { params.forEach { (k, v) -> addQueryParameter(k, v) } }.build()
            try {
                client.newCall(Request.Builder().url(url).header("User-Agent", AGENT).build()).execute().use { r ->
                    if (!r.isSuccessful) throw RadioBrowserError("HTTP ${r.code}")
                    val data = FreqJson.parseToJsonElement(r.body!!.string()) as? JsonArray
                        ?: throw RadioBrowserError("not a list")
                    base = b
                    return data
                }
            } catch (e: Exception) {
                last = e
            }
        }
        throw RadioBrowserError(plain(last))
    }

    private fun plain(e: Exception?): String {
        val text = (e?.message ?: "").lowercase()
        return when {
            "reset" in text || "aborted" in text -> "The station directory dropped the connection. Try again."
            "timeout" in text || "timed out" in text -> "The station directory did not answer in time."
            else -> "Could not reach the station directory."
        }
    }

    /** Stations with exactly this stream address (artupgrade.candidates). */
    fun byUrl(url: String): List<Result> = get("/json/stations/byurl", mapOf("url" to url))
        .filterIsInstance<JsonObject>().map(::parse)

    /** Names and tags matching [query] (or the most listened with no query), page [offset]. */
    fun search(query: String, limit: Int = 40, offset: Int = 0, order: String = "clickcount"): List<Result> {
        val common = mapOf(
            "limit" to (offset + limit).toString(), "offset" to "0", "order" to order,
            "reverse" to (order != "name").toString(), "hidebroken" to "true",
        )
        val searches = if (query.isBlank()) listOf(emptyMap()) else listOf(mapOf("name" to query.trim()), mapOf("tag" to query.trim()))
        val results = ArrayList<Result>()
        val seen = HashSet<String>()
        for (extra in searches) {
            val data = try { get("/json/stations/search", common + extra) } catch (e: RadioBrowserError) {
                if (results.isEmpty() && extra === searches.last()) throw e else continue
            }
            for (item in data) {
                val r = parse(item as? JsonObject ?: continue)
                if (r.url.isEmpty() || !seen.add(r.uuid)) continue
                results.add(r)
            }
        }
        return results.drop(offset).take(limit)
    }

    /**
     * radiobrowser.new_stations: recently changed and recently played in
     * one "New" list, whichever signal is fresher first.
     */
    fun newStations(limit: Int = 40): List<Result> {
        val results = ArrayList<Result>()
        val seen = HashSet<String>()
        var failed: RadioBrowserError? = null
        for (order in listOf("changetimestamp", "clicktimestamp")) {
            val data = try {
                get("/json/stations/search", mapOf("limit" to maxOf(10, limit / 2).toString(), "reverse" to "true", "hidebroken" to "true", "order" to order))
            } catch (e: RadioBrowserError) { failed = e; continue }
            for (item in data) {
                val r = parse(item as? JsonObject ?: continue)
                if (r.url.isEmpty() || !seen.add(r.uuid)) continue
                results.add(r)
            }
        }
        if (results.isEmpty() && failed != null) throw failed
        return results.take(limit)
    }

    /** Tell the directory a station was played (its click counts). Failures don't matter. */
    fun registerClick(uuid: String) {
        if (uuid.isEmpty()) return
        runCatching { get("/json/url/$uuid", emptyMap()) }
    }

    companion object {
        const val AGENT = "Frequencies/1.5 (Android)"

        /**
         * discover.DiscoverPage's ways to browse the directory: its sort
         * menu plus the Trending and Random buttons, and the "New" list
         * (order key, label).
         */
        val BROWSE = listOf(
            "clickcount" to "Most listened", "clicktrend" to "Trending", "random" to "Random",
            "votes" to "Most voted", "new" to "New",
        )
        val HOSTS = listOf(
            "all.api.radio-browser.info", "de1.api.radio-browser.info", "de2.api.radio-browser.info",
            "at1.api.radio-browser.info", "nl1.api.radio-browser.info",
        )

        fun parse(item: JsonObject): Result {
            fun s(key: String) = item[key].str().orEmpty()
            return Result(
                uuid = s("stationuuid"),
                name = s("name").trim().ifEmpty { "Unnamed station" },
                url = s("url_resolved").ifEmpty { s("url") },
                country = s("country"), countryCode = s("countrycode"), language = s("language"),
                tags = s("tags"), favicon = s("favicon"), homepage = s("homepage"), codec = s("codec"),
                bitrate = item["bitrate"].num()?.toInt() ?: 0,
                votes = item["votes"].num()?.toLong() ?: 0,
                clickCount = item["clickcount"].num()?.toLong() ?: 0,
            )
        }

        /**
         * A directory result as a new saved frequency. No tags, like the
         * desktop's Discover: the directory's genre lists are long and
         * would fill the tag pills (and Android Auto's Tags tab).
         */
        fun toStation(r: Result, newId: () -> String, now: Double): Station =
            Imports.toStation(Imports.Entry(r.name, r.url, r.favicon, emptyList(), r.uuid, r.homepage), newId, now)
    }
}
