package com.frequencies.app.ui

import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TriStateCheckbox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.state.ToggleableState
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.Importer
import com.frequencies.app.Sharing
import com.frequencies.app.artists
import com.frequencies.core.ArtLink
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import com.frequencies.app.library
import com.frequencies.core.Playlists
import com.frequencies.core.Station
import com.frequencies.core.TagLook
import com.frequencies.core.Tags
import com.frequencies.core.stringArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive

@Composable
fun ColorDot(hex: String, size: Int = 12) {
    Box(Modifier.size(size.dp).clip(CircleShape).background(parseHex(hex)))
}

/** Tags… for the selected stations: each tag on all, some or none of them. */
@Composable
fun TagsForSelectionDialog(ids: Set<String>, onClose: () -> Unit) {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    val stations by library.stations.collectAsStateWithLifecycle()
    val chosen = stations.filter { it.id in ids }
    val all = remember { Tags.library(stations, settings) }
    // tag (lowercase) -> true on all, false on none; missing = left as it is.
    var changes by remember { mutableStateOf(mapOf<String, Boolean>()) }
    var extra by remember { mutableStateOf(listOf<String>()) }
    var newTag by remember { mutableStateOf("") }
    val colors = remember(all, extra, settings) { TagLook.colors(all + extra, settings) }
    fun state(tag: String): ToggleableState {
        changes[tag.lowercase()]?.let { return if (it) ToggleableState.On else ToggleableState.Off }
        val n = chosen.count { s -> s.tags.any { it.equals(tag, ignoreCase = true) } }
        return when (n) { 0 -> ToggleableState.Off; chosen.size -> ToggleableState.On; else -> ToggleableState.Indeterminate }
    }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text(if (chosen.size == 1) "Tags for ${chosen[0].displayName}" else "Tags for ${chosen.size} frequencies") },
        text = {
            Column {
                LazyColumn(Modifier.heightIn(max = 320.dp)) {
                    items(all + extra) { tag ->
                        ListItem(
                            modifier = Modifier.clickable { changes = changes + (tag.lowercase() to (state(tag) != ToggleableState.On)) },
                            leadingContent = {
                                TriStateCheckbox(state = state(tag), onClick = {
                                    changes = changes + (tag.lowercase() to (state(tag) != ToggleableState.On))
                                })
                            },
                            // The desktop's tag picker pill: filled once the tag is on every chosen frequency.
                            headlineContent = { TagPill(tag, TagLook.colorOf(tag, colors, settings), if (state(tag) == ToggleableState.On) TagLook.PICKED else TagLook.PICK) },
                        )
                    }
                }
                OutlinedTextField(
                    value = newTag,
                    onValueChange = { newTag = it },
                    label = { Text("New tag") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = {
                        val t = newTag.trim()
                        if (t.isNotEmpty() && (all + extra).none { it.equals(t, ignoreCase = true) }) extra = extra + t
                        if (t.isNotEmpty()) changes = changes + (t.lowercase() to true)
                        newTag = ""
                    }),
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val pending = newTag.trim()
                val names = (all + extra + listOf(pending).filter { it.isNotEmpty() })
                val final = if (pending.isNotEmpty()) changes + (pending.lowercase() to true) else changes
                library.changeStationsAndSettings { list, s ->
                    var out = list
                    for ((key, on) in final) {
                        val tag = names.firstOrNull { it.lowercase() == key } ?: key
                        out = Tags.set(out, ids, tag, on)
                        if (on) s.tagColor(tag, assign = true)
                    }
                    out
                }
                onClose()
            }) { Text("Done") }
        },
        dismissButton = { TextButton(onClick = onClose) { Text("Cancel") } },
    )
}

