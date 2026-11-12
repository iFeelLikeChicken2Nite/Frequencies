package com.frequencies.app.ui

import androidx.compose.material.icons.filled.Folder
import com.frequencies.app.localMusic
import com.frequencies.core.LocalStations
import com.frequencies.core.OfflineRules
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.ListItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Switch
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.Importer
import com.frequencies.app.artUpgrader
import com.frequencies.app.library
import com.frequencies.app.playback.SoundPreview
import com.frequencies.core.Equalizer
import com.frequencies.core.Playlists
import com.frequencies.core.Prebuffer
import com.frequencies.core.Twitch
import com.frequencies.core.num
import com.frequencies.core.StationTls
import com.frequencies.core.Station
import com.frequencies.core.TagLook
import com.frequencies.core.Tags
import com.frequencies.core.floatJson
import com.frequencies.core.intJson
import com.frequencies.core.str
import com.frequencies.core.stringArray
import com.frequencies.core.truthy
import kotlin.math.roundToInt
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive

private val ART_CHOICES = listOf(
    "auto" to "Automatic (Shazam - custom - stream)",
    "custom" to "Custom image",
    "shazam" to "Shazam cover art",
    "stream" to "Station logo from the stream",
)
private val READOUT_CHOICES = listOf(
    "auto" to "Automatic (Shazam - custom - stream)",
    "auto_stream" to "Automatic (Shazam - stream - custom)",
    "custom" to "Custom text",
    "shazam" to "Shazam result",
    "stream" to "Whatever the stream reports",
)

