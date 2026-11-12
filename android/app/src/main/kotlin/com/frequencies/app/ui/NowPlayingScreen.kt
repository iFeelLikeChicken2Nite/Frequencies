package com.frequencies.app.ui

import android.content.Intent
import android.app.Activity
import android.content.ContextWrapper
import android.os.SystemClock
import android.view.accessibility.AccessibilityManager
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.statusBarsIgnoringVisibility
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.State
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.layoutId
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.frequencies.core.Comfort
import com.frequencies.core.GlyphMotion
import kotlin.math.roundToInt
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.AssistChip
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.MutableIntState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.Library
import com.frequencies.app.artists
import com.frequencies.app.library
import com.frequencies.app.playback.SleepTimer
import com.frequencies.app.playback.SongNow
import com.frequencies.core.SongHistory
import kotlinx.coroutines.delay
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.displayCutout
import androidx.compose.runtime.produceState
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.isSpecified
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Now Playing over the moving background (comfort spec 8), with Comfort
 * View merged into it (comfort spec): the artwork slowly retracts after
 * 7 s (Settings: 5 to 20) without a touch and the lyrics (or the biography) grow to fill the
 * screen; a tap on the artwork, the small header or the corners button
 * switches back. The window's size picks the layout (spec 2): Upright on
 * a phone held up, Sideways on a phone on its side, Wide on tablets and
 * unfolded foldables (the desktop's Comfort View, cover, lyrics and
 * artist side by side, the full player bar under them). [wide] is no
 * longer read: the size decides.
 */
@Composable
fun NowPlayingScreen(nowPlaying: NowPlaying, connection: PlayerConnection, onClose: () -> Unit, modifier: Modifier = Modifier, wide: Boolean = false, sheetDrag: SheetDrag? = null) {
    val library = LocalContext.current.library
    val songNow by SongNow.song.collectAsStateWithLifecycle()
    val song = songNow?.takeIf { it.stationId == nowPlaying.stationId }
    val history by library.history.collectAsStateWithLifecycle()
    val starred = song != null && song.key in SongHistory.favoriteKeys(history)
    val onStar: (Boolean) -> Unit = { on -> song?.let { starSong(library, it, nowPlaying.stationName, on) } }
    // No favorites from a music folder or iBroadcast (task 241): no star at all.
    val canStar = SongHistory.canFavorite(library.station(nowPlaying.stationId))
    val wash = MaterialTheme.freq.wash
    val text = wash?.text?.let(::rgb) ?: MaterialTheme.freq.text
    val text2 = wash?.text2?.let(::rgb) ?: MaterialTheme.freq.text2

    ComfortBackground(modifier.fillMaxSize(), playing = nowPlaying.playWhenReady) {
        CompositionLocalProvider(LocalContentColor provides text, LocalSheetDrag provides sheetDrag, LocalCanStar provides canStar) {
            BoxWithConstraints(Modifier.fillMaxSize()) {
                val shape = Comfort.shape(maxWidth.value, maxHeight.value)
                ComfortNowPlaying(nowPlaying, connection, song, starred, onStar, onClose, text2, shape, maxWidth.value)
            }
        }
    }
}

private fun rgb(v: Int) = androidx.compose.ui.graphics.Color(0xFF000000 or v.toLong())

/** The line under the song: what is wrong, tuning in, or the artist. */
private fun secondLine(n: NowPlaying): String = when {
    n.error != null || n.buffering -> statusLine(n)
    n.hasSong && n.artist.isNotEmpty() && n.artist != n.stationName -> n.artist
    else -> statusLine(n)
}

@Composable
private fun SongTitle(nowPlaying: NowPlaying, song: SongNow.Song?, starred: Boolean, onStar: (Boolean) -> Unit, text2: androidx.compose.ui.graphics.Color, modifier: Modifier = Modifier) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            val text = if (nowPlaying.hasSong) nowPlaying.title else nowPlaying.stationName
            val style = MaterialTheme.typography.headlineSmall
            FitTitle(text, style, rememberTitleFit(text, style))
            Text(
                secondLine(nowPlaying),
                style = MaterialTheme.typography.bodyMedium,
                color = if (nowPlaying.error != null) MaterialTheme.colorScheme.error else text2,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (song != null) {
            SongMenu(song, starred, text2, onStar)
            StarButton(starred, onStar)
        }
    }
}

/** Which [Comfort.TITLE_FITS] step a title is on; starts over for a new title, size or way up. */
@Composable
private fun rememberTitleFit(text: String, style: androidx.compose.ui.text.TextStyle): MutableIntState {
    val orientation = androidx.compose.ui.platform.LocalConfiguration.current.orientation
    return remember(text, style, orientation) { mutableIntStateOf(0) }
}

/**
 * The song's title, fitted (user, batch 24): two lines, then three, then a
 * size smaller, before it is cut off with "…". Each step is taken when the
 * last one's layout still overflowed.
 */
@Composable
private fun FitTitle(text: String, style: androidx.compose.ui.text.TextStyle, step: MutableIntState) {
    val fit = Comfort.TITLE_FITS[step.intValue]
    Text(
        text,
        style = if (fit.textScale == 1f) style else style.copy(
            fontSize = if (style.fontSize.isSpecified) style.fontSize * fit.textScale else style.fontSize,
            lineHeight = if (style.lineHeight.isSpecified) style.lineHeight * fit.textScale else style.lineHeight,
        ),
        maxLines = fit.lines,
        overflow = TextOverflow.Ellipsis,
        onTextLayout = { result -> step.intValue = Comfort.nextTitleFit(step.intValue, result.hasVisualOverflow) },
    )
}

/** A screen reader (TalkBack, or braille) is on: no automatic layout changes (comfort spec 5b). */
@Composable
private fun rememberScreenReader(): Boolean {
    val context = LocalContext.current
    val manager = remember { context.getSystemService(AccessibilityManager::class.java) }
    fun read(): Boolean = manager != null && manager.isEnabled && (
        manager.isTouchExplorationEnabled ||
            manager.getEnabledAccessibilityServiceList(
                android.accessibilityservice.AccessibilityServiceInfo.FEEDBACK_SPOKEN or android.accessibilityservice.AccessibilityServiceInfo.FEEDBACK_BRAILLE,
            ).isNotEmpty()
        )
    var on by remember { mutableStateOf(read()) }
    DisposableEffect(manager) {
        val listener = AccessibilityManager.AccessibilityStateChangeListener { on = read() }
        val touch = AccessibilityManager.TouchExplorationStateChangeListener { on = read() }
        manager?.addAccessibilityStateChangeListener(listener)
        manager?.addTouchExplorationStateChangeListener(touch)
        onDispose {
            manager?.removeAccessibilityStateChangeListener(listener)
            manager?.removeTouchExplorationStateChangeListener(touch)
        }
    }
    return on
}

