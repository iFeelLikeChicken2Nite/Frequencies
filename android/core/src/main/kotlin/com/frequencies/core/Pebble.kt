package com.frequencies.core

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive

/**
 * The Pebble watchapp's messages (task 234, build 190): what the phone and
 * the watch say to each other through PebbleKit Android 2 (AppMessage).
 * Plain Kotlin so it is tested here; the watch's side is pebble/src/c
 * (freq.h, comm.c) and the table is notes/pebble-protocol.md. Keep all
 * three in step.
 */
object Pebble {
    const val PROTOCOL = 1
    const val WATCHAPP_UUID = "45e9b1e5-580c-414a-a559-e7a31f438d51"

    /** Message keys (package.json messageKeys). */
    object Key {
        const val PROTOCOL = 0; const val CMD = 1; const val ARG = 2; const val ARG2 = 3; const val TEXT = 4; const val WATCH = 5
        const val STATION = 10; const val SONG = 11; const val ARTIST = 12; const val STATE = 13; const val FLAGS = 14
        const val SLEEP_LEFT = 15; const val SLEEP_ACTION = 16; const val AD_LEFT = 17; const val POS = 18; const val COUNT = 19
        const val THEME = 20; const val PREFS = 21; const val THEME_NAME = 22; const val SORT = 23; const val TAGS = 24
        const val VOLUME = 25  // [step, steps] of the phone's media volume (build 191)
        const val STATION_SET = 26; const val STATION_COVER = 27  // the playing station's settings (build 196): StationSet
        const val ART_ID = 30; const val ART_KIND = 31; const val ART_W = 32; const val ART_H = 33; const val ART_OFFSET = 34
        const val ART_DATA = 35; const val ART_TOTAL = 36; const val ART_SLOT = 37
        const val LIST_KIND = 40; const val LIST_TOTAL = 41; const val LIST_START = 42; const val LIST_ROWS = 43
        const val BIO_NAME = 50; const val BIO_TEXT = 51; const val BIO_FROM = 52; const val VERDICT = 53
        const val THUMBS_LEFT = 54; const val THUMBS_WHAT = 55
        const val LYRIC_PREV = 60; const val LYRIC_CUR = 61; const val LYRIC_NEXT = 62; const val LYRIC_STATE = 63
    }

    /** Watch -> phone commands (CMD). */
    object Cmd {
        const val HELLO = 1; const val BYE = 2; const val PLAY_PAUSE = 3; const val NEXT = 4; const val PREV = 5
        const val SCAN_TO = 6; const val PLAY_ITEM = 7; const val PLAY_RANDOM = 8; const val STAR = 9; const val SHUFFLE = 10
        const val SONG_ID = 11; const val THUMB_UP = 12; const val THUMB_DOWN = 13; const val THUMB_UNDO = 14
        const val THUMB_SEND_NOW = 15; const val SLEEP_SET = 16; const val SLEEP_ACTION = 17; const val GET_LIST = 18
        const val SET_TAG = 19; const val FAVORITE = 20; const val GET_BIO = 21; const val LYRICS = 22; const val LYRIC_HOLD = 23
        const val LYRIC_STEP = 24; const val LYRIC_SYNC = 25; const val SEARCH = 26; const val LISTEN = 27; const val SAVE = 28
        const val OPEN_SERVICE = 29; const val SORT = 30; const val PREF = 31; const val FIND_MORE = 32; const val GET_FEATURED = 33
        const val VOLUME = 34  // ARG -1 = tell me, else set the media volume to that step (build 191)
        const val STATION = 35  // ARG a StationSet field (-1 = tell me), ARG2 its value (build 196)
    }

    object Lists {
        const val SAVED = 1; const val TAGS = 2; const val FAVORITES = 3; const val HISTORY = 4; const val SEARCH = 5
        const val FEATURED = 6; const val PREV_FEATURED = 7; const val THEMES = 8; const val PICK_TAG = 9
        const val COVERS = 10  // frequencies that can cover the playing station's opening ad (build 196)
    }

    object State { const val STOPPED = 0; const val PLAYING = 1; const val PAUSED = 2; const val TUNING = 3; const val BUFFERING = 4; const val DOWN = 5 }

