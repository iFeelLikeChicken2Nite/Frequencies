package com.frequencies.app.ui

import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.material3.CardDefaults
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.lazy.itemsIndexed
import android.content.Context
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.artists
import com.frequencies.app.library
import com.frequencies.core.Featured
import com.frequencies.core.FeaturedHistory
import com.frequencies.core.FeaturedList
import com.frequencies.core.RadioBrowser
import com.frequencies.core.Station
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject

/**
 * Browse (the desktop's Discover page): today's Featured frequencies, the
 * curator's hand-picked list with their own words, then the whole
 * radio-browser.info directory by Most listened, Trending, Random, Most
 * voted, New or Name. A tap plays without saving; + keeps it. Since build
 * 170 it is part of Add Frequency (user: "add and browse should not be
 * separate screens"): [rememberBrowseState] keeps what it shows,
 * [browseItems] lays it out under the ways in, [BrowseSheet] opens a card.
 */
@Stable
class BrowseState internal constructor(
    private val context: Context,
    private val scope: CoroutineScope,
    private val connection: PlayerConnection,
    private val say: (String) -> Unit,
) {
    private val library = context.library
    val featuredDir = File(context.filesDir, "data/featured")
    private val featured = FeaturedList(featuredDir, context.artists.http)
    /** Every list ever featured (desktop featuredhistory, build 172): beside the daily cache, as on the desktop. */
    private val history = FeaturedHistory(featuredDir, context.artists.http)
    /** Previously featured frequencies: null until first read from disk. */
    var previous by mutableStateOf<List<FeaturedHistory.Previous>?>(null)
    /** The quiet line under that list (looking, couldn't read, nothing yet). */
    var previousNote by mutableStateOf("")
    private var previousJob: kotlinx.coroutines.Job? = null
    var cards by mutableStateOf<List<JsonObject>?>(null)
    var openCard by mutableStateOf<JsonObject?>(null)
    var mode by mutableStateOf("clickcount")
    var results by mutableStateOf<List<RadioBrowser.Result>>(emptyList())
    var loading by mutableStateOf(false)
    var error by mutableStateOf<String?>(null)
    var shownMode by mutableStateOf<String?>(null)
    val previews = mutableStateMapOf<String, Station>()

    fun loadCards(force: Boolean = false) {
        scope.launch {
            cards = withContext(Dispatchers.IO) {
                featured.load(force = force).also { list -> runCatching { history.noteSeen(list) } }
            }
        }
    }

    /**
     * discover.PreviouslyFeaturedWindow: what is on disk at once, then any
     * older lists read from GitHub (once a day), then the list again.
     */
    fun loadPrevious(force: Boolean = false) {
        if (previousJob?.isActive == true) return
        previousJob = scope.launch {
            val now = cards.orEmpty()
            previous = withContext(Dispatchers.IO) { history.previous(now) }
            previousNote = if (previous.isNullOrEmpty()) "Looking for older lists…" else ""
            val why = withContext(Dispatchers.IO) {
                runCatching { history.refresh(force = force, offline = com.frequencies.app.Connectivity.offline.value) }.getOrElse { "couldn't reach GitHub" }
            }
            val found = withContext(Dispatchers.IO) { history.previous(cards.orEmpty()) }
            previous = found
            previousNote = when {
                found.isNotEmpty() -> if (why.isEmpty()) "" else "Showing the ones this phone knows about ($why)."
                why.isNotEmpty() -> "Nothing to show yet: older lists couldn't be read ($why). Try again later."
                else -> "Nothing has been featured before today's picks."
            }
        }
    }

    fun load(m: String) {
        loading = true
        error = null
        scope.launch {
            try {
                results = withContext(Dispatchers.IO) {
                    if (m == "new") library.directory.newStations(40) else library.directory.search("", limit = 40, order = m)
                }
                shownMode = m
            } catch (e: RadioBrowser.RadioBrowserError) {
                error = e.message
            } finally {
                loading = false
            }
        }
    }

    /** The top bar's Refresh: today's cards and the directory again. */
    fun refresh() {
        loadCards(force = true)
        load(mode)
    }

    /** A featured entry as a station (its art written beside the others), or the saved one with that address. */
    fun featuredStation(entry: JsonObject, stations: List<Station>): Station {
        val url = FeaturedList.url(entry).trim()
        return stations.firstOrNull { it.url.trim().equals(url, ignoreCase = true) }
            ?: previews.getOrPut("featured:$url") { Featured.toStation(entry, library.files.artDir, library.newId) }
    }

    fun play(station: Station, isSaved: Boolean) {
        if (isSaved) connection.play(station, library.sorted())
        else { library.preview(station); connection.preview(station) }
        say("Playing ${station.displayName}." + if (!isSaved) " Tap + to keep it." else "")
    }

    fun addFeatured(entry: JsonObject) {
        val added = library.add(listOf(Featured.toStation(entry, library.files.artDir, library.newId)))
        say(if (added.isEmpty()) "${FeaturedList.card(entry).name} is already in Saved Frequencies." else "Added ${added[0].displayName} to Saved Frequencies.")
    }

    fun playResult(r: RadioBrowser.Result, stations: List<Station>) {
        val key = r.uuid.ifEmpty { r.url }
        val mine = stations.firstOrNull { it.url.trim().equals(r.url.trim(), ignoreCase = true) }
        play(mine ?: previews.getOrPut(key) { RadioBrowser.toStation(r, library.newId, library.now()) }, mine != null)
        scope.launch(Dispatchers.IO) { library.directory.registerClick(r.uuid) }
    }

    fun addResult(r: RadioBrowser.Result) {
        val added = library.add(listOf(RadioBrowser.toStation(r, library.newId, library.now())))
        say(if (added.isEmpty()) "${r.name} is already in Saved Frequencies." else "Added ${added[0].displayName} to Saved Frequencies.")
        if (added.isNotEmpty()) scope.launch(Dispatchers.IO) { library.directory.registerClick(r.uuid) }
    }
}

