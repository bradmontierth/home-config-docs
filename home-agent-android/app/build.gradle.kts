plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val localGoogleServices = file("google-services.json")
val externalGoogleServices = file(
    providers.gradleProperty("homeAgentGoogleServicesJson")
        .orElse(providers.environmentVariable("HOME_AGENT_GOOGLE_SERVICES_JSON"))
        .orElse("/home/pi/cecret_lake/home-agent-android/google-services.json")
        .get()
)

if (!localGoogleServices.isFile && externalGoogleServices.isFile) {
    externalGoogleServices.copyTo(localGoogleServices, overwrite = true)
}

if (localGoogleServices.isFile) {
    apply(plugin = "com.google.gms.google-services")
}

android {
    namespace = "com.homeagent.phone"
    compileSdk = 35

    signingConfigs {
        getByName("debug") {
            storeFile = rootProject.file(".android-user-home/debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    defaultConfig {
        applicationId = "com.homeagent.phone"
        minSdk = 26
        targetSdk = 35
        // Epoch-seconds versionCode: every rebuild counts as an upgrade so the
        // self-hosted F-Droid repo can push it to phones unattended.
        versionCode = (System.currentTimeMillis() / 1000L).toInt()
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation(platform("com.google.firebase:firebase-bom:34.7.0"))
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("com.google.firebase:firebase-messaging")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.json:json:20240303")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
