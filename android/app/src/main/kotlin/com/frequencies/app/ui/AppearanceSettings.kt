package com.frequencies.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.Accent
import com.frequencies.app.library
import com.frequencies.core.ArtAccent
import com.frequencies.core.Aurora
import com.frequencies.core.Comfort
import kotlinx.serialization.json.JsonPrimitive
import com.frequencies.core.Settings
import com.frequencies.core.ShellTheme
import com.frequencies.core.ShellThemes

private fun c(rgb: Int) = Color(0xFF000000 or (rgb.toLong() and 0xFFFFFF))

/** Settings > Appearance: Light or dark, the dark and the light theme (the desktop's themes), and the accent color. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppearanceSection(header: @Composable (String) -> Unit) {
    val library = LocalContext.current.library
    val settings by library.settings.collectAsStateWithLifecycle()
    var picking by remember { mutableStateOf<String?>(null) }

    header("Look")
    val mode = settings.androidTheme
    ListItem(
        headlineContent = { Text("Light or dark") },
        supportingContent = {
            Column {
                val choices = listOf("system" to "System", "dark" to "Dark", "light" to "Light")
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(top = 8.dp)) {
                    choices.forEachIndexed { i, (key, label) ->
                        SegmentedButton(
                            selected = settings.androidTheme == key,
                            onClick = { library.updateSettings { it.androidTheme = key } },
                            shape = SegmentedButtonDefaults.itemShape(i, choices.size),
                        ) { Text(label) }
                    }
                }
                Text(
                    when (mode) {
                        "dark" -> "Always the dark theme below."
                        "light" -> "Always the light theme below."
                        else -> "Follows your phone: the dark theme when it's in dark mode, the light one otherwise."
                    },
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
        },
    )
    // A dark and a light theme of your choice (user, build 170); System switches between them.
    val dark = ShellThemes.find(settings.darkTheme)
    val light = ShellThemes.find(settings.lightTheme)
    if (mode != "light") ComboRow("Dark theme", dark.name, { picking = "dark" }, trailing = { ThemeSwatch(dark) })
    if (mode != "dark") ComboRow("Light theme", light.name, { picking = "light" }, trailing = { ThemeSwatch(light) })

    val accent = settings.accentMode.let { if (it == Settings.ACCENT_MATERIAL_YOU && !Accent.materialYou) Settings.ACCENT_THEME else it }
    ComboRow(
        "Accent color",
        accentChoices().first { it.first == accent }.second,
        { picking = "accent" },
        leading = { Box(Modifier.size(14.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary)) },
    )

    header("Now Playing")
    ListItem(
        headlineContent = { Text("Moving background") },
        supportingContent = {
            Column {
                val current = Aurora.setting(settings)
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(top = 8.dp)) {
                    Aurora.CHOICES.forEachIndexed { i, (key, label) ->
                        SegmentedButton(
                            selected = current == key,
                            onClick = { library.updateSettings { it[Aurora.SETTING] = JsonPrimitive(key) } },
                            shape = SegmentedButtonDefaults.itemShape(i, Aurora.CHOICES.size),
                        ) { Text(label) }
                    }
                }
                Text(
                    "Soft colors drift slowly behind Now Playing. Auto stops them in Battery Saver and when the battery is low. Off shows a still background.",
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
        },
    )
    val auto = Comfort.auto(settings)
    val savedSeconds = Comfort.retractSeconds(settings)
    // The slider moves freely and saves when let go; the row's text follows it.
    var seconds by remember(savedSeconds) { mutableIntStateOf(savedSeconds) }
    SwitchRow(
        "Fade into Comfort on its own",
        "After ${secondsText(seconds)} without a touch the artwork moves aside and the lyrics fill the screen. A new song brings the artwork back.",
        auto,
    ) { on -> library.updateSettings { it[Comfort.AUTO] = JsonPrimitive(on) } }
    // How long until the artwork moves aside (user, 2026-11-05): 7 s by default, 5 to 20.
    ListItem(
        headlineContent = { Text("Fade into Comfort after") },
        supportingContent = {
            FreqSlider(
                value = seconds.toFloat(),
                onValueChange = { seconds = Comfort.clampRetract(Math.round(it)) },
                valueRange = Comfort.RETRACT_MIN.toFloat()..Comfort.RETRACT_MAX.toFloat(),
                steps = Comfort.RETRACT_MAX - Comfort.RETRACT_MIN - 1,
                enabled = auto,
                onValueChangeFinished = {
                    library.updateSettings { it[Comfort.RETRACT_SECONDS] = JsonPrimitive(seconds) }
                },
                modifier = Modifier.semantics { stateDescription = secondsText(seconds) },
            )
        },
        trailingContent = { Text(secondsText(seconds)) },
    )
    SwitchRow(
        "Keep the screen on in Comfort",
        "While the lyrics fill the screen and something is playing.",
        Comfort.keepAwake(settings),
    ) { on -> library.updateSettings { it[Comfort.KEEP_AWAKE] = JsonPrimitive(on) } }

    when (picking) {
        "dark" -> ThemePicker("Dark theme", dark.key, light = false, onDismiss = { picking = null }) { key ->
            library.updateSettings { it.darkTheme = key }
            picking = null
        }
        "light" -> ThemePicker("Light theme", light.key, light = true, onDismiss = { picking = null }) { key ->
            library.updateSettings { it.lightTheme = key }
            picking = null
        }
        "accent" -> AccentPicker(accent, onDismiss = { picking = null }) { choice ->
            if (choice == Settings.ACCENT_FIXED) {
                picking = "custom"
            } else {
                library.updateSettings { it.accentMode = choice }
                picking = null
            }
        }
        "custom" -> CustomColorDialog(settings.accentFixed ?: MaterialTheme.colorScheme.primary.toRgb(), onDismiss = { picking = null }) { color ->
            library.updateSettings {
                it.accentFixed = color
                it.accentMode = Settings.ACCENT_FIXED
            }
            picking = null
        }
    }
}

private fun secondsText(seconds: Int): String = "$seconds seconds"

private fun Color.toRgb(): Int =
    ((red * 255 + 0.5f).toInt() shl 16) or ((green * 255 + 0.5f).toInt() shl 8) or (blue * 255 + 0.5f).toInt()

private fun accentChoices(): List<Triple<String, String, String>> = buildList {
    add(Triple(Settings.ACCENT_ARTWORK, "Follow the artwork", "Changes with each song's cover or the station's picture, like on the desktop."))
    if (Accent.materialYou) add(Triple(Settings.ACCENT_MATERIAL_YOU, "Material You", "Your wallpaper's color."))
    add(Triple(Settings.ACCENT_THEME, "Match the theme", "The theme's own accent."))
    add(Triple(Settings.ACCENT_FIXED, "Custom", "A color you choose."))
}

/** A tiny picture of a theme: its canvas, a raised row, text lines and its accent. */
@Composable
private fun ThemeSwatch(theme: ShellTheme) {
    Box(
        Modifier.size(width = 56.dp, height = 40.dp).clip(RoundedCornerShape(8.dp))
            .background(c(theme.token("bg0")))
            .border(1.dp, c(theme.token("line")), RoundedCornerShape(8.dp)),
    ) {
        Column(Modifier.padding(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(width = 24.dp, height = 4.dp).clip(RoundedCornerShape(2.dp)).background(c(theme.token("text"))))
                Spacer(Modifier.width(4.dp))
                Box(Modifier.size(8.dp).clip(CircleShape).background(c(theme.accent)))
            }
            Box(Modifier.size(width = 18.dp, height = 3.dp).clip(RoundedCornerShape(2.dp)).background(c(theme.token("text2"))))
            Box(Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(2.dp)).background(c(theme.token("bar_bg"))))
        }
    }
}

