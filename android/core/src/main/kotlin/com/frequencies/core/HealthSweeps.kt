package com.frequencies.core

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.longOrNull

/**
 * When the phone sweeps every saved stream (Android build 203, task 246).
 * The desktop sweeps 8 s after each launch. The phone's app often stays
 * alive for days (playback, Android Auto, the Pebble), so it also sweeps
 * when it comes back on screen [gapMs] or more after the last sweep, and a
 * sweep that found the phone offline is owed: it runs when the internet is
 * back, on the next return to the screen, or once a few minutes later.
 * Times are the caller's clock in ms.
 */
class SweepClock(var gapMs: Long = GAP_MS) {
    /** The end of the last sweep that ran (not skipped or cut short as offline). */
    var lastAt: Long? = null
        private set
    /** The last sweep was skipped or cut short as offline. */
    var owed = false
        private set
    /** A sweep is waiting to start or running. */
    var busy = false
        private set

    sealed interface Next {
        /** Nothing to do and nothing to log (one is already on its way). */
        object Nothing : Next
        data class Sweep(val why: String) : Next
        data class Skip(val reason: String) : Next
    }

    /** Takes the one sweep slot; false if a sweep is already waiting or running. */
    @Synchronized
    fun claim(): Boolean {
        if (busy) return false
        busy = true
        return true
    }

    /** Back on screen. */
    @Synchronized
    fun foreground(now: Long): Next {
        if (busy) return Next.Nothing
        val last = lastAt
        val why = when {
            owed -> "back on screen, retrying"
            last == null -> "back on screen"
            now - last >= gapMs -> "back on screen"
            else -> return Next.Skip(tooSoon(now - last))
        }
        busy = true
        return Next.Sweep(why)
    }

    /** The internet came back: only an owed sweep runs. */
    @Synchronized
    fun online(): Next {
        if (!owed || busy) return Next.Nothing
        busy = true
        return Next.Sweep("internet back, retrying")
    }

    /** The timed retry after a sweep was cut short as offline. */
    @Synchronized
    fun retry(): Next {
        if (!owed || busy) return Next.Nothing
        busy = true
        return Next.Sweep("retrying")
    }

    /** A claimed sweep ended: [ran] false = skipped or cut short as offline (owed). */
    @Synchronized
    fun done(now: Long, ran: Boolean) {
        busy = false
        if (ran) {
            owed = false
            lastAt = now
        } else {
            owed = true
        }
    }

    private fun tooSoon(agoMs: Long): String {
        val ago = (agoMs / 60_000).coerceAtLeast(0)
        val gap = gapMs / 60_000
        return "the last sweep was ${if (ago < 1) "less than a minute" else if (ago == 1L) "1 minute" else "$ago minutes"} ago (at most one every ${if (gap == 1L) "minute" else "$gap minutes"})"
    }

    companion object {
        const val GAP_MS = 30 * 60_000L
        const val RETRY_MS = 5 * 60_000L
    }
}

/**
 * Settings > Station health checks (healthlog.HealthLogWindow, plus the
 * sweeps themselves): newest first, at most [max] lines, kept in a file of
 * its own so it lasts across launches. A sweep line has [sweep] set; a
 * station line has [station].
 */
object HealthLog {
    data class Entry(
        val at: Long,
        val station: String = "",
        val ok: Boolean = true,
        val reason: String = "",
        /** What started the sweep ("launch", "back on screen", "daily"…); null on a station line. */
        val sweep: String? = null,
        /** started / finished / skipped / cut short. */
        val what: String = "",
    ) {
        val isSweep get() = sweep != null
    }

    const val MAX = 500
    private const val TOO_SOON = "Skipped: the last sweep was"

    // The desktop's wording (build 202, Batch CU), plus the phone's skipped count and reasons.
    private const val AGAIN = "Trying again when the internet is back (or in 5 minutes)."

    fun started(at: Long, why: String, toCheck: Int, skipped: Int) =
        Entry(at, sweep = why, what = "started", reason = "Started: $toCheck saved stream(s) to check, $skipped skipped")

    fun finished(at: Long, why: String, checked: Int, ok: Int, skipped: Int) =
        Entry(at, sweep = why, what = "finished", reason = "Finished: $checked checked, $ok answered, ${checked - ok} no answer, $skipped skipped")

    fun nothingToCheck(at: Long, why: String) = Entry(at, sweep = why, what = "skipped", reason = "Skipped: no saved internet streams to check")

    fun offline(at: Long, why: String) = Entry(at, sweep = why, what = "skipped", reason = "Skipped: the phone is offline. $AGAIN")

    fun looksOffline(at: Long, why: String, failed: Int) =
        Entry(at, sweep = why, what = "cut short", ok = false, reason = "Cut short: the first $failed streams all failed, looks offline, nothing marked. $AGAIN")

    fun noneAnswered(at: Long, why: String, checked: Int) =
        Entry(at, sweep = why, what = "cut short", ok = false, reason = "Cut short: none of the $checked streams answered, nothing marked. $AGAIN")

    fun skipped(at: Long, why: String, reason: String) = Entry(at, sweep = why, what = "skipped", reason = "Skipped: $reason")

    fun cutShort(at: Long, why: String, reason: String) = Entry(at, sweep = why, what = "cut short", ok = false, reason = "Cut short: $reason")

    /**
     * [entry] on top. A "too soon" skip right after another one replaces it,
     * so switching apps back and forth leaves one line, not dozens.
     */
    fun add(log: List<Entry>, entry: Entry, max: Int = MAX): List<Entry> {
        val top = log.firstOrNull()
        val rest = if (top != null && entry.what == "skipped" && top.what == "skipped" && top.sweep == entry.sweep && entry.reason.startsWith(TOO_SOON) && top.reason.startsWith(TOO_SOON)) log.drop(1) else log
        val all = listOf(entry) + rest
        if (all.size <= max) return all
        // Full: the oldest station lines go first, so the sweep lines (did it run?) stay longest.
        val out = ArrayList(all)
        var over = out.size - max
        var i = out.size - 1
        while (over > 0 && i >= 0) {
            if (!out[i].isSweep) { out.removeAt(i); over-- }
            i--
        }
        return out.take(max)
    }

    fun toJson(log: List<Entry>): JsonArray = JsonArray(log.map { e ->
        val m = LinkedHashMap<String, kotlinx.serialization.json.JsonElement>()
        m["at"] = JsonPrimitive(e.at)
        if (e.station.isNotEmpty()) m["station"] = JsonPrimitive(e.station)
        m["ok"] = JsonPrimitive(e.ok)
        if (e.reason.isNotEmpty()) m["reason"] = JsonPrimitive(e.reason)
        e.sweep?.let { m["sweep"] = JsonPrimitive(it) }
        if (e.what.isNotEmpty()) m["what"] = JsonPrimitive(e.what)
        JsonObject(m)
    })

    fun fromJson(json: kotlinx.serialization.json.JsonElement?): List<Entry> =
        (json as? JsonArray)?.mapNotNull { el ->
            val o = el as? JsonObject ?: return@mapNotNull null
            val at = (o["at"] as? JsonPrimitive)?.longOrNull ?: return@mapNotNull null
            Entry(
                at = at,
                station = o["station"].str().orEmpty(),
                ok = (o["ok"] as? JsonPrimitive)?.booleanOrNull ?: true,
                reason = o["reason"].str().orEmpty(),
                sweep = o["sweep"].str(),
                what = o["what"].str().orEmpty(),
            )
        }?.take(MAX).orEmpty()
}