    object Flag {
        const val STARRED = 0x01; const val SHUFFLE = 0x02; const val SONG_ID = 0x04; const val LYRICS = 0x08
        const val BIO = 0x10; const val SAVED = 0x20; const val HAS_SONG = 0x40; const val LOGO = 0x80
        /** 199: this song can't be a favorite (music folder, iBroadcast), so the watch shows no star. FLAGS is two bytes since. */
        const val NO_STAR = 0x100
    }

    object RowFlag {
        const val PLAYING = 0x01; const val DOWN = 0x02; const val FAVORITE = 0x04; const val SAVED = 0x08
        const val TAG_ON = 0x10; const val TAG_HIDDEN = 0x20; const val HEADING = 0x40; const val CURRENT = 0x80
    }

    object Art { const val COVER = 1; const val LOGO = 2; const val PHOTO = 3; const val FEATURED = 4 }
    object Pref { const val FOLLOW = 0; const val THEME = 1; const val ACCENT = 2; const val GROW = 3; const val NAMES = 4; const val NAMES_SECONDS = 5 }

    /** The grown Now Playing's name line (192): artist and station taking turns, or just one of them. */
    object Names {
        val KEYS = listOf("both", "artist", "station")
        const val SECONDS_DEFAULT = 7
        const val SECONDS_MIN = 3
        const val SECONDS_MAX = 60
        fun mode(key: String): Int = KEYS.indexOf(key).coerceAtLeast(0)
        fun clampSeconds(v: Int): Int = v.coerceIn(SECONDS_MIN, SECONDS_MAX)
    }

    /** The watch's abilities, sent with HELLO (WATCH). */
    object Caps { const val COLOR = 1; const val ROUND = 2; const val LARGE = 4; const val TOUCH = 8; const val MIC = 16 }

    /** The watch's sort menu, in its order (Station.kt SORT_LABELS keys). */
    val SORT_MODES = listOf("manual", "alphabetical", "added", "played", "play_count")
    /** The watch's sleep menu sends minutes; the phone's SleepTimer.ACTIONS keys by index. */
    val SLEEP_ACTIONS = listOf("fade", "switch", "tag")
    /** RadioBrowser.BROWSE on the watch's Search strip (chip 1..5); chip 0 is a dictated search. */
    val SEARCH_CHIPS = listOf("", "Most listened", "Trending", "Random", "Most voted", "New")
    /** History.searchLinks order on the watch's "Open on your phone". */
    val SERVICES = listOf("Apple Music", "YouTube Music", "Spotify", "Deezer", "Tidal")

    /** Largest list message the phone sends (the watch's inbox is 4 KB; leave room for the other keys). */
    const val ROWS_BUDGET = 1500
    /** Picture bytes per message. */
    const val ART_CHUNK = 2000
    /** The watch won't take a picture bigger than this. */
    const val ART_MAX = 16000

    // ---------------------------------------------------------------- commands

    /** One message from the watch. */
    data class Command(val cmd: Int, val arg: Int = 0, val arg2: Int = 0, val text: String? = null, val caps: Int = 0, val protocol: Int = 0)

    /** [values]: key -> Number, String or ByteArray, as the dictionary came in. */
    fun parse(values: Map<Int, Any?>): Command? {
        fun num(k: Int): Int? = when (val v = values[k]) {
            is Number -> v.toInt()
            is UByte -> v.toInt(); is UShort -> v.toInt(); is UInt -> v.toInt()
            else -> null
        }
        val cmd = num(Key.CMD) ?: return null
        val text = when (val t = values[Key.TEXT]) {
            is String -> t.trimEnd('\u0000')
            is ByteArray -> t.toString(Charsets.UTF_8).trimEnd('\u0000')
            else -> null
        }
        return Command(cmd, num(Key.ARG) ?: 0, num(Key.ARG2) ?: 0, text, num(Key.WATCH) ?: 0, num(Key.PROTOCOL) ?: 0)
    }

    // ---------------------------------------------------------------- text

    /** [s] cut to at most [maxBytes] of UTF-8, never inside a character. */
    fun utf8(s: String, maxBytes: Int): ByteArray {
        val all = s.toByteArray(Charsets.UTF_8)
        if (all.size <= maxBytes) return all
        var end = maxBytes
        while (end > 0 && (all[end].toInt() and 0xC0) == 0x80) end--
        return all.copyOf(end)
    }

    fun fit(s: String, maxBytes: Int): String = utf8(s, maxBytes).toString(Charsets.UTF_8)

    // ---------------------------------------------------------------- rows

