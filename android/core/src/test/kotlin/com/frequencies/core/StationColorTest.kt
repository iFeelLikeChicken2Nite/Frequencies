package com.frequencies.core

import org.junit.Test
import org.junit.Assert.assertEquals
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

class StationColorTest {
    // Expected values from the desktop's station_hue and QColor.setHsl(h, 130, 150).
    @Test
    fun nameGivesTheDesktopsColor() {
        assertEquals(210, StationColor.hue("Nightwave Plaza"))
        assertEquals(96, StationColor.hue("SomaFM Groove Salad"))
        assertEquals(0xFF6096CC.toInt(), StationColor.fromName("Nightwave Plaza"))
        assertEquals(0xFF8BCC60.toInt(), StationColor.fromName("SomaFM Groove Salad"))
        assertEquals(0xFFCC6096.toInt(), StationColor.fromName("?"))
    }

    @Test
    fun customNameFirstThenNameThenQuestionMark() {
        fun st(vararg kv: Pair<String, String>) = Station(JsonObject(kv.associate { it.first to JsonPrimitive(it.second) }))
        assertEquals("Mine", StationColor.nameOf(st("name" to "Theirs", "custom_name" to "Mine")))
        assertEquals("Theirs", StationColor.nameOf(st("name" to "Theirs", "custom_name" to "")))
        assertEquals("?", StationColor.nameOf(st("url" to "https://x.example/stream")))
    }
}
