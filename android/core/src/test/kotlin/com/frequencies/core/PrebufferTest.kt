package com.frequencies.core

import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Per-station pre-buffering with the desktop's keys and rules (bridge._buffer_for, Player.play's clamp). */
class PrebufferTest {
    private fun station(id: String, vararg values: Pair<String, Any>): Station {
        val s = Station(LinkedHashMap(Defaults.station))
        s["id"] = JsonPrimitive(id)
        for ((k, v) in values) s[k] = when (v) { is Boolean -> JsonPrimitive(v); is Number -> JsonPrimitive(v); else -> JsonPrimitive(v.toString()) }
        return s
    }

    @Test
    fun desktopRules() {
        // Off by default: a fresh station has the desktop's keys, switched off.
        assertEquals(false, truthy(Defaults.station["buffer_enabled"]))
        assertEquals(0.0, Prebuffer.seconds(station("a")), 0.0)
        assertEquals(0.0, Prebuffer.seconds(station("a", "buffer_seconds" to 8.0)), 0.0)
        assertEquals(0.0, Prebuffer.seconds(null), 0.0)
        assertEquals(8.0, Prebuffer.seconds(station("a", "buffer_enabled" to true, "buffer_seconds" to 8.0)), 0.0)
        // "auto": the worst start measured; nothing measured yet means nothing to wait for.
        assertEquals(2.9, Prebuffer.seconds(station("a", "buffer_enabled" to true, "connect_worst" to 2.9)), 0.0)
        assertEquals(0.0, Prebuffer.seconds(station("a", "buffer_enabled" to true)), 0.0)
        // The player's 1–60 s clamp.
        assertEquals(1.0, Prebuffer.seconds(station("a", "buffer_enabled" to true, "buffer_seconds" to 0.3)), 0.0)
        assertEquals(60.0, Prebuffer.seconds(station("a", "buffer_enabled" to true, "connect_worst" to 200.0)), 0.0)
        val targets = Prebuffer.targets(listOf(
            station("on", "buffer_enabled" to true, "buffer_seconds" to 2.4),
            station("off", "buffer_seconds" to 5.0),
        ))
        assertEquals(mapOf("on" to 2400L), targets)
    }

    @Test
    fun worstStartIsLearned() {
        val s = station("a", "connect_worst" to 2.0)
        assertNull(Prebuffer.newWorst(s, 1.5))
        assertEquals(3.2, Prebuffer.newWorst(s, 3.24)!!, 0.0)
        assertEquals("The longest this station has taken to start is 2.0s.", Prebuffer.worstLine(s))
        assertEquals("Once this station has played, its slowest start will be shown here as a guide.", Prebuffer.worstLine(station("b")))
        // connect_worst is a measurement: it never travels in a share code or zip.
        val coded = ShareCode.entry(ShareCode.fromStation(station("c", "url" to "http://x/y", "connect_worst" to 4.0, "buffer_enabled" to true), thumbnailer = null).first)!!
        assertNull(coded["connect_worst"])
        assertEquals(true, truthy(coded["buffer_enabled"]))
    }
}
