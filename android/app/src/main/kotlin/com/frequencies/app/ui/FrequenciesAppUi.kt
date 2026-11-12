package com.frequencies.app.ui

import com.frequencies.app.TagFilter
import android.content.Intent
import android.net.Uri
import androidx.activity.compose.PredictiveBackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsBottomHeight
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.VerticalDivider
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteScaffoldDefaults
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteType
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.activity.result.PickVisualMediaRequest
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.artists
import com.frequencies.app.schedule
import com.frequencies.app.library
import com.frequencies.app.Importer
import com.frequencies.app.Incoming
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.coroutines.cancellation.CancellationException

/** Now Playing's slide up and down. */
private const val SHEET_MS = 300

/** A flick on the player bar this fast (px/s) opens or drops Now Playing whatever the distance. */
private const val FLICK = 900f

/** Destinations in the bottom bar (phones) or the sidebar (wide), with the desktop's glyphs. */
private enum class Destination(val label: String, val short: String, val glyph: String) {
    Saved("Saved Frequencies", "Saved", "stations"),
    // Add Frequency and Browse are one screen (user, build 170).
    Add("Add Frequency…", "Add", "plus"),
    History("History", "History", "history"),
    // The Schedule has its own place in the bar (user, build 170), not a button on Saved Frequencies.
    Schedule("Schedule", "Schedule", "schedule"),
    Settings("Settings", "Settings", "gear"),
}

@Composable
fun FrequenciesAppUi(connection: PlayerConnection, opened: MutableStateFlow<Intent?>) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    val say: (String) -> Unit = { text -> scope.launch { snackbar.showSnackbar(text, withDismissAction = true) } }
    var incoming by remember { mutableStateOf<Incoming?>(null) }
    var addAddress by remember { mutableStateOf<String?>(null) }
    var showSchedule by rememberSaveable { mutableStateOf(false) }
    // Messages from the playback service and background checks.
    LaunchedEffect(Unit) { com.frequencies.app.Notices.messages.collect { say(it) } }

    // Files, codes and links from the file picker, "Open with", the share sheet, the camera or the clipboard.
    fun readUri(uri: Uri) = scope.launch { incoming = withContext(Dispatchers.IO) { Importer.read(context, uri) } }
    // A computer's sync QR code (or a pasted pairing link) pairs, from wherever it was scanned.
    var syncPairing by rememberSaveable { mutableStateOf<String?>(null) }
    syncPairing?.let { PairDeviceDialog(onClose = { syncPairing = null }, scanned = it) }
    fun readText(text: String) = if (com.frequencies.core.SyncWire.parseQr(text) != null) {
        syncPairing = text.trim()
        null
    } else scope.launch {
        incoming = withContext(Dispatchers.IO) {
            runCatching { Importer.readText(context, text) }.getOrElse { Incoming.Failed("That isn't a frequency, a code or a station list.") }
        }
    }
    val openFile = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> uri?.let(::readUri) }
    var scanning by rememberSaveable { mutableStateOf(false) }
    // The Add screen's Import QR code tile: Android's photo picker, then the code in that picture (build 172).
    val pickQrPicture = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) scope.launch {
            val text = readPictureCode(context, uri)
            if (text != null) readText(text) else say(NO_CODE_IN_PICTURE)
        }
    }
    val actions = remember {
        AddActions(
            byAddress = { addAddress = "" },
            fromFile = { openFile.launch(arrayOf("*/*")) },
            scan = { scanning = true },
            importQr = { pickQrPicture.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
        )
    }
    val openedIntent by opened.collectAsStateWithLifecycle()
    LaunchedEffect(openedIntent) {
        val intent = openedIntent ?: return@LaunchedEffect
        opened.value = null
        @Suppress("DEPRECATION")
        val stream = intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
        when {
            // Back from the iBroadcast sign-in page: the sign-in itself was already picked up.
            intent.data?.scheme == "frequencies" -> Unit
            intent.action == com.frequencies.app.ScheduleWatch.ACTION_OPEN_SCHEDULE -> showSchedule = true
            intent.action == Intent.ACTION_VIEW && intent.data != null -> readUri(intent.data!!)
            intent.action == Intent.ACTION_SEND && stream != null -> readUri(stream)
            intent.action == Intent.ACTION_SEND -> intent.getStringExtra(Intent.EXTRA_TEXT)?.let(::readText)
        }
    }

    CompositionLocalProvider(LocalSay provides say) {
        AppScaffold(connection, actions, showSchedule) { showSchedule = it }
        incoming?.let { IncomingDialog(it, onAddress = { url -> addAddress = url }, onClose = { incoming = null }) }
        addAddress?.let { AddByAddressDialog(it) { addAddress = null } }
        if (scanning) QrScannerDialog(onCode = { readText(it) }, onClose = { scanning = false })
        // The thumbs-down question stays up whatever plays next (desktop build 152).
        val thumbs by context.artists.question.collectAsStateWithLifecycle()
        thumbs?.let { ThumbsQuestion(it) }
    }
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
        SnackbarHost(snackbar, Modifier.windowInsetsPadding(WindowInsets.navigationBars).padding(bottom = 140.dp))
    }
}

