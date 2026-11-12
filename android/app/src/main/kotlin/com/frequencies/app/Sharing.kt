package com.frequencies.app

import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.text.TextPaint
import android.text.TextUtils
import android.util.Log
import androidx.core.content.FileProvider
import com.frequencies.core.ArtAccent
import com.frequencies.core.ArtLink
import com.frequencies.core.Featured
import com.frequencies.core.LocalStations
import com.frequencies.core.ShareCode
import com.frequencies.core.ShareZip
import com.frequencies.core.Station
import com.frequencies.core.StationColor
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel
import com.google.zxing.qrcode.encoder.Encoder
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.math.roundToInt
import kotlinx.serialization.json.JsonPrimitive

/**
 * Sending frequencies to other people: a share zip through the share
 * sheet, or one station's FREQ1 code as text or a QR picture.
 */
object Sharing {
    /** The desktop's code_thumbnail: the picture's short side scaled to [side] px, as JPEG. */
    val thumbnailer = ShareCode.Thumbnailer { picture, side, quality ->
        val bitmap = BitmapFactory.decodeByteArray(picture, 0, picture.size) ?: return@Thumbnailer null
        val scale = side.toFloat() / minOf(bitmap.width, bitmap.height)
        val small = Bitmap.createScaledBitmap(
            bitmap, maxOf(1, Math.round(bitmap.width * scale)), maxOf(1, Math.round(bitmap.height * scale)), true,
        )
        ByteArrayOutputStream().also { small.compress(Bitmap.CompressFormat.JPEG, quality, it) }.toByteArray()
    }

    /**
     * artlink._picture_bytes's smaller copy: at most 1200 px, PNG when it
     * has transparency, else JPEG at 90, 80, 70 until it fits.
     */
    val shrinker = ArtLink.Shrinker { picture ->
        val bitmap = BitmapFactory.decodeByteArray(picture, 0, picture.size) ?: return@Shrinker null
        val longest = maxOf(bitmap.width, bitmap.height)
        val scaled = if (longest <= ArtLink.SHRINK_PX) bitmap else {
            val scale = ArtLink.SHRINK_PX.toFloat() / longest
            Bitmap.createScaledBitmap(bitmap, maxOf(1, Math.round(bitmap.width * scale)), maxOf(1, Math.round(bitmap.height * scale)), true)
        }
        val png = scaled.hasAlpha()
        var data = ByteArray(0)
        for (quality in listOf(90, 80, 70)) {
            val out = ByteArrayOutputStream()
            scaled.compress(if (png) Bitmap.CompressFormat.PNG else Bitmap.CompressFormat.JPEG, if (png) 100 else quality, out)
            data = out.toByteArray()
            if (data.size <= ArtLink.MAX_UPLOAD || png) break
        }
        data
    }

    /**
     * The desktop's _link_shared_picture: a picture of the user's own goes
     * to the picture service so the code can carry a link to it at full
     * size. Blocking (call it off the main thread). The new link is saved
     * with the station only while it still has that picture; returns the
     * station as it is now, or null when the upload didn't work.
     */
    fun linkPicture(context: Context, station: Station): Station? {
        val app = context.applicationContext
        val service = ArtLink.service(app.artists.addresses)
        val path = station.customArtPath
        val result = ArtLink.upload(app.artists.http, service, File(path), shrinker)
        Log.i("Frequencies", ArtLink.logLine(result))
        if (result.link.isEmpty()) return null
        val library = app.library
        library.update(station.id) {
            if (it.customArtPath == path) {
                it["art_url"] = JsonPrimitive(result.link)
                it["art_url_sha"] = JsonPrimitive(Featured.sha1(File(path)))
            }
        }
        return library.station(station.id)?.takeIf { Featured.artLink(it) == result.link }
    }

    private fun shareDir(context: Context) = File(context.cacheDir, "share").apply {
        mkdirs()
        listFiles()?.forEach { it.delete() }
    }

    private fun uriFor(context: Context, file: File): Uri =
        FileProvider.getUriForFile(context, "${context.packageName}.files", file)

