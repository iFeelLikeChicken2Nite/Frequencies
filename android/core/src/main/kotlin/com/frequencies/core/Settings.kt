package com.frequencies.core

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * settings.json, as the desktop keeps it: the defaults with the file laid
 * over them, every key saved. Keys the phone has no use for (Winamp,
 * projectM, tray, window sizes) are kept and written back untouched.
 */
class Settings(val data: LinkedHashMap<String, JsonElement>) {

    fun toJson(): JsonObject = JsonObject(LinkedHashMap(data))

    operator fun get(key: String): JsonElement? = data[key]
    operator fun set(key: String, value: JsonElement) { data[key] = value }

    fun bool(key: String, fallback: Boolean = false): Boolean = data[key]?.let { truthy(it) } ?: fallback
    fun double(key: String, fallback: Double): Double = data[key].num() ?: fallback
    fun string(key: String, fallback: String = ""): String = data[key].str() ?: fallback

    var lastStationId: String?
        get() = data["last_station_id"].str()?.takeIf { it.isNotEmpty() }
        set(v) { data["last_station_id"] = if (v == null) kotlinx.serialization.json.JsonNull else JsonPrimitive(v) }

    val sortMode: String get() = string("sort_mode", "manual")
    /** settings sort_desc: null until the user flips the direction. */
    val sortDescending: Boolean? get() = data["sort_desc"]?.takeIf { it !is kotlinx.serialization.json.JsonNull }?.let { truthy(it) }
    val staticOn: Boolean get() = bool("noise_between_stations", true)
    /** The static's own level, 0-100 (100 since build 120). */
    val staticVolume: Int get() = double("noise_volume", 100.0).toInt().coerceIn(0, 100)
    val crossfadeMs: Int get() = double("crossfade_ms", 700.0).toInt().coerceIn(0, 5000)
    val clock12h: Boolean get() = bool("clock_12h", true)

    // Android's own keys. The desktop keeps keys it doesn't know, so these
    // ride along harmlessly in a backup.
    /** "system", "dark" or "light". */
    var androidTheme: String
        get() = string(ANDROID_THEME, "system").takeIf { it in listOf("system", "dark", "light") } ?: "system"
        set(v) { data[ANDROID_THEME] = JsonPrimitive(v) }

    /** The desktop's "theme": one of its shell themes (ShellThemes), OG by default. */
    var shellTheme: String
        get() = ShellThemes.find(string("theme", ShellThemes.DEFAULT)).key
        set(v) { data["theme"] = JsonPrimitive(v) }

    /**
     * The dark theme and the light theme (user, build 170: "let them pick
     * which dark and light theme they want"). The shared "theme" (the
     * desktop's, which syncs) fills whichever of the two it suits; the
     * other is the phone's own (android_theme_dark / android_theme_light),
     * OG and Flashbang until chosen.
     */
    var darkTheme: String
        get() {
            val shared = ShellThemes.find(string("theme", ShellThemes.DEFAULT))
            if (!shared.light) return shared.key
            return ShellThemes.all.firstOrNull { it.key == string(ANDROID_THEME_DARK, "") && !it.light }?.key ?: ShellThemes.DEFAULT
        }
        set(v) {
            val theme = ShellThemes.find(v)
            if (theme.light) return
            data[ANDROID_THEME_DARK] = JsonPrimitive(theme.key)
            if (!ShellThemes.find(string("theme", ShellThemes.DEFAULT)).light) shellTheme = theme.key
        }

    var lightTheme: String
        get() {
            val shared = ShellThemes.find(string("theme", ShellThemes.DEFAULT))
            if (shared.light) return shared.key
            return ShellThemes.all.firstOrNull { it.key == string(ANDROID_THEME_LIGHT, "") && it.light }?.key ?: ShellThemes.LIGHT_DEFAULT
        }
        set(v) {
            val theme = ShellThemes.find(v)
            if (!theme.light) return
            data[ANDROID_THEME_LIGHT] = JsonPrimitive(theme.key)
            if (ShellThemes.find(string("theme", ShellThemes.DEFAULT)).light) shellTheme = theme.key
        }

    /**
     * What the phone shows: Dark / Light pick the dark or the light theme,
     * System follows the phone's own dark mode (build 170; before, only OG
     * switched, to Flashbang, and any other theme always showed as it was).
     */
    fun shownTheme(systemDark: Boolean): ShellTheme {
        val dark = when (androidTheme) { "dark" -> true; "light" -> false; else -> systemDark }
        return ShellThemes.find(if (dark) darkTheme else lightTheme)
    }

    /**
     * The accent: [ACCENT_ARTWORK], [ACCENT_MATERIAL_YOU], [ACCENT_THEME] or
     * [ACCENT_FIXED]. The desktop's `accent_source` only knows "artwork",
     * "desktop" and "fixed" (an unknown one isn't guarded against before
     * desktop task 208), so the phone's own choice lives in
     * `android_accent`, with the `accent_source` and `accent_fixed` it
     * wrote beside it: when the desktop has changed either since, the
     * desktop's choice wins ("desktop", Match my desktop, is Material You here).
     */
    var accentMode: String
        get() {
            val shared = string("accent_source", "artwork")
            val mine = string(ANDROID_ACCENT)
            if (mine in ACCENT_MODES && data[ANDROID_ACCENT_SHARED].str() == sharedStamp()) {
                return if (mine == ACCENT_FIXED && accentFixed == null) ACCENT_ARTWORK else mine
            }
            return when (shared) {
                "fixed" -> if (accentFixed != null) ACCENT_FIXED else ACCENT_ARTWORK
                "desktop" -> ACCENT_MATERIAL_YOU
                "theme" -> ACCENT_THEME
                else -> ACCENT_ARTWORK
            }
        }
        set(mode) {
            val now = string("accent_source", "artwork")
            val shared = when (mode) {
                ACCENT_FIXED -> "fixed"
                ACCENT_MATERIAL_YOU -> "desktop"
                ACCENT_ARTWORK -> "artwork"
                // Match the theme: the desktop's own "theme" since build 151
                // (sync section 6.2 lists it).
                ACCENT_THEME -> "theme"
                else -> now.takeIf { it in DESKTOP_ACCENT_SOURCES } ?: "artwork"
            }
            data["accent_source"] = JsonPrimitive(shared)
            data[ANDROID_ACCENT] = JsonPrimitive(mode)
            data[ANDROID_ACCENT_SHARED] = JsonPrimitive(sharedStamp())
        }

