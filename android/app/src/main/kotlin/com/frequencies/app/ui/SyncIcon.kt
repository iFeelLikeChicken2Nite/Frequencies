package com.frequencies.app.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.sync
import com.frequencies.core.SettingsTabs
import kotlin.math.cos
import kotlin.math.sin
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * The sync icon (the desktop's syncicon.pixmap, build 167): two arrows
 * chasing each other round a circle, in the app's glyph pen. [turning]
 * spins it a turn every 1.2 s (the desktop's 12° every 40 ms); still, it
 * rests at 0°. [calm] (reduced motion) keeps it still.
 */
@Composable
fun SyncGlyph(turning: Boolean, tint: Color, size: Dp, modifier: Modifier = Modifier) {
    val calm = LocalCalm.current
    val angle = if (turning && !calm) {
        val spin = rememberInfiniteTransition(label = "sync")
        val a by spin.animateFloat(0f, 360f, infiniteRepeatable(tween(paced(1_200).toInt(), easing = LinearEasing), RepeatMode.Restart), label = "turn")
        a
    } else 0f
    Canvas(modifier.size(size)) {
        val u = this.size.minDimension / 24f
        rotate(angle) {
            val r = 7.5f * u
            val c = center
            val stroke = Stroke(width = maxOf(1.4f, 1.9f * u), cap = StrokeCap.Round)
            // Qt's arcs run counter-clockwise from 3 o'clock; Compose's clockwise: the same arcs, mirrored angles.
            for (start in listOf(20f, 200f)) {
                drawArc(tint, -(start + 130f), 130f, false, Offset(c.x - r, c.y - r), Size(2 * r, 2 * r), style = stroke)
            }
            // An arrowhead at the end of each arc, pointing on round.
            for (end in listOf(150f, 330f)) {
                val a = Math.toRadians(-end.toDouble())
                fun pt(x: Float, y: Float): Offset {
                    val ca = cos(a).toFloat(); val sa = sin(a).toFloat()
                    return Offset(c.x + x * ca - y * sa, c.y + x * sa + y * ca)
                }
                val head = Path().apply {
                    val p1 = pt(r - 3.2f * u, 0.4f * u); moveTo(p1.x, p1.y)
                    val p2 = pt(r + 3.2f * u, 0.4f * u); lineTo(p2.x, p2.y)
                    val p3 = pt(r, -3.6f * u); lineTo(p3.x, p3.y)
                    close()
                }
                drawPath(head, tint)
            }
        }
    }
}

/** Links into Settings from elsewhere in the app (the player bar's sync icon). */
object SettingsLink {
    /** The Settings tab to open, or null. The app shell opens it and clears it. */
    val open = MutableStateFlow<String?>(null)

    fun sync() { open.value = SettingsTabs.LIBRARY }
}

/**
 * The player bar's small sync icon (the desktop's SyncBadge): only there
 * while a sync runs, turning in the accent; a problem from the last run
 * shows it still, in the down color. Tap: Settings at the sync section.
 */
@Composable
fun SyncBadge(size: Dp = 18.dp) {
    val sync = LocalContext.current.sync
    val activity by sync.activity.collectAsStateWithLifecycle()
    val showing = activity.busy || activity.problem.isNotEmpty()
    if (!showing) return
    val words = if (activity.busy) activity.text else activity.problem
    IconButton(onClick = { SettingsLink.sync() }, modifier = Modifier.semantics { contentDescription = words }) {
        SyncGlyph(
            turning = activity.busy,
            tint = if (activity.busy) MaterialTheme.colorScheme.primary else MaterialTheme.freq.down,
            size = size,
        )
    }
}
