package com.frequencies.core

import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * A change asked for on the main thread (a Settings switch), Android build
 * 203, batch 28: [show] runs at once without the library lock (the screen
 * flips), and [apply] (the change under the lock, then the save) runs on a
 * thread of its own, in the order asked. A sync merge holding the lock
 * makes the apply wait, never the switch. Like LatestSaver (build 198), but
 * every change is kept: two switches flipped quickly are two changes.
 */
class QuickChanges(name: String, private val onError: (Throwable) -> Unit = {}) {
    private val pool = Executors.newSingleThreadExecutor { r -> Thread(r, name).apply { isDaemon = true } }

    fun change(show: () -> Unit, apply: () -> Unit) {
        try { show() } catch (e: Throwable) { onError(e) }
        pool.execute { try { apply() } catch (e: Throwable) { onError(e) } }
    }

    /** Waits until every change asked for so far is applied (tests). */
    fun flush(timeoutMs: Long): Boolean = runCatching { pool.submit {}.get(timeoutMs, TimeUnit.MILLISECONDS); true }.getOrDefault(false)
}
