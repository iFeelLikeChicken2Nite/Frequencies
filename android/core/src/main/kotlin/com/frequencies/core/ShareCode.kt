package com.frequencies.core

import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Base64
import java.util.zip.Deflater
import java.util.zip.Inflater
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * One station as a line of text, the desktop's `FREQ1:` code
 * (storage.station_to_code / station_from_code): url-safe base64 of
 * zlib-packed JSON of the featured entry, small enough for a QR code.
 */
object ShareCode {
    const val PREFIX = "FREQ1:"
    const val LIMIT = 2800

    /** Shrinks a picture to a [side]-px JPEG at [quality]; null when it can't be read. Platform code. */
    fun interface Thumbnailer {
        fun thumbnail(picture: ByteArray, side: Int, quality: Int): ByteArray?
    }

    private val compact = kotlinx.serialization.json.Json { prettyPrint = false }

    private fun encode(entry: Map<String, JsonElement>): String {
        val raw = compact.encodeToString(JsonElement.serializer(), JsonObject(entry)).toByteArray(Charsets.UTF_8)
        val deflater = Deflater(9)
        deflater.setInput(raw)
        deflater.finish()
        val out = ByteArrayOutputStream()
        val buf = ByteArray(4096)
        while (!deflater.finished()) out.write(buf, 0, deflater.deflate(buf))
        deflater.end()
        return PREFIX + Base64.getUrlEncoder().encodeToString(out.toByteArray())
    }

    /**
     * The code and whether the picture went in. Smaller and smaller
     * thumbnails until it fits; then no picture, then no card texts or
     * shows, then only what makes it play and sound the same.
     */
    fun fromStation(station: Station, keepArtwork: Boolean = true, thumbnailer: Thumbnailer?): Pair<String, Boolean> {
        val entry = LinkedHashMap(Featured.fromStation(station))
        val art = entry["art_data"].str()
        if (keepArtwork && art != null && thumbnailer != null) {
            val picture = runCatching { Base64.getDecoder().decode(art) }.getOrNull()
            if (picture != null) {
                for ((side, quality) in listOf(64 to 50, 56 to 45, 48 to 40, 40 to 35, 32 to 30)) {
                    val thumb = thumbnailer.thumbnail(picture, side, quality) ?: break
                    val shrunk = LinkedHashMap(entry)
                    shrunk["art_data"] = JsonPrimitive(Base64.getEncoder().encodeToString(thumb))
                    shrunk["art_name"] = JsonPrimitive("shared.jpg")
                    val code = encode(shrunk)
                    if (code.length <= LIMIT) return code to true
                }
            }
        }
        var slim: Map<String, JsonElement> = entry - setOf("art_data", "art_name")
        var code = encode(slim)
        if (code.length > LIMIT) {
            slim = slim - setOf("card_body", "card_subtitle", "card_title", "shows")
            code = encode(slim)
        }
        if (code.length > LIMIT) {
            val keep = setOf(
                "name", "url", "custom_name", "kind", "playlist_url", "twitch_channel", "tags",
                "eq_bass", "eq_mid", "eq_treble", "eq_bands", "gain_db", "insecure", "favicon", "homepage", "art_url",
            )
            code = encode(slim.filterKeys { it in keep })
        }
        return code to false
    }

    /** The featured entry a code carries, or null for anything unreadable (clipboards and cameras send nonsense). */
    fun entry(text: String?): JsonObject? {
        val t = text?.trim().orEmpty()
        if (!t.startsWith(PREFIX)) return null
        return try {
            val packed = Base64.getUrlDecoder().decode(t.substring(PREFIX.length).trim())
            val inflater = Inflater()
            inflater.setInput(packed)
            val out = ByteArrayOutputStream()
            val buf = ByteArray(4096)
            while (!inflater.finished()) {
                val n = inflater.inflate(buf)
                if (n == 0 && (inflater.needsInput() || inflater.needsDictionary())) break
                out.write(buf, 0, n)
            }
            inflater.end()
            val obj = FreqJson.parseToJsonElement(out.toString(Charsets.UTF_8.name())) as? JsonObject
            obj?.takeIf { truthy(it["url"]) }
        } catch (e: Exception) {
            null
        }
    }

    /** The station a code describes (picture written to [artDir]), or null. */
    fun toStation(text: String?, artDir: File, newId: () -> String): Station? =
        entry(text)?.let { Featured.toStation(it, artDir, newId) }
}
