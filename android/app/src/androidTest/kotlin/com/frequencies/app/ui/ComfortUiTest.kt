package com.frequencies.app.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.DpRect
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.width
import androidx.compose.ui.unit.height
import com.frequencies.core.Comfort
import com.frequencies.core.ShellThemes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import kotlin.math.abs

/**
 * Comfort Now Playing (comfort spec 10): the state machine on the test's
 * clock, the controls that never move or hide, the five even slots, the
 * tabs right above them. Run on a phone: see SUBMITTING.md ("UI tests").
 */
class ComfortUiTest {
    @get:Rule val rule = createComposeRule()

    private val now = NowPlaying(stationId = "t1", stationName = "Test FM", title = "A Song", artist = "An Artist", playWhenReady = true, isPlaying = true)

    private fun show(width: Int = 360, height: Int = 780) {
        rule.mainClock.autoAdvance = false
        rule.setContent {
            val theme = ShellThemes.find("outrun")
            val context = LocalContext.current
            FrequenciesTheme(theme, theme.accent) {
                // Still glyphs and a jump instead of the 1.5 s move; the idle clock is the test's.
                CompositionLocalProvider(LocalCalm provides true, LocalComfortClock provides { rule.mainClock.currentTime }) {
                    Box(Modifier.requiredSize(width.dp, height.dp)) {
                        NowPlayingScreen(now, PlayerConnection(context), onClose = {})
                    }
                }
            }
        }
        rule.mainClock.advanceTimeBy(100)
    }

    private fun bounds(tag: String): DpRect = rule.onNodeWithTag(tag, useUnmergedTree = true).getUnclippedBoundsInRoot()

    private fun artSide(): Float = bounds("comfort_art").width.value

    @Test
    fun idleGoesToComfortAtSevenSecondsAndATapWorksBothWays() {
        show()
        assertEquals(290f, artSide(), 1f)
        rule.mainClock.advanceTimeBy(Comfort.IDLE_MS - 1_500)
        assertEquals("still Out before 7 s", 290f, artSide(), 1f)
        rule.mainClock.advanceTimeBy(2_500)
        assertEquals("Comfort after 7 s idle", 52f, artSide(), 1f)
        rule.onNodeWithTag("comfort_toggle").performClick()
        rule.mainClock.advanceTimeBy(100)
        assertEquals("tapped back Out", 290f, artSide(), 1f)
        rule.onNodeWithTag("comfort_art").performClick()
        rule.mainClock.advanceTimeBy(100)
        assertEquals("a tap on the artwork goes to Comfort", 52f, artSide(), 1f)
        rule.mainClock.advanceTimeBy(60_000)
        assertEquals("tapped into Comfort: stays", 52f, artSide(), 1f)
    }

    @Test
    fun theControlsNeverMoveAndTheSlotsAreEven() {
        show()
        val root = bounds("comfort_root")
        val controlsOut = bounds("comfort_controls")
        val slots = listOf("slot_eq", "slot_prev", "slot_play", "slot_next", "slot_shuffle").map(::bounds)
        for (s in slots) assertEquals("five equal slots", slots[0].width.value, s.width.value, 1f)
        val play = slots[2]
        assertEquals("Play centered", (root.left + root.right).value / 2, (play.left + play.right).value / 2, 1f)
        assertTrue("controls inside the screen", controlsOut.bottom <= root.bottom && controlsOut.top >= root.top)
        // The tabs sit right above the controls.
        assertEquals(bounds("comfort_tabs").bottom.value, controlsOut.top.value, 1f)
        rule.onNodeWithTag("comfort_toggle").performClick()
        rule.mainClock.advanceTimeBy(Comfort.MOVE_MS + 100L)
        assertEquals("the controls stay put in Comfort", controlsOut, bounds("comfort_controls"))
    }

    @Test
    fun sidewaysRetractsAndKeepsTheControlsInside() {
        show(780, 360)
        assertEquals("sideways cover Out", 284f, artSide(), 1f)
        val row = bounds("comfort_controls")
        assertTrue("five-slot row inside", row.bottom.value <= 360.5f && row.right.value <= 780.5f && row.height.value > 40f)
        assertTrue("the right column holds the row", row.left.value > 284f)
        rule.onNodeWithTag("comfort_root").assertExists()
        rule.onAllNodesWithTag("comfort_toggle").onFirst().performClick()
        rule.mainClock.advanceTimeBy(Comfort.MOVE_MS + 100L)
        assertEquals("sideways cover in Comfort", 118f, artSide(), 1f)
        val tabs = bounds("comfort_tabs")
        assertTrue("tabs under the middle, inside", tabs.bottom.value <= 360.5f && tabs.left.value >= 150f)
    }

    @Test
    fun wideRetractsTheCoverAndKeepsTheBar() {
        show(1024, 660)
        assertEquals("wide cover Out", 340f, artSide(), 1f)
        assertTrue("no tabs on a wide screen", rule.onAllNodesWithTag("comfort_tabs").fetchSemanticsNodes().isEmpty())
        rule.onNodeWithTag("comfort_toggle").performClick()
        rule.mainClock.advanceTimeBy(Comfort.MOVE_MS + 100L)
        assertEquals("wide cover in Comfort", 150f, artSide(), 1f)
        val body = bounds("comfort_body")
        assertTrue("lyrics inside", body.right.value <= 1024.5f && body.bottom.value <= 660.5f)
    }

    @Test
    fun theMovingBackgroundIsThere() {
        show()
        rule.onNodeWithTag("moving_background").assertExists()
    }

    @Test
    fun tabletUprightComposes() {
        composesAt(600, 960)
        assertEquals("a Medium-wide upright window: the cover grows to 420", 420f, artSide(), 1f)
    }

    private fun composesAt(w: Int, h: Int) {
        show(w, h)
        val rect = bounds("comfort_controls")
        assertTrue("controls shown at $w x $h", rect.height.value > 40f && abs(rect.width.value - (w - 40f)) < 2f)
        assertTrue("controls inside at $w x $h", rect.bottom.value <= h + 0.5f)
    }
}
