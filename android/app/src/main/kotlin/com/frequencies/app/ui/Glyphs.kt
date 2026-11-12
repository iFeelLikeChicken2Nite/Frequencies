package com.frequencies.app.ui

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.PowerManager
import android.provider.Settings as AndroidSettings
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.interaction.InteractionSource
import androidx.compose.foundation.interaction.PressInteraction
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathBuilder
import androidx.compose.ui.graphics.vector.PathNode
import androidx.compose.ui.graphics.vector.group
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import com.frequencies.core.GlyphMotion
import com.frequencies.core.Glyphs
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.cos
import kotlin.math.sin

/*
 * The desktop's drawn icons (core Glyphs, from shell.py _make_icon) as
 * Compose ImageVectors, and their animations frame for frame (core
 * GlyphMotion). Every frame is built once and kept, as the desktop caches
 * its frames by name.
 */

/**
 * Reduced motion: Settings > Accessibility > Remove animations (animator
 * duration scale 0) or battery saver. Glyphs jump to their last frame,
 * the playing row's bars stand still.
 */
val LocalCalm = compositionLocalOf { false }

/**
 * The phone's glyph animations run this much slower than the desktop's
 * frame times (user, build 170: "lower the speed of all icon glyph
 * animations a bit"): the frames are the same, each one stays longer.
 * The playing bars and the sync icon's turn slow down by the same amount.
 */
const val GLYPH_PACE = 1.4f

/** A desktop frame time in ms, slowed by [GLYPH_PACE]. */
fun paced(ms: Int): Long = (ms * GLYPH_PACE).toLong()

@Composable
fun ProvideCalm(content: @Composable () -> Unit) {
    val context = LocalContext.current
    fun read(): Boolean {
        val scale = runCatching { AndroidSettings.Global.getFloat(context.contentResolver, AndroidSettings.Global.ANIMATOR_DURATION_SCALE, 1f) }.getOrDefault(1f)
        val saver = context.getSystemService(PowerManager::class.java)?.isPowerSaveMode == true
        return scale == 0f || saver
    }
    var calm by remember { mutableStateOf(read()) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(lifecycle) { lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) { calm = read() } }
    DisposableEffect(context) {
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) { calm = read() }
        }
        context.registerReceiver(receiver, IntentFilter(PowerManager.ACTION_POWER_SAVE_MODE_CHANGED))
        onDispose { context.unregisterReceiver(receiver) }
    }
    CompositionLocalProvider(LocalCalm provides calm, content = content)
}

object GlyphVectors {
    private val cache = HashMap<String, ImageVector>()

    /** [name] at progress [t] (core Glyphs.draw), the parts in [ink] only. */
    fun of(name: String, t: Float = 0f, ink: Glyphs.Ink = Glyphs.Ink.MAIN): ImageVector {
        val key = "$name@$t@$ink"
        return synchronized(cache) { cache.getOrPut(key) { build(name, t, ink) } }
    }

    private fun build(name: String, t: Float, ink: Glyphs.Ink): ImageVector {
        val b = ImageVector.Builder(name = key(name, t), defaultWidth = 24.dp, defaultHeight = 24.dp, viewportWidth = 24f, viewportHeight = 24f)
        for (part in Glyphs.draw(name, t).filter { it.ink == ink }) {
            val nodes = nodes(part.shape)
            val turn = (part.shape as? Glyphs.RRect)?.turn ?: 0f
            val clip = part.clip?.let { (cx, cy, r) -> PathBuilder().apply { oval(cx - r, cy - r, 2 * r, 2 * r) }.nodes } ?: emptyList()
            b.group(rotate = turn, pivotX = 12f, pivotY = 12f, clipPathData = clip) {
                addPath(
                    pathData = nodes,
                    fill = if (part.fill) SolidColor(Color.Black) else null,
                    fillAlpha = part.alpha,
                    stroke = if (part.stroke > 0f) SolidColor(Color.Black) else null,
                    strokeAlpha = part.alpha,
                    strokeLineWidth = part.stroke,
                    strokeLineCap = if (part.squareCaps) StrokeCap.Square else StrokeCap.Round,
                    strokeLineJoin = StrokeJoin.Round,
                )
            }
        }
        return b.build()
    }

    private fun key(name: String, t: Float) = if (t == 0f) name else "$name$t"

