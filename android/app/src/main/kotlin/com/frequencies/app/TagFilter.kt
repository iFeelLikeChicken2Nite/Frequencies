package com.frequencies.app

import com.frequencies.core.Search
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Saved Frequencies' tag filter, one for the whole app (user, build 189):
 * the phone's tag pills, the wide sidebar's tags, Android Auto's Tags tab
 * and the car's ⋯ tag buttons all read and set this, so a change on one
 * shows on the others at once. Only the tag terms live here (a Search
 * query such as `tag:"80s" -tag:"talk"`); the phone's typed search stays
 * the phone's own.
 */
object TagFilter {
    private val state = MutableStateFlow("")

    /** The tag terms in force now ("" = no tag filter). */
    val query: StateFlow<String> = state.asStateFlow()

    /** Takes the tag terms of [query] (any typed text in it is left out). */
    fun set(query: String) {
        state.value = Search.withText(query, "")
    }
}
