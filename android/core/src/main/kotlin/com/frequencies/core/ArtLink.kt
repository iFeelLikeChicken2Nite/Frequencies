package com.frequencies.core

import java.io.File
import java.io.IOException
import kotlinx.serialization.json.JsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * The desktop's artlink.py (build 128): a link to a station's full
 * picture, for QR codes. A code only fits a 64 px copy, so it also carries
 * `art_url`. A picture from the web keeps the address it came from; one
 * the user picked from their own files is uploaded, when it is shared, to
 * the developer's picture service (cloudflare/art-worker.js, the "art"
 * address of photo_pack.json; not a secret, the Worker only takes small
 * pictures).
 *
 * `POST <address>/upload` with the picture as the body answers
 * {"url": ...}. The Worker names a picture by its sha256, so sharing the
 * same one twice stores nothing new.
 */
object ArtLink {
    /** Bytes; the Worker refuses more than 2 MB. */
    const val MAX_UPLOAD = 1_500_000
    /** A bigger picture is scaled to this first. */
    const val SHRINK_PX = 1200
    const val WAITING = "Adding a link to the full-size picture…"
    const val FAILED = "Couldn't upload the picture, so the code carries a small copy."

    /** The picture service's address without the trailing slash, or "" when the build has none. */
    fun service(addresses: PackAddresses): String =
        addresses.art.trim().trimEnd('/').takeIf { it.startsWith("https://") || it.startsWith("http://") }.orEmpty()

    /** artlink.needs_upload: a picture of the user's own that a code can't link to yet. */
    fun needsUpload(station: Station, service: String): Boolean {
        val path = station.customArtPath.trim()
        return service.isNotEmpty() && path.isNotEmpty() && File(path).isFile &&
            !truthy(station["art_from_code"]) && Featured.artLink(station).isEmpty()
    }

    /**
     * Shrinks a picture to at most [SHRINK_PX] px and re-encodes it (PNG when
     * it has transparency, else JPEG at falling quality) until it fits in
     * [MAX_UPLOAD]; null when it can't be read. Platform code (Bitmap).
     */
    fun interface Shrinker {
        fun shrink(picture: ByteArray): ByteArray?
    }

    /** artlink._picture_bytes: the file as it is when small enough, else a smaller copy. */
    fun pictureBytes(file: File, shrinker: Shrinker): ByteArray {
        val data = file.readBytes()
        if (data.size <= MAX_UPLOAD) return data
        val small = shrinker.shrink(data) ?: throw IOException("not a picture")
        if (small.size > MAX_UPLOAD) throw IOException("too big")
        return small
    }

    /** One upload's outcome: the link, or "" and why not (the desktop's reasons). */
    data class Result(val link: String, val reason: String = "")

    /** artlink.Upload.work: put one picture on the service. Blocking; never throws. */
    fun upload(http: OkHttpClient, service: String, file: File, shrinker: Shrinker): Result = try {
        val data = pictureBytes(file, shrinker)
        val request = Request.Builder().url("$service/upload")
            .header("User-Agent", Playlists.STREAM_AGENT)
            .post(data.toRequestBody("application/octet-stream".toMediaType()))
            .build()
        http.newCall(request).execute().use { answer ->
            if (answer.code != 200) return@use Result("", "the picture service said ${answer.code}")
            val body = answer.body?.string().orEmpty()
            val link = runCatching { (FreqJson.parseToJsonElement(body) as? JsonObject)?.get("url").str() }.getOrNull().orEmpty()
            if (link.startsWith("https://") || link.startsWith("http://")) Result(link) else Result("", "the picture service gave no link")
        }
    } catch (e: Exception) {
        Result("", e.javaClass.simpleName)
    }

    /** The desktop's [art] log line. */
    fun logLine(result: Result): String =
        "[art] shared picture " + if (result.link.isNotEmpty()) "uploaded: ${result.link}" else "not uploaded (${result.reason})"
}
