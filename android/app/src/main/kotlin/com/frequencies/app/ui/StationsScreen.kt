package com.frequencies.app.ui

import androidx.compose.ui.unit.sp
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.ui.draw.alpha
import com.frequencies.app.localMusic
import com.frequencies.core.LocalStations
import com.frequencies.core.OfflineRules
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.grid.items as gridItems
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Label
import androidx.compose.material.icons.automirrored.filled.Sort
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.DragHandle
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import com.frequencies.app.schedule
import com.frequencies.core.Health
import com.frequencies.core.Schedule
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeTopAppBar
import androidx.compose.material3.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.Sharing
import com.frequencies.app.library
import com.frequencies.core.SORT_LABELS
import com.frequencies.core.Search
import com.frequencies.core.Settings
import com.frequencies.core.Station
import com.frequencies.core.TagLook
import com.frequencies.core.Tags
import com.frequencies.core.defaultDescending
import com.frequencies.core.sortStations
import kotlinx.serialization.json.JsonPrimitive

/**
 * Saved Frequencies: search, tag chips, the desktop's sort modes, drag
 * handles in manual order, and long-press selection (share, tags,
 * remove, station settings). The look is the desktop list's (spec 5.3):
 * flat 58 dp rows in stripes, the playing one lit with its song line
 * and moving bars. [query] lives in the app scaffold so the wide
 * sidebar's tag list filters the same list; [wide] drops the chip row.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun StationsScreen(
    connection: PlayerConnection,
    nowPlaying: NowPlaying,
    onOpenSchedule: () -> Unit,
    query: String,
    onQuery: (String) -> Unit,
    wide: Boolean = false,
) {
    val context = LocalContext.current
    val library = context.library
    val say = LocalSay.current
    val stations by library.stations.collectAsStateWithLifecycle()
    val settings by library.settings.collectAsStateWithLifecycle()
    val health by library.health.collectAsStateWithLifecycle()
    // Offline mode (task 214): stations that need the internet are grayed out and can't start.
    val offline by com.frequencies.app.Connectivity.offline.collectAsStateWithLifecycle()
    val live by context.schedule.live.collectAsStateWithLifecycle()
    val liveShows = remember(live) { live.associate { it.station.id to it.name } }
    val scope = rememberCoroutineScope()
    /** A frequency a live pill jumped to: shown selected for a moment, not played (mainwindow.jump_to_live). */
    var flashed by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(flashed) { if (flashed != null) { delay(2500); flashed = null } }
    var searching by rememberSaveable { mutableStateOf(false) }
    // The magnifying glass opens the search box ready to type: focused, keyboard up (user, build 184).
    val searchField = remember { androidx.compose.ui.focus.FocusRequester() }
    val keyboard = androidx.compose.ui.platform.LocalSoftwareKeyboardController.current
    var focusAsked by remember { mutableStateOf(0) }
    LaunchedEffect(focusAsked) {
        if (focusAsked > 0 && searching) {
            delay(50)
            runCatching { searchField.requestFocus() }
            keyboard?.show()
        }
    }
    var selected by remember { mutableStateOf(setOf<String>()) }
    var editing by remember { mutableStateOf<Station?>(null) }
    var tagging by remember { mutableStateOf<Set<String>?>(null) }
    var managingTags by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf<Set<String>?>(null) }
    var qrFor by remember { mutableStateOf<Station?>(null) }

    val sorted = remember(stations, settings.sortMode, settings.sortDescending) {
        sortStations(stations, settings.sortMode, settings.sortDescending)
    }
    val shown = remember(sorted, query) { Search.filter(sorted, query) }
    // Playing from Saved: Next, Previous and Shuffle follow the tags and search as they change (user, build 186).
    LaunchedEffect(shown) { connection.follow(shown) }
    val manual = settings.sortMode == "manual" && Search.tokens(query).isEmpty()
    val allTags = remember(stations, settings) { Tags.library(stations, settings) }
    // The color each tag shows in, as the desktop gives them out (core TagLook.colors).
    val tagColors = remember(allTags, settings) { TagLook.colors(allTags, settings) }

    // Drag to reorder: the order while a row is held, written when it's let go.
    val listState = rememberLazyListState()
    // List or Thumbnails (the desktop's station_grid, chosen in the sort menu); no dragging in the grid, as there.
    val grid = settings.bool("station_grid", false)
    val gridState = rememberLazyGridState()
    var dragId by remember { mutableStateOf<String?>(null) }
    var dragOffset by remember { mutableFloatStateOf(0f) }
    var dragOrder by remember { mutableStateOf(listOf<String>()) }
    val byId = remember(stations) { stations.associateBy { it.id } }
    val rows = if (dragId != null) dragOrder.mapNotNull { byId[it] } else shown

    BackHandler(enabled = selected.isNotEmpty()) { selected = emptySet() }

    // A local files station on this phone can take its music along (desktop: "Include the audio files too?").
    var askMusic by remember { mutableStateOf<List<Station>?>(null) }
    fun shareFile(chosen: List<Station>) {
        if (chosen.any { LocalStations.isLocal(it) && context.localMusic.onThisPhone(it) }) askMusic = chosen
        else Sharing.shareZip(context, chosen)
    }
    askMusic?.let { chosen ->
        val local = chosen.filter { LocalStations.isLocal(it) && context.localMusic.onThisPhone(it) }
        val names = local.take(3).joinToString(", ") { it.displayName } + if (local.size > 3) ", …" else ""
        AlertDialog(
            onDismissRequest = { askMusic = null },
            title = { Text("Include the music files?") },
            text = { Text("$names\n\nWithout them, the other person picks a folder of their own music for it.") },
            confirmButton = { TextButton(onClick = { askMusic = null; Sharing.shareZip(context, chosen, withMusic = true) }) { Text("Include music") } },
            dismissButton = {
                Row {
                    TextButton(onClick = { askMusic = null }) { Text("Cancel") }
                    TextButton(onClick = { askMusic = null; Sharing.shareZip(context, chosen) }) { Text("Without") }
                }
            },
        )
    }
    Scaffold(
        containerColor = MaterialTheme.freq.bg0,
        contentWindowInsets = WindowInsets(0),
        topBar = {
            if (selected.isNotEmpty()) {
                SelectionBar(
                    count = selected.size,
                    onClose = { selected = emptySet() },
                    onShare = {
                        val chosen = sorted.filter { it.id in selected }
                        if (chosen.size == 1 && !LocalStations.isLocal(chosen[0])) qrFor = chosen[0] else shareFile(chosen)
                    },
                    onTags = { tagging = selected },
                    onRemove = { removing = selected },
                    onSettings = if (selected.size == 1) { { editing = byId[selected.first()] } } else null,
                    onSelectAll = { selected = shown.map { it.id }.toSet() },
                )
            } else {
                FreqTopBar("Saved Frequencies") {
                    GlyphButton("search", "Search", onClick = { searching = !searching; if (searching) focusAsked++ else onQuery(Search.withText(query, "")) })
                    SortMenu(settings, onView = { wantsGrid -> library.updateSettings { it["station_grid"] = JsonPrimitive(wantsGrid) } }) { mode, descending ->
                        library.updateSettings {
                            if (mode != null) it["sort_mode"] = JsonPrimitive(mode)
                            if (descending != null) it["sort_desc"] = JsonPrimitive(descending)
                        }
                        if (mode != null && mode != "manual") say("Sorted by ${SORT_LABELS[mode]?.lowercase()}. Switch to manual to drag frequencies around.")
                    }
                    // Search, sort, Manage tags; the Schedule is in the bottom bar and a long press selects (user, build 170).
                    GlyphButton("tag", "Manage tags", onClick = { managingTags = true }, tint = MaterialTheme.freq.text2)
                }
            }
        },
    ) { padding ->
        Column(Modifier.padding(top = padding.calculateTopPadding()).fillMaxSize()) {
            if (offline) OfflineBanner()
            NotificationsHint()
            if (stations.size > 1) HintCard(com.frequencies.core.Hints.SELECT)
            if (searching || Search.text(query).isNotEmpty()) {
                OutlinedTextField(
                    value = Search.text(query),
                    onValueChange = { onQuery(Search.withText(query, it)) },
                    placeholder = { Text("Search your frequencies") },
                    leadingIcon = { Glyph("search", null, tint = MaterialTheme.freq.text2, size = 20.dp) },
                    shape = RoundedCornerShape(6.dp),
                    trailingIcon = {
                        IconButton(onClick = { onQuery(Search.withText(query, "")); searching = false }) {
                            Icon(Icons.Filled.Clear, contentDescription = "Clear search")
                        }
                    },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp).focusRequester(searchField),
                )
            }
            if (allTags.isNotEmpty() && !wide) {
                TagFilterRow(allTags, settings, tagColors, query, onChange = onQuery)
            }
            if (stations.isEmpty()) {
                ListItem(
                    headlineContent = { Text("No saved frequencies yet. Add some from Add Frequency…") },
                    colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                )
            } else if (shown.isEmpty()) {
                ListItem(
                    headlineContent = { Text("No frequency matches.") },
                    trailingContent = { TextButton(onClick = { onQuery(""); searching = false }) { Text("Show all") } },
                    colors = ListItemDefaults.colors(containerColor = Color.Transparent),
                )
            }
            Box(Modifier.weight(1f)) {
            fun tap(station: Station) {
                if (selected.isNotEmpty()) selected = if (station.id in selected) selected - station.id else selected + station.id
                else context.localMusic.blocked(station, offline)?.let(say) ?: connection.play(station, shown, fromSaved = true)
            }
            fun hold(station: Station) { selected = if (station.id in selected) selected - station.id else selected + station.id }
            if (grid) LazyVerticalGrid(
                columns = GridCells.Adaptive(TILE_MIN),
                state = gridState,
                contentPadding = PaddingValues(start = 10.dp, end = 10.dp, top = 4.dp, bottom = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                gridItems(rows, key = { it.id }) { station ->
                    val current = station.id == nowPlaying.stationId
                    StationTile(
                        station = station,
                        current = current,
                        playing = current && nowPlaying.playWhenReady,
                        checked = if (selected.isEmpty()) null else station.id in selected,
                        flashed = station.id == flashed,
                        down = Health.describe(health, station.id, library.now()).isNotEmpty(),
                        live = station.id in liveShows,
                        unavailable = library.unavailable(station) != null && (library.unavailable(station) == OfflineRules.NOT_HERE || offline),
                        onClick = { tap(station) },
                        onLongClick = { hold(station) },
                        modifier = Modifier.animateItem(),
                    )
                }
            } else
            LazyColumn(state = listState, contentPadding = PaddingValues(bottom = 8.dp), modifier = Modifier.fillMaxSize()) {
                itemsIndexed(rows, key = { _, it -> it.id }) { index, station ->
                    val dragging = station.id == dragId
                    val current = station.id == nowPlaying.stationId
                    StationRow(
                        station = station,
                        settings = settings,
                        tagColors = tagColors,
                        index = index,
                        current = current,
                        playing = current && nowPlaying.playWhenReady,
                        checked = if (selected.isEmpty()) null else station.id in selected,
                        flashed = station.id == flashed,
                        downText = Health.describe(health, station.id, library.now()),
                        liveShow = liveShows[station.id],
                        unavailable = library.unavailable(station)?.let { if (it == OfflineRules.NOT_HERE || !offline) it else OfflineRules.ROW },
                        onClick = { tap(station) },
                        onLongClick = { hold(station) },
                        handle = if (!manual || selected.isNotEmpty()) null else Modifier.pointerInput(station.id, shown) {
                            detectVerticalDragGestures(
                                onDragStart = {
                                    dragOrder = shown.map { it.id }
                                    dragId = station.id
                                    dragOffset = 0f
                                },
                                onVerticalDrag = { change, amount ->
                                    change.consume()
                                    dragOffset += amount
                                    val items = listState.layoutInfo.visibleItemsInfo
                                    val held = items.firstOrNull { it.key == dragId } ?: return@detectVerticalDragGestures
                                    val middle = held.offset + held.size / 2 + dragOffset
                                    val over = items.firstOrNull {
                                        it.key != dragId && middle.toInt() in it.offset until it.offset + it.size
                                    } ?: return@detectVerticalDragGestures
                                    val from = dragOrder.indexOf(dragId)
                                    val to = dragOrder.indexOf(over.key as String)
                                    if (from < 0 || to < 0) return@detectVerticalDragGestures
                                    dragOrder = dragOrder.toMutableList().apply { add(to, removeAt(from)) }
                                    dragOffset -= (over.offset - held.offset)
                                },
                                onDragEnd = {
                                    library.setManualOrder(dragOrder)
                                    dragId = null
                                    dragOffset = 0f
                                },
                                onDragCancel = {
                                    dragId = null
                                    dragOffset = 0f
                                },
                            )
                        },
                        modifier = if (dragging) Modifier.zIndex(1f).graphicsLayer { translationY = dragOffset }
                        else Modifier.animateItem(),
                    )
                }
            }
            // Live shows whose frequency is out of sight (mainwindow._place_live): at most two, then "More live shows".
            fun seen(): Seen = if (grid) gridState.seen() else listState.seen()
            val outOfSight by remember(rows, live, grid) {
                derivedStateOf {
                    val view = seen()
                    val visible = view.items
                    val first = visible.firstOrNull()?.index ?: 0
                    val last = visible.lastOrNull()?.index ?: -1
                    val fullyIn = visible.filter { it.top >= view.start && it.bottom <= view.end }.map { it.key }.toSet()
                    val index = rows.withIndex().associate { (i, s) -> s.id to i }
                    val pills = Schedule.pills(live) { id -> id in index && id !in fullyIn }
                    Triple(pills, pills.named.map { (index[it.station.id] ?: 0) > last }, first)
                }
            }
            // "Jump to playing" (mainwindow._place_jump_playing): only while the playing row is out of sight,
            // above or below the list, whichever way it will take you.
            val playingId = nowPlaying.stationId
            val jump by remember(rows, playingId, grid) {
                derivedStateOf {
                    val i = rows.indexOfFirst { it.id == playingId }
                    if (i < 0) null else {
                        val view = seen()
                        val item = view.items.firstOrNull { it.index == i }
                        val inSight = item != null && item.bottom > view.start && item.top < view.end
                        if (inSight) null else (i > (view.items.lastOrNull()?.index ?: -1))
                    }
                }
            }
            fun scrollTo(i: Int) = scope.launch {
                if (grid) gridState.animateScrollToItem(i) else listState.animateScrollToItem((i - 2).coerceAtLeast(0))
            }
            // "Automatically scroll list position to currently playing frequency" (jump_on_change):
            // Next and Previous (from here, the notification, a car or headphones) follow the station.
            var lastPlaying by remember { mutableStateOf(playingId) }
            LaunchedEffect(playingId) {
                val was = lastPlaying
                lastPlaying = playingId
                if (was == null || playingId == null || was == playingId) return@LaunchedEffect
                if (!settings.bool("jump_on_change", true) || jump == null) return@LaunchedEffect
                val i = rows.indexOfFirst { it.id == playingId }
                if (i >= 0) scrollTo(i)
            }
            if (selected.isEmpty() && dragId == null) {
                LivePills(
                    pills = outOfSight.first,
                    below = outOfSight.second,
                    jumpBelow = jump,
                    onJumpToPlaying = { rows.indexOfFirst { it.id == playingId }.takeIf { it >= 0 }?.let(::scrollTo) },
                    onJump = { id ->
                        val i = rows.indexOfFirst { it.id == id }
                        if (i >= 0) scope.launch { if (grid) gridState.animateScrollToItem(i) else listState.animateScrollToItem(i) }
                        flashed = id
                    },
                    onMore = onOpenSchedule,
                )
            }
            }
        }
    }

    editing?.let { StationSettingsDialog(it) { editing = null } }
    tagging?.let { ids -> TagsForSelectionDialog(ids) { tagging = null; selected = emptySet() } }
    if (managingTags) TagManagerDialog { managingTags = false }
    qrFor?.let { QrDialog(it) { qrFor = null } }
    removing?.let { ids ->
        val names = sorted.filter { it.id in ids }
        AlertDialog(
            onDismissRequest = { removing = null },
            title = { Text(if (names.size == 1) "Remove ${names[0].displayName}?" else "Remove ${names.size} frequencies?") },
            text = { Text("They come off Saved Frequencies on this phone.") },
            confirmButton = {
                TextButton(onClick = {
                    library.remove(ids)
                    // A music folder no station plays any more: its permission goes back.
                    context.localMusic.releaseUnused(library.stations.value)
                    context.localMusic.cleanUnpacked(library.stations.value)
                    say(if (names.size == 1) "Removed ${names[0].displayName}." else "Removed ${names.size} frequencies.")
                    removing = null
                    selected = emptySet()
                }) { Text("Remove") }
            },
            dismissButton = { TextButton(onClick = { removing = null }) { Text("Cancel") } },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SelectionBar(
    count: Int,
    onClose: () -> Unit,
    onShare: () -> Unit,
    onTags: () -> Unit,
    onRemove: () -> Unit,
    onSettings: (() -> Unit)?,
    onSelectAll: () -> Unit,
) {
    FreqTopBar(
        "$count selected",
        color = MaterialTheme.freq.bg2,
        navigation = { IconButton(onClick = onClose) { Icon(Icons.Filled.Close, contentDescription = "Done") } },
    ) {
        IconButton(onClick = onShare) { Icon(Icons.Filled.Share, contentDescription = "Share") }
        GlyphButton("tag", "Tags", onClick = onTags)
        GlyphButton("trash", "Remove", onClick = onRemove)
        var menu by remember { mutableStateOf(false) }
        Box {
            GlyphButton("menu", "More", onClick = { menu = true })
            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                if (onSettings != null) DropdownMenuItem(text = { Text("Station settings") }, onClick = { menu = false; onSettings() })
                DropdownMenuItem(text = { Text("Select all") }, onClick = { menu = false; onSelectAll() })
            }
        }
    }
}

/** The desktop's sort menu: which way it runs (not for manual), then the modes. */
@Composable
private fun SortMenu(settings: Settings, onView: (grid: Boolean) -> Unit, onChange: (mode: String?, descending: Boolean?) -> Unit) {
    var open by remember { mutableStateOf(false) }
    // The desktop has no sort glyph (its sort is a combo box); Material's, in the bar's text2.
    Box {
    IconButton(onClick = { open = true }) { Icon(Icons.AutoMirrored.Filled.Sort, contentDescription = "Sort", tint = MaterialTheme.freq.text2) }
    DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
        // mainwindow._sort_menu: how the list looks first, then how it is ordered.
        val grid = settings.bool("station_grid", false)
        for ((label, wantsGrid) in listOf("List" to false, "Thumbnails" to true)) {
            DropdownMenuItem(
                text = { Text(label) },
                trailingIcon = if (grid == wantsGrid) { { Icon(Icons.Filled.Check, contentDescription = null) } } else null,
                onClick = { open = false; if (grid != wantsGrid) onView(wantsGrid) },
            )
        }
        HorizontalDivider()
        val mode = settings.sortMode
        if (mode != "manual") {
            val down = settings.sortDescending ?: defaultDescending(mode)
            DropdownMenuItem(
                text = { Text(if (down) "↓  Descending" else "↑  Ascending") },
                onClick = { open = false; onChange(null, !down) },
            )
            HorizontalDivider()
        }
        SORT_LABELS.forEach { (key, label) ->
            DropdownMenuItem(
                text = { Text(label) },
                trailingIcon = if (key == mode) { { Icon(Icons.Filled.Check, contentDescription = null) } } else null,
                onClick = { open = false; if (key != mode) onChange(key, null) },
            )
        }
    }
    }
}

