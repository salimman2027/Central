# NQ Phone — Flutter (iOS + Android)

التطبيق بيفتح صفحة تليفون NQ من السيرفر (`https://3.89.245.93.sslip.io/phone/`) جوه WebView،
والمكالمات **من السنترال بس** (WebRTC). أي تحديث على السيرفر بيوصل التطبيق من غير رفع نسخة جديدة.

## التجهيز (مرة واحدة — على Mac عشان iOS)
```bash
cd flutter
./setup.sh
```
السكربت بيعمل فولدرات `ios/` و `android/` بـ `flutter create`، وبيظبط:
- Bundle ID للآيفون: `sa.nq.phone` — iOS 15+
- صلاحيات المايك والكاميرا والإشعارات، و Background Mode = Audio (المكالمة ماتقطعش لما الشاشة تقفل)
- قناة `nq/audio` (جلسة الصوت + السبيكر + حساس القرب) — `native/ios/AppDelegate.swift` و `native/android/MainActivity.kt`
- الأيقونة من `assets/icon.png`

## الرفع على TestFlight
1. `open ios/Runner.xcworkspace`
2. Runner → **Signing & Capabilities** → اختار الـ **Team**
3. **Product → Archive** → **Distribute App → App Store Connect → Upload**
   (أو: `flutter build ipa` وبعدين ارفع الـ ipa بـ Transporter)
4. App Store Connect → TestFlight → ضيف المناديب

قبل كل رفع زوّد `version: 1.0.0+N` في `pubspec.yaml` (الرقم بعد `+`).

## الإعدادات
- عنوان السيرفر: `kServerUrl` في `lib/main.dart`

## الجسر بين الصفحة والتطبيق: `window.NQiOS.post(type, data)`
| type | data | بيعمل إيه |
|---|---|---|
| `call` | true/false | جلسة صوت المكالمة + حساس القرب |
| `speaker` | true/false | السبيكر |
| `notify` | {title, body} | إشعار (مكالمة واردة والتطبيق في الخلفية) |
| `open` | url | يفتح رابط بره التطبيق |
| `share` | text | مشاركة |
| `file` | {name, data(base64)} | حفظ/مشاركة ملف (تصدير Excel/PDF) |
| `haptic` | — | اهتزاز |

## حدود النسخة دي
- **من السنترال بس** — مفيش اتصال من شريحة الموبايل.
- المكالمات الواردة بترن والتطبيق مفتوح (أو لسه رايح الخلفية). عشان ترن والموبايل مقفول تمامًا محتاجين
  المرحلة التانية: PushKit/CallKit (iOS) + Firebase (Android) + السيرفر يبعت push قبل ما السنترال يرن.