@Composable
fun rememberBrowseState(connection: PlayerConnection): BrowseState {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val say = LocalSay.current
    val state = remember { BrowseState(context, scope, connection, say) }
    LaunchedEffect(Unit) { if (state.cards == null) state.loadCards() }
    LaunchedEffect(state.mode) { if (state.shownMode != state.mode || state.results.isEmpty()) state.load(state.mode) }
    return state
}

/** Featured and Discover as rows of a LazyColumn (the Add Frequency screen's, under its ways in). */
fun LazyListScope.browseItems(state: BrowseState, nowPlaying: NowPlaying, stations: List<Station>, saved: Set<String>, onPrevious: () -> Unit) {
    item { SectionTitle("Featured") }
    item {
        val list = state.cards
        when {
            list == null -> Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            list.isEmpty() -> Text(
                FeaturedList.EMPTY,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            )
            else -> LazyRow(contentPadding = PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                items(list) { entry ->
                    FeaturedCard(entry, state.featuredDir, have = FeaturedList.url(entry).trim().lowercase() in saved) { state.openCard = entry }
                }
            }
        }
    }
    // The desktop's "Previously featured frequencies…" as a real button under the cards
    // (build 172 had it as a text link beside the heading; build 173 makes it a filled tonal button).
    item {
        FilledTonalButton(
            onClick = onPrevious,
            modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 12.dp).heightIn(min = 48.dp),
        ) {
            Icon(Icons.Filled.History, null, Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Previously featured frequencies…")
        }
    }
    item { SectionTitle("Discover") }
    item {
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            RadioBrowser.BROWSE.forEach { (key, label) ->
                FreqChip(
                    label,
                    selected = state.mode == key,
                    onClick = { if (state.mode == key && key == "random") state.load(key) else state.mode = key },
                )
            }
        }
    }
    state.error?.let { message ->
        item {
            ListItem(
                headlineContent = { Text(message) },
                trailingContent = { TextButton(onClick = { state.load(state.mode) }) { Text("Try again") } },
            )
        }
    }
    if (!state.loading && state.error == null && state.shownMode == state.mode && state.results.isEmpty()) {
        item { ListItem(headlineContent = { Text("No stations found.") }) }
    }
    if (state.loading) {
        item { Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
    } else {
        itemsIndexed(state.results, key = { _, it -> "browse:" + it.uuid.ifEmpty { it.url } }) { index, r ->
            val key = r.uuid.ifEmpty { r.url }
            val have = r.url.trim().lowercase() in saved
            val playingThis = nowPlaying.stationId != null && nowPlaying.playWhenReady &&
                (state.previews[key]?.id == nowPlaying.stationId || stations.any { it.id == nowPlaying.stationId && it.url.trim().equals(r.url.trim(), ignoreCase = true) })
            val bits = listOf(r.country, r.tags.split(',').take(3).joinToString(", ").trim(), if (r.bitrate > 0) "${r.bitrate} kbps" else "")
            FreqRow(
                index = index,
                title = r.name,
                subtitle = bits.filter { it.isNotBlank() }.joinToString(" · "),
                current = playingThis,
                onClickLabel = "Play ${r.name}",
                onClick = { state.playResult(r, stations) },
                leading = { StationArt(r.favicon.takeIf { it.startsWith("http") }?.let(Uri::parse), r.name, Modifier.size(40.dp), corner = 6.dp) },
                trailing = {
                    if (playingThis) LiveBarsView(moving = true, color = MaterialTheme.freq.playing, modifier = Modifier.semantics { contentDescription = "Playing" })
                    if (have) {
                        Icon(Icons.Filled.Check, contentDescription = "In Saved Frequencies", tint = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(12.dp))
                    } else {
                        GlyphButton("plus", "Add ${r.name} to Saved Frequencies", tint = MaterialTheme.colorScheme.primary, onClick = { state.addResult(r) })
                    }
                },
            )
        }
    }
}