    private fun nodes(s: Glyphs.Shape): List<PathNode> = PathBuilder().apply {
        when (s) {
            is Glyphs.Poly -> {
                moveTo(s.points[0], s.points[1])
                for (i in 2 until s.points.size step 2) lineTo(s.points[i], s.points[i + 1])
                if (s.closed) close()
            }
            is Glyphs.Oval -> oval(s.x, s.y, s.w, s.h)
            is Glyphs.Arc -> {
                // Qt: degrees counter-clockwise from three o'clock, on screen (y down).
                val rx = s.w / 2; val ry = s.h / 2; val cx = s.x + rx; val cy = s.y + ry
                fun at(deg: Float) = Math.toRadians(deg.toDouble()).let { Offset(cx + rx * cos(it).toFloat(), cy - ry * sin(it).toFloat()) }
                val from = at(s.start); val to = at(s.start + s.sweep)
                moveTo(from.x, from.y)
                arcTo(rx, ry, 0f, kotlin.math.abs(s.sweep) > 180f, s.sweep < 0f, to.x, to.y)
            }
            is Glyphs.RRect -> {
                val r = s.r.coerceAtMost(minOf(s.w, s.h) / 2)
                val x = s.x; val y = s.y; val w = s.w; val h = s.h
                if (r <= 0f) {
                    moveTo(x, y); lineTo(x + w, y); lineTo(x + w, y + h); lineTo(x, y + h); close()
                } else {
                    moveTo(x + r, y)
                    lineTo(x + w - r, y); arcTo(r, r, 0f, false, true, x + w, y + r)
                    lineTo(x + w, y + h - r); arcTo(r, r, 0f, false, true, x + w - r, y + h)
                    lineTo(x + r, y + h); arcTo(r, r, 0f, false, true, x, y + h - r)
                    lineTo(x, y + r); arcTo(r, r, 0f, false, true, x + r, y)
                    close()
                }
            }
        }
    }.nodes

    private fun PathBuilder.oval(x: Float, y: Float, w: Float, h: Float) {
        val rx = w / 2; val ry = h / 2
        moveTo(x, y + ry)
        arcTo(rx, ry, 0f, false, true, x + w, y + ry)
        arcTo(rx, ry, 0f, false, true, x, y + ry)
        close()
    }
}

/** One of the desktop's glyphs, tinted like an Icon (current content color by default). */
@Composable
fun Glyph(
    name: String,
    contentDescription: String?,
    modifier: Modifier = Modifier,
    t: Float = 0f,
    tint: Color = LocalContentColor.current,
    size: Dp = 24.dp,
    /** The gray under shazam_fill (its BASE ink). */
    baseTint: Color = MaterialTheme.colorScheme.onSurfaceVariant,
) {
    val hasBase = name == "shazam_fill"
    if (!hasBase) {
        Icon(GlyphVectors.of(name, t), contentDescription, modifier.size(size), tint = tint)
    } else {
        Box(modifier.size(size)) {
            Icon(GlyphVectors.of(name, t, Glyphs.Ink.BASE), contentDescription, Modifier.size(size), tint = baseTint)
            Icon(GlyphVectors.of(name, t), null, Modifier.size(size), tint = tint)
        }
    }
}

/**
 * Steps a glyph through [motion]'s frames toward 0 or 1, from wherever it
 * is (reversing mid-way, as the desktop's timers do). Calm: straight to
 * the end frame.
 */
@Composable
fun rememberFrames(target: Boolean, motion: GlyphMotion.Frames): Float {
    val calm = LocalCalm.current
    var frame by remember { mutableIntStateOf(if (target) motion.frames else 0) }
    LaunchedEffect(target, calm) {
        val goal = if (target) motion.frames else 0
        if (calm) { frame = goal; return@LaunchedEffect }
        while (frame != goal) {
            delay(paced(motion.ms))
            frame += if (frame < goal) 1 else -1
        }
    }
    return motion.progress(frame)
}

/**
 * The desktop's hover animations, played on press: forward while held
 * (or to the end for a quick tap), then back (shell._animate_glyph).
 */
@Composable
fun rememberPressFrames(interaction: InteractionSource, motion: GlyphMotion.Frames): Float {
    var held by remember { mutableStateOf(false) }
    var run by remember { mutableIntStateOf(0) }
    LaunchedEffect(interaction) {
        interaction.interactions.collect { i ->
            when (i) {
                is PressInteraction.Press -> { held = true; run++ }
                is PressInteraction.Release, is PressInteraction.Cancel -> held = false
            }
        }
    }
    var frame by remember { mutableIntStateOf(0) }
    val calm = LocalCalm.current
    LaunchedEffect(run) {
        if (run == 0 || calm) return@LaunchedEffect
        while (frame < motion.frames) { delay(paced(motion.ms)); frame++ }
        while (held) delay(16)
        while (frame > 0) { delay(paced(motion.ms)); frame-- }
    }
    return motion.progress(frame)
}

/** Play/pause, morphing between the triangle and the two bars on every change (_morph_play). */
@Composable
fun PlayPauseGlyph(playing: Boolean, tint: Color, size: Dp, contentDescription: String?) {
    val t = rememberFrames(playing, GlyphMotion.PLAY_PAUSE)
    Glyph("playpause", contentDescription, t = t, tint = tint, size = size)
}