/** The page last chosen on a station that isn't talk radio, for the whole time the app runs (build 198). */
private object PageMemory {
    @Volatile var nonTalk: String = "lyrics"
}

/** Now Playing dragged down by its top (user, build 198): the reverse of dragging the player bar up. */
class SheetDrag(val onDrag: (Float) -> Unit, val onDragEnd: (Float) -> Unit)

val LocalSheetDrag = androidx.compose.runtime.staticCompositionLocalOf<SheetDrag?> { null }

/** Dragging down here pulls Now Playing down after the finger; let go and it snaps open or shut. */
@Composable
private fun Modifier.dragsSheetDown(): Modifier {
    val drag = LocalSheetDrag.current ?: return this
    val state = androidx.compose.foundation.gestures.rememberDraggableState { drag.onDrag(it) }
    return this.then(
        Modifier.draggable(
            state,
            androidx.compose.foundation.gestures.Orientation.Vertical,
            onDragStopped = { velocity -> drag.onDragEnd(velocity) },
        ),
    )
}

/** The idle clock's time (uptime ms); UI tests hand in the test clock. */
val LocalComfortClock = androidx.compose.runtime.staticCompositionLocalOf<() -> Long> { { SystemClock.uptimeMillis() } }

private fun android.content.Context.findActivity(): Activity? {
    var c: android.content.Context? = this
    while (c is ContextWrapper) {
        if (c is Activity) return c
        c = c.baseContext
    }
    return null
}

/**
 * Everything the three layouts share: Out or Comfort with its two
 * progress values, the page, the toggle, and the artist photo the band
 * logo sits on.
 */
private class ComfortParts(
    val nowPlaying: NowPlaying,
    val connection: PlayerConnection,
    val song: SongNow.Song?,
    val starred: Boolean,
    val onStar: (Boolean) -> Unit,
    val onClose: () -> Unit,
    val text2: Color,
    val comfort: Boolean,
    val toggle: () -> Unit,
    val page: String,
    val setPage: (String) -> Unit,
    /** 0 = Out, 1 = Comfort; 0 = Lyrics, 1 = Artist. Read in the layout and draw phases only. */
    val progress: State<Float>,
    val pageProgress: State<Float>,
    /** The artist photo is showing (not the cover), and the luminance of its bottom band. */
    val photoShown: Boolean,
    val tone: Double?,
    val onPhoto: (android.graphics.Bitmap?) -> Unit,
    /** The Artist page's height (px) for an artist, as it is laid out (task 243). */
    val onArtistHeight: (String, Int) -> Unit,
)