    private fun sharedStamp() = string("accent_source", "artwork") + " " + data["accent_fixed"].str().orEmpty()

    /** The desktop's `accent_fixed` ("#rrggbb", A color I choose), or null. */
    var accentFixed: Int?
        get() = data["accent_fixed"].str()?.trim()?.takeIf { Regex("#?[0-9a-fA-F]{6}").matches(it) }?.let(ShellThemes::parseHex)
        set(v) {
            val mode = accentMode
            if (v == null) data.remove("accent_fixed") else data["accent_fixed"] = JsonPrimitive("#%06x".format(v and 0xFFFFFF))
            accentMode = mode           // the phone's own change: keep its choice
        }

    /**
     * storage.tag_color: a tag's color, case-insensitive, given one from
     * the palette the first time it is asked for when [assign] is true.
     */
    fun tagColor(tag: String, assign: Boolean = false): String {
        val colors = LinkedHashMap((data["tag_colors"] as? JsonObject) ?: JsonObject(emptyMap()))
        val key = tag.trim().lowercase()
        colors[key].str()?.let { have ->
            return OLD_DIALOG_COLORS[have.trim().lowercase()] ?: have
        }
        if (!assign) return TAG_PALETTE[0]
        val color = TAG_PALETTE[colors.size % TAG_PALETTE.size]
        colors[key] = JsonPrimitive(color)
        data["tag_colors"] = JsonObject(colors)
        return color
    }

    companion object {
        const val ANDROID_THEME = "android_theme"
        const val ANDROID_THEME_DARK = "android_theme_dark"
        const val ANDROID_THEME_LIGHT = "android_theme_light"
        const val ANDROID_ACCENT = "android_accent"
        const val ANDROID_ACCENT_SHARED = "android_accent_shared"

        const val ACCENT_ARTWORK = "artwork"
        const val ACCENT_MATERIAL_YOU = "material_you"
        const val ACCENT_THEME = "theme"
        const val ACCENT_FIXED = "fixed"
        val ACCENT_MODES = listOf(ACCENT_ARTWORK, ACCENT_MATERIAL_YOU, ACCENT_THEME, ACCENT_FIXED)
        /** optionspage.ACCENT_CHOICES since desktop build 151 ("theme", task 208). */
        val DESKTOP_ACCENT_SOURCES = listOf("artwork", "desktop", "fixed", "theme")

        val TAG_PALETTE = listOf(
            "#d96c6c", "#d9914e", "#d9b54e", "#c9c04a", "#a3c94a", "#6fbf73", "#4ebf9e", "#4ea3c9",
            "#5a8fd9", "#7b7bd9", "#a86fd9", "#c96fbf", "#d96f9a", "#a67c52", "#6c7a89", "#8a8a8a",
        )
        private val OLD_DIALOG_COLORS = mapOf(
            "#e2564b" to "#d96c6c", "#e07a5a" to "#d9914e", "#d99a2b" to "#d9b54e", "#c9b23f" to "#c9c04a",
            "#9aae4a" to "#a3c94a", "#5dbb6a" to "#6fbf73", "#4fc39a" to "#4ebf9e", "#3fb5b5" to "#4ebf9e",
            "#43a6d4" to "#4ea3c9", "#5a8ce0" to "#5a8fd9", "#7f77dd" to "#7b7bd9", "#a06ddb" to "#a86fd9",
            "#c761c7" to "#c96fbf", "#dd6494" to "#d96f9a", "#d4537e" to "#d96f9a", "#8a93a6" to "#6c7a89",
        )

        /**
         * storage.REMOVED_SETTINGS: settings no platform has any more,
         * dropped when settings.json or a backup is read (so they are out
         * of the file at the next save). "local_station_ratio" was
         * "Custom station frequency" (user, 2026-11-04).
         */
        val REMOVED = listOf("local_station_ratio")

        /** storage.load_settings: defaults, the file over them, removed keys dropped, volume clamped. */
        fun from(file: JsonObject?): Settings {
            val d = LinkedHashMap<String, JsonElement>(Defaults.settings)
            file?.let { d.putAll(it) }
            // Shuffle was off by default before build 182 (desktop 181, task
            // 232): a settings file or backup that never saved it keeps it off.
            if (!file.isNullOrEmpty() && Shuffle.SETTING !in file) d[Shuffle.SETTING] = JsonPrimitive(false)
            REMOVED.forEach { d.remove(it) }
            val vol = d["volume"].num()
            val clamped = (vol ?: 80.0).toLong().coerceIn(0, 100)
            if (vol == null || clamped.toDouble() != vol) d["volume"] = intJson(clamped)
            return Settings(d)
        }
    }
}
