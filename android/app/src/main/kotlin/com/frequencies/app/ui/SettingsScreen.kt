package com.frequencies.app.ui

import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings as AndroidSettings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.selection.toggleable
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.semantics.Role
import com.frequencies.core.SettingsTabs
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Checkbox
import androidx.compose.material3.RadioButton
import androidx.compose.ui.Alignment
import androidx.compose.ui.text.style.TextOverflow
import com.frequencies.app.health
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import com.frequencies.app.BuildConfig
import com.frequencies.app.artists
import com.frequencies.app.library
import com.frequencies.app.playback.SleepTimer
import com.frequencies.core.Imports
import com.frequencies.core.intJson
import java.time.LocalDate
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonPrimitive

/**
 * Settings in the desktop's tabs (spec 5.5): Appearance, Sound,
 * Frequencies, Lyrics and artists, Connections, Library and tools, each its
 * own scrolling page. [initialTab] is for links that open Settings on a
 * given tab (SettingsTabs.indexOf).
 */
@Composable
fun SettingsScreen(onRestore: () -> Unit, initialTab: Int = 0) {
    var tab by rememberSaveable { mutableIntStateOf(initialTab.coerceIn(0, SettingsTabs.ALL.lastIndex)) }
    // A link from elsewhere (the player bar's sync icon): that tab.
    val link by SettingsLink.open.collectAsStateWithLifecycle()
    LaunchedEffect(link) {
        link?.let { tab = SettingsTabs.indexOf(it); SettingsLink.open.value = null }
    }
    Scaffold(
        containerColor = MaterialTheme.freq.bg0,
        contentWindowInsets = WindowInsets(0),
        topBar = {
            Column(Modifier.background(MaterialTheme.freq.bg0)) {
                FreqTopBar("Settings")
                HintCard(com.frequencies.core.Hints.AUTO)
                TextTabs(SettingsTabs.ALL, tab, { tab = it }, edge = MaterialTheme.freq.bg0)
            }
        },
    ) { padding ->
        // Each tab keeps its own scroll position while you switch between them.
        val scrolls = SettingsTabs.ALL.map { rememberScrollState() }
        Column(
            Modifier
                .padding(top = padding.calculateTopPadding())
                .fillMaxSize()
                .verticalScroll(scrolls[tab])
                .padding(bottom = 16.dp),
        ) {
            when (SettingsTabs.ALL[tab]) {
                SettingsTabs.APPEARANCE -> {
                    AppearanceSection { Header(it) }
                    CarOrderSection { Header(it) }
                    PebbleSection { Header(it) }
                }
                SettingsTabs.SOUND -> SoundPage()
                SettingsTabs.FREQUENCIES -> {
                    FrequenciesSection { Header(it) }
                    BatteryRow()
                }
                SettingsTabs.LYRICS -> LyricsPage()
                SettingsTabs.ACCOUNTS -> {
                    Header("iBroadcast")
                    IBroadcastAccountRow()
                    PebbleConnection { Header(it) }
                }
                else -> {
                    LibrarySection(onRestore)
                    SyncSection()
                    AboutSection()
                    if (BuildConfig.DEV) DevSection()
                }
            }
        }
    }
}

/** Sound: static between stations, evening out loudness, the equalizer, and what the sleep timer does when it ends. */
@Composable
private fun SoundPage() {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    Header("Static")
    SwitchRow(
        "Static between stations",
        "Real radio static while a frequency tunes in.",
        settings.staticOn,
    ) { on -> library.updateSettings { it["noise_between_stations"] = JsonPrimitive(on) } }
    if (settings.staticOn) {
        var level by remember(settings.staticVolume) { mutableFloatStateOf(settings.staticVolume.toFloat()) }
        ListItem(
            headlineContent = { Text("Static volume") },
            supportingContent = {
                FreqSlider(
                    value = level,
                    onValueChange = { level = it },
                    valueRange = 0f..100f,
                    onValueChangeFinished = {
                        library.updateSettings { it["noise_volume"] = intJson(level.toLong()) }
                    },
                )
            },
            trailingContent = { Text("${level.toInt()}%") },
        )
    }
    // The desktop's Sound > "Even out loudness between stations" (task 250); synced as `normalize`.
    Header("Loudness")
    SwitchRow(
        "Even out loudness between stations",
        "Quiet stations come up, loud ones come down.",
        settings.bool("normalize", true),
    ) { on -> library.updateSettings { it["normalize"] = JsonPrimitive(on) } }
    Header("Equalizer")
    SwitchRow(
        "Ten-band equalizer",
        "Off gives the three-band bass/mid/treble instead. Applies to every station; the values themselves are per-station.",
        settings.bool("eq_advanced"),
    ) { on -> library.updateSettings { it["eq_advanced"] = JsonPrimitive(on) } }
    Header("Sleep timer")
    SleepSection()
}