/**
 * Station settings, the desktop's tabs: Station (name, address, tags),
 * Sound (level trim and EQ, heard as they are moved), Display (the
 * station's picture since build 198, and where the picture and the song
 * line come from) and Identify (Song ID and lyrics).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StationSettingsDialog(station: Station, initialTab: Int = 0, onClose: () -> Unit) {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableIntStateOf(initialTab) }

    // Station
    var name by remember { mutableStateOf(station.customName.orEmpty()) }
    var url by remember { mutableStateOf(station.url) }
    var insecure by remember { mutableStateOf(station.insecure) }
    // A local files station: its music folder instead of a stream address (task 214).
    val local = LocalStations.isLocal(station)
    var localPath by remember { mutableStateOf(LocalStations.localPath(station)) }
    var localFolder by remember { mutableStateOf(LocalStations.androidFolder(station)) }
    var tags by remember { mutableStateOf(station.tags) }
    var newTag by remember { mutableStateOf("") }
    // Sound
    var gain by remember { mutableDoubleStateOf((station.gainDb * 10).roundToInt() / 10.0) }
    var three by remember { mutableStateOf(Equalizer.threeBand(station).toList()) }
    var ten by remember { mutableStateOf(Equalizer.tenBand(station)) }
    val advanced = settings.bool("eq_advanced")
    // Pre-buffering (desktop "Pre-buffer this station"): 0 s = auto, the measured worst start.
    var bufferOn by remember { mutableStateOf(truthy(station["buffer_enabled"])) }
    var bufferSeconds by remember {
        mutableDoubleStateOf((station["buffer_seconds"].num() ?: 0.0).let { if (it > 0) it else Prebuffer.worst(station) }.coerceIn(0.0, Prebuffer.MAX_S))
    }
    // A station's opening ad (desktop "Cover the opening ad"): every station since build 184, Twitch only before.
    val adOffered = Twitch.adCoverOffered(station)
    var adOn by remember { mutableStateOf(Twitch.adCoverOn(station)) }
    var adSeconds by remember { mutableDoubleStateOf(Twitch.adCoverSeconds(station).takeIf { it > 0 } ?: 15.0) }
    var adKind by remember { mutableStateOf(if ((station["ad_filler_kind"].str() ?: "any") == "any") "any" else "chosen") }
    var adSource by remember { mutableStateOf(station["ad_filler_station_id"].str().orEmpty()) }
    // Display
    var artSource by remember { mutableStateOf(station["art_source"].str() ?: "auto") }
    var artFallback by remember { mutableStateOf(truthy(station["art_fallback"] ?: JsonPrimitive(true))) }
    var readout by remember { mutableStateOf(station["readout_source"].str() ?: "auto") }
    val defaultReadout = station.displayName.trim()
    var customReadout by remember { mutableStateOf(station["custom_readout"].str().orEmpty().ifEmpty { defaultReadout }) }
    // Identify
    var identify by remember { mutableStateOf(truthy(station["shazam_auto"] ?: JsonPrimitive(true))) }
    var lyrics by remember { mutableStateOf(truthy(station["lyrics_enabled"] ?: JsonPrimitive(true))) }
    var talkRadio by remember { mutableStateOf(station.talkRadio) }

    fun sound(): Station = station.copy().also {
        it["gain_db"] = floatJson(gain)
        it["eq_bass"] = intJson(three[0].toLong())
        it["eq_mid"] = intJson(three[1].toLong())
        it["eq_treble"] = intJson(three[2].toLong())
        it["eq_bands"] = JsonArray(ten.map { v -> JsonPrimitive(v) })
    }
    // What the Sound tab says, heard now on this station; back to the saved values on close.
    LaunchedEffect(gain, three, ten, advanced) {
        val s = sound()
        SoundPreview.current.value = SoundPreview.Preview(station.id, Equalizer.bands(s, advanced), Equalizer.gainDb(s))
    }
    DisposableEffect(Unit) { onDispose { SoundPreview.current.value = null } }

    fun addTag() {
        val t = newTag.trim()
        if (t.isNotEmpty() && tags.none { it.equals(t, ignoreCase = true) }) tags = tags + t
        newTag = ""
    }

    fun save() {
        addTag()
        val finalTags = tags
        library.changeStationsAndSettings { list, s ->
            finalTags.forEach { s.tagColor(it, assign = true) }
            list.map { old ->
                if (old.id != station.id) return@map old
                old.copy().also {
                    val shown = name.trim()
                    it["custom_name"] = if (shown.isNotEmpty() && shown != it.name) JsonPrimitive(shown) else JsonNull
                    if (local) {
                        it["local_path"] = JsonPrimitive(localPath)
                        it[LocalStations.ANDROID_FOLDER] = JsonPrimitive(localFolder)
                        it["url"] = JsonPrimitive("local://$localPath")
                    } else {
                        it["url"] = JsonPrimitive(url.trim())
                        it["insecure"] = JsonPrimitive(insecure)
                    }
                    it["tags"] = stringArray(finalTags)
                    it["gain_db"] = floatJson(gain)
                    it["eq_bass"] = intJson(three[0].toLong())
                    it["eq_mid"] = intJson(three[1].toLong())
                    it["eq_treble"] = intJson(three[2].toLong())
                    it["eq_bands"] = JsonArray(ten.map { v -> JsonPrimitive(v) })
                    it["buffer_enabled"] = JsonPrimitive(bufferOn)
                    it["buffer_seconds"] = floatJson(Math.round(bufferSeconds * 10) / 10.0)
                    if (adOffered) {
                        // The desktop's keys and values (dialogs.StationDialog values), so a backup round-trips.
                        val chosen = adKind == "chosen" && adSource.isNotEmpty()
                        it["ad_override"] = JsonPrimitive(adOn)
                        it["ad_seconds"] = floatJson(Math.round(adSeconds).toDouble())
                        it["ad_filler_kind"] = JsonPrimitive(if (chosen) "chosen" else "any")
                        it["ad_filler_station_id"] = JsonPrimitive(if (adKind == "chosen") adSource else "")
                        it["ad_filler_url"] = JsonPrimitive("")
                    }
                    it["art_source"] = JsonPrimitive(artSource)
                    it["art_fallback"] = JsonPrimitive(artFallback)
                    it["readout_source"] = JsonPrimitive(readout)
                    // Empty means the station's name (task 100).
                    it["custom_readout"] = JsonPrimitive(customReadout.trim().takeIf { t -> t != defaultReadout }.orEmpty())
                    it["shazam_auto"] = JsonPrimitive(identify)
                    it["shazam_auto_set"] = JsonPrimitive(true)
                    it["lyrics_enabled"] = JsonPrimitive(lyrics)
                    it["talk_radio"] = JsonPrimitive(talkRadio)
                }
            }
        }
        onClose()
    }

    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Scaffold(
            containerColor = MaterialTheme.freq.bg0,
            topBar = {
                Column(Modifier.background(MaterialTheme.freq.bg0)) {
                    FreqTopBar(
                        "Station settings",
                        navigation = { IconButton(onClick = onClose) { Icon(Icons.Filled.Close, contentDescription = "Cancel") } },
                    ) {
                        TextButton(enabled = local || Playlists.isUrl(url), onClick = ::save) { Text("Save") }
                    }
                    TextTabs(listOf("Station", "Sound", "Display", "Identify"), tab, { tab = it })
                    HorizontalDivider(thickness = 1.dp, color = MaterialTheme.freq.line)
                }
            },
        ) { padding ->
            Column(
                Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                when (tab) {
                    0 -> StationTab(station, name, { name = it }, url, { url = it }, insecure, { insecure = it },
                        localPath, { path, folder -> localPath = path; localFolder = folder }, tags, { tags = it }, newTag, { newTag = it }, ::addTag)
                    1 -> {
                        SoundTab(gain, { gain = it }, three, { three = it }, ten, { ten = it }, advanced)
                        if (adOffered) AdCoverSection(station, adOn, { adOn = it }, adSeconds, { adSeconds = it }, adKind, { adKind = it }, adSource, { adSource = it })
                        if (!local) PrebufferSection(station, bufferOn, { bufferOn = it }, bufferSeconds, { bufferSeconds = it })
                    }
                    2 -> {
                        StationPicture(station)
                        Choice("Artwork", ART_CHOICES, artSource) { artSource = it }
                        SwitchLine("Fall back to the other sources if empty", artFallback) { artFallback = it }
                        Choice("Song text", READOUT_CHOICES, readout) { readout = it }
                        OutlinedTextField(
                            value = customReadout,
                            onValueChange = { customReadout = it },
                            label = { Text("Custom text") },
                            placeholder = { Text("e.g. The Skippy and Dippy Morning Radio Show") },
                            supportingText = { Text("Shown when the song text is the custom text, or nothing better is known.") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    else -> {
                        SwitchLine("Automatically identify the song playing (Shazam)", identify) { identify = it }
                        SwitchLine("Look up lyrics for identified songs", lyrics) { lyrics = it }
                        Text(
                            "Song ID listens to the station as it plays, every 30 seconds, and needs no second connection.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        // Every build (user, build 170), apart from the dev builds' shipped talk list.
                        SwitchLine(
                            "Talk radio",
                            talkRadio,
                            "When your phone speaks (directions, the assistant), this station goes silent and keeps playing, instead of pausing.",
                        ) { talkRadio = it }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StationTab(
    station: Station,
    name: String, setName: (String) -> Unit,
    url: String, setUrl: (String) -> Unit,
    insecure: Boolean, setInsecure: (Boolean) -> Unit,
    localPath: String, setFolder: (path: String, folder: String) -> Unit,
    tags: List<String>, setTags: (List<String>) -> Unit,
    newTag: String, setNewTag: (String) -> Unit,
    addTag: () -> Unit,
) {
    val context = LocalContext.current
    val library = context.library
    val settings by library.settings.collectAsStateWithLifecycle()
    val stations by library.stations.collectAsStateWithLifecycle()
    val live = stations.firstOrNull { it.id == station.id } ?: station

    OutlinedTextField(
        value = name,
        onValueChange = setName,
        label = { Text("Name") },
        placeholder = { Text(station.name.ifEmpty { "Its own name" }) },
        supportingText = { Text("Empty: the station's own name, ${station.name.ifEmpty { "none" }}") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    if (LocalStations.isLocal(station)) {
        // Its music folder; one from the desktop (or lost) can be pointed at a folder on this phone.
        val music = context.localMusic
        val chooser = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { tree ->
            if (tree != null) {
                val (path, _) = music.adopt(tree)
                music.forget(station)
                setFolder(path, tree.toString())
            }
        }
        val here = music.onThisPhone(live)
        ListItem(
            leadingContent = { Icon(Icons.Filled.Folder, contentDescription = null) },
            headlineContent = { Text("Music folder") },
            supportingContent = { Text(localPath.ifEmpty { "None chosen" } + if (!here && localPath == LocalStations.localPath(live)) " (${OfflineRules.NOT_HERE.lowercase()})" else "") },
            trailingContent = { OutlinedButton(onClick = { chooser.launch(null) }) { Text("Choose folder…") } },
        )
    } else {
        OutlinedTextField(
            value = url,
            onValueChange = setUrl,
            label = { Text("Stream address") },
            isError = !Playlists.isUrl(url),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        // No broken-certificate switch since build 170: every station plays without the check (StationTls.EVERY_STATION).
        // The station's own `insecure` value is kept as it was, for the desktop.
    }
    Text("Tags", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        // The desktop's tag pills: tap one to take it off.
        val colors = remember(tags, settings) { TagLook.colors(Tags.library(stations, settings) + tags, settings) }
        tags.forEach { t ->
            TagPill(t, TagLook.colorOf(t, colors, settings), TagLook.PICKED, onClick = { setTags(tags - t) }, onClickLabel = "Remove $t")
        }
    }
    OutlinedTextField(
        value = newTag,
        onValueChange = setNewTag,
        label = { Text("Add a tag") },
        singleLine = true,
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { addTag() }),
        trailingIcon = { IconButton(onClick = addTag) { Icon(Icons.Filled.Add, contentDescription = "Add tag") } },
        modifier = Modifier.fillMaxWidth(),
    )
    val suggestions = Tags.library(stations, settings).filter { s ->
        tags.none { it.equals(s, ignoreCase = true) } && (newTag.isBlank() || s.contains(newTag.trim(), ignoreCase = true))
    }
    if (suggestions.isNotEmpty()) {
        val colors = remember(suggestions, settings) { TagLook.colors(Tags.library(stations, settings) + tags, settings) }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            suggestions.take(24).forEach { s ->
                TagPill(s, TagLook.colorOf(s, colors, settings), TagLook.PICK, onClick = { setTags(tags + s); setNewTag("") }, onClickLabel = "Add $s")
            }
        }
    }
}

/**
 * The station's picture and its three buttons (Choose, Find, Remove), at the top of the
 * Display tab since build 198 (user: moved from the Station tab).
 */
