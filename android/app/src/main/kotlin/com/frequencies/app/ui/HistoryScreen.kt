package com.frequencies.app.ui

import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.lazy.itemsIndexed
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.clickable
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
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
import com.frequencies.app.library
import com.frequencies.core.SongHistory
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlinx.serialization.json.JsonObject

/**
 * The songs Song ID has named (historypage.py): search, Everything or
 * Favorites, one station or all, favorites first, then by day. Each song
 * can be starred or looked up in a music service.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryScreen() {
    val library = LocalContext.current.library
    val history by library.history.collectAsStateWithLifecycle()
    val settings by library.settings.collectAsStateWithLifecycle()
    var query by rememberSaveable { mutableStateOf("") }
    var favoritesOnly by rememberSaveable { mutableStateOf(false) }
    var station by rememberSaveable { mutableStateOf<String?>(null) }
    var menu by remember { mutableStateOf(false) }
    var clearing by remember { mutableStateOf(false) }

    val sorted = remember(history) { SongHistory.sorted(history) }
    val stations = remember(sorted) { sorted.map(SongHistory::station).filter { it.isNotEmpty() }.distinct().sortedBy { it.lowercase() } }
    val words = query.trim().lowercase()
    val shown = sorted.filter { e ->
        (!favoritesOnly || SongHistory.isFavorite(e)) &&
            (station == null || SongHistory.station(e) == station) &&
            (words.isEmpty() || listOf(SongHistory.title(e), SongHistory.artist(e), SongHistory.station(e)).any { it.lowercase().contains(words) })
    }
    val clock12 = settings.clock12h

    Scaffold(
        containerColor = MaterialTheme.freq.bg0,
        contentWindowInsets = WindowInsets(0),
        topBar = {
            FreqTopBar("History") {
                    Box {
                        GlyphButton("menu", "More", onClick = { menu = true })
                        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                            DropdownMenuItem(
                                text = { Text("Clear history") },
                                enabled = sorted.any { !SongHistory.isFavorite(it) },
                                onClick = { menu = false; clearing = true },
                            )
                        }
                    }
            }
        },
    ) { padding ->
        Column(Modifier.padding(top = padding.calculateTopPadding()).fillMaxSize()) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                placeholder = { Text("Songs, artists, stations") },
                leadingIcon = { Glyph("search", null, tint = MaterialTheme.freq.text2, size = 20.dp) },
                shape = RoundedCornerShape(6.dp),
                trailingIcon = {
                    if (query.isNotEmpty()) IconButton(onClick = { query = "" }) { Icon(Icons.Filled.Clear, contentDescription = "Clear search") }
                },
                singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
            )
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Row(Modifier.weight(1f).selectableGroup()) {
                    listOf(false to "Everything", true to "Favorites").forEach { (fav, label) ->
                        FreqChip(label, selected = favoritesOnly == fav, onClick = { favoritesOnly = fav })
                    }
                }
                StationFilter(stations, station) { station = it }
            }
            Text(
                plural(shown.size, "song", "songs"),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp),
            )
            if (shown.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(
                        if (sorted.isEmpty()) "Nothing here yet. Songs land here as they are identified." else "No songs match.",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(32.dp),
                    )
                }
                return@Column
            }
            val favorites = shown.filter(SongHistory::isFavorite)
            val rest = shown.filterNot(SongHistory::isFavorite)
            LazyColumn(Modifier.fillMaxSize()) {
                if (favorites.isNotEmpty() && !favoritesOnly) {
                    item { DayHeader("Favorites") }
                    item {
                        LazyRow(
                            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            items(favorites) { e -> FavoriteTile(e) }
                        }
                    }
                }
                val groups = (if (favoritesOnly) favorites else rest).groupBy { dayLabel(SongHistory.at(it)) }
                for ((day, entries) in groups) {
                    item { DayHeader(day) }
                    itemsIndexed(entries) { i, e -> SongRow(e, clock12, i) }
                }
            }
        }
    }

    if (clearing) {
        AlertDialog(
            onDismissRequest = { clearing = false },
            title = { Text("Clear history?") },
            text = { Text("Every song goes except your favorites.") },
            confirmButton = {
                TextButton(onClick = { library.clearHistory(); clearing = false }) { Text("Clear") }
            },
            dismissButton = { TextButton(onClick = { clearing = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun StationFilter(stations: List<String>, chosen: String?, onChoose: (String?) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        ComboBox(chosen ?: "All stations", onClick = { open = true }, modifier = Modifier.widthIn(max = 170.dp), contentDescription = "Choose a station")
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            DropdownMenuItem(text = { Text("All stations") }, onClick = { open = false; onChoose(null) })
            stations.forEach { s -> DropdownMenuItem(text = { Text(s) }, onClick = { open = false; onChoose(s) }) }
        }
    }
}

@Composable
private fun DayHeader(text: String) = GroupLabel(text)

@Composable
private fun FavoriteTile(e: JsonObject) {
    var menu by remember { mutableStateOf(false) }
    Box {
        Column(Modifier.width(112.dp).clickable { menu = true }) {
            Cover(e, Modifier.size(112.dp))
            Text(SongHistory.title(e), style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(top = 6.dp))
            Text(SongHistory.artist(e), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        SongEntryMenu(e, menu) { menu = false }
    }
}

@Composable
private fun SongRow(e: JsonObject, clock12: Boolean, index: Int) {
    val library = LocalContext.current.library
    var menu by remember { mutableStateOf(false) }
    val time = timeLabel(SongHistory.at(e), clock12)
    FreqRow(
        index = index,
        title = SongHistory.title(e),
        subtitle = listOf(SongHistory.artist(e), SongHistory.station(e), time).filter { it.isNotEmpty() }.joinToString(" · "),
        subtitleLines = 2,
        onClick = { menu = true },
        leading = { Cover(e, Modifier.size(40.dp), corner = 6.dp) },
        trailing = {
            Box {
                StarButton(SongHistory.isFavorite(e)) { on -> library.changeHistory { SongHistory.setFavorite(it, SongHistory.entryKey(e), on, library.now()) } }
                SongEntryMenu(e, menu) { menu = false }
            }
        },
    )
}

@Composable
private fun SongEntryMenu(e: JsonObject, open: Boolean, onClose: () -> Unit) {
    val library = LocalContext.current.library
    DropdownMenu(expanded = open, onDismissRequest = onClose) {
        SongSearchItems(SongHistory.artist(e), SongHistory.title(e), onClose)
        HorizontalDivider()
        val starred = SongHistory.isFavorite(e)
        DropdownMenuItem(
            text = { Text(if (starred) "Unfavorite" else "Favorite") },
            onClick = {
                onClose()
                library.changeHistory { SongHistory.setFavorite(it, SongHistory.entryKey(e), !starred, library.now()) }
            },
        )
    }
}

@Composable
private fun Cover(e: JsonObject, modifier: Modifier, corner: androidx.compose.ui.unit.Dp = 8.dp) {
    val url = SongHistory.coverUrl(e).takeIf { it.startsWith("http") }
    StationArt(url?.let(Uri::parse), SongHistory.title(e).ifEmpty { SongHistory.artist(e) }, modifier, corner = corner)
}

/** historypage._day_label */
private fun dayLabel(at: Double): String {
    if (at <= 0) return "Earlier"
    val zone = ZoneId.systemDefault()
    val d = Instant.ofEpochMilli((at * 1000).toLong()).atZone(zone).toLocalDate()
    val today = LocalDate.now(zone)
    return when {
        d == today -> "Today"
        d == today.minusDays(1) -> "Yesterday"
        d.isAfter(today.minusDays(7)) -> d.format(DateTimeFormatter.ofPattern("EEEE", Locale.US))
        d.year == today.year -> d.format(DateTimeFormatter.ofPattern("d MMMM", Locale.US))
        else -> d.format(DateTimeFormatter.ofPattern("d MMMM yyyy", Locale.US))
    }
}

private fun timeLabel(at: Double, clock12: Boolean): String {
    if (at <= 0) return ""
    val t = Instant.ofEpochMilli((at * 1000).toLong()).atZone(ZoneId.systemDefault())
    return t.format(DateTimeFormatter.ofPattern(if (clock12) "h:mm a" else "HH:mm", Locale.US))
}
