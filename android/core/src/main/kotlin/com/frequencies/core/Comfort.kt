package com.frequencies.core

import kotlinx.serialization.json.JsonPrimitive

/**
 * Comfort Now Playing (comfort spec 5b): the one state the Now Playing
 * screen is in, Out (the big artwork) or Comfort (the artwork in a small
 * header, the lyrics or biography filling the screen), and the rules that
 * move it. The screen keeps the state and the clock; these decide.
 */
object Comfort {
    /**
     * A long song title fits before the band logo keeps its size (user,
     * batch 24: "if you have to sacrifice logo size to make a title fit,
     * do it"). Each step is tried in turn while the title is still cut
     * off: two lines; three lines with the logo at 60%; then the title a
     * size smaller (90%, then 80%) with the logo at 50%.
     */
    data class TitleFit(val lines: Int, val textScale: Float, val logoScale: Float)

    val TITLE_FITS = listOf(
        TitleFit(2, 1f, 1f),
        TitleFit(3, 1f, 0.6f),
        TitleFit(3, 0.9f, 0.5f),
        TitleFit(3, 0.8f, 0.5f),
    )

    /** The next step when the title is still cut off at [step], else [step]. */
    fun nextTitleFit(step: Int, cutOff: Boolean): Int = if (cutOff && step < TITLE_FITS.lastIndex) step + 1 else step

    /** Out goes to Comfort after this long without a touch, unless the setting below says otherwise. */
    const val IDLE_MS = 7_000L

    /**
     * "Fade into Comfort after" (user, 2026-11-05): whole seconds, 7 by
     * default, any of 5 to 20. Android only (the desktop has no retract):
     * not in sync's SHARED_SETTINGS, so never sent or overwritten by sync.
     */
    const val RETRACT_SECONDS = "android_comfort_retract_seconds"
    const val RETRACT_DEFAULT = 7
    const val RETRACT_MIN = 5
    const val RETRACT_MAX = 20

    /** The setting's seconds, rounded and kept to 5-20 (a bad or missing value gives 7). */
    fun retractSeconds(settings: Settings?): Int {
        val v = settings?.double(RETRACT_SECONDS, RETRACT_DEFAULT.toDouble()) ?: return RETRACT_DEFAULT
        if (v.isNaN()) return RETRACT_DEFAULT
        return clampRetract(Math.round(v.coerceIn(-1e6, 1e6)).toInt())
    }

    fun clampRetract(seconds: Int): Int = seconds.coerceIn(RETRACT_MIN, RETRACT_MAX)

    /** The idle time before the retract, in ms. */
    fun idleMs(settings: Settings?): Long = retractSeconds(settings) * 1000L
    /** The retract and expand: position, size and text size together, fast-out-slow-in. */
    const val MOVE_MS = 1500

    /** "Fade into Comfort on its own" (on): the idle retract and the new-song expand. */
    const val AUTO = "android_comfort_auto"
    /** "Keep the screen on in Comfort" (off): only while in Comfort and playing. */
    const val KEEP_AWAKE = "android_comfort_keep_awake"

    fun auto(settings: Settings?): Boolean = settings?.bool(AUTO, true) ?: true
    fun keepAwake(settings: Settings?): Boolean = settings?.bool(KEEP_AWAKE, false) ?: false

    enum class Event { IDLE, TAP, NEW_SONG }

    /**
     * Where [comfort] goes on [event]. A tap always works, either way.
     * The idle retract and the new-song expand happen only with [auto] on
     * and no screen reader ([screenReader]: layout changes under one are
     * disorienting).
     */
    fun next(comfort: Boolean, event: Event, auto: Boolean, screenReader: Boolean): Boolean = when (event) {
        Event.TAP -> !comfort
        Event.IDLE -> if (auto && !screenReader) true else comfort
        Event.NEW_SONG -> if (auto && !screenReader) false else comfort
    }

    /** Song ID for this station: its "Automatically identify the song playing" switch (on unless turned off). */
    fun songIdOn(station: Station?): Boolean = station == null || truthy(station["shazam_auto"] ?: JsonPrimitive(true))

