package com.frequencies.app.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings as AndroidSettings
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Lightbulb
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import com.frequencies.app.ibroadcast
import com.frequencies.app.library
import com.frequencies.app.openInCustomTab
import com.frequencies.app.youtube
import com.frequencies.core.Hints
import com.frequencies.core.IBroadcast
import com.frequencies.core.Twitch
import com.frequencies.core.YouTube
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// -- first-run hints (no Clippy on Android: plain Material tips, shown once) ------

/** A short tip, shown until dismissed (Settings' "Show the tips again" brings it back). */
@Composable
fun HintCard(hint: String, modifier: Modifier = Modifier, action: (@Composable () -> Unit)? = null) {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    if (!Hints.shouldShow(settings, hint)) return
    OutlinedCard(
        modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
        shape = androidx.compose.foundation.shape.RoundedCornerShape(8.dp),
        colors = androidx.compose.material3.CardDefaults.outlinedCardColors(containerColor = MaterialTheme.freq.bg1),
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.freq.line),
    ) {
        Row(Modifier.padding(start = 16.dp, top = 12.dp, end = 8.dp), verticalAlignment = Alignment.Top) {
            Glyph("info", null, tint = MaterialTheme.colorScheme.primary, size = 20.dp, modifier = Modifier.padding(top = 2.dp))
            Spacer(Modifier.width(12.dp))
            Text(Hints.TEXT[hint].orEmpty(), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        }
        Row(Modifier.fillMaxWidth().padding(end = 8.dp, bottom = 4.dp), horizontalArrangement = Arrangement.End) {
            action?.invoke()
            TextButton(onClick = { library.updateSettings { Hints.dismiss(it, hint) } }) { Text("Got it") }
        }
    }
}

/** The notification tip: only while Android 13+ has notifications off for the app, with a way to turn them on. */
@Composable
fun NotificationsHint() {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
    val context = LocalContext.current
    var allowed by remember { mutableStateOf(true) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            allowed = ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        }
    }
    if (allowed) return
    HintCard(Hints.NOTIFICATIONS) {
        TextButton(onClick = {
            context.startActivity(
                Intent(AndroidSettings.ACTION_APP_NOTIFICATION_SETTINGS)
                    .putExtra(AndroidSettings.EXTRA_APP_PACKAGE, context.packageName)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
        }) { Text("Allow") }
    }
}

// -- iBroadcast -----------------------------------------------------------------

/**
 * Settings > iBroadcast: signed in or not, Sign in (a Custom Tab, the
 * phone's own browser: your password goes to iBroadcast, never to this
 * app) and Sign out.
 */
@Composable
fun IBroadcastAccountRow() {
    val context = LocalContext.current
    val keeper = context.ibroadcast
    val say = LocalSay.current
    val scope = rememberCoroutineScope()
    val signedIn by keeper.signedIn.collectAsStateWithLifecycle()
    // The sign-in belongs to the app (IBroadcastKeeper.startSignIn): it goes on while this screen is gone.
    val waiting by keeper.signingIn.collectAsStateWithLifecycle()
    ListItem(
        headlineContent = { Text("iBroadcast") },
        supportingContent = {
            Text(
                when {
                    waiting -> "Finish signing in on the iBroadcast page, then come back here."
                    signedIn -> "Signed in. Add your library or a playlist from Add Frequency."
                    else -> "Play your own music library as a station. You sign in on iBroadcast's page; your password never goes through Frequencies."
                },
            )
        },
        trailingContent = {
            when {
                waiting -> TextButton(onClick = { keeper.cancelSignIn() }) { Text("Cancel") }
                signedIn -> TextButton(onClick = { scope.launch { keeper.signOut(); say("Signed out of iBroadcast.") } }) { Text("Sign out") }
                else -> TextButton(onClick = { keeper.startSignIn(context) }) { Text("Sign in") }
            }
        },
    )
}

