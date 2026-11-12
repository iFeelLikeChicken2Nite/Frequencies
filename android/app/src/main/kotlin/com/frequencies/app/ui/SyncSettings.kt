package com.frequencies.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.RadioButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.sync
import com.frequencies.core.SyncRuns
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Settings > Library and tools > Sync with another device (sync protocol
 * section 9, the desktop's syncpage.py on the phone): this phone's name,
 * the status line and the last run's problem, Sync now (its icon turns
 * while a run goes), Sync automatically, each paired device with "Last
 * synced … ago" and Forget this device, and Pair a device….
 */
@Composable
fun SyncSection() {
    val sync = LocalContext.current.sync
    val ui by sync.ui.collectAsStateWithLifecycle()
    val activity by sync.activity.collectAsStateWithLifecycle()
    var pairing by remember { mutableStateOf(false) }
    var choosing by remember { mutableStateOf(false) }
    // Frequensync (desktop build 169): Sync now asks what to sync first; Choose what to sync… only saves.
    var frequensync by remember { mutableStateOf<Boolean?>(null) }
    // The "… ago" lines get older by themselves.
    LaunchedEffect(Unit) {
        while (true) { sync.tick(); delay(30_000) }
    }
    GroupLabel("Sync with another device")
    ListItem(
        headlineContent = { Text(ui.summary) },
        supportingContent = {
            Column {
                Text("Keep your frequencies (with their pictures and settings), tags, stars, History and look the same on your " +
                    "computer. They sync when Frequencies opens, every so often while both are open on the same network, and when " +
                    "you press Sync now; nothing goes through the internet.")
                // What went wrong in the last run, in plain words (desktop build 167).
                if (activity.problem.isNotEmpty()) Text(
                    activity.problem,
                    color = MaterialTheme.freq.down,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
        },
    )
    ListItem(
        headlineContent = {
            FilledTonalButton(
                onClick = { if (ui.rows.isNotEmpty()) frequensync = true else sync.syncNow() },
                enabled = ui.rows.isNotEmpty() && !activity.busy && !activity.looking,
                modifier = Modifier.semantics { if (activity.busy) stateDescription = activity.text },
            ) {
                SyncGlyph(activity.busy, LocalContentColor.current, 16.dp)
                Spacer(Modifier.width(8.dp))
                // Looking for the devices turns nothing (task 245): the words say it.
                Text(if (activity.busy) "Syncing…" else if (activity.looking) "Looking…" else "Sync now")
            }
        },
        supportingContent = {
            Text("Looks for your paired devices on this network and syncs with them right away.", modifier = Modifier.padding(top = 4.dp))
        },
    )
    ComboRow("Sync automatically", SyncRuns.label(ui.interval), { choosing = true })
    ListItem(
        headlineContent = {
            FilledTonalButton(onClick = { frequensync = false }, enabled = ui.rows.isNotEmpty()) { Text("Choose what to sync…") }
        },
        supportingContent = {
            Text(
                "Pick what this device keeps the same with each paired device: frequencies, pictures, settings, stars and History.",
                modifier = Modifier.padding(top = 4.dp),
            )
        },
    )
    frequensync?.let { andSync -> FrequensyncDialog(onClose = { frequensync = null }, syncAfter = andSync) }
    if (choosing) {
        AlertDialog(
            onDismissRequest = { choosing = false },
            title = { Text("Sync automatically") },
            text = {
                Column {
                    Text(
                        "How often this phone looks for your other devices and syncs with them while Frequencies is open " +
                            "(and when you open it). This setting is this phone's own.",
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                    SyncRuns.INTERVAL_CHOICES.forEach { minutes ->
                        ListItem(
                            modifier = Modifier.clickable { sync.setInterval(minutes); choosing = false },
                            headlineContent = { Text(SyncRuns.label(minutes)) },
                            leadingContent = { RadioButton(selected = minutes == ui.interval, onClick = null) },
                        )
                    }
                }
            },
            confirmButton = { TextButton(onClick = { choosing = false }) { Text("Cancel") } },
        )
    }
    for (row in ui.rows) {
        ListItem(
            headlineContent = { Text(row.name, fontWeight = FontWeight.SemiBold) },
            supportingContent = { Text(row.status) },
            trailingContent = { TextButton(onClick = { sync.forget(row.id) }) { Text("Forget this device") } },
        )
    }
    ListItem(
        headlineContent = { FilledTonalButton(onClick = { pairing = true }) { Text("Pair a device…") } },
        supportingContent = { Text("This device: ${ui.name}", modifier = Modifier.padding(top = 4.dp)) },
    )
    if (pairing) PairDeviceDialog(onClose = { pairing = false })
}

/**
 * Pair a device…: the phone enters the computer's code, by scanning the
 * QR code in the computer's Pair a device… window or typing the code
 * shown beside it. (The phone never shows a code: it doesn't listen.)
 */
@Composable
fun PairDeviceDialog(onClose: () -> Unit, scanned: String? = null) {
    val sync = LocalContext.current.sync
    val scope = rememberCoroutineScope()
    var typed by remember { mutableStateOf("") }
    var note by remember { mutableStateOf(" ") }
    var busy by remember { mutableStateOf(false) }
    var scanning by remember { mutableStateOf(false) }
    var done by remember { mutableStateOf(false) }
    // Paired from here: this phone starts the sync, so What to sync comes first (desktop build 169).
    var choose by remember { mutableStateOf<String?>(null) }

    fun go(qr: String?, code: String?) {
        if (busy) return
        busy = true
        note = "Looking for that device…"
        scope.launch {
            val (ok, text, pid) = sync.pair(qr, code)
            note = text
            busy = false
            if (ok) {
                done = true
                delay(1_500)
                choose = pid
            }
        }
    }
    LaunchedEffect(scanned) { if (scanned != null) go(scanned, null) }

    choose?.let { pid ->
        FrequensyncDialog(onClose = onClose, peers = listOf(pid), syncAfter = true, first = true)
        return
    }

    if (scanning) {
        QrScannerDialog(onCode = { text -> scanning = false; go(text, null) }, onClose = { scanning = false })
        return
    }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Pair a device") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    "On your computer, open Settings > Library and tools > Sync with another device > Pair a device…. " +
                        "Then scan its QR code, or type the code shown beside it. Both need to be on the same Wi-Fi.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                FilledTonalButton(onClick = { scanning = true }, enabled = !busy && !done) { Text("Scan the QR code") }
                Text("Or type a code that another device is showing", style = MaterialTheme.typography.titleSmall)
                OutlinedTextField(
                    value = typed,
                    onValueChange = { if (it.length <= 14) typed = it },
                    placeholder = { Text("ABCDE-12345") },
                    singleLine = true,
                    enabled = !busy && !done,
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters, autoCorrectEnabled = false, imeAction = ImeAction.Go),
                    keyboardActions = KeyboardActions(onGo = { go(null, typed) }),
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(note, style = MaterialTheme.typography.bodyMedium)
            }
        },
        confirmButton = { TextButton(onClick = { go(null, typed) }, enabled = !busy && !done && typed.isNotBlank()) { Text("Pair") } },
        dismissButton = { TextButton(onClick = onClose) { Text("Close") } },
    )
}
