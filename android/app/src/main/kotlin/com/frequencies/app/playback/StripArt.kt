package com.frequencies.app.playback

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import android.net.Uri
import com.frequencies.core.Search
import java.io.File

/**
 * Pictures for the tag strip at the top of Saved in Android Auto (user,
 * build 185): Auto lays its rows out itself and only puts items side by
 * side in a grid, so each tag is a grid tile whose picture is the tag's
 * pill (its own color, as on the phone: outlined when off, solid with
 * dark text when showing only these, faint and struck through when
 * hidden), with an arrow tile on each side. Drawn once into the cache and
 * handed to the car through [ArtProvider].
 */
object StripArt {
    const val FOLDER = "strip"
    private const val SIZE = 256

    fun dir(context: Context): File = File(context.cacheDir, FOLDER).also { it.mkdirs() }

    private fun uri(context: Context, name: String): Uri =
        Uri.Builder().scheme("content").authority("${context.packageName}.art").appendPath(FOLDER).appendPath(name).build()

    /** The pill for [tag] in [color] ("#rrggbb") and its [state] (Search.NEUTRAL / INCLUDED / EXCLUDED). */
    fun pill(context: Context, tag: String, color: String, state: Int): Uri? = runCatching {
        val name = "pill2-" + Integer.toHexString("$tag|$color|$state".hashCode()) + ".png"
        draw(context, name) { canvas ->
            val tint = runCatching { Color.parseColor(color) }.getOrDefault(Color.rgb(0x7c, 0x9c, 0xff))
            // Build 188 (user: on and off too hard to tell apart): on is solid with a check; off a thin,
            // dimmer outline; hidden gray, struck through, with an x.
            val on = state == Search.INCLUDED
            val hidden = state == Search.EXCLUDED
            val main = when {
                hidden -> Color.argb(150, 0x9a, 0x9a, 0xa0)
                on -> tint
                else -> Color.argb(170, Color.red(tint), Color.green(tint), Color.blue(tint))
            }
            val box = RectF(10f, SIZE * 0.31f, SIZE - 10f, SIZE * 0.69f)
            val radius = box.height() / 2
            val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { this.color = main }
            if (on) canvas.drawRoundRect(box, radius, radius, fill)
            else {
                fill.style = Paint.Style.STROKE
                fill.strokeWidth = 4f
                val inner = RectF(box).apply { inset(2f, 2f) }
                canvas.drawRoundRect(inner, radius, radius, fill)
            }
            val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                this.color = if (on) Color.rgb(0x14, 0x14, 0x1a) else main
                typeface = Typeface.create(Typeface.DEFAULT, if (hidden) Typeface.NORMAL else Typeface.BOLD)
                textAlign = Paint.Align.CENTER
                textSize = 46f
                isStrikeThruText = hidden
            }
            val label = when { on -> "✓ $tag"; hidden -> "✕ $tag"; else -> tag }
            // Shrink a long tag to fit, then cut it.
            val room = box.width() - 36f
            while (text.measureText(label) > room && text.textSize > 28f) text.textSize -= 2f
            var shown = label
            while (text.measureText(shown) > room && shown.length > 1) shown = shown.dropLast(2) + "…"
            val y = box.centerY() - (text.descent() + text.ascent()) / 2
            canvas.drawText(shown, box.centerX(), y, text)
        }
    }.getOrNull()

    /** The strip's arrow: [back] points left. */
    fun arrow(context: Context, back: Boolean): Uri? = runCatching {
        draw(context, if (back) "arrow-back.png" else "arrow-next.png") { canvas ->
            val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE }
            val c = SIZE / 2f
            val h = SIZE * 0.17f
            val w = SIZE * 0.13f
            val path = Path().apply {
                if (back) { moveTo(c + w * 0.6f, c - h); lineTo(c - w, c); lineTo(c + w * 0.6f, c + h) }
                else { moveTo(c - w * 0.6f, c - h); lineTo(c + w, c); lineTo(c - w * 0.6f, c + h) }
                close()
            }
            canvas.drawPath(path, paint)
        }
    }.getOrNull()

    private fun draw(context: Context, name: String, paint: (Canvas) -> Unit): Uri {
        val file = File(dir(context), name)
        if (!file.isFile) {
            val bitmap = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
            paint(Canvas(bitmap))
            val tmp = File(file.parentFile, "$name.tmp")
            tmp.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
            bitmap.recycle()
            tmp.renameTo(file)
        }
        return uri(context, name)
    }
}
