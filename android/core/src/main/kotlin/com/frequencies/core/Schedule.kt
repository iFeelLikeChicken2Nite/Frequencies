package com.frequencies.core

import java.time.LocalDateTime
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * The desktop's schedule (schedule.py) and clock (clock.py). Shows live on
 * the station under "shows", so they travel with backups and shares:
 *
 *     {"name": str, "days": [0-6], "start": "HH:MM", "end": "HH:MM", "notify": bool}
 *
 * Days are Monday = 0. Times are the phone's own local time, like the
 * desktop's (no time zone is stored), and always saved as 24-hour "HH:MM";
 * the 12-hour setting only changes how they read. A show never starts by
 * itself: the schedule only tells.
 */
object Schedule {
    val DAY_NAMES = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    val DAY_NAMES_LONG = listOf("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    const val DAY_MINUTES = 24 * 60

    /** One show of one station. [show] is the stored object, unknown keys and all. */
    data class Live(val station: Station, val show: JsonObject) {
        val name: String get() = show["name"].str().orEmpty().trim()
        /** "<frequency> - <show>", as the desktop's jump pills say it. */
        val label: String get() = if (name.isEmpty()) station.displayName else "${station.displayName} - $name"
    }

    fun shows(station: Station): List<JsonObject> = (station["shows"] as? JsonArray)?.mapNotNull { it as? JsonObject } ?: emptyList()

    /** "HH:MM" to minutes past midnight, or null (schedule.parse_time). */
    fun parseTime(text: String?): Int? {
        val parts = (text ?: return null).split(":")
        if (parts.size != 2) return null
        val h = parts[0].trim().toIntOrNull() ?: return null
        val m = parts[1].trim().toIntOrNull() ?: return null
        return h * 60 + m
    }

    /** (start, end) in minutes; an end before the start runs past midnight; no end is an hour. */
    fun bounds(show: JsonObject): Pair<Int, Int> {
        val start = parseTime(show["start"].str()) ?: 0
        var end = parseTime(show["end"].str()) ?: (start + 60)
        if (end <= start) end += DAY_MINUTES
        return start to end
    }

    /** The show's days (Monday = 0); a day saved as text doesn't count, as in Python. */
    fun days(show: JsonObject): Set<Int> = (show["days"] as? JsonArray)?.mapNotNull { e ->
        val p = e as? JsonPrimitive ?: return@mapNotNull null
        if (p.isString) return@mapNotNull null
        val n = p.num() ?: return@mapNotNull null
        if (n == Math.floor(n)) n.toInt() else null
    }?.toSet() ?: emptySet()

    fun notifies(show: JsonObject): Boolean = show["notify"]?.let { truthy(it) } ?: true

    private fun weekday(t: LocalDateTime) = t.dayOfWeek.value - 1
    private fun minuteOf(t: LocalDateTime) = t.hour * 60 + t.minute

    /** schedule.playing_now: the show on this station at [now], or null. */
    fun playingNow(station: Station, now: LocalDateTime): JsonObject? {
        val minute = minuteOf(now)
        val today = weekday(now)
        for (show in shows(station)) {
            val d = days(show)
            if (today !in d) continue
            val (start, end) = bounds(show)
            if (minute in start until end) return show
            // A show that began yesterday and is still running.
            if (end > DAY_MINUTES && minute < end - DAY_MINUTES && Math.floorMod(today - 1, 7) in d) return show
        }
        return null
    }

    /** schedule.live_now: every show on the air at [now], in the stations' order. */
    fun liveNow(stations: List<Station>, now: LocalDateTime): List<Live> =
        stations.mapNotNull { s -> playingNow(s, now)?.let { Live(s, it) } }

    /**
     * The phone's jump pills (mainwindow._place_live): the live frequencies
     * that are out of sight, at most two named, then "More live shows".
     */
    data class Pills(val named: List<Live>, val more: Boolean)

    fun pills(live: List<Live>, outOfSight: (String) -> Boolean): Pills {
        val hidden = live.filter { outOfSight(it.station.id) }
        return Pills(hidden.take(2), hidden.size > 2)
    }

    /**
     * schedule.Reminders: a show is announced at its start (a minute
     * either side, since the check runs once a minute) and only once; never
     * for a start before the app was opened (the first check is silent).
     */
    class Reminders {
        private val announced = HashSet<String>()
        private var primed = false

        /** The shows starting now that should be announced, if reminders are on. */
        fun check(stations: List<Station>, settings: Settings, now: LocalDateTime): List<Live> {
            if (!settings.bool("show_reminders", true)) return emptyList()
            val announce = primed
            primed = true
            val minute = minuteOf(now)
            val today = weekday(now)
            val out = ArrayList<Live>()
            for (s in stations) for (show in shows(s)) {
                if (!notifies(show)) continue
                if (today !in days(show)) continue
                val (start, _) = bounds(show)
                if (kotlin.math.abs(minute - start) > 1) continue
                val key = "${s.id}\u0000${show["name"].str()}\u0000$start\u0000$today"
                if (!announced.add(key)) continue
                if (announce) out += Live(s, show)
            }
            return out
        }
    }

    /** What the reminder says (mainwindow._show_starting). */
    fun startingText(live: Live): String = "${live.show["name"].str() ?: "A show"} is starting on ${live.station.displayName}"

    // -- the phone's Schedule screen ----------------------------------------------

    /** One show on one day, for the list. [live] is on the air at the moment asked about. */
    data class Slot(val station: Station, val show: JsonObject, val start: Int, val end: Int, val live: Boolean, val fromYesterday: Boolean)

    /**
     * The shows on [day] (Monday = 0), earliest first, for the Schedule
     * screen. On today, a show from yesterday that is still running is
     * listed first; [now] marks what is live.
     */
    fun day(stations: List<Station>, day: Int, now: LocalDateTime): List<Slot> {
        val today = weekday(now)
        val minute = minuteOf(now)
        val out = ArrayList<Slot>()
        for (s in stations) for (show in shows(s)) {
            val d = days(show)
            val (start, end) = bounds(show)
            if (day in d) {
                val live = day == today && minute in start until end
                out += Slot(s, show, start, end, live, fromYesterday = false)
            }
            if (day == today && end > DAY_MINUTES && Math.floorMod(today - 1, 7) in d && minute < end - DAY_MINUTES) {
                out += Slot(s, show, start, end, live = true, fromYesterday = true)
            }
        }
        return out.sortedWith(compareBy<Slot>({ !it.fromYesterday }, { it.start }, { it.station.displayName.lowercase() }))
    }

    /** A new or edited show, in the desktop's shape. */
    fun showJson(name: String, startMinutes: Int, endMinutes: Int, days: Collection<Int>, notify: Boolean, keep: JsonObject? = null): JsonObject {
        val out = LinkedHashMap<String, JsonElement>()
        keep?.let { out.putAll(it) }
        out["name"] = JsonPrimitive(name.trim().ifEmpty { "Untitled" })
        out["start"] = JsonPrimitive(hhmm(startMinutes))
        out["end"] = JsonPrimitive(hhmm(endMinutes))
        out["days"] = JsonArray(days.filter { it in 0..6 }.distinct().sorted().map { JsonPrimitive(it) })
        out["notify"] = JsonPrimitive(notify)
        return JsonObject(out)
    }

    fun hhmm(minutes: Int): String {
        val m = Math.floorMod(minutes, DAY_MINUTES)
        return "%02d:%02d".format(m / 60, m % 60)
    }

    /** The station's shows with [old] replaced by [new] (or [new] added; null [new] deletes [old]). */
    fun withShow(station: Station, old: JsonObject?, new: JsonObject?): JsonArray {
        val list = shows(station).toMutableList()
        val at = old?.let { list.indexOf(it) } ?: -1
        when {
            new == null -> if (at >= 0) list.removeAt(at)
            at >= 0 -> list[at] = new
            else -> list += new
        }
        return JsonArray(list)
    }

    // -- clock.py -------------------------------------------------------------------

    /** clock.fmt: "9:15 PM" in 12-hour mode, "21:15" otherwise. */
    fun fmt(minutes: Int, twelveHour: Boolean): String {
        val m = Math.floorMod(minutes, DAY_MINUTES)
        val h = m / 60
        val mm = m % 60
        if (!twelveHour) return "%02d:%02d".format(h, mm)
        val h12 = if (h % 12 == 0) 12 else h % 12
        return "%d:%02d %s".format(h12, mm, if (h < 12) "AM" else "PM")
    }

    /** schedule._shown_time: a stored "HH:mm" in the chosen style (the text itself when it isn't a time). */
    fun shownTime(text: String?, twelveHour: Boolean): String {
        val m = parseTime(text) ?: return text.orEmpty()
        return fmt(Math.floorMod(m / 60, 24) * 60 + (m % 60), twelveHour)
    }

    /** clock.fmt_hour: the schedule ruler's "9 AM" or "09:00". */
    fun fmtHour(hour: Int, twelveHour: Boolean): String {
        val h = Math.floorMod(hour, 24)
        if (!twelveHour) return "%02d:00".format(h)
        return "${if (h % 12 == 0) 12 else h % 12} ${if (h < 12) "AM" else "PM"}"
    }
}
