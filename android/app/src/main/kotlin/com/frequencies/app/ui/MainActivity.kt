package com.frequencies.app.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.frequencies.app.BuildConfig
import com.frequencies.app.accent
import com.frequencies.app.health
import com.frequencies.app.library
import com.frequencies.app.sync
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.serialization.json.JsonPrimitive

class MainActivity : ComponentActivity() {
    private lateinit var connection: PlayerConnection

    /** A file, link or text opened with Frequencies or shared to it, until the UI reads it. */
    private val opened = MutableStateFlow<Intent?>(null)

    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        connection = PlayerConnection(this)
        askForNotificationsOnce()
        // Every saved stream is checked a few seconds after launch (desktop LaunchCheck, always on).
        health.launchCheckOnce()
        if (savedInstanceState == null) opened.value = intent.takeIf { it.action != Intent.ACTION_MAIN }
        lookFromAdb(intent)
        pebbleAdd(intent)

        setContent {
            val settings by library.settings.collectAsStateWithLifecycle()
            val systemDark = isSystemInDarkTheme()
            val theme = settings.shownTheme(systemDark)
            val accentColor by accent.color.collectAsStateWithLifecycle()
            LaunchedEffect(systemDark) { accent.refresh() }
            // Light icons on the dark themes' bars, dark ones on Flashbang and Cassette.
            LaunchedEffect(theme.light) {
                val style = if (!theme.light) SystemBarStyle.dark(Color.TRANSPARENT)
                else SystemBarStyle.light(Color.TRANSPARENT, Color.TRANSPARENT)
                enableEdgeToEdge(statusBarStyle = style, navigationBarStyle = style)
            }
            FrequenciesTheme(theme, accentColor) {
                FrequenciesAppUi(connection, opened)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        lookFromAdb(intent)
        pebbleAdd(intent)
        if (intent.action != Intent.ACTION_MAIN) opened.value = intent
    }

    override fun onStart() {
        super.onStart()
        connection.connect()
        // A new wallpaper changes Material You's color.
        accent.refresh()
        // Sync sessions only while the app is on screen (sync protocol section 8).
        sync.foreground(true)
        // Back on screen 30+ minutes after the last sweep, or one owed after an offline start: sweep again (build 203).
        health.foreground()
    }

    override fun onStop() {
        if (!isChangingConfigurations) health.background()
        sync.foreground(false)
        connection.release()
        super.onStop()
    }

    /**
     * Android 13+: notifications need the user's yes. The playing
     * notification shows without it; show reminders need it. Asked once,
     * at the first launch.
     */
    private fun askForNotificationsOnce() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (library.settings.value.bool(ASKED_KEY)) return
        library.updateSettings { it[ASKED_KEY] = JsonPrimitive(true) }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    /**
     * Dev builds: set the look from adb, for screenshots of every theme
     * without tapping through Settings:
     *   adb shell am start -a android.intent.action.MAIN -n com.frequencies.app.dev/com.frequencies.app.ui.MainActivity \
     *     --es freq_theme tron --es freq_accent theme --es freq_mode dark --es freq_color "#3366ff"
     */
    private fun lookFromAdb(intent: Intent?) {
        if (!BuildConfig.DEV || intent == null) return
        val theme = intent.getStringExtra("freq_theme")
        val accent = intent.getStringExtra("freq_accent")
        val mode = intent.getStringExtra("freq_mode")
        val color = intent.getStringExtra("freq_color")
        devTestData(intent)
        if (theme == null && accent == null && mode == null && color == null) return
        library.updateSettings {
            theme?.let { t -> it.shellTheme = t }
            mode?.let { m -> it.androidTheme = m }
            color?.let { c -> it.accentFixed = com.frequencies.core.ShellThemes.parseHex(c) }
            accent?.let { a -> if (a in com.frequencies.core.Settings.ACCENT_MODES) it.accentMode = a }
        }
    }

    /** "Find more on phone" on the Pebble: its notification opens Add Frequency. */
    private fun pebbleAdd(intent: Intent) {
        if (intent.getStringExtra(com.frequencies.app.pebble.PebbleLink.OPEN_ADD) != null) {
            intent.removeExtra(com.frequencies.app.pebble.PebbleLink.OPEN_ADD)
            OpenAdd.open.value = true
        }
    }

    /**
     * Dev builds: test data for the health and schedule checks, from adb.
     *   --es freq_add_dead 1     adds "Dead test (dev)", an address nothing answers (127.0.0.1:9)
     *   --es freq_test_show N    adds "Test show (dev)" to the first saved frequency, starting in N minutes, 30 minutes long, every day
     *   --es freq_test_clear 1   takes both away again
     */
    private fun devTestData(intent: Intent) {
        val lib = library
        // Dev: a shorter gap between "back on screen" sweeps, to test it without waiting 30 minutes (this run only).
        intent.getStringExtra("freq_health_gap")?.toLongOrNull()?.let { minutes -> health.clock.gapMs = minutes.coerceAtLeast(1) * 60_000L }
        if (intent.getStringExtra("freq_add_dead") != null) {
            val dead = com.frequencies.core.Station(LinkedHashMap(com.frequencies.core.Defaults.station))
            dead["name"] = JsonPrimitive(DEAD_NAME)
            dead["url"] = JsonPrimitive("http://127.0.0.1:9/dead")
            lib.add(listOf(dead), markNew = false)
        }
        intent.getStringExtra("freq_test_show")?.toIntOrNull()?.let { inMinutes ->
            val first = com.frequencies.core.Browse.saved(lib.stations.value).firstOrNull() ?: return@let
            val now = java.time.LocalDateTime.now()
            val start = now.hour * 60 + now.minute + inMinutes
            val show = com.frequencies.core.Schedule.showJson(SHOW_NAME, start, start + 30, (0..6).toList(), notify = true)
            lib.update(first.id) { it["shows"] = com.frequencies.core.Schedule.withShow(it, null, show) }
        }
        if (intent.getStringExtra("freq_test_clear") != null) {
            lib.remove(lib.stations.value.filter { it.name == DEAD_NAME }.map { it.id }.toSet())
            lib.changeStations { list ->
                list.map { s ->
                    val shows = com.frequencies.core.Schedule.shows(s)
                    if (shows.none { it["name"].let { n -> n is JsonPrimitive && n.content == SHOW_NAME } }) s
                    else s.copy().also { c -> c["shows"] = kotlinx.serialization.json.JsonArray(shows.filterNot { it["name"].let { n -> n is JsonPrimitive && n.content == SHOW_NAME } }) }
                }
            }
        }
    }

    companion object {
        private const val DEAD_NAME = "Dead test (dev)"
        private const val SHOW_NAME = "Test show (dev)"
        private const val ASKED_KEY = "android_asked_notifications"
    }
}
