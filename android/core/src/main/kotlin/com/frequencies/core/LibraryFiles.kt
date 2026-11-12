package com.frequencies.core

import java.io.File
import java.util.UUID
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * The phone's copy of the desktop's config folder: stations.json,
 * settings.json, health.json, history.json and artwork/, in the same
 * formats, so a full backup moves between them without translation.
 */
class LibraryFiles(
    val dir: File,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
    private val newId: () -> String = { UUID.randomUUID().toString() },
) {
    val stationsFile get() = File(dir, "stations.json")
    val settingsFile get() = File(dir, "settings.json")
    val healthFile get() = File(dir, "health.json")
    val historyFile get() = File(dir, "history.json")
    val artDir get() = File(dir, "artwork")

    /**
     * storage.load_stations: a fresh profile starts with the desktop's
     * default frequencies (full-size art, no tags or play counts) and
     * saves them at once.
     */
    fun loadStations(): List<Station> {
        if (!stationsFile.exists()) {
            val fresh = defaultStations()
            saveStations(fresh)
            return fresh
        }
        val list = readJson(stationsFile) as? JsonArray ?: return emptyList()
        return list.mapNotNull { it as? JsonObject }.map { Station.normalize(Station(it), clock(), newId) }
    }

    fun saveStations(stations: List<Station>) =
        writeJsonAtomic(stationsFile, JsonArray(stations.map { it.toJson() }))

    fun defaultStations(): List<Station> =
        Featured.readList(Defaults.defaultStationsText()).map { entry ->
            val s = Featured.toStation(entry, artDir, newId)
            s["added_at"] = floatJson(clock())
            Station.normalize(s, clock(), newId)
        }

    fun loadSettings(): Settings = Settings.from(readJson(settingsFile) as? JsonObject)

    fun saveSettings(settings: Settings) = writeJsonAtomic(settingsFile, settings.toJson())

    fun loadHealth(): JsonObject = Backup.cleanHealth(readJson(healthFile))
    fun loadHistory(): JsonArray = readJson(historyFile) as? JsonArray ?: JsonArray(emptyList())
    fun saveHealth(health: JsonObject) = writeJsonAtomic(healthFile, health)
    fun saveHistory(history: JsonArray) = writeJsonAtomic(historyFile, history)

    /** Optional files the desktop also keeps in its config folder. */
    fun loadObject(name: String): JsonObject = readJson(File(dir, name)) as? JsonObject ?: JsonObject(emptyMap())
    fun saveObject(name: String, value: JsonElement) = writeJsonAtomic(File(dir, name), value)
}
