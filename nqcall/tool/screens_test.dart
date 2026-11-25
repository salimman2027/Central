// صور للشاشات بالبيانات التجريبية (بتتعمل في GitHub Actions عشان نراجع الشكل)
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nq_call/core.dart';
import 'package:nq_call/phone.dart';
import 'package:nq_call/store.dart';
import 'package:nq_call/ui/call_screen.dart';
import 'package:nq_call/ui/login_page.dart';
import 'package:nq_call/ui/main_screen.dart';
import 'package:nq_call/ui/theme.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<void> _font(String family, List<String> paths) async {
  final l = FontLoader(family);
  var any = false;
  for (final p in paths) {
    final f = File(p);
    if (f.existsSync()) {
      l.addFont(Future.value(ByteData.view(f.readAsBytesSync().buffer)));
      any = true;
    }
  }
  if (any) await l.load();
}

Widget _app(Widget home) => MaterialApp(
      debugShowCheckedModeBanner: false,
      locale: const Locale('ar'),
      supportedLocales: const [Locale('ar')],
      localizationsDelegates: const [GlobalMaterialLocalizations.delegate, GlobalWidgetsLocalizations.delegate, GlobalCupertinoLocalizations.delegate],
      theme: buildTheme(Brightness.light),
      home: home,
    );

void main() {
  setUpAll(() async {
    final root = Platform.environment['FLUTTER_ROOT'] ?? '';
    final ar = ['/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf', '/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf'];
    final lat = ['/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf', '/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf'];
    await _font('Segoe UI', [...lat, ...ar]);
    await _font('Noto Sans Arabic', ar);
    await _font('MaterialIcons', ['$root/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf']);
    await _font('Roboto', lat);
  });

  Future<void> shot(WidgetTester t, Size size, Widget w, String name) async {
    t.view.physicalSize = size;
    t.view.devicePixelRatio = 1.0;
    await t.pumpWidget(_app(w));
    for (var i = 0; i < 6; i++) {
      await t.pump(const Duration(milliseconds: 200));
    }
    await expectLater(find.byType(MaterialApp), matchesGoldenFile('shots/$name.png'));
  }

  void fake() {
    final e = PhoneEngine.I;
    e.account = const Account(ext: '109', password: 'x', username: 'ali', name: 'Ali');
    e.line = LineState.online;
    e.lineText = 'متصل بالسنترال';
    e.quality = Quality.excellent;
    e.rttMs = 92;
    e.missed
      ..clear()
      ..add(Missed('+966551234567', 'صالح المدير المالي', DateTime.now().subtract(const Duration(minutes: 7)), 9, 'مرحلة: التفاوض'));
    final d = HomeData.I;
    d.people = [Person('102', 'سوزان', 'idle'), Person('105', 'إسراء', 'talk'), Person('107', 'محمد', 'idle'), Person('104', 'عبدالرحيم', 'off')];
    final now = DateTime.now();
    d.history = [
      HistoryItem(number: '+966551234567', direction: 'in', started: now.subtract(const Duration(minutes: 7)), answered: false, duration: 0, customer: 'صالح المدير المالي'),
      HistoryItem(number: '102', direction: 'out', started: now.subtract(const Duration(hours: 2)), answered: true, duration: 724),
      HistoryItem(number: '0559876543', direction: 'out', started: now.subtract(const Duration(hours: 3)), answered: true, duration: 95, customer: 'شركة النخبة'),
      HistoryItem(number: '0559876543', direction: 'out', started: now.subtract(const Duration(days: 1)), answered: true, duration: 60, customer: 'شركة النخبة'),
    ];
    d.serverVer = '4.36';
  }

  testWidgets('screens', (t) async {
    SharedPreferences.setMockInitialValues({});
    await Prefs.init();
    fake();
    await shot(t, const Size(500, 1180), const MainScreen(), 'main_500');
    HomeData.I.stop();
    fake();
    await shot(t, const Size(1100, 760), const MainScreen(), 'main_wide');
    HomeData.I.stop();
    fake();
    await shot(t, const Size(412, 900), const MainScreen(), 'main_phone');
    HomeData.I.stop();
    // شاشة المكالمة الواردة
    final c = ActiveCall(null, true, '+966551234567', CallPhase.incoming)
      ..name = 'صالح المدير المالي'
      ..info = {
        'crm': {'name': 'شركة الأفق', 'potential': 'التفاوض'},
        'owner': {'name': 'Ali'},
        'calls': 4,
        'note': {'text': 'طلب عرض سعر لـ 20 رخصة — مستني رد المدير'}
      };
    await shot(t, const Size(500, 900), CallScreen(call: c), 'incoming');
    final c2 = ActiveCall(null, false, '0559876543', CallPhase.active)
      ..name = 'شركة النخبة'
      ..answeredAt = DateTime.now().subtract(const Duration(seconds: 83))
      ..muted = true;
    await shot(t, const Size(500, 900), CallScreen(call: c2), 'active');
    PhoneEngine.I.account = null;
    await shot(t, const Size(500, 800), const LoginPage(), 'login');
    await t.pumpWidget(const SizedBox());
    await t.pump(const Duration(seconds: 1));
  });
}