/**
 * The tag chips (phones; the wide sidebar lists tags instead), larger than
 * a row's. Tap shows only those (any of them), long press hides one
 * (struck through, as the desktop's TagPill), the same again to clear.
 * No "All" chip (user, build 170): tapping a chosen tag again shows all.
 */
@Composable
private fun TagFilterRow(tags: List<String>, settings: Settings, colors: Map<String, String>, query: String, onChange: (String) -> Unit) {
    val states = Search.tagStates(query)
    LazyRow(
        contentPadding = PaddingValues(horizontal = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        items(tags) { tag ->
            val state = states[tag.lowercase()] ?: Search.NEUTRAL
            // The desktop's sidebar chips: tinted in the tag's color; filled when required, struck through when hidden.
            TagPill(
                tag,
                TagLook.colorOf(tag, colors, settings),
                TagLook.filterStyle(state),
                onClick = { onChange(Search.cycleTag(query, tag)) },
                onLongClick = { onChange(Search.cycleTag(query, tag, exclude = true)) },
                onClickLabel = if (state == Search.INCLUDED) "Show all" else "Show only $tag",
                onLongClickLabel = "Hide $tag",
                chosen = state == Search.INCLUDED,
                scale = FILTER_PILL_SCALE,
            )
        }
    }
}

/**
 * One Saved row (spec 5.3): 58 dp, art 40 dp with 6 dp corners, name and
 * a second line. Stripes; the frequency you're hearing on bg2 with the
 * desktop's three bars (no song line under it: user, build 170).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun StationRow(
    station: Station,
    settings: Settings,
    /** Each tag's color (TagLook.colors), so the pills match the desktop's. */
    tagColors: Map<String, String>,
    index: Int,
    current: Boolean,
    playing: Boolean,
    checked: Boolean?,
    flashed: Boolean,
    downText: String,
    liveShow: String?,
    /** Why it can't play now (offline, or its music isn't on this phone): grayed out (task 214). */
    unavailable: String?,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
    handle: Modifier?,
    modifier: Modifier = Modifier,
) {
    val library = LocalContext.current.library
    val f = MaterialTheme.freq
    val down = downText.isNotEmpty()
    val background = when {
        checked == true || flashed -> MaterialTheme.colorScheme.secondaryContainer
        else -> stripe(index, current)
    }
    Row(
        modifier
            .fillMaxWidth()
            .background(background)
            .combinedClickable(onClick = onClick, onLongClick = onLongClick, onLongClickLabel = "Select")
            .alpha(if (unavailable != null && checked != true) 0.45f else 1f)
            .heightIn(min = 58.dp)
            .padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (checked == true) {
            Icon(Icons.Filled.CheckCircle, contentDescription = "Selected", tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(40.dp))
        } else {
            StationArt(library.artUri(station), station.displayName, Modifier.size(40.dp), corner = 6.dp)
        }
        Column(Modifier.weight(1f)) {
            Text(
                station.displayName,
                style = MaterialTheme.typography.titleMedium,
                color = if (down) f.text3 else f.text,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            val line = MaterialTheme.typography.bodySmall
            when {
                unavailable != null -> Text(unavailable, style = line, color = f.text2, maxLines = 2, overflow = TextOverflow.Ellipsis)
                down -> Text("Not answering · $downText", style = line, color = f.down, maxLines = 1, overflow = TextOverflow.Ellipsis)
                LocalStations.isLocal(station) -> Text(LocalStations.subtitle(station), style = line, color = f.text2, maxLines = 1, overflow = TextOverflow.Ellipsis)
                station.isNew -> Text("New!", style = line.copy(fontWeight = FontWeight.SemiBold), color = MaterialTheme.colorScheme.primary)
            }
            if (liveShow != null) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 3.dp)) {
                    LiveNowBadge()
                    if (liveShow.isNotEmpty()) Text(
                        liveShow,
                        style = MaterialTheme.typography.labelMedium,
                        color = f.text2,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.padding(start = 6.dp),
                    )
                }
            }
        }
        // The tags as the desktop's rows show them: up to two pills at the right, left of the live bars.
        RowTagPills(station.tags, { TagLook.colorOf(it, tagColors, settings) })
        if (current) {
            LiveBarsView(
                moving = playing,
                color = f.playing,
                modifier = Modifier.semantics { contentDescription = if (playing) "Playing" else "Paused" },
            )
        }
        if (handle != null) Icon(Icons.Filled.DragHandle, contentDescription = "Drag to move", tint = f.text2, modifier = handle)
    }
}

