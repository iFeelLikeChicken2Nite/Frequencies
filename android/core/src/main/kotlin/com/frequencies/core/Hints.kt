package com.frequencies.core

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive

/**
 * First-run hints (the phone's stand-in for the desktop's Clippy tips,
 * which don't come to Android): one short tip per place that needs it,
 * shown once, dismissed for good, all back with Settings' "Show the tips
 * again". Kept in settings.json under an Android-only key.
 */
object Hints {
    const val KEY = "android_hints_seen"

    const val SEARCH = "add_search"
    const val SELECT = "long_press"
    const val SHARE = "share_qr"
    const val AUTO = "android_auto"
    const val NOTIFICATIONS = "notifications"

    /** Where each shows, and its words (American spelling, plain). */
    val TEXT = linkedMapOf(
        SEARCH to "Search by a station's name or a genre, like jazz or lofi. Tap a result to hear it first; + keeps it.",
        SELECT to "Touch and hold a frequency to select it. Then share, tag or remove several at once.",
        SHARE to "Share sends a code or a QR picture. Anyone with Frequencies, on a phone or a computer, can add the station from it.",
        AUTO to "Frequencies works in Android Auto. Plug in your phone or connect wirelessly, then pick Frequencies from the car's media apps.",
        NOTIFICATIONS to "Allow notifications so playback controls show on the lock screen and show reminders can reach you.",
    )

    fun seen(settings: Settings): Set<String> = (settings[KEY] as? JsonArray)?.mapNotNull { it.str() }?.toSet().orEmpty()

    fun shouldShow(settings: Settings, hint: String): Boolean = hint !in seen(settings)

    fun dismiss(settings: Settings, hint: String) {
        settings[KEY] = stringArray((seen(settings) + hint).sorted())
    }

    fun reset(settings: Settings) {
        settings[KEY] = JsonArray(emptyList<JsonPrimitive>())
    }
}