@Composable
private fun ThemePicker(title: String, current: String, light: Boolean, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    val all = ShellThemes.all.filter { it.light == light }
    val state = rememberLazyListState(initialFirstVisibleItemIndex = all.indexOfFirst { it.key == current }.coerceAtLeast(0))
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            LazyColumn(Modifier.heightIn(max = 460.dp), state = state) {
                items(all, key = { it.key }) { theme ->
                    ListItem(
                        modifier = Modifier.clickable(role = Role.RadioButton) { onPick(theme.key) },
                        leadingContent = { RadioButton(selected = theme.key == current, onClick = null) },
                        headlineContent = { Text(theme.name) },
                        supportingContent = { Text(theme.note, maxLines = 2, overflow = TextOverflow.Ellipsis) },
                        trailingContent = { ThemeSwatch(theme) },
                    )
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun AccentPicker(current: String, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Accent color") },
        text = {
            Column {
                accentChoices().forEach { (key, label, note) ->
                    ListItem(
                        modifier = Modifier.clickable(role = Role.RadioButton) { onPick(key) },
                        leadingContent = { RadioButton(selected = key == current, onClick = null) },
                        headlineContent = { Text(if (key == Settings.ACCENT_FIXED && current == key) "Custom (change color…)" else label) },
                        supportingContent = { Text(note) },
                    )
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/** Hue, saturation and lightness sliders with hex entry, like the desktop's color picker. */
@Composable
private fun CustomColorDialog(start: Int, onDismiss: () -> Unit, onPick: (Int) -> Unit) {
    val first = ArtAccent.hsl(start)
    var h by remember { mutableIntStateOf(first.h.coerceAtLeast(0)) }
    var s by remember { mutableIntStateOf(first.s) }
    var l by remember { mutableIntStateOf(first.l) }
    var hex by remember { mutableStateOf("#%06x".format(start)) }
    val color = ArtAccent.fromHsl(h, s, l)
    val shown = ArtAccent.readable(color)
    fun fromHex(text: String) {
        hex = text
        val clean = text.trim().removePrefix("#")
        if (Regex("[0-9a-fA-F]{6}").matches(clean)) {
            val v = ArtAccent.hsl(clean.toInt(16))
            h = v.h.coerceAtLeast(0); s = v.s; l = v.l
        }
    }
    fun moved() { hex = "#%06x".format(ArtAccent.fromHsl(h, s, l)) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Custom accent color") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(48.dp).clip(RoundedCornerShape(12.dp)).background(c(color)).border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(12.dp)))
                    Spacer(Modifier.width(12.dp))
                    OutlinedTextField(
                        value = hex,
                        onValueChange = ::fromHex,
                        label = { Text("Hex color") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.None, imeAction = ImeAction.Done),
                    )
                }
                Text("Hue $h°")
                FreqSlider(value = h.toFloat(), onValueChange = { h = it.toInt(); moved() }, valueRange = 0f..359f)
                Text("Saturation ${s * 100 / 255}%")
                FreqSlider(value = s.toFloat(), onValueChange = { s = it.toInt(); moved() }, valueRange = 0f..255f)
                Text("Lightness ${l * 100 / 255}%")
                FreqSlider(value = l.toFloat(), onValueChange = { l = it.toInt(); moved() }, valueRange = 0f..255f)
                if (ArtAccent.distance(shown, color) >= ArtAccent.MIN_SHIFT) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(20.dp).clip(CircleShape).background(c(shown)))
                        Spacer(Modifier.width(8.dp))
                        Text(
                            "Shown as this shade, so it stays readable (the desktop does the same).",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onPick(color) }) { Text("Use this color") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
