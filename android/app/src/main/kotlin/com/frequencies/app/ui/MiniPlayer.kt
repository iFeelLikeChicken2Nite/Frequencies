package com.frequencies.app.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconToggleButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.library
import com.frequencies.app.playback.SleepTimer
import com.frequencies.app.playback.SongNow
import com.frequencies.core.GlyphMotion
import com.frequencies.core.SongHistory
import com.frequencies.core.Shuffle
import kotlinx.serialization.json.JsonPrimitive

/**
 * The player bar on every screen (desktop PlayerBar): bar_bg with a lit
 * 1 dp top edge, the art, the song with its star, "artist · station", and
 * previous, the round accent play button and next, with the sync icon while a sync runs and
 * the ad hourglass beside the star while an opening ad is covered. Tap it
 * for Now Playing. [wide]: the
 * desktop's full bar under the sidebar, with the transport in the middle
 * and Song ID, sleep timer, equalizer and Comfort View on the right.
 */
@Composable
fun MiniPlayer(
    nowPlaying: NowPlaying,
    connection: PlayerConnection,
    onOpen: () -> Unit,
    modifier: Modifier = Modifier,
    wide: Boolean = false,
    /** Off under Comfort View, where the bar is part of the view. */
    tapToOpen: Boolean = true,
    /** Under the wide Comfort View: its Comfort button switches the cover's retract ([onComfort]) instead of opening. */
    comfort: Boolean = false,
    onComfort: (() -> Unit)? = null,
    /** The phone's bar: dragged up, Now Playing follows the finger ([onDrag] gets each move in px, [onDragEnd] the speed). */
    onDrag: ((Float) -> Unit)? = null,
    onDragEnd: ((Float) -> Unit)? = null,
) {
    AnimatedVisibility(nowPlaying.hasStation, enter = expandVertically(), exit = shrinkVertically()) {
        val f = MaterialTheme.freq
        Column(
            modifier
                .fillMaxWidth()
                .background(f.barBg)
                .then(if (tapToOpen) Modifier.clickable(onClick = onOpen, onClickLabel = "Open Now Playing") else Modifier)
                .then(
                    if (onDrag == null) Modifier
                    else Modifier.draggable(
                        rememberDraggableState { onDrag(it) },
                        Orientation.Vertical,
                        onDragStopped = { velocity -> onDragEnd?.invoke(velocity) },
                    ),
                ),
        ) {
            // The desktop's lit edge: a hairline of bar_edge.
            HorizontalDivider(thickness = 1.dp, color = f.barEdge)
            if (wide) WideBar(nowPlaying, connection, onOpen, comfort, onComfort) else PhoneBar(nowPlaying, connection)
        }
    }
}

@Composable
private fun PhoneBar(nowPlaying: NowPlaying, connection: PlayerConnection) {
    Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        StationArt(nowPlaying.artworkUri, nowPlaying.stationName, Modifier.size(46.dp), corner = 6.dp)
        Spacer(Modifier.width(12.dp))
        SongLines(nowPlaying, Modifier.weight(1f))
        // A sync run (the desktop's player-bar sync icon), then the star with the ad hourglass beside it.
        SyncBadge()
        BarStar(nowPlaying)
        AdHourglass(MaterialTheme.freq.onBar2)
        // More controls while it's down (user, build 170): the star, then previous, play, next.
        GlyphButton("prev", "Previous frequency", { connection.previous() }, tint = MaterialTheme.freq.onBar2, size = 20.dp)
        RoundPlayButton(nowPlaying, connection, 44.dp, 20.dp)
        NextButton(connection, MaterialTheme.freq.onBar2, 20.dp)
    }
}

@Composable
private fun WideBar(nowPlaying: NowPlaying, connection: PlayerConnection, onOpen: () -> Unit, comfort: Boolean = false, onComfort: (() -> Unit)? = null) {
    val f = MaterialTheme.freq
    Box(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 10.dp)) {
        Row(Modifier.align(Alignment.CenterStart), verticalAlignment = Alignment.CenterVertically) {
            StationArt(nowPlaying.artworkUri, nowPlaying.stationName, Modifier.size(46.dp), corner = 6.dp)
            Spacer(Modifier.width(12.dp))
            SongLines(nowPlaying, Modifier.width(260.dp))
            BarStar(nowPlaying)
            // Beside the star, as on the desktop: the covered ad's sand.
            AdHourglass(f.onBar2)
        }
        Row(Modifier.align(Alignment.Center), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            GlyphButton("prev", "Previous frequency", { connection.previous() }, tint = f.onBar2, size = 22.dp)
            RoundPlayButton(nowPlaying, connection, 48.dp, 22.dp)
            NextButton(connection, f.onBar2, 22.dp)
        }
        Row(Modifier.align(Alignment.CenterEnd), verticalAlignment = Alignment.CenterVertically) {
            // The desktop's order: the sync icon while a run goes, Song ID, shuffle, sleep timer, equalizer, Comfort View.
            SyncBadge()
            SongIdButton(nowPlaying, f.onBar2)
            ShuffleButton(f.onBar2)
            SleepButton(f.onBar2)
            EqualizerButton(nowPlaying, f.onBar2)
            ComfortViewButton(onComfort ?: onOpen, f.onBar2, if (onComfort != null) com.frequencies.core.Comfort.toggleLabel(comfort) else "Comfort View", onComfort != null && comfort)
        }
    }
}

