package com.frequencies.core

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/**
 * Android's regex engine is ICU, not the JDK's: it rejects the (?U) flag
 * (and UNICODE_CHARACTER_CLASS), which desktop Java accepts, so a pattern
 * with it passes every test here and crashes the app on the phone (build
 * 149 did, at startup). Spell Unicode classes out instead: \p{L}, \p{N},
 * \p{Z}. Patterns were also checked by compiling them with ICU4C.
 */
class AndroidRegexTest {
    @Test
    fun noJavaOnlyRegexFlags() {
        val root = File("..").canonicalFile
        val bad = listOf("core", "songid", "app").flatMap { dir ->
            File(root, "$dir/src/main").walkTopDown().filter { it.extension == "kt" }.flatMap { f ->
                f.readLines().mapIndexedNotNull { i, line ->
                    if ("(?U)" in line || "UNICODE_CHARACTER_CLASS" in line) "${f.relativeTo(root)}:${i + 1}" else null
                }
            }.toList()
        }
        assertEquals("Java-only regex flags (ICU rejects them)", emptyList<String>(), bad)
    }
}