@Composable
private fun StationPicture(station: Station) {
    val context = LocalContext.current
    val library = context.library
    val say = LocalSay.current
    val stations by library.stations.collectAsStateWithLifecycle()
    val live = stations.firstOrNull { it.id == station.id } ?: station
    var finding by remember { mutableStateOf(false) }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) {
            val bytes = runCatching { context.contentResolver.openInputStream(uri)?.use { it.readBytes() } }.getOrNull()
            if (bytes == null) say("That picture couldn't be opened.")
            else library.setArt(station.id, Importer.fileName(context, uri).ifEmpty { "cover.png" }, bytes)
        }
    }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        StationArt(library.artUri(live), live.displayName, Modifier.size(96.dp), corner = 12.dp)
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            OutlinedButton(onClick = {
                pick.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
            }) { Text("Choose picture…") }
            OutlinedButton(onClick = { finding = true }) { Text("Find a picture…") }
            if (live.customArtPath.isNotEmpty()) TextButton(onClick = { library.clearArt(station.id) }) { Text("Remove picture") }
        }
    }
    if (finding) FindPictureDialog(live) { finding = false }
}

/** The desktop's Sound tab: level trim, then bass/mid/treble or the ten bands. */
@Composable
private fun SoundTab(
    gain: Double, setGain: (Double) -> Unit,
    three: List<Int>, setThree: (List<Int>) -> Unit,
    ten: List<Int>, setTen: (List<Int>) -> Unit,
    advanced: Boolean,
) {
    val library = LocalContext.current.library
    Section("Level trim for this station", "Permanently boost a station that's always too quiet, or duck one that's always too loud. Saved with the station and applied every time it plays.")
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("Duck", style = MaterialTheme.typography.labelMedium)
        FreqSlider(
            value = gain.toFloat(),
            onValueChange = { setGain((it * 10).roundToInt() / 10.0) },
            valueRange = -20f..20f,
            modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
        )
        Text("Boost", style = MaterialTheme.typography.labelMedium)
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("%+.1f dB".format(gain), modifier = Modifier.weight(1f))
        TextButton(onClick = { setGain(0.0) }, enabled = gain != 0.0) { Text("Reset") }
    }

    Section("Equalizer for this station", "Saved with the station and applied every time it plays.")
    SwitchLine("Advanced (10 bands)", advanced, "Applies to every station; the values themselves are per-station.") { on ->
        library.updateSettings { it["eq_advanced"] = JsonPrimitive(on) }
    }
    if (advanced) {
        Equalizer.BANDS.indices.forEach { i ->
            BandRow("${Equalizer.BAND_LABELS[i]} Hz".replace("k Hz", " kHz"), ten[i], "%+d") { v -> setTen(ten.toMutableList().also { it[i] = v }) }
        }
    } else {
        listOf("Bass", "Mid", "Treble").forEachIndexed { i, label ->
            BandRow(label, three[i], "%+d dB") { v -> setThree(three.toMutableList().also { it[i] = v }) }
        }
    }
    OutlinedButton(
        onClick = { setThree(listOf(0, 0, 0)); setTen(List(10) { 0 }) },
        enabled = three.any { it != 0 } || ten.any { it != 0 },
    ) { Text("Reset EQ") }
}

