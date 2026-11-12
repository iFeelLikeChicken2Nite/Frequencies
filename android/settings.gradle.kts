// Frequencies for Android.
//
// core and songid are plain Kotlin/JVM and build anywhere Maven Central
// does (the cloud sandbox included). app needs the Android SDK, so it is
// only part of the build where local.properties (or ANDROID_HOME) says where
// the SDK is.
pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        // Only NewPipe's extractor and its JSON reader come from JitPack (built from their GPL source).
        maven("https://jitpack.io") {
            content { includeGroup("com.github.TeamNewPipe") }
        }
    }
}

rootProject.name = "frequencies-android"

include(":core", ":songid")
// F-Droid's build server names the SDK with ANDROID_HOME (and writes its own
// local.properties); either one brings the app in.
if (file("local.properties").exists() || System.getenv("ANDROID_HOME") != null || System.getenv("ANDROID_SDK_ROOT") != null) {
    include(":app")
}
