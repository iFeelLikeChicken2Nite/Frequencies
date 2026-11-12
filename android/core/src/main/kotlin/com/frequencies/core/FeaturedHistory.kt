package com.frequencies.core

import java.time.LocalDate
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Previously featured frequencies (desktop featuredhistory.py, build 151,
 * task 211; Android build 172).
 *
 * featured.json lives in a public repository, so every list it ever held is
 * in that repository's history. [refresh] asks GitHub's commits API (no
 * token; once a day at most) which versions of the file there are, and
 * reads each version not read yet from raw.githubusercontent.com. A version
 * is read once, ever: what it held is folded into `history.json` beside the
 * featured cache, the same shape the desktop writes.
 *
 * Every list this install is shown is recorded too ([noteSeen]), so the list
 * still has something to show offline or rate-limited.
 *
 * A station is one entry, by its stream address: its newest settings, the
 * first day it was featured and the day it stopped being. [refresh] is
 * network: never on the main thread.
 */
class FeaturedHistory(
    private val dir: java.io.File,
    private val http: okhttp3.OkHttpClient,
    private val listUrl: String = FeaturedList.URL,
    private val api: String = API,
    private val raw: String = RAW,
    private val clock: () -> Long = { System.currentTimeMillis() / 1000 },
    private val today: () -> String = { LocalDate.now().toString() },
) {
    data class Previous(val item: JsonObject, val first: String, val last: String)

    private val file get() = java.io.File(dir, FILE)

    /** (owner, repo, branch, path) of featured.json, from its raw address. */
    fun source(): List<String>? =
        Regex("https://raw\\.githubusercontent\\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$").find(listUrl)?.groupValues?.drop(1)

    // -- the file ---------------------------------------------------------------

    private class Data(
        val commits: MutableList<String>,
        var lastKeys: List<String>,
        var checkedAt: Long,
        var failedAt: Long,
        val stations: LinkedHashMap<String, MutableMap<String, JsonElement>>,
    )

    private fun read(): Data {
        val o = readJson(file) as? JsonObject
        val st = o?.get("stations") as? JsonObject ?: return Data(mutableListOf(), emptyList(), 0, 0, LinkedHashMap())
        val stations = LinkedHashMap<String, MutableMap<String, JsonElement>>()
        for ((k, v) in st) (v as? JsonObject)?.let { stations[k] = LinkedHashMap(it) }
        return Data(
            (o["commits"] as? JsonArray)?.mapNotNull { it.str() }?.toMutableList() ?: mutableListOf(),
            (o["last_keys"] as? JsonArray)?.mapNotNull { it.str() } ?: emptyList(),
            o["checked_at"].num()?.toLong() ?: 0,
            o["failed_at"].num()?.toLong() ?: 0,
            stations,
        )
    }

    private fun write(d: Data) {
        runCatching {
            writeJsonAtomic(
                file,
                JsonObject(
                    linkedMapOf(
                        "commits" to stringArray(d.commits),
                        "last_keys" to stringArray(d.lastKeys),
                        "checked_at" to JsonPrimitive(d.checkedAt),
                        "failed_at" to JsonPrimitive(d.failedAt),
                        "stations" to JsonObject(d.stations.mapValues { JsonObject(it.value) }),
                    ),
                ),
            )
        }
    }

    // -- what this install has seen ------------------------------------------------

    /** Record today's featured list (raw featured.json entries). Written only when something changed. */
    fun noteSeen(items: List<JsonObject>, day: String = today()) = synchronized(LOCK) {
        val d = read()
        var changed = false
        for (item in items) {
            val key = keyOf(item).takeIf { it.isNotEmpty() } ?: continue
            val entry = d.stations.getOrPut(key) { changed = true; linkedMapOf("item" to item) }
            if ((entry["seen_from"].str() ?: "9999") > day) { entry["seen_from"] = JsonPrimitive(day); changed = true }
            if ((entry["seen_to"].str() ?: "") < day) {
                entry["seen_to"] = JsonPrimitive(day)
                entry["item"] = item
                changed = true
            }
        }
        if (changed) write(d)
    }

    // -- the repository's history ---------------------------------------------------

    private fun get(url: String): okhttp3.Response =
        http.newCall(
            okhttp3.Request.Builder().url(url)
                .header("User-Agent", RadioBrowser.AGENT)
                .header("Accept", "application/vnd.github+json")
                .build(),
        ).execute()

    private class Limited(message: String) : Exception(message)

    /** Every version of the file, oldest first: (sha, day). */
    private fun commits(owner: String, repo: String, branch: String, path: String): List<Pair<String, String>> {
        val out = mutableListOf<Pair<String, String>>()
        for (page in 1..MAX_PAGES) {
            val batch = get("$api/repos/$owner/$repo/commits?path=$path&sha=$branch&per_page=100&page=$page").use { r ->
                if (r.code == 403 || r.code == 429) throw Limited("GitHub's limit for now (${r.code}, ${r.header("X-RateLimit-Remaining") ?: "?"} left)")
                if (!r.isSuccessful) throw java.io.IOException("HTTP ${r.code}")
                FreqJson.parseToJsonElement(r.body!!.string()) as? JsonArray
            } ?: break
            for (c in batch) {
                val o = c as? JsonObject ?: continue
                val info = o["commit"] as? JsonObject
                val date = ((info?.get("committer") as? JsonObject)?.get("date").str()
                    ?: (info?.get("author") as? JsonObject)?.get("date").str()).orEmpty()
                o["sha"].str()?.takeIf { it.isNotEmpty() }?.let { out += it to date.take(10) }
            }
            if (batch.size < 100) break
        }
        return out.reversed()
    }

    /**
     * Read any versions of featured.json not read yet. Returns "" when the
     * history is current, else why not (shown as a quiet line under the list).
     */
    fun refresh(force: Boolean = false, offline: Boolean = false): String {
        val (owner, repo, branch, path) = source() ?: return "no featured list address"
        val now = clock()
        val start = synchronized(LOCK) { read() }
        if (!force && now - start.checkedAt < REFRESH_EVERY) return ""
        if (!force && now - start.failedAt < RETRY_AFTER) return "GitHub couldn't be reached a little while ago"
        if (offline) return "offline"
        fun failed() = synchronized(LOCK) { read().also { it.failedAt = now; write(it) } }
        val versions = try {
            commits(owner, repo, branch, path)
        } catch (e: Exception) {
            failed()
            return if (e is Limited) e.message.orEmpty() else "couldn't reach GitHub"
        }
        val done = start.commits.toSet()
        for ((sha, day) in versions.filter { it.first !in done }) {
            val payload: JsonElement? = try {
                get("$raw/$owner/$repo/$sha/$path").use { r ->
                    if (!r.isSuccessful) throw java.io.IOException("HTTP ${r.code}")
                    val text = r.body!!.string()
                    runCatching { FreqJson.parseToJsonElement(text) }.getOrNull()   // a broken upload: skip it
                }
            } catch (e: Exception) {
                failed()
                return "couldn't read every older list"
            }
            synchronized(LOCK) {
                val d = read()
                if (sha !in d.commits) {
                    if (payload != null) fold(d, items(payload), day)
                    d.commits += sha
                    write(d)
                }
            }
        }
        synchronized(LOCK) { read().also { it.checkedAt = now; it.failedAt = 0; write(it) } }
        return ""
    }

    /** One version of the list, in commit order, into the history. */
    private fun fold(d: Data, items: List<JsonObject>, day: String) {
        val keys = mutableListOf<String>()
        for (item in items) {
            val key = keyOf(item)
            keys += key
            val entry = d.stations.getOrPut(key) { linkedMapOf("item" to item) }
            if ((entry["from"].str() ?: "9999") > day) entry["from"] = JsonPrimitive(day)
            if (day >= (entry["version_day"].str() ?: "")) {
                entry["item"] = item
                entry["version_day"] = JsonPrimitive(day)
            }
            entry["to"] = JsonPrimitive("")
        }
        for (key in d.lastKeys) if (key !in keys) d.stations[key]?.set("to", JsonPrimitive(day))
        d.lastKeys = keys
    }

    // -- what the list shows -------------------------------------------------------

    /** Every frequency featured before and not featured now, newest first. [current] is today's list. */
    fun previous(current: List<JsonObject>): List<Previous> {
        val d = synchronized(LOCK) { read() }
        val now = current.map(::keyOf).toSet()
        val out = mutableListOf<Previous>()
        for ((key, entry) in d.stations) {
            val item = entry["item"] as? JsonObject ?: continue
            if (key in now) continue
            var first = listOf(entry["from"].str(), entry["seen_from"].str(), "9999").filter { !it.isNullOrEmpty() }.map { it!! }.min()
            val last = entry["to"].str()?.takeIf { it.isNotEmpty() } ?: entry["seen_to"].str() ?: ""
            if (first == "9999") first = last
            out += Previous(item, first, last)
        }
        return out.sortedWith(compareByDescending<Previous> { it.last.ifEmpty { it.first } }.thenByDescending { it.first })
    }

    companion object {
        const val FILE = "history.json"
        const val REFRESH_EVERY = 24 * 60 * 60L
        const val RETRY_AFTER = 60 * 60L
        const val MAX_PAGES = 5
        const val API = "https://api.github.com"
        const val RAW = "https://raw.githubusercontent.com"
        private val LOCK = Any()

        /** One station, whatever its name says: its stream address. */
        fun keyOf(item: JsonObject): String = item["url"].str().orEmpty().trim().lowercase().trimEnd('/')

        private fun items(payload: JsonElement): List<JsonObject> {
            val list = payload as? JsonArray ?: (payload as? JsonObject)?.get("stations") as? JsonArray ?: return emptyList()
            return list.filterIsInstance<JsonObject>().filter { keyOf(it).isNotEmpty() }
        }

        private val MONTHS = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

        private fun day(text: String): LocalDate? = runCatching { LocalDate.parse(text) }.getOrNull()

        /** "Sep 3 – Oct 18" (the year only when it isn't this year's), "Sep 3, 2025 – Jan 4, 2026" across years. */
        fun whenText(first: String, last: String, today: LocalDate = LocalDate.now()): String {
            val a = day(first)
            val b = day(last)
            fun short(d: LocalDate, withYear: Boolean) = "${MONTHS[d.monthValue - 1]} ${d.dayOfMonth}" + if (withYear) ", ${d.year}" else ""
            if (a != null && b != null && a != b) {
                val across = a.year != b.year
                return "${short(a, across)} – ${short(b, across || b.year != today.year)}"
            }
            val one = a ?: b ?: return "Featured before"
            return short(one, one.year != today.year)
        }
    }
}
