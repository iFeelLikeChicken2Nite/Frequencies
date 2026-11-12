package com.frequencies.app.ui

import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.LibraryMusic
import androidx.compose.material.icons.filled.LiveTv
import androidx.compose.material.icons.filled.SmartDisplay
import androidx.compose.material3.AlertDialog
import com.frequencies.app.localMusic
import com.frequencies.core.LocalStations
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.foundation.layout.Row
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.heightIn
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.material3.LocalContentColor
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.ui.unit.dp
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Spacer
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Image
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.style.TextAlign
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.library
import com.frequencies.core.RadioBrowser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** The ways in that the Add Frequency screen offers besides the directory. */
class AddActions(
    val byAddress: () -> Unit,
    /** Opens a file; since build 173 only Settings' Restore uses it (the Add screen's File tile is gone). */
    val fromFile: () -> Unit,
    val scan: () -> Unit,
    /** A QR code in a saved picture or a screenshot (build 172's "Import QR code" tile). */
    val importQr: () -> Unit,
)

/**
 * Add Frequency: the ways in as tiles, like the desktop's Discover page
 * (an address, the directory search, a shared code from a picture, the
 * camera, your own music, YouTube, Twitch, iBroadcast; build 173 dropped
 * the File and Paste tiles, user: exactly these eight), then Browse's Featured and Discover under them (one screen
 * since build 170). Since build 172 there is no search bar on top (user:
 * "remove the top search bar from the add screen, make the second tile
 * 'search', make the third tile 'import QR code', make the fourth tile
 * 'scan QR code'"): the Search tile opens the directory search as a page of
 * this screen, and Featured's "Previously featured frequencies…" opens the
 * other page. Back returns to the tiles.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AddFrequencyScreen(actions: AddActions, connection: PlayerConnection, nowPlaying: NowPlaying) {
    val context = LocalContext.current
    val library = context.library
    val say = LocalSay.current
    val focus = LocalFocusManager.current
    val scope = rememberCoroutineScope()
    val stations by library.stations.collectAsStateWithLifecycle()
    val saved = remember(stations) { stations.map { it.url.trim().lowercase() }.toSet() }

    var query by rememberSaveable { mutableStateOf("") }
    var asked by remember { mutableStateOf<String?>(null) }
    var results by remember { mutableStateOf<List<RadioBrowser.Result>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var pages by remember { mutableIntStateOf(1) }
    /** Directory results played without saving: result key -> the station played. */
    val previews = remember { mutableStateMapOf<String, com.frequencies.core.Station>() }

    fun search(text: String, more: Boolean = false) {
        // Nothing typed: back to the tiles and Browse.
        if (text.isBlank() && !more) {
            asked = null
            results = emptyList()
            error = null
            return
        }
        val page = if (more) pages + 1 else 1
        loading = true
        error = null
        scope.launch {
            try {
                val found = withContext(Dispatchers.IO) { library.directory.search(text, limit = 40, offset = (page - 1) * 40) }
                results = if (more) results + found else found
                pages = page
                asked = text
            } catch (e: RadioBrowser.RadioBrowserError) {
                error = e.message
            } finally {
                loading = false
            }
        }
    }

    // A folder of your own music (the desktop's local files station, task 214): Android's folder picker.
    var explaining by remember { mutableStateOf(false) }
    // iBroadcast, YouTube or Twitch (AP10b).
    var service by remember { mutableStateOf<String?>(null) }
    val pickFolder = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { tree ->
        if (tree == null) return@rememberLauncherForActivityResult
        scope.launch {
            val music = context.localMusic
            val (path, name) = music.adopt(tree)
            val station = LocalStations.create(name, path, tree.toString(), library.now(), library.newId)
            val count = withContext(Dispatchers.IO) { music.tracks(station).size }
            if (count == 0) {
                music.releaseUnused(library.stations.value)
                say("That folder has no audio files in it.")
                return@launch
            }
            val added = library.add(listOf(station))
            if (added.isEmpty()) say("$name is already in Saved Frequencies.")
            else say("Added $name — $count tracks.")
        }
    }

    val browse = rememberBrowseState(connection)
    val searched = !asked.isNullOrEmpty()
    /** Which page of the screen: null = the tiles with Featured and Discover, "search", or "previous". */
    var page by rememberSaveable { mutableStateOf<String?>(null) }
    fun back() {
        if (page == "search") { query = ""; search("") }
        page = null
    }
    BackHandler(enabled = page != null) { back() }
    LaunchedEffect(page) { if (page == "previous") browse.loadPrevious() }
    val searchField = remember { FocusRequester() }
    LaunchedEffect(page) { if (page == "search" && !searched) { kotlinx.coroutines.delay(100); runCatching { searchField.requestFocus() } } }

    Scaffold(
        containerColor = MaterialTheme.freq.bg0,
        contentWindowInsets = WindowInsets(0),
        topBar = {
            FreqTopBar(
                when (page) {
                    "search" -> "Search"
                    "previous" -> "Previously featured frequencies"
                    else -> "Add Frequency…"
                },
                navigation = if (page == null) null else {
                    {
                        IconButton(onClick = ::back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back") }
                    }
                },
            ) {
                IconButton(onClick = {
                    when (page) {
                        "search" -> { if (searched) search(asked.orEmpty()) }
                        "previous" -> browse.loadPrevious(force = true)
                        else -> browse.refresh()
                    }
                    Unit
                }) {
                    Icon(Icons.Filled.Refresh, contentDescription = "Refresh", tint = MaterialTheme.freq.text2)
                }
            }
        },
    ) { padding ->
        LazyColumn(contentPadding = PaddingValues(top = padding.calculateTopPadding(), bottom = 8.dp)) {
            if (page == null) {
                // discover.SOURCES' ways in, four across; the user's order for the first four (build 172).
                item {
                    WayTiles(
                        listOf(
                            WayTile("URL", "Enter a stream address", { Glyph("link", null, size = 28.dp) }, actions.byAddress),
                            WayTile("Search", "Search stations by name or genre", { Glyph("search", null, size = 28.dp) }) { page = "search" },
                            WayTile("Import QR code", "Import a QR code from a picture", { Icon(Icons.Outlined.Image, null, Modifier.size(28.dp)) }, actions.importQr),
                            WayTile("Scan QR code", "Scan a QR code with the camera", { Icon(Icons.Filled.QrCodeScanner, null, Modifier.size(28.dp)) }, actions.scan),
                            WayTile("Local", "Choose a folder of your own music", { Glyph("folder", null, size = 28.dp) }) { explaining = true },
                            WayTile("YouTube", "Add a YouTube playlist", { Glyph("youtube", null, size = 28.dp) }) { service = "youtube" },
                            WayTile("Twitch", "Add a Twitch channel", { Glyph("twitch", null, size = 28.dp) }) { service = "twitch" },
                            WayTile("iBroadcast", "Add your iBroadcast library", { Icon(Icons.Filled.LibraryMusic, null, Modifier.size(28.dp)) }) { service = "ibroadcast" },
                        ),
                    )
                }
                browseItems(browse, nowPlaying, stations, saved, onPrevious = { page = "previous" })
                return@LazyColumn
            }
            if (page == "previous") {
                previousItems(browse, nowPlaying, stations, saved)
                return@LazyColumn
            }
            item {
                OutlinedTextField(
                    value = query,
                    onValueChange = { query = it },
                    placeholder = { Text("Search stations by name or genre") },
                    leadingIcon = { Glyph("search", null, tint = MaterialTheme.freq.text2, size = 20.dp) },
                    trailingIcon = if (query.isEmpty()) null else {
                        { IconButton(onClick = { query = ""; search("") }) { Icon(Icons.Filled.Clear, contentDescription = "Clear") } }
                    },
                    singleLine = true,
                    shape = RoundedCornerShape(6.dp),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { focus.clearFocus(); search(query) }),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).focusRequester(searchField),
                )
            }
            item { HintCard(com.frequencies.core.Hints.SEARCH) }
            if (!searched && !loading && error == null) return@LazyColumn
            if (searched) item {
                GroupLabel("Results for “$asked”")
            }
            error?.let { message ->
                item {
                    ListItem(
                        headlineContent = { Text(message) },
                        trailingContent = { TextButton(onClick = { search(query) }) { Text("Try again") } },
                    )
                }
            }
            if (!loading && error == null && asked != null && results.isEmpty()) {
                item { ListItem(headlineContent = { Text("No stations found.") }) }
            }
            itemsIndexed(results, key = { _, it -> it.uuid.ifEmpty { it.url } }) { index, r ->
                val have = r.url.trim().lowercase() in saved
                val key = r.uuid.ifEmpty { r.url }
                val playingThis = nowPlaying.stationId != null && nowPlaying.playWhenReady &&
                    (previews[key]?.id == nowPlaying.stationId || stations.any { it.id == nowPlaying.stationId && it.url.trim().equals(r.url.trim(), ignoreCase = true) })
                fun add() {
                    val added = library.add(listOf(RadioBrowser.toStation(r, library.newId, library.now())))
                    if (added.isNotEmpty()) {
                        say("Added ${added[0].displayName} to Saved Frequencies.")
                        scope.launch(Dispatchers.IO) { library.directory.registerClick(r.uuid) }
                    } else {
                        say("${r.name} is already in Saved Frequencies.")
                    }
                }
                val bits = listOf(r.country, r.tags.split(',').take(3).joinToString(", ").trim(), if (r.bitrate > 0) "${r.bitrate} kbps" else "")
                FreqRow(
                    index = index,
                    title = r.name,
                    subtitle = bits.filter { it.isNotBlank() }.joinToString(" · "),
                    current = playingThis,
                    // A tap plays it (the desktop's Discover plays a result without adding it); the + adds it.
                    onClickLabel = "Play ${r.name}",
                    onClick = {
                        val mine = stations.firstOrNull { it.url.trim().equals(r.url.trim(), ignoreCase = true) }
                        if (mine != null) {
                            connection.play(mine, library.sorted())
                        } else {
                            val station = previews.getOrPut(key) { RadioBrowser.toStation(r, library.newId, library.now()) }
                            library.preview(station)
                            connection.preview(station)
                            scope.launch(Dispatchers.IO) { library.directory.registerClick(r.uuid) }
                        }
                        say("Playing ${r.name}." + if (mine == null) " Tap + to keep it." else "")
                    },
                    leading = {
                        StationArt(r.favicon.takeIf { it.startsWith("http") }?.let(Uri::parse), r.name, Modifier.size(40.dp), corner = 6.dp)
                    },
                    trailing = {
                        if (playingThis) LiveBarsView(moving = true, color = MaterialTheme.freq.playing, modifier = Modifier.semantics { contentDescription = "Playing" })
                        if (have) {
                            Icon(
                                Icons.Filled.Check,
                                contentDescription = "In Saved Frequencies",
                                tint = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.padding(12.dp),
                            )
                        } else {
                            GlyphButton("plus", "Add ${r.name} to Saved Frequencies", onClick = ::add, tint = MaterialTheme.colorScheme.primary)
                        }
                    },
                )
            }
            if (loading) {
                item { Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
            } else if (results.size >= pages * 40 && error == null) {
                item {
                    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                        TextButton(onClick = { search(asked.orEmpty(), more = true) }) { Text("Show more") }
                    }
                }
            }
        }
    }
    BrowseSheet(browse, stations, saved)
    if (explaining) FolderPermissionDialog(onGo = { explaining = false; pickFolder.launch(null) }, onClose = { explaining = false })
    when (val kind = service) {
        "ibroadcast" -> IBroadcastAddDialog { service = null }
        "youtube", "twitch" -> ServiceLinkDialog(kind) { service = null }
        else -> Unit
    }
}