    /** What Comfort comes in for on its own: nothing, the lyrics, or the station's own bio on the Artist page. */
    enum class AutoPage { NONE, LYRICS, ARTIST }

    /**
     * Android batch 22 (user, 2026-11-05): with Song ID on, Comfort comes in
     * on its own as before (the lyrics). With Song ID off there's no song to
     * show, so it doesn't, except on a talk radio station the dev marked
     * (the shipped talk list or its talk radio switch) that has its own
     * typed-in bio [talkBio]: then Comfort comes in on the Artist page.
     */
    fun autoPage(songId: Boolean, talkBio: Boolean): AutoPage = when {
        songId -> AutoPage.LYRICS
        talkBio -> AutoPage.ARTIST
        else -> AutoPage.NONE
    }

    /**
     * Build 198 (user, 2026-11-11): the artwork retracts only once what the page being
     * viewed shows has loaded: the lyrics on Lyrics, the biography on Artist. Until then
     * (and for good, if it never loads) it stays out, so it never retracts into an empty
     * space. Once it loads, the retract delay counts from then.
     */
    fun viewedReady(page: String, lyricsFound: Boolean, bioShown: Boolean): Boolean =
        if (page == "artist") bioShown else lyricsFound

    /**
     * Task 243 (user, build 198): a biography fills the screen when the Artist page
     * ([pageDp] tall, as laid out) takes at least [BIO_FILLS] of the room it gets in
     * Comfort (the area under the small header, [heroDp] being the whole area between
     * the top bar and the tabs). A shorter one keeps the artist's photo big.
     */
    fun bioFills(pageDp: Float, heroDp: Float): Boolean {
        val room = heroDp - Upright.BODY_TOP_COMFORT
        return room > 0 && pageDp >= room * BIO_FILLS
    }

    /**
     * Task 248 (user, build 206: "don't automatically retract the artist bio photo if there's a lot of
     * blank space"): the page has to reach the fade at the bottom of the body, so a retract never leaves
     * empty space under the biography. Build 198's 0.6 let a page 40% empty retract the photo.
     */
    const val BIO_FILLS = 1f - Upright.FADE

    /**
     * Build 198 (user): a talk station opens on the Artist page (the stream's bio); any
     * other station on the page the user last chose on a station that isn't talk radio.
     */
    fun pageFor(talk: Boolean, lastNonTalk: String): String = if (talk) "artist" else lastNonTalk.takeIf { it == "artist" } ?: "lyrics"

    /** Whether the idle clock runs: Out, automatic on, no screen reader, the screen in front. */
    fun timing(comfort: Boolean, auto: Boolean, screenReader: Boolean, inFront: Boolean): Boolean =
        !comfort && auto && !screenReader && inFront

    /** How long until the idle retract, [lastTouchMs] being the last touch anywhere on the screen. */
    fun idleLeft(nowMs: Long, lastTouchMs: Long, idleMs: Long = IDLE_MS): Long = (lastTouchMs + idleMs - nowMs).coerceAtLeast(0)

    /** Whether the screen stays awake: the switch on, in Comfort and playing. */
    fun keepScreenOn(keepAwake: Boolean, comfort: Boolean, playing: Boolean): Boolean = keepAwake && comfort && playing

    /** The toggle's TalkBack label: what a tap does. */
    fun toggleLabel(comfort: Boolean): String = if (comfort) "Show artwork" else "Show lyrics fullscreen"

    /** Which layout Now Playing takes (spec 2), by the window's size, not by "phone" or "tablet". */
    enum class Shape { UPRIGHT, SIDEWAYS, WIDE }

    /** Window size class breakpoints (dp): width Compact < 600 <= Medium < 840; height Compact < 480. */
    const val MEDIUM_WIDTH = 600f
    const val EXPANDED_WIDTH = 840f
    const val MEDIUM_HEIGHT = 480f