/** Manage tags: rename, recolor, remove, or make one no station has yet. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TagManagerDialog(onClose: () -> Unit) {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    val stations by library.stations.collectAsStateWithLifecycle()
    val tags = Tags.library(stations, settings)
    val colors = remember(tags, settings) { TagLook.colors(tags, settings) }
    var renaming by remember { mutableStateOf<String?>(null) }
    var coloring by remember { mutableStateOf<String?>(null) }
    var removing by remember { mutableStateOf<String?>(null) }
    var making by remember { mutableStateOf(false) }

    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Scaffold(
            containerColor = MaterialTheme.freq.bg0,
            topBar = {
                FreqTopBar("Tags", navigation = { IconButton(onClick = onClose) { Icon(Icons.Filled.Close, contentDescription = "Close") } }) {
                    GlyphButton("plus", "New tag", onClick = { making = true })
                }
            },
        ) { padding ->
            LazyColumn(Modifier.padding(padding).fillMaxSize()) {
                if (tags.isEmpty()) item { ListItem(headlineContent = { Text("No tags yet. Tap + to make one.") }) }
                itemsIndexed(tags) { index, tag ->
                    val count = stations.count { s -> s.tags.any { it.equals(tag, ignoreCase = true) } }
                    FreqRow(
                        index = index,
                        title = tag,
                        titleContent = { TagPill(tag, TagLook.colorOf(tag, colors, settings), TagLook.OFF) },
                        subtitle = if (count == 0) "On no frequency yet" else plural(count, "frequency", "frequencies"),
                        onClick = { renaming = tag },
                        onClickLabel = "Rename",
                        leading = {
                            IconButton(onClick = { coloring = tag }, modifier = Modifier.semantics { contentDescription = "Color of $tag" }) { ColorDot(TagLook.colorOf(tag, colors, settings), 20) }
                        },
                        trailing = {
                            GlyphButton("edit", "Rename", onClick = { renaming = tag }, tint = MaterialTheme.freq.text2)
                            GlyphButton("trash", "Remove", onClick = { removing = tag }, tint = MaterialTheme.freq.text2)
                        },
                    )
                }
            }
        }
    }

    renaming?.let { old ->
        NameDialog("Rename tag", old, onClose = { renaming = null }) { new ->
            library.changeStationsAndSettings { list, s -> Tags.rename(list, s, mapOf(old to new)) }
        }
    }
    if (making) {
        NameDialog("New tag", "", onClose = { making = false }) { new ->
            library.updateSettings { s ->
                Tags.setSpare(s, Tags.spare(s) + new)
                s.tagColor(new, assign = true)
            }
        }
    }
    removing?.let { tag ->
        AlertDialog(
            onDismissRequest = { removing = null },
            title = { Text("Remove “$tag”?") },
            text = { Text("It comes off every frequency that has it. The frequencies stay.") },
            confirmButton = {
                TextButton(onClick = {
                    library.changeStationsAndSettings { list, s -> Tags.remove(list, s, tag) }
                    removing = null
                }) { Text("Remove") }
            },
            dismissButton = { TextButton(onClick = { removing = null }) { Text("Cancel") } },
        )
    }
    coloring?.let { tag ->
        AlertDialog(
            onDismissRequest = { coloring = null },
            title = { Text("Color for “$tag”") },
            text = {
                LazyColumn(Modifier.heightIn(max = 360.dp)) {
                    items(Tags.COLOR_CHOICES.entries.toList()) { (label, hex) ->
                        ListItem(
                            modifier = Modifier.clickable {
                                library.updateSettings { Tags.setColor(it, tag, hex) }
                                coloring = null
                            },
                            leadingContent = { ColorDot(hex, 20) },
                            headlineContent = { Text(label) },
                        )
                    }
                }
            },
            confirmButton = { TextButton(onClick = { coloring = null }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun NameDialog(title: String, initial: String, onClose: () -> Unit, onDone: (String) -> Unit) {
    var text by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text(title) },
        text = {
            OutlinedTextField(value = text, onValueChange = { text = it }, singleLine = true, modifier = Modifier.fillMaxWidth())
        },
        confirmButton = {
            TextButton(enabled = text.isNotBlank(), onClick = { onDone(text.trim()); onClose() }) { Text("OK") }
        },
        dismissButton = { TextButton(onClick = onClose) { Text("Cancel") } },
    )
}

/**
 * One station's QR code (the desktop's Share window), with the code and the
 * picture to send on. A picture of the user's own is uploaded first (desktop
 * build 128) so the code carries a link to it at full size; until that's
 * done the share buttons wait, and a failed upload keeps the small copy.
 */
@Composable
fun QrDialog(station: Station, onClose: () -> Unit) {
    val context = LocalContext.current
    val service = remember { ArtLink.service(context.artists.addresses) }
    var shown by remember(station.id) { mutableStateOf(station) }
    val code = remember(shown) { Sharing.code(shown) }
    // The desktop's frame: the station's name set into a border in its own color.
    val bitmap = remember(code) { Sharing.qr(code, label = shown.displayName, accent = Sharing.stationColor(shown)) }
    var linking by remember(station.id) { mutableStateOf(ArtLink.needsUpload(station, service)) }
    var said by remember(station.id) { mutableStateOf(if (linking) ArtLink.WAITING else "") }
    LaunchedEffect(station.id) {
        if (!linking) return@LaunchedEffect
        val linked = withContext(Dispatchers.IO) { Sharing.linkPicture(context, station) }
        if (linked != null) { shown = linked; said = "" } else said = ArtLink.FAILED
        linking = false
    }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text(station.displayName) },
        text = {
            Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Image(bitmap.asImageBitmap(), contentDescription = "QR code", Modifier.fillMaxWidth().aspectRatio(1f))
                if (said.isNotEmpty()) Text(said, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
                HintCard(com.frequencies.core.Hints.SHARE)
                Text("Scan it with Frequencies' Add Frequency on another phone, or open the picture on a computer.", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = {
            Row {
                TextButton(enabled = !linking, onClick = { Sharing.shareCode(context, shown, code) }) { Text("Share the code") }
                TextButton(enabled = !linking, onClick = { Sharing.shareQr(context, shown, bitmap) }) { Text("Share the picture") }
            }
        },
        dismissButton = { TextButton(onClick = onClose) { Text("Close") } },
    )
}
