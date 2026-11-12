package com.frequencies.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Brush
import androidx.compose.material.icons.outlined.ColorLens
import androidx.compose.material.icons.outlined.OpenInFull
import androidx.compose.material.icons.outlined.SwapHoriz
import androidx.compose.material.icons.outlined.Timer
import androidx.compose.material.icons.outlined.Watch
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.library
import com.frequencies.app.pebble.PebbleLink
import com.frequencies.core.Pebble
import com.frequencies.core.ShellThemes
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonPrimitive

/**
 * Settings › Appearance › Pebble (build 190, spec 7.5; laid out as the
 * user's sketch in build 194): the watchapp's own choices, the same ones as
 * its Settings screen. Each is a row with its icon, its name and what it is
 * set to now; a tap opens the choices. The Pebble app that may connect is on
 * the Connections tab ([PebbleConnection]).
 */
@Composable
fun PebbleSection(header: @Composable (String) -> Unit) {
    val context = LocalContext.current
    val library = context.library
    val settings by library.settings.collectAsStateWithLifecycle()
    var pick by remember { mutableStateOf<String?>(null) }
    fun set(key: String, value: Any) = library.updateSettings {
        it[key] = when (value) { is Boolean -> JsonPrimitive(value); else -> JsonPrimitive(value.toString()) }
    }

    header("Pebble")
    // Watch theme: "Match phone theme" first, then the themes (the old "Same colors as the phone" switch folded in).
    val follow = settings.bool(PebbleLink.FOLLOW, true)
    val theme = settings.string(PebbleLink.THEME, "outrun")
    ChoiceRow(Icons.Outlined.Brush, "Watch theme", if (follow) "Match the phone" else ShellThemes.find(theme).name) { pick = "theme" }
    val ownAccent = settings.bool(PebbleLink.ACCENT_THEME, false)
    ChoiceRow(Icons.Outlined.ColorLens, "Accent color", if (ownAccent) "The theme's own color" else "From the artwork") { pick = "accent" }
    val grow = settings.bool(PebbleLink.GROW, true)
    ChoiceRow(Icons.Outlined.OpenInFull, "Now playing art auto expand", if (grow) "After 5 seconds" else "Never") { pick = "grow" }
    // The grown picture's name line (user, 2026-11-10): artist and station take turns by default, every 7 seconds.
    val names = settings.string(PebbleLink.NAMES, "both")
    ChoiceRow(Icons.Outlined.SwapHoriz, "Expanded view shows", NAMES_LABELS[Pebble.Names.mode(names)]) { pick = "names" }
    if (Pebble.Names.mode(names) == 0) {
        val saved = PebbleLink.namesSeconds(settings)
        var seconds by remember(saved) { mutableIntStateOf(saved) }
        ListItem(
            leadingContent = { Icon(Icons.Outlined.Timer, null) },
            headlineContent = { Text("Swap names every", fontWeight = FontWeight.SemiBold) },
            supportingContent = {
                FreqSlider(
                    value = seconds.toFloat(),
                    onValueChange = { seconds = Pebble.Names.clampSeconds(Math.round(it)) },
                    valueRange = Pebble.Names.SECONDS_MIN.toFloat()..Pebble.Names.SECONDS_MAX.toFloat(),
                    steps = Pebble.Names.SECONDS_MAX - Pebble.Names.SECONDS_MIN - 1,
                    onValueChangeFinished = { library.updateSettings { it[PebbleLink.NAMES_SECONDS] = JsonPrimitive(seconds) } },
                    modifier = Modifier.semantics { stateDescription = "$seconds seconds" },
                )
            },
            trailingContent = { Text("$seconds seconds") },
        )
    }

    when (pick) {
        "theme" -> {
            val choices = listOf(MATCH to "Match phone theme") + ShellThemes.all.map { it.key to it.name }
            Picker("Watch theme", choices, if (follow) MATCH else theme, { pick = null }) { key ->
                if (key == MATCH) set(PebbleLink.FOLLOW, true)
                else library.updateSettings { it[PebbleLink.FOLLOW] = JsonPrimitive(false); it[PebbleLink.THEME] = JsonPrimitive(key) }
                pick = null
            }
        }
        "accent" -> Picker("Accent color", listOf("art" to "From the artwork", "theme" to "The theme's own color"), if (ownAccent) "theme" else "art", { pick = null }) { key ->
            set(PebbleLink.ACCENT_THEME, key == "theme")
            pick = null
        }
        "grow" -> Picker("Now playing art auto expand", listOf("on" to "After 5 seconds", "off" to "Never"), if (grow) "on" else "off", { pick = null }) { key ->
            set(PebbleLink.GROW, key == "on")
            pick = null
        }
        "names" -> Picker("Expanded view shows", Pebble.Names.KEYS.zip(NAMES_LABELS), names, { pick = null }) { key ->
            set(PebbleLink.NAMES, key)
            pick = null
        }
    }
}

/** Settings › Connections › Pebble (moved from Appearance in build 194): the Pebble app that may connect. */
@Composable
fun PebbleConnection(header: @Composable (String) -> Unit) {
    val context = LocalContext.current
    val library = context.library
    val settings by library.settings.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    var pickApp by remember { mutableStateOf(false) }
    header("Pebble")
    val only = settings.string(PebbleLink.ONLY_APP, "")
    ChoiceRow(Icons.Outlined.Watch, "Pebble app that may connect", if (only.isEmpty()) "Any Pebble app" else appLabel(context, only)) { pickApp = true }
    if (pickApp) {
        val apps = remember { PebbleLink.pebbleApps(context) }
        val choices = listOf("" to "Any Pebble app") + apps.map { it to appLabel(context, it) }
        Picker("Pebble app that may connect", choices, only, { pickApp = false }) { pkg ->
            library.updateSettings { it[PebbleLink.ONLY_APP] = JsonPrimitive(pkg) }
            scope.launch { runCatching { PebbleLink.onlyApp(context, pkg.ifEmpty { null }) } }
            pickApp = false
        }
    }
}

/** A row as in the user's sketch: the icon, the name in bold, what it's set to under it; a tap opens the choices. */
@Composable
private fun ChoiceRow(icon: ImageVector, title: String, value: String, onClick: () -> Unit) {
    ListItem(
        modifier = Modifier.clickable(onClickLabel = "Change $title", role = Role.Button, onClick = onClick),
        leadingContent = { Icon(icon, null) },
        headlineContent = { Text(title, fontWeight = FontWeight.SemiBold) },
        supportingContent = { Text(value, color = MaterialTheme.colorScheme.primary) },
    )
}

private const val MATCH = "\u0000match"

private val NAMES_LABELS = listOf("Artist and station, taking turns", "Artist", "Station")

private fun appLabel(context: android.content.Context, pkg: String): String = runCatching {
    val pm = context.packageManager
    pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
}.getOrDefault(pkg)

@Composable
private fun Picker(title: String, choices: List<Pair<String, String>>, chosen: String, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                choices.forEach { (key, label) ->
                    Row(
                        Modifier.fillMaxWidth().clickable { onPick(key) }.padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(selected = key == chosen, onClick = { onPick(key) })
                        Text(label)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onDismiss() }) { Text("Close") } },
    )
}