@Composable
private fun AppScaffold(connection: PlayerConnection, actions: AddActions, showSchedule: Boolean, setSchedule: (Boolean) -> Unit) {
    val nowPlaying by connection.state.collectAsStateWithLifecycle()
    var destination by rememberSaveable { mutableStateOf(Destination.Saved) }
    // Opened from a reminder: the Schedule's place.
    LaunchedEffect(showSchedule) {
        if (showSchedule) {
            destination = Destination.Schedule
            setSchedule(false)
        }
    }
    var showPlayer by rememberSaveable { mutableStateOf(false) }
    // The Pebble's "Find more on phone" (through its notification): Add Frequency.
    val openAdd by OpenAdd.open.collectAsStateWithLifecycle()
    LaunchedEffect(openAdd) {
        if (openAdd) {
            showPlayer = false
            destination = Destination.Add
            OpenAdd.open.value = false
        }
    }
    // The player bar's sync icon opens Settings (SettingsScreen picks the tab and clears the link).
    val settingsLink by SettingsLink.open.collectAsStateWithLifecycle()
    LaunchedEffect(settingsLink) {
        if (settingsLink != null) {
            showPlayer = false
            destination = Destination.Settings
        }
    }
    var backProgress by remember { mutableFloatStateOf(0f) }
    // How far Now Playing is up: 0 hidden, 1 open. The player bar can be dragged up to pull it (user, build 170).
    val sheet = remember { Animatable(if (showPlayer) 1f else 0f) }
    val sheetScope = rememberCoroutineScope()
    var rootHeight by remember { mutableIntStateOf(1) }
    LaunchedEffect(showPlayer) { sheet.animateTo(if (showPlayer) 1f else 0f, tween(SHEET_MS)) }
    val dragBar: (Float) -> Unit = { dy -> sheetScope.launch { sheet.snapTo((sheet.value - dy / rootHeight).coerceIn(0f, 1f)) } }
    val dropBar: (Float) -> Unit = { velocity ->
        // A flick up opens; a slow drag opens past a third of the way.
        val open = velocity < -FLICK || (velocity <= FLICK && sheet.value > 0.33f)
        showPlayer = open
        sheetScope.launch { sheet.animateTo(if (open) 1f else 0f, tween(SHEET_MS)) }
    }
    // Now Playing dragged down by its top (user, build 198), the reverse: a flick down closes; a slow drag closes past a third of the way.
    val dropSheet: (Float) -> Unit = { velocity ->
        val open = velocity < -FLICK || (velocity <= FLICK && sheet.value > 0.67f)
        showPlayer = open
        sheetScope.launch { sheet.animateTo(if (open) 1f else 0f, tween(SHEET_MS)) }
    }
    // Made once: the lambdas read the sheet, its height and showPlayer as they are when called.
    val sheetDrag = remember { SheetDrag(dragBar, dropSheet) }
    // Saved Frequencies' search and tag filter, here so the wide sidebar's tag list can set it too.
    // The tags are the app's one tag filter, shared with Android Auto (user, build 189): a tag
    // changed in the car shows here at once and the other way round. The typed text stays here.
    var searchText by rememberSaveable { mutableStateOf("") }
    val tagTerms by TagFilter.query.collectAsStateWithLifecycle()
    val query = remember(searchText, tagTerms) { com.frequencies.core.Search.withText(tagTerms, searchText) }
    val setQuery: (String) -> Unit = { q ->
        searchText = com.frequencies.core.Search.text(q)
        TagFilter.set(q)
    }

    // The same switch point as before: where the rail came, the desktop's sidebar comes now.
    val layout = NavigationSuiteScaffoldDefaults.calculateFromAdaptiveInfo(currentWindowAdaptiveInfo())
    val wide = layout != NavigationSuiteType.NavigationBar
    val go: (Destination) -> Unit = { d -> destination = d }

    Box(Modifier.fillMaxSize().background(MaterialTheme.freq.bg0).onSizeChanged { rootHeight = it.height.coerceAtLeast(1) }) {
        val screen: @Composable () -> Unit = {
            when (destination) {
                Destination.Saved -> StationsScreen(connection, nowPlaying, onOpenSchedule = { go(Destination.Schedule) }, query = query, onQuery = setQuery, wide = wide)
                Destination.Add -> AddFrequencyScreen(actions, connection, nowPlaying)
                Destination.History -> HistoryScreen()
                Destination.Schedule -> ScheduleScreen(connection, nowPlaying)
                Destination.Settings -> SettingsScreen(onRestore = actions.fromFile)
            }
        }
        if (wide) {
            Column(Modifier.fillMaxSize()) {
                Row(Modifier.weight(1f)) {
                    Sidebar(
                        destination = destination,
                        nowPlaying = nowPlaying,
                        query = query,
                        onGo = go,
                        onTag = { tag ->
                            go(Destination.Saved)
                            setQuery(com.frequencies.core.Search.cycleTag(query, tag))
                        },
                    )
                    Box(Modifier.weight(1f).fillMaxSize()) { screen() }
                }
                MiniPlayer(
                    nowPlaying, connection, onOpen = { showPlayer = true }, wide = true,
                    modifier = Modifier.windowInsetsPadding(WindowInsets.navigationBars.only(WindowInsetsSides.Bottom)),
                )
            }
        } else {
            Column(Modifier.fillMaxSize()) {
                Box(Modifier.weight(1f)) { screen() }
                MiniPlayer(nowPlaying, connection, onOpen = { showPlayer = true }, onDrag = dragBar, onDragEnd = dropBar)
                BottomBar(destination, go)
            }
        }

        if (sheet.value > 0f && nowPlaying.hasStation) {
            NowPlayingScreen(
                nowPlaying,
                connection,
                onClose = { showPlayer = false },
                wide = wide,
                sheetDrag = if (wide) null else sheetDrag,
                modifier = Modifier.graphicsLayer {
                    // Up from the bottom as it opens (or follows the finger); predictive back shrinks and slides it.
                    val scale = 1f - 0.1f * backProgress
                    scaleX = scale
                    scaleY = scale
                    translationY = size.height * (1f - sheet.value) + size.height * 0.15f * backProgress
                },
            )
        }
    }

    PredictiveBackHandler(enabled = showPlayer) { progress ->
        try {
            progress.collect { backProgress = it.progress }
            showPlayer = false
        } catch (e: CancellationException) {
            // Gesture canceled: stay.
        } finally {
            backProgress = 0f
        }
    }
}

