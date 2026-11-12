package com.frequencies.core

/**
 * Sync runs (notes/sync-protocol.md section 5.1, the desktop's
 * sync.SyncService.start_run / _check_run / _end_run, build 167): when
 * this phone looks for its paired computers and syncs with them, how long
 * a run waits, what the spinner shows, and the problems said in plain
 * words. The network is the app's (SyncKeeper); this is the bookkeeping,
 * so the tests can drive it with a clock of their own.
 */
object SyncRuns {
    /** This device's own setting, never sent (section 6.2). */
    const val INTERVAL_KEY = "sync_interval_minutes"
    const val INTERVAL_DEFAULT = 30
    val INTERVAL_CHOICES = listOf(5, 15, 30, 60, 120, 0)

    /** syncpage.INTERVAL_LABELS, in the same order. */
    val INTERVAL_LABELS: Map<Int, String> = linkedMapOf(
        5 to "Every 5 minutes", 15 to "Every 15 minutes", 30 to "Every 30 minutes",
        60 to "Every hour", 120 to "Every 2 hours", 0 to "Off (only when I press Sync now)",
    )

    /** sync.RUN_LOOK_FOR: how long a run looks for a device. */
    const val LOOK_FOR_MS = 25_000L
    /** sync.RUN_ART_FOR: how long it waits for pictures it asked for. */
    const val ART_FOR_MS = 90_000L
    /** sync.RUN_SHOW_AT_LEAST: the spinner never just flickers. */
    const val SHOW_AT_LEAST_MS = 800L
    /** A state that changed something between runs turns the spinner this long. */
    const val PULSE_MS = 1_000L
    /** sync.RUN_RETRY: between tries of one device in a run. */
    const val RETRY_MS = 3_000L

    /** Minutes between runs; 0 = only Sync now. An unknown value counts as 30. */
    fun interval(settings: Settings?): Int {
        val raw = settings?.data?.get(INTERVAL_KEY).num() ?: return INTERVAL_DEFAULT
        val minutes = raw.toInt()
        return if (raw == minutes.toDouble() && minutes in INTERVAL_CHOICES) minutes else INTERVAL_DEFAULT
    }

    fun label(minutes: Int): String = INTERVAL_LABELS[minutes] ?: INTERVAL_LABELS.getValue(INTERVAL_DEFAULT)

    /**
     * Phones (section 8): coming to the foreground starts a run at most
     * once per interval; never when the interval is off. [lastRunAt] 0 =
     * no run since the app started (the launch run).
     */
    fun dueOnForeground(minutes: Int, lastRunAt: Long, now: Long): Boolean =
        minutes != 0 && (lastRunAt <= 0L || now - lastRunAt >= minutes * 60_000L)

    /** sync._names: "A", "A and B", "A, B and C". */
    fun names(names: List<String>, word: String = "and"): String = when (names.size) {
        0 -> ""
        1 -> names[0]
        else -> names.dropLast(1).joinToString(", ") + " $word " + names.last()
    }

    /** Sync now found nobody (task 245, build 198): the short note said instead of a spinner. */
    fun notRunning(names: List<String>): String =
        "${names(names)} ${if (names.size == 1) "isn't" else "aren't"} running Frequencies right now."

    fun notFound(names: List<String>): String =
        "Couldn't find ${names(names, "or")} on this network. Check that it's on the same network with Frequencies open."

    fun pairedAgain(name: String): String =
        "${name.ifEmpty { "The other device" }} was paired again with something else. Forget it here and pair again."

    /** The spinner's words. */
    fun busyText(names: List<String>): String = if (names.isEmpty()) "Syncing…" else "Syncing with ${names(names)}…"
}

/**
 * One sync run: which paired devices it is for, which have answered, and
 * when it gives up on the rest. Times are milliseconds on one clock (the
 * app's elapsedRealtime).
 */
class SyncRun(why: String, now: Long) {
    var why: String = why
        private set
    var manual = false
        private set
    val started = now
    val peers = LinkedHashSet<String>()
    val done = LinkedHashSet<String>()
    /** Device id -> a problem to say when the run ends. */
    val errors = LinkedHashMap<String, String>()
    var until = now
        private set
    var artUntil = now
        private set
    val showUntil = now + SyncRuns.SHOW_AT_LEAST_MS

    /** Start (or widen) the run for [wanted]; a Sync now makes the whole run a manual one. */
    fun add(wanted: Collection<String>, manual: Boolean, now: Long, why: String = this.why) {
        if (manual && !this.manual) this.why = why
        this.manual = this.manual || manual
        peers.addAll(wanted)
        until = maxOf(until, now + SyncRuns.LOOK_FOR_MS)
        artUntil = maxOf(artUntil, now + SyncRuns.ART_FOR_MS)
        for (pid in wanted) {
            done.remove(pid)
            errors.remove(pid)
        }
    }

    /** A device that already had a session open: everything said once more counts as done. */
    fun reached(pid: String) {
        done.add(pid)
    }

    /** The other device was paired again with something else: done, with that to say. */
    fun failed(pid: String, problem: String) {
        errors[pid] = problem
        done.add(pid)
    }

    /** What [check] decided. */
    sealed interface Verdict {
        /** Still going. */
        data object Running : Verdict
        /** Over; [missing] are the devices it never reached. */
        data class Over(val missing: List<String>) : Verdict
    }

    /**
     * sync._check_run. [paired]: the device ids still paired.
     * [stateAt]: when the device's state last came in a session (null =
     * not yet). [artWaiting]: pictures asked for that haven't come.
     * [connected]: whether a device has a session open.
     */
    fun check(now: Long, paired: Set<String>, stateAt: (String) -> Long?, artWaiting: Boolean, connected: (String) -> Boolean): Verdict {
        peers.retainAll(paired)
        for (pid in peers) {
            val at = stateAt(pid) ?: continue
            if (at >= started) done.add(pid)
        }
        val waiting = peers - done
        val art = artWaiting && now < artUntil && done.any(connected)
        return when {
            waiting.isEmpty() && !art && now >= showUntil -> Verdict.Over(emptyList())
            waiting.isNotEmpty() && now >= until -> Verdict.Over(waiting.toList())
            else -> Verdict.Running
        }
    }

    /**
     * sync._end_run: the problem line to show once the run is over, or
     * null to leave the old one. An automatic run doesn't complain about a
     * device that simply isn't on; a run that reached everyone clears an
     * old problem. [name]: a device id's name.
     */
    fun problem(missing: List<String>, name: (String) -> String?): String? {
        val names = missing.mapNotNull(name)
        val said = errors.values.toMutableList()
        if (names.isNotEmpty() && manual) said.add(SyncRuns.notRunning(names))
        return if (manual || said.isNotEmpty() || names.isEmpty()) said.joinToString(" ") else null
    }
}
