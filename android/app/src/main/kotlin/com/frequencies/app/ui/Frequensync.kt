package com.frequencies.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TriStateCheckbox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.state.ToggleableState
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.platform.LocalContext
import com.frequencies.app.sync
import com.frequencies.core.SyncData

/**
 * "Frequensync" (the user's name for it; desktop build 169's
 * FrequensyncDialog, sync protocol 6.4 and 9): what this phone syncs with
 * each paired computer. Asked after pairing ("Not now" / "Sync") and on
 * every Sync now ("Cancel" / "Sync"); Settings' "Choose what to sync…"
 * opens it with "Cancel" / "Save". The choice is kept per computer and
 * automatic syncs use it without asking. A phone never shows the desktop's
 * "Desktop only" group (it neither sends nor takes those settings).
 */
@Composable
fun FrequensyncDialog(
    onClose: () -> Unit,
    peers: List<String>? = null,
    syncAfter: Boolean = false,
    first: Boolean = false,
) {
    val sync = LocalContext.current.sync
    val ui by sync.ui.collectAsStateWithLifecycle()
    val ids = remember { (peers ?: ui.rows.map { it.id }).filter { id -> ui.rows.any { it.id == id } } }
    if (ids.isEmpty()) {
        LaunchedEffect(Unit) { onClose() }
        return
    }
    val names = remember { ui.rows.associate { it.id to it.name } }
    // Every computer's choice while the dialog is open; saved together.
    val choices = remember { mutableStateMapOf<String, SyncData.What>().apply { ids.forEach { put(it, sync.savedWhat(it)) } } }
    var current by remember { mutableStateOf(ids.first()) }
    val what = choices.getValue(current)

    fun set(part: String? = null, group: String? = null, on: Boolean) {
        val parts = LinkedHashMap(what.parts)
        val groups = LinkedHashMap(what.groups)
        // Settings was off: its groups showed unticked, so ticking one ticks only that one (as the desktop reads it).
        if (group != null && !what.settings) PHONE_GROUPS.forEach { (g, _, _) -> groups[g] = false }
        if (part != null) parts[part] = on
        if (group != null) groups[group] = on
        when (part) {
            // Pictures travel with each frequency, so they need it (and come back with it).
            "stations" -> parts["art"] = on
            "settings" -> PHONE_GROUPS.forEach { (g, _, _) -> groups[g] = on }
        }
        if (group != null || part == "settings") parts["settings"] = PHONE_GROUPS.any { (g, _, _) -> groups[g] != false }
        choices[current] = SyncData.What(parts, groups)
    }

    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Frequensync") },
        text = {
            Column(Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState())) {
                Text("What to sync with ${names[current] ?: "your other device"}", style = MaterialTheme.typography.titleSmall)
                if (ids.size > 1) {
                    ids.forEach { id ->
                        Row(
                            Modifier.fillMaxWidth().clickable { current = id }.padding(vertical = 2.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(selected = id == current, onClick = { current = id })
                            Text(names[id].orEmpty())
                        }
                    }
                }
                Column(Modifier.padding(top = 8.dp)) {
                    PartRow("Saved Frequencies", PARTS.getValue("stations"), what.stations) { set("stations", on = it) }
                    PartRow("Station pictures", PARTS.getValue("art"), what.art, indent = 1, enabled = what.stations) { set("art", on = it) }
                    val shown = PHONE_GROUPS.map { (g, _, _) -> what.settings && what.group(g) }
                    PartRow(
                        "Settings", PARTS.getValue("settings"),
                        state = when {
                            shown.all { it } -> ToggleableState.On
                            shown.none { it } -> ToggleableState.Off
                            else -> ToggleableState.Indeterminate
                        },
                    ) { set("settings", on = it) }
                    PHONE_GROUPS.forEach { (g, label, hint) ->
                        PartRow(label, hint, what.settings && what.group(g), indent = 1) { set(group = g, on = it) }
                    }
                    PartRow("Favorites", PARTS.getValue("favorites"), what.favorites) { set("favorites", on = it) }
                    PartRow("History", PARTS.getValue("history"), what.history) { set("history", on = it) }
                }
                Text(
                    "Anything you uncheck isn't sent to or taken from that device, and stays as it is on each one.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = {
                sync.setWhat(choices.toMap(), sync = syncAfter)
                onClose()
            }) { Text(if (syncAfter) "Sync" else "Save") }
        },
        dismissButton = { TextButton(onClick = onClose) { Text(if (first) "Not now" else "Cancel") } },
    )
}

@Composable
private fun PartRow(
    label: String,
    hint: String,
    checked: Boolean,
    indent: Int = 0,
    enabled: Boolean = true,
    onChange: (Boolean) -> Unit,
) = PartRow(label, hint, if (checked) ToggleableState.On else ToggleableState.Off, indent, enabled, tri = false, onChange)

@Composable
private fun PartRow(
    label: String,
    hint: String,
    state: ToggleableState,
    indent: Int = 0,
    enabled: Boolean = true,
    tri: Boolean = true,
    onChange: (Boolean) -> Unit,
) {
    val on = state == ToggleableState.On
    Row(
        Modifier
            .fillMaxWidth()
            .toggleable(value = on, enabled = enabled, role = Role.Checkbox, onValueChange = { onChange(state != ToggleableState.On) })
            .padding(start = (indent * 24).dp, top = 2.dp, bottom = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (tri) TriStateCheckbox(state = state, onClick = null, enabled = enabled)
        else Checkbox(checked = on, onCheckedChange = null, enabled = enabled)
        Column(Modifier.padding(start = 8.dp)) {
            Text(
                label,
                style = MaterialTheme.typography.bodyLarge,
                color = if (enabled) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
            )
            Text(hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** The desktop's PARTS tips, word for word. */
private val PARTS = mapOf(
    "stations" to "Your stations and their settings: names, tags, equalizer, gain, ad cover and the rest",
    "art" to "The picture of each station",
    "settings" to "The settings ticked below",
    "favorites" to "Starred songs",
    "history" to "Songs heard",
)

/** The desktop's GROUPS, without "Desktop only" (shown only between desktops). */
private val PHONE_GROUPS = listOf(
    Triple("look", "Look", "Theme, accent color, 12-hour clock"),
    Triple("playback", "Playback", "Static, crossfade, normalize, skips and checks, sleep timer, shuffle, 10-band EQ"),
    Triple("lyrics", "Lyrics and artists", "Lyrics, artist info, photo pack, band logos, lyric size, songs kept"),
    Triple("list", "Saved Frequencies list", "Sort, tag colors and spare tags, jump to playing"),
)
