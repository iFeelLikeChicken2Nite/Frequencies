package com.frequencies.core

import java.io.File
import java.util.Base64
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * The desktop's full backup file (storage.export_backup / read_backup):
 * every station, settings, health, history and overrides, with each
 * station's picture embedded, because a path only means something on the
 * machine that wrote it. Reading one writes the pictures to this device
 * and points the stations at the copies.
 */
object Backup {
    const val FORMAT = "frequencies-backup"
    private val LEGACY_FORMATS = setOf("streamdeck-radio-backup")
    const val VERSION = 2

    class Contents(
        val stations: List<Station>,
        val settings: Settings,
        val health: JsonObject,
        val history: JsonArray,
        val artistOverrides: JsonObject,
        val stationOverrides: JsonObject,
    )

    class BackupError(message: String) : Exception(message)

    fun export(
        contents: Contents,
        exportedAt: Double = System.currentTimeMillis() / 1000.0,
    ): JsonObject = JsonObject(
        linkedMapOf(
            "format" to JsonPrimitive(FORMAT),
            "version" to JsonPrimitive(VERSION),
            "exported_at" to floatJson(exportedAt),
            "stations" to JsonArray(contents.stations.map { it.toJson() }),
            "settings" to contents.settings.toJson(),
            "health" to contents.health,
            "history" to contents.history,
            "artist_overrides" to contents.artistOverrides,
            "station_overrides" to contents.stationOverrides,
            "artwork" to collectArtwork(contents.stations),
        ),
    )

    fun writeTo(file: File, contents: Contents) = writeJsonAtomic(file, export(contents))

    /** {station id: {"name", "data"}} for every station whose picture file exists. */
    fun collectArtwork(stations: List<Station>): JsonObject {
        val art = LinkedHashMap<String, JsonElement>()
        for (s in stations) {
            val path = s.customArtPath
            if (path.isEmpty()) continue
            val file = File(path)
            if (!file.isFile) continue
            val data = try { Base64.getEncoder().encodeToString(file.readBytes()) } catch (e: Exception) { continue }
            art[s.id] = JsonObject(linkedMapOf(
                "name" to JsonPrimitive(baseName(path)),
                "data" to JsonPrimitive(data),
            ))
        }
        return JsonObject(art)
    }

    /** A path's last part, whichever OS wrote it. */
    fun baseName(path: String): String = path.split('/', '\\').last()

    fun read(text: String, artDir: File, now: Double = System.currentTimeMillis() / 1000.0,
             newId: () -> String = { java.util.UUID.randomUUID().toString() }): Contents {
        val data = try {
            FreqJson.parseToJsonElement(text)
        } catch (e: Exception) {
            throw BackupError("That isn't a valid backup file.")
        }
        if (data !is JsonObject) throw BackupError("That file isn't a Frequencies backup.")
        val format = data["format"].str()
        if (format != FORMAT && format !in LEGACY_FORMATS) throw BackupError("That file isn't a Frequencies backup.")
        val raw = data["stations"] as? JsonArray ?: throw BackupError("The backup has no station list in it.")

        val stations = raw.mapNotNull { it as? JsonObject }.filter { truthy(it["url"]) }
            .map { Station.normalize(Station(it), now, newId) }
        restoreArtwork(data["artwork"] as? JsonObject, stations, artDir)

        return Contents(
            stations = stations,
            settings = Settings.from(data["settings"] as? JsonObject),
            health = cleanHealth(data["health"]),
            history = data["history"] as? JsonArray ?: JsonArray(emptyList()),
            artistOverrides = data["artist_overrides"] as? JsonObject ?: JsonObject(emptyMap()),
            stationOverrides = data["station_overrides"] as? JsonObject ?: JsonObject(emptyMap()),
        )
    }

    /**
     * storage.restore_artwork: write each embedded picture into [artDir]
     * and point its station at it. A station whose picture isn't in the
     * backup keeps the path it had.
     */
    fun restoreArtwork(artwork: JsonObject?, stations: List<Station>, artDir: File) {
        if (artwork == null || artwork.isEmpty()) return
        val byId = stations.associateBy { it.id }
        for ((id, entry) in artwork) {
            val station = byId[id] ?: continue
            val obj = entry as? JsonObject ?: continue
            val blob = obj["data"].str() ?: continue
            val name = baseName(obj["name"].str()?.takeIf { it.isNotEmpty() } ?: "$id.png")
            val file = Featured.writeArt(artDir, id, name, blob) ?: continue
            station["custom_art_path"] = JsonPrimitive(file.absolutePath)
        }
    }

    private fun blankHealth() = linkedMapOf<String, JsonElement>(
        "last_ok" to JsonNull,
        "last_check" to JsonNull,
        "fail_count" to JsonPrimitive(0),
        "down" to JsonPrimitive(false),
        "last_error" to JsonPrimitive(""),
    )

    fun cleanHealth(value: JsonElement?): JsonObject {
        val obj = value as? JsonObject ?: return JsonObject(emptyMap())
        return JsonObject(obj.filterValues { it is JsonObject }.mapValues { (_, v) ->
            JsonObject(blankHealth().apply { putAll(v as JsonObject) })
        })
    }
}