    data class Row(
        val index: Int,
        val id: Long,
        val name: String,
        val sub: String = "",
        val flags: Int = 0,
        val color: Int = 0,
        val time: Long = 0,
        val tags: List<Int> = emptyList(),
    )

    /** u16 index, u32 id, u8 flags, u8 color, i32 time, u8 ntags, tags, u8 len + name (≤43), u8 len + sub (≤51); little-endian. */
    fun packRow(r: Row): ByteArray {
        val name = utf8(r.name, 43)
        val sub = utf8(r.sub, 51)
        val tags = r.tags.take(4)
        val out = java.io.ByteArrayOutputStream()
        fun u16(v: Int) { out.write(v and 0xFF); out.write((v shr 8) and 0xFF) }
        fun u32(v: Long) { for (i in 0 until 4) out.write(((v shr (8 * i)) and 0xFF).toInt()) }
        u16(r.index); u32(r.id); out.write(r.flags and 0xFF); out.write(r.color and 0xFF); u32(r.time and 0xFFFFFFFFL)
        out.write(tags.size); tags.forEach { out.write(it and 0xFF) }
        out.write(name.size); out.write(name); out.write(sub.size); out.write(sub)
        return out.toByteArray()
    }

    fun packRows(rows: List<Row>): ByteArray = rows.fold(ByteArray(0)) { acc, r -> acc + packRow(r) }

    /** [rows] in groups whose packed size stays within [budget]. */
    fun rowMessages(rows: List<Row>, budget: Int = ROWS_BUDGET): List<ByteArray> {
        val out = mutableListOf<ByteArray>()
        var cur = ByteArray(0)
        for (r in rows) {
            val b = packRow(r)
            if (cur.isNotEmpty() && cur.size + b.size > budget) { out += cur; cur = ByteArray(0) }
            cur += b
        }
        if (cur.isNotEmpty()) out += cur
        return out
    }

    /** The watch's decoder (comm.c rows_tuple), here for the tests. */
    fun unpackRows(p: ByteArray): List<Row> {
        val out = mutableListOf<Row>()
        var i = 0
        fun u(k: Int) = p[k].toInt() and 0xFF
        while (i + 13 <= p.size) {
            val index = u(i) or (u(i + 1) shl 8)
            val id = (u(i + 2).toLong() or (u(i + 3).toLong() shl 8) or (u(i + 4).toLong() shl 16) or (u(i + 5).toLong() shl 24))
            val flags = u(i + 6); val color = u(i + 7)
            val time = (u(i + 8) or (u(i + 9) shl 8) or (u(i + 10) shl 16) or (u(i + 11) shl 24)).toLong()
            val nt = u(i + 12)
            i += 13
            val tags = (0 until nt).map { u(i + it) }; i += nt
            val ln = u(i++); val name = String(p, i, ln, Charsets.UTF_8); i += ln
            val ls = u(i++); val sub = String(p, i, ls, Charsets.UTF_8); i += ls
            out += Row(index, id, name, sub, flags, color, time, tags)
        }
        return out
    }

    /** A stable 32-bit id for a string id (station ids, history keys): the watch hands it back with a command. */
    fun id32(s: String): Long {
        var h = 0x811C9DC5L
        for (b in s.toByteArray(Charsets.UTF_8)) { h = h xor (b.toLong() and 0xFF); h = (h * 0x01000193L) and 0xFFFFFFFFL }
        return if (h == 0L) 1L else h
    }

    // ---------------------------------------------------------------- tags

    data class Tag(val name: String, val color: Int, val state: Int, val count: Int)

    /** [count] then per tag: u8 color, u8 state (0 off, 1 shown, 2 hidden), u16 count, u8 len + name (≤23). At most 24. */
    fun packTags(tags: List<Tag>): ByteArray {
        val out = java.io.ByteArrayOutputStream()
        val list = tags.take(24)
        out.write(list.size)
        for (t in list) {
            val n = utf8(t.name, 23)
            out.write(t.color and 0xFF); out.write(t.state and 0xFF)
            out.write(t.count and 0xFF); out.write((t.count shr 8) and 0xFF)
            out.write(n.size); out.write(n)
        }
        return out.toByteArray()
    }

    /** The watch's tag state (0 off, 1 shown, 2 hidden) from the app's tag filter query. */
    fun tagState(query: String, tag: String): Int = when (Search.tagState(query, tag)) {
        Search.INCLUDED -> 1
        Search.EXCLUDED -> 2
        else -> 0
    }