/**
 * discover.PreviouslyFeaturedWindow as rows of the Add screen's list: every
 * frequency featured before, newest first, with the days it was featured.
 * A tap opens the curator's card (Play, Add); + adds it.
 */
fun LazyListScope.previousItems(state: BrowseState, nowPlaying: NowPlaying, stations: List<Station>, saved: Set<String>) {
    item {
        Text(
            "Every frequency featured before, newest first, with the days it was featured. Tap one for its card; + adds it.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
        )
    }
    val list = state.previous
    if (list == null) {
        item { Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
        return
    }
    itemsIndexed(list, key = { i, p -> "prev:$i:" + FeaturedList.url(p.item) }) { index, p ->
        val entry = p.item
        val card = remember(entry) { FeaturedList.card(entry) }
        val url = FeaturedList.url(entry).trim()
        val have = url.lowercase() in saved
        val playingThis = nowPlaying.stationId != null && nowPlaying.playWhenReady &&
            (state.previews["featured:$url"]?.id == nowPlaying.stationId || stations.any { it.id == nowPlaying.stationId && it.url.trim().equals(url, ignoreCase = true) })
        val art = remember(entry) { FeaturedList.artFile(state.featuredDir, entry) }
        FreqRow(
            index = index,
            title = card.name,
            subtitle = FeaturedHistory.whenText(p.first, p.last),
            current = playingThis,
            onClickLabel = "Open ${card.name}'s card",
            onClick = { state.openCard = entry },
            leading = {
                StationArt(art?.let(Uri::fromFile) ?: FeaturedList.written(entry, "favicon").takeIf { it.startsWith("http") }?.let(Uri::parse), card.name, Modifier.size(40.dp), corner = 6.dp)
            },
            trailing = {
                if (playingThis) LiveBarsView(moving = true, color = MaterialTheme.freq.playing, modifier = Modifier.semantics { contentDescription = "Playing" })
                if (have) {
                    Icon(Icons.Filled.Check, contentDescription = "In Saved Frequencies", tint = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(12.dp))
                } else {
                    GlyphButton("plus", "Add ${card.name} to Saved Frequencies", tint = MaterialTheme.colorScheme.primary, onClick = { state.addFeatured(entry) })
                }
            },
        )
    }
    if (state.previousNote.isNotEmpty()) {
        item {
            Text(
                state.previousNote,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
            )
        }
    }
}

/** A featured card opened: its picture, words, Play and Add. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BrowseSheet(state: BrowseState, stations: List<Station>, saved: Set<String>) {
    val entry = state.openCard ?: return
    val card = FeaturedList.card(entry)
    val have = FeaturedList.url(entry).trim().lowercase() in saved
    ModalBottomSheet(onDismissRequest = { state.openCard = null }) {
        Column(Modifier.padding(horizontal = 24.dp).padding(bottom = 24.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                val art = remember(entry) { FeaturedList.artFile(state.featuredDir, entry) }
                StationArt(art?.let(Uri::fromFile) ?: FeaturedList.written(entry, "favicon").takeIf { it.startsWith("http") }?.let(Uri::parse), card.name, Modifier.size(72.dp))
                Spacer(Modifier.width(16.dp))
                Column(Modifier.weight(1f)) {
                    Text(card.name, style = MaterialTheme.typography.titleLarge)
                    if (card.subtitle.isNotEmpty()) Text(card.subtitle, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (card.heading.isNotEmpty()) Text(card.heading, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 16.dp))
            if (card.body.isNotEmpty()) {
                Text(
                    card.body,
                    style = MaterialTheme.typography.bodyLarge,
                    fontStyle = if (card.bodyIsBlurb) FontStyle.Normal else FontStyle.Italic,
                    color = if (card.bodyIsBlurb) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
            Spacer(Modifier.height(20.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(onClick = { state.play(state.featuredStation(entry, stations), have); state.openCard = null }) {
                    Glyph("play", null, size = 18.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("Play")
                }
                if (have) {
                    FilledTonalButton(onClick = {}, enabled = false) { Text("In Saved Frequencies") }
                } else {
                    FilledTonalButton(onClick = { state.addFeatured(entry); state.openCard = null }) {
                        Glyph("plus", null, size = 18.dp)
                        Spacer(Modifier.width(8.dp))
                        Text("Add")
                    }
                }
            }
        }
    }
}

@Composable
private fun SectionTitle(text: String) = GroupLabel(text)

/** stationcard: the picture, the name, what it is, and the curator's heading and blurb (or what is known about the stream). */
@Composable
private fun FeaturedCard(entry: JsonObject, artDir: File, have: Boolean, onOpen: () -> Unit) {
    val card = remember(entry) { FeaturedList.card(entry) }
    val art = remember(entry) { FeaturedList.artFile(artDir, entry) }
    ElevatedCard(
        onClick = onOpen,
        modifier = Modifier.width(260.dp),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.freq.bg1),
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StationArt(art?.let(Uri::fromFile) ?: FeaturedList.written(entry, "favicon").takeIf { it.startsWith("http") }?.let(Uri::parse), card.name, Modifier.size(56.dp))
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(card.name, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (card.subtitle.isNotEmpty()) Text(card.subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                if (have) Icon(Icons.Filled.Check, contentDescription = "In Saved Frequencies", tint = MaterialTheme.colorScheme.primary)
            }
            if (card.heading.isNotEmpty()) {
                Text(card.heading, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 12.dp))
            }
            if (card.body.isNotEmpty()) {
                Text(
                    card.body,
                    style = MaterialTheme.typography.bodyMedium,
                    fontStyle = if (card.bodyIsBlurb) FontStyle.Normal else FontStyle.Italic,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 4,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
        }
    }
}