/** The one-line offline banner (task 214), in the desktop's words. */
@Composable
fun OfflineBanner() {
    Surface(color = MaterialTheme.colorScheme.secondaryContainer, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Filled.CloudOff, contentDescription = null, tint = MaterialTheme.colorScheme.onSecondaryContainer, modifier = Modifier.size(20.dp))
            Text(
                OfflineRules.BANNER,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSecondaryContainer,
                modifier = Modifier.padding(start = 12.dp),
            )
        }
    }
}

fun parseHex(hex: String): Color = try {
    Color(android.graphics.Color.parseColor(hex))
} catch (e: IllegalArgumentException) {
    Color(android.graphics.Color.parseColor(Settings.TAG_PALETTE[0]))
}

/**
 * The jump pills over the list: "<frequency> - <show>" for live shows out
 * of sight, above or below the list whichever way they lead, and "More
 * live shows" (opens the Schedule) when there are more than two.
 */
@Composable
private fun BoxScope.LivePills(
    pills: Schedule.Pills,
    below: List<Boolean>,
    /** Where "Jump to playing" shows: true below the list, false above, null not at all. */
    jumpBelow: Boolean?,
    onJumpToPlaying: () -> Unit,
    onJump: (String) -> Unit,
    onMore: () -> Unit,
) {
    if (pills.named.isEmpty() && jumpBelow == null) return
    val up = pills.named.withIndex().filter { !below[it.index] }.map { it.value }
    val down = pills.named.withIndex().filter { below[it.index] }.map { it.value }
    @Composable
    fun pill(text: String, onClick: () -> Unit) {
        Surface(
            onClick = onClick,
            shape = RoundedCornerShape(50),
            color = MaterialTheme.colorScheme.surfaceContainerHighest,
            contentColor = MaterialTheme.colorScheme.onSurface,
            border = BorderStroke(1.dp, LiveRed),
            shadowElevation = 3.dp,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 6.dp, end = 14.dp, top = 6.dp, bottom = 6.dp)) {
                LiveNowBadge()
                Text(text, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(start = 8.dp))
            }
        }
    }
    // Stacked on each side, "Jump to playing" nearest the edge and the live pills clear of it (mainwindow._place_live).
    Column(
        Modifier.align(Alignment.TopCenter).padding(top = 8.dp, start = 24.dp, end = 24.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        if (jumpBelow == false) JumpToPlaying(onJumpToPlaying)
        up.forEach { live -> pill(live.label) { onJump(live.station.id) } }
        if (pills.more && down.isEmpty()) pill("More live shows", onMore)
    }
    Column(
        Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp, start = 24.dp, end = 24.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        down.forEach { live -> pill(live.label) { onJump(live.station.id) } }
        if (pills.more && down.isNotEmpty()) pill("More live shows", onMore)
        if (jumpBelow == true) JumpToPlaying(onJumpToPlaying)
    }
}

