package com.frequencies.core

import java.io.File
import java.nio.file.Files
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Batch 21 (build 176): iBroadcast says why, in plain words. */
class Batch21Test {
    @Test fun saidPicksIBroadcastsReason() {
        assertEquals("Invalid signature", IBroadcast.said("""{"result":false,"message":"Invalid signature"}"""))
        assertEquals("The refresh token is invalid.", IBroadcast.said("""{"error":"invalid_grant","error_description":"The refresh token is invalid."}"""))
        assertEquals("invalid_grant", IBroadcast.said("""{"error":"invalid_grant"}"""))
        assertEquals("Forbidden", IBroadcast.said("Forbidden\n"))
        assertEquals("", IBroadcast.said("<html><body>403 Forbidden</body></html>"))
        assertEquals("", IBroadcast.said("x".repeat(300)))
        assertEquals("", IBroadcast.said("{not json"))
        assertEquals("", IBroadcast.said(""))
    }

    @Test fun refusalsInPlainWords() {
        assertEquals("iBroadcast couldn't find that track (HTTP 404).", IBroadcast.refusal(404))
        assertTrue(IBroadcast.refusal(403).contains("Sign in to iBroadcast again in Settings."))
        assertTrue(IBroadcast.refusal(503).startsWith("iBroadcast's streaming server had a problem (HTTP 503)"))
        assertTrue(IBroadcast.refusal(429).contains("Try again in a minute"))
        assertEquals("iBroadcast refused that track (HTTP 400). iBroadcast said: “bad file”", IBroadcast.refusal(400, """{"message":"bad file"}"""))
        for (code in listOf(400, 401, 403, 404, 410, 429, 500, 503)) assertFalse(IBroadcast.refusal(code).contains("said no"))
        assertTrue(IBroadcast.signInRefusal(401) && IBroadcast.signInRefusal(403))
        assertFalse(IBroadcast.signInRefusal(404) || IBroadcast.signInRefusal(500))
    }

    @Test fun renewalProblemsInPlainWords() {
        assertEquals(
            "iBroadcast ended this phone's sign-in (“The refresh token is invalid.”). Sign in to iBroadcast again in Settings.",
            IBroadcast.renewalProblem("""rejected (HTTP 400 {"error":"invalid_grant","error_description":"The refresh token is invalid."})"""),
        )
        assertEquals("iBroadcast ended this phone's sign-in. Sign in to iBroadcast again in Settings.", IBroadcast.renewalProblem("rejected (HTTP 401 )"))
        assertEquals("iBroadcast ended this phone's sign-in (“invalid_grant”). Sign in to iBroadcast again in Settings.", IBroadcast.renewalProblem("rejected (invalid_grant)"))
        assertTrue(IBroadcast.renewalProblem("HTTP 502").contains("(HTTP 502)"))
        assertTrue(IBroadcast.renewalProblem("timeout").startsWith("Couldn't reach iBroadcast"))
        assertTrue(IBroadcast.renewalProblem("signed out").startsWith("You're signed out"))
    }

    @Test fun deadSignInIsMarked() {
        val server = MockWebServer()
        server.start()
        val dir = Files.createTempDirectory("ib21").toFile()
        val account = IBroadcastAccount(File(dir, "ibroadcast.json"), OkHttpClient(), oauth = server.url("/").toString().trimEnd('/'))
        account.store(JsonObject(mapOf("access_token" to JsonPrimitive("old"), "refresh_token" to JsonPrimitive("r1"), "expires_in" to JsonPrimitive(3600))))
        server.enqueue(MockResponse().setResponseCode(400).setBody("""{"error":"invalid_grant"}"""))
        val dead = account.freshToken(force = true)
        assertTrue(dead.dead)
        assertTrue(dead.problem.startsWith("rejected"))
        // A refusal that isn't about the refresh token (rate limit, a server's bad day) keeps the sign-in.
        server.enqueue(MockResponse().setResponseCode(403).setBody("slow down"))
        assertFalse(account.freshToken(force = true).dead)
        server.enqueue(MockResponse().setResponseCode(500))
        assertFalse(account.freshToken(force = true).dead)
        server.enqueue(MockResponse().setResponseCode(401))
        assertTrue(account.freshToken(force = true).dead)
        server.shutdown()
    }
}
