@file:OptIn(ExperimentalSerializationApi::class)

package com.frequencies.core

import java.io.File
import java.math.BigDecimal
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonUnquotedLiteral
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull

/**
 * The desktop writes its files with Python's json.dump(indent=2,
 * ensure_ascii=False). This writes the same shape, so a file that goes
 * phone -> desktop -> phone reads the same in a text diff too.
 */
val FreqJson = Json {
    prettyPrint = true
    prettyPrintIndent = "  "
    isLenient = true
    allowSpecialFloatingPointValues = true
}

fun readJson(file: File): JsonElement? =
    try {
        if (file.isFile) FreqJson.parseToJsonElement(file.readText(Charsets.UTF_8)) else null
    } catch (e: Exception) {
        null
    }

/** Write through a temp file, as the desktop's _atomic_write_json does. */
fun writeJsonAtomic(file: File, value: JsonElement) {
    file.parentFile?.mkdirs()
    val tmp = File(file.path + ".tmp")
    tmp.writeText(encodeJson(value), Charsets.UTF_8)
    if (!tmp.renameTo(file)) {
        file.delete()
        if (!tmp.renameTo(file)) {
            file.writeText(tmp.readText(Charsets.UTF_8), Charsets.UTF_8)
            tmp.delete()
        }
    }
}

private val CompactJson = Json { isLenient = true; allowSpecialFloatingPointValues = true }

/** One line, no spaces (a sync message), numbers written exactly as they were read. */
fun encodeJsonCompact(value: JsonElement): String = CompactJson.encodeToString(JsonElement.serializer(), rawNumbers(value))

/** Text for [value], numbers written exactly as they were read. */
fun encodeJson(value: JsonElement): String = FreqJson.encodeToString(JsonElement.serializer(), rawNumbers(value))

/**
 * kotlinx re-prints a parsed float through Double.toString, which turns
 * the desktop's 1792536316.53 into 1.79253631653E9. An unquoted literal
 * is written as its text, so every number goes out as it came in.
 */
private fun rawNumbers(e: JsonElement): JsonElement = when (e) {
    is JsonObject -> JsonObject(e.mapValuesTo(LinkedHashMap()) { rawNumbers(it.value) })
    is JsonArray -> JsonArray(e.map(::rawNumbers))
    is JsonNull -> e
    is JsonPrimitive -> if (e.isString || e.booleanOrNull != null) e else JsonUnquotedLiteral(e.content)
}

/**
 * A number written the way Python writes a float: 1792536316.53, never
 * 1.79253631653E9 (which Python reads, but nobody else enjoys).
 */
fun floatJson(value: Double): JsonElement {
    if (value.isNaN() || value.isInfinite()) return JsonPrimitive(0.0)
    var text = BigDecimal.valueOf(value).toPlainString()
    if (!text.contains('.')) text += ".0"
    return JsonUnquotedLiteral(text)
}

fun intJson(value: Long): JsonElement = JsonPrimitive(value)

/** Python's truthiness, for the flags the desktop runs through bool(). */
fun truthy(e: JsonElement?): Boolean = when (e) {
    null, JsonNull -> false
    is JsonPrimitive -> {
        if (e.isString) e.content.isNotEmpty()
        else e.booleanOrNull ?: ((e.doubleOrNull ?: 0.0) != 0.0)
    }
    is JsonArray -> e.isNotEmpty()
    is JsonObject -> e.isNotEmpty()
}

fun JsonElement?.str(): String? = (this as? JsonPrimitive)?.takeIf { it !is JsonNull }?.contentOrNull

fun JsonElement?.num(): Double? {
    val p = this as? JsonPrimitive ?: return null
    if (p is JsonNull) return null
    p.booleanOrNull?.let { if (!p.isString) return if (it) 1.0 else 0.0 }
    return p.content.trim().toDoubleOrNull()
}

fun stringArray(values: List<String>): JsonArray = JsonArray(values.map { JsonPrimitive(it) })