/**
 * The phone's Now Playing with Comfort View inside it (comfort spec 3, 4,
 * 5 and 5b): one state, Out or Comfort, with one progress value driving
 * the artwork, the title and the body together in the layout and draw
 * phases (nothing recomposes per frame). The controls never hide; only
 * the status bar does, in Comfort.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ComfortNowPlaying(
    nowPlaying: NowPlaying,
    connection: PlayerConnection,
    song: SongNow.Song?,
    starred: Boolean,
    onStar: (Boolean) -> Unit,
    onClose: () -> Unit,
    text2: Color,
    shape: Comfort.Shape,
    widthDp: Float,
) {
    val context = LocalContext.current
    val library = context.library
    val settings by library.settings.collectAsStateWithLifecycle()
    val stations by library.stations.collectAsStateWithLifecycle()
    val station = stations.firstOrNull { it.id == nowPlaying.stationId }
    // Android batch 22 (user): Comfort comes in on its own only with Song ID on, or on a
    // marked talk station with its own bio (then on the Artist page).
    val songIdOn = Comfort.songIdOn(station)
    val talkBio = remember(station) { station != null && runCatching { context.artists.overrides.talkWithBio(station) }.getOrDefault(false) }
    val autoPage = Comfort.autoPage(songIdOn, talkBio)
    val auto = Comfort.auto(settings) && autoPage != Comfort.AutoPage.NONE
    val idleMs = Comfort.idleMs(settings)
    val keepAwake = Comfort.keepAwake(settings)
    val reader = rememberScreenReader()
    val calm = LocalCalm.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val lifeState by lifecycle.currentStateFlow.collectAsState()
    val inFront = lifeState.isAtLeast(Lifecycle.State.RESUMED)

    // Out (false) or Comfort (true), and which page: "lyrics" or "artist". Both survive rotation.
    var comfort by rememberSaveable { mutableStateOf(false) }
    var page by rememberSaveable { mutableStateOf(PageMemory.nonTalk) }
    // Build 198 (user): a talk station opens on Artist (the stream's bio); the next station that isn't
    // talk radio goes back to the page last chosen on one. Only a new station (or opening) picks it.
    val talk = remember(station) { station != null && (station.talkRadio || runCatching { context.artists.overrides.isTalkStation(station) }.getOrDefault(false)) }
    var pagedFor by rememberSaveable { mutableStateOf<String?>(null) }
    LaunchedEffect(nowPlaying.stationId, talk) {
        val key = "${nowPlaying.stationId}|$talk"
        if (key == pagedFor) return@LaunchedEffect
        pagedFor = key
        page = Comfort.pageFor(talk, PageMemory.nonTalk)
    }
    val setPage: (String) -> Unit = { p ->
        page = p
        if (!talk) PageMemory.nonTalk = p
    }
    // Build 198 (user): the artwork retracts only once the page being viewed has something to show.
    val lyricsNow by SongNow.lyrics.collectAsStateWithLifecycle()
    val artistNow by com.frequencies.app.playback.ArtistNow.state.collectAsStateWithLifecycle()
    val viewed = if (autoPage == Comfort.AutoPage.ARTIST && !comfort) "artist" else page
    // Task 243 (user): a biography too short to fill most of the screen keeps the artist's photo big
    // (as a tap on it would). Measured: the Artist page's height against the room it has in Comfort.
    val density = LocalDensity.current
    var artistHeight by remember { mutableStateOf<Pair<String, Int>?>(null) }
    var heroHeight by remember { mutableIntStateOf(0) }
    val panelNow = artistNow.panel?.takeIf { artistNow.stationId == nowPlaying.stationId }
    // Measured on the upright layout only; sideways and wide go by whether there is a biography at all.
    val heroPx = if (shape == Comfort.Shape.UPRIGHT) heroHeight else 0
    val measured = heroPx > 0 && panelNow != null && artistHeight?.first == panelNow.key
    val bioFills = panelNow != null && panelNow.bio.isNotEmpty() &&
        (heroPx == 0 || (measured && Comfort.bioFills(artistHeight!!.second / density.density, heroPx / density.density)))
    val ready = Comfort.viewedReady(
        viewed,
        lyricsFound = lyricsNow is SongNow.Lyrics.Found,
        bioShown = bioFills,
    )
    // On the Artist page with a short (or no) biography, the photo comes out big by itself.
    val shortBio = measured && !artistNow.busy && !bioFills
    // Only on arriving at the page or a new artist: a tap that shrinks it again is the user's to make.
    // Build 201: whether or not the art retracts by itself (it does only with Song ID on), as the user asked.
    LaunchedEffect(page, panelNow?.key, shortBio) {
        if (comfort && page == "artist" && shortBio && !reader) comfort = false
    }
    val toggle = { comfort = Comfort.next(comfort, Comfort.Event.TAP, auto, reader) }
    val clock = LocalComfortClock.current

    // The idle clock: any touch anywhere on the screen, scrolling the lyrics included, starts it again.
    val lastTouch = remember { longArrayOf(clock()) }
    val held = remember { booleanArrayOf(false) }
    LaunchedEffect(comfort, auto, reader, inFront, idleMs, autoPage, ready) {
        if (!Comfort.timing(comfort, auto, reader, inFront)) return@LaunchedEffect
        // Nothing to show yet on the page being viewed: stay out; the delay counts from when it loads.
        if (!ready) return@LaunchedEffect
        lastTouch[0] = clock()
        while (true) {
            if (held[0]) lastTouch[0] = clock()
            val left = Comfort.idleLeft(clock(), lastTouch[0], idleMs)
            if (left == 0L) {
                comfort = Comfort.next(comfort, Comfort.Event.IDLE, auto, reader)
                if (comfort && autoPage == Comfort.AutoPage.ARTIST) page = "artist"
                break
            }
            delay(minOf(left, 1000L))
        }
    }
    // A new song (title or station): the artwork comes back out.
    val songKey = "${nowPlaying.stationId}|${if (nowPlaying.hasSong) nowPlaying.title else ""}"
    var seenSong by rememberSaveable { mutableStateOf(songKey) }
    LaunchedEffect(songKey) {
        if (songKey == seenSong) return@LaunchedEffect
        seenSong = songKey
        lastTouch[0] = clock()
        comfort = Comfort.next(comfort, Comfort.Event.NEW_SONG, auto, reader)
    }

    // Only the status bar hides in Comfort (a swipe brings it back for a moment); never the controls.
    val view = LocalView.current
    DisposableEffect(comfort) {
        val window = view.context.findActivity()?.window
        val bars = window?.let { WindowCompat.getInsetsController(it, view) }
        if (bars != null) {
            if (comfort) {
                bars.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                bars.hide(WindowInsetsCompat.Type.statusBars())
            } else bars.show(WindowInsetsCompat.Type.statusBars())
        }
        onDispose { bars?.show(WindowInsetsCompat.Type.statusBars()) }
    }
    // "Keep the screen on in Comfort": only while in Comfort and playing.
    val awake = Comfort.keepScreenOn(keepAwake, comfort, nowPlaying.playWhenReady)
    DisposableEffect(awake) {
        view.keepScreenOn = awake
        onDispose { view.keepScreenOn = false }
    }

    val motion = if (calm) snap<Float>() else tween(Comfort.MOVE_MS, easing = FastOutSlowInEasing)
    val progress = animateFloatAsState(if (comfort) 1f else 0f, motion, label = "comfort")
    val pageProgress = animateFloatAsState(if (page == "artist") 1f else 0f, motion, label = "page")

    // The artist photo (not the cover) is up: the band logo can sit on it, outlined against its bottom band.
    var photo by remember { mutableStateOf<android.graphics.Bitmap?>(null) }
    val tone by produceState<Double?>(null, photo) {
        value = photo?.let { b -> withContext(Dispatchers.Default) { bottomTone(b) } }
    }

    val parts = ComfortParts(
        nowPlaying, connection, song, starred, onStar, onClose, text2,
        comfort, toggle, page, setPage, progress, pageProgress,
        photo != null, tone, { photo = it },
        { key, px -> artistHeight = key to px },
    )

    val root = Modifier
        .fillMaxSize()
        // The status bar's room is kept while it hides, so nothing jumps.
        .windowInsetsPadding(WindowInsets.statusBarsIgnoringVisibility)
        .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal + WindowInsetsSides.Bottom))
        .then(if (shape == Comfort.Shape.SIDEWAYS) Modifier.padding(cutoutClearance()) else Modifier)
        .pointerInput(Unit) {
            awaitPointerEventScope {
                while (true) {
                    val event = awaitPointerEvent(PointerEventPass.Initial)
                    lastTouch[0] = clock()
                    held[0] = event.changes.any { it.pressed }
                }
            }
        }
        .testTag("comfort_root")

    when (shape) {
        Comfort.Shape.UPRIGHT -> Column(root) {
            ComfortTopBar(nowPlaying, song, starred, onStar, onClose, comfort, toggle, text2)
            ComfortHero(parts, Comfort.uprightCap(widthDp), Modifier.weight(1f).fillMaxWidth().onSizeChanged { heroHeight = it.height })
            PageTabs(page, setPage, text2)
            ControlsRow(nowPlaying, connection, text2)
        }
        Comfort.Shape.SIDEWAYS -> SidewaysComfort(parts, root)
        Comfort.Shape.WIDE -> WideComfort(parts, root)
    }
}

/** The extra room on the side with the camera cutout, so sideways content stays 40 dp clear of it (spec 4). */
@Composable
private fun cutoutClearance(): androidx.compose.foundation.layout.PaddingValues {
    val density = LocalDensity.current
    val direction = LocalLayoutDirection.current
    val cut = WindowInsets.displayCutout
    fun extra(px: Int) = if (px <= 0) 0.dp else (Comfort.Sideways.CUTOUT.dp - with(density) { px.toDp() }).coerceAtLeast(0.dp)
    return androidx.compose.foundation.layout.PaddingValues.Absolute(
        left = extra(cut.getLeft(density, direction)),
        right = extra(cut.getRight(density, direction)),
    )
}

