package com.frequencies.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CellTower
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconToggleButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil3.compose.SubcomposeAsyncImage
import coil3.toBitmap
import androidx.compose.ui.platform.testTag
import com.frequencies.app.artists
import com.frequencies.app.playback.ArtistNow
import com.frequencies.core.ArtistPanel
import com.frequencies.core.ThumbsSnapshot
import java.io.File
import android.graphics.BitmapFactory
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.produceState
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import com.frequencies.app.library
import com.frequencies.core.BandLogos
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * The Artist tab (the desktop's artist panel): the photo, the name and
 * genres, the biography, whose words and picture they are, and "Is this
 * right?" with the thumbs. A talk station shows its own info instead.
 */
@Composable
fun ArtistPane(
    stationId: String?,
    modifier: Modifier = Modifier,
    header: Boolean = true,
    column: Boolean = false,
    /** The page's own height in px (not the space it is given), for this artist's [ArtistPanel.key]: Now Playing keeps a short bio's photo big. */
    onContentHeight: ((String, Int) -> Unit)? = null,
) {
    val state by ArtistNow.state.collectAsStateWithLifecycle()
    val panel = state.panel?.takeIf { state.stationId == stationId }
    if (panel == null) {
        Box(modifier.padding(24.dp), contentAlignment = Alignment.Center) {
            Text(
                "The artist shows up here once a song is named.",
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        return
    }
    Column(
        modifier.verticalScroll(rememberScrollState())
            // Build 201: measured with no minimum height. Under fillMaxSize the scrolling column was always at least
            // as tall as its space, so a two-line bio "filled" the screen and the photo never came out big (task 243).
            .then(if (onContentHeight != null) Modifier.wrapContentHeight(Alignment.Top).onSizeChanged { onContentHeight(panel.key, it.height) } else Modifier)
            .padding(vertical = 8.dp),
    ) {
        // Comfort Now Playing shows the photo and the name above the body itself ([header] false).
        // [column]: the wide Comfort View's artist column, as the desktop's: the logo (or name) on top, the photo last.
        if (column) {
            ArtistName(panel.name, column = true)
        } else if (header) {
            Photo(panel)
            Spacer(Modifier.height(16.dp))
            ArtistName(panel.name)
        }
        if (panel.genres.isNotEmpty()) {
            Text(
                panel.genres,
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        if (state.busy) {
            LinearProgressIndicator(Modifier.padding(top = 16.dp).width(64.dp))
        }
        if (panel.bio.isNotEmpty()) {
            Text(panel.bio, style = MaterialTheme.typography.bodySmall.copy(lineHeight = 20.sp), modifier = Modifier.padding(top = 12.dp))
        }
        Credits(panel)
        if (panel.canJudge && !state.busy) Verdict(panel)
        if (column) {
            Spacer(Modifier.height(16.dp))
            Photo(panel)
        }
        Spacer(Modifier.height(16.dp))
    }
}

/**
 * The artist's name, or the band's logo in its place (desktop build 141,
 * bandlogo.py): from the logo pack, placed as the developer set it, faded
 * in with a short slide, never recolored, and outlined only when it would
 * be lost against the panel.
 */
@Composable
fun ArtistName(
    name: String,
    boxDp: Float = BandLogos.BOX.toFloat(),
    style: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.headlineSmall,
    color: Color = Color.Unspecified,
    maxLines: Int = Int.MAX_VALUE,
    /** The wide Comfort View's artist column: bandlogo.LogoName (COMFORT_BOX, scale and left-right only). */
    column: Boolean = false,
) {
    val panel = MaterialTheme.colorScheme.surface
    val behind = BandLogos.luminance((panel.red * 255).toInt(), (panel.green * 255).toInt(), (panel.blue * 255).toInt())
    val shown = rememberShownLogo(name, behind)
    if (shown == null) {
        Text(name, style = style, color = color, maxLines = maxLines, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis, modifier = Modifier.semantics { heading() })
        return
    }
    val appear = remember(shown) { Animatable(0f) }
    LaunchedEffect(shown) { appear.animateTo(1f, tween(BandLogos.FADE_MS)) }
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val density = LocalDensity.current
        val width = with(density) { maxWidth.toPx() }.toDouble()
        val boxHeight = with(density) { boxDp.dp.toPx() }.toDouble()
        val imageW = shown.image.width.toDouble()
        val imageH = shown.image.height.toDouble()
        val place = if (column) {
            val unit = with(density) { 1.dp.toPx() }.toDouble()
            BandLogos.inColumn(width / unit, imageW, imageH, shown.logo.scale, shown.logo.x)
                ?.let { BandLogos.Box(it.left * unit, it.top * unit, it.width * unit, it.height * unit) }
        } else {
            BandLogos.place(
                BandLogos.Box(0.0, 0.0, width, boxHeight),
                imageW, imageH,
                shown.logo.scale, shown.logo.x, shown.logo.y,
                bounds = BandLogos.Box(0.0, 0.0, width, boxHeight * 2),
            )
        }
        if (place == null) {
            Text(name, style = style, color = color, maxLines = maxLines, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis, modifier = Modifier.semantics { heading() })
            return@BoxWithConstraints
        }
        val canvasHeight = with(density) { (if (column) place.bottom else maxOf(boxHeight, place.bottom)).toFloat().toDp() }
        Canvas(
            Modifier.fillMaxWidth().height(canvasHeight)
                .semantics { contentDescription = name; heading() }
                .graphicsLayer {
                    alpha = appear.value
                    translationY = (1f - appear.value) * BandLogos.SLIDE.dp.toPx()
                },
        ) { drawLogo(shown, place) }
    }
}

/** The pack's logo for [name], loaded off the main thread, with the outline it needs against [behind] (luminance 0-1). */
@Composable
private fun rememberShownLogo(name: String?, behind: Double): ShownLogo? {
    val context = LocalContext.current
    val kit = context.artists
    val changed by kit.logosChanged.collectAsStateWithLifecycle()
    val settings by context.library.settings.collectAsStateWithLifecycle()
    val on = settings.bool(BandLogos.SETTING, true)
    val logo by produceState<ShownLogo?>(null, name, changed, on, behind) {
        value = if (name.isNullOrEmpty()) null else withContext(Dispatchers.IO) { kit.logoFor(name)?.let { loadLogo(it, behind) } }
    }
    return logo
}

/** The logo at [place] (pixels), never recolored; a 1.5 dp outline only when it would be lost. */
private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawLogo(shown: ShownLogo, place: BandLogos.Box) {
    val dst = IntSize(place.width.toInt().coerceAtLeast(1), place.height.toInt().coerceAtLeast(1))
    val at = IntOffset(place.left.toInt(), place.top.toInt())
    val outline = when (shown.outline) {
        true -> Color.Black
        false -> Color.White
        null -> null
    }
    if (outline != null) {
        val r = 1.5.dp.toPx().toInt().coerceAtLeast(1)
        val tint = ColorFilter.tint(outline, BlendMode.SrcIn)
        for (dx in -1..1) for (dy in -1..1) {
            if (dx == 0 && dy == 0) continue
            drawImage(shown.image, dstOffset = IntOffset(at.x + dx * r, at.y + dy * r), dstSize = dst, colorFilter = tint, filterQuality = FilterQuality.High)
        }
    }
    drawImage(shown.image, dstOffset = at, dstSize = dst, filterQuality = FilterQuality.High)
}

/**
 * The band's logo on the artist's photo (comfort spec 6): the desktop's
 * spot (bandlogo.header_box: 12 from the left, bottom 8 above the photo's
 * edge, up to 96 tall) placed with the pack's scale and position, kept 4
 * inside the photo. It fades in sliding up (350 ms, 16 dp) once the photo
 * is showing ([photoShown]) and out sliding down when it goes. No logo,
 * or no photo: the name as plain white text in the same spot. [tone] is
 * the luminance of the photo's bottom band, which decides the outline.
 * [modifier] sizes it to the photo.
 */
@Composable
fun PhotoLogo(name: String?, photoShown: Boolean, tone: Double?, modifier: Modifier = Modifier) {
    if (name == null) return
    val shown = rememberShownLogo(name, tone ?: 0.0)
    val logoOn = shown != null && photoShown
    val appear = remember { Animatable(0f) }
    val calm = LocalCalm.current
    LaunchedEffect(logoOn) {
        if (calm) appear.snapTo(if (logoOn) 1f else 0f) else appear.animateTo(if (logoOn) 1f else 0f, tween(BandLogos.FADE_MS))
    }
    BoxWithConstraints(modifier) {
        val density = LocalDensity.current
        val unit = with(density) { 1.dp.toPx() }.toDouble()
        val w = with(density) { maxWidth.toPx() }.toDouble()
        val h = with(density) { maxHeight.toPx() }.toDouble()
        val place = shown?.let {
            BandLogos.onPhoto(w / unit, h / unit, it.image.width.toDouble(), it.image.height.toDouble(), it.logo.scale, it.logo.x, it.logo.y)
                ?.let { b -> BandLogos.Box(b.left * unit, b.top * unit, b.width * unit, b.height * unit) }
        }
        // The plain name, while the logo isn't up.
        Box(
            Modifier.fillMaxSize().padding(start = 12.dp, end = 12.dp, bottom = 8.dp).graphicsLayer { alpha = 1f - appear.value },
            contentAlignment = Alignment.BottomStart,
        ) {
            Text(
                name,
                color = Color.White,
                fontSize = 20.sp,
                fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold,
                maxLines = 2,
                overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                style = MaterialTheme.typography.titleLarge.copy(shadow = androidx.compose.ui.graphics.Shadow(Color.Black.copy(alpha = 0.6f), blurRadius = 8f)),
            )
        }
        if (shown != null && place != null) {
            Canvas(
                Modifier.fillMaxSize()
                    .testTag("photo_logo")
                    .semantics { contentDescription = name }
                    .graphicsLayer {
                        alpha = appear.value
                        translationY = (1f - appear.value) * BandLogos.SLIDE.dp.toPx()
                    },
            ) { drawLogo(shown, place) }
        }
    }
}

/** The luminance (0-1) of a photo's bottom band, where the logo sits, as the photo shows cropped to a square. */
fun bottomTone(bitmap: android.graphics.Bitmap): Double? = runCatching {
    val b = if (bitmap.config == android.graphics.Bitmap.Config.HARDWARE) bitmap.copy(android.graphics.Bitmap.Config.ARGB_8888, false) else bitmap
    val side = minOf(b.width, b.height)
    val x0 = (b.width - side) / 2
    val y0 = (b.height - side) / 2
    var sum = 0.0
    var n = 0
    for (j in 0 until 8) for (i in 0 until 24) {
        val x = x0 + (side * (i + 0.5) / 24).toInt()
        val y = y0 + (side * (0.66 + 0.34 * (j + 0.5) / 8)).toInt()
        val c = b.getPixel(x.coerceIn(0, b.width - 1), y.coerceIn(0, b.height - 1))
        sum += BandLogos.luminance(android.graphics.Color.red(c), android.graphics.Color.green(c), android.graphics.Color.blue(c))
        n++
    }
    sum / n
}.getOrNull()

/** A logo ready to draw, and the outline it needs (true: dark, false: light, null: none). */
private class ShownLogo(val logo: BandLogos.Logo, val image: ImageBitmap, val outline: Boolean?)

private fun loadLogo(logo: BandLogos.Logo, behind: Double): ShownLogo? {
    val bitmap = (if (logo.file.extension.equals("svg", ignoreCase = true)) svgBitmap(logo.file) else BitmapFactory.decodeFile(logo.file.path))
        ?: return null
    // bandlogo.outline_for: the luminance of the logo's solid pixels, sampled.
    val step = maxOf(1, kotlin.math.sqrt(bitmap.width.toDouble() * bitmap.height / 6000.0).toInt())
    val solid = ArrayList<Double>()
    var y = 0
    while (y < bitmap.height) {
        var x = 0
        while (x < bitmap.width) {
            val c = bitmap.getPixel(x, y)
            if (android.graphics.Color.alpha(c) >= 128) solid += BandLogos.luminance(android.graphics.Color.red(c), android.graphics.Color.green(c), android.graphics.Color.blue(c))
            x += step
        }
        y += step
    }
    return ShownLogo(logo, bitmap.asImageBitmap(), BandLogos.needsOutline(solid, behind))
}

/**
 * An SVG logo as a picture (build 170: 21 of the logo pack's logos are
 * SVG, Linkin Park's among them, and BitmapFactory reads none of them, so
 * they never showed). Drawn [SVG_LOGO_PX] wide (or tall, whichever is
 * longer), sharp at any size the logo is shown.
 */
private fun svgBitmap(file: java.io.File): android.graphics.Bitmap? = runCatching {
    val svg = file.inputStream().use { com.caverock.androidsvg.SVG.getFromInputStream(it) }
    val box = svg.documentViewBox
    val w0 = box?.width() ?: svg.documentWidth
    val h0 = box?.height() ?: svg.documentHeight
    if (w0 <= 0f || h0 <= 0f) return@runCatching null
    val scale = SVG_LOGO_PX / maxOf(w0, h0)
    val w = (w0 * scale).toInt().coerceAtLeast(1)
    val h = (h0 * scale).toInt().coerceAtLeast(1)
    svg.setDocumentWidth(w.toFloat())
    svg.setDocumentHeight(h.toFloat())
    if (box == null) svg.setDocumentViewBox(0f, 0f, w0, h0)
    val bitmap = android.graphics.Bitmap.createBitmap(w, h, android.graphics.Bitmap.Config.ARGB_8888)
    svg.renderToCanvas(android.graphics.Canvas(bitmap))
    bitmap
}.onFailure { android.util.Log.w("Frequencies", "[logos] ${file.name}: ${it.message}") }.getOrNull()

private const val SVG_LOGO_PX = 1200f

/**
 * The artist's photo filling [modifier] (Comfort Now Playing's Artist
 * page, in the cover's place), or [fallback] (the cover) while no artist
 * or photo is known.
 */
@Composable
fun ArtistPhoto(stationId: String?, modifier: Modifier = Modifier, onPhoto: (android.graphics.Bitmap?) -> Unit = {}, fallback: @Composable () -> Unit) {
    val state by ArtistNow.state.collectAsStateWithLifecycle()
    val panel = state.panel?.takeIf { state.stationId == stationId }
    val photo = panel?.photo
    // No photo (the cover shows): nothing for the logo to sit on.
    LaunchedEffect(photo, panel == null) { if (panel == null || photo == null) onPhoto(null) }
    Box(modifier) {
        if (panel == null || photo == null) fallback()
        else SubcomposeAsyncImage(
            model = if (photo.startsWith("http")) photo else File(photo),
            contentDescription = "Photo of ${panel.name}",
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
            error = { fallback() },
            onSuccess = { success -> onPhoto(runCatching { success.result.image.toBitmap() }.getOrNull()) },
            onError = { onPhoto(null) },
        )
    }
}

/** The artist the Artist tab is about, or null while none is known. */
@Composable
fun artistNameFor(stationId: String?): String? {
    val state by ArtistNow.state.collectAsStateWithLifecycle()
    return state.panel?.takeIf { state.stationId == stationId }?.name
}

@Composable
private fun Photo(panel: ArtistPanel) {
    val shape = RoundedCornerShape(8.dp)
    val frame = Modifier.fillMaxWidth().aspectRatio(16f / 10f).clip(shape)
    val empty: @Composable () -> Unit = {
        // The desktop's tower: something in the frame when there is no photo.
        Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.surfaceContainerHigh), contentAlignment = Alignment.Center) {
            Icon(Icons.Filled.CellTower, contentDescription = null, Modifier.size(56.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    val photo = panel.photo
    Box(frame) {
        if (photo == null) empty()
        else SubcomposeAsyncImage(
            model = if (photo.startsWith("http")) photo else File(photo),
            contentDescription = "Photo of ${panel.name}",
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
            error = { empty() },
        )
    }
}

/** "Biography from Wikipedia · photo from Deezer", each service's name its link. */
@Composable
private fun Credits(panel: ArtistPanel) {
    if (panel.credits.isEmpty()) return
    val link = TextLinkStyles(SpanStyle(color = MaterialTheme.colorScheme.primary, textDecoration = TextDecoration.Underline))
    val text = buildAnnotatedString {
        panel.credits.forEachIndexed { i, c ->
            if (i > 0) append(" · ")
            if (c.prefix.isNotEmpty()) { append(c.prefix); append(' ') }
            if (c.url.isNotEmpty()) withLink(LinkAnnotation.Url(c.url, link)) { append(c.name) } else append(c.name)
        }
    }
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 12.dp),
    )
}

/** "Is this right?" and the thumbs: one or the other, never both. Down asks what is wrong first. */
@Composable
private fun Verdict(panel: ArtistPanel) {
    val kit = LocalContext.current.artists
    val verdict by ArtistNow.verdict.collectAsStateWithLifecycle()
    Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("Is this right?", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        IconToggleButton(checked = verdict == "up", onCheckedChange = { kit.thumbsUp(it) }) {
            if (verdict == "up") Glyph("thumb_up_on", "Right", tint = GOOD)
            else Glyph("thumb_up", "Right")
        }
        IconToggleButton(checked = verdict == "down", onCheckedChange = { on -> if (on) kit.press(panel) else kit.thumbsUp(false) }) {
            if (verdict == "down") Glyph("thumb_down_on", "Wrong", tint = BAD)
            else Glyph("thumb_down", "Wrong")
        }
    }
}

/**
 * mainwindow._ask_what_wrong: Bio, Photo or Both, about the artist the
 * thumb was pressed on (the snapshot), whatever plays meanwhile. Shown
 * over every screen, so leaving Now Playing doesn't close it.
 */
@Composable
fun ThumbsQuestion(snap: ThumbsSnapshot) {
    val kit = LocalContext.current.artists
    AlertDialog(
        onDismissRequest = { kit.answer(snap, "") },
        title = { Text("What's wrong?") },
        text = { Text("What is wrong about “${snap.artist}”?") },
        confirmButton = {
            Row {
                for ((label, what) in listOf("Bio" to "bio", "Photo" to "photo", "Both" to "both")) {
                    TextButton(onClick = { kit.answer(snap, what) }) { Text(label) }
                }
            }
        },
        dismissButton = { TextButton(onClick = { kit.answer(snap, "") }) { Text("Cancel") } },
    )
}

/** sidepanel.ArtistView.GOOD / BAD */
private val GOOD = Color(0xFF4FC3A1)
private val BAD = Color(0xFFE0574F)
