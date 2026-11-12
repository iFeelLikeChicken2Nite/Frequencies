package com.frequencies.app.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.Icon
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.layout.onPlaced
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import com.frequencies.core.GlyphMotion
import com.frequencies.core.LiveBars

/*
 * The desktop's chrome on a phone (overhaul spec sections 4–5): the flat
 * top bar, chips, text tabs, combo boxes, group labels, switches and
 * sliders in the theme's tokens, the playing row's bars and the Comfort
 * View wash.
 */

/** A screen's 56 dp top bar: title on the left (titleLarge), icons in text2 on the right. */
@Composable
fun FreqTopBar(
    title: String,
    modifier: Modifier = Modifier,
    navigation: (@Composable () -> Unit)? = null,
    color: Color = MaterialTheme.freq.bg0,
    actions: @Composable RowScope.() -> Unit = {},
) {
    Row(
        modifier
            .fillMaxWidth()
            .background(color)
            .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal))
            .height(56.dp)
            .padding(start = if (navigation == null) 16.dp else 4.dp, end = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (navigation != null) {
            CompositionLocalProvider(LocalContentColor provides MaterialTheme.freq.text2) { navigation() }
            Spacer(Modifier.width(4.dp))
        }
        Text(
            title,
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.freq.text,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f).semantics { heading() },
        )
        CompositionLocalProvider(LocalContentColor provides MaterialTheme.freq.text2) {
            Row(verticalAlignment = Alignment.CenterVertically, content = actions)
        }
    }
}

/** An icon button drawing one of the desktop's glyphs. */
@Composable
fun GlyphButton(
    name: String,
    contentDescription: String?,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    tint: Color = LocalContentColor.current,
    size: Dp = 24.dp,
    t: Float = 0f,
) {
    IconButton(onClick = onClick, modifier = modifier, enabled = enabled) {
        Glyph(name, contentDescription, t = t, tint = if (enabled) tint else tint.copy(alpha = 0.38f), size = size)
    }
}

/** A settings group's label in small caps ("LOOK"), as on the desktop's pages. */
@Composable
fun GroupLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(),
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.freq.text2,
        modifier = modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 6.dp).semantics { heading() },
    )
}

/**
 * A desktop chip: 6 dp corners in bg2; the chosen one with a 1 dp accent
 * outline and text color. [struck] draws the desktop TagPill's
 * strike-through across it (180 ms, out-cubic) for an excluded tag.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun FreqChip(
    text: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onLongClick: (() -> Unit)? = null,
    onClickLabel: String? = null,
    onLongClickLabel: String? = null,
    struck: Boolean = false,
    dot: Color? = null,
    trailing: (@Composable () -> Unit)? = null,
) {
    val f = MaterialTheme.freq
    val accent = MaterialTheme.colorScheme.primary
    val calm = LocalCalm.current
    val strike by animateFloatAsState(
        if (struck) 1f else 0f,
        if (calm) tween(0) else tween(GlyphMotion.STRIKE_MS, easing = { GlyphMotion.outCubic(it) }),
        label = "strike",
    )
    val shape = RoundedCornerShape(6.dp)
    val chosen = selected
    Box(
        modifier
            .minimumInteractiveComponentSize()
            .clip(shape)
            .combinedClickable(
                onClick = onClick,
                onLongClick = onLongClick,
                onClickLabel = onClickLabel,
                onLongClickLabel = onLongClickLabel,
                role = Role.Button,
            )
            .semantics { this.selected = chosen },
        contentAlignment = Alignment.Center,
    ) {
        Row(
            Modifier
                .heightIn(min = 30.dp)
                .background(f.bg2, shape)
                .then(if (selected) Modifier.border(BorderStroke(1.dp, accent), shape) else Modifier)
                .drawBehind {
                    if (strike > 0.01f) {
                        val y = size.height / 2
                        val left = 8.dp.toPx()
                        drawLine(f.text2, Offset(left, y), Offset(left + (size.width - 16.dp.toPx()) * strike, y), strokeWidth = 1.4.dp.toPx())
                    }
                }
                .padding(horizontal = 10.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (dot != null) {
                Box(Modifier.size(8.dp).background(dot, CircleShape))
                Spacer(Modifier.width(6.dp))
            }
            Text(
                text,
                style = MaterialTheme.typography.bodySmall,
                color = if (selected) f.text else f.text2,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 220.dp),
            )
            if (trailing != null) {
                Spacer(Modifier.width(4.dp))
                trailing()
            }
        }
    }
}

/**
 * Text tabs with the desktop's 2 dp accent underline; scrolls sideways
 * when they don't fit. TalkBack reads each as a tab. The chosen tab is
 * scrolled into view.
 *
 * [edge] (Settings, user 2026-11-05: "make it more obvious that there are
 * more tabs along the top and that you need to swipe"): the screen's
 * background color. When tabs are hidden off one side, that side fades
 * into it and shows a round arrow button (a tap scrolls that way), and the
 * first time the row shows in a run of the app it slides a little to the
 * left and back, like a swipe (not with Remove animations on).
 */
