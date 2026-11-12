package com.frequencies.app

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.frequencies.core.ReportQueue
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Sends the thumbs-down reports waiting in the queue (photopack.ReportQueue.kick,
 * desktop build 152): WorkManager, network required, so a report pressed
 * offline, or in a tunnel, or just before the app was closed goes out once
 * the phone is back online. A failed send is tried again with backoff
 * (30 s doubling, like the desktop's 30 s to 30 min).
 */
class ReportWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val app = applicationContext as FrequenciesApp
        val kit = app.artists
        repeat(20) {
            when (val r = kit.sendNextReport()) {
                is ReportQueue.Sent.Filed -> Log.i(TAG, "[pack] thumbs down for '${r.artist}' filed: ${r.text}")
                is ReportQueue.Sent.GaveUp -> {
                    Log.i(TAG, "[pack] thumbs down for '${r.artist}' not filed (${r.why})${if (r.address.isNotEmpty()) ", offering the browser" else ""}")
                    if (r.address.isNotEmpty()) offerBrowser(applicationContext, r.artist, r.address)
                }
                is ReportQueue.Sent.Retry -> {
                    Log.i(TAG, "[pack] thumbs down for '${r.artist}' not sent yet (${r.why}); will try again")
                    return@withContext Result.retry()
                }
                is ReportQueue.Sent.Held -> {
                    val wait = (r.atSeconds - System.currentTimeMillis() / 1000).coerceAtLeast(1)
                    enqueue(applicationContext, wait, chain = false)
                    return@withContext Result.success()
                }
                ReportQueue.Sent.NoService, ReportQueue.Sent.Empty -> return@withContext Result.success()
            }
        }
        Result.success()
    }

    companion object {
        private const val TAG = "Frequencies"
        private const val NAME = "frequencies-reports"
        private const val CHANNEL = "reports"

        /** Send what is waiting, [delaySeconds] from now, once online. */
        fun enqueue(context: Context, delaySeconds: Long, chain: Boolean = true) {
            val request = OneTimeWorkRequestBuilder<ReportWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setInitialDelay(delaySeconds.coerceAtLeast(0), TimeUnit.SECONDS)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            // After a run that is going now (a Held answer replaces its own follow-up).
            WorkManager.getInstance(context).enqueueUniqueWork(
                NAME, if (chain) ExistingWorkPolicy.APPEND_OR_REPLACE else ExistingWorkPolicy.REPLACE, request,
            )
        }

        /**
         * The Worker refused the report for good: the pre-filled issue, one
         * tap away. A notification, because this runs in the background and
         * Android doesn't let it open the browser by itself.
         */
        fun offerBrowser(context: Context, artist: String, address: String) {
            val allowed = (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) &&
                NotificationManagerCompat.from(context).areNotificationsEnabled()
            if (!allowed) {
                Notices.say("The report about $artist couldn't be sent. Tap thumbs down again tomorrow, or report it on GitHub.")
                return
            }
            val manager = context.getSystemService(NotificationManager::class.java)
            if (manager.getNotificationChannel(CHANNEL) == null) {
                manager.createNotificationChannel(
                    NotificationChannel(CHANNEL, "Reports", NotificationManager.IMPORTANCE_LOW).apply {
                        description = "When a thumbs-down report couldn't be sent and can be filed in the browser instead."
                    },
                )
            }
            val open = PendingIntent.getActivity(
                context, artist.hashCode(),
                Intent(Intent.ACTION_VIEW, Uri.parse(address)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val note = NotificationCompat.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat_frequencies)
                .setContentTitle("Report about $artist")
                .setContentText("It couldn't be sent. Tap to file it in your browser.")
                .setContentIntent(open)
                .setAutoCancel(true)
                .build()
            try {
                NotificationManagerCompat.from(context).notify("report:$artist".hashCode(), note)
            } catch (e: SecurityException) {
                Notices.say("The report about $artist couldn't be sent.")
            }
        }
    }
}