/**
 * The desktop's floating "Jump to playing" button (theme's
 * QPushButton[floating]): opaque bg3 with a 1 dp line, round 15 dp
 * corners, the Saved Frequencies glyph in the accent; pressed, the text
 * and edge turn to the accent.
 */
@Composable
private fun JumpToPlaying(onClick: () -> Unit) {
    val f = MaterialTheme.freq
    val accent = MaterialTheme.colorScheme.primary
    val press = remember { MutableInteractionSource() }
    val pressed by press.collectIsPressedAsState()
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(15.dp),
        color = f.bg3,
        contentColor = if (pressed) accent else f.text,
        border = BorderStroke(1.dp, if (pressed) accent else f.line),
        shadowElevation = 3.dp,
        interactionSource = press,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 12.dp, end = 16.dp, top = 6.dp, bottom = 6.dp)) {
            Glyph("stations", null, tint = accent, size = 14.dp)
            Text("Jump to playing", fontSize = 12.sp, maxLines = 1, modifier = Modifier.padding(start = 6.dp))
        }
    }
}

/** The narrowest a Thumbnails tile gets; three across on a phone, more on a tablet. */
private val TILE_MIN = 104.dp

/** One visible item of the list or the grid, top and bottom along the scroll. */
private data class SeenItem(val index: Int, val key: Any, val top: Int, val bottom: Int)

