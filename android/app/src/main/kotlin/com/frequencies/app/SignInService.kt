package com.frequencies.app

import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.frequencies.app.ui.MainActivity

/**
 * Keeps the app awake and on the network while an iBroadcast sign-in
 * waits for the browser (a short foreground service, two minutes at most).
 *
 * The sign-in page opens in a Custom Tab, which is the browser's screen:
 * while it is up, Frequencies is in the background, and Android cuts an
 * app in the background off the network (Android 15 and later, and
 * GrapheneOS) and may close it. Then the browser's hand-back to
 * 127.0.0.1:8731 and the code exchange never got through, and the
 * sign-in never finished. A foreground service is how Android lets an app
 * keep working meanwhile; it shows a quiet notification until the
 * sign-in ends.
 */
class SignInService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, "Signing in", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "Shown while Frequencies waits for a sign-in page (iBroadcast) to finish."
                },
            )
        }
        val note = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_frequencies)
            .setContentTitle("Signing in to iBroadcast")
            .setContentText("Finish signing in on the iBroadcast page.")
            .setOngoing(true)
            .setSilent(true)
            .build()
        try {
            val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SHORT_SERVICE else 0
            ServiceCompat.startForeground(this, NOTE_ID, note, type)
        } catch (e: Exception) {
            // Not allowed now (the app wasn't on screen): the sign-in still tries without it.
            Log.w(TAG, "[ibroadcast] sign-in: couldn't keep the app awake (${e.javaClass.simpleName}: ${e.message})")
            stopSelf()
        }
        return START_NOT_STICKY
    }

    /** A short service's time is up (Android 14+, about three minutes): the sign-in has ended by then. */
    override fun onTimeout(startId: Int) {
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    companion object {
        private const val TAG = "Frequencies"
        private const val CHANNEL = "sign_in"
        private const val NOTE_ID = 4120
        private const val STOP = "com.frequencies.SIGN_IN_STOP"

        fun start(context: Context) {
            runCatching { context.startForegroundService(Intent(context, SignInService::class.java)) }
                .onFailure { Log.w(TAG, "[ibroadcast] sign-in: couldn't start the sign-in service: ${it.message}") }
        }

        fun stop(context: Context) {
            runCatching { context.startService(Intent(context, SignInService::class.java).setAction(STOP)) }
                .onFailure { runCatching { context.stopService(Intent(context, SignInService::class.java)) } }
        }
    }
}

/**
 * "Back to Frequencies" on the page iBroadcast's sign-in ends on
 * (frequencies://signed-in): brings back the Frequencies screen that
 * started the sign-in, closing the sign-in tab above it, instead of
 * opening a second copy of the app on top of the tab.
 */
class SignedInActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startActivity(
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NEW_TASK),
        )
        finish()
    }
}