/** Lyrics and artists: lyrics, their size, the artist photo pack and band logos. */
@Composable
private fun LyricsPage() {
    val context = LocalContext.current
    val library = context.library
    val settings by library.settings.collectAsStateWithLifecycle()
    Header("Lyrics")
    SwitchRow(
        "Lyrics",
        "Look up lyrics for identified songs. Each station can also turn them off in its settings.",
        settings.bool("lyrics_on", true),
    ) { on -> library.updateSettings { it["lyrics_on"] = JsonPrimitive(on) } }
    run {
        var size by remember(settings) { mutableFloatStateOf(settings.double("lyric_size", 13.0).toFloat().coerceIn(12f, 34f)) }
        ListItem(
            headlineContent = { Text("Lyrics text size") },
            supportingContent = {
                FreqSlider(
                    value = size,
                    onValueChange = { size = it },
                    valueRange = 12f..34f,
                    steps = 21,
                    onValueChangeFinished = { library.updateSettings { it["lyric_size"] = intJson(size.toLong()) } },
                )
            },
            trailingContent = { Text("${size.toInt()} px") },
        )
    }

    Header("Artists")
    PhotoPackSection()
    // Band logos (desktop build 141): the logo in place of the name, from the logo pack.
    SwitchRow(
        "Band logos",
        "Show a band's logo in place of its name on the Artist tab, when the logo pack has one.",
        settings.bool(com.frequencies.core.BandLogos.SETTING, true),
    ) { on ->
        library.updateSettings { it[com.frequencies.core.BandLogos.SETTING] = JsonPrimitive(on) }
        context.artists.checkLogos(delayMs = 0)
        context.artists.logosChanged.value++
    }
}

/** About: the build, the tips again, and the bundled font's license. */
@Composable
private fun AboutSection() {
    val context = LocalContext.current
    val library = context.library
    var license by remember { mutableStateOf<String?>(null) }
    Header("About")
    ListItem(
        headlineContent = { Text(if (BuildConfig.DEV) "Frequencies Dev" else "Frequencies") },
        supportingContent = { Text("Build ${BuildConfig.FREQ_BUILD} (version ${BuildConfig.VERSION_NAME})") },
    )
    ListItem(
        modifier = Modifier.clickable { library.updateSettings { com.frequencies.core.Hints.reset(it) } },
        headlineContent = { Text("Show the tips again") },
        supportingContent = { Text("The short tips on Add Frequency…, Saved Frequencies, sharing and Android Auto come back.") },
    )
    ListItem(
        modifier = Modifier.clickable {
            license = runCatching {
                context.assets.open("licenses/noto-sans-OFL.txt").use { it.readBytes().toString(Charsets.UTF_8) }
            }.getOrDefault("SIL Open Font License, Version 1.1")
        },
        headlineContent = { Text("Font: Noto Sans") },
        supportingContent = { Text("By the Noto Project Authors, under the SIL Open Font License 1.1. Tap to read it.") },
    )
    license?.let { text ->
        AlertDialog(
            onDismissRequest = { license = null },
            title = { Text("Noto Sans license") },
            text = {
                Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                    Text(text, style = MaterialTheme.typography.bodySmall)
                }
            },
            confirmButton = { TextButton(onClick = { license = null }) { Text("Close") } },
        )
    }
}

/** A choice that opens a list, as the desktop's combo box: its name, then the box with the current value. */
@Composable
fun ComboRow(
    title: String,
    value: String,
    onClick: () -> Unit,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
) {
    ListItem(
        headlineContent = { Text(title) },
        supportingContent = {
            ComboBox(value, onClick, modifier = Modifier.padding(top = 6.dp), contentDescription = "Change $title", leading = leading)
        },
        trailingContent = trailing,
    )
}

