package com.frequencies.app

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Log
import com.frequencies.core.Backup
import com.frequencies.core.FreqJson
import com.frequencies.core.Imports
import com.frequencies.core.Playlists
import com.frequencies.core.ShareCode
import com.frequencies.core.ShareZip
import com.frequencies.core.Station
import com.frequencies.core.str
import kotlinx.serialization.json.JsonObject

/** What a file, a code or a shared text turned out to hold. */
sealed class Incoming {
    /** Stations to add, after the user picks which ([from] names the file's kind). */
    class Stations(val stations: List<Station>, val from: String) : Incoming()
    /** A full backup: Replace or Merge. */
    class BackupFile(val contents: Backup.Contents) : Incoming()
    /** A plain stream address, to add by address. */
    class Address(val url: String) : Incoming()
    class Failed(val message: String) : Incoming()
}

/**
 * Reads whatever arrives (the file picker, "Open with", the share sheet,
 * a QR code or the clipboard) by its content, like the desktop's import.
 * Runs off the main thread: pictures are written as it reads.
 */
object Importer {
    private const val TAG = "Frequencies"
    private const val MOST_BYTES = 64L * 1024 * 1024

    fun fileName(context: Context, uri: Uri): String =
        runCatching {
            context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0) else null
            }
        }.getOrNull() ?: uri.lastPathSegment.orEmpty()

    fun read(context: Context, uri: Uri): Incoming {
        val library = context.library
        val name = fileName(context, uri)
        val input = try {
            context.contentResolver.openInputStream(uri)?.buffered(64 * 1024) ?: return Incoming.Failed("That file couldn't be opened.")
        } catch (e: Exception) {
            Log.w(TAG, "[import] couldn't open $uri: $e")
            return Incoming.Failed("That file couldn't be opened.")
        }
        return input.use { stream ->
            try {
                // A share zip can carry a local station's music (task 214), so it is read as it streams, with no size cap.
                stream.mark(8)
                val head = ByteArray(4)
                val got = stream.read(head)
                stream.reset()
                if (got == 4 && ShareZip.looksLikeZip(head)) {
                    Log.i(TAG, "[import] reading the share zip '$name'")
                    val stations = ShareZip.read(stream, library.files.artDir, library.newId, audioRoot = context.localMusic.unpackDir)
                    context.localMusic.grantsChanged()
                    if (stations.isEmpty()) Incoming.Failed("That share has no frequencies in it.")
                    else Incoming.Stations(stations, "a Frequencies share")
                } else {
                    val out = java.io.ByteArrayOutputStream()
                    val buf = ByteArray(64 * 1024)
                    var total = 0L
                    while (true) {
                        val n = stream.read(buf)
                        if (n < 0) break
                        total += n
                        if (total > MOST_BYTES) return@use Incoming.Failed("That file is too big to be a station list.")
                        out.write(buf, 0, n)
                    }
                    Log.i(TAG, "[import] reading '$name' ($total bytes)")
                    readText(context, out.toByteArray().toString(Charsets.UTF_8))
                }
            } catch (e: ShareZip.ShareZipError) {
                Incoming.Failed(e.message ?: "That zip isn't a Frequencies share.")
            } catch (e: Backup.BackupError) {
                Incoming.Failed(e.message ?: "That backup couldn't be read.")
            } catch (e: Imports.ImportError) {
                Incoming.Failed(e.message ?: "That file couldn't be read.")
            } catch (e: java.io.IOException) {
                Log.w(TAG, "[import] couldn't read '$name': $e")
                Incoming.Failed(if (e.message.orEmpty().contains("ENOSPC", true) || e.message.orEmpty().contains("No space", true))
                    "There isn't enough room on this phone for that share's music." else "That file couldn't be read.")
            } catch (e: Exception) {
                Log.w(TAG, "[import] failed on '$name': $e")
                Incoming.Failed("That file couldn't be read.")
            }
        }
    }

    /** Text from a file, the clipboard, the share sheet or a QR code. */
    fun readText(context: Context, text: String): Incoming {
        val library = context.library
        val trimmed = text.trim()
        if (trimmed.startsWith(ShareCode.PREFIX)) {
            val station = ShareCode.toStation(trimmed, library.files.artDir, library.newId)
                ?: return Incoming.Failed("That code couldn't be read. It may have been cut off.")
            return Incoming.Stations(listOf(station), "a shared code")
        }
        // A FREQ1 code inside a longer message (a chat app adds words around it).
        Regex("FREQ1:[A-Za-z0-9_=-]+").find(trimmed)?.let { return readText(context, it.value) }
        if (!trimmed.contains('\n') && Playlists.isUrl(trimmed) && !Playlists.looksLikePlaylist(trimmed)) {
            return Incoming.Address(trimmed)
        }
        if (trimmed.startsWith("{")) {
            val obj = runCatching { FreqJson.parseToJsonElement(trimmed) as? JsonObject }.getOrNull()
            val format = obj?.get("format").str()
            if (format == Backup.FORMAT || format == "streamdeck-radio-backup") {
                return Incoming.BackupFile(Backup.read(trimmed, library.files.artDir, newId = library.newId))
            }
        }
        val (format, entries) = Imports.read(trimmed)
        val now = library.now()
        return Incoming.Stations(entries.map { Imports.toStation(it, library.newId, now) }, Imports.FORMAT_NAMES[format] ?: format)
    }
}