/**
 * Add Frequency > iBroadcast (mainwindow._add_ibroadcast): sign in if
 * needed, then a playlist (or Everything), a name, the quality and
 * Shuffle, as on the desktop.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun IBroadcastAddDialog(onClose: () -> Unit) {
    val context = LocalContext.current
    val keeper = context.ibroadcast
    val library = context.library
    val say = LocalSay.current
    val scope = rememberCoroutineScope()
    val signedIn by keeper.signedIn.collectAsStateWithLifecycle()
    val waiting by keeper.signingIn.collectAsStateWithLifecycle()
    val signInProblem by keeper.signInProblem.collectAsStateWithLifecycle()
    var problem by remember { mutableStateOf<String?>(null) }
    var account by remember { mutableStateOf<Pair<IBroadcast.Account, String>?>(null) }
    var loading by remember { mutableStateOf(false) }
    var playlist by remember { mutableStateOf(IBroadcast.LIBRARY_CHOICE) }
    var name by remember { mutableStateOf("iBroadcast") }
    var typed by remember { mutableStateOf(false) }
    var shuffle by remember { mutableStateOf(true) }
    var rate by remember { mutableStateOf(library.settings.value.string("ibroadcast_bitrate", "128").ifEmpty { "128" }) }
    // Only a sign-in started from here says its problem here (an older one's stays in the past).
    var armed by remember { mutableStateOf(signInProblem == null) }
    LaunchedEffect(signInProblem) {
        if (signInProblem == null) armed = true else if (armed) problem = signInProblem
    }

    LaunchedEffect(signedIn) {
        if (!signedIn || account != null) return@LaunchedEffect
        loading = true
        problem = null
        account = withContext(Dispatchers.IO) {
            runCatching { keeper.account.readAccount() }.onFailure { problem = it.message ?: "Couldn't read your library." }.getOrNull()
        }
        loading = false
    }

    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Add from iBroadcast") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                when {
                    !signedIn -> Text(
                        if (waiting) "Finish signing in on the iBroadcast page. Frequencies picks up the sign-in by itself."
                        else "Sign in to iBroadcast to play your library as a station. The sign-in opens in your browser, so your password never goes through Frequencies.",
                    )
                    loading -> Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(24.dp))
                        Spacer(Modifier.width(12.dp))
                        Text("Reading your library…")
                    }
                    account != null -> {
                        val (acct, _) = account!!
                        val choices = listOf(IBroadcast.Playlist(IBroadcast.LIBRARY_CHOICE, "Everything (your whole library)", acct.tracks.keys.toList())) + acct.playlists
                        Text("Playlist", style = MaterialTheme.typography.labelLarge)
                        LazyColumn(Modifier.heightIn(max = 220.dp)) {
                            items(choices, key = { it.id }) { p ->
                                Row(
                                    Modifier.fillMaxWidth().selectable(selected = p.id == playlist, role = Role.RadioButton) {
                                        playlist = p.id
                                        if (!typed) name = if (p.id == IBroadcast.LIBRARY_CHOICE) "iBroadcast" else p.name
                                        if (p.id == IBroadcast.LIBRARY_CHOICE) shuffle = true
                                    }.padding(vertical = 2.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    RadioButton(selected = p.id == playlist, onClick = null)
                                    Spacer(Modifier.width(8.dp))
                                    Text("${p.name} (${p.trackIds.size})", maxLines = 1, overflow = TextOverflow.Ellipsis)
                                }
                            }
                        }
                        OutlinedTextField(
                            value = name,
                            onValueChange = { name = it; typed = true },
                            label = { Text("Name") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        var open by remember { mutableStateOf(false) }
                        ExposedDropdownMenuBox(expanded = open, onExpandedChange = { open = it }) {
                            OutlinedTextField(
                                value = RATES[rate] ?: "$rate kbps",
                                onValueChange = {},
                                readOnly = true,
                                label = { Text("Quality") },
                                supportingText = { Text("Free accounts get 128 kbps whatever is asked for.") },
                                trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(open) },
                                modifier = Modifier.fillMaxWidth().menuAnchor(MenuAnchorType.PrimaryNotEditable),
                            )
                            ExposedDropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                                RATES.forEach { (value, label) ->
                                    DropdownMenuItem(text = { Text(label) }, onClick = { rate = value; open = false })
                                }
                            }
                        }
                        Row(Modifier.fillMaxWidth().clickable { shuffle = !shuffle }, verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("Shuffle it")
                                Text("Tune in partway through a random track and keep picking at random.", style = MaterialTheme.typography.bodySmall)
                            }
                            FreqSwitch(checked = shuffle, onCheckedChange = { shuffle = it })
                        }
                    }
                }
                problem?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            when {
                !signedIn -> TextButton(enabled = !waiting, onClick = {
                    problem = null
                    keeper.startSignIn(context)
                }) { Text(if (waiting) "Waiting…" else "Sign in") }
                else -> TextButton(enabled = account != null, onClick = {
                    val (acct, token) = account ?: return@TextButton
                    scope.launch {
                        library.updateSettings { it["ibroadcast_bitrate"] = kotlinx.serialization.json.JsonPrimitive(rate) }
                        val station = withContext(Dispatchers.Default) {
                            IBroadcast.station(acct, playlist, name, rate, shuffle, token, library.newId, library.now())
                        }
                        if (station == null) { problem = "That playlist has no tracks Frequencies can play."; return@launch }
                        // The account's own preference too: what iBroadcast falls back to (a nicety).
                        launch(Dispatchers.IO) { keeper.account.setBitratePref(token, rate) }
                        val added = library.add(listOf(station))
                        say(if (added.isEmpty()) "That one is already in Saved Frequencies." else "Added ${added[0].displayName}: ${IBroadcast.urls(station).size} tracks.")
                        onClose()
                    }
                }) { Text("Add") }
            }
        },
        dismissButton = { TextButton(onClick = { if (waiting) keeper.cancelSignIn(); onClose() }) { Text("Cancel") } },
    )
}

/** ibroadcast.BITRATE_LABELS, in the desktop's order. */
private val RATES = linkedMapOf(
    "96" to "96 kbps (smallest)",
    "128" to "128 kbps",
    "256" to "256 kbps",
    "320" to "320 kbps",
    "orig" to "Original (as uploaded)",
)

