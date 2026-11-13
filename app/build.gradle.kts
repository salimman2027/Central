plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "sa.nq.phone"
    compileSdk = 34

    defaultConfig {
        applicationId = "sa.nq.phone"
        minSdk = 26
        targetSdk = 34
        versionCode = 2
        versionName = "1.1"
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}
