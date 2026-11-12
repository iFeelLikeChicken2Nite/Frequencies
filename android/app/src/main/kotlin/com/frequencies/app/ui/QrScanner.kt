package com.frequencies.app.ui

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.os.Build
import android.provider.MediaStore
import android.util.Size
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.view.LifecycleCameraController
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.FlashlightOff
import androidx.compose.material.icons.filled.FlashlightOn
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import zxingcpp.BarcodeReader
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Reads a Frequencies QR code with the camera, or from a picture.
 *
 * zxing-cpp, the reader the desktop uses: a desktop code with art in it is
 * a version 30-40 symbol (a module is a few pixels in a camera frame), which
 * the Java ZXing reader in the old scanner only read from a perfect, close
 * picture. The camera is analyzed at about 1920 x 1440 over the whole frame,
 * not a small box in the middle; pinch to zoom and tap to focus come with
 * the camera controller.
 */
@Composable
fun QrScannerDialog(onCode: (String) -> Unit, onClose: () -> Unit) {
    val context = LocalContext.current
    val say = LocalSay.current
    val scope = rememberCoroutineScope()
    var allowed by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    val askCamera = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { allowed = it }
    LaunchedEffect(Unit) { if (!allowed) askCamera.launch(Manifest.permission.CAMERA) }

    val camera = remember { scannerCamera(context) }
    val done = remember { AtomicBoolean(false) }
    fun found(text: String) {
        if (done.compareAndSet(false, true)) {
            onCode(text)
            onClose()
        }
    }

    val pickPicture = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            val text = readPictureCode(context, uri)
            if (text != null) found(text) else say(NO_CODE_IN_PICTURE)
        }
    }

    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false)) {
        Box(Modifier.fillMaxSize().background(Color.Black)) {
            if (allowed) {
                CameraReader(camera, onText = ::found)
                // Where to aim; the whole frame is read, so this is only a guide.
                Box(
                    Modifier.align(Alignment.Center).fillMaxWidth(0.8f).aspectRatio(1f)
                        .border(2.dp, Color.White.copy(alpha = 0.7f), RoundedCornerShape(24.dp)),
                )
            } else {
                Text(
                    "Frequencies needs the camera to read QR codes. You can also choose a picture of a code.",
                    color = Color.White,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.align(Alignment.Center).padding(32.dp),
                )
            }
            Column(
                Modifier.align(Alignment.BottomCenter).safeDrawingPadding().padding(16.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Text(
                    "Point the camera at a Frequencies QR code. Big codes read best from a little farther back: pinch to zoom.",
                    color = Color.White,
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.background(Color.Black.copy(alpha = 0.5f), RoundedCornerShape(12.dp)).padding(12.dp),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    FilledTonalButton(onClick = { pickPicture.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) }) {
                        Icon(Icons.Outlined.Image, contentDescription = null)
                        Text("  Choose a picture")
                    }
                }
            }
            FilledTonalIconButton(onClick = onClose, modifier = Modifier.align(Alignment.TopStart).safeDrawingPadding().padding(8.dp)) {
                Icon(Icons.Filled.Close, contentDescription = "Close")
            }
            if (allowed) TorchButton(camera, Modifier.align(Alignment.TopEnd).safeDrawingPadding().padding(8.dp))
        }
    }
}

@Composable
private fun TorchButton(controller: LifecycleCameraController, modifier: Modifier) {
    var on by remember { mutableStateOf(false) }
    FilledTonalIconButton(onClick = { on = !on; controller.enableTorch(on) }, modifier = modifier) {
        Icon(if (on) Icons.Filled.FlashlightOn else Icons.Filled.FlashlightOff, contentDescription = if (on) "Light off" else "Light on")
    }
}

private fun scannerCamera(context: android.content.Context) =
    LifecycleCameraController(context).apply {
        setEnabledUseCases(LifecycleCameraController.IMAGE_ANALYSIS)
        imageAnalysisBackpressureStrategy = ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST
        imageAnalysisResolutionSelector = ResolutionSelector.Builder()
            .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
            .setResolutionStrategy(
                ResolutionStrategy(Size(1920, 1440), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER),
            )
            .build()
    }

@Composable
private fun CameraReader(controller: LifecycleCameraController, onText: (String) -> Unit) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val worker = remember { Executors.newSingleThreadExecutor() }
    DisposableEffect(controller) {
        val reader = BarcodeReader().apply {
            options.formats = setOf(BarcodeReader.Format.QR_CODE)
            options.tryHarder = true
            options.tryRotate = true
            options.tryInvert = true
        }
        controller.setImageAnalysisAnalyzer(worker) { image ->
            image.use {
                reader.read(it).firstOrNull { r -> !r.text.isNullOrEmpty() }?.text?.let { text ->
                    ContextCompat.getMainExecutor(context).execute { onText(text) }
                }
            }
        }
        controller.bindToLifecycle(owner)
        onDispose {
            controller.clearImageAnalysisAnalyzer()
            controller.unbind()
            worker.shutdown()
        }
    }
    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { PreviewView(it).apply { scaleType = PreviewView.ScaleType.FILL_CENTER; this.controller = controller } },
    )
}

const val NO_CODE_IN_PICTURE = "No QR code found in that picture."

/** The QR code in a picture the user chose (the scanner's "Choose a picture" and the Add screen's Import QR code tile), or null. */
suspend fun readPictureCode(context: android.content.Context, uri: android.net.Uri): String? = withContext(Dispatchers.Default) {
    runCatching {
        val bitmap = if (Build.VERSION.SDK_INT >= 28) {
            ImageDecoder.decodeBitmap(ImageDecoder.createSource(context.contentResolver, uri)) { decoder, _, _ ->
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            }
        } else {
            @Suppress("DEPRECATION")
            MediaStore.Images.Media.getBitmap(context.contentResolver, uri)
        }
        readPicture(bitmap)
    }.getOrNull()
}

/**
 * A code in a saved picture or a screenshot. Large pictures are tried as they
 * are first, then scaled down, which helps zxing-cpp with photos of screens.
 */
fun readPicture(source: Bitmap): String? {
    val bitmap = if (source.config == Bitmap.Config.HARDWARE) source.copy(Bitmap.Config.ARGB_8888, false) else source
    val reader = BarcodeReader().apply {
        options.formats = setOf(BarcodeReader.Format.QR_CODE)
        options.tryHarder = true
        options.tryRotate = true
        options.tryInvert = true
        options.tryDownscale = true
    }
    var current = bitmap
    while (true) {
        reader.read(current).firstOrNull { !it.text.isNullOrEmpty() }?.text?.let { return it }
        if (maxOf(current.width, current.height) < 800) return null
        current = Bitmap.createScaledBitmap(current, current.width / 2, current.height / 2, true)
    }
}
