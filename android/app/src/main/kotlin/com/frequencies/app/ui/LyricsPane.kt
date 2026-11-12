package com.frequencies.app.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.material3.FilledTonalButton
import com.frequencies.core.LyricsSearch

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.library
import com.frequencies.app.playback.SongNow
import com.frequencies.core.LyricsClient
import kotlin.math.abs
import kotlinx.coroutines.delay

/**
 * The identified song's lyrics. Timed lyrics follow the song: the line
 * being sung is larger, in the accent, kept a little above the middle;
 * the others are dimmer and fade toward the top and bottom edges. Tap a line
 * when it is sung to put the clock right (the desktop's double-click).
 * Scrolling by hand pauses the following for a few seconds.
 */
@Composable
fun LyricsPane(modifier: Modifier = Modifier) {
    val library = LocalContext.current.library
    val say = LocalSay.current
    val settings by library.settings.collectAsStateWithLifecycle()
    val state by SongNow.lyrics.collectAsStateWithLifecycle()
    val resyncs by SongNow.resyncs.collectAsStateWithLifecycle()
    val base = settings.double("lyric_size", 13.0).coerceIn(12.0, 34.0).toFloat() * 1.4f
    val wash = MaterialTheme.freq.wash
    val accent = wash?.let { Color(0xFF000000 or it.accent.toLong()) } ?: MaterialTheme.colorScheme.primary
    val text2 = wash?.let { Color(0xFF000000 or it.text2.toLong()) } ?: MaterialTheme.colorScheme.onSurfaceVariant

    Box(modifier, contentAlignment = Alignment.Center) {
        when (val s = state) {
            is SongNow.Lyrics.Idle -> Message(s.reason)
            SongNow.Lyrics.Loading -> Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
                CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 3.dp)
                Message("Looking up lyrics…")
            }
            is SongNow.Lyrics.Missing -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Message(s.message)
                // Task 213: in place of the empty panel, a web search for the song's lyrics.
                if (s.search.isNotEmpty()) {
                    val context = LocalContext.current
                    FilledTonalButton(onClick = {
                        runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(s.search))) }
                            .onFailure { say("No browser on this phone can open it.") }
                    }) {
                        Glyph("search", null, size = 18.dp)
                        Spacer(Modifier.width(8.dp))
                        Text(LyricsSearch.TEXT)
                    }
                }
            }
            is SongNow.Lyrics.Found -> {
                val result = s.result
                if (result.instrumental || result.lines.isEmpty()) {
                    Message("(instrumental)")
                    return@Box
                }
                val lines = result.lines
                val listState = rememberLazyListState()
                var current by remember(lines) { mutableIntStateOf(-1) }
                var touchedAt by remember { mutableLongStateOf(0L) }
                if (result.timed) {
                    LaunchedEffect(lines) {
                        while (true) {
                            current = LyricsClient.lineAt(lines, SongNow.positionMs())
                            delay(200)
                        }
                    }
                }
                // A drag by hand (not our own scrolling) holds the view where it was put.
                LaunchedEffect(listState) {
                    listState.interactionSource.interactions.collect { touchedAt = System.currentTimeMillis() }
                }
                LaunchedEffect(resyncs) { touchedAt = 0L }
                LaunchedEffect(current, resyncs) {
                    if (current < 0 || System.currentTimeMillis() - touchedAt < 4000) return@LaunchedEffect
                    listState.animateScrollToItem(current)
                }
                BoxWithConstraints(Modifier.fillMaxSize()) {
                    val above = maxHeight * 0.38f
                    LazyColumn(
                        state = listState,
                        contentPadding = PaddingValues(top = above, bottom = maxHeight * 0.5f),
                        modifier = Modifier
                            .fillMaxSize()
                            .graphicsLayer(compositingStrategy = CompositingStrategy.Offscreen)
                            .drawWithContent {
                                drawContent()
                                drawRect(
                                    Brush.verticalGradient(
                                        0f to Color.Transparent, 0.14f to Color.Black,
                                        0.86f to Color.Black, 1f to Color.Transparent,
                                    ),
                                    blendMode = BlendMode.DstIn,
                                )
                            },
                    ) {
                        itemsIndexed(lines) { i, line ->
                            // The desktop's Comfort View lyrics: the line being sung larger, semibold, in the
                            // accent (as it reads on the wash); the rest smaller in text2 at 55%.
                            val active = result.timed && i == current
                            Text(
                                line.text.ifEmpty { "♪" },
                                style = MaterialTheme.typography.bodyLarge.copy(
                                    fontSize = (if (active) base else base * 0.82f).sp,
                                    lineHeight = (base * 1.45f).sp,
                                    fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal,
                                ),
                                color = when {
                                    active -> accent
                                    result.timed -> text2.copy(alpha = 0.55f)
                                    else -> text2
                                },
                                textAlign = TextAlign.Center,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .then(
                                        if (result.timed) Modifier.clickable {
                                            SongNow.resync(line.ms)
                                            current = i
                                            say("Lyrics resynced to here.")
                                        } else Modifier,
                                    )
                                    .padding(horizontal = 8.dp, vertical = 6.dp),
                            )
                        }
                        item {
                            Text(
                                "Lyrics from ${result.source}",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                textAlign = TextAlign.Center,
                                modifier = Modifier.fillMaxWidth().padding(top = 24.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Message(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodyLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        textAlign = TextAlign.Center,
        modifier = Modifier.padding(24.dp),
    )
}
