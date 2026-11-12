package com.frequencies.app

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.frequencies.core.StationTls
import com.frequencies.core.Health
import com.frequencies.core.HealthLog
import com.frequencies.core.LatestSaver
import com.frequencies.core.SweepClock
import com.frequencies.core.readJson
import com.frequencies.core.writeJsonAtomic
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.updateAndGet
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Short messages from the playback service and background work to whatever screen is open (its snackbar). */
object Notices {
    val messages = MutableSharedFlow<String>(extraBufferCapacity = 8)
    fun say(text: String) { messages.tryEmit(text) }
}

/**
 * The desktop's health checks: a sweep of every saved stream a few
 * seconds after launch (workers.LaunchCheck, always on), a re-check of the
 * ones marked down every few minutes while the app runs
 * (workers.HealthMonitor, Settings' "Re-check frequencies that are down"),
 * and, the phone's own addition, the same sweep about once a day in the
 * background ([HealthWorker], network required, no battery exemption
 * asked for). Nothing here notifies: the desktop only shows the marks.
 */
class HealthChecks(private val context: Context, private val library: Library, private val scope: CoroutineScope) {
    /** Settings > Station health checks: station lines and sweep lines, newest first, kept across launches (build 203). */
    private val logFile = java.io.File(context.filesDir, "health_log.json")
    private val _log = MutableStateFlow(runCatching { HealthLog.fromJson(readJson(logFile)) }.getOrDefault(emptyList()))
    val log: StateFlow<List<HealthLog.Entry>> = _log.asStateFlow()
    private val logSaver = LatestSaver<List<HealthLog.Entry>>("health-log", onError = { Log.w(TAG, "[health] couldn't save the log: $it") }) {
        writeJsonAtomic(logFile, HealthLog.toJson(it))
    }
    fun clearLog() { _log.value = emptyList(); logSaver.save(emptyList()) }

    private fun add(entry: HealthLog.Entry) {
        val next = _log.updateAndGet { HealthLog.add(it, entry) }
        logSaver.save(next)
    }

    /** When sweeps run (core SweepClock): launch, back on screen after 30 min, and the retry after an offline start. */
    val clock = SweepClock()
    private val launched = AtomicBoolean(false)
    @Volatile private var backgrounded = true
    private val sweeping = Mutex()

    private fun note(id: String, ok: Boolean, reason: String) {
        val name = library.station(id)?.displayName?.ifEmpty { null } ?: "(unnamed)"
        add(HealthLog.Entry(System.currentTimeMillis(), name, ok, reason))
    }

    private fun record(id: String, ok: Boolean, reason: String) {
        note(id, ok, reason)
        if (ok) library.markOk(id) else library.markFailed(id, reason)
    }

    /** Every saved stream, once per launch of the app, after the first station has had time to start. */
    fun launchCheckOnce() {
        if (!launched.compareAndSet(false, true)) return
        val claimed = clock.claim()
        scope.launch(Dispatchers.IO) {
            delay(LAUNCH_DELAY_MS)
            if (claimed) runClaimed("launch") else sweep("launch")
        }
        scope.launch(Dispatchers.IO) { recheckLoop() }
        // An owed sweep (offline at launch, or cut short) runs as soon as the internet is back.
        scope.launch(Dispatchers.IO) {
            Connectivity.offline.drop(1).filter { !it }.collect { start(clock.online()) }
        }
    }

    /** MainActivity went off screen (not a rotation). */
    fun background() { backgrounded = true }

    /** MainActivity came on screen: sweeps if the last one was 30+ minutes ago or one is owed. */
    fun foreground() {
        if (!backgrounded) return
        backgrounded = false
        if (!launched.get()) return
        start(clock.foreground(System.currentTimeMillis()))
    }

    private fun start(next: SweepClock.Next) {
        when (next) {
            is SweepClock.Next.Sweep -> scope.launch(Dispatchers.IO) { runClaimed(next.why) }
            is SweepClock.Next.Skip -> {
                Log.i(TAG, "[health] back on screen: no sweep, ${next.reason}")
                add(HealthLog.skipped(System.currentTimeMillis(), "back on screen", next.reason))
            }
            SweepClock.Next.Nothing -> {}
        }
    }

    /** A sweep the clock already handed out; tells the clock how it went, and once retries an offline one 5 minutes later. */
    private suspend fun runClaimed(why: String) {
        var ran = false
        try { ran = sweepNow(why) } finally { clock.done(System.currentTimeMillis(), ran) }
        if (!ran && retryArmed.compareAndSet(false, true)) {
            scope.launch(Dispatchers.IO) {
                delay(SweepClock.RETRY_MS)
                start(clock.retry())
            }
        }
    }
    private val retryArmed = AtomicBoolean(false)

    /** The full sweep from outside the clock (the daily job). Returns false if it found the phone offline. */
    suspend fun sweep(why: String): Boolean {
        if (!clock.claim()) {
            add(HealthLog.skipped(System.currentTimeMillis(), why, "another sweep is running."))
            return true
        }
        var ran = false
        try { ran = sweepNow(why) } finally { clock.done(System.currentTimeMillis(), ran) }
        return ran
    }

