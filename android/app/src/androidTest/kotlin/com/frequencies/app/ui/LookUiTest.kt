package com.frequencies.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import com.frequencies.core.Settings
import com.frequencies.core.SettingsTabs
import com.frequencies.core.ShellThemes
import com.frequencies.core.Station
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import kotlin.math.abs

/**
 * The overhaul's UI checks (spec section 8): the playing Saved row shows
 * its song in the playing color, and every Settings tab opens as a tab.
 * Run on a phone: see SUBMITTING.md ("UI tests").
 */
class LookUiTest {
    @get:Rule val rule = createComposeRule()

    private fun isTab(label: String) =
        hasText(label) and SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.Tab)

    @Test
    fun everySettingsTabOpens() {
        val theme = ShellThemes.find("outrun")
        rule.setContent { FrequenciesTheme(theme, theme.accent) { SettingsScreen(onRestore = {}) } }
        // Something only that tab shows.
        val shows = mapOf(
            SettingsTabs.APPEARANCE to "Theme",
            SettingsTabs.SOUND to "Static between stations",
            SettingsTabs.FREQUENCIES to "Re-check frequencies that are down",
            SettingsTabs.LYRICS to "Lyrics text size",
            SettingsTabs.ACCOUNTS to "IBROADCAST",
            SettingsTabs.LIBRARY to "Back up everything",
        )
        for (tab in SettingsTabs.ALL) {
            // The tab strip scrolls sideways; the last tabs start off screen.
            rule.onNode(isTab(tab)).performScrollTo().performClick()
            rule.onNode(isTab(tab)).assertIsSelected()
            rule.onNodeWithText(shows.getValue(tab)).assertIsDisplayed()
        }
    }

    @Test
    fun thePlayingRowShowsItsSongInThePlayingColor() {
        val theme = ShellThemes.find("midnight")
        val station = Station(linkedMapOf<String, JsonElement>("id" to JsonPrimitive("t1"), "name" to JsonPrimitive("Test FM"), "url" to JsonPrimitive("https://example.com/stream")))
        var playing = Color.Unspecified
        rule.setContent {
            // Still bars: the moving ones never let Compose go idle.
            FrequenciesTheme(theme, theme.accent) { CompositionLocalProvider(LocalCalm provides true) {
                playing = MaterialTheme.freq.playing
                StationRow(
                    station = station, settings = Settings(linkedMapOf()), tagColors = emptyMap(), index = 0, current = true, playing = true,
                    checked = null, flashed = false, downText = "", liveShow = null,
                    unavailable = null, onClick = {}, onLongClick = {}, handle = null,
                )
            } }
        }
        rule.onNodeWithText("Artist — Title").assertIsDisplayed()
        val pixels = rule.onNodeWithText("Artist — Title").captureToImage().toPixelMap()
        var best = Float.MAX_VALUE
        for (x in 0 until pixels.width) for (y in 0 until pixels.height) {
            val p = pixels[x, y]
            best = minOf(best, abs(p.red - playing.red) + abs(p.green - playing.green) + abs(p.blue - playing.blue))
        }
        assertTrue("no pixel of the song line is the playing color (closest off by $best)", best < 0.12f)
    }
}
