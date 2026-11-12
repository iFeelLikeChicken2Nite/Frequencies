package com.frequencies.app.ui

import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.outlined.NotificationsOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.library
import com.frequencies.app.schedule
import com.frequencies.core.Browse
import com.frequencies.core.Schedule
import com.frequencies.core.Station
import com.frequencies.core.str
import java.time.LocalDateTime
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonObject

/** schedule.LIVE_RED: the desktop's live badge color, the same on every theme. */
val LiveRed = Color(0xFFD93030)

/** The red "LIVE NOW" badge (schedule.paint_live_pill). */
@Composable
fun LiveNowBadge(modifier: Modifier = Modifier) {
    Surface(shape = RoundedCornerShape(50), color = LiveRed, contentColor = Color.White, modifier = modifier) {
        Text(
            "LIVE NOW",
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(horizontal = 7.dp, vertical = 2.dp),
            maxLines = 1,
        )
    }
}

/** A show being added (show null) or edited. */
private data class Editing(val stationId: String?, val show: JsonObject?)

/**
 * The Schedule (schedule.SchedulePage), phone-shaped: a day at a time,
 * shows in time order, what's on the air marked LIVE NOW. Tapping a show
 * plays its frequency; a show never starts by itself. Press and hold a
 * show to edit or delete it; "Add a show" adds one to any frequency.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun ScheduleScreen(connection: PlayerConnection, nowPlaying: NowPlaying) {
    val context = LocalContext.current
    val library = context.library
    val say = LocalSay.current
    val stations by library.stations.collectAsStateWithLifecycle()
    val settings by library.settings.collectAsStateWithLifecycle()
    // Moves with the clock, so a show turns live (and stops) while the screen is open.
    val live by context.schedule.live.collectAsStateWithLifecycle()
    val now by produceState(LocalDateTime.now(), live) {
        while (true) {
            value = LocalDateTime.now()
            delay(30_000)
        }
    }
    val twelve = settings.clock12h
    var day by rememberSaveable { mutableIntStateOf(-1) }        // -1: today
    var editing by remember { mutableStateOf<Editing?>(null) }

    val saved = remember(stations) { Browse.saved(stations) }
    val today = now.dayOfWeek.value - 1
    val shownDay = if (day < 0) today else day
    val slots = remember(saved, shownDay, now.hour, now.minute) { Schedule.day(saved, shownDay, now) }
    val minute = now.hour * 60 + now.minute

    Scaffold(
        containerColor = MaterialTheme.freq.bg0,
        contentWindowInsets = WindowInsets(0),
        topBar = {
            FreqTopBar("Schedule")
        },
        floatingActionButton = {
            if (stations.isNotEmpty()) {
                ExtendedFloatingActionButton(
                    onClick = { editing = Editing(nowPlaying.stationId?.takeIf { library.isSavedId(it) } ?: saved.firstOrNull()?.id, null) },
                    icon = { Glyph("plus", null, size = 20.dp) },
                    text = { Text("Add a show") },
                    shape = RoundedCornerShape(8.dp),
                )
            }
        },
    ) { padding ->
        LazyColumn(contentPadding = PaddingValues(top = padding.calculateTopPadding(), bottom = 96.dp)) {
            item {
                LazyRow(
                    contentPadding = PaddingValues(horizontal = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    item { FreqChip("Today", selected = day < 0, onClick = { day = -1 }) }
                    items(7) { d ->
                        FreqChip(Schedule.DAY_NAMES[d], selected = day == d, onClick = { day = d })
                    }
                }
            }
            item {
                Text(
                    "Tap a show to play its frequency. Press and hold a show to edit it. Shows never start by themselves.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                )
            }
            if (slots.isEmpty()) {
                item {
                    ListItem(
                        headlineContent = {
                            Text(if (stations.none { Schedule.shows(it).isNotEmpty() }) "No shows yet" else "Nothing on ${if (day < 0) "today" else Schedule.DAY_NAMES_LONG[shownDay]}")
                        },
                        supportingContent = {
                            Text("Add the shows you like with Add a show. Frequencies can tell you when one starts; it never tunes in by itself.")
                        },
                    )
                }
            }
            val groups: List<Pair<String?, List<Schedule.Slot>>> = if (shownDay != today) listOf(null to slots) else listOf(
                "On the air now" to slots.filter { it.live },
                "Later today" to slots.filter { !it.live && !it.fromYesterday && it.start > minute },
                "Earlier today" to slots.filter { !it.live && !it.fromYesterday && it.start <= minute },
            )
            for ((title, group) in groups) {
                if (group.isEmpty()) continue
                if (title != null) {
                    item(key = "h:$title") { GroupLabel(title) }
                }
                itemsIndexed(group, key = { _, it -> "${it.station.id}:${it.show.hashCode()}:${it.fromYesterday}" }) { index, slot ->
                    ShowRow(
                        slot = slot,
                        index = index,
                        twelve = twelve,
                        playing = slot.station.id == nowPlaying.stationId && nowPlaying.playWhenReady,
                        onPlay = {
                            connection.play(slot.station, library.sorted())
                            say("Playing ${slot.station.displayName}.")
                        },
                        onEdit = { editing = Editing(slot.station.id, slot.show) },
                        onDelete = {
                            library.update(slot.station.id) { it["shows"] = Schedule.withShow(it, slot.show, null) }
                            say("Deleted ${slot.show["name"].str() ?: "the show"}.")
                        },
                    )
                }
            }
        }
    }

    editing?.let { e ->
        ShowDialog(
            editing = e,
            stations = saved,
            twelve = twelve,
            onDismiss = { editing = null },
            onSave = { stationId, show ->
                if (e.show != null && e.stationId != null && e.stationId != stationId) {
                    library.update(e.stationId) { it["shows"] = Schedule.withShow(it, e.show, null) }
                    library.update(stationId) { it["shows"] = Schedule.withShow(it, null, show) }
                } else {
                    library.update(stationId) { it["shows"] = Schedule.withShow(it, e.show, show) }
                }
                editing = null
            },
        )
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ShowRow(
    slot: Schedule.Slot,
    index: Int,
    twelve: Boolean,
    playing: Boolean,
    onPlay: () -> Unit,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    val library = LocalContext.current.library
    var menu by remember { mutableStateOf(false) }
    val name = slot.show["name"].str()?.trim().orEmpty().ifEmpty { "Untitled" }
    val time = "${Schedule.shownTime(slot.show["start"].str() ?: Schedule.hhmm(slot.start), twelve)} – " +
        Schedule.fmt(slot.end, twelve)
    Box {
        FreqRow(
            index = index,
            title = name,
            subtitle = (if (slot.fromYesterday) "$time · since yesterday" else time) + " · " + slot.station.displayName,
            subtitleColor = if (playing) MaterialTheme.freq.playing else MaterialTheme.freq.text2,
            subtitleLines = 2,
            current = slot.live,
            onClick = onPlay,
            onClickLabel = "Play ${slot.station.displayName}",
            onLongClick = { menu = true },
            onLongClickLabel = "Edit or delete",
            leading = { StationArt(library.artUri(slot.station), slot.station.displayName, Modifier.size(40.dp), corner = 6.dp) },
            trailing = {
                if (!Schedule.notifies(slot.show)) {
                    Icon(Icons.Outlined.NotificationsOff, contentDescription = "No reminder", tint = MaterialTheme.freq.text2, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                }
                if (slot.live) LiveNowBadge()
            },
        )
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            DropdownMenuItem(text = { Text("Play ${slot.station.displayName}") }, onClick = { menu = false; onPlay() })
            DropdownMenuItem(text = { Text("Edit show…") }, onClick = { menu = false; onEdit() })
            DropdownMenuItem(text = { Text("Delete show") }, onClick = { menu = false; onDelete() })
        }
    }
}

/** schedule.ShowDialog: which frequency, the show's name, its times and days, and whether to be told. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun ShowDialog(
    editing: Editing,
    stations: List<Station>,
    twelve: Boolean,
    onDismiss: () -> Unit,
    onSave: (stationId: String, show: JsonObject) -> Unit,
) {
    val context = LocalContext.current
    val old = editing.show
    var stationId by remember { mutableStateOf(editing.stationId ?: stations.firstOrNull()?.id) }
    var name by remember { mutableStateOf(old?.get("name").str().orEmpty()) }
    var start by remember { mutableIntStateOf(Schedule.parseTime(old?.get("start").str() ?: "09:00") ?: 9 * 60) }
    var end by remember { mutableIntStateOf(Schedule.parseTime(old?.get("end").str() ?: "10:00") ?: (start + 60)) }
    var days by remember { mutableStateOf(old?.let { Schedule.days(it) }?.takeIf { it.isNotEmpty() } ?: (0..6).toSet()) }
    var notify by remember { mutableStateOf(old?.let { Schedule.notifies(it) } ?: true) }
    var picking by remember { mutableStateOf<String?>(null) }      // "start" or "end"
    var choosingStation by remember { mutableStateOf(false) }
    val station = stations.firstOrNull { it.id == stationId }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (old == null) "Add a show" else "Edit show") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Box {
                    OutlinedButton(onClick = { choosingStation = true }, modifier = Modifier.fillMaxWidth()) {
                        Text(station?.displayName ?: "Choose a frequency", Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Icon(Icons.Filled.ArrowDropDown, contentDescription = null)
                    }
                    DropdownMenu(expanded = choosingStation, onDismissRequest = { choosingStation = false }) {
                        stations.forEach { s ->
                            DropdownMenuItem(text = { Text(s.displayName, maxLines = 1, overflow = TextOverflow.Ellipsis) }, onClick = {
                                stationId = s.id
                                choosingStation = false
                            })
                        }
                    }
                }
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Show name") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { picking = "start" }, modifier = Modifier.weight(1f)) { Text(Schedule.fmt(start, twelve)) }
                    Text("to")
                    OutlinedButton(onClick = { picking = "end" }, modifier = Modifier.weight(1f)) { Text(Schedule.fmt(end, twelve)) }
                }
                if (Math.floorMod(end, Schedule.DAY_MINUTES) <= Math.floorMod(start, Schedule.DAY_MINUTES)) {
                    Text("Ends the next day.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Schedule.DAY_NAMES.forEachIndexed { i, label ->
                        FilterChip(
                            selected = i in days,
                            onClick = { days = if (i in days) days - i else days + i },
                            label = { Text(label) },
                        )
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Tell me when it starts", Modifier.weight(1f))
                    FreqSwitch(checked = notify, onCheckedChange = { notify = it })
                }
                if (notify && !context.schedule.canNotify()) {
                    Text(
                        "Notifications are off for Frequencies, so you'll only see this while the app is open.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = stationId != null && days.isNotEmpty(),
                onClick = { stationId?.let { onSave(it, Schedule.showJson(name, start, end, days, notify, keep = old)) } },
            ) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )

    picking?.let { which ->
        val initial = if (which == "start") start else end
        val state = rememberTimePickerState(
            initialHour = Math.floorMod(initial, Schedule.DAY_MINUTES) / 60,
            initialMinute = Math.floorMod(initial, 60),
            is24Hour = !twelve,
        )
        AlertDialog(
            onDismissRequest = { picking = null },
            title = { Text(if (which == "start") "Starts" else "Ends") },
            text = { TimePicker(state = state) },
            confirmButton = {
                TextButton(onClick = {
                    val m = state.hour * 60 + state.minute
                    if (which == "start") start = m else end = m
                    picking = null
                }) { Text("OK") }
            },
            dismissButton = { TextButton(onClick = { picking = null }) { Text("Cancel") } },
        )
    }
}
