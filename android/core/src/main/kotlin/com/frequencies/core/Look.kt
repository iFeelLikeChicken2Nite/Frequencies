package com.frequencies.core

/**
 * Settings' tabs, as on the desktop (optionspage.py): which tab each
 * setting the phone's Settings screen shows lives on. Connections (the
 * desktop's Accounts, renamed by the user in build 194) holds the streaming
 * services (iBroadcast) and the Pebble app that may connect; there is no
 * Keyboard tab on a phone.
 */
object SettingsTabs {
    const val APPEARANCE = "Appearance"
    const val SOUND = "Sound"
    const val FREQUENCIES = "Frequencies"
    const val LYRICS = "Lyrics and artists"
    const val ACCOUNTS = "Connections"
    const val LIBRARY = "Library and tools"

    val ALL = listOf(APPEARANCE, SOUND, FREQUENCIES, LYRICS, ACCOUNTS, LIBRARY)

    /** The settings keys each tab changes. */
    val KEYS: Map<String, List<String>> = mapOf(
        APPEARANCE to listOf("theme", Settings.ANDROID_THEME, Settings.ANDROID_THEME_DARK, Settings.ANDROID_THEME_LIGHT, "accent_source", Settings.ANDROID_ACCENT, Settings.ANDROID_ACCENT_SHARED, "accent_fixed", Comfort.AUTO, Comfort.RETRACT_SECONDS, Comfort.KEEP_AWAKE, Aurora.SETTING, "car_sort_mode"),
        SOUND to listOf("noise_between_stations", "noise_volume", "normalize", "eq_advanced", "sleep_action", "sleep_wake_station_id", "sleep_wake_tag"),
        FREQUENCIES to listOf("jump_on_change", "health_recheck", "health_recheck_minutes", "auto_skip_broken", "show_reminders"),
        LYRICS to listOf("lyrics_on", "lyric_size", PhotoPackStore.SETTING, BandLogos.SETTING),
        ACCOUNTS to listOf("pebble_only_app"),
        LIBRARY to listOf(Hints.KEY, SyncRuns.INTERVAL_KEY),
    )

    /** The tab [key] is on, or null for one Settings doesn't show. */
    fun tabOf(key: String): String? = KEYS.entries.firstOrNull { key in it.value }?.key

    /** A link into Settings (the battery hint, a notification): the tab it lands on. */
    fun indexOf(tab: String?): Int = ALL.indexOf(tab).coerceAtLeast(0)
}

/**
 * Now Playing's background, the Comfort View wash (desktop Comfort View,
 * drafts' look C): a glow of the accent from the top left over a canvas
 * tinted toward the theme's playing color at the top, with the text
 * colors that read on every part of it.
 */
data class ComfortWash(
    /** The glow's color at the top-left corner (it fades out by 60% of the way). */
    val glow: Int,
    /** The canvas at the top (tinted) and at the bottom (bg0). */
    val top: Int,
    val bottom: Int,
    val text: Int,
    val text2: Int,
    /** The accent as text (the lyric being sung). */
    val accent: Int,
) {
    companion object {
        const val GLOW_DARK = 0.32
        const val GLOW_LIGHT = 0.22
        const val TINT_DARK = 0.14
        const val TINT_LIGHT = 0.10

        fun of(theme: ShellTheme, scheme: ThemeScheme): ComfortWash {
            val bg0 = scheme.background
            val top = ThemeScheme.mix(scheme.playing, bg0, if (scheme.light) TINT_LIGHT else TINT_DARK)
            // The spec's strength, softened only when a very bright accent would leave no text color
            // that reads on the glow (Steam Classic with a yellow cover, for one).
            var strength = if (scheme.light) GLOW_LIGHT else GLOW_DARK
            while (true) {
                val glow = ThemeScheme.mix(scheme.primary, bg0, strength)
                val under = listOf(glow, top, bg0)
                val wash = ComfortWash(
                    glow = glow,
                    top = top,
                    bottom = bg0,
                    text = ThemeScheme.readable(theme.token("text"), under, ThemeScheme.TEXT),
                    text2 = ThemeScheme.readable(theme.token("text2"), under, ThemeScheme.TEXT),
                    accent = ThemeScheme.readable(scheme.primary, under, ThemeScheme.TEXT),
                )
                val reads = listOf(wash.text, wash.text2, wash.accent).all { fg ->
                    under.all { ThemeScheme.contrast(fg, it) >= ThemeScheme.TEXT }
                }
                if (reads || strength <= 0.0) return wash
                strength = (strength - 0.02).coerceAtLeast(0.0)
            }
        }
    }
}
