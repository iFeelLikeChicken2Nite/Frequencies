package com.frequencies.app.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.playback.AdNow
import com.frequencies.core.AdGlass
import com.frequencies.core.Glyphs
import kotlinx.coroutines.delay

/**
 * The ad hourglass in the player bar, beside the star (the desktop's
 * shell.Hourglass): there while a station's opening ad is covered or
 * counted, its sand following the player's own ad clock, empty when the
 * ad time is up and gone 1.5 s later. Drawn in the app's glyph pen: the
 * frame in [frame], the sand in the accent.
 */
@Composable
fun AdHourglass(frame: Color, size: Dp = 20.dp, modifier: Modifier = Modifier) {
    val cover by AdNow.cover.collectAsStateWithLifecycle()
    var run by remember { mutableFloatStateOf(0f) }
    var words by remember { mutableStateOf("") }
    var showing by remember { mutableStateOf(false) }
    LaunchedEffect(cover) {
        val c = cover
        if (c == null) { showing = false; return@LaunchedEffect }
        val total = maxOf(1.0, c.seconds)
        showing = true
        while (true) {
            val left = AdNow.timeLeft()
            if (left == null || left <= 0) {
                // Over: the empty glass stays a moment, then goes.
                run = 1f
                words = AdGlass.timeText(null)
                delay(AdGlass.EMPTY_HOLD_MS)
                showing = false
                break
            }
            run = AdGlass.run(total, minOf(total, left))
            words = AdGlass.timeText(left)
            delay(AdGlass.TICK_MS)
        }
    }
    if (!showing) return
    val sandColor = MaterialTheme.colorScheme.primary
    Canvas(modifier.size(size).semantics { contentDescription = words }) {
        val u = this.size.minDimension / 24f
        fun o(x: Float, y: Float) = Offset(x * u, y * u)
        val pen = Glyphs.PEN * u
        for (l in AdGlass.FRAME) drawLine(frame, o(l[0], l[1]), o(l[2], l[3]), strokeWidth = pen, cap = StrokeCap.Round)
        val sand = AdGlass.sand(run)
        for (poly in listOfNotNull(sand.upper, sand.lower)) {
            val path = Path().apply {
                moveTo(poly[0] * u, poly[1] * u)
                for (i in 2 until poly.size step 2) lineTo(poly[i] * u, poly[i + 1] * u)
                close()
            }
            drawPath(path, sandColor)
        }
        sand.stream?.let { drawLine(sandColor, o(it[0], it[1]), o(it[2], it[3]), strokeWidth = 1.2f * u) }
    }
}