    /** Logs the daily job's skip when the re-check switch is off. */
    fun skippedDisabled(why: String) =
        add(HealthLog.skipped(System.currentTimeMillis(), why, "Re-check frequencies that are down is off."))

    /** The sweep itself; false = skipped or cut short as offline (a retry is owed). */
    private suspend fun sweepNow(why: String): Boolean = sweeping.withLock {
        try {
            // Offline (task 214): background checks pause quietly instead of marking stations down.
            if (Connectivity.offline.value) {
                Log.i(TAG, "[health] $why check: skipped, the phone is offline")
                add(HealthLog.offline(System.currentTimeMillis(), why))
                return@withLock false
            }
            val stations = library.stations.value
            val plan = Health.launchTargets(stations, library.health.value)
            // An iBroadcast or YouTube "down" an older check left behind (desktop build 131).
            plan.cleared.forEach { library.markOk(it) }
            val skipped = stations.size - plan.targets.size
            if (plan.targets.isEmpty()) {
                Log.i(TAG, "[health] $why check: no saved streams to check")
                add(HealthLog.nothingToCheck(System.currentTimeMillis(), why))
                return@withLock true
            }
            add(HealthLog.started(System.currentTimeMillis(), why, plan.targets.size, skipped))
            // Broken-certificate stations (their `insecure` flag) are probed without the check; no others.
            val flagged = Health.insecureUrls(stations)
            val summary = Health.sweep(
                plan.targets,
                probe = { url -> Health.probe(url, insecure = StationTls.EVERY_STATION || url in flagged, log = { Log.i(TAG, it) }) },
                log = { Log.i(TAG, it.replace("launch check", "$why check")) },
                onResult = { id, ok, reason -> record(id, ok, reason) },
            )
            val now = System.currentTimeMillis()
            when {
                summary.offline -> {
                    add(HealthLog.looksOffline(now, why, summary.checked))
                    false
                }
                summary.stopped -> {
                    add(HealthLog.cutShort(now, why, "stopped after ${summary.checked} checked."))
                    true
                }
                summary.answered == 0 -> {
                    add(HealthLog.noneAnswered(now, why, summary.checked))
                    false
                }
                else -> {
                    add(HealthLog.finished(now, why, summary.checked, summary.answered, skipped))
                    true
                }
            }
        } catch (e: Exception) {
            if (e is kotlinx.coroutines.CancellationException) throw e
            Log.w(TAG, "[health] $why check failed: $e")
            add(HealthLog.cutShort(System.currentTimeMillis(), why, "something went wrong (${e::class.simpleName}). Not trying again until the next launch."))
            true
        }
    }

    /** workers.HealthMonitor: only stations already marked down, every health_recheck_minutes. */
    private suspend fun recheckLoop() {
        var waited = 0L
        while (scope.isActive) {
            delay(60_000)
            waited += 1
            val settings = library.settings.value
            if (!settings.bool("health_recheck", true)) { waited = 0; continue }
            val every = settings.double("health_recheck_minutes", 30.0).toLong().coerceIn(5, 180)
            if (waited < every || Connectivity.offline.value) continue
            waited = 0
            val flagged = Health.insecureUrls(library.stations.value)
            for ((id, url) in Health.downTargets(library.stations.value, library.health.value)) {
                if (!scope.isActive) return
                val (ok, reason) = Health.probe(url, insecure = StationTls.EVERY_STATION || url in flagged, log = { Log.i(TAG, it) })
                Log.i(TAG, "[health] re-check '${library.station(id)?.displayName}': ${if (ok) "answered" else "no answer ($reason)"}")
                record(id, ok, reason)
            }
        }
    }

    companion object {
        private const val TAG = "Frequencies"
        private const val MAX_LOG = 500
        private const val LAUNCH_DELAY_MS = 8_000L
    }
}

/** The daily sweep in the background (survey section 8): WorkManager, network required. */
class HealthWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as FrequenciesApp
        if (!app.library.settings.value.bool("health_recheck", true)) {
            app.health.skippedDisabled("daily")
            return Result.success()
        }
        Log.i("Frequencies", "[health] daily check: starting")
        app.health.sweep("daily")
        return Result.success()
    }

    companion object {
        private const val NAME = "frequencies-health-check"

        /** On with Settings' "Re-check frequencies that are down", off without it. */
        fun schedule(context: Context, on: Boolean) {
            val work = WorkManager.getInstance(context)
            if (!on) { work.cancelUniqueWork(NAME); return }
            val request = PeriodicWorkRequestBuilder<HealthWorker>(1, TimeUnit.DAYS, 4, TimeUnit.HOURS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setInitialDelay(6, TimeUnit.HOURS)
                .build()
            work.enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.KEEP, request)
        }
    }
}
