package com.frequencies.songid

import com.frequencies.core.FreqJson
import com.frequencies.core.str
import java.io.IOException
import java.net.URI
import java.net.URLEncoder
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * shazam.py: a signature to Shazam's app endpoint and back. Not a public,
 * documented API; if it stops answering, the rest of the app goes on.
 */
class Shazam(
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).build(),
    private val endpoint: String = ENDPOINT,
) {
    class ShazamError(message: String) : Exception(message)

    /** What the screens need from a match (shazam._parse). */
    data class Track(
        val title: String,
        val artist: String,
        val coverUrl: String,
        val appleMusicUrl: String,
        val shazamUrl: String,
        /** Seconds into the song where the sample started; null when Shazam didn't say. */
        val matchOffset: Double?,
        val releaseDate: String,
        val album: String,
    )

    /** The song, or null when nothing matched. Throws [ShazamError] when the lookup itself failed. */
    fun identify(signature: Signature, timestampMs: Long = System.currentTimeMillis()): Track? {
        val body = buildJsonObject {
            put("timezone", "America/Phoenix")
            putJsonObject("signature") {
                put("uri", signature.uri)
                put("samplems", signature.durationMs)
            }
            put("timestamp", timestampMs)
            putJsonObject("context") {}
            putJsonObject("geolocation") {}
        }
        val url = endpoint
            .replace("{uuid_1}", UUID.randomUUID().toString().uppercase())
            .replace("{uuid_2}", UUID.randomUUID().toString().uppercase())
        val request = Request.Builder().url(url)
            .header("User-Agent", AGENT)
            .header("Content-Language", "en_US")
            .header("Accept", "application/json")
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .build()
        val payload = try {
            client.newCall(request).execute().use { r ->
                if (!r.isSuccessful) throw ShazamError("Lookup failed: HTTP ${r.code}")
                FreqJson.parseToJsonElement(r.body!!.string())
            }
        } catch (e: IOException) {
            throw ShazamError("Lookup failed: ${e.javaClass.simpleName}")
        } catch (e: IllegalArgumentException) {
            throw ShazamError("Unreadable response from Shazam")
        }
        return parse(payload as? JsonObject ?: return null)
    }

    companion object {
        const val ENDPOINT = "https://amp.shazam.com/discovery/v5/en/US/android/-/tag/{uuid_1}/{uuid_2}" +
            "?sync=true&webv3=true&sampling=true&connected=&shazamapiversion=v3&sharehub=true" +
            "&hubv5minorversion=v5.1&hidelb=true&video=v3"
        const val AGENT = "Dalvik/2.1.0 (Linux; U; Android 13; Pixel 7 Build/TQ3A.230805.001)"

        fun parse(payload: JsonObject): Track? {
            val track = payload["track"] as? JsonObject ?: return null
            val title = track["title"].str().orEmpty().trim()
            val artist = track["subtitle"].str().orEmpty().trim()
            if (title.isEmpty() && artist.isEmpty()) return null
            return Track(
                title = title,
                artist = artist,
                coverUrl = cover(track),
                appleMusicUrl = appleMusicUrl(payload, title, artist),
                shazamUrl = track["url"].str().orEmpty(),
                matchOffset = matchOffset(payload),
                releaseDate = metadataRow(track, "released", "release date") ?: track["releasedate"].str().orEmpty().trim(),
                album = metadataRow(track, "album").orEmpty(),
            )
        }

        private fun cover(track: JsonObject): String {
            val images = track["images"] as? JsonObject
            for (key in listOf("coverarthq", "coverart", "background")) {
                images?.get(key).str()?.takeIf { it.isNotEmpty() }?.let { return it }
            }
            return ((track["share"] as? JsonObject)?.get("image")).str().orEmpty()
        }

        private fun matchOffset(payload: JsonObject): Double? {
            val first = (payload["matches"] as? JsonArray)?.firstOrNull() as? JsonObject ?: return null
            return (first["offset"] as? JsonPrimitive)?.takeIf { !it.isString }?.doubleOrNull
        }

        private fun metadataRow(track: JsonObject, vararg titles: String): String? {
            for (section in (track["sections"] as? JsonArray).orEmpty()) {
                for (row in ((section as? JsonObject)?.get("metadata") as? JsonArray).orEmpty()) {
                    val r = row as? JsonObject ?: continue
                    if (r["title"].str().orEmpty().trim().lowercase() in titles) {
                        r["text"].str()?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
                    }
                }
            }
            return null
        }

        private fun strings(node: JsonElement?): Sequence<String> = sequence {
            when (node) {
                is JsonPrimitive -> if (node.isString) yield(node.content)
                is JsonObject -> for (v in node.values) yieldAll(strings(v))
                is JsonArray -> for (v in node) yieldAll(strings(v))
                else -> {}
            }
        }

        /** shazam._apple_music_url: a real song link if the answer has one, else an Apple Music search. */
        fun appleMusicUrl(payload: JsonObject, title: String, artist: String): String {
            var best = ""
            for (text in strings(payload)) {
                if (!text.startsWith("http") || "music.apple.com" !in text) continue
                val host = runCatching { URI(text).host.orEmpty().lowercase() }.getOrDefault("")
                if (host != "music.apple.com" && !host.endsWith(".music.apple.com")) continue
                val cleaned = normalizeAppleUrl(text)
                if ("i=" in cleaned || "/song/" in cleaned) return cleaned
                if (best.isEmpty()) best = cleaned
            }
            if (best.isNotEmpty()) return best
            val terms = listOf(artist, title).filter { it.isNotEmpty() }.joinToString(" ").trim()
            return if (terms.isEmpty()) "" else "https://music.apple.com/us/search?term=" + URLEncoder.encode(terms, "UTF-8").replace("+", "%20")
        }

        private val DROPPED = setOf("app", "at", "ct", "itscg", "itsct", "ls", "uo", "mt")

        fun normalizeAppleUrl(url: String): String {
            val uri = runCatching { URI(url) }.getOrNull() ?: return url
            var host = uri.host ?: return url
            if (host.startsWith("geo.")) host = host.substring(4)
            if (!host.endsWith("music.apple.com")) return url
            val kept = (uri.rawQuery ?: "").split('&').filter { it.isNotEmpty() && it.substringBefore('=') !in DROPPED }
            return "https://$host${uri.rawPath}" + if (kept.isEmpty()) "" else "?" + kept.joinToString("&")
        }
    }
}
