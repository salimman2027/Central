import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:window_manager/window_manager.dart';

import 'core.dart';
import 'native.dart';
import 'phone.dart';
import 'store.dart';
import 'ui/call_screen.dart';
import 'ui/login_page.dart';
import 'ui/main_screen.dart';
import 'ui/theme.dart';
import 'win.dart';

Future<void> main(List<String> args) async {
  WidgetsFlutterBinding.ensureInitialized();
  await Prefs.init();
  // نسخة واحدة بس: لو البرنامج مفتوح، نبعتله الرقم (من الـ CRM) ونقفل
  if (!await Win.ensureSingle(args)) exit(0);
  await Win.init();
  runApp(const NQApp());
  final a = Account.load();
  if (a != null) PhoneEngine.I.signIn(a);
  Native.askPermissions();
}

class NQApp extends StatefulWidget {
  const NQApp({super.key});
  static final navKey = GlobalKey<NavigatorState>();
  @override
  State<NQApp> createState() => _NQAppState();
}

class _NQAppState extends State<NQApp> with WidgetsBindingObserver, WindowListener {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    if (Native.isDesktop) windowManager.addListener(this);
    PhoneEngine.I.addListener(_onEngine);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    if (Native.isDesktop) windowManager.removeListener(this);
    PhoneEngine.I.removeListener(_onEngine);
    super.dispose();
  }

  bool _hadCall = false;
  void _onEngine() {
    // مكالمة جت والبرنامج في الوضع المصغّر: نكبّره عشان شاشة المكالمة
    final has = PhoneEngine.I.current != null;
    if (has && !_hadCall && Win.compact.value) Win.toggleCompact(false);
    _hadCall = has;
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    // رجع للتطبيق والخط واقع؟ نتصل تاني على طول
    final e = PhoneEngine.I;
    if (s == AppLifecycleState.resumed && e.account != null && e.line != LineState.online && e.current == null) e.reconnect();
  }

  // ---------- الشباك (ويندوز) ----------
  @override
  void onWindowResized() => Win.rememberSize();

  @override
  Future<void> onWindowClose() async {
    final ctx = NQApp.navKey.currentContext;
    if (ctx == null || PhoneEngine.I.account == null) {
      await windowManager.destroy();
      return;
    }
    final inCall = PhoneEngine.I.current != null;
    final v = await showDialog<String>(
      context: ctx,
      builder: (c) => AlertDialog(
        title: Text(inCall ? 'فيه مكالمة شغالة!' : 'تقفل البرنامج؟'),
        content: Text(inCall ? 'لو قفلت هتقفل المكالمة.' : 'لو قفلته مش هتوصلك مكالمات لحد ما تفتحه تاني.\nالأحسن تصغّره بس.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, 'cancel'), child: const Text('رجوع')),
          OutlinedButton(onPressed: () => Navigator.pop(c, 'quit'), style: OutlinedButton.styleFrom(foregroundColor: kRed), child: const Text('اقفل خالص')),
          FilledButton(onPressed: () => Navigator.pop(c, 'min'), child: const Text('صغّره بس')),
        ],
      ),
    );
    if (v == 'min') await windowManager.minimize();
    if (v == 'quit') {
      PhoneEngine.I.hangup();
      await windowManager.destroy();
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      navigatorKey: NQApp.navKey,
      title: 'NQ Phone',
      debugShowCheckedModeBanner: false,
      locale: const Locale('ar'),
      supportedLocales: const [Locale('ar'), Locale('en')],
      localizationsDelegates: const [GlobalMaterialLocalizations.delegate, GlobalWidgetsLocalizations.delegate, GlobalCupertinoLocalizations.delegate],
      theme: buildTheme(Brightness.light),
      themeMode: ThemeMode.light,
      home: const _Root(),
    );
  }
}

class _Root extends StatelessWidget {
  const _Root();
  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: PhoneEngine.I,
      builder: (context, _) {
        final e = PhoneEngine.I;
        if (e.account == null) {
          HomeData.I.stop();
          return const LoginPage();
        }
        return Stack(children: [
          const MainScreen(),
          if (e.current != null) Positioned.fill(child: CallScreen(call: e.current!)),
        ]);
      },
    );
  }
}

bool get isMobile => Platform.isAndroid || Platform.isIOS;