    private fun send(context: Context, intent: Intent, title: String) {
        context.startActivity(Intent.createChooser(intent, title).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    /**
     * Several frequencies (or one) as a share zip, through the share sheet.
     * [withMusic]: local files stations on this phone take their music
     * along (desktop sharezip.write with_audio), written off the main
     * thread since that can be a lot.
     */
    fun shareZip(context: Context, stations: List<Station>, withMusic: Boolean = false) {
        val app = context.applicationContext
        val title = if (stations.size == 1) "Share ${stations[0].displayName}" else "Share ${stations.size} frequencies"
        val file = File(shareDir(app), ShareZip.fileName(stations))
        fun open() {
            val uri = uriFor(app, file)
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = "application/zip"
                putExtra(Intent.EXTRA_STREAM, uri)
                clipData = ClipData.newRawUri(file.name, uri)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            send(app, intent, title)
        }
        if (!withMusic) {
            file.outputStream().use { ShareZip.write(stations, it) }
            open()
            return
        }
        Notices.say("Making the zip…")
        Thread({
            val music = app.localMusic
            val result = runCatching {
                file.outputStream().use { out ->
                    ShareZip.write(stations, out) { s -> if (LocalStations.isLocal(s) && music.onThisPhone(s)) music.shareTracks(s) else null }
                }
            }
            Handler(Looper.getMainLooper()).post {
                result.onSuccess { count ->
                    Log.i("Frequencies", "[share] '${file.name}' with $count music files (${file.length() / 1024} KB)")
                    open()
                }.onFailure { e ->
                    Log.w("Frequencies", "[share] couldn't make the zip: $e")
                    file.delete()
                    Notices.say("Couldn't make the zip: ${e.message ?: "something went wrong"}")
                }
            }
        }, "share-zip").start()
    }

    fun code(station: Station): String = ShareCode.fromStation(station, thumbnailer = thumbnailer).first

    fun shareCode(context: Context, station: Station, code: String) {
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, code)
            putExtra(Intent.EXTRA_SUBJECT, station.displayName)
        }
        send(context, intent, "Share the code")
    }

    /**
     * sharedialog.qr_pixmap: the code on white with a quiet zone, a small
     * mark in the middle (inside what error correction can lose), and,
     * with a [label], a border in [accent] with the name set into its
     * bottom edge. Level M first, then L for a code too big for M.
     * Sizes follow the desktop's 360-pixel window, scaled to [size].
     */
    fun qr(text: String, size: Int = 900, label: String = "", accent: Int = Color.WHITE): Bitmap {
        val code = runCatching { Encoder.encode(text, ErrorCorrectionLevel.M) }.getOrNull()
            ?: Encoder.encode(text, ErrorCorrectionLevel.L)
        val matrix = code.matrix
        val count = matrix.width
        val k = size / 360f
        val quiet = 2
        val frame = if (label.isNotEmpty()) (22 * k).roundToInt() else 0
        val total = count + quiet * 2
        val scale = maxOf(2, (size - frame * 2) / total)
        val inner = total * scale
        val side = inner + frame * 2
        val bitmap = Bitmap.createBitmap(side, side, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        val white = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE }
        val ink = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = QR_INK }

        canvas.drawRoundRect(RectF(frame.toFloat(), frame.toFloat(), (frame + inner).toFloat(), (frame + inner).toFloat()), 6 * k, 6 * k, white)
        val module = Paint().apply { color = QR_INK }   // no smoothing: neighbors meet without seams
        for (y in 0 until count) for (x in 0 until count) {
            if (matrix[x, y].toInt() != 1) continue
            val left = (frame + (x + quiet) * scale).toFloat()
            val top = (frame + (y + quiet) * scale).toFloat()
            canvas.drawRect(left, top, left + scale, top + scale, module)
        }

