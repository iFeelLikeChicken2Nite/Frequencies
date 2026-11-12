// The Android Gradle plugin lives on Google's Maven only, so it joins the
// build classpath only where the SDK is (see settings.gradle.kts).
buildscript {
    val hasSdk = rootDir.resolve("local.properties").exists() || System.getenv("ANDROID_HOME") != null || System.getenv("ANDROID_SDK_ROOT") != null
    repositories {
        google()
        mavenCentral()
    }
    if (hasSdk) {
        dependencies { classpath("com.android.tools.build:gradle:8.13.0") }
    }
}

plugins {
    alias(libs.plugins.kotlin.jvm) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.serialization) apply false
    alias(libs.plugins.compose.compiler) apply false
}