// -- YouTube and Twitch -----------------------------------------------------------

/**
 * Add Frequency > YouTube playlist or Twitch channel: a link (or a
 * channel name) becomes a station the desktop's way. A YouTube playlist
 * is read once to name the station after it.
 */
@Composable
fun ServiceLinkDialog(kind: String, initial: String = "", onClose: () -> Unit) {
    val context = LocalContext.current
    val library = context.library
    val say = LocalSay.current
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf(initial) }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf<String?>(null) }
    val youtube = kind == "youtube"
    val valid = if (youtube) YouTube.looksLikePlaylist(text) else Twitch.channelName(text).isNotEmpty()
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text(if (youtube) "Add a YouTube playlist" else "Add a Twitch channel") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    if (youtube) "Plays like a station: a random video's sound, joined partway through. Paste a playlist or channel link."
                    else "Plays the channel's sound while it's live. Its opening ad is covered with another of your frequencies.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it; problem = null },
                    label = { Text(if (youtube) "Playlist or channel link" else "Channel name or link") },
                    placeholder = { Text(if (youtube) "https://www.youtube.com/playlist?list=…" else "twitch.tv/…") },
                    singleLine = true,
                    isError = text.isNotBlank() && !valid,
                    keyboardOptions = KeyboardOptions(keyboardType = if (youtube) KeyboardType.Uri else KeyboardType.Text, imeAction = ImeAction.Done),
                    modifier = Modifier.fillMaxWidth(),
                )
                if (busy) Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(20.dp))
                    Spacer(Modifier.width(12.dp))
                    Text("Reading the playlist…")
                }
                problem?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            TextButton(enabled = valid && !busy, onClick = {
                if (!youtube) {
                    val added = library.add(listOf(Twitch.stationFor(Twitch.channelName(text), library.newId, library.now())))
                    say(if (added.isEmpty()) "That channel is already in Saved Frequencies." else "Added ${added[0].displayName}.")
                    onClose()
                    return@TextButton
                }
                busy = true
                scope.launch {
                    val link = text.trim()
                    val read = withContext(Dispatchers.IO) { runCatching { context.youtube.entries(link) } }
                    busy = false
                    read.onFailure { problem = it.message ?: "Couldn't read that playlist."; return@launch }
                    val (entries, title) = read.getOrThrow()
                    val added = library.add(listOf(YouTube.stationFor(link, title, library.newId, library.now())))
                    say(if (added.isEmpty()) "That playlist is already in Saved Frequencies." else "Added ${added[0].displayName}: ${entries.size} videos.")
                    onClose()
                }
            }) { Text("Add") }
        },
        dismissButton = { TextButton(onClick = onClose) { Text("Cancel") } },
    )
}

/** A link typed into "Enter a stream address" that is really a Twitch channel or a YouTube playlist. */
fun serviceKindOf(url: String): String? = when {
    YouTube.looksLikePlaylist(url) -> "youtube"
    "twitch.tv/" in url.lowercase() && Twitch.channelName(url).isNotEmpty() -> "twitch"
    else -> null
}