    /** The tag filter query after the watch set [tag] to [state]. */
    fun setTag(query: String, tag: String, state: Int): String =
        Search.setTag(query, tag, when (state) { 1 -> Search.INCLUDED; 2 -> Search.EXCLUDED; else -> Search.NEUTRAL })

    // ---------------------------------------------------------------- colors

    private fun level(c: Int) = ((c + 42) / 85).coerceIn(0, 3)

    /** The nearest of Pebble's 64 colors, as GColor8 (0b11rrggbb). */
    fun gcolor8(rgb: Int): Int = 0xC0 or (level((rgb shr 16) and 0xFF) shl 4) or (level((rgb shr 8) and 0xFF) shl 2) or level(rgb and 0xFF)

    fun rgbOf(g: Int): Int = (((g shr 4) and 3) * 85 shl 16) or (((g shr 2) and 3) * 85 shl 8) or ((g and 3) * 85)

    /**
     * The watch's 9 colors from an app theme's tokens: bg, text, text2, text3, accent, playing, down, stripe, good.
     * The stripe is kept apart from the background even when both snap to the same color.
     */
    fun themeBytes(tokens: Map<String, Int>, accent: Int? = null, light: Boolean = false): ByteArray {
        val bg = gcolor8(tokens["bg0"] ?: 0)
        val stripeSrc = tokens["bar_bg"] ?: tokens["bg2"] ?: 0
        var stripe = gcolor8(stripeSrc)
        if (stripe == bg) {
            // one step lighter (dark themes) or darker (light themes) on the stripe's strongest channels
            val ch = intArrayOf((stripeSrc shr 16) and 0xFF, (stripeSrc shr 8) and 0xFF, stripeSrc and 0xFF)
            val m = ch.max()
            val bl = intArrayOf((bg shr 4) and 3, (bg shr 2) and 3, bg and 3)
            val nl = IntArray(3) { i -> if (ch[i] >= m - 8) (bl[i] + if (light) -1 else 1).coerceIn(0, 3) else bl[i] }
            stripe = 0xC0 or (nl[0] shl 4) or (nl[1] shl 2) or nl[2]
        }
        fun c(k: String, fb: Int) = gcolor8(tokens[k] ?: fb)
        var text3 = c("text3", tokens["text2"] ?: 0x888888)
        if (text3 == bg) text3 = c("text2", 0x888888)
        return byteArrayOf(
            bg.toByte(), c("text", 0xFFFFFF).toByte(), c("text2", 0xAAAAAA).toByte(), text3.toByte(),
            gcolor8(accent ?: tokens["accent"] ?: 0xFFAA00).toByte(), c("playing", 0x55FFFF).toByte(),
            c("down", tokens["danger"] ?: 0xFF5555).toByte(), stripe.toByte(),
            (if (light) 0xC8 else 0xDD).toByte(),  // IslamicGreen on light, ScreaminGreen on dark (thumbs up)
        )
    }

    // ---------------------------------------------------------------- pictures

    private val BAYER = arrayOf(intArrayOf(0, 8, 2, 10), intArrayOf(12, 4, 14, 6), intArrayOf(3, 11, 1, 9), intArrayOf(15, 7, 13, 5))

    /**
     * ARGB pixels to Pebble 8-bit color (one byte a pixel). Since build 191 the rounding error is spread to the
     * neighbors (Floyd–Steinberg, every other row right to left) instead of a 4×4 pattern: smoother skin and skies,
     * no cross-hatching (user: "the dithering is also kinda not good").
     */
    fun color8(px: IntArray, w: Int, h: Int, dither: Boolean = true): ByteArray {
        val out = ByteArray(w * h)
        // error carried in 1/16ths, this row and the next, per channel, with a pixel of margin each side
        var cur = IntArray((w + 2) * 3)
        var nxt = IntArray((w + 2) * 3)
        for (y in 0 until h) {
            java.util.Arrays.fill(nxt, 0)
            val ltr = y % 2 == 0
            val f = if (ltr) 1 else -1
            for (k in 0 until w) {
                val x = if (ltr) k else w - 1 - k
                val p = px[y * w + x]
                var g = 0xC0
                for (ch in 0 until 3) {
                    val c = (p shr (16 - ch * 8)) and 0xFF
                    val i = (x + 1) * 3 + ch
                    val v = if (dither) c + cur[i] / 16 else c
                    val q = level(v.coerceIn(0, 255))
                    g = g or (q shl (4 - ch * 2))
                    if (!dither) continue
                    val e = (v - q * 85).coerceIn(-128, 128)
                    cur[i + f * 3] += e * 7
                    nxt[i - f * 3] += e * 3
                    nxt[i] += e * 5
                    nxt[i + f * 3] += e
                }
                out[y * w + x] = g.toByte()
            }
            val t = cur; cur = nxt; nxt = t
        }
        return out
    }