/**
 * The star: lit in the accent when the song is a favorite; favoriting it
 * throws the desktop's rays first (star_shine1..4, 55 ms each).
 */
@Composable
fun StarGlyph(on: Boolean, size: Dp, contentDescription: String?, onTint: Color, offTint: Color) {
    var shine by remember { mutableIntStateOf(0) }
    var was by remember { mutableStateOf(on) }
    val calm = LocalCalm.current
    LaunchedEffect(on) {
        if (on && !was && !calm) {
            for (step in 1..GlyphMotion.STAR_SHINE.frames) { shine = step; delay(paced(GlyphMotion.STAR_SHINE.ms)) }
        }
        shine = 0
        was = on
    }
    when {
        shine > 0 -> Glyph("star_shine", contentDescription, t = GlyphMotion.STAR_SHINE.progress(shine), tint = onTint, size = size)
        on -> Glyph("star_on", contentDescription, tint = onTint, size = size)
        else -> Glyph("star", contentDescription, tint = offTint, size = size)
    }
}

/**
 * Song ID's button: the mark (lit while the station identifies songs),
 * and when a song is identified ([pulses] goes up) the desktop's ripple
 * with the mark filling from its middle, then settling (shazam_pulse).
 */
@Composable
fun SongIdGlyph(on: Boolean, pulses: Int, accent: Color, rest: Color, size: Dp, contentDescription: String?) {
    var step by remember { mutableIntStateOf(-1) }
    val ripple = remember { Animatable(0f) }
    val scope = rememberCoroutineScope()
    val calm = LocalCalm.current
    LaunchedEffect(pulses) {
        if (pulses == 0 || calm) return@LaunchedEffect
        scope.launch {
            ripple.snapTo(0f)
            ripple.animateTo(1f, tween(paced(GlyphMotion.RIPPLE_MS).toInt(), easing = { GlyphMotion.outCubic(it) }))
            ripple.snapTo(0f)
        }
        for (s in 1 until GlyphMotion.SONG_ID.frames) { step = s; delay(paced(GlyphMotion.SONG_ID.ms)) }
        step = -1
    }
    Box(contentAlignment = Alignment.Center) {
        val p = ripple.value
        if (p > 0f && p < 1f) {
            Canvas(Modifier.size(size * 2)) {
                val base = this.size.minDimension / 4
                for (lag in listOf(0f, 0.22f)) {
                    val t = p - lag
                    if (t <= 0f || t >= 1f) continue
                    val radius = base * (0.45f + t * 1.15f)
                    val alpha = 150f / 255f * Math.pow((1.0 - t), 1.6).toFloat()
                    drawCircle(accent.copy(alpha = alpha), radius, style = Stroke(width = maxOf(1.2f, base * 0.16f * (1f - t))))
                }
                if (p < 0.34f) drawCircle(accent.copy(alpha = 70f / 255f * (1f - p / 0.34f)), base * 0.95f)
            }
        }
        val fill = if (step >= 0) GlyphMotion.songIdFill(step) else null
        // The desktop's own mark (its icons/shazam.png, tinted: gray off, the accent on), not
        // the ring and dot it only draws when that file is missing (build 180, user).
        when {
            step >= 0 && fill != null -> Box(Modifier.size(size)) {
                SongIdMark(rest, size, contentDescription)
                // The accent arrives from the middle outward (desktop shazam_fill).
                if (fill > 0.02f) SongIdMark(accent, size, null, Modifier.drawWithContent {
                    val radius = this.size.minDimension / 2f * fill * 1.15f
                    val circle = androidx.compose.ui.graphics.Path().apply {
                        addOval(androidx.compose.ui.geometry.Rect(center, radius))
                    }
                    clipPath(circle) { this@drawWithContent.drawContent() }
                })
            }
            step >= 0 -> SongIdMark(
                Color(0xFF000000 or GlyphMotion.lighter(accent.rgbInt(), GlyphMotion.settleLighter(step)).toLong()),
                size, contentDescription,
            )
            on -> SongIdMark(accent, size, contentDescription)
            else -> SongIdMark(rest, size, contentDescription)
        }
    }
}

/** The Song ID mark (the desktop's icons/shazam.png, a white mark), tinted like the desktop's _from_file. */
@Composable
private fun SongIdMark(tint: Color, size: Dp, contentDescription: String?, modifier: Modifier = Modifier) {
    Icon(
        androidx.compose.ui.res.painterResource(com.frequencies.app.R.drawable.song_id_mark),
        contentDescription,
        modifier.size(size),
        tint = tint,
    )
}

fun Color.rgbInt(): Int =
    ((red * 255 + 0.5f).toInt() shl 16) or ((green * 255 + 0.5f).toInt() shl 8) or (blue * 255 + 0.5f).toInt()