    /**
     * A phone upright (Compact width): Upright. A phone on its side
     * (Compact height, wider than tall): Sideways. Tablets and unfolded
     * foldables (Medium or Expanded both ways): Wide, except a Medium-wide
     * window standing upright, which keeps Upright with a bigger cover.
     */
    fun shape(widthDp: Float, heightDp: Float): Shape = when {
        heightDp < MEDIUM_HEIGHT && widthDp > heightDp -> Shape.SIDEWAYS
        widthDp < MEDIUM_WIDTH -> Shape.UPRIGHT
        widthDp < EXPANDED_WIDTH && heightDp > widthDp -> Shape.UPRIGHT
        heightDp < MEDIUM_HEIGHT -> Shape.UPRIGHT
        else -> Shape.WIDE
    }

    /** The upright cover's largest side: 290 on a phone, 420 on a Medium-wide window (spec 2). */
    fun uprightCap(widthDp: Float): Float = if (widthDp >= MEDIUM_WIDTH) Upright.ART_CAP_WIDE else Upright.ART_OUT

    /** The sideways phone (spec 4), dp. */
    object Sideways {
        const val ART_OUT = 284f
        /** Out, the cover takes at most this share of the width (the right column needs the rest). */
        const val ART_SHARE = 0.42f
        const val COLUMN = 150f
        const val ART_COMFORT = 118f
        const val PLAY = 62f
        const val GAP = 26f
        /** Kept clear of the side with the camera cutout. */
        const val CUTOUT = 40f
        const val TRANSPORT = 80f

        /** The cover's side Out on a [w] x [h] screen (after insets). */
        fun artOut(w: Float, h: Float): Float = minOf(ART_OUT, h - 32f, w * ART_SHARE).coerceAtLeast(ART_COMFORT)

        /** The cover's side in Comfort: 118, smaller only when the column's buttons need the room. */
        fun artComfort(h: Float): Float = minOf(ART_COMFORT, h - 48f - 96f - 64f).coerceAtLeast(52f)
    }

    /** Tablets and unfolded foldables (spec 5), dp. */
    object Wide {
        const val ART_OUT = 340f
        const val ART_COMFORT = 150f
        const val ARTIST = 240f

        /** The cover's side Out, leaving room under it for the title in a [h] tall area. */
        fun artOut(h: Float): Float = minOf(ART_OUT, h - 130f).coerceAtLeast(ART_COMFORT)
    }

    /** The two ends of the upright layout (spec 3), dp, on the mock-up's 360 x 780 screen under the 44 dp top bar. */
    object Upright {
        const val ART_OUT = 290f
        /** A Medium-wide window standing upright (a tablet in portrait): the cover can grow to this. */
        const val ART_CAP_WIDE = 420f
        const val ART_TOP_OUT = 8f
        const val ART_COMFORT = 52f
        const val ART_X_COMFORT = 16f
        const val ART_TOP_COMFORT = 6f
        const val TITLE_X_OUT = 34f
        const val TITLE_X_COMFORT = 78f
        const val TITLE_SP_OUT = 22f
        const val TITLE_SP_COMFORT = 16f
        const val BODY_TOP_COMFORT = 68f
        /** The sides the cover keeps clear of at most (360 - 290). */
        const val ART_SIDES = 70f
        /** The cover takes at most this share of the space between the top bar and the tabs. */
        const val ART_SHARE = 0.56f
        /** Out, the body's peek is at least this tall (about five lines of biography). */
        const val PEEK_MIN = 120f
        /** The fade at the body's top and bottom edges. */
        const val FADE = 0.18f
        const val TOP_BAR = 44f
        const val CORNER_OUT = 8f
        const val CORNER_COMFORT = 6f

        /** The cover's side Out, for a body area [w] x [h] (between the top bar and the tabs). */
        fun artOut(w: Float, h: Float, cap: Float = ART_OUT): Float =
            minOf(cap, w - ART_SIDES, h * ART_SHARE, h - PEEK_MIN - 90f).coerceAtLeast(ART_COMFORT)

        fun lerp(a: Float, b: Float, t: Float): Float = a + (b - a) * t
    }
}
