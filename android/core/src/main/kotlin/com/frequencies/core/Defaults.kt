package com.frequencies.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject

/**
 * The desktop's STATION_DEFAULTS and SETTINGS_DEFAULTS (storage.py),
 * exported as JSON from build 131 so the two never drift by hand.
 */
object Defaults {
    val station: JsonObject by lazy { resource("station_defaults.json").jsonObject }
    val settings: JsonObject by lazy { resource("settings_defaults.json").jsonObject }

    /** The desktop's shipped default frequencies (build 127), as featured entries. */
    fun defaultStationsText(): String = text("default_stations.json")

    private fun text(name: String): String {
        val stream = Defaults::class.java.getResourceAsStream(name)
            ?: error("missing resource $name")
        return stream.use { it.readBytes().toString(Charsets.UTF_8) }
    }

    private fun resource(name: String) = FreqJson.parseToJsonElement(text(name))
}
