package com.frequencies.core

import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * A thumbs down on the Artist tab (photopack.report and friends): an issue
 * on the developer's reports repository, filed by the developer's report
 * Worker ([ReportPoster]); with no report address, or when the Worker
 * says no, the pre-filled issue opens in the browser for the user to
 * submit. One report per artist per day. Nothing about the user goes in it.
 */
object ThumbsDown {
    /** photopack.WRONG_THINGS */
    val WRONG_THINGS = linkedMapOf("bio" to "Bio", "photo" to "Photo", "both" to "Bio and photo")

    /** photopack._issue_text: the issue's title and body. */
    fun issueText(artist: String, source: String, bio: String, what: String = "both", photo: String = "", now: Date = Date()): Pair<String, String> {
        val w = if (what in WRONG_THINGS) what else "both"
        val lines = mutableListOf("Artist: $artist", "What is wrong: ${WRONG_THINGS[w]}")
        if (w == "bio" || w == "both") {
            lines += "Bio from: ${source.ifEmpty { "unknown" }}"
            lines += "Wrong info (first 200 characters):\n> ${bio.take(200)}"
        }
        if (w == "photo" || w == "both") lines += "Photo shown: ${photo.ifEmpty { "none shown" }}"
        lines += "(${SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(now)})"
        return "Wrong artist info (${WRONG_THINGS[w]!!.lowercase()}): $artist" to lines.joinToString("\n")
    }

    /** photopack._issue_address: the browser's pre-filled new issue. */
    fun issueAddress(repo: String, title: String, body: String): String =
        "$repo/issues/new?title=${pyQuote(title, safe = "")}&body=${pyQuote(body, safe = "")}"

    private fun sentToday(settings: Settings, now: Date): Pair<MutableMap<String, kotlinx.serialization.json.JsonElement>, String> {
        val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(now)
        val sent = (settings["report_sent"] as? JsonObject).orEmpty().filterValues { it.str() == today }.toMutableMap()
        settings["report_sent"] = JsonObject(sent)
        return sent to today
    }

    /**
     * photopack._due: one report per artist per day; true if none went out
     * today. Since desktop build 152 it only asks: [markSent] writes it down
     * once the report really went.
     */
    fun due(settings: Settings, artist: String, now: Date = Date()): Boolean =
        artist.lowercase() !in sentToday(settings, now).first

    /** photopack._mark_sent: this artist was reported today. */
    fun markSent(settings: Settings, artist: String, now: Date = Date()) {
        val (sent, today) = sentToday(settings, now)
        sent[artist.lowercase()] = JsonPrimitive(today)
        settings["report_sent"] = JsonObject(sent)
    }

    /** mainwindow._store_verdict: the local note of a wrong lookup (settings artist_reports). */
    fun noteWrong(settings: Settings, artist: String, source: String, what: String) {
        val reports = LinkedHashMap((settings["artist_reports"] as? JsonObject).orEmpty())
        val entry = LinkedHashMap((reports[artist.lowercase()] as? JsonObject).orEmpty())
        entry["wrong"] = JsonPrimitive((entry["wrong"].num()?.toInt() ?: 0) + 1)
        entry["source"] = JsonPrimitive(source)
        entry["what"] = JsonPrimitive(what)
        reports[artist.lowercase()] = JsonObject(entry)
        settings["artist_reports"] = JsonObject(reports)
    }

    /** A thumbs down taken back within its fifteen seconds: [noteWrong]'s count goes back down. */
    fun takeBackNote(settings: Settings, artist: String) {
        val reports = LinkedHashMap((settings["artist_reports"] as? JsonObject).orEmpty())
        val entry = LinkedHashMap((reports[artist.lowercase()] as? JsonObject) ?: return)
        val left = (entry["wrong"].num()?.toInt() ?: 1) - 1
        if (left <= 0 && entry.keys.all { it in setOf("wrong", "source", "what") }) reports.remove(artist.lowercase())
        else { entry["wrong"] = JsonPrimitive(left.coerceAtLeast(0)); reports[artist.lowercase()] = JsonObject(entry) }
        settings["artist_reports"] = JsonObject(reports)
    }

    /**
     * photopack.photo_link: where the photo shown came from, in words. A
     * download's address, the pack's file name (with the developer's link),
     * the developer's photo link, or an honest "unknown".
     */
    fun photoLink(shown: String, pack: PhotoPackStore, override: Map<String, kotlinx.serialization.json.JsonElement>): String {
        if (shown.isEmpty()) return ""
        if (shown.startsWith("http://") || shown.startsWith("https://")) return shown
        val link = override["photo_url"].str().orEmpty().trim()
        val file = File(shown.removePrefix("file://"))
        pack.relativeToPack(file)?.let { name -> return "photo pack, $name" + if (link.isNotEmpty()) " ($link)" else "" }
        if (link.isNotEmpty()) return link
        return "unknown (a local file, ${file.name})"
    }
}

/**
 * photopack.report_fields + ReportPost (build 151): one report posted to the
 * developer's report Worker (cloudflare/report-worker.js), which holds the
 * token and files the issue. No token is in the app. The request format is
 * cloudflare/README.md's; Linux, Windows and Android send the same fields.
 */
class ReportPoster(private val http: OkHttpClient) {
    sealed interface Result {
        /** The Worker filed it; [url] is the issue. */
        data class Filed(val url: String) : Result
        /** That artist was already reported today; nothing new was filed. */
        data object Already : Result
        /**
         * Not filed. [permanent]: the Worker said no (photopack.ReportPost.permanent:
         * a 4xx other than 408/429, or an answer without "ok"), so retrying won't
         * help and the pre-filled browser issue is the way; otherwise try again later.
         */
        data class Failed(val why: String, val permanent: Boolean = false) : Result
    }

    fun send(address: String, fields: JsonObject): Result {
        val base = address.trim().trimEnd('/')
        if (!base.startsWith("https://") && !base.startsWith("http://")) return Result.Failed("no report address")
        val request = runCatching {
            Request.Builder()
                .url("$base/report")
                .header("User-Agent", PackUpdater.AGENT)
                .post(encodeJson(fields).toRequestBody("application/json".toMediaType()))
                .build()
        }.getOrElse { return Result.Failed("the report address isn't a web address") }
        return try {
            http.newCall(request).execute().use { r ->
                val data = runCatching { FreqJson.parseToJsonElement(r.body?.string().orEmpty()) as? JsonObject }.getOrNull()
                when {
                    !r.isSuccessful -> Result.Failed(
                        "the report service answered ${r.code}" + (data?.get("error").str()?.let { " ($it)" } ?: ""),
                        permanent = r.code in 400..499 && r.code != 408 && r.code != 429,
                    )
                    data == null || !truthy(data["ok"]) -> Result.Failed(data?.get("error").str() ?: "no answer", permanent = true)
                    truthy(data["already"]) -> Result.Already
                    else -> Result.Filed(data["url"].str() ?: "filed")
                }
            }
        } catch (e: java.io.IOException) {
            Result.Failed("couldn't reach the report service (offline?)")
        }
    }

    companion object {
        /** photopack.report_fields, with os "android". */
        fun fields(artist: String, source: String, bio: String, what: String = "both", photo: String = "", station: String = "", build: String = ""): JsonObject =
            buildJsonObject {
                put("artist", artist)
                put("what", if (what in ThumbsDown.WRONG_THINGS || what == "test") what else "both")
                put("source", source)
                put("bio", bio.take(200))
                put("photo", photo)
                put("station", station)
                put("build", build)
                put("os", "android")
            }
    }
}