/** The song (titleSmall) and "artist · station" (text2), or what is wrong, tuning or paused. */
@Composable
private fun SongLines(nowPlaying: NowPlaying, modifier: Modifier) {
    val f = MaterialTheme.freq
    Column(modifier) {
        Text(
            if (nowPlaying.hasSong) nowPlaying.title else nowPlaying.stationName,
            style = MaterialTheme.typography.titleSmall,
            color = f.onBar,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        val status = statusLine(nowPlaying)
        if (status.isNotEmpty()) Text(
            status,
            style = MaterialTheme.typography.bodySmall,
            color = if (nowPlaying.error != null) MaterialTheme.colorScheme.error else f.onBar2,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/** The star right of the song: lit in the accent when it's a favorite; the same as Now Playing's star. */
@Composable
private fun BarStar(nowPlaying: NowPlaying) {
    val library = LocalContext.current.library
    val songNow by SongNow.song.collectAsStateWithLifecycle()
    val song = songNow?.takeIf { it.stationId == nowPlaying.stationId } ?: return
    // No favorites from a music folder or iBroadcast (task 241).
    if (!SongHistory.canFavorite(library.station(nowPlaying.stationId))) return
    val history by library.history.collectAsStateWithLifecycle()
    val starred = song.key in SongHistory.favoriteKeys(history)
    val f = MaterialTheme.freq
    Box(
        Modifier
            .minimumInteractiveComponentSize()
            .clip(CircleShape)
            .clickable(role = Role.Button) { starSong(library, song, nowPlaying.stationName, !starred) },
        contentAlignment = Alignment.Center,
    ) {
        StarGlyph(starred, 16.dp, if (starred) "Unfavorite" else "Favorite", onTint = f.barAccent, offTint = f.onBar2)
    }
}

/** The round accent play/pause button, morphing between its two glyphs; a spinner while tuning in. */
@Composable
fun RoundPlayButton(nowPlaying: NowPlaying, connection: PlayerConnection, size: Dp, glyph: Dp) {
    val cs = MaterialTheme.colorScheme
    val playing = nowPlaying.playWhenReady && nowPlaying.error == null
    Box(
        Modifier
            .size(size)
            .clip(CircleShape)
            .background(cs.primary)
            .clickable(role = Role.Button, onClick = connection::togglePlay)
            // While tuning in the spinner shows, and TalkBack still hears what the button does.
            .then(if (nowPlaying.buffering) Modifier.semantics { contentDescription = if (playing) "Pause" else "Play" } else Modifier),
        contentAlignment = Alignment.Center,
    ) {
        if (nowPlaying.buffering) CircularProgressIndicator(Modifier.size(glyph), color = cs.onPrimary, strokeWidth = 2.dp)
        else PlayPauseGlyph(playing, cs.onPrimary, glyph, if (playing) "Pause" else "Play")
    }
}

/**
 * The sleep timer's button and choices (mainwindow._sleep_menu). Its
 * hands wind on press (timer_0..100, 24 ms); it stays in the accent
 * while the timer counts down.
 */
@Composable
fun SleepButton(rest: Color, size: Dp = 22.dp) {
    val say = LocalSay.current
    val ends by SleepTimer.endsAt.collectAsStateWithLifecycle()
    var open by remember { mutableStateOf(false) }
    val press = remember { MutableInteractionSource() }
    val t = rememberPressFrames(press, GlyphMotion.TIMER)
    Box {
        IconButton(onClick = { open = true }, interactionSource = press) {
            Glyph("timer", "Sleep timer", t = t, tint = if (ends != null) MaterialTheme.colorScheme.primary else rest, size = size)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            // Dev builds also offer one minute, to try the ending without waiting.
            for (minutes in (if (com.frequencies.app.BuildConfig.DEV) listOf(1) else emptyList()) + SleepTimer.CHOICES) {
                DropdownMenuItem(
                    text = { Text(SleepTimer.label(minutes)) },
                    onClick = {
                        open = false
                        SleepTimer.start(minutes)
                        say("Sleeping in ${SleepTimer.label(minutes)}.")
                    },
                )
            }
            if (ends != null) {
                HorizontalDivider()
                DropdownMenuItem(
                    text = { Text("Cancel the sleep timer") },
                    onClick = {
                        open = false
                        SleepTimer.cancel()
                        say("Sleep timer canceled.")
                    },
                )
            }
        }
    }
}

/**
 * The equalizer for this frequency (desktop "Equalizer for this station"):
 * its Station settings, on the Sound tab. The knobs slide on press.
 */
@Composable
fun EqualizerButton(nowPlaying: NowPlaying, rest: Color, size: Dp = 22.dp) {
    val library = LocalContext.current.library
    val station = library.station(nowPlaying.stationId)?.takeIf { library.isSavedId(it.id) }
    var open by remember { mutableStateOf(false) }
    val press = remember { MutableInteractionSource() }
    val t = rememberPressFrames(press, GlyphMotion.EQ)
    IconButton(onClick = { open = true }, enabled = station != null, interactionSource = press) {
        Glyph("eq", "Equalizer for this frequency", t = t, tint = if (station != null) rest else rest.copy(alpha = 0.38f), size = size)
    }
    if (open && station != null) StationSettingsDialog(station, initialTab = 1) { open = false }
}

/**
 * Song ID for this frequency (the station's "Automatically identify the
 * song playing" switch), lit while on; when a song is identified the
 * desktop's ripple runs over it and the mark fills from the middle.
 * The wide bar and Now Playing (by the song menu and the star, Android
 * batch 22) both use it; [accent] is the lit color.
 */
@Composable
fun SongIdButton(nowPlaying: NowPlaying, rest: Color, size: Dp = 22.dp, accent: Color = MaterialTheme.freq.barAccent) {
    val library = LocalContext.current.library
    val stations by library.stations.collectAsStateWithLifecycle()
    val station = stations.firstOrNull { it.id == nowPlaying.stationId } ?: return
    val on = com.frequencies.core.Comfort.songIdOn(station)
    val songNow by SongNow.song.collectAsStateWithLifecycle()
    var pulses by remember { mutableIntStateOf(0) }
    var seen by remember { mutableStateOf(songNow?.key) }
    LaunchedEffect(songNow) {
        val now = songNow?.takeIf { it.stationId == station.id }
        if (now != null && now.key != seen) pulses++
        if (now != null) seen = now.key
    }
    IconButton(onClick = {
        library.update(station.id) {
            it["shazam_auto"] = JsonPrimitive(!on)
            it["shazam_auto_set"] = JsonPrimitive(true)
        }
    }) {
        SongIdGlyph(
            on, pulses, accent, rest, size,
            if (on) "Identifying songs on this frequency" else "Identify songs on this frequency",
        )
    }
}

/**
 * Shuffle (the desktop's "shuffle" setting, Ctrl+S there): the arrows walk
 * across as it turns on or off (shuffle_0..4, 30 ms), in the accent while
 * on. The notification and Android Auto show the same state.
 */
@Composable
fun ShuffleButton(rest: Color, size: Dp = 22.dp, modifier: Modifier = Modifier) {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    val on = Shuffle.on(settings)
    val t = rememberFrames(on, GlyphMotion.SHUFFLE)
    IconToggleButton(
        checked = on,
        onCheckedChange = { want -> library.updateSettings { it[Shuffle.SETTING] = JsonPrimitive(want) } },
        modifier = modifier.semantics { stateDescription = if (on) "On" else "Off" },
    ) {
        Glyph("shuffle", "Shuffle stations", t = t, tint = if (on) MaterialTheme.colorScheme.primary else rest, size = size)
    }
}

/** Next; while shuffle is on it grows the crossing tail (next_shuffle1..4, 30 ms) and picks one at random. It stays neutral either way. */
@Composable
fun NextButton(connection: PlayerConnection, rest: Color, size: Dp = 22.dp, modifier: Modifier = Modifier) {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    val on = Shuffle.on(settings)
    val t = rememberFrames(on, GlyphMotion.NEXT_SHUFFLE)
    GlyphButton("next_shuffle", if (on) "Random frequency" else "Next frequency", { connection.next() }, modifier = modifier, tint = rest, size = size, t = t)
}

/** Comfort View (Now Playing) from the wide bar; its corners push out on press (fullscreen1–2, 28 ms). */
@Composable
private fun ComfortViewButton(onOpen: () -> Unit, rest: Color, label: String = "Comfort View", on: Boolean = false) {
    val press = remember { MutableInteractionSource() }
    val t = rememberPressFrames(press, GlyphMotion.FULLSCREEN)
    IconButton(onClick = onOpen, interactionSource = press, modifier = Modifier.testTag("comfort_toggle")) {
        Glyph("fullscreen", label, t = t, tint = if (on) MaterialTheme.colorScheme.primary else rest, size = 22.dp)
    }
}

/** The second line: what is wrong, what is happening, or who is playing; empty while it just plays. */
fun statusLine(n: NowPlaying): String = when {
    n.error != null -> n.error
    n.buffering -> "Tuning in…"
    n.hasSong && n.artist.isNotEmpty() && n.artist != n.stationName -> "${n.artist} · ${n.stationName}"
    n.hasSong -> n.stationName
    !n.playWhenReady -> "Paused"
    // Playing with no song known: nothing (user, build 170: "I'd rather it not say live").
    else -> ""
}
