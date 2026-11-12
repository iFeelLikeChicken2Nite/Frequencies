package com.frequencies.app.playback

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.media.browse.MediaBrowser
import android.media.session.MediaController
import android.media.session.PlaybackState
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.legacy.MediaBrowserCompat
import androidx.test.platform.app.InstrumentationRegistry
import com.frequencies.app.TagFilter
import com.frequencies.app.library
import com.frequencies.app.ui.MainActivity
import com.frequencies.core.Browse
import com.frequencies.core.Search
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Build 189: the tag filter as Android Auto reaches it. Auto on a phone is an
 * old-style browser (MediaBrowserCompat: Media3 tells only those browsers
 * a list changed, never a bare platform MediaBrowser), so the car's lists
 * come through MediaBrowserCompat and its buttons through the platform
 * MediaController: the Tags tab follows the phone at once, and a tag tile's
 * tap gets an answer (no "Getting your selection…") while the frequency
 * playing goes on. Run on a phone with the UI tests (README), unlocked.
 * It plays a default frequency for a few seconds.
 */
@OptIn(UnstableApi::class)
class CarTagTest {
    private val instr = InstrumentationRegistry.getInstrumentation()
    private val context = instr.targetContext
    private val thread = HandlerThread("car").apply { start() }
    private val handler = Handler(thread.looper)
    private lateinit var browser: MediaBrowser
    private lateinit var carBrowser: MediaBrowserCompat
    private lateinit var controller: MediaController
    private val states = CopyOnWriteArrayList<Int>()
    private val tagged = mutableListOf<String>()

    @Before
    fun connect() {
        TagFilter.set("")
        // Two default frequencies get a tag each, so the Tags tab has tiles.
        val lib = context.library
        val two = lib.stations.value.filter { it.url.startsWith("http") && "youtube" !in it.url && "twitch" !in it.url }.take(2)
        assertEquals(2, two.size)
        two.forEachIndexed { i, s ->
            lib.update(s.id) { it["tags"] = JsonArray(listOf(JsonPrimitive(if (i == 0) A else B))) }
            tagged += s.id
        }
        val connected = CountDownLatch(1)
        handler.post {
            browser = MediaBrowser(
                context,
                ComponentName(context, PlaybackService::class.java),
                object : MediaBrowser.ConnectionCallback() {
                    override fun onConnected() = connected.countDown()
                },
                null,
            )
            browser.connect()
        }
        assertTrue("the car's browser connects", connected.await(20, TimeUnit.SECONDS))
        val listsConnected = CountDownLatch(1)
        handler.post {
            carBrowser = MediaBrowserCompat(
                context,
                ComponentName(context, PlaybackService::class.java),
                object : MediaBrowserCompat.ConnectionCallback() {
                    override fun onConnected() = listsConnected.countDown()
                },
                null,
            )
            carBrowser.connect()
        }
        assertTrue("the car's list browser connects", listsConnected.await(20, TimeUnit.SECONDS))
        controller = MediaController(context, browser.sessionToken)
        controller.registerCallback(object : MediaController.Callback() {
            override fun onPlaybackStateChanged(state: PlaybackState?) {
                state?.let { states += it.state }
            }
        }, handler)
    }

    @After
    fun disconnect() {
        runCatching { controller.transportControls.stop() }
        handler.post {
            runCatching { carBrowser.disconnect() }
            runCatching { browser.disconnect() }
        }
        TagFilter.set("")
        tagged.forEach { id -> context.library.update(id) { it["tags"] = JsonArray(emptyList()) } }
        SystemClock.sleep(500)
        thread.quitSafely()
    }

    private fun subscribe(parent: String): LinkedBlockingQueue<List<MediaBrowserCompat.MediaItem>> {
        val loads = LinkedBlockingQueue<List<MediaBrowserCompat.MediaItem>>()
        handler.post {
            carBrowser.subscribe(parent, Bundle(), object : MediaBrowserCompat.SubscriptionCallback() {
                override fun onChildrenLoaded(parentId: String?, children: List<MediaBrowserCompat.MediaItem>?) {
                    loads += children.orEmpty().toList()
                }
                override fun onChildrenLoaded(parentId: String?, children: List<MediaBrowserCompat.MediaItem>?, options: Bundle?) {
                    loads += children.orEmpty().toList()
                }
            })
        }
        return loads
    }

    private fun title(items: List<MediaBrowserCompat.MediaItem>, tag: String): String? =
        items.firstOrNull { it.mediaId == Browse.tagFlipId(tag) }?.description?.title?.toString()

    /** Waits up to [ms] for a load where [check] holds (Auto reloads when told; a few loads may come first). */
    private fun awaitLoad(loads: LinkedBlockingQueue<List<MediaBrowserCompat.MediaItem>>, ms: Long, check: (List<MediaBrowserCompat.MediaItem>) -> Boolean): List<MediaBrowserCompat.MediaItem>? {
        val end = SystemClock.elapsedRealtime() + ms
        while (true) {
            val left = end - SystemClock.elapsedRealtime()
            if (left <= 0) return null
            val items = loads.poll(left, TimeUnit.MILLISECONDS) ?: return null
            if (check(items)) return items
        }
    }

    private fun awaitState(ms: Long, check: () -> Boolean): Boolean {
        val end = SystemClock.elapsedRealtime() + ms
        while (SystemClock.elapsedRealtime() < end) {
            if (check()) return true
            SystemClock.sleep(50)
        }
        return check()
    }

