package com.frequencies.app.playback

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import com.frequencies.app.library
import java.io.File
import java.io.FileNotFoundException

/**
 * Station pictures for Android Auto, the notification and the lock
 * screen: they live in the app's private folder, which other apps can't
 * open, so they are handed out as content:// links. Read only, and only
 * files inside the art folder.
 */
class ArtProvider : ContentProvider() {
    override fun onCreate() = true

    private fun fileFor(uri: Uri): File {
        // The car's tag strip pictures (StripArt), drawn into the cache.
        val dir = if (uri.pathSegments.firstOrNull() == StripArt.FOLDER && uri.pathSegments.size == 2) StripArt.dir(context!!).canonicalFile
        else context!!.library.files.artDir.canonicalFile
        val name = uri.lastPathSegment ?: throw FileNotFoundException(uri.toString())
        val file = File(dir, name).canonicalFile
        if (file.parentFile != dir || !file.isFile) throw FileNotFoundException(uri.toString())
        return file
    }

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        if (mode != "r") throw SecurityException("Station pictures are read only")
        return ParcelFileDescriptor.open(fileFor(uri), ParcelFileDescriptor.MODE_READ_ONLY)
    }

    override fun getType(uri: Uri): String = when (uri.lastPathSegment?.substringAfterLast('.')?.lowercase()) {
        "jpg", "jpeg" -> "image/jpeg"
        "webp" -> "image/webp"
        "gif" -> "image/gif"
        "bmp" -> "image/bmp"
        else -> "image/png"
    }

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, args: Array<out String>?, sort: String?): Cursor? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun update(uri: Uri, values: ContentValues?, selection: String?, args: Array<out String>?) = 0
    override fun delete(uri: Uri, selection: String?, args: Array<out String>?) = 0

    companion object {
        /** The content:// link for a picture in the art folder, or null for a file elsewhere. */
        fun uriFor(packageName: String, artDir: File, file: File): Uri? {
            if (!file.isFile || file.canonicalFile.parentFile != artDir.canonicalFile) return null
            return Uri.Builder().scheme("content").authority("$packageName.art").appendPath(file.name).build()
        }
    }
}
