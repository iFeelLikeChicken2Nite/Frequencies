package com.frequencies.app.ui

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings as AndroidSettings
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.AnimationSpec
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.library
import com.frequencies.core.Aurora

/**
 * Now Playing's background (comfort spec 8): the desktop's moving
 * background (nowplaying.py _paint_aurora), four soft pools of the
 * accent drifting under a veil that keeps every text color readable.
 * Drawn on its own layer, in the draw phase only: the time is the one
 * value that changes, at most 30 times a second (15 after two minutes
 * without a touch). It stands still (the still frame) with Remove
 * animations, and in "Auto" with Battery Saver or the battery under 20%
 * and not charging; the clock stops while the app is out of sight or the
 * phone is on a car's screen (Android Auto, build 185); play
 * and pause ease the drift in and out. "Moving background: Off" shows
 * the still wash ([StillWash]) instead.
 */
@Composable
fun ComfortBackground(modifier: Modifier = Modifier, playing: Boolean = false, content: @Composable () -> Unit) {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    val setting = Aurora.setting(settings)
    val look = MaterialTheme.freq.aurora
    if (setting == Aurora.OFF || look == null) {
        StillWash(modifier, content)
        return
    }
    val power = rememberPower()
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val lifeState by lifecycle.currentStateFlow.collectAsState()
    // On a car's screen the phone's own background holds still, like out of sight (user, build 185).
    val inCar by com.frequencies.app.playback.CarLink.onCar.collectAsStateWithLifecycle()
    val inFront = lifeState.isAtLeast(Lifecycle.State.RESUMED) && !inCar
    val run = Aurora.run(setting, power.removeAnimations, power.saver, power.battery, power.charging, inFront, 0L)

    // A new accent (a new song's cover, usually) cross-fades the pools.
    val fade: AnimationSpec<Color> = if (power.removeAnimations) snap() else tween(Aurora.COLOR_MS)
    val base by animateColorAsState(rgb(look.base), fade, label = "aurora base")
    val pool0 by animateColorAsState(rgb(look.pools[0]), fade, label = "aurora 0")
    val pool1 by animateColorAsState(rgb(look.pools[1]), fade, label = "aurora 1")
    val pool2 by animateColorAsState(rgb(look.pools[2]), fade, label = "aurora 2")
    val pool3 by animateColorAsState(rgb(look.pools[3]), fade, label = "aurora 3")
    val veil by animateFloatAsState(look.veil.toFloat(), if (power.removeAnimations) snap<Float>() else tween<Float>(Aurora.COLOR_MS), label = "aurora veil")

    val time = remember { mutableDoubleStateOf(Aurora.STILL_T) }
    val speed = remember { doubleArrayOf(0.0) }
    val lastTouch = remember { longArrayOf(SystemClock.uptimeMillis()) }
    LaunchedEffect(run, playing) {
        when (run) {
            Aurora.Run.STILL -> { time.doubleValue = Aurora.STILL_T; speed[0] = 0.0; return@LaunchedEffect }
            Aurora.Run.HOLD, Aurora.Run.OFF -> return@LaunchedEffect
            else -> Unit
        }
        var t = time.doubleValue
        var last = -1L
        var shown = 0L
        while (true) {
            val now = withFrameNanos { it }
            if (last < 0) { last = now; continue }
            val dtMs = (now - last) / 1e6
            last = now
            speed[0] = Aurora.ease(speed[0], playing, dtMs)
            t += dtMs / 1000.0 * Aurora.SPEED * speed[0]
            val idle = SystemClock.uptimeMillis() - lastTouch[0]
            val fps = if (idle >= Aurora.SLOW_AFTER_MS) Aurora.SLOW_FPS else Aurora.FPS
            // A frame early by up to 2 ms still counts, so 30 fps on a 60 Hz screen is every other frame.
            if (now - shown >= 1_000_000_000L / fps - 2_000_000L) {
                shown = now
                time.doubleValue = t
            }
            if (!playing && speed[0] == 0.0) {
                time.doubleValue = t
                break
            }
        }
    }

    Box(
        modifier.pointerInput(Unit) {
            // Any touch: back to 30 a second. Only watched, never taken.
            awaitPointerEventScope {
                while (true) {
                    awaitPointerEvent(PointerEventPass.Initial)
                    lastTouch[0] = SystemClock.uptimeMillis()
                }
            }
        },
    ) {
        Box(
            Modifier
                .matchParentSize()
                .testTag("moving_background")
                .graphicsLayer { }
                .drawBehind {
                    val t = time.doubleValue
                    val w = size.width.toDouble()
                    val h = size.height.toDouble()
                    drawRect(base)
                    val pools = arrayOf(pool0, pool1, pool2, pool3)
                    val blend = if (look.screen) BlendMode.Screen else BlendMode.SrcOver
                    for (i in pools.indices) {
                        val (cx, cy, r) = Aurora.position(i, t, w, h)
                        val radius = r.toFloat().coerceAtLeast(1f)
                        val center = Offset(cx.toFloat(), cy.toFloat())
                        val c = pools[i]
                        val a = look.poolAlpha[i].toFloat()
                        drawCircle(
                            Brush.radialGradient(listOf(c.copy(alpha = a), c.copy(alpha = 0f)), center = center, radius = radius),
                            radius = radius,
                            center = center,
                            blendMode = blend,
                        )
                    }
                    drawRect(base.copy(alpha = veil.coerceIn(0f, 1f)))
                },
        )
        content()
    }
}

private fun rgb(v: Int) = Color(0xFF000000 or (v.toLong() and 0xFFFFFF))

/** What the moving background's rules read: Remove animations, Battery Saver and the battery. */
private data class Power(val removeAnimations: Boolean, val saver: Boolean, val battery: Int?, val charging: Boolean)

@Composable
private fun rememberPower(): Power {
    val context = LocalContext.current
    fun read(battery: Intent?): Power {
        val scale = runCatching { AndroidSettings.Global.getFloat(context.contentResolver, AndroidSettings.Global.ANIMATOR_DURATION_SCALE, 1f) }.getOrDefault(1f)
        val saver = context.getSystemService(PowerManager::class.java)?.isPowerSaveMode == true
        val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val max = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val percent = if (level >= 0 && max > 0) level * 100 / max else null
        val status = battery?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val plugged = (battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        val charging = plugged || status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        return Power(scale == 0f, saver, percent, charging)
    }
    fun sticky(): Intent? = runCatching { context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) }.getOrNull()
    var power by remember { mutableStateOf(read(sticky())) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val lifeState by lifecycle.currentStateFlow.collectAsState()
    // Remove animations has no broadcast: read again whenever the app comes back to the front.
    LaunchedEffect(lifeState) { if (lifeState.isAtLeast(Lifecycle.State.RESUMED)) power = read(sticky()) }
    DisposableEffect(context) {
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                power = read(if (i.action == Intent.ACTION_BATTERY_CHANGED) i else sticky())
            }
        }
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_BATTERY_CHANGED)
            addAction(PowerManager.ACTION_POWER_SAVE_MODE_CHANGED)
        }
        context.registerReceiver(receiver, filter)
        onDispose { context.unregisterReceiver(receiver) }
    }
    return power
}