/**
 * Lyrics | Artist, centered (user, build 170: "center these two buttons
 * and make them look nicer"): a round bg2 track with a 1 dp line, the
 * chosen page on a pill in the accent's tint that slides across.
 */
@Composable
private fun PageTabs(page: String, setPage: (String) -> Unit, text2: Color, modifier: Modifier = Modifier, padding: androidx.compose.ui.unit.Dp = 34.dp) {
    val pages = listOf("lyrics" to "Lyrics", "artist" to "Artist")
    val f = MaterialTheme.freq
    val accent = MaterialTheme.colorScheme.primary
    val chosen = pages.indexOfFirst { it.first == page }.coerceAtLeast(0)
    val calm = LocalCalm.current
    val slide by animateFloatAsState(chosen.toFloat(), if (calm) tween(0) else tween(220), label = "pageTab")
    val segment = 104.dp
    Box(modifier.fillMaxWidth().padding(horizontal = padding, vertical = 6.dp).testTag("comfort_tabs"), contentAlignment = Alignment.Center) {
        Box(
            Modifier
                .clip(RoundedCornerShape(50))
                .background(f.bg2.copy(alpha = 0.7f))
                .border(1.dp, f.line, RoundedCornerShape(50))
                .padding(3.dp)
                .selectableGroup(),
        ) {
            // The pill under the chosen page.
            Box(
                Modifier
                    .offset(x = segment * slide)
                    .size(segment, 34.dp)
                    .clip(RoundedCornerShape(50))
                    .background(accent.copy(alpha = 0.22f))
                    .border(1.dp, accent.copy(alpha = 0.55f), RoundedCornerShape(50)),
            )
            Row {
                pages.forEachIndexed { i, (key, label) ->
                    val on = i == chosen
                    Box(
                        Modifier
                            .size(segment, 34.dp)
                            .clip(RoundedCornerShape(50))
                            .selectable(selected = on, onClick = { setPage(key) }, role = Role.Tab),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            label,
                            style = MaterialTheme.typography.labelLarge,
                            fontSize = 13.sp,
                            fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal,
                            color = if (on) f.text else text2,
                            maxLines = 1,
                        )
                    }
                }
            }
        }
    }
}

/** Close, the station centered (and the sleep timer's time left under it), the sleep timer. */
@Composable
private fun ComfortTopBar(
    nowPlaying: NowPlaying,
    song: SongNow.Song?,
    starred: Boolean,
    onStar: (Boolean) -> Unit,
    onClose: () -> Unit,
    comfort: Boolean,
    toggle: () -> Unit,
    text2: Color,
    modifier: Modifier = Modifier,
) {
    Row(modifier.fillMaxWidth().height(Comfort.Upright.TOP_BAR.dp).dragsSheetDown().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        GlyphButton("collapse", "Close", onClose, tint = text2)
        Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                nowPlaying.stationName,
                style = MaterialTheme.typography.bodySmall,
                color = text2,
                textAlign = TextAlign.Center,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            SleepLeft()
        }
        SaveNowButton(nowPlaying.stationId, text2)
        SleepButton(text2, 22.dp)
        // No Comfort View button (user, build 170): Comfort comes by itself when idle, and the song menu sits by the star.
    }
}

/**
 * Save (user, build 194): a + with "Save" while the frequency playing isn't
 * in Saved Frequencies yet (one played from Browse or the add search). A tap
 * saves it as Browse's + does; the + turns into a green check with "Saved!"
 * and the button goes away 10 seconds later.
 */
@Composable
private fun SaveNowButton(stationId: String?, rest: Color) {
    val library = LocalContext.current.library
    val say = LocalSay.current
    val stations by library.stations.collectAsStateWithLifecycle()
    val station = remember(stationId, stations) { library.station(stationId) }
    val saved = station != null && stations.any { it.url.trim().equals(station.url.trim(), ignoreCase = true) }
    var justSaved by remember(stationId) { mutableStateOf(false) }
    LaunchedEffect(justSaved) {
        if (justSaved) { delay(10_000); justSaved = false }
    }
    if (station == null || (saved && !justSaved)) return
    val done by animateFloatAsState(if (justSaved) 1f else 0f, tween(350, easing = FastOutSlowInEasing), label = "saved")
    val green = Color(0xFF3DDC84)
    Row(
        Modifier
            .clip(RoundedCornerShape(50))
            .clickable(enabled = !justSaved, onClickLabel = "Save ${station.displayName}", role = Role.Button) {
                val added = library.add(listOf(station))
                justSaved = true
                say(if (added.isEmpty()) "${station.displayName} is already in Saved Frequencies." else "Saved ${station.displayName}.")
            }
            .padding(horizontal = 8.dp, vertical = 6.dp)
            .testTag("save_now"),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(20.dp), contentAlignment = Alignment.Center) {
            // The + turns a quarter and fades as the check grows in.
            Icon(
                Icons.Filled.Add, null,
                tint = rest,
                modifier = Modifier.fillMaxSize().graphicsLayer { rotationZ = 90f * done; alpha = 1f - done; scaleX = 1f - 0.4f * done; scaleY = 1f - 0.4f * done },
            )
            Icon(
                Icons.Filled.Check, null,
                tint = green,
                modifier = Modifier.fillMaxSize().graphicsLayer { alpha = done; scaleX = 0.4f + 0.6f * done; scaleY = 0.4f + 0.6f * done },
            )
        }
        Text(
            if (justSaved) "Saved!" else "Save",
            style = MaterialTheme.typography.labelMedium,
            color = if (justSaved) green else rest,
            modifier = Modifier.padding(start = 4.dp),
            maxLines = 1,
        )
    }
}

/** "Sleeping in 12 minutes" in the accent under the station while the timer runs. */
@Composable
private fun SleepLeft() {
    val ends by SleepTimer.endsAt.collectAsStateWithLifecycle()
    if (ends == null) return
    var left by remember { mutableLongStateOf(SleepTimer.remainingMs() ?: 0) }
    LaunchedEffect(ends) {
        while (true) {
            left = SleepTimer.remainingMs() ?: 0
            delay(1000)
        }
    }
    val minutes = (left + 59_999) / 60_000
    Text(
        if (minutes <= 1) "Sleeping in under a minute" else "Sleeping in $minutes minutes",
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.primary,
        maxLines = 1,
    )
}

/**
 * The artwork, or on the Artist page the artist's photo with the band's
 * logo (or name) on it; a tap switches Out and Comfort. Drawn at its Out
 * size; the layouts scale it, so pictures never reload while it moves.
 * The logo shows on the Artist page Out only (in Comfort the photo is too
 * small to read it; the name beside it takes over).
 */
