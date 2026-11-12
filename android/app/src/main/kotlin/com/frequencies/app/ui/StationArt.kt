package com.frequencies.app.ui

import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import coil3.compose.SubcomposeAsyncImage

/**
 * A station's picture, or its first letter on a tinted square when it has
 * none. [modifier] sets the size.
 *
 * Never a blank tile (Android batch 22, Radio Caprice): the letter shows
 * while the picture loads and when it can't be read, and a picture with
 * see-through parts sits on the tinted square, not on nothing.
 */
@Composable
fun StationArt(uri: Uri?, name: String, modifier: Modifier = Modifier, corner: Dp = 8.dp) {
    val shape = RoundedCornerShape(corner)
    Box(modifier.clip(shape).background(MaterialTheme.colorScheme.secondaryContainer)) {
        if (uri == null) {
            Letter(name)
        } else {
            SubcomposeAsyncImage(
                model = uri.toString(),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
                loading = { Letter(name) },
                error = { Letter(name) },
            )
        }
    }
}

@Composable
private fun Letter(name: String) {
    BoxWithConstraints(
        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.secondaryContainer),
        contentAlignment = Alignment.Center,
    ) {
        val size = with(LocalDensity.current) { (minOf(maxWidth, maxHeight) * 0.42f).toSp() }
        Text(
            name.trim().take(1).uppercase(),
            color = MaterialTheme.colorScheme.onSecondaryContainer,
            fontSize = size,
        )
    }
}