/**
 * Settings > Lyrics and artists > Artist photo pack (photopack.py): the
 * opt-in, off unless switched on, its status, Check now, and the bio
 * fixes every user gets. Dev builds also see the addresses and can send a
 * test report (the token itself is never shown).
 */
@Composable
private fun PhotoPackSection() {
    val context = LocalContext.current
    val library = context.library
    val kit = context.artists
    val settings by library.settings.collectAsStateWithLifecycle()
    val status by kit.keeper.status.collectAsStateWithLifecycle()
    val bios by kit.keeper.bioStatus.collectAsStateWithLifecycle()
    val busy by kit.keeper.busy.collectAsStateWithLifecycle()
    val on = settings.bool(com.frequencies.core.PhotoPackStore.SETTING)
    ListItem(
        modifier = Modifier.clickable(enabled = kit.keeper.hasAddress) { toggle(library, kit, !on) },
        headlineContent = { Text("Artist photo pack") },
        supportingContent = { Text("Download the artist photo pack and keep it updated (Recommended to turn on)") },
        trailingContent = { FreqSwitch(checked = on, onCheckedChange = { toggle(library, kit, it) }, enabled = kit.keeper.hasAddress) },
    )
    val bioLine: (@Composable () -> Unit)? = if (bios.isNotEmpty()) { { Text(bios) } } else null
    ListItem(
        headlineContent = { Text(status, style = MaterialTheme.typography.bodyMedium) },
        supportingContent = bioLine,
        trailingContent = {
            TextButton(onClick = { kit.keeper.checkNow() }, enabled = kit.keeper.hasAddress && !busy) { Text("Check now") }
        },
    )
    if (BuildConfig.DEV) {
        val scope = rememberCoroutineScope()
        var test by remember { mutableStateOf("") }
        ListItem(
            headlineContent = { Text("Addresses this build ships") },
            supportingContent = {
                Text(
                    "Photo pack: ${kit.addresses.pack.ifEmpty { "(none)" }}\n" +
                        "Reports repository: ${kit.addresses.reportRepo.ifEmpty { "(none)" }}\n" +
                        "Report address: ${kit.reportService.ifEmpty { "(none: reports open in the browser)" }}" +
                        if (test.isNotEmpty()) "\n$test" else "",
                )
            },
            trailingContent = {
                TextButton(onClick = { test = "Sending…"; scope.launch { test = kit.testReport() } }) { Text("Send a test report") }
            },
        )
    }
}

private fun toggle(library: com.frequencies.app.Library, kit: com.frequencies.app.ArtistKit, on: Boolean) {
    library.updateSettings { it[com.frequencies.core.PhotoPackStore.SETTING] = JsonPrimitive(on) }
    kit.keeper.setEnabled(on)
}

/**
 * Backup, restore and export, like the desktop's File menu. A backup is
 * the desktop's own format, so it restores on either.
 */
@Composable
private fun LibrarySection(onRestore: () -> Unit) {
    val context = LocalContext.current
    val library = context.library
    val say = LocalSay.current
    val scope = rememberCoroutineScope()
    var exportFormat by remember { mutableStateOf<String?>(null) }
    var choosing by remember { mutableStateOf(false) }

    val backupTo = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            val ok = withContext(Dispatchers.IO) {
                runCatching { context.contentResolver.openOutputStream(uri, "wt")!!.use { library.backup(it) } }.isSuccess
            }
            say(if (ok) "Backed up everything." else "The backup couldn't be saved there.")
        }
    }
    val exportTo = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("*/*")) { uri ->
        val format = exportFormat
        if (uri == null || format == null) return@rememberLauncherForActivityResult
        scope.launch {
            val stations = library.sorted()
            val ok = withContext(Dispatchers.IO) {
                runCatching {
                    context.contentResolver.openOutputStream(uri, "wt")!!.use {
                        it.write(Imports.write(stations, format).toByteArray(Charsets.UTF_8))
                    }
                }.isSuccess
            }
            say(if (ok) "Exported ${plural(stations.size, "frequency", "frequencies")}." else "The file couldn't be saved there.")
        }
    }

    Header("Your frequencies")
    ListItem(
        modifier = Modifier.clickable { backupTo.launch("frequencies-backup-${LocalDate.now()}.json") },
        headlineContent = { Text("Back up everything") },
        supportingContent = { Text("Frequencies, tags, colors, pictures and settings in one file. The desktop app can restore it too.") },
    )
    ListItem(
        modifier = Modifier.clickable(onClick = onRestore),
        headlineContent = { Text("Restore from a backup") },
        supportingContent = { Text("Replace everything with a backup, or merge in what's new.") },
    )
    ListItem(
        modifier = Modifier.clickable { choosing = true },
        headlineContent = { Text("Export frequencies") },
        supportingContent = { Text("A playlist for other radio apps: M3U, PLS, XSPF, OPML, Goodvibes or RadioDroid.") },
    )
    if (choosing) {
        AlertDialog(
            onDismissRequest = { choosing = false },
            title = { Text("Export as") },
            text = {
                Column {
                    Imports.EXPORT_FORMATS.forEach { format ->
                        ListItem(
                            modifier = Modifier.clickable {
                                choosing = false
                                exportFormat = format
                                exportTo.launch("frequencies${Imports.extension(format)}")
                            },
                            headlineContent = { Text(Imports.FORMAT_NAMES[format] ?: format) },
                        )
                    }
                }
            },
            confirmButton = { TextButton(onClick = { choosing = false }) { Text("Cancel") } },
        )
    }
}

