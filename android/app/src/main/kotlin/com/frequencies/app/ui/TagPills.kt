package com.frequencies.app.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.frequencies.core.GlyphMotion
import com.frequencies.core.TagLook

/** Saved Frequencies' tag filter pills, half again the size of a row's (user, build 170: "make tag pills bigger"). */
const val FILTER_PILL_SCALE = 1.5f

/** A tag color at [alpha] (0-255), the desktop's theme.tint_color. */
fun tagTint(hex: String, alpha: Int): Color = Color(TagLook.argb(hex, alpha))

/**
 * One tag as the desktop paints it (core [TagLook]): a round pill in the
 * tag's own color, tinted, the text in that color (or the theme's text
 * when it is chosen). [onClick] makes it a button with a full-size touch
 * target around the small pill; without one it is only a picture.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun TagPill(
    text: String,
    hex: String,
    style: TagLook.Style,
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    onLongClick: (() -> Unit)? = null,
    onClickLabel: String? = null,
    onLongClickLabel: String? = null,
    chosen: Boolean? = null,
    maxWidth: Dp = 220.dp,
    /** Grows the whole pill (Saved Frequencies' tag filter: [FILTER_PILL_SCALE]). */
    scale: Float = 1f,
) {
    val f = MaterialTheme.freq
    val press = remember { MutableInteractionSource() }
    val pressed by press.collectIsPressedAsState()
    val calm = LocalCalm.current
    val strike by animateFloatAsState(
        if (style.struck) 1f else 0f,
        if (calm) tween(0) else tween(GlyphMotion.STRIKE_MS, easing = { GlyphMotion.outCubic(it) }),
        label = "strike",
    )
    val shape = RoundedCornerShape((style.radiusPx * scale).dp)
    val textColor = when (style.text) {
        TagLook.Text.TAG -> tagTint(hex, 255)
        TagLook.Text.THEME -> f.text
        TagLook.Text.FAINT -> f.text3
        TagLook.Text.DARK -> Color(TagLook.DARK)
    }
    val fill = tagTint(hex, if (pressed && onClick != null) TagLook.pressedAlpha(style) else style.fillAlpha)
    val pill = Modifier
        .heightIn(min = (style.heightPx * scale).dp)
        .clip(shape)
        .background(fill, shape)
        .then(if (style.borderAlpha > 0) Modifier.border(1.dp, tagTint(hex, style.borderAlpha), shape) else Modifier)
        .drawWithContent {
            drawContent()
            // Drawn, not a font flag, so the line can grow (the desktop's TagPill).
            if (strike > 0.01f) {
                val y = size.height / 2
                val left = (style.padPx * scale).dp.toPx()
                drawLine(textColor, Offset(left, y), Offset(left + (size.width - 2 * left) * strike, y), strokeWidth = 1.dp.toPx())
            }
        }
        .padding(horizontal = (style.padPx * scale).dp, vertical = (((style.heightPx - style.fontPx - 4).coerceAtLeast(0) / 2) * scale).dp)
    val label: @Composable () -> Unit = {
        Box(pill, contentAlignment = Alignment.Center) {
            Text(
                text,
                style = TextStyle(fontSize = (style.fontPx * scale).sp, lineHeight = ((style.fontPx + 4) * scale).sp, fontWeight = if (style.bold) FontWeight.Bold else null),
                color = textColor,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = maxWidth),
            )
        }
    }
    if (onClick == null) {
        Box(modifier) { label() }
        return
    }
    Box(
        modifier
            .minimumInteractiveComponentSize()
            .combinedClickable(
                interactionSource = press,
                indication = null,
                onClick = onClick,
                onLongClick = onLongClick,
                onClickLabel = onClickLabel,
                onLongClickLabel = onLongClickLabel,
                role = Role.Button,
            )
            .then(if (chosen != null) Modifier.semantics { selected = chosen } else Modifier),
        contentAlignment = Alignment.Center,
    ) { label() }
}

/** A Saved row's tags: at most two pills, as the desktop's rows show them. */
@Composable
fun RowTagPills(tags: List<String>, colorOf: (String) -> String, modifier: Modifier = Modifier) {
    if (tags.isEmpty()) return
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(TagLook.ROW_GAP_PX.dp), verticalAlignment = Alignment.CenterVertically) {
        tags.take(TagLook.ROW_MAX).forEach { tag -> TagPill(tag, colorOf(tag), TagLook.ROW, maxWidth = 96.dp) }
    }
}