        // The mark: a clear patch with three pairs of arcs and a dot, like the desktop's.
        var patch = maxOf(scale * 3, (inner * 0.15f).toInt())
        patch -= patch % 2
        val c = frame + inner / 2f
        val half = patch / 2f + 2 * k
        canvas.drawRoundRect(RectF(c - half, c - half, c + half, c + half), 4 * k, 4 * k, white)
        val pen = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = QR_INK
            style = Paint.Style.STROKE
            strokeWidth = maxOf(1.5f * k, patch * 0.075f)
            strokeCap = Paint.Cap.ROUND
        }
        for (i in 0 until 3) {
            val r = patch * (0.16f + i * 0.15f)
            val arc = RectF(c - r, c - r, c + r, c + r)
            canvas.drawArc(arc, -55f, 110f, false, pen)   // right
            canvas.drawArc(arc, 125f, 110f, false, pen)   // left
        }
        canvas.drawCircle(c, c, maxOf(2f * k, patch * 0.07f), ink)

        if (label.isNotEmpty()) qrFrame(canvas, side.toFloat(), frame.toFloat(), k, label, accent)
        return bitmap
    }

    /** sharedialog._draw_frame: the whole name, shrinking to a floor before it's cut short. */
    private fun qrFrame(canvas: Canvas, side: Float, frame: Float, k: Float, label: String, accent: Int) {
        val font = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
            typeface = Typeface.DEFAULT_BOLD
            textSize = maxOf(9 * k, frame - 10 * k)
            color = accent
            textAlign = Paint.Align.CENTER
        }
        val room = side - frame * 2
        var text = label
        var width = font.measureText(text) + 14 * k
        while (width > room && font.textSize > 7 * k) {
            font.textSize -= k
            width = font.measureText(text) + 14 * k
        }
        if (width > room) {
            text = TextUtils.ellipsize(label, font, room - 14 * k, TextUtils.TruncateAt.END).toString()
            width = font.measureText(text) + 14 * k
        }
        val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = accent
            style = Paint.Style.STROKE
            strokeWidth = 2 * k
        }
        val inset = frame / 2f
        val box = RectF(inset, inset, side - inset, side - inset)
        val gapLeft = box.centerX() - width / 2f
        val gapRight = box.centerX() + width / 2f
        val radius = minOf(14 * k, box.width() / 8f)
        val path = Path().apply {
            moveTo(gapRight, box.bottom)
            lineTo(box.right - radius, box.bottom)
            quadTo(box.right, box.bottom, box.right, box.bottom - radius)
            lineTo(box.right, box.top + radius)
            quadTo(box.right, box.top, box.right - radius, box.top)
            lineTo(box.left + radius, box.top)
            quadTo(box.left, box.top, box.left, box.top + radius)
            lineTo(box.left, box.bottom - radius)
            quadTo(box.left, box.bottom, box.left + radius, box.bottom)
            lineTo(gapLeft, box.bottom)
        }
        canvas.drawPath(path, line)
        val metrics = font.fontMetrics
        canvas.drawText(text, box.centerX(), box.bottom - (metrics.ascent + metrics.descent) / 2f, font)
    }

    /**
     * stationlist.station_color: the dominant color of the station's own
     * picture when it has one on the phone, otherwise the one its name gives.
     */
    fun stationColor(station: Station): Int {
        val path = station.customArtPath
        if (path.isNotEmpty() && File(path).isFile) {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(path, bounds)
            var sample = 1
            while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= 256) sample *= 2
            val art = BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = sample })
            if (art != null) {
                val px = IntArray(art.width * art.height)
                art.getPixels(px, 0, art.width, 0, 0, art.width, art.height)
                ArtAccent.dominant(px, art.width, art.height)?.let { return it or (0xFF shl 24) }
            }
        }
        return StationColor.fromName(StationColor.nameOf(station))
    }

    private const val QR_INK = 0xFF101012.toInt()

    fun shareQr(context: Context, station: Station, bitmap: Bitmap) {
        val file = File(shareDir(context), ShareZip.safeName(station.displayName) + " QR.png")
        file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        val uri = uriFor(context, file)
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "image/png"
            putExtra(Intent.EXTRA_STREAM, uri)
            clipData = ClipData.newRawUri(file.name, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        send(context, intent, "Share the QR code")
    }
}