@Composable
fun TextTabs(
    tabs: List<String>,
    selected: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
    padding: Dp = 16.dp,
    gap: Dp = 22.dp,
    minHeight: Dp = 48.dp,
    textSize: androidx.compose.ui.unit.TextUnit = androidx.compose.ui.unit.TextUnit.Unspecified,
    divider: Boolean = true,
    color: Color? = null,
    color2: Color? = null,
    edge: Color? = null,
) {
    val f = MaterialTheme.freq
    val accent = MaterialTheme.colorScheme.primary
    val scroll = rememberScrollState()
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    val calm = LocalCalm.current
    // Each tab's left edge and width in the row (px), for scrolling the chosen one into view.
    val starts = remember(tabs.size) { IntArray(tabs.size) }
    val widths = remember(tabs.size) { IntArray(tabs.size) }
    var viewport by remember { mutableIntStateOf(0) }
    LaunchedEffect(selected, viewport, scroll.maxValue) {
        if (viewport == 0 || selected !in tabs.indices || widths[selected] == 0) return@LaunchedEffect
        val margin = with(density) { 56.dp.roundToPx() }
        val left = starts[selected] - margin
        val right = starts[selected] + widths[selected] + margin - viewport
        val target = when {
            left < scroll.value -> left
            right > scroll.value -> right
            else -> return@LaunchedEffect
        }.coerceIn(0, scroll.maxValue)
        if (calm) scroll.scrollTo(target) else scroll.animateScrollTo(target)
    }
    // The swipe hint: once per run of the app, only when some tabs are out of sight.
    if (edge != null) {
        LaunchedEffect(scroll.maxValue > 0) {
            if (scroll.maxValue == 0 || calm || tabsNudged) return@LaunchedEffect
            tabsNudged = true
            delay(600)
            val from = scroll.value
            val to = (from + with(density) { 72.dp.roundToPx() }).coerceAtMost(scroll.maxValue)
            if (to == from) return@LaunchedEffect
            scroll.animateScrollTo(to, tween(450))
            delay(150)
            scroll.animateScrollTo(from, tween(450))
        }
    }
    val canLeft = edge != null && scroll.value > 0
    val canRight = edge != null && scroll.value < scroll.maxValue
    Column(modifier.fillMaxWidth()) {
        Box(Modifier.fillMaxWidth()) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .onSizeChanged { viewport = it.width }
                    .drawWithContent {
                        drawContent()
                        if (edge == null) return@drawWithContent
                        val w = minOf(64.dp.toPx(), size.width / 4)
                        if (canRight) drawRect(
                            Brush.horizontalGradient(listOf(edge.copy(alpha = 0f), edge, edge), startX = size.width - w * 1.6f, endX = size.width),
                            topLeft = Offset(size.width - w * 1.6f, 0f),
                            size = Size(w * 1.6f, size.height),
                        )
                        if (canLeft) drawRect(
                            Brush.horizontalGradient(listOf(edge, edge, edge.copy(alpha = 0f)), startX = 0f, endX = w * 1.6f),
                            topLeft = Offset.Zero,
                            size = Size(w * 1.6f, size.height),
                        )
                    }
                    .horizontalScroll(scroll)
                    .selectableGroup()
                    .padding(horizontal = padding),
                horizontalArrangement = Arrangement.spacedBy(gap),
            ) {
                tabs.forEachIndexed { i, label ->
                    val on = i == selected
                    Box(
                        Modifier
                            .heightIn(min = minHeight)
                            .onPlaced { if (i < starts.size) { starts[i] = it.positionInParent().x.toInt(); widths[i] = it.size.width } }
                            .selectable(selected = on, onClick = { onSelect(i) }, role = Role.Tab)
                            .drawBehind {
                                if (on) {
                                    val h = 2.dp.toPx()
                                    drawRect(accent, Offset(0f, size.height - h), Size(size.width, h))
                                }
                            }
                            .padding(top = if (minHeight < 48.dp) 4.dp else 8.dp, bottom = if (minHeight < 48.dp) 6.dp else 10.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            label,
                            style = MaterialTheme.typography.labelLarge,
                            fontSize = textSize,
                            fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal,
                            color = if (on) (color ?: f.text) else (color2 ?: f.text2),
                            maxLines = 1,
                        )
                    }
                }
            }
            if (canRight) TabsArrow(
                Icons.AutoMirrored.Filled.KeyboardArrowRight, "More tabs",
                Modifier.align(Alignment.CenterEnd),
            ) { scope.launch { scroll.animateScrollTo((scroll.value + viewport * 2 / 3).coerceAtMost(scroll.maxValue)) } }
            if (canLeft) TabsArrow(
                Icons.AutoMirrored.Filled.KeyboardArrowLeft, "Earlier tabs",
                Modifier.align(Alignment.CenterStart),
            ) { scope.launch { scroll.animateScrollTo((scroll.value - viewport * 2 / 3).coerceAtLeast(0)) } }
        }
        if (divider) HorizontalDivider(thickness = 1.dp, color = f.line)
    }
}

