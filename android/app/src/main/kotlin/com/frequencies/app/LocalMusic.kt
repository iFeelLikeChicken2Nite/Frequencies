package com.frequencies.app

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import android.util.Log
import com.frequencies.core.LocalStations
import com.frequencies.core.OfflineRules
import com.frequencies.core.ShareZip
import com.frequencies.core.Station
import java.io.File
import java.util.concurrent.ConcurrentHashMap

/**
 * The music of local files stations on this phone. A folder the user
 * picked (Storage Access Framework: Android asks once, the app keeps the
 * permission for that folder only) or one the app unpacked from a share
 * zip into its own storage. A station whose folder is neither (a desktop
 * path from a backup) is "not on this phone": shown grayed, never played,
 * and kept exactly as it came so it goes back to the desktop the same.
 */
class LocalMusic(private val context: Context) {
    /** Where share zips' music is unpacked (the app's own, no permission needed). */
    val unpackDir = File(context.filesDir, "music")

    private val cache = ConcurrentHashMap<String, Pair<Long, List<Uri>>>()

    private fun treeUri(s: Station): Uri? = LocalStations.androidFolder(s).takeIf { it.startsWith("content://") }?.let(Uri::parse)

    /** The folders the app may read, asked again at most every few seconds (it is asked per row). */
    @Volatile private var grants: Pair<Long, Set<String>> = 0L to emptySet()

    private fun permitted(tree: Uri): Boolean {
        val (at, set) = grants
        val now = System.currentTimeMillis()
        val have = if (now - at < 5_000) set else context.contentResolver.persistedUriPermissions
            .filter { it.isReadPermission }.map { it.uri.toString() }.toSet().also { grants = now to it }
        return tree.toString() in have
    }

    /** A folder was just given or given back: ask again next time. */
    fun grantsChanged() { grants = 0L to emptySet() }

    /** Its music can be played here (a folder the user gave the app, or one the app holds itself). */
    fun onThisPhone(s: Station?): Boolean {
        if (!LocalStations.isLocal(s)) return true
        val tree = treeUri(s!!)
        if (tree != null && permitted(tree)) return true
        val path = LocalStations.localPath(s)
        if (path.isEmpty()) return false
        val dir = File(path)
        return dir.isDirectory && dir.canRead() && (dir.list()?.isNotEmpty() == true)
    }

    /**
     * Why [s] can't be played now, or null when it can: offline and it
     * needs the internet (task 214), or a local files station whose folder
     * isn't on this phone.
     */
    fun blocked(s: Station?, offline: Boolean = Connectivity.offline.value): String? = when {
        s == null -> null
        !onThisPhone(s) -> OfflineRules.notHere(s.displayName)
        offline && OfflineRules.needsInternet(s) -> OfflineRules.cantPlay(s.displayName)
        else -> null
    }

    /** storage.scan_local_tracks: every audio file in the folder and below, sorted, at most 20000. Network-free but slow: not on the main thread. */
    fun tracks(s: Station): List<Uri> {
        val key = s.id + "|" + LocalStations.androidFolder(s) + "|" + LocalStations.localPath(s)
        cache[key]?.let { (at, list) -> if (System.currentTimeMillis() - at < 10 * 60_000) return list }
        val tree = treeUri(s)
        val found = when {
            tree != null && permitted(tree) -> scanTree(tree)
            LocalStations.localPath(s).isNotEmpty() && File(LocalStations.localPath(s)).isDirectory ->
                File(LocalStations.localPath(s)).walkTopDown().filter { it.isFile && LocalStations.isAudio(it.name) }
                    .take(LIMIT).map { it.path }.sorted().map { Uri.fromFile(File(it)) }.toList()
            else -> emptyList()
        }
        cache[key] = System.currentTimeMillis() to found
        return found
    }

    fun forget(s: Station) { cache.keys.removeAll { it.startsWith(s.id + "|") } }

    /** The share zip's music for [s]: each file and its place in the folder. */
    fun shareTracks(s: Station): List<ShareZip.Track> {
        val tree = treeUri(s)
        if (tree != null && permitted(tree)) {
            return walkTree(tree).map { (rel, uri) -> ShareZip.Track(rel) { context.contentResolver.openInputStream(uri) ?: error("unreadable") } }
        }
        val dir = File(LocalStations.localPath(s))
        if (!dir.isDirectory) return emptyList()
        return dir.walkTopDown().filter { it.isFile && LocalStations.isAudio(it.name) }.sortedBy { it.path }
            .map { f -> ShareZip.Track(f.relativeTo(dir).invariantSeparatorsPath) { f.inputStream() } }.toList()
    }

    private fun scanTree(tree: Uri): List<Uri> = walkTree(tree).sortedBy { it.first }.map { it.second }

    /** (path inside the folder, document link) of every audio file under [tree]. */
    private fun walkTree(tree: Uri): List<Pair<String, Uri>> {
        val out = ArrayList<Pair<String, Uri>>()
        val pending = ArrayDeque<Pair<String, String>>()       // (document id, path so far)
        pending.add(DocumentsContract.getTreeDocumentId(tree) to "")
        val columns = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
        )
        while (pending.isNotEmpty() && out.size < LIMIT) {
            val (doc, prefix) = pending.removeFirst()
            val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, doc)
            try {
                context.contentResolver.query(children, columns, null, null, null)?.use { c ->
                    while (c.moveToNext()) {
                        val id = c.getString(0) ?: continue
                        val name = c.getString(1).orEmpty()
                        val mime = c.getString(2).orEmpty()
                        val rel = if (prefix.isEmpty()) name else "$prefix/$name"
                        if (mime == DocumentsContract.Document.MIME_TYPE_DIR) pending.add(id to rel)
                        else if (LocalStations.isAudio(name) || mime.startsWith("audio/")) out += rel to DocumentsContract.buildDocumentUriUsingTree(tree, id)
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "[local] couldn't read a folder: $e")
            }
        }
        return out
    }

    /** The folder the user picked: keep the permission (it lasts across restarts) and describe it. */
    fun adopt(tree: Uri): Pair<String, String> {
        runCatching { context.contentResolver.takePersistableUriPermission(tree, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
        grantsChanged()
        val path = LocalStations.pathOfTreeId(DocumentsContract.getTreeDocumentId(tree))
        return path to LocalStations.folderName(path)
    }

    /** A folder no saved station uses any more: its permission goes back. */
    fun releaseUnused(stations: List<Station>) {
        val used = stations.map { LocalStations.androidFolder(it) }.toSet()
        for (p in context.contentResolver.persistedUriPermissions) {
            if (p.uri.toString() !in used) runCatching { context.contentResolver.releasePersistableUriPermission(p.uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
        }
        grantsChanged()
    }

    /** Music unpacked from a share zip that no saved station plays any more (removed, or the import was canceled). */
    fun cleanUnpacked(stations: List<Station>) {
        if (stations.isEmpty()) return      // an unreadable list must never cost the music
        val used = stations.map { LocalStations.localPath(it) }.filter { it.isNotEmpty() }.map { File(it).absolutePath }.toSet()
        unpackDir.listFiles()?.forEach { dir ->
            if (dir.absolutePath !in used && !dir.name.startsWith(".incoming-")) {
                Log.i(TAG, "[local] removing unused shared music '${dir.name}'")
                dir.deleteRecursively()
            }
        }
    }

    companion object {
        private const val TAG = "Frequencies"
        private const val LIMIT = 20_000
    }
}
