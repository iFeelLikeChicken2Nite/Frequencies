plugins {
    alias(libs.plugins.kotlin.jvm)
}

// Java 17 bytecode, which the Android app's compiler takes as it is.
java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}
kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

dependencies {
    api(libs.kotlinx.serialization.json)
    api(libs.okhttp)
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.okhttp.tls)
}

// A live check against the real services (Twitch), only when asked for:
//   gradlew :core:test --tests '*LiveCheckTest*' -Pfrequencies.live=true
tasks.test {
    // The sync tests against the desktop's own Python (SyncDesktopTest), only when given its source:
    //   gradlew :core:test --tests '*SyncDesktopTest*' -Pfrequencies.desktop=<desktop source>/src
    systemProperty("frequencies.desktop", (project.findProperty("frequencies.desktop") ?: "").toString())
    systemProperty("frequencies.tools", rootProject.file("tools").absolutePath)
    systemProperty("frequencies.live", (project.findProperty("frequencies.live") ?: "").toString())
    // Always run again when asked live: the answer depends on the service, not on the code.
    if ((project.findProperty("frequencies.live") ?: "").toString() == "true") outputs.upToDateWhen { false }
}
