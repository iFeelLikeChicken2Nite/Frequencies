package com.frequencies.app.playback

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.util.Log
import androidx.core.content.ContextCompat

/**
 * Is the phone showing on a car's screen through Android Auto right now?
 * Android Auto answers through its own content provider and a broadcast
 * when that changes (what androidx.car.app's CarConnection reads, without
 * the library). Used for the car's own buttons and the static's extra
 * loudness (batch 24). [onChange] runs on a background thread.
 */
class CarLink(private val context: Context, private val onChange: (Boolean) -> Unit) {
    @Volatile var connected: Boolean = false
        private set
    private var known = false

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) = check()
    }

    fun start() {
        runCatching {
            ContextCompat.registerReceiver(context, receiver, IntentFilter(ACTION_UPDATED), ContextCompat.RECEIVER_EXPORTED)
        }.onFailure { Log.w(TAG, "[auto] can't listen for Android Auto connecting: $it") }
        check()
    }

    fun stop() {
        runCatching { context.unregisterReceiver(receiver) }
    }

    /** Ask Android Auto again (a broadcast came, or the car's media browser just connected). */
    fun check() {
        Thread({
            val now = query()
            synchronized(this) {
                if (known && now == connected) return@Thread
                known = true
                connected = now
            }
            onCar.value = now
            Log.i(TAG, "[auto] Android Auto ${if (now) "connected (projection)" else "not connected"}")
            onChange(now)
        }, "car-link").start()
    }

    private fun query(): Boolean = try {
        context.contentResolver.query(Uri.parse("content://$AUTHORITY"), arrayOf(COLUMN), null, null, null)?.use { c ->
            c.moveToNext() && c.getInt(c.getColumnIndexOrThrow(COLUMN)) != NOT_CONNECTED
        } ?: false
    } catch (e: Exception) {
        // Android Auto isn't installed, or this phone's version has no provider: not in a car.
        false
    }

    companion object {
        /** The phone is on a car's screen now: the phone's own screens rest (Now Playing's moving background holds still). */
        val onCar = kotlinx.coroutines.flow.MutableStateFlow(false)
        private const val TAG = "Frequencies"
        private const val AUTHORITY = "androidx.car.app.connection"
        private const val COLUMN = "CarConnectionState"
        private const val ACTION_UPDATED = "androidx.car.app.connection.action.CAR_CONNECTION_UPDATED"
        private const val NOT_CONNECTED = 0
    }
}
