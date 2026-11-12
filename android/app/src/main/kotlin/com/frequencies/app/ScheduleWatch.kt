package com.frequencies.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.frequencies.app.ui.MainActivity
import com.frequencies.core.Schedule
import java.time.LocalDateTime
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * What the Schedule says is on the air, checked every minute while the
 * app runs: the LIVE NOW badge, the jump pills, Android Auto's Live now
 * tab, and the start reminders (schedule.Reminders). A reminder is only a
 * notice: the app never tunes to a show by itself.
 */
class ScheduleWatch(private val context: Context, private val library: Library, private val scope: CoroutineScope) {
    private val _live = MutableStateFlow<List<Schedule.Live>>(emptyList())
    /** Every show on the air now, in the stations' manual order. */
    val live: StateFlow<List<Schedule.Live>> = _live.asStateFlow()

    private val reminders = Schedule.Reminders()

    fun start() {
        refresh()
        // Nothing from before launch: the first check only notes what has started (schedule.Reminders).
        reminders.check(library.stations.value, library.settings.value, LocalDateTime.now())
        scope.launch {
            while (isActive) {
                // On the minute, so a show turns live when it starts.
                val now = LocalDateTime.now()
                delay(((60 - now.second) * 1000L - now.nano / 1_000_000L).coerceAtLeast(200L))
                tick()
            }
        }
        // A show added or edited counts at once.
        scope.launch { library.stations.drop(1).collect { refresh() } }
    }

    private fun refresh() {
        val stations = com.frequencies.core.Browse.saved(library.stations.value)
        val now = Schedule.liveNow(stations, LocalDateTime.now())
        if (now.map { it.station.id to it.show } != _live.value.map { it.station.id to it.show }) _live.value = now
    }

    private fun tick() {
        refresh()
        for (live in reminders.check(library.stations.value, library.settings.value, LocalDateTime.now())) announce(live)
    }

    @SuppressLint("MissingPermission")     // checked in canNotify()
    private fun announce(live: Schedule.Live) {
        val text = Schedule.startingText(live)
        Log.i(TAG, "[schedule] $text")
        if (!canNotify()) {
            // Without the permission (Android 13+), only a screen that's open can say it.
            Notices.say("$text.")
            return
        }
        ensureChannel()
        val open = PendingIntent.getActivity(
            context, live.station.id.hashCode(),
            Intent(context, MainActivity::class.java)
                .setAction(ACTION_OPEN_SCHEDULE)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val note = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_frequencies)
            .setContentTitle(live.name.ifEmpty { "A show" })
            .setContentText(text)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(context).notify("show:${live.station.id}".hashCode(), note)
        } catch (e: SecurityException) {
            Notices.say("$text.")
        }
    }

    fun canNotify(): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return false
        return NotificationManagerCompat.from(context).areNotificationsEnabled()
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL) != null) return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Show reminders", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "When a show on your Schedule starts. Frequencies never starts it by itself."
            },
        )
    }

    companion object {
        private const val TAG = "Frequencies"
        private const val CHANNEL = "show_reminders"
        const val ACTION_OPEN_SCHEDULE = "com.frequencies.OPEN_SCHEDULE"
    }
}
