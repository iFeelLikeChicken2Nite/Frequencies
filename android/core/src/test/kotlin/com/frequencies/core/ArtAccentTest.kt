package com.frequencies.core

import javax.imageio.ImageIO
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * "Follow the artwork" gives the same accent as the desktop. The answers
 * come from the desktop's own theme.py run on the same pictures
 * (tools/make_accent_fixtures.py): the 13 default stations' art plus
 * made-up edge cases (gray, a speck of color, pale, odd sizes).
 */
class ArtAccentTest {
    private val answers = FreqJson.parseToJsonElement(
        ArtAccentTest::class.java.getResourceAsStream("/accent/desktop-answers.json")!!.use { it.readBytes().toString(Charsets.UTF_8) },
    ).jsonObject

    private fun hex(c: Int) = "#%06x".format(c and 0xFFFFFF)
    private fun parse(s: String) = s.removePrefix("#").toInt(16)

    @Test
    fun sameColorAsTheDesktop() {
        val dominant = answers.getValue("dominant").jsonObject
        var checked = 0
        for ((name, want) in dominant) {
            val img = ImageIO.read(ArtAccentTest::class.java.getResourceAsStream("/accent/$name.png"))
            val px = img.getRGB(0, 0, img.width, img.height, null, 0, img.width)
            val got = ArtAccent.dominant(px, img.width, img.height)?.let(::hex)
            val expected = if (want is JsonNull) null else want.jsonPrimitive.content
            assertEquals("$name (${img.width}x${img.height})", expected, got)
            checked++
        }
        assertEquals(18, checked)
    }

    @Test
    fun colorlessArtHasNoColor() {
        val dominant = answers.getValue("dominant").jsonObject
        for (name in listOf("gray", "white_speck", "pale")) assertEquals(JsonNull, dominant[name])
    }

    @Test
    fun sameFadeAsTheDesktop() {
        assertEquals(30, ArtAccent.fadeFrames)
        for (fade in answers.getValue("fades").jsonArray) {
            val o = fade.jsonObject
            val from = parse(o.getValue("from").jsonPrimitive.content)
            val to = parse(o.getValue("to").jsonPrimitive.content)
            val frames = o.getValue("frames").jsonArray.map { (it as JsonPrimitive).content }
            frames.forEachIndexed { i, want -> assertEquals("frame ${i + 1} of ${hex(from)} -> ${hex(to)}", want, hex(ArtAccent.fadeStep(from, to, i + 1))) }
        }
    }

    @Test
    fun sameColorMathAsQt() {
        val qt = FreqJson.parseToJsonElement(
            ArtAccentTest::class.java.getResourceAsStream("/accent/qt-hsl.json")!!.use { it.readBytes().toString(Charsets.UTF_8) },
        ).jsonObject
        for (row in qt.getValue("to_hsl").jsonArray) {
            val r = row.jsonArray.map { it.jsonPrimitive.content }
            assertEquals(r[0], ArtAccent.Hsl(r[1].toInt(), r[2].toInt(), r[3].toInt()), ArtAccent.hsl(parse(r[0])))
        }
        for (row in qt.getValue("from_hsl").jsonArray) {
            val r = row.jsonArray.map { it.jsonPrimitive.content }
            assertEquals("hsl(${r[0]}, ${r[1]}, ${r[2]})", r[3], hex(ArtAccent.fromHsl(r[0].toInt(), r[1].toInt(), r[2].toInt())))
        }
    }

    @Test
    fun sameClampAsTheDesktop() {
        for ((input, want) in answers.getValue("readable").jsonObject) {
            assertEquals(input, want.jsonPrimitive.content, hex(ArtAccent.readable(parse(input))))
        }
    }
}