@Composable
private fun ComfortArt(p: ComfortParts, modifier: Modifier = Modifier, corner: (Float) -> Float) {
    val artist = artistNameFor(p.nowPlaying.stationId)
    Box(
        modifier
            .graphicsLayer {
                shape = RoundedCornerShape(corner(p.progress.value).dp)
                clip = true
            }
            .clickable(onClickLabel = Comfort.toggleLabel(p.comfort), role = Role.Button) { p.toggle() }
            .testTag("comfort_art"),
    ) {
        Crossfade(p.page, animationSpec = tween(Comfort.MOVE_MS), label = "art") { shown ->
            val cover: @Composable () -> Unit = { StationArt(p.nowPlaying.artworkUri, p.nowPlaying.stationName, Modifier.fillMaxSize(), corner = 0.dp) }
            if (shown == "artist") ArtistPhoto(p.nowPlaying.stationId, Modifier.fillMaxSize(), onPhoto = p.onPhoto, fallback = cover) else cover()
        }
        PhotoLogo(
            artist,
            p.photoShown,
            p.tone,
            Modifier.fillMaxSize().graphicsLayer { alpha = p.pageProgress.value * (1f - p.progress.value) },
        )
    }
}

/** The song's title and the artist (the band's logo in its place), or on the Artist page the logo (or name) alone. */
@Composable
private fun TitleBlock(p: ComfortParts, shown: String) {
    val nowPlaying = p.nowPlaying
    val artist = artistNameFor(nowPlaying.stationId)
    if (shown == "artist") {
        if (artist != null) ArtistName(artist, boxDp = 34f, style = MaterialTheme.typography.headlineSmall, maxLines = 2)
        return
    }
    Column {
        val text = if (nowPlaying.hasSong) nowPlaying.title else nowPlaying.stationName
        val style = MaterialTheme.typography.headlineSmall
        // A long title takes a third line and then a smaller size before it is cut off; the band logo shrinks to make room.
        val fit = rememberTitleFit(text, style)
        FitTitle(text, style, fit)
        val line = secondLine(nowPlaying)
        if (nowPlaying.error == null && !nowPlaying.buffering && nowPlaying.hasSong && line == nowPlaying.artist) {
            ArtistName(line, boxDp = 48f * Comfort.TITLE_FITS[fit.intValue].logoScale, style = MaterialTheme.typography.bodyMedium, color = p.text2, maxLines = 1)
        } else {
            Text(
                line,
                style = MaterialTheme.typography.bodyMedium,
                color = if (nowPlaying.error != null) MaterialTheme.colorScheme.error else p.text2,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/** The lyrics or the biography, faded out at the bottom edge (18%) and, for the lyrics only, the top; cross-fading with the page. */
@Composable
private fun ComfortBody(p: ComfortParts, modifier: Modifier = Modifier) {
    val u = Comfort.Upright
    Box(
        modifier
            .testTag("comfort_body")
            .graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }
            .drawWithContent {
                drawContent()
                // The Artist page keeps its top edge sharp, so the genres and the start of the bio never fade (user, build 170).
                val top = Color.Black.copy(alpha = p.pageProgress.value.coerceIn(0f, 1f))
                drawRect(
                    Brush.verticalGradient(
                        0f to top, u.FADE to Color.Black, 1f - u.FADE to Color.Black, 1f to Color.Transparent,
                    ),
                    blendMode = BlendMode.DstIn,
                )
            },
    ) {
        Crossfade(p.page, animationSpec = tween(Comfort.MOVE_MS), label = "body") { shown ->
            if (shown == "artist") ArtistPane(p.nowPlaying.stationId, Modifier.fillMaxSize().padding(horizontal = 24.dp), header = false, onContentHeight = p.onArtistHeight)
            else LyricsPane(Modifier.fillMaxSize().padding(horizontal = 20.dp))
        }
    }
}

/**
 * Upright (spec 3): the artwork (or the artist's photo), the title block,
 * the star and the body (lyrics or biography), placed between the two
 * ends of the upright layout by progress (0 = Out, 1 = Comfort) and
 * pageProgress (0 = Lyrics, 1 = Artist). Positions, sizes and the title's
 * size are read in the layout and draw phases only. [cap]: the cover's
 * largest side (290, or 420 on a Medium-wide window).
 */
@Composable
private fun ComfortHero(p: ComfortParts, cap: Float, modifier: Modifier = Modifier) {
    val u = Comfort.Upright
    val label = Comfort.toggleLabel(p.comfort)
    val progress = p.progress
    val pageProgress = p.pageProgress
    // The art is drawn at its Out size and scaled, so pictures never reload while it moves.
    var artScale = 1f
    Layout(
        modifier = modifier,
        content = {
            ComfortArt(p, Modifier.layoutId("art").dragsSheetDown()) { t -> Comfort.Upright.lerp(u.CORNER_OUT, u.CORNER_COMFORT, t) / artScale.coerceAtLeast(0.01f) }
            Box(
                Modifier
                    .layoutId("title")
                    .dragsSheetDown()
                    .then(if (p.comfort) Modifier.clickable(onClickLabel = label, role = Role.Button) { p.toggle() } else Modifier),
            ) {
                Crossfade(p.page, animationSpec = tween(Comfort.MOVE_MS), label = "title") { shown -> TitleBlock(p, shown) }
            }
            Row(Modifier.layoutId("star").width(144.dp).dragsSheetDown(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.End) {
                // Song ID, the song's menu, then its star (Song ID: Android batch 22, user); the star
                // favorites the song, so none of them is on the Artist page. Song ID shows with no song too.
                if (p.page != "artist") {
                    SongIdButton(p.nowPlaying, p.text2, 22.dp, MaterialTheme.colorScheme.primary)
                    if (p.song != null) {
                        SongMenu(p.song, p.starred, p.text2, p.onStar)
                        StarButton(p.starred, p.onStar)
                    }
                }
            }
            ComfortBody(p, Modifier.layoutId("body"))
        },
    ) { measurables, constraints ->
        val w = constraints.maxWidth
        val h = constraints.maxHeight
        val pr = progress.value
        val a = pageProgress.value
        fun px(dp: Float) = dp * density
        val wDp = w / density
        val hDp = h / density
        val lerp = Comfort.Upright::lerp

        // The two ends of the art.
        val artOut = u.artOut(wDp, hDp, cap)
        val artSize = lerp(artOut, u.ART_COMFORT, pr)
        val artX = lerp((wDp - artOut) / 2f, u.ART_X_COMFORT, pr)
        val artY = lerp(u.ART_TOP_OUT, u.ART_TOP_COMFORT, pr)
        artScale = artSize / artOut
        val artSide = px(artOut).roundToInt().coerceAtLeast(1)
        val art = measurables.first { it.layoutId == "art" }.measure(Constraints.fixed(artSide, artSide))

        // The title: measured at its Out width, scaled from 22 sp to 16 sp.
        val starSide = px(48f).roundToInt()
        // Song ID, the menu and the star side by side.
        val slotW = px(144f).roundToInt()
        val titleXOut = maxOf(u.TITLE_X_OUT, (wDp - artOut) / 2f)
        val titleX = lerp(titleXOut, u.TITLE_X_COMFORT, pr)
        val titleWidthOut = (w - px(titleXOut) - px(22f) - slotW).roundToInt().coerceAtLeast(1)
        val title = measurables.first { it.layoutId == "title" }.measure(Constraints(maxWidth = titleWidthOut, maxHeight = (h / 3).coerceAtLeast(1)))
        val titleScale = lerp(1f, u.TITLE_SP_COMFORT / u.TITLE_SP_OUT, pr)
        val titleOutY = u.ART_TOP_OUT + artOut + 12f
        val titleComfortY = u.ART_TOP_COMFORT + (u.ART_COMFORT - title.height / density * titleScale) / 2f
        val titleY = lerp(titleOutY, titleComfortY, pr)
        // On the Artist page Out the photo carries the logo, so no title block under it.
        val titleAlpha = lerp(1f, pr, a)

        val star = measurables.first { it.layoutId == "star" }.measure(Constraints(maxWidth = slotW, maxHeight = starSide))
        val starX = lerp(wDp - 22f - 144f, wDp - 60f - 120f, pr)
        val starY = lerp(titleOutY + (title.height / density - 48f) / 2f, u.ART_TOP_COMFORT + (u.ART_COMFORT - 48f) / 2f, pr)

        // The body: under the title (or the photo) Out, under the small header in Comfort, down to the tabs.
        val bodyOutTop = lerp(titleOutY + title.height / density + 8f, u.ART_TOP_OUT + artOut + 8f, a)
        val bodyTop = lerp(bodyOutTop, u.BODY_TOP_COMFORT, pr)
        val bodyH = (h - px(bodyTop)).roundToInt().coerceAtLeast(0)
        val body = measurables.first { it.layoutId == "body" }.measure(Constraints.fixed(w, bodyH))

        layout(w, h) {
            body.place(0, px(bodyTop).roundToInt())
            art.placeWithLayer(px(artX).roundToInt(), px(artY).roundToInt()) {
                scaleX = artScale
                scaleY = artScale
                transformOrigin = TransformOrigin(0f, 0f)
            }
            title.placeWithLayer(px(titleX).roundToInt(), px(titleY).roundToInt()) {
                alpha = titleAlpha
                scaleX = titleScale
                scaleY = titleScale
                transformOrigin = TransformOrigin(0f, 0f)
            }
            star.placeWithLayer(px(starX).roundToInt(), px(starY).roundToInt()) { alpha = 1f - a }
        }
    }
}

/**
 * A phone on its side (spec 4). Out: the cover (or photo) on the left;
 * on the right the top bar, the title, the lyrics or biography, the tabs
 * and the five-slot controls. Comfort: a 150 dp column on the left
 * (Close, a 118 dp cover with the title under it, a 3 x 2 grid of Star,
 * Shuffle, Sleep timer, Equalizer, Comfort toggle and menu), the lyrics
 * or biography in the middle with the tabs under them, Previous, Play and
 * Next stacked on the right. The same state, timer and motion as upright:
 * the cover and the body move with progress, the two sets of controls
 * cross-fade (each only placed, so only tappable, while it shows).
 */
@Composable
private fun SidewaysComfort(p: ComfortParts, modifier: Modifier) {
    val s = Comfort.Sideways
    val progress = p.progress
    val pageProgress = p.pageProgress
    var artScale = 1f
    Layout(
        modifier = modifier,
        content = {
            ComfortArt(p, Modifier.layoutId("art")) { t -> Comfort.Upright.lerp(8f, 6f, t) / artScale.coerceAtLeast(0.01f) }
            ComfortTopBar(p.nowPlaying, p.song, p.starred, p.onStar, p.onClose, p.comfort, p.toggle, p.text2, Modifier.layoutId("bar"))
            Box(Modifier.layoutId("titleOut")) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f)) { TitleBlock(p, "lyrics") }
                    SongIdButton(p.nowPlaying, p.text2, 22.dp, MaterialTheme.colorScheme.primary)
                    if (p.song != null) {
                        SongMenu(p.song, p.starred, p.text2, p.onStar)
                        StarButton(p.starred, p.onStar)
                    }
                }
            }
            Box(
                Modifier
                    .layoutId("titleComfort")
                    .clickable(onClickLabel = Comfort.toggleLabel(p.comfort), role = Role.Button) { p.toggle() },
            ) {
                Crossfade(p.page, animationSpec = tween(Comfort.MOVE_MS), label = "title") { shown -> ScaledTitle(p, shown) }
            }
            ComfortBody(p, Modifier.layoutId("body"))
            PageTabs(p.page, p.setPage, p.text2, Modifier.layoutId("tabs"), padding = 12.dp)
            Box(Modifier.layoutId("row")) { ControlsRow(p.nowPlaying, p.connection, p.text2) }
            Box(Modifier.layoutId("close")) { GlyphButton("collapse", "Close", p.onClose, tint = p.text2) }
            Column(Modifier.layoutId("grid")) {
                Row {
                    Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) { if (p.song != null) StarButton(p.starred, p.onStar) }
                    Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) { ShuffleButton(p.text2, 22.dp) }
                    Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) { SleepButton(p.text2, 22.dp) }
                }
                Row {
                    Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) { EqualizerButton(p.nowPlaying, p.text2, 22.dp) }
                    Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) { SongMenu(p.song, p.starred, p.text2, p.onStar) }
                    Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) { SongIdButton(p.nowPlaying, p.text2, 22.dp, MaterialTheme.colorScheme.primary) }
                }
            }
            Column(
                Modifier.layoutId("transport").width(s.TRANSPORT.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(s.GAP.dp),
            ) {
                GlyphButton("prev", "Previous frequency", { p.connection.previous() }, tint = p.text2, size = 26.dp)
                RoundPlayButton(p.nowPlaying, p.connection, s.PLAY.dp, 26.dp)
                NextButton(p.connection, p.text2, 26.dp)
            }
        },
    ) { measurables, constraints ->
        val w = constraints.maxWidth
        val h = constraints.maxHeight
        val pr = progress.value
        val a = pageProgress.value
        fun px(dp: Float) = (dp * density).roundToInt()
        fun m(id: String) = measurables.first { it.layoutId == id }
        val wDp = w / density
        val hDp = h / density
        val lerp = Comfort.Upright::lerp

        // Out: the cover on the left, centered up and down; the right column beside it.
        val artOut = s.artOut(wDp, hDp)
        val artC = s.artComfort(hDp)
        val colX = 16f + artOut + 24f
        val colW = (wDp - colX - 8f).coerceAtLeast(48f)
        // Comfort: the left column, the transport on the right, the middle between them.
        val leftX = 8f
        val midX = leftX + s.COLUMN + 16f
        val transportX = wDp - 8f - s.TRANSPORT
        val midW = (transportX - 8f - midX).coerceAtLeast(48f)

        artScale = lerp(artOut, artC, pr) / artOut
        val artSide = px(artOut).coerceAtLeast(1)
        val art = m("art").measure(Constraints.fixed(artSide, artSide))
        val artX = lerp(16f, leftX + (s.COLUMN - artC) / 2f, pr)
        val artY = lerp((hDp - artOut) / 2f, 48f, pr)

        val bar = m("bar").measure(Constraints.fixed(px(colW + 8f), px(Comfort.Upright.TOP_BAR)))
        val row = m("row").measure(Constraints(minWidth = px(colW), maxWidth = px(colW)))
        val tabsWidth = lerp(colW, midW, pr)
        val tabs = m("tabs").measure(Constraints(minWidth = px(tabsWidth), maxWidth = px(tabsWidth)))
        val titleOut = m("titleOut").measure(Constraints(maxWidth = px(colW - 8f), maxHeight = (h / 3).coerceAtLeast(1)))
        val titleComfort = m("titleComfort").measure(Constraints(maxWidth = px(s.COLUMN), maxHeight = px(hDp - 48f - artC - 6f - 96f - 8f).coerceAtLeast(0)))
        val close = m("close").measure(Constraints())
        val grid = m("grid").measure(Constraints())
        val transport = m("transport").measure(Constraints())

        val rowH = row.height / density
        val tabsH = tabs.height / density
        val tabsOutY = hDp - rowH - tabsH
        val tabsComfortY = hDp - tabsH - 4f
        val tabsX = lerp(colX, midX, pr)
        val tabsY = lerp(tabsOutY, tabsComfortY, pr)

        // The body: under the title (the top bar on the Artist page) Out; the full middle in Comfort.
        val titleOutH = titleOut.height / density
        val bodyOutTop = Comfort.Upright.TOP_BAR + titleOutH * (1f - a) + 4f
        val bodyTop = lerp(bodyOutTop, 8f, pr)
        val bodyX = lerp(colX, midX, pr)
        val bodyW = lerp(colW, midW, pr)
        val bodyH = (tabsY - bodyTop).coerceAtLeast(0f)
        val body = m("body").measure(Constraints.fixed(px(bodyW), px(bodyH)))

        layout(w, h) {
            body.place(px(bodyX), px(bodyTop))
            art.placeWithLayer(px(artX), px(artY)) {
                scaleX = artScale
                scaleY = artScale
                transformOrigin = TransformOrigin(0f, 0f)
            }
            tabs.place(px(tabsX), px(tabsY))
            // Out's controls: the top bar, the title and the five slots.
            if (pr < 1f) {
                bar.placeWithLayer(px(colX - 4f), 0) { alpha = 1f - pr }
                titleOut.placeWithLayer(px(colX + 4f), px(Comfort.Upright.TOP_BAR)) { alpha = (1f - pr) * (1f - a) }
                row.placeWithLayer(px(colX), h - row.height) { alpha = 1f - pr }
            }
            // Comfort's: Close, the title under the small cover, the grid, the transport.
            if (pr > 0f) {
                close.placeWithLayer(px(leftX), 0) { alpha = pr }
                titleComfort.placeWithLayer(px(leftX), px(48f + artC + 6f)) { alpha = pr }
                grid.placeWithLayer(px(leftX + (s.COLUMN - grid.width / density) / 2f), h - grid.height - px(4f)) { alpha = pr }
                transport.placeWithLayer(px(transportX), (h - transport.height) / 2) { alpha = pr }
            }
        }
    }
}