/**
 * The desktop's "Cover the opening ad" box, for every station that can have
 * an ad (not music folders, YouTube or iBroadcast): on/off, how
 * long the ad lasts, and which frequency covers it.
 */
@Composable
private fun AdCoverSection(
    station: Station,
    on: Boolean, setOn: (Boolean) -> Unit,
    seconds: Double, setSeconds: (Double) -> Unit,
    kind: String, setKind: (String) -> Unit,
    source: String, setSource: (String) -> Unit,
) {
    val stations by LocalContext.current.library.stations.collectAsStateWithLifecycle()
    // What can cover here: a plain radio stream that starts at once, A to Z ignoring capitals (desktop build 71).
    val covers = remember(stations) {
        stations.filter { it.id != station.id && Twitch.playableCover(it) }.sortedBy { it.displayName.trim().lowercase() }
    }
    Section(
        "Cover the opening ad",
        "Many stations inject an ad the moment you connect. It is part of the stream, so it can't be skipped - but it can be " +
            "covered. Another of your frequencies plays over it while the stream runs on silently underneath, then a burst of static " +
            "hands over to the station, so it sounds like the dial settling. Static fills in until the cover can be heard.\n\n" +
            "Set the length to however long that station's ad usually runs. Too short and you'll catch the tail of it; too long and " +
            "you'll miss the first few seconds of the show.",
    )
    SwitchLine("Cover the ad with another frequency", on, onChange = setOn)
    Text("Ad break lasts ${Math.round(seconds)} seconds", style = MaterialTheme.typography.bodyMedium)
    FreqSlider(
        value = seconds.toFloat(),
        onValueChange = { setSeconds(Math.round(it).toDouble()) },
        valueRange = 1f..120f,
        steps = 118,
        enabled = on,
        modifier = Modifier.fillMaxWidth(),
    )
    Choice("Cover it with", listOf("any" to "Any frequency without ads", "chosen" to "Choose frequency…"), kind, enabled = on, onChange = setKind)
    if (kind == "chosen") {
        val choices = if (covers.isEmpty()) listOf("" to "No saved frequency can cover an ad")
        else covers.map { c -> c.id to c.displayName + if (truthy(c["ad_override"])) "  (has its own ad)" else "" }
        Choice("Frequency", choices, source.takeIf { id -> covers.any { it.id == id } } ?: choices[0].first, enabled = on && covers.isNotEmpty()) { setSource(it) }
        // The list's first entry is what the box shows when nothing was chosen yet: that's the one saved.
        LaunchedEffect(covers, source) { if (covers.isNotEmpty() && covers.none { it.id == source }) setSource(covers[0].id) }
    }
    if (covers.isEmpty()) {
        Text(
            "None of your saved frequencies can cover an ad yet: it takes a normal radio station (Twitch, YouTube, iBroadcast " +
                "and music folders can't start fast enough). Until then the static covers the ad.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
        )
    }
}