    @Test
    fun aPhoneTagShowsInTheCarAtOnce() {
        val tags = subscribe(Browse.TAGS)
        assertNotNull("the Tags tab loads", awaitLoad(tags, 10_000) { title(it, A) == A })
        val saved = subscribe(Browse.SAVED)
        assertNotNull("Saved loads", awaitLoad(saved, 10_000) { it.isNotEmpty() })

        // The phone's chip: only A.
        val t0 = SystemClock.elapsedRealtime()
        instr.runOnMainSync { TagFilter.set(Search.cycleTag("", A)) }
        assertNotNull("the car's tile turns on", awaitLoad(tags, 3_000) { title(it, A) == "✓ $A" })
        val tileMs = SystemClock.elapsedRealtime() - t0
        val only = awaitLoad(saved, 5_000) { it.size == 1 }
        assertNotNull("the car's Saved shows only $A", only)
        val onlyId = only!!.single().mediaId.orEmpty()
        assertEquals(tagged[0], Browse.splitItemId(onlyId)?.second ?: onlyId)
        android.util.Log.i("CarTagTest", "phone -> car: tile in $tileMs ms")
    }

    @Test
    fun aCarTapIsAnsweredAndThePhoneFollows() {
        // Nothing loaded yet: the tap answers with Saved as filtered (only A), paused: not "none", with a title,
        // which is what ends Auto's "Getting your selection…". A tag tap never starts the sound.
        states.clear()
        controller.transportControls.playFromMediaId(Browse.tagFlipId(A), null)
        assertTrue("the car hears back at once", awaitState(3_000) { states.any { it != PlaybackState.STATE_NONE && it != PlaybackState.STATE_ERROR } })
        assertTrue("with a title", awaitState(3_000) { !controller.metadata?.description?.title.isNullOrEmpty() })
        assertEquals(Search.INCLUDED, Search.tagState(TagFilter.query.value, A))
        SystemClock.sleep(1_000)
        assertTrue("no sound started", controller.playbackState?.state != PlaybackState.STATE_PLAYING)
        val playing = controller.metadata?.description?.mediaId
        assertNotNull(playing)
        controller.transportControls.play()
        assertTrue("Play plays it", awaitState(45_000) { controller.playbackState?.state == PlaybackState.STATE_PLAYING })

        // Playing: a tap answers within a second (loading, then playing), and the same frequency goes on.
        states.clear()
        val t0 = SystemClock.elapsedRealtime()
        controller.transportControls.playFromMediaId(Browse.tagFlipId(B), null)
        assertTrue("loading first", awaitState(2_000) { states.contains(PlaybackState.STATE_BUFFERING) })
        assertTrue("then playing again", awaitState(2_000) { states.lastOrNull() == PlaybackState.STATE_PLAYING })
        val answerMs = SystemClock.elapsedRealtime() - t0
        assertTrue("answered in $answerMs ms", answerMs < 1_500)
        assertEquals(playing, controller.metadata?.description?.mediaId)
        assertEquals(Search.INCLUDED, Search.tagState(TagFilter.query.value, B))
        SystemClock.sleep(1_000)
        assertEquals("still playing", PlaybackState.STATE_PLAYING, controller.playbackState?.state)
        android.util.Log.i("CarTagTest", "car tap answered in $answerMs ms, states $states")

        // The phone's Saved shows the car's tags as chosen chips.
        showPhone()
        assertTrue("the phone's $A chip is on (${phone()})", awaitState(15_000) { chipOn(A) == true })
        assertTrue("the phone's $B chip is on (${phone()})", awaitState(3_000) { chipOn(B) == true })
        // A tap in the car while the phone shows Saved: the chip changes at once (B -> hidden).
        val t1 = SystemClock.elapsedRealtime()
        controller.transportControls.playFromMediaId(Browse.tagFlipId(B), null)
        assertTrue("the phone's $B chip turns off its 'only' look (${phone()})", awaitState(3_000) { chipOn(B) == false })
        val chipMs = SystemClock.elapsedRealtime() - t1
        assertEquals(Search.EXCLUDED, Search.tagState(TagFilter.query.value, B))
        android.util.Log.i("CarTagTest", "car -> phone: chip in $chipMs ms")
    }

    /** Opens the app on Saved, screen awake, with no notification question over it (a fresh debug install asks). */
    private fun showPhone() {
        if (Build.VERSION.SDK_INT >= 33) {
            runCatching { instr.uiAutomation.grantRuntimePermission(context.packageName, Manifest.permission.POST_NOTIFICATIONS) }
        }
        runCatching { instr.uiAutomation.executeShellCommand("input keyevent KEYCODE_WAKEUP").close() }
        context.startActivity(
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        )
    }

    private fun walk(node: AccessibilityNodeInfo?, found: (AccessibilityNodeInfo) -> Boolean): AccessibilityNodeInfo? {
        if (node == null) return null
        if (found(node)) return node
        for (i in 0 until node.childCount) walk(node.getChild(i), found)?.let { return it }
        return null
    }

    private fun labels(node: AccessibilityNodeInfo) = node.actionList.mapNotNull { it.label?.toString() }

    /**
     * The tag's chip in Saved's filter row, known by its own actions (long
     * press "Hide <tag>"; a row's pills have none), and whether it is on:
     * its tap then says "Show all". Null while it isn't on screen.
     */
    private fun chipOn(tag: String): Boolean? {
        val chip = walk(instr.uiAutomation.rootInActiveWindow) { "Hide $tag" in labels(it) } ?: return null
        return "Show all" in labels(chip)
    }

    /** What the phone shows, for a failure message. */
    private fun phone(): String {
        val root = instr.uiAutomation.rootInActiveWindow ?: return "no window"
        val chips = mutableListOf<String>()
        walk(root) { n -> labels(n).filter { it.startsWith("Hide ") }.forEach { chips += "${it.removePrefix("Hide ")}=${labels(n)}" }; false }
        return "${root.packageName}: $chips"
    }

    private companion object {
        const val A = "CarTestA"
        const val B = "CarTestB"
    }
}