/** What the list or the grid shows now, and where its viewport starts and ends. */
private class Seen(val items: List<SeenItem>, val start: Int, val end: Int)

private fun LazyListState.seen(): Seen = layoutInfo.let { info ->
    Seen(info.visibleItemsInfo.map { SeenItem(it.index, it.key, it.offset, it.offset + it.size) }, info.viewportStartOffset, info.viewportEndOffset)
}

private fun LazyGridState.seen(): Seen = layoutInfo.let { info ->
    Seen(info.visibleItemsInfo.map { SeenItem(it.index, it.key, it.offset.y, it.offset.y + it.size.height) }, info.viewportStartOffset, info.viewportEndOffset)
}

/**
 * One Thumbnails tile (the desktop's stationlist grid, "artwork-first
 * tiles, for when you recognize stations by their logo"): the picture,
 * the name under it, the frequency you're hearing on bg2 with the three
 * bars over the picture's corner, a red dot while a show is live.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun StationTile(
    station: Station,
    current: Boolean,
    playing: Boolean,
    checked: Boolean?,
    flashed: Boolean,
    down: Boolean,
    live: Boolean,
    unavailable: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val library = LocalContext.current.library
    val f = MaterialTheme.freq
    val background = when {
        checked == true || flashed -> MaterialTheme.colorScheme.secondaryContainer
        current -> f.bg2
        else -> Color.Transparent
    }
    Column(
        modifier
            .clip(RoundedCornerShape(8.dp))
            .background(background)
            .combinedClickable(onClick = onClick, onLongClick = onLongClick, onLongClickLabel = "Select")
            .alpha(if (unavailable && checked != true) 0.45f else 1f)
            .padding(6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(Modifier.fillMaxWidth().aspectRatio(1f)) {
            StationArt(library.artUri(station), station.displayName, Modifier.fillMaxSize(), corner = 6.dp)
            if (checked == true) {
                Box(Modifier.fillMaxSize().clip(RoundedCornerShape(6.dp)).background(Color.Black.copy(alpha = 0.45f)), contentAlignment = Alignment.Center) {
                    Icon(Icons.Filled.CheckCircle, contentDescription = "Selected", tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(36.dp))
                }
            }
            if (current) {
                Box(
                    Modifier.align(Alignment.BottomEnd).padding(4.dp).clip(RoundedCornerShape(4.dp)).background(f.bg0.copy(alpha = 0.8f)),
                ) {
                    LiveBarsView(
                        moving = playing,
                        color = f.playing,
                        modifier = Modifier.semantics { contentDescription = if (playing) "Playing" else "Paused" },
                    )
                }
            }
            if (live) Box(Modifier.align(Alignment.TopEnd).padding(5.dp).size(10.dp).clip(CircleShape).background(LiveRed).semantics { contentDescription = "Live now" })
        }
        Text(
            station.displayName,
            style = MaterialTheme.typography.labelMedium,
            color = if (down) f.text3 else if (current) f.text else f.text2,
            textAlign = TextAlign.Center,
            maxLines = 2,
            minLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 5.dp),
        )
    }
}