/** [TitleBlock] in the smaller sizes of the sideways Comfort column (about 17 sp title, 13 sp artist). */
@Composable
private fun ScaledTitle(p: ComfortParts, shown: String) {
    val type = MaterialTheme.typography
    fun androidx.compose.ui.text.TextStyle.times(k: Float) = copy(
        fontSize = if (fontSize.isSpecified) fontSize * k else fontSize,
        lineHeight = if (lineHeight.isSpecified) lineHeight * k else lineHeight,
    )
    MaterialTheme(typography = type.copy(headlineSmall = type.headlineSmall.times(0.75f), bodyMedium = type.bodyMedium.times(0.93f))) {
        TitleBlock(p, shown)
    }
}

/** Five even slots: Equalizer, Previous, Play/Pause, Next, Shuffle (comfort spec 3). */
@Composable
private fun ControlsRow(nowPlaying: NowPlaying, connection: PlayerConnection, text2: Color) {
    Row(
        Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, top = 6.dp, bottom = 14.dp).testTag("comfort_controls"),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.weight(1f).testTag("slot_eq"), contentAlignment = Alignment.Center) { EqualizerButton(nowPlaying, text2, 24.dp) }
        Box(Modifier.weight(1f).testTag("slot_prev"), contentAlignment = Alignment.Center) {
            GlyphButton("prev", "Previous frequency", { connection.previous() }, tint = text2, size = 26.dp)
        }
        Box(Modifier.weight(1f).testTag("slot_play"), contentAlignment = Alignment.Center) { RoundPlayButton(nowPlaying, connection, 60.dp, 26.dp) }
        Box(Modifier.weight(1f).testTag("slot_next"), contentAlignment = Alignment.Center) { NextButton(connection, text2, 26.dp) }
        Box(Modifier.weight(1f).testTag("slot_shuffle"), contentAlignment = Alignment.Center) { ShuffleButton(text2, 24.dp) }
    }
}

