package com.frequencies.app.ui

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.frequencies.app.R
import com.frequencies.core.Aurora
import com.frequencies.core.ComfortWash
import com.frequencies.core.ShellTheme
import com.frequencies.core.ThemeScheme

/**
 * The desktop's tokens Material 3 has no role for (overhaul spec section 2).
 * Material's roles still carry the rest: primary = accent, surface = bg0,
 * onSurface = text, onSurfaceVariant = text2.
 */
@Immutable
data class FreqColors(
    /** The frequency you are hearing (desktop "playing": never the accent, which art can turn red). */
    val playing: Color,
    /** A frequency that isn't answering. */
    val down: Color,
    /** The player bar's fill and its lit 1 dp top edge. */
    val barBg: Color,
    val barEdge: Color,
    /** Text, secondary text and the accent as they read on the player bar. */
    val onBar: Color,
    val onBar2: Color,
    val barAccent: Color,
    val line: Color,
    /** Canvas, panels (bottom bar, sidebar), raised (selected row, chips, combo boxes), tracks. */
    val bg0: Color,
    val bg1: Color,
    val bg2: Color,
    val bg3: Color,
    /** Every other Saved row. */
    val rowAlt: Color,
    val text: Color,
    val text2: Color,
    /** Faint: a frequency that isn't answering. */
    val text3: Color,
    /** Now Playing's Comfort View wash and the text that reads on it. */
    val wash: ComfortWash?,
    val light: Boolean,
    /** Now Playing's moving background (comfort spec 8): its colors and veil for this theme and accent. */
    val aurora: Aurora.Look? = null,
)

private val FALLBACK = FreqColors(
    playing = Color(0xFF4FC3A1), down = Color(0xFFE0574F), barBg = Color(0xFF2F2F38), barEdge = Color(0xFF464654),
    onBar = Color(0xFFECECEE), onBar2 = Color(0xFF9A9AA2), barAccent = Color(0xFFD99A2B), line = Color(0xFF2C2C31),
    bg0 = Color(0xFF131315), bg1 = Color(0xFF1A1A1D), bg2 = Color(0xFF242428), bg3 = Color(0xFF2B2B30),
    rowAlt = Color(0xFF1B1B1E), text = Color(0xFFECECEE), text2 = Color(0xFF9A9AA2), text3 = Color(0xFF6A6A72),
    wash = null, light = false,
)

val LocalFreqColors = staticCompositionLocalOf { FALLBACK }

/** Shorthand: MaterialTheme.colorScheme's sibling for the desktop's own tokens. */
val MaterialTheme.freq: FreqColors
    @Composable get() = LocalFreqColors.current

private fun c(rgb: Int) = Color(0xFF000000 or (rgb.toLong() and 0xFFFFFF))

fun ThemeScheme.toColorScheme(): ColorScheme {
    val base = if (light) lightColorScheme() else darkColorScheme()
    return base.copy(
        primary = c(primary), onPrimary = c(onPrimary), primaryContainer = c(primaryContainer),
        onPrimaryContainer = c(onPrimaryContainer), inversePrimary = c(inversePrimary),
        secondary = c(secondary), onSecondary = c(onSecondary), secondaryContainer = c(secondaryContainer),
        onSecondaryContainer = c(onSecondaryContainer),
        tertiary = c(tertiary), onTertiary = c(onTertiary), tertiaryContainer = c(tertiaryContainer),
        onTertiaryContainer = c(onTertiaryContainer),
        background = c(background), onBackground = c(onBackground),
        surface = c(surface), onSurface = c(onSurface), surfaceVariant = c(surfaceVariant), onSurfaceVariant = c(onSurfaceVariant),
        surfaceTint = c(surfaceTint), inverseSurface = c(inverseSurface), inverseOnSurface = c(inverseOnSurface),
        error = c(error), onError = c(onError), errorContainer = c(errorContainer), onErrorContainer = c(onErrorContainer),
        outline = c(outline), outlineVariant = c(outlineVariant), scrim = c(scrim),
        surfaceBright = c(surfaceBright), surfaceDim = c(surfaceDim),
        surfaceContainerLowest = c(surfaceContainerLowest), surfaceContainerLow = c(surfaceContainerLow),
        surfaceContainer = c(surfaceContainer), surfaceContainerHigh = c(surfaceContainerHigh),
        surfaceContainerHighest = c(surfaceContainerHighest),
    )
}