/** What happens when the sleep timer runs out (optionspage._build_sleep). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SleepSection() {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    val stations by library.stations.collectAsStateWithLifecycle()
    val action = settings.string("sleep_action", "fade").takeIf { a -> SleepTimer.ACTIONS.any { it.first == a } } ?: "fade"
    var picking by remember { mutableStateOf<String?>(null) }
    ComboRow("When the sleep timer ends", SleepTimer.ACTIONS.first { it.first == action }.second, { picking = "action" })
    if (action == "switch") {
        val wake = library.station(settings.string("sleep_wake_station_id").ifEmpty { null })
        ComboRow("Wake up to", wake?.displayName ?: "Choose a frequency", { picking = "station" })
    }
    if (action == "tag") {
        ComboRow("Wake up to anything tagged", settings.string("sleep_wake_tag").ifEmpty { "Choose a tag" }, { picking = "tag" })
    }
    val choices: List<Pair<String, String>> = when (picking) {
        "action" -> SleepTimer.ACTIONS
        "station" -> library.sorted().map { it.id to it.displayName }
        "tag" -> com.frequencies.core.Tags.library(stations, settings).map { it to it }
        else -> emptyList()
    }
    picking?.let { what ->
        AlertDialog(
            onDismissRequest = { picking = null },
            title = {
                Text(when (what) { "action" -> "When the sleep timer ends"; "station" -> "Wake up to"; else -> "Wake up to anything tagged" })
            },
            text = {
                androidx.compose.foundation.lazy.LazyColumn(Modifier.heightIn(max = 400.dp)) {
                    items(choices.size) { i ->
                        val (key, label) = choices[i]
                        ListItem(
                            modifier = Modifier.clickable {
                                library.updateSettings {
                                    it[when (what) { "action" -> "sleep_action"; "station" -> "sleep_wake_station_id"; else -> "sleep_wake_tag" }] = JsonPrimitive(key)
                                }
                                picking = null
                            },
                            headlineContent = { Text(label) },
                        )
                    }
                }
            },
            confirmButton = { TextButton(onClick = { picking = null }) { Text("Cancel") } },
        )
    }
}

/**
 * Android Auto: Saved's order in the car, its own and apart from the
 * app's (user, build 197: "most listened" unless chosen).
 */
@Composable
private fun CarOrderSection(header: @Composable (String) -> Unit) {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    val order = com.frequencies.core.Browse.carOrder(settings)
    var picking by remember { mutableStateOf(false) }
    header("Android Auto")
    val label = { key: String -> if (key == "manual") "Manual (as you dragged them)" else com.frequencies.core.SORT_LABELS[key].orEmpty() }
    ComboRow("Saved order in the car", label(order), { picking = true })
    if (picking) {
        AlertDialog(
            onDismissRequest = { picking = false },
            title = { Text("Saved order in the car") },
            text = {
                Column {
                    com.frequencies.core.SORT_LABELS.keys.forEach { key ->
                        ListItem(
                            modifier = Modifier.clickable {
                                library.updateSettings { it["car_sort_mode"] = JsonPrimitive(key) }
                                picking = false
                            },
                            headlineContent = { Text(label(key)) },
                            trailingContent = { if (key == order) Text("✓") },
                        )
                    }
                }
            },
            confirmButton = { TextButton(onClick = { picking = false }) { Text("Cancel") } },
        )
    }
}

