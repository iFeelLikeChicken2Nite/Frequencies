package com.frequencies.core

import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Writes the newest value on a thread of its own (Android build 198, batch 26).
 * [save] only hands the value over: the caller (often the main thread: Settings'
 * Forget, Frequensync's What to sync) never waits for a lock a sync run holds or
 * for the disk. Values saved quickly one after another are written once, newest
 * last; two writes never run at the same time.
 */
class LatestSaver<T : Any>(name: String, private val onError: (Throwable) -> Unit = {}, private val write: (T) -> Unit) {
    private val pending = AtomicReference<T?>(null)
    private val pool = Executors.newSingleThreadExecutor { r -> Thread(r, name).apply { isDaemon = true } }

    fun save(value: T) {
        // Only the save that finds nothing waiting queues a write; a later one just replaces the value.
        if (pending.getAndSet(value) == null) pool.execute(::drain)
    }

    private fun drain() {
        val v = pending.getAndSet(null) ?: return
        try { write(v) } catch (e: Throwable) { onError(e) }
    }

    /** Waits until everything saved so far is written (tests). */
    fun flush(timeoutMs: Long): Boolean = runCatching { pool.submit {}.get(timeoutMs, TimeUnit.MILLISECONDS); true }.getOrDefault(false)
}