/**
 * The phone's bottom bar (spec 5.1): 72 dp in bg1 under a line, the
 * chosen item on a bg2 plate with its glyph in the accent.
 */
@Composable
private fun BottomBar(destination: Destination, onGo: (Destination) -> Unit) {
    val f = MaterialTheme.freq
    // A show on the air now: the red dot the Schedule button had on Saved Frequencies.
    val live by LocalContext.current.schedule.live.collectAsStateWithLifecycle()
    Column(Modifier.fillMaxWidth().background(f.bg1)) {
        HorizontalDivider(thickness = 1.dp, color = f.line)
        Row(
            Modifier.fillMaxWidth().height(72.dp).padding(horizontal = 6.dp).selectableGroup(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Destination.entries.forEach { d ->
                val on = d == destination
                Column(
                    Modifier
                        .weight(1f)
                        .padding(horizontal = 3.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(if (on) f.bg2 else Color.Transparent)
                        .selectable(selected = on, onClick = { onGo(d) }, role = Role.Tab)
                        .padding(vertical = 7.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    BadgedBox(badge = { if (d == Destination.Schedule && live.isNotEmpty()) Badge(containerColor = LiveRed) }) {
                        Glyph(d.glyph, null, tint = if (on) MaterialTheme.colorScheme.primary else f.text2, size = 22.dp)
                    }
                    Text(d.short, style = MaterialTheme.typography.labelSmall, color = if (on) f.text else f.text2, maxLines = 1)
                }
            }
        }
        Spacer(Modifier.windowInsetsBottomHeight(WindowInsets.navigationBars))
    }
}

/**
 * The desktop's sidebar on wide screens (spec 5.1): the places (Schedule
 * among them), the tags (tap = show only that tag, as the tag filter does) and the
 * artwork dock at the bottom when there is room.
 */
@Composable
private fun Sidebar(
    destination: Destination,
    nowPlaying: NowPlaying,
    query: String,
    onGo: (Destination) -> Unit,
    onTag: (String) -> Unit,
) {
    val f = MaterialTheme.freq
    val context = LocalContext.current
    val stations by context.library.stations.collectAsStateWithLifecycle()
    val settings by context.library.settings.collectAsStateWithLifecycle()
    val tags = remember(stations, settings) { com.frequencies.core.Tags.library(stations, settings) }
    val states = com.frequencies.core.Search.tagStates(query)
    Row(Modifier.fillMaxHeight()) {
        BoxWithConstraints(
            Modifier
                .width(228.dp)
                .fillMaxHeight()
                .background(f.bg1)
                .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Start)),
        ) {
            val dock = maxHeight > 560.dp && nowPlaying.hasStation
            Column(Modifier.fillMaxSize().padding(horizontal = 10.dp, vertical = 12.dp)) {
                Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).selectableGroup()) {
                    Destination.entries.forEach { d ->
                        SideRow(d.glyph, d.label, d == destination) { onGo(d) }
                    }
                    if (tags.isNotEmpty()) {
                        Text(
                            "TAGS",
                            style = MaterialTheme.typography.labelMedium,
                            color = f.text2,
                            modifier = Modifier.padding(start = 12.dp, top = 16.dp, bottom = 6.dp).semantics { heading() },
                        )
                        val colors = remember(tags, settings) { com.frequencies.core.TagLook.colors(tags, settings) }
                        tags.forEach { tag ->
                            val state = states[tag.lowercase()] ?: com.frequencies.core.Search.NEUTRAL
                            // The desktop sidebar's tag chips: the tag's own color, filled when shown only, struck through when hidden.
                            TagPill(
                                tag,
                                com.frequencies.core.TagLook.colorOf(tag, colors, settings),
                                com.frequencies.core.TagLook.filterStyle(state),
                                onClick = { onTag(tag) },
                                onClickLabel = "Show only $tag",
                                chosen = state == com.frequencies.core.Search.INCLUDED,
                                modifier = Modifier.padding(start = 6.dp),
                            )
                        }
                    }
                }
                if (dock) {
                    Spacer(Modifier.height(10.dp))
                    StationArt(nowPlaying.artworkUri, nowPlaying.stationName, Modifier.fillMaxWidth().aspectRatio(1f), corner = 8.dp)
                }
            }
        }
        VerticalDivider(thickness = 1.dp, color = f.line)
    }
}

@Composable
private fun SideRow(glyph: String, label: String, on: Boolean, onClick: () -> Unit) {
    val f = MaterialTheme.freq
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(7.dp))
            .background(if (on) f.bg2 else Color.Transparent)
            .selectable(selected = on, onClick = onClick, role = Role.Tab)
            .heightIn(min = 48.dp)
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Glyph(glyph, null, tint = if (on) MaterialTheme.colorScheme.primary else f.text2, size = 20.dp)
        Spacer(Modifier.width(12.dp))
        Text(label, style = MaterialTheme.typography.labelLarge, color = if (on) f.text else f.text2, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** Opens Add Frequency from outside the screens (the Pebble watchapp's "Find more on phone", build 190). */
object OpenAdd {
    val open = kotlinx.coroutines.flow.MutableStateFlow(false)
}