/** What the folder permission means, in plain words, before Android's own picker asks. */
@Composable
private fun FolderPermissionDialog(onGo: () -> Unit, onClose: () -> Unit) {
    AlertDialog(
        onDismissRequest = onClose,
        icon = { Icon(Icons.Filled.Folder, contentDescription = null) },
        title = { Text("Choose a music folder") },
        text = {
            Text(
                "Next, Android shows your folders. Pick the one with your music and tap “Use this folder”, then “Allow”.\n\n" +
                    "Frequencies can then play the songs in that folder and the folders inside it. It can't see anything else on your phone, " +
                    "and it never changes or deletes your files. Removing the station gives the permission back.",
            )
        },
        confirmButton = { TextButton(onClick = onGo) { Text("Choose folder") } },
        dismissButton = { TextButton(onClick = onClose) { Text("Cancel") } },
    )
}

/** One way in: the desktop's short label under its glyph, the full words for TalkBack. */
private class WayTile(val label: String, val says: String, val icon: @Composable () -> Unit, val onClick: () -> Unit)

/**
 * The ways in as the desktop's big source blocks (discover.SourceRow,
 * big): equal tiles four across, a glyph over a short label, on bg1.
 */
@Composable
private fun WayTiles(tiles: List<WayTile>) {
    val f = MaterialTheme.freq
    Column(Modifier.padding(horizontal = 12.dp, vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        tiles.chunked(4).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Top) {
                row.forEach { tile ->
                    Column(
                        Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(8.dp))
                            .background(f.bg1)
                            .border(1.dp, f.line, RoundedCornerShape(8.dp))
                            .clickable(onClickLabel = tile.says, role = Role.Button, onClick = tile.onClick)
                            .semantics(mergeDescendants = true) { contentDescription = tile.says }
                            .heightIn(min = 84.dp)
                            .padding(vertical = 12.dp, horizontal = 4.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterVertically),
                    ) {
                        CompositionLocalProvider(LocalContentColor provides f.text2) { tile.icon() }
                        // "Import QR code" and "Scan QR code" take two lines a quarter of a phone wide.
                        Text(tile.label, style = MaterialTheme.typography.labelMedium, color = f.text, maxLines = 2, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center)
                    }
                }
                // A short last row keeps the tiles their size.
                repeat(4 - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}
