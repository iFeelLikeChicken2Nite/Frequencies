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
    // OkHttp and the JSON reader for Shazam's lookup.
    api(project(":core"))
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
}
