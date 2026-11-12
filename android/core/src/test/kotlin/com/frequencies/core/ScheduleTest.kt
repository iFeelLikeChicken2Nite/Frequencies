package com.frequencies.core

import java.time.LocalDateTime
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Schedule against the desktop's own schedule.py and clock.py
 * (answers from tools/make_schedule_fixtures.py on desktop build 144).
 */
class ScheduleTest {
    private val answers: JsonObject = FreqJson.parseToJsonElement(
        javaClass.getResourceAsStream("/schedule/answers.json")!!.use { it.readBytes().toString(Charsets.UTF_8) },
    ).jsonObject

    private val stations: List<Station> = answers["stations"]!!.jsonArray.map { Station(LinkedHashMap(it.jsonObject)) }

    private fun time(text: String) = LocalDateTime.parse(text)

    @Test
    fun playingNowMatchesTheDesktopAllWeek() {
        val rows = answers["playing"]!!.jsonArray
        assertTrue(rows.size > 1000)
        for (row in rows.map { it.jsonObject }) {
            val t = time(row["t"].str()!!)
            for (s in stations) {
                val want = row[s.id]?.takeIf { it !is JsonNull }?.str()
                val got = Schedule.playingNow(s, t)?.let { it["name"].str() ?: "" }
                assertEquals("${s.id} at $t", want, got)
            }
            val live = (row["live"] as JsonArray).map { it.str() }
            assertEquals("live at $t", live, Schedule.liveNow(stations, t).map { it.station.id })
        }
    }

    @Test
    fun remindersMatchTheDesktopMinuteByMinute() {
        val r = answers["reminders"]!!.jsonObject
        val want = r["announced"]!!.jsonArray.map { it.jsonObject.let { a -> Triple(a["t"].str(), a["station"].str(), a["show"].str()) } }
        val reminders = Schedule.Reminders()
        val start = time(r["start"].str()!!)
        val settings = Settings.from(null)
        assertTrue("the first check is silent", reminders.check(stations, settings, start).isEmpty())
        val got = ArrayList<Triple<String?, String?, String?>>()
        for (i in 1 until 3 * 24 * 60) {
            val t = start.plusMinutes(i.toLong())
            for (live in reminders.check(stations, settings, t)) got += Triple(t.toString(), live.station.id, live.show["name"].str())
        }
        assertEquals(want, got)
        assertEquals(8, got.size)
    }

    @Test
    fun remindersCanBeSwitchedOff() {
        val off = Settings.from(null).also { it["show_reminders"] = JsonPrimitive(false) }
        val reminders = Schedule.Reminders()
        val t = LocalDateTime.of(2026, 11, 2, 5, 58)
        reminders.check(stations, off, t)
        assertTrue(reminders.check(stations, off, t.plusMinutes(2)).isEmpty())
    }

    @Test
    fun clockMatchesTheDesktop() {
        for ((mode, twelve) in listOf("12h" to true, "24h" to false)) {
            val c = answers["clock"]!!.jsonObject[mode]!!.jsonObject
            for ((hhmm, want) in c["fmt"]!!.jsonObject) {
                assertEquals("$mode $hhmm", want.str(), Schedule.fmt(Schedule.parseTime(hhmm)!!, twelve))
            }
            for ((hour, want) in c["hour"]!!.jsonObject) assertEquals("$mode $hour", want.str(), Schedule.fmtHour(hour.toInt(), twelve))
            for ((text, want) in c["shown"]!!.jsonObject) assertEquals("$mode '$text'", want.str(), Schedule.shownTime(text, twelve))
        }
    }

    @Test
    fun dayListsShowsInOrderWithTheLiveOnesMarked() {
        // Saturday 01:30: Friday's Night Owls is still on (from yesterday); Saturday's runs from 22:00.
        val sat = LocalDateTime.of(2026, 11, 7, 1, 30)
        val slots = Schedule.day(stations, 5, sat)
        assertEquals("Night Owls", slots.first().show["name"].str())
        assertTrue(slots.first().fromYesterday && slots.first().live)
        assertEquals(listOf("Early", "", "Night Owls"), slots.drop(1).map { it.show["name"].str() ?: "" })
        assertTrue(slots.drop(1).none { it.live })
        // Another day: nothing live, nothing from yesterday.
        val thu = Schedule.day(stations, 3, sat)
        assertTrue(thu.none { it.live || it.fromYesterday })
        assertEquals(listOf("Early", "Morning Drive", "Allday"), thu.map { it.show["name"].str() })
    }

    @Test
    fun pillsNameTwoThenMore() {
        val t = LocalDateTime.of(2026, 11, 5, 5, 30)   // Thursday: Early on twoshows only
        val live = Schedule.liveNow(stations, t)
        assertEquals(listOf("Twoshows - Early"), live.map { it.label })
        val many = listOf("a", "b", "c").map { id ->
            Schedule.Live(Station(LinkedHashMap(mapOf("id" to JsonPrimitive(id), "name" to JsonPrimitive(id.uppercase())))), JsonObject(mapOf("name" to JsonPrimitive("Show $id"))))
        }
        val p = Schedule.pills(many) { it != "b" }
        assertEquals(listOf("A - Show a", "C - Show c"), p.named.map { it.label })
        assertFalse(p.more)
        assertTrue(Schedule.pills(many) { true }.more)
        assertTrue(Schedule.pills(many) { false }.named.isEmpty())
    }

    @Test
    fun editingKeepsTheDesktopShape() {
        val s = stations.first { it.id == "morning" }
        val old = Schedule.shows(s).first()
        val new = Schedule.showJson("  ", 21 * 60 + 5, 30, listOf(6, 0, 0, 9), notify = false, keep = old)
        assertEquals("Untitled", new["name"].str())
        assertEquals("21:05", new["start"].str())
        assertEquals("00:30", new["end"].str())
        assertEquals(listOf(0, 6), Schedule.days(new).sorted())
        assertEquals(new, Schedule.withShow(s, old, new).single())
        assertTrue(Schedule.withShow(s, old, null).isEmpty())
        assertEquals(2, Schedule.withShow(s, null, new).size)
        // A Station with no shows key at all.
        assertTrue(Schedule.shows(Station(LinkedHashMap())).isEmpty())
    }

    @Test
    fun liveNowTabFollowsManualOrder() {
        val t = LocalDateTime.of(2026, 11, 6, 23, 0)    // Friday: Night Owls
        assertEquals(listOf("late"), Browse.live(stations, t).map { it.id })
        assertEquals(listOf("late"), Browse.list(stations, Browse.LIVE, t)!!.map { it.id })
    }
}