    /**
     * ARGB pixels to a 1-bit shape, rows of (w+7)/8 bytes, first pixel in the top bit (Pebble's 1-bit palette format).
     * [logo]: a pixel counts when it is opaque and light (a band logo's letters); else by brightness, dithered (B&W watches).
     */
    fun mono(px: IntArray, w: Int, h: Int, logo: Boolean): ByteArray {
        val stride = (w + 7) / 8
        val out = ByteArray(stride * h)
        fun luma(p: Int) = (((p shr 16) and 0xFF) * 299 + ((p shr 8) and 0xFF) * 587 + (p and 0xFF) * 114) / 1000
        // a logo with no see-through background: the shape is what differs from its corner color
        val opaque = logo && px.count { (it ushr 24) >= 128 } > px.size * 95 / 100
        val corner = if (px.isNotEmpty()) luma(px[0]) else 0
        for (y in 0 until h) for (x in 0 until w) {
            val p = px[y * w + x]
            val on = when {
                !logo -> luma(p) > BAYER[y and 3][x and 3] * 16 + 8
                opaque -> kotlin.math.abs(luma(p) - corner) > 60
                else -> (p ushr 24) >= 128
            }
            if (on) out[y * stride + x / 8] = (out[y * stride + x / 8].toInt() or (0x80 ushr (x % 8))).toByte()
        }
        return out
    }

    /** [data] in pieces of at most [size] bytes: (offset, bytes). */
    fun chunks(data: ByteArray, size: Int = ART_CHUNK): List<Pair<Int, ByteArray>> =
        (data.indices step size).map { it to data.copyOfRange(it, minOf(data.size, it + size)) }

    /** A picture's size on the watch: [w]×[h] shrunk to fit [maxW]×[maxH] and the watch's byte limit. */
    fun fitSize(w: Int, h: Int, maxW: Int, maxH: Int, bytesPerPixel: Double = 1.0): Pair<Int, Int> {
        if (w <= 0 || h <= 0) return 0 to 0
        var s = minOf(1.0, maxW.toDouble() / w, maxH.toDouble() / h)
        var nw = maxOf(1, (w * s).toInt()); var nh = maxOf(1, (h * s).toInt())
        while (nw * nh * bytesPerPixel > ART_MAX) { s *= 0.95; nw = maxOf(1, (w * s).toInt()); nh = maxOf(1, (h * s).toInt()) }
        return nw to nh
    }

    // ---------------------------------------------------------------- station settings (build 196)

    /**
     * The playing station's own settings on the watch (user's sketch, 2026-11-10): Station settings at the
     * bottom of Now Playing's hold-Select menu. The same keys the phone's station settings save.
     */
    object StationSet {
        const val ASK = -1; const val GAIN = 0; const val AD_ON = 1; const val AD_SECONDS = 2; const val AD_ANY = 3
        const val AD_COVER = 4; const val BUFFER_ON = 5; const val BUFFER_SECONDS = 6; const val TALK = 7; const val EQ_RESET = 8
        const val EQ3 = 10; const val EQ10 = 20

        const val SAVED = 0x01; const val AD_OFFERED = 0x02; const val AD_ON_FLAG = 0x04; const val AD_CHOSEN = 0x08
        const val BUFFER_ON_FLAG = 0x10; const val TALK_FLAG = 0x20; const val EQ_ADVANCED = 0x40
        const val SIZE = 18

