#!/usr/bin/env bash
# تجهيز مشروع NQ Call (Flutter) — بيعمل فولدرات Windows/Android/iOS ويظبط الصلاحيات والخدمة والأيقونة.
# شغّله مرة واحدة بعد ما تنزّل الكود:  ./setup.sh   (في الويندوز من Git Bash)
set -euo pipefail
cd "$(dirname "$0")"

PLATFORMS="${PLATFORMS:-windows,android,ios}"
flutter create --org sa.nq --project-name nq_call --platforms "$PLATFORMS" . >/dev/null
rm -f test/widget_test.dart
flutter pub get

# ---------- Android ----------
if [[ -d android ]]; then
  KT=$(find android/app/src/main -name MainActivity.kt | head -1)
  PKG=$(sed -n 's/^package //p' "$KT")
  DIR=$(dirname "$KT")
  sed "s/__PKG__/$PKG/" native/android/MainActivity.kt > "$KT"
  sed "s/__PKG__/$PKG/" native/android/NQService.kt > "$DIR/NQService.kt"
  # بعض المكتبات (file_picker) نسخة Java/Kotlin مختلفة — نخليها تحذير بدل ما توقف البناء
  grep -q 'kotlin.jvm.target.validation.mode' android/gradle.properties 2>/dev/null || echo 'kotlin.jvm.target.validation.mode=warning' >> android/gradle.properties
  M=android/app/src/main/AndroidManifest.xml
  for perm in INTERNET ACCESS_NETWORK_STATE RECORD_AUDIO MODIFY_AUDIO_SETTINGS WAKE_LOCK POST_NOTIFICATIONS FOREGROUND_SERVICE \
              FOREGROUND_SERVICE_SPECIAL_USE USE_FULL_SCREEN_INTENT REQUEST_IGNORE_BATTERY_OPTIMIZATIONS VIBRATE BLUETOOTH_CONNECT \
              CHANGE_NETWORK_STATE CALL_PHONE; do
    grep -q "android.permission.$perm\"" "$M" || \
      perl -0pi -e "s|(<manifest[^>]*>)|\$1\n    <uses-permission android:name=\"android.permission.$perm\"/>|" "$M"
  done
  grep -q 'NQService' "$M" || perl -0pi -e 's|</application>|    <service android:name=".NQService" android:exported="false" android:foregroundServiceType="specialUse">\n            <property android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE" android:value="voip_line_keepalive"/>\n        </service>\n    </application>|' "$M"
  perl -0pi -e 's/android:label="nq_call"/android:label="NQ Call"/' "$M"
  G=android/app/build.gradle.kts; [[ -f "$G" ]] || G=android/app/build.gradle
  perl -pi -e 's/minSdk = flutter\.minSdkVersion/minSdk = 24/; s/minSdkVersion flutter\.minSdkVersion/minSdkVersion 24/' "$G"
  if ! grep -q desugar_jdk_libs "$G"; then
    if [[ "$G" == *.kts ]]; then
      perl -0pi -e 's/compileOptions \{/compileOptions {\n        isCoreLibraryDesugaringEnabled = true/' "$G"
      printf '\ndependencies {\n    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")\n}\n' >> "$G"
    else
      perl -0pi -e 's/compileOptions \{/compileOptions {\n        coreLibraryDesugaringEnabled true/' "$G"
      printf "\ndependencies {\n    coreLibraryDesugaring 'com.android.tools:desugar_jdk_libs:2.1.4'\n}\n" >> "$G"
    fi
  fi
fi

# ---------- Windows ----------
if [[ -d windows ]]; then
  # webview_windows بيستخدم coroutines قديمة — Visual Studio الجديد بيرفضها من غير التعريف ده
  grep -q SILENCE_EXPERIMENTAL_COROUTINE windows/CMakeLists.txt || \
    perl -0pi -e 's/(project\([^\n]*\)\n)/$1add_compile_definitions(_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS)\n/' windows/CMakeLists.txt
  # اسم الشباك والبرنامج
  perl -pi -e 's/L"nq_call"/L"NQ Call"/g' windows/runner/main.cpp 2>/dev/null || true
  perl -pi -e 's/"FileDescription", "nq_call"/"FileDescription", "NQ Call"/; s/"ProductName", "nq_call"/"ProductName", "NQ Call"/; s/"InternalName", "nq_call"/"InternalName", "NQ Call"/' windows/runner/Runner.rc 2>/dev/null || true
fi

# ---------- iOS (على Mac بس) ----------
if [[ -d ios && "$(uname)" == "Darwin" ]]; then
  P=ios/Runner/Info.plist; PB=/usr/libexec/PlistBuddy
  put() { $PB -c "Delete :$1" "$P" 2>/dev/null || true; $PB -c "Add :$1 $2 $3" "$P"; }
  put CFBundleDisplayName string "NQ Call"
  put NSMicrophoneUsageDescription string "التطبيق محتاج المايك عشان تتكلم في المكالمات."
  put ITSAppUsesNonExemptEncryption bool false
  $PB -c "Delete :UIBackgroundModes" "$P" 2>/dev/null || true
  $PB -c "Add :UIBackgroundModes array" "$P"
  $PB -c "Add :UIBackgroundModes:0 string audio" "$P"
  $PB -c "Add :UIBackgroundModes:1 string voip" "$P"
  sed -i '' "s/IPHONEOS_DEPLOYMENT_TARGET = [0-9.]*;/IPHONEOS_DEPLOYMENT_TARGET = 15.0;/g" ios/Runner.xcodeproj/project.pbxproj
  [[ -f ios/Podfile ]] || flutter build ios --config-only --no-codesign >/dev/null
  sed -i '' "s/^# platform :ios.*/platform :ios, '15.0'/" ios/Podfile
  if ! grep -q PERMISSION_MICROPHONE ios/Podfile; then
    sed -i '' "s/    flutter_additional_ios_build_settings(target)/    flutter_additional_ios_build_settings(target)\\
    target.build_configurations.each do |config|\\
      config.build_settings['GCC_PREPROCESSOR_DEFINITIONS'] ||= ['\$(inherited)', 'PERMISSION_MICROPHONE=1', 'PERMISSION_NOTIFICATIONS=1']\\
    end/" ios/Podfile
  fi
  (cd ios && pod install >/dev/null)
fi

dart run flutter_launcher_icons >/dev/null || echo "(الأيقونة: اتخطّت)"
echo "✔ جاهز."
echo "  Windows:  flutter run -d windows     |  EXE: flutter build windows --release"
echo "  Android:  flutter run                |  APK: flutter build apk --release"