/**
 * Wide (spec 5), the desktop's Comfort View: the cover and the song, the
 * lyrics, the artist (logo on top, biography, photo); both pages show,
 * so no tabs. The cover still retracts, 340 dp Out to 150 in Comfort, and
 * the lyrics take the room it leaves. The full player bar stays under it
 * in every state; its Comfort button switches Out and Comfort here.
 */
@Composable
private fun WideComfort(p: ComfortParts, modifier: Modifier) {
    val w = Comfort.Wide
    val progress = p.progress
    Column(modifier) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            GlyphButton("collapse", "Close", p.onClose, tint = p.text2)
            Text(
                p.nowPlaying.stationName,
                style = MaterialTheme.typography.bodySmall,
                color = p.text2,
                modifier = Modifier.weight(1f),
                textAlign = TextAlign.Center,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            // The song menu is by the star under the cover now (build 170); this keeps the station centered.
            Spacer(Modifier.size(48.dp))
        }
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth().padding(horizontal = 30.dp, vertical = 8.dp)) {
            val artOut = w.artOut(maxHeight.value)
            Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(28.dp)) {
                // The cover column: as wide as the cover, which is drawn at its Out size and scaled.
                Column(
                    Modifier
                        .fillMaxHeight()
                        .layout { measurable, constraints ->
                            val side = Comfort.Upright.lerp(artOut, w.ART_COMFORT, progress.value).dp.roundToPx()
                            val placeable = measurable.measure(constraints.copy(minWidth = side, maxWidth = side))
                            layout(side, placeable.height) { placeable.place(0, 0) }
                        }
                        .verticalScroll(rememberScrollState()),
                ) {
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .aspectRatio(1f)
                            .layout { measurable, constraints ->
                                val out = artOut.dp.roundToPx()
                                val side = constraints.maxWidth
                                val placeable = measurable.measure(Constraints.fixed(out, out))
                                layout(side, side) {
                                    placeable.placeWithLayer(0, 0) {
                                        val scale = side / out.toFloat()
                                        scaleX = scale
                                        scaleY = scale
                                        transformOrigin = TransformOrigin(0f, 0f)
                                    }
                                }
                            },
                    ) {
                        Box(
                            Modifier
                                .fillMaxSize()
                                .graphicsLayer { shape = RoundedCornerShape(8.dp); clip = true }
                                .clickable(onClickLabel = Comfort.toggleLabel(p.comfort), role = Role.Button) { p.toggle() }
                                .testTag("comfort_art"),
                        ) { StationArt(p.nowPlaying.artworkUri, p.nowPlaying.stationName, Modifier.fillMaxSize(), corner = 0.dp) }
                    }
                    SongTitle(p.nowPlaying, p.song, p.starred, p.onStar, p.text2, Modifier.padding(top = 14.dp))
                    SleepChip()
                }
                LyricsPane(Modifier.weight(1f).fillMaxHeight().testTag("comfort_body"))
                ArtistPane(p.nowPlaying.stationId, Modifier.width(w.ARTIST.dp).fillMaxHeight(), column = true)
            }
        }
        // The full desktop bar; its Comfort View button switches Out and Comfort.
        MiniPlayer(p.nowPlaying, p.connection, onOpen = p.onClose, wide = true, tapToOpen = false, comfort = p.comfort, onComfort = p.toggle)
    }
}