        /**
         * STATION_SET: [0] flags, [1] level trim in half dB + 40, [2] ad seconds 1..120, [3] pre-buffer half seconds
         * (0 = auto), [4] the measured worst start in half seconds, [5..7] bass/mid/treble + 12, [8..17] ten bands + 12.
         */
        fun bytes(st: Station, saved: Boolean, advanced: Boolean): ByteArray {
            val b = ByteArray(SIZE)
            var f = 0
            if (saved) f = f or SAVED
            if (Twitch.adCoverOffered(st)) f = f or AD_OFFERED
            if (Twitch.adCoverOn(st)) f = f or AD_ON_FLAG
            if ((st["ad_filler_kind"].str() ?: "any") == "chosen" && !st["ad_filler_station_id"].str().isNullOrEmpty()) f = f or AD_CHOSEN
            if (truthy(st["buffer_enabled"])) f = f or BUFFER_ON_FLAG
            if (st.talkRadio) f = f or TALK_FLAG
            if (advanced) f = f or EQ_ADVANCED
            b[0] = f.toByte()
            b[1] = (Math.round(Equalizer.gainDb(st) * 2) + 40).toInt().coerceIn(0, 80).toByte()
            b[2] = Math.round(Twitch.adCoverSeconds(st).takeIf { it > 0 } ?: 15.0).toInt().coerceIn(1, 120).toByte()
            b[3] = Math.round((st["buffer_seconds"].num() ?: 0.0) * 2).toInt().coerceIn(0, 120).toByte()
            b[4] = Math.round(Prebuffer.worst(st) * 2).toInt().coerceIn(0, 240).toByte()
            Equalizer.threeBand(st).forEachIndexed { i, v -> b[5 + i] = (v + 12).toByte() }
            Equalizer.tenBand(st).take(10).forEachIndexed { i, v -> b[8 + i] = (v.coerceIn(-12, 12) + 12).toByte() }
            return b
        }

        /** One change from the watch, into [st] (a copy of the saved station). [coverId]: AD_COVER's frequency. */
        fun apply(st: Station, field: Int, value: Int, coverId: String? = null) {
            when (field) {
                GAIN -> st["gain_db"] = floatJson((value.coerceIn(-40, 40)) / 2.0)
                AD_ON -> {
                    st["ad_override"] = JsonPrimitive(value != 0)
                    if ((st["ad_seconds"].num() ?: 0.0) <= 0) st["ad_seconds"] = floatJson(Twitch.adCoverSeconds(st).takeIf { it > 0 } ?: 15.0)
                }
                AD_SECONDS -> st["ad_seconds"] = floatJson(value.coerceIn(1, 120).toDouble())
                AD_ANY -> { st["ad_filler_kind"] = JsonPrimitive("any"); st["ad_filler_station_id"] = JsonPrimitive(""); st["ad_filler_url"] = JsonPrimitive("") }
                AD_COVER -> if (!coverId.isNullOrEmpty()) {
                    st["ad_filler_kind"] = JsonPrimitive("chosen"); st["ad_filler_station_id"] = JsonPrimitive(coverId); st["ad_filler_url"] = JsonPrimitive("")
                }
                BUFFER_ON -> st["buffer_enabled"] = JsonPrimitive(value != 0)
                BUFFER_SECONDS -> st["buffer_seconds"] = floatJson(value.coerceIn(0, 120) / 2.0)
                TALK -> st["talk_radio"] = JsonPrimitive(value != 0)
                EQ_RESET -> {
                    st["eq_bass"] = intJson(0); st["eq_mid"] = intJson(0); st["eq_treble"] = intJson(0)
                    st["eq_bands"] = JsonArray(List(10) { JsonPrimitive(0) })
                }
                in EQ3 until EQ3 + 3 -> st[listOf("eq_bass", "eq_mid", "eq_treble")[field - EQ3]] = intJson(value.coerceIn(-12, 12).toLong())
                in EQ10 until EQ10 + 10 -> {
                    val ten = Equalizer.tenBand(st).toMutableList().also { while (it.size < 10) it.add(0) }
                    ten[field - EQ10] = value.coerceIn(-12, 12)
                    st["eq_bands"] = JsonArray(ten.map { JsonPrimitive(it) })
                }
            }
        }
    }

    // ---------------------------------------------------------------- small things

    /** SORT: mode index | descending << 7. */
    fun sortByte(mode: String, descending: Boolean): Int = (SORT_MODES.indexOf(mode).coerceAtLeast(0)) or (if (descending) 0x80 else 0)

    /** Seconds left, for SLEEP_LEFT / AD_LEFT (0 = off). */
    fun secondsLeft(ms: Long?): Int = if (ms == null || ms <= 0) 0 else ((ms + 999) / 1000).toInt()
}
