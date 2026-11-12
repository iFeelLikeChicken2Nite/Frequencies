package com.frequencies.core

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Tags the desktop's way (storage.all_tags … rename_tags): kept on each
 * station, colors in settings tag_colors by lowercase name, and "spare"
 * tags (made in the tag manager, on no station yet) in settings
 * spare_tags. Every function returns new stations; the ones passed in
 * are left alone.
 */
object Tags {
    val COLOR_CHOICES = linkedMapOf(
        "Red" to "#d96c6c", "Orange" to "#d9914e", "Amber" to "#d9b54e", "Yellow" to "#c9c04a",
        "Lime" to "#a3c94a", "Green" to "#6fbf73", "Teal" to "#4ebf9e", "Cyan" to "#4ea3c9",
        "Blue" to "#5a8fd9", "Indigo" to "#7b7bd9", "Purple" to "#a86fd9", "Magenta" to "#c96fbf",
        "Pink" to "#d96f9a", "Brown" to "#a67c52", "Slate" to "#6c7a89", "Gray" to "#8a8a8a",
    )

    fun colorName(hex: String): String =
        COLOR_CHOICES.entries.firstOrNull { it.value.equals(hex.trim(), ignoreCase = true) }?.key ?: hex

    /** Every tag in use, A to Z, the first spelling met. */
    fun all(stations: List<Station>): List<String> {
        val seen = HashMap<String, String>()
        for (s in stations) for (t in s.tags) seen.putIfAbsent(t.lowercase(), t)
        return seen.keys.sorted().map { seen.getValue(it) }
    }

    fun spare(settings: Settings): List<String> =
        (settings["spare_tags"] as? JsonArray)?.mapNotNull { it.str()?.trim()?.takeIf(String::isNotEmpty) } ?: emptyList()

    fun setSpare(settings: Settings, names: List<String>) {
        val seen = HashSet<String>()
        val kept = names.map { it.trim() }.filter { it.isNotEmpty() && seen.add(it.lowercase()) }
        settings["spare_tags"] = stringArray(kept)
    }

    /** Station tags plus spare ones. */
    fun library(stations: List<Station>, settings: Settings): List<String> {
        val seen = HashMap<String, String>()
        for (t in all(stations) + spare(settings)) seen.putIfAbsent(t.lowercase(), t)
        return seen.keys.sorted().map { seen.getValue(it) }
    }

    fun setColor(settings: Settings, tag: String, color: String) {
        val colors = LinkedHashMap((settings["tag_colors"] as? JsonObject) ?: JsonObject(emptyMap()))
        colors[tag.trim().lowercase()] = JsonPrimitive(color)
        settings["tag_colors"] = JsonObject(colors)
    }

    private fun renameColor(settings: Settings, old: String, new: String) {
        val oldKey = old.trim().lowercase()
        val newKey = new.trim().lowercase()
        if (newKey.isEmpty() || oldKey == newKey) return
        val colors = LinkedHashMap((settings["tag_colors"] as? JsonObject) ?: JsonObject(emptyMap()))
        val color = colors.remove(oldKey) ?: return
        if (newKey !in colors) colors[newKey] = color
        settings["tag_colors"] = JsonObject(colors)
    }

    private fun withTags(s: Station, tags: List<String>): Station = s.copy().apply { this["tags"] = stringArray(tags) }

    private fun renameOn(stations: List<Station>, old: String, new: String): List<Station> {
        val oldKey = old.trim().lowercase()
        val newName = new.trim()
        if (newName.isEmpty()) return stations
        return stations.map { s ->
            if (s.tags.none { it.lowercase() == oldKey }) return@map s
            val updated = ArrayList<String>()
            for (t in s.tags) {
                if (t.lowercase() == oldKey) {
                    if (updated.none { it.lowercase() == newName.lowercase() }) updated.add(newName)
                } else {
                    updated.add(t)
                }
            }
            withTags(s, updated)
        }
    }

    /**
     * Several renames at once, {old: new}, as if all happened together
     * (each old name goes to a stand-in first). Colors and spare tags
     * move with their names; [settings] is changed in place.
     */
    fun rename(stations: List<Station>, settings: Settings, renamed: Map<String, String>): List<Station> {
        var list = stations
        val standIns = LinkedHashMap<String, String>()
        renamed.entries.forEachIndexed { i, (old, new) ->
            val standIn = "\u0000rename$i"
            standIns[standIn] = new
            list = renameOn(list, old, standIn)
            renameColor(settings, old, standIn)
            setSpare(settings, spare(settings).map { if (it.lowercase() == old.trim().lowercase()) standIn else it })
        }
        for ((standIn, new) in standIns) {
            list = renameOn(list, standIn, new)
            renameColor(settings, standIn, new)
            setSpare(settings, spare(settings).map { if (it == standIn) new else it })
        }
        return list
    }

    /** Take [tag] off every station and out of the spare list. */
    fun remove(stations: List<Station>, settings: Settings, tag: String): List<Station> {
        val key = tag.trim().lowercase()
        setSpare(settings, spare(settings).filter { it.lowercase() != key })
        return stations.map { s -> if (s.tags.any { it.lowercase() == key }) withTags(s, s.tags.filter { it.lowercase() != key }) else s }
    }

    /** Give [tag] to (or take it from) the stations whose ids are in [ids]. */
    fun set(stations: List<Station>, ids: Set<String>, tag: String, on: Boolean): List<Station> {
        val name = tag.trim()
        if (name.isEmpty()) return stations
        val key = name.lowercase()
        return stations.map { s ->
            if (s.id !in ids) return@map s
            val has = s.tags.any { it.lowercase() == key }
            when {
                on && !has -> withTags(s, s.tags + name)
                !on && has -> withTags(s, s.tags.filter { it.lowercase() != key })
                else -> s
            }
        }
    }
}
