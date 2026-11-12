package com.frequencies.app

import android.content.Context
import android.content.res.Configuration
import android.net.Uri
import android.os.Build
import coil3.SingletonImageLoader
import coil3.request.ImageRequest
import coil3.request.SuccessResult
import coil3.request.allowHardware
import coil3.size.Size
import coil3.toBitmap
import com.frequencies.core.ArtAccent
import com.frequencies.core.Settings
import com.frequencies.core.ShellTheme
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The app's accent color, like the desktop's AccentController: one color
 * for the screens, the system bars and the playing notification.
 *
 * Follow the artwork (the default): the desktop's own pick from the
 * picture that is showing (ArtAccent), a one-second fade to it, the old
 * color held until new art arrives (a station change alone does nothing),
 * and the theme's accent for art with no color of its own. Material You:
 * the wallpaper's primary color (Android 12+; older phones get the
 * theme's). Match the theme: the theme's own accent. Custom: the color
 * chosen, made readable the way the desktop does it.
 */
class Accent(private val context: Context, private val library: Library) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val _color = MutableStateFlow(theme().accent)
    /** The accent being shown, 0xRRGGBB. */
    val color: StateFlow<Int> = _color.asStateFlow()

    private var target = _color.value
    private var fade: Job? = null
    private var setup: Triple<String, String, Int?>? = null
    private var artUri: Uri? = null
    /** The last art's pick: null before any, or when it had no color. */
    private var artColor: Int? = null
    private var artLoad: Job? = null

    init {
        scope.launch { library.settings.collect { refresh() } }
    }

    /** The theme the screens show now (OG follows the phone's dark mode). */
    fun theme(): ShellTheme = library.settings.value.shownTheme(systemDark())

    private fun systemDark() =
        (context.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES

    /** The choice or the theme changed, or the phone's wallpaper or dark mode may have: an answer to a question, so no fade. */
    fun refresh() {
        val s = library.settings.value
        val now = Triple(mode(s), theme().key, s.accentFixed)
        val changed = now != setup
        setup = now
        val wanted = when (now.first) {
            Settings.ACCENT_ARTWORK -> if (changed) artColor ?: theme().accent else null
            else -> fixedColor(now.first, s)
        }
        if (wanted != null) moveTo(wanted, animate = false)
    }

    private fun mode(s: Settings): String {
        val m = s.accentMode
        return if (m == Settings.ACCENT_MATERIAL_YOU && !materialYou) Settings.ACCENT_THEME else m
    }

    private fun fixedColor(mode: String, s: Settings): Int = when (mode) {
        Settings.ACCENT_MATERIAL_YOU -> wallpaperColor(theme().light)
        Settings.ACCENT_FIXED -> s.accentFixed?.let(ArtAccent::readable) ?: theme().accent
        else -> theme().accent
    }

    /** Material 3's primary from the wallpaper: tone 80 on dark themes, tone 40 on light ones. */
    private fun wallpaperColor(light: Boolean): Int =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            context.getColor(if (light) android.R.color.system_accent1_600 else android.R.color.system_accent1_200) and 0xFFFFFF
        } else theme().accent

    /** The picture now showing for the station (the notification's, the screens'). */
    fun artwork(uri: Uri?) {
        scope.launch {
            if (uri == null || uri == artUri) return@launch
            artUri = uri
            artLoad?.cancel()
            artLoad = launch {
                val picked = withContext(Dispatchers.Default) { pick(uri) } ?: return@launch
                if (uri != artUri) return@launch
                artColor = picked.color
                if (mode(library.settings.value) == Settings.ACCENT_ARTWORK) moveTo(picked.color ?: theme().accent, animate = true)
            }
        }
    }

    private class Picked(val color: Int?)

    /** shell's scaler: the whole decoded picture into theme.dominant_color; null if it couldn't be read. */
    private suspend fun pick(uri: Uri): Picked? {
        val request = ImageRequest.Builder(context).data(uri).size(Size.ORIGINAL).allowHardware(false).build()
        val result = SingletonImageLoader.get(context).execute(request) as? SuccessResult ?: return null
        val bitmap = result.image.toBitmap()
        val w = bitmap.width
        val h = bitmap.height
        val px = IntArray(w * h)
        bitmap.getPixels(px, 0, w, 0, 0, w, h)
        return Picked(ArtAccent.dominant(px, w, h))
    }

    /** AccentController.set_color: small changes ignored; a one-second eased fade through hue. */
    private fun moveTo(color: Int, animate: Boolean) {
        if (ArtAccent.distance(color, target) < ArtAccent.MIN_SHIFT && (animate || fade == null)) return
        target = color
        fade?.cancel()
        fade = null
        if (!animate) {
            _color.value = color
            return
        }
        val from = _color.value
        fade = scope.launch {
            for (frame in 1..ArtAccent.fadeFrames) {
                delay(ArtAccent.FADE_STEP_MS.toLong())
                _color.value = ArtAccent.fadeStep(from, color, frame)
            }
            fade = null
        }
    }

    companion object {
        val materialYou: Boolean get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
    }
}
