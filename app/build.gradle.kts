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
        versionCode = 17
        versionName = "2.5"
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

dependencies {
    // سكانر المستندات (زي CamScanner) من Google ML Kit
    implementation("com.google.android.gms:play-services-mlkit-document-scanner:16.0.0-beta1")
}
