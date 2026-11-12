package com.frequencies.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.frequencies.app.Incoming
import com.frequencies.app.library
import com.frequencies.core.Imports
import com.frequencies.core.Playlists

/** Shows a short message in the app's snackbar. */
val LocalSay = compositionLocalOf<(String) -> Unit> { {} }

/**
 * What to do with something that arrived: pick stations to add, restore
 * a backup, or say why it couldn't be read.
 */
@Composable
fun IncomingDialog(incoming: Incoming, onAddress: (String) -> Unit, onClose: () -> Unit) {
    val library = LocalContext.current.library
    val say = LocalSay.current
    when (incoming) {
        is Incoming.Failed -> AlertDialog(
            onDismissRequest = onClose,
            title = { Text("Couldn't import that") },
            text = { Text(incoming.message) },
            confirmButton = { TextButton(onClick = onClose) { Text("OK") } },
        )

        is Incoming.Address -> LaunchedEffect(incoming) {
            onAddress(incoming.url)
            onClose()
        }

        is Incoming.BackupFile -> AlertDialog(
            onDismissRequest = onClose,
            title = { Text("Restore this backup?") },
            text = {
                Text(
                    "It has ${plural(incoming.contents.stations.size, "frequency", "frequencies")}.\n\n" +
                        "Replace: this phone's frequencies, tags, colors and settings become the backup's. " +
                        "Your theme stays.\n\nMerge: only the frequencies you don't have yet are added.",
                )
            },
            confirmButton = {
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(onClick = onClose) { Text("Cancel") }
                    TextButton(onClick = {
                        val n = library.restore(incoming.contents, replace = false)
                        say(if (n == 0) "Nothing new in that backup." else "Merged in ${plural(n, "new frequency", "new frequencies")}.")
                        onClose()
                    }) { Text("Merge") }
                    TextButton(onClick = {
                        val n = library.restore(incoming.contents, replace = true)
                        say("Restored ${plural(n, "frequency", "frequencies")}.")
                        onClose()
                    }) { Text("Replace") }
                }
            },
        )

        is Incoming.Stations -> {
            val rows = incoming.stations
            val saved = remember(rows) { rows.map { library.isSaved(it.url) } }
            var picked by remember(rows) { mutableStateOf(rows.indices.filter { !saved[it] }.toSet()) }
            AlertDialog(
                onDismissRequest = onClose,
                title = { Text(if (rows.size == 1) "Add this frequency?" else "Add frequencies") },
                text = {
                    Column {
                        Text("From ${incoming.from}:", style = MaterialTheme.typography.bodyMedium)
                        LazyColumn(Modifier.heightIn(max = 360.dp).padding(top = 8.dp)) {
                            items(rows.indices.toList()) { i ->
                                val s = rows[i]
                                ListItem(
                                    modifier = Modifier.clickable(enabled = !saved[i]) {
                                        picked = if (i in picked) picked - i else picked + i
                                    },
                                    leadingContent = {
                                        Checkbox(
                                            checked = i in picked,
                                            enabled = !saved[i],
                                            onCheckedChange = { on -> picked = if (on) picked + i else picked - i },
                                        )
                                    },
                                    headlineContent = { Text(s.displayName, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                                    supportingContent = {
                                        Text(
                                            if (saved[i]) "Already in your frequencies" else s.tags.joinToString(", ").ifEmpty { s.url },
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis,
                                        )
                                    },
                                )
                            }
                        }
                    }
                },
                confirmButton = {
                    TextButton(enabled = picked.isNotEmpty(), onClick = {
                        val added = library.add(picked.sorted().map { rows[it] })
                        say(
                            when {
                                added.isEmpty() -> "Those are already in your frequencies."
                                added.size == 1 -> "Added ${added[0].displayName}."
                                else -> "Added ${added.size} frequencies."
                            },
                        )
                        onClose()
                    }) { Text(if (picked.size <= 1) "Add" else "Add ${picked.size}") }
                },
                dismissButton = { TextButton(onClick = onClose) { Text("Cancel") } },
            )
        }
    }
}

/** Add Frequency by its stream address. */
@Composable
fun AddByAddressDialog(initialUrl: String, onClose: () -> Unit) {
    val library = LocalContext.current.library
    val say = LocalSay.current
    var name by remember { mutableStateOf("") }
    var url by remember { mutableStateOf(initialUrl) }
    // A Twitch channel or a YouTube playlist typed in here becomes that kind of station (mainwindow._add_from_source).
    var service by remember { mutableStateOf<String?>(null) }
    service?.let { kind -> ServiceLinkDialog(kind, url.trim(), onClose); return }
    val valid = Playlists.isUrl(url)
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Add by address") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = url,
                    onValueChange = { url = it },
                    label = { Text("Stream address") },
                    placeholder = { Text("https://…") },
                    singleLine = true,
                    isError = url.isNotBlank() && !valid,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Name") },
                    placeholder = { Text("Optional") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(enabled = valid, onClick = {
                serviceKindOf(url.trim())?.let { service = it; return@TextButton }
                val station = Imports.toStation(Imports.Entry(name.trim().ifEmpty { url.trim() }, url.trim()), library.newId, library.now())
                val added = library.add(listOf(station))
                say(if (added.isEmpty()) "That address is already in your frequencies." else "Added ${added[0].displayName}.")
                onClose()
            }) { Text("Add") }
        },
        dismissButton = { TextButton(onClick = onClose) { Text("Cancel") } },
    )
}

fun plural(n: Int, one: String, many: String) = if (n == 1) "1 $one" else "$n $many"