/**
 * Some phones stop apps in the background to save battery, which cuts
 * the radio off with the screen off. Shown only while Frequencies is
 * still battery-optimized.
 */
@Composable
private fun BatteryRow() {
    val context = LocalContext.current
    val power = context.getSystemService(PowerManager::class.java)
    var optimized by remember { mutableStateOf(!power.isIgnoringBatteryOptimizations(context.packageName)) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            optimized = !power.isIgnoringBatteryOptimizations(context.packageName)
        }
    }
    if (!optimized) return
    Header("Battery")
    ListItem(
        modifier = Modifier.clickable {
            context.startActivity(
                Intent(AndroidSettings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null)),
            )
        },
        headlineContent = { Text("If the radio stops when the screen is off") },
        supportingContent = {
            Text("Tap here, then App battery usage, and choose Unrestricted.")
        },
    )
}

@Composable
private fun DevSection() {
    val library = LocalContext.current.library
    var confirm by remember { mutableStateOf(false) }
    Header("Developer")
    ListItem(
        headlineContent = { Text("Library files") },
        supportingContent = { Text(library.files.dir.absolutePath) },
    )
    ListItem(
        modifier = Modifier.clickable { confirm = true },
        headlineContent = { Text("Start over with the default frequencies") },
        supportingContent = { Text("Replaces the saved list with the 13 defaults.") },
    )
    if (confirm) {
        AlertDialog(
            onDismissRequest = { confirm = false },
            title = { Text("Start over?") },
            text = { Text("Your saved frequencies are replaced with the 13 defaults.") },
            confirmButton = { TextButton(onClick = { library.resetToDefaults(); confirm = false }) { Text("Start over") } },
            dismissButton = { TextButton(onClick = { confirm = false }) { Text("Cancel") } },
        )
    }
}

/**
 * Settings > Frequencies, the desktop's health and schedule switches:
 * re-checking frequencies marked down (and how often while the app is
 * open; the phone also checks about once a day in the background), moving
 * on when one stalls, show reminders, and the log of every check.
 */
@Composable
private fun FrequenciesSection(header: @Composable (String) -> Unit) {
    val context = LocalContext.current
    val library = context.library
    val settings by library.settings.collectAsStateWithLifecycle()
    var showLog by remember { mutableStateOf(false) }
    var choosing by remember { mutableStateOf(false) }
    header("Saved Frequencies")
    // The desktop's words (optionspage "Frequencies"): Next and Previous scroll Saved to the frequency playing.
    SwitchRow(
        "Automatically scroll list position to currently playing frequency",
        "Next and Previous bring the frequency playing into view in Saved Frequencies. \"Jump to playing\" shows whenever it is out of sight.",
        settings.bool("jump_on_change", true),
    ) { on -> library.updateSettings { it["jump_on_change"] = JsonPrimitive(on) } }
    header("Health checks")
    val recheck = settings.bool("health_recheck", true)
    SwitchRow(
        "Re-check frequencies that are down",
        "A frequency that comes back gets un-flagged on its own. Also checks every saved frequency about once a day in the background.",
        recheck,
    ) { on ->
        library.updateSettings { it["health_recheck"] = JsonPrimitive(on) }
        com.frequencies.app.HealthWorker.schedule(context, on)
    }
    if (recheck) {
        val minutes = settings.double("health_recheck_minutes", 30.0).toInt()
        ComboRow("Check every", RECHECK_CHOICES.firstOrNull { it.first == minutes }?.second ?: "$minutes minutes", { choosing = true })
        if (choosing) {
            AlertDialog(
                onDismissRequest = { choosing = false },
                title = { Text("Check every") },
                text = {
                    Column {
                        RECHECK_CHOICES.forEach { (value, label) ->
                            ListItem(
                                modifier = Modifier.clickable {
                                    library.updateSettings { it["health_recheck_minutes"] = intJson(value.toLong()) }
                                    choosing = false
                                },
                                headlineContent = { Text(label) },
                                leadingContent = { RadioButton(selected = value == minutes, onClick = null) },
                            )
                        }
                    }
                },
                confirmButton = { TextButton(onClick = { choosing = false }) { Text("Cancel") } },
            )
        }
    }
    SwitchRow(
        "Skip to the next frequency when one stalls",
        "A frequency that goes quiet is marked down and the next one plays.",
        settings.bool("auto_skip_broken", true),
    ) { on -> library.updateSettings { it["auto_skip_broken"] = JsonPrimitive(on) } }
    SwitchRow(
        "Tell me when a scheduled show starts",
        "Uses the schedule you have set for each frequency.",
        settings.bool("show_reminders", true),
    ) { on -> library.updateSettings { it["show_reminders"] = JsonPrimitive(on) } }
    ListItem(
        modifier = Modifier.clickable { showLog = true },
        headlineContent = { Text("Station health checks") },
        supportingContent = { Text("Every check, newest first: when each sweep ran, what was checked, and what came back.") },
    )
    if (showLog) HealthLogDialog { showLog = false }
}