/** A song's star; favoriting shines it, as on the desktop's bar. */
fun starSong(library: Library, song: SongNow.Song, station: String, on: Boolean) {
    library.changeHistory {
        SongHistory.setFavorite(it, song.key, on, library.now(), song.title, song.artist, station, song.appleMusicUrl, song.coverUrl)
    }
}

/** False on Now Playing for a song from a music folder or iBroadcast (task 241): [StarButton] isn't drawn. */
val LocalCanStar = androidx.compose.runtime.compositionLocalOf { true }

@Composable
fun StarButton(starred: Boolean, onChange: (Boolean) -> Unit) {
    if (!LocalCanStar.current) return
    IconButton(onClick = { onChange(!starred) }) {
        StarGlyph(
            starred, 24.dp, if (starred) "Unfavorite" else "Favorite",
            onTint = MaterialTheme.colorScheme.primary, offTint = LocalContentColor.current,
        )
    }
}

/** The identified song elsewhere (services.SERVICES), and its star; beside the song's star since build 170. */
@Composable
private fun SongMenu(song: SongNow.Song?, starred: Boolean, tint: androidx.compose.ui.graphics.Color, onStar: (Boolean) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        // The services' own marks in turn (user, build 170), where the menu glyph was.
        IconButton(onClick = { open = true }, enabled = song != null) {
            Box(Modifier.semantics { contentDescription = "Find this song in Apple Music, Spotify and more" }) {
                CyclingServiceMark(if (song != null) tint else tint.copy(alpha = 0.38f), 20.dp)
            }
        }
        DropdownMenu(expanded = open && song != null, onDismissRequest = { open = false }) {
            // No Favorite here (user, build 198): the star right beside this button does that.
            if (song != null) SongSearchItems(song.artist, song.title) { open = false }
        }
    }
}

/** "Search Apple Music" and the rest, opened in the service's app when it is installed. */
@Composable
fun SongSearchItems(artist: String, title: String, done: () -> Unit) {
    val context = LocalContext.current
    for ((service, link) in SongHistory.searchLinks(artist, title)) {
        DropdownMenuItem(
            text = { Text("Search $service") },
            onClick = {
                done()
                runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link))) }
            },
        )
    }
}

/** How long is left while the timer runs; the x cancels it. */
@Composable
private fun SleepChip() {
    val say = LocalSay.current
    val ends by SleepTimer.endsAt.collectAsStateWithLifecycle()
    if (ends == null) return
    var left by remember { mutableLongStateOf(SleepTimer.remainingMs() ?: 0) }
    LaunchedEffect(ends) {
        while (true) {
            left = SleepTimer.remainingMs() ?: 0
            delay(1000)
        }
    }
    val minutes = (left + 59_999) / 60_000
    AssistChip(
        onClick = {},
        label = { Text(if (minutes <= 1) "Sleeping in under a minute" else "Sleeping in $minutes minutes") },
        leadingIcon = { Glyph("timer", null, tint = MaterialTheme.colorScheme.primary, size = 18.dp) },
        trailingIcon = {
            Icon(
                Icons.Filled.Close,
                contentDescription = "Cancel the sleep timer",
                modifier = Modifier.size(18.dp).clickable { SleepTimer.cancel(); say("Sleep timer canceled.") },
            )
        },
        modifier = Modifier.padding(top = 8.dp),
    )
}
