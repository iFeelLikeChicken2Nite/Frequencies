import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val build = (findProperty("frequencies.build") as String).toInt()

// The release key and its passwords stay outside the project (and out of
// every zip): ~/.gradle/gradle.properties names the file that holds them,
// e.g. frequencies.signing=C:\\Users\\YOU\\Android\\keys\\signing.properties
val signing = Properties().apply {
    (findProperty("frequencies.signing") as String?)?.let { path ->
        val file = File(path)
        if (file.isFile) file.inputStream().use { load(it) }
    }
}

android {
    namespace = "com.frequencies.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.frequencies.app"
        minSdk = 26
        targetSdk = 36
        versionCode = build
        versionName = "1.5.$build"
        buildConfigField("int", "FREQ_BUILD", "$build")
        // The Compose UI tests (app/src/androidTest): run by hand on a phone, never part of a release.
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (signing.getProperty("storeFile") != null) {
            create("release") {
                storeFile = File(signing.getProperty("storeFile"))
                storePassword = signing.getProperty("storePassword")
                keyAlias = signing.getProperty("keyAlias")
                keyPassword = signing.getProperty("keyPassword")
            }
        }
    }

    // Like the desktop's --dev: the dev build shows dev settings and
    // installs beside the user build; the user build is the one to share.
    flavorDimensions += "channel"
    productFlavors {
        create("dev") {
            dimension = "channel"
            applicationIdSuffix = ".dev"
            versionNameSuffix = "-dev"
            resValue("string", "app_name", "Frequencies Dev")
            buildConfigField("boolean", "DEV", "true")
            signingConfig = signingConfigs.findByName("release")
        }
        create("user") {
            dimension = "channel"
            resValue("string", "app_name", "Frequencies")
            buildConfigField("boolean", "DEV", "false")
            signingConfig = signingConfigs.findByName("release")
        }
        // What F-Droid builds from the public source: the user build's app
        // (same id, Android Auto kept), no dev code paths, nothing that
        // needs a secret. Never signed here: F-Droid builds it unsigned and
        // signs it itself, so a clean build here is what F-Droid's server makes.
        create("fdroid") {
            dimension = "channel"
            resValue("string", "app_name", "Frequencies")
            buildConfigField("boolean", "DEV", "false")
        }
    }

    // The release key is set per flavor above (dev and user), so the
    // fdroid flavor's release APK stays unsigned.
    buildTypes {
        release {
            isMinifyEnabled = false
        }
        // Only for the UI tests: its own id, so it never collides with the installed signed builds.
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    // No Google-signed dependency list inside the APK: F-Droid refuses it
    // and it would keep the build from being reproducible.
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // NewPipeExtractor uses Java APIs newer than Android 8 has.
        isCoreLibraryDesugaringEnabled = true
    }

    lint {
        // Lint runs in a later batch; it must not stop a release build meanwhile.
        checkReleaseBuilds = false
    }

    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}", "/META-INF/versions/9/OSGI-INF/MANIFEST.MF")
    }
}

kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

dependencies {
    implementation(project(":core"))
    implementation(project(":songid"))

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.material3.adaptive.navigation.suite)
    implementation(libs.compose.material.icons.extended)
    debugImplementation(libs.compose.ui.tooling)
    debugImplementation(libs.compose.ui.test.manifest)
    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.test.runner)
    // Espresso 3.5 (pulled in by ui-test-junit4) fails on Android 16: no InputManager.getInstance.
    androidTestImplementation(libs.androidx.test.espresso.core)

    implementation(libs.media3.exoplayer)
    implementation(libs.media3.exoplayer.hls)
    implementation(libs.media3.session)
    implementation(libs.media3.datasource.okhttp)
    implementation(libs.coil.compose)
    implementation(libs.coil.network.okhttp)
    implementation(libs.androidsvg)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.androidx.work.runtime.ktx)
    implementation(libs.zxing.core)
    implementation(libs.zxing.cpp)
    implementation(libs.camerax.camera2)
    implementation(libs.camerax.lifecycle)
    implementation(libs.camerax.view)
    // Sign-in pages (iBroadcast) open in a Custom Tab, never a web view inside the app.
    implementation(libs.androidx.browser)
    // YouTube playlists and videos (GPL v3; no Google Play services).
    implementation(libs.newpipe.extractor)
    // The Pebble watchapp's phone side (build 190)
    implementation(libs.pebblekit2.client)
    coreLibraryDesugaring(libs.desugar.jdk.libs)
}

// What the desktop build ships, as plain data in the public source
// (tools/ship_assets.py refreshes it). No build carries a token of any
// kind (desktop build 151: reports go through the developer's Cloudflare
// Worker). Before every build: only those files, no token file anywhere,
// and no token-looking text in any asset.
val shipped = file("src/main/assets/shipped")
val checkShippedAssets by tasks.registering {
    doLast {
        val allowed = setOf("artist_overrides.json", "photo_pack.json", "station_overrides.json", "twitch_app.json")
        val token = Regex("(github_pat_|ghp_|gho_|ghs_|ghu_)[A-Za-z0-9_]{10,}")
        file("src").walkTopDown().filter { it.isFile && "assets" in it.path }.forEach { f ->
            if (f.parentFile == shipped && f.name !in allowed) throw GradleException("assets/shipped/${f.name} isn't something the build ships")
            if (f.name in setOf("report_token.json", "github_token.json", "release_token.json")) throw GradleException("${f.name} must never go in an APK")
            if (f.length() < 4_000_000 && token.containsMatchIn(f.readText(Charsets.ISO_8859_1))) {
                throw GradleException("${f.path} holds something that looks like a GitHub token")
            }
        }
    }
}
tasks.configureEach { if (name == "preBuild") dependsOn(checkShippedAssets) }