/** desktop RECHECK_CHOICES (optionspage.py). */
private val RECHECK_CHOICES = listOf(5 to "5 minutes", 15 to "15 minutes", 30 to "30 minutes", 60 to "an hour", 180 to "3 hours")

/** healthlog.HealthLogWindow: newest first, "Only what failed", Clear. */
@Composable
private fun HealthLogDialog(onClose: () -> Unit) {
    val context = LocalContext.current
    val checks by context.health.log.collectAsStateWithLifecycle()
    val settings by context.library.settings.collectAsStateWithLifecycle()
    var onlyFailed by remember { mutableStateOf(false) }
    // Sweep lines (started, finished, skipped) aren't checks: not counted, and hidden by "Only what failed" (desktop 202).
    val shown = if (onlyFailed) checks.filter { !it.isSweep && !it.ok } else checks
    val stationLines = checks.filter { !it.isSweep }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Station health checks") },
        text = {
            Column {
                Text(
                    if (checks.isEmpty()) "Nothing checked yet." else "${stationLines.size} checked · ${stationLines.count { !it.ok }} with no answer",
                    style = MaterialTheme.typography.bodySmall,
                )
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.clickable { onlyFailed = !onlyFailed }) {
                    Checkbox(checked = onlyFailed, onCheckedChange = { onlyFailed = it })
                    Text("Only what failed")
                }
                LazyColumn(Modifier.heightIn(max = 360.dp)) {
                    items(shown.size) { i ->
                        val c = shown[i]
                        val t = java.time.LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(c.at), java.time.ZoneId.systemDefault())
                        val today = java.time.LocalDate.now()
                        val day = when (t.toLocalDate()) {
                            today -> ""
                            today.minusDays(1) -> "Yesterday "
                            else -> t.toLocalDate().format(java.time.format.DateTimeFormatter.ofPattern("MMM d ", java.util.Locale.US))
                        }
                        val clock = day + com.frequencies.core.Schedule.fmt(t.hour * 60 + t.minute, settings.clock12h)
                        if (c.isSweep) {
                            ListItem(
                                headlineContent = { Text("Health sweep · ${c.sweep}", fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold) },
                                supportingContent = {
                                    Text(
                                        "$clock · ${c.reason}",
                                        color = if (c.ok) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.freq.down,
                                    )
                                },
                            )
                            return@items
                        }
                        ListItem(
                            headlineContent = { Text(c.station, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                            supportingContent = {
                                Text(
                                    "$clock · " + (if (c.ok) "answered" else "no answer") + if (c.reason.isNotEmpty()) " · ${c.reason}" else "",
                                    color = if (c.ok) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.freq.down,
                                )
                            },
                        )
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text("Close") } },
        dismissButton = { TextButton(onClick = { context.health.clearLog() }) { Text("Clear") } },
    )
}

/** A group's label, in the desktop's small caps ("LOOK"). */
@Composable
private fun Header(text: String) = GroupLabel(text)

@Composable
fun SwitchRow(title: String, summary: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    ListItem(
        modifier = Modifier.toggleable(value = checked, role = Role.Switch, onValueChange = onChange),
        headlineContent = { Text(title) },
        supportingContent = { Text(summary) },
        trailingContent = { FreqSwitch(checked = checked, onCheckedChange = null) },
    )
}