/** Set once the tabs' swipe hint has played in this run of the app. */
private var tabsNudged = false

/** The round arrow at a tab row's edge: a 28 dp disc in a 48 dp touch target. */
@Composable
private fun TabsArrow(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, modifier: Modifier, onClick: () -> Unit) {
    val f = MaterialTheme.freq
    Box(
        modifier
            .size(48.dp)
            .clickable(onClickLabel = label, role = Role.Button, onClick = onClick)
            .semantics { contentDescription = label },
        contentAlignment = Alignment.Center,
    ) {
        Box(
            Modifier.size(28.dp).background(f.bg2, CircleShape).border(1.dp, f.line, CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, contentDescription = null, tint = f.text, modifier = Modifier.size(22.dp))
        }
    }
}

/** The desktop's combo box: current value and a chevron, bg2 with a 1 dp line border; tapping opens the choices. */
@Composable
fun ComboBox(value: String, onClick: () -> Unit, modifier: Modifier = Modifier, contentDescription: String? = null, leading: (@Composable () -> Unit)? = null) {
    val f = MaterialTheme.freq
    val shape = RoundedCornerShape(6.dp)
    Row(
        modifier
            .heightIn(min = 40.dp)
            .background(f.bg2, shape)
            .border(1.dp, f.line, shape)
            .clickable(onClick = onClick, onClickLabel = contentDescription, role = Role.DropdownList)
            .padding(start = 12.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (leading != null) {
            leading()
            Spacer(Modifier.width(8.dp))
        }
        Text(value, style = MaterialTheme.typography.bodyMedium.copy(fontSize = MaterialTheme.typography.labelLarge.fontSize), color = f.text, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
        Spacer(Modifier.width(8.dp))
        Glyph("collapse", null, tint = f.text2, size = 16.dp)
    }
}

/** Switches in the tokens: off track bg3, on track accent, thumb onPrimary (Material behavior kept). */
@Composable
fun FreqSwitch(checked: Boolean, onCheckedChange: ((Boolean) -> Unit)?, modifier: Modifier = Modifier, enabled: Boolean = true) {
    val f = MaterialTheme.freq
    val cs = MaterialTheme.colorScheme
    Switch(
        checked = checked,
        onCheckedChange = onCheckedChange,
        modifier = modifier,
        enabled = enabled,
        colors = SwitchDefaults.colors(
            checkedThumbColor = cs.onPrimary,
            checkedTrackColor = cs.primary,
            checkedBorderColor = cs.primary,
            uncheckedThumbColor = f.text2,
            uncheckedTrackColor = f.bg3,
            uncheckedBorderColor = f.bg3,
        ),
    )
}

/** Sliders in the tokens: a 4 dp track in bg3, accent fill, a 14 dp thumb in the text color. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FreqSlider(
    value: Float,
    onValueChange: (Float) -> Unit,
    modifier: Modifier = Modifier,
    valueRange: ClosedFloatingPointRange<Float> = 0f..1f,
    steps: Int = 0,
    onValueChangeFinished: (() -> Unit)? = null,
    enabled: Boolean = true,
) {
    val f = MaterialTheme.freq
    // Switched off (a setting that isn't on): the fill goes to the line color.
    val accent = if (enabled) MaterialTheme.colorScheme.primary else f.line
    Slider(
        value = value,
        onValueChange = onValueChange,
        modifier = modifier,
        enabled = enabled,
        valueRange = valueRange,
        steps = steps,
        onValueChangeFinished = onValueChangeFinished,
        colors = SliderDefaults.colors(thumbColor = f.text, activeTrackColor = accent, inactiveTrackColor = f.bg3),
        thumb = { Box(Modifier.size(14.dp).background(f.text, CircleShape)) },
        track = { state ->
            Canvas(Modifier.fillMaxWidth().height(4.dp)) {
                val span = state.valueRange.endInclusive - state.valueRange.start
                val frac = if (span > 0f) ((state.value - state.valueRange.start) / span).coerceIn(0f, 1f) else 0f
                val r = CornerRadius(size.height / 2)
                drawRoundRect(f.bg3, cornerRadius = r)
                drawRoundRect(accent, size = Size(size.width * frac, size.height), cornerRadius = r)
            }
        },
    )
}

/**
 * The playing row's three bars (desktop stationlist._draw_live): moving
 * at 30 fps while [moving], only while this row is on screen and the app
 * is in front; still bars when paused, in reduced motion or battery saver.
 */
@Composable
fun LiveBarsView(moving: Boolean, color: Color, modifier: Modifier = Modifier) {
    val calm = LocalCalm.current
    var seconds by remember { mutableDoubleStateOf(0.0) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val run = moving && !calm
    if (run) {
        LaunchedEffect(lifecycle) {
            lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
                val frame = 1_000_000_000L / LiveBars.FPS
                var start = -1L
                var last = 0L
                while (true) {
                    androidx.compose.runtime.withFrameNanos { now ->
                        // The bars' clock runs GLYPH_PACE slower than the wall clock (build 170).
                        if (start < 0) start = now - (seconds * 1e9 * GLYPH_PACE).toLong()
                        if (now - last >= frame) {
                            last = now
                            seconds = (now - start) / 1e9 / GLYPH_PACE
                        }
                    }
                }
            }
        }
    }
    val total = LiveBars.BAR * 3 + LiveBars.GAP * 2
    Canvas(modifier.size(width = total.dp, height = 18.dp)) {
        val heights = LiveBars.heights(seconds, run)
        val w = LiveBars.BAR.dp.toPx(); val gap = LiveBars.GAP.dp.toPx()
        val cy = size.height / 2
        heights.forEachIndexed { i, h ->
            val hp = h.dp.toPx()
            drawRoundRect(color, Offset(i * (w + gap), cy - hp / 2), Size(w, hp), CornerRadius(1.5.dp.toPx()))
        }
    }
}

/**
 * Now Playing's still background: the Comfort View wash (spec 5.4). The
 * accent glows from the top left (120% × 70% ellipse, gone by 60%) over
 * the canvas tinted toward the playing color at the top. A drawn gradient
 * (no blur, no shader). Since build 163 the moving background
 * ([ComfortBackground]) draws instead; this is what "Moving background:
 * Off" shows.
 */
@Composable
fun StillWash(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val f = MaterialTheme.freq
    val wash = f.wash
    val glow = wash?.let { Color(0xFF000000 or it.glow.toLong()) } ?: f.bg0
    val top = wash?.let { Color(0xFF000000 or it.top.toLong()) } ?: f.bg0
    Box(
        modifier.drawBehind {
            drawRect(Brush.verticalGradient(listOf(top, f.bg0)))
            val center = Offset(size.width * 0.2f, 0f)
            val ry = size.height * 0.7f
            val rx = size.width * 1.2f
            // A circle of radius ry, stretched sideways into the ellipse.
            scale(scaleX = rx / ry, scaleY = 1f, pivot = center) {
                drawCircle(
                    Brush.radialGradient(0f to glow, 0.6f to glow.copy(alpha = 0f), center = center, radius = ry),
                    radius = ry,
                    center = center,
                )
            }
        },
    ) { content() }
}

/** Every other row in row_alt, the current one in bg2 (Saved, History, Add, Browse). */
@Composable
fun stripe(index: Int, current: Boolean = false): Color {
    val f = MaterialTheme.freq
    return when {
        current -> f.bg2
        index % 2 == 1 -> f.rowAlt
        else -> f.bg0
    }
}

/**
 * A list row in the desktop's look (spec 5.3/5.6): flat, at least 58 dp,
 * 16 dp sides, 12 dp gaps, in stripes ([index]), bg2 when [current].
 * [leading] is usually 40 dp art with 6 dp corners.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun FreqRow(
    index: Int,
    title: String,
    subtitle: String?,
    modifier: Modifier = Modifier,
    current: Boolean = false,
    subtitleColor: Color = MaterialTheme.freq.text2,
    subtitleLines: Int = 1,
    onClick: (() -> Unit)? = null,
    onClickLabel: String? = null,
    onLongClick: (() -> Unit)? = null,
    onLongClickLabel: String? = null,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable RowScope.() -> Unit)? = null,
    /** Drawn in place of the title text (a tag's pill); [title] still names the row. */
    titleContent: (@Composable () -> Unit)? = null,
) {
    val f = MaterialTheme.freq
    Row(
        modifier
            .fillMaxWidth()
            .background(stripe(index, current))
            .then(
                if (onClick != null) Modifier.combinedClickable(onClick = onClick, onClickLabel = onClickLabel, onLongClick = onLongClick, onLongClickLabel = onLongClickLabel)
                else Modifier,
            )
            .heightIn(min = 58.dp)
            .padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        leading?.invoke()
        Column(Modifier.weight(1f)) {
            if (titleContent != null) titleContent()
            else Text(title, style = MaterialTheme.typography.titleMedium, color = f.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (!subtitle.isNullOrEmpty()) {
                Text(subtitle, style = MaterialTheme.typography.bodySmall, color = subtitleColor, maxLines = subtitleLines, overflow = TextOverflow.Ellipsis)
            }
        }
        if (trailing != null) Row(verticalAlignment = Alignment.CenterVertically, content = trailing)
    }
}
