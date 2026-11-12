package com.frequencies.core

import java.io.File

/** A picture's size from its header (PNG, JPEG, GIF, WebP), without decoding it. */
object ImageSize {
    fun of(file: File): Pair<Int, Int>? = try {
        file.inputStream().use { input ->
            // readNBytes is Android 13+; this works from Android 8.
            val buf = ByteArray(256 * 1024)
            var n = 0
            while (n < buf.size) {
                val got = input.read(buf, n, buf.size - n)
                if (got < 0) break
                n += got
            }
            of(buf.copyOf(n))
        }
    } catch (e: Exception) {
        null
    }

    /** storage._small_art: a code's thumbnail, 128 px or less. */
    fun isSmall(file: File, most: Int = 128): Boolean =
        of(file)?.let { (w, h) -> maxOf(w, h) <= most } ?: false

    fun of(b: ByteArray): Pair<Int, Int>? {
        fun u8(i: Int) = b[i].toInt() and 0xff
        fun be16(i: Int) = (u8(i) shl 8) or u8(i + 1)
        fun le16(i: Int) = u8(i) or (u8(i + 1) shl 8)
        fun be32(i: Int) = (be16(i) shl 16) or be16(i + 2)
        if (b.size >= 24 && u8(0) == 0x89 && b[1] == 'P'.code.toByte()) return be32(16) to be32(20)
        if (b.size >= 10 && b[0] == 'G'.code.toByte() && b[1] == 'I'.code.toByte()) return le16(6) to le16(8)
        if (b.size >= 30 && String(b, 0, 4) == "RIFF" && String(b, 8, 4) == "WEBP") {
            return when (String(b, 12, 4)) {
                "VP8 " -> (le16(26) and 0x3fff) to (le16(28) and 0x3fff)
                "VP8L" -> {
                    val bits = u8(21) or (u8(22) shl 8) or (u8(23) shl 16) or (u8(24) shl 24)
                    ((bits and 0x3fff) + 1) to (((bits shr 14) and 0x3fff) + 1)
                }
                "VP8X" -> ((u8(24) or (u8(25) shl 8) or (u8(26) shl 16)) + 1) to
                    ((u8(27) or (u8(28) shl 8) or (u8(29) shl 16)) + 1)
                else -> null
            }
        }
        if (b.size >= 4 && u8(0) == 0xff && u8(1) == 0xd8) {
            var i = 2
            while (i + 9 < b.size) {
                if (u8(i) != 0xff) { i++; continue }
                val marker = u8(i + 1)
                if (marker == 0xd8 || marker == 0x01 || marker in 0xd0..0xd7 || marker == 0xff) { i++; continue }
                val len = be16(i + 2)
                if (marker in 0xc0..0xcf && marker != 0xc4 && marker != 0xc8 && marker != 0xcc) {
                    return be16(i + 7) to be16(i + 5)
                }
                i += 2 + len
            }
        }
        return null
    }
}