fun ThemeScheme.toFreqColors(theme: ShellTheme): FreqColors = FreqColors(
    playing = c(playing), down = c(down), barBg = c(barBg), barEdge = c(barEdge),
    onBar = c(onBar), onBar2 = c(onBar2), barAccent = c(barAccent), line = c(line),
    bg0 = c(surface), bg1 = c(surfaceContainer), bg2 = c(surfaceContainerHigh), bg3 = c(surfaceContainerHighest),
    rowAlt = c(surfaceContainerLow), text = c(onSurface), text2 = c(onSurfaceVariant), text3 = c(text3),
    wash = ComfortWash.of(theme, this), light = light,
).let { it.copy(aurora = it.wash?.let { w -> Aurora.look(this, w) }) }

/** Noto Sans (SIL Open Font License 1.1, assets/licenses/noto-sans-OFL.txt): the desktop's face, bundled. */
val NotoSans = FontFamily(
    Font(R.font.noto_sans_regular, FontWeight.Normal),
    Font(R.font.noto_sans_medium, FontWeight.Medium),
    Font(R.font.noto_sans_semibold, FontWeight.SemiBold),
    // Bold asks get SemiBold, the heaviest bundled weight (no Bold file: nothing needs it).
    Font(R.font.noto_sans_semibold, FontWeight.Bold),
)

/** The spec's type scale (section 3); every style in Noto Sans so dialogs and Material parts use it too. */
val FreqTypography: Typography = Typography().let { m ->
    fun TextStyle.noto() = copy(fontFamily = NotoSans)
    Typography(
        displayLarge = m.displayLarge.noto(), displayMedium = m.displayMedium.noto(), displaySmall = m.displaySmall.noto(),
        headlineLarge = m.headlineLarge.noto(), headlineMedium = m.headlineMedium.noto(),
        headlineSmall = m.headlineSmall.noto().copy(fontSize = 22.sp, lineHeight = 28.sp, fontWeight = FontWeight.SemiBold),
        titleLarge = m.titleLarge.noto().copy(fontSize = 20.sp, lineHeight = 26.sp, fontWeight = FontWeight.SemiBold),
        titleMedium = m.titleMedium.noto().copy(fontSize = 15.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.sp),
        titleSmall = m.titleSmall.noto().copy(fontSize = 14.sp, lineHeight = 19.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.sp),
        bodyLarge = m.bodyLarge.noto().copy(fontSize = 16.sp, lineHeight = 22.sp, letterSpacing = 0.sp),
        bodyMedium = m.bodyMedium.noto().copy(fontSize = 15.sp, lineHeight = 21.sp, fontWeight = FontWeight.Normal, letterSpacing = 0.sp),
        bodySmall = m.bodySmall.noto().copy(fontSize = 13.sp, lineHeight = 18.sp, fontWeight = FontWeight.Normal, letterSpacing = 0.sp),
        labelLarge = m.labelLarge.noto().copy(fontSize = 14.sp, lineHeight = 19.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.sp),
        labelMedium = m.labelMedium.noto().copy(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.08.em),
        labelSmall = m.labelSmall.noto().copy(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Normal, letterSpacing = 0.sp),
    )
}

/** Small corners: the desktop's 6–8 dp; rows stay flat (they draw no shape). */
val FreqShapes = Shapes(
    extraSmall = RoundedCornerShape(4.dp),
    small = RoundedCornerShape(6.dp),
    medium = RoundedCornerShape(8.dp),
    large = RoundedCornerShape(12.dp),
    extraLarge = RoundedCornerShape(16.dp),
)

/**
 * One of the desktop's shell themes as Material 3, with [accent] as its
 * primary (the Accent color setting's answer, fading with the artwork).
 */
@Composable
fun FrequenciesTheme(theme: ShellTheme, accent: Int, content: @Composable () -> Unit) {
    val scheme = remember(theme.key, accent) { ThemeScheme.of(theme, accent) }
    val freq = remember(scheme) { scheme.toFreqColors(theme) }
    CompositionLocalProvider(LocalFreqColors provides freq) {
        MaterialTheme(
            colorScheme = remember(scheme) { scheme.toColorScheme() },
            typography = FreqTypography,
            shapes = FreqShapes,
        ) { ProvideCalm(content) }
    }
}
