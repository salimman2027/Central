#!/usr/bin/env bash
# تجهيز مشروع NQ Phone (Flutter) — شغّله مرة واحدة: ./setup.sh
# بيعمل فولدرات iOS/Android، ويحط الصلاحيات، والـ Bundle ID، وقناة الصوت، والأيقونة.
set -euo pipefail
cd "$(dirname "$0")"
BUNDLE_IOS="sa.nq.phone"

flutter create --org sa.nq --project-name nq_phone --platforms ios,android . >/dev/null
rm -f test/widget_test.dart   # اختبار القالب الافتراضي (مش بتاعنا)
flutter pub get

# ---------- iOS ----------
cp native/ios/AppDelegate.swift ios/Runner/AppDelegate.swift
P=ios/Runner/Info.plist
if [[ "$(uname)" == "Darwin" ]]; then
  PB=/usr/libexec/PlistBuddy
  put() { $PB -c "Delete :$1" "$P" 2>/dev/null || true; $PB -c "Add :$1 $2 $3" "$P"; }
  put CFBundleDisplayName string "NQ Phone"
  put NSMicrophoneUsageDescription string "التطبيق محتاج المايك عشان تتكلم في المكالمات."
  put NSCameraUsageDescription string "لتصوير المستندات وإرسالها للعملاء."
  put ITSAppUsesNonExemptEncryption bool false
  $PB -c "Delete :UIBackgroundModes" "$P" 2>/dev/null || true
  $PB -c "Add :UIBackgroundModes array" "$P"
  $PB -c "Add :UIBackgroundModes:0 string audio" "$P"
  sed -i '' "s/sa\.nq\.nqPhone/$BUNDLE_IOS/g" ios/Runner.xcodeproj/project.pbxproj
  sed -i '' "s/IPHONEOS_DEPLOYMENT_TARGET = [0-9.]*;/IPHONEOS_DEPLOYMENT_TARGET = 15.0;/g" ios/Runner.xcodeproj/project.pbxproj
  [[ -f ios/Podfile ]] || flutter build ios --config-only --no-codesign >/dev/null
  sed -i '' "s/^# platform :ios.*/platform :ios, '15.0'/" ios/Podfile
  if ! grep -q PERMISSION_MICROPHONE ios/Podfile; then
    sed -i '' "s/    flutter_additional_ios_build_settings(target)/    flutter_additional_ios_build_settings(target)\\
    target.build_configurations.each do |config|\\
      config.build_settings['GCC_PREPROCESSOR_DEFINITIONS'] ||= ['\$(inherited)', 'PERMISSION_MICROPHONE=1', 'PERMISSION_NOTIFICATIONS=1', 'PERMISSION_CAMERA=1']\\
    end/" ios/Podfile
  fi
  (cd ios && pod install >/dev/null)
fi

# ---------- Android ----------
KT=$(find android/app/src/main -name MainActivity.kt | head -1)
PKG=$(sed -n 's/^package //p' "$KT")
sed "s/__PKG__/$PKG/" native/android/MainActivity.kt > "$KT"
M=android/app/src/main/AndroidManifest.xml
for perm in INTERNET RECORD_AUDIO MODIFY_AUDIO_SETTINGS WAKE_LOCK POST_NOTIFICATIONS CAMERA; do
  grep -q "android.permission.$perm\"" "$M" || \
    perl -0pi -e "s|(<manifest[^>]*>)|\$1\n    <uses-permission android:name=\"android.permission.$perm\"/>|" "$M"
done
perl -0pi -e 's/android:label="nq_phone"/android:label="NQ Phone"/' "$M"
G=$(ls android/app/build.gradle.kts android/app/build.gradle 2>/dev/null | head -1)
perl -pi -e 's/minSdk = flutter\.minSdkVersion/minSdk = 24/' "$G"
# flutter_local_notifications محتاجة core library desugaring
if ! grep -q desugar_jdk_libs "$G"; then
  if [[ "$G" == *.kts ]]; then
    perl -0pi -e 's/compileOptions \{/compileOptions {\n        isCoreLibraryDesugaringEnabled = true/' "$G"
    printf '\ndependencies {\n    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")\n}\n' >> "$G"
  else
    perl -0pi -e 's/compileOptions \{/compileOptions {\n        coreLibraryDesugaringEnabled true/' "$G"
    printf "\ndependencies {\n    coreLibraryDesugaring 'com.android.tools:desugar_jdk_libs:2.1.4'\n}\n" >> "$G"
  fi
fi

dart run flutter_launcher_icons >/dev/null
echo "✔ جاهز. iOS: افتح ios/Runner.xcworkspace في Xcode → Signing (Team) → Product › Archive → Upload."
echo "  Android: flutter build apk --release"
