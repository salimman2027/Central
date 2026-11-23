# NQ Phone — iOS

تطبيق الآيفون لتليفون NQ: صفحة التليفون من السيرفر (`https://3.89.245.93.sslip.io/phone/`) جوه `WKWebView`،
والمكالمات بـ WebRTC (JsSIP) على السنترال. أي تحديث على السيرفر بيوصل التطبيق على طول من غير رفع نسخة جديدة.

## المطلوب
- Mac عليه Xcode 15 أو أحدث
- حساب Apple Developer (Team ID)
- Bundle ID: `sa.nq.phone` (اعمله في developer.apple.com → Identifiers لو مش موجود)

## خطوات الرفع على TestFlight
1. افتح `NQPhone.xcodeproj` (موجود جاهز في الفولدر).
   - لو حابب تولّده من جديد: `brew install xcodegen && xcodegen` جوه فولدر `ios`.
2. اختار الـ Target **NQPhone** → **Signing & Capabilities**:
   - **Team**: فريق الشركة
   - سيب **Automatically manage signing** متعلّم
3. من فوق اختار **Any iOS Device (arm64)**.
4. **Product → Archive** → لما يخلص: **Distribute App → App Store Connect → Upload**.
5. في **App Store Connect → TestFlight**: ضيف المناديب كـ Testers (داخليين أو برابط عام).

قبل كل رفع جديد زوّد **Build** (CURRENT_PROJECT_VERSION) في إعدادات الـ Target.

## الإعدادات
- عنوان السيرفر: `NQServerURL` في `NQPhone/Info.plist`.
- الصلاحيات: المايك (إجباري للمكالمات)، الكاميرا والصور (للمستندات)، الإشعارات.
- Background Mode: **Audio** — عشان المكالمة ماتقطعش لما الشاشة تقفل وسط المكالمة.

## الجسر بين الصفحة و iOS (`window.NQiOS.post(type, data)`)
| type | data | بيعمل إيه |
|---|---|---|
| `call` | `true/false` | يفتح/يقفل جلسة الصوت (playAndRecord / voiceChat) وحساس القرب |
| `speaker` | `true/false` | السبيكر |
| `notify` | `{title, body}` | إشعار محلي (مكالمة واردة والتطبيق في الخلفية) |
| `open` | `url` | يفتح رابط بره التطبيق |
| `share` | `text` | مشاركة |
| `haptic` | — | اهتزاز خفيف |

## حدود النسخة دي
- المكالمات الواردة بتوصل والتطبيق مفتوح أو لسه رايح الخلفية. عشان ترن والموبايل مقفول تمامًا
  محتاجين المرحلة التانية: **PushKit (VoIP push) + CallKit** + تعديل على السيرفر يبعت push قبل ما السنترال يرن.
- مفيش اتصال من شريحة الآيفون أو قراية سجل المكالمات (Apple مابتسمحش).
- لو هيتنشر على App Store العام (مش TestFlight) ممكن Apple تطلب مميزات native أكتر (Guideline 4.2) — المرحلة التانية بتحل ده.