/** The desktop's "Pre-buffering" box: read further ahead before the station starts. */
@Composable
private fun PrebufferSection(station: Station, on: Boolean, setOn: (Boolean) -> Unit, seconds: Double, setSeconds: (Double) -> Unit) {
    Section(Prebuffer.TITLE, Prebuffer.BLURB + "\n\n" + Prebuffer.worstLine(station))
    SwitchLine(Prebuffer.SWITCH, on, onChange = setOn)
    Text(
        "Seconds: " + if (seconds <= 0) Prebuffer.AUTO else "%.1f".format(seconds),
        style = MaterialTheme.typography.bodyMedium,
    )
    FreqSlider(
        value = seconds.toFloat(),
        onValueChange = { setSeconds(Math.round(it * 2) / 2.0) },
        valueRange = 0f..Prebuffer.MAX_S.toFloat(),
        steps = 119,
        enabled = on,
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun BandRow(label: String, value: Int, format: String, onChange: (Int) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, modifier = Modifier.width(64.dp), style = MaterialTheme.typography.bodyMedium)
        FreqSlider(
            value = value.toFloat(),
            onValueChange = { onChange(it.roundToInt()) },
            valueRange = -12f..12f,
            steps = 23,
            modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
        )
        Text(format.format(value), modifier = Modifier.width(56.dp), textAlign = TextAlign.End, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun Section(title: String, blurb: String) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
        Text(blurb, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun SwitchLine(title: String, checked: Boolean, summary: String? = null, onChange: (Boolean) -> Unit) {
    ListItem(
        modifier = Modifier.clickable { onChange(!checked) },
        headlineContent = { Text(title) },
        supportingContent = summary?.let { { Text(it) } },
        trailingContent = { FreqSwitch(checked = checked, onCheckedChange = onChange) },
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Choice(label: String, choices: List<Pair<String, String>>, value: String, enabled: Boolean = true, onChange: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(expanded = open && enabled, onExpandedChange = { open = it && enabled }) {
        OutlinedTextField(
            value = choices.firstOrNull { it.first == value }?.second ?: choices[0].second,
            onValueChange = {},
            readOnly = true,
            enabled = enabled,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = open) },
            modifier = Modifier.fillMaxWidth().menuAnchor(MenuAnchorType.PrimaryNotEditable),
        )
        ExposedDropdownMenu(expanded = open && enabled, onDismissRequest = { open = false }) {
            choices.forEach { (key, text) ->
                DropdownMenuItem(text = { Text(text) }, onClick = { onChange(key); open = false })
            }
        }
    }
}

/**
 * Find a picture: the station's directory entries, its website and TuneIn
 * (the places the art upgrade looks), shown to choose from. The one
 * tapped is saved full size, like a picture chosen from the phone.
 */
@Composable
private fun FindPictureDialog(station: Station, onClose: () -> Unit) {
    val context = LocalContext.current
    val say = LocalSay.current
    val scope = rememberCoroutineScope()
    var links by remember { mutableStateOf<List<String>?>(null) }
    var saving by remember { mutableStateOf(false) }
    val failed = remember { mutableStateOf(setOf<String>()) }
    LaunchedEffect(station.id) {
        links = withContext(Dispatchers.IO) { runCatching { context.artUpgrader.finder.pictureLinks(station) }.getOrDefault(emptyList()) }
    }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Find a picture") },
        text = {
            val found = links
            when {
                found == null || saving -> Box(Modifier.fillMaxWidth().heightIn(min = 120.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
                found.isEmpty() -> Text("No pictures of ${station.displayName} turned up in the station directory, on its website or on TuneIn.")
                else -> LazyVerticalGrid(GridCells.Adaptive(96.dp), Modifier.heightIn(max = 420.dp)) {
                    items(found.filter { it !in failed.value }) { link ->
                        coil3.compose.SubcomposeAsyncImage(
                            model = link,
                            contentDescription = null,
                            contentScale = androidx.compose.ui.layout.ContentScale.Fit,
                            modifier = Modifier.padding(4.dp).aspectRatio(1f).clickable {
                                saving = true
                                scope.launch {
                                    val ok = withContext(Dispatchers.IO) { context.artUpgrader.saveChosen(station.id, link) }
                                    saving = false
                                    if (ok) onClose() else say("That picture couldn't be downloaded. Try another one.")
                                }
                            },
                            error = { LaunchedEffect(link) { failed.value = failed.value + link } },
                        )
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text("Cancel") } },
    )
}
