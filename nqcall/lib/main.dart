import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:window_manager/window_manager.dart';

import 'core.dart';
import 'native.dart';
import 'phone.dart';
import 'ui/call_screen.dart';
import 'ui/home.dart';
import 'ui/login_page.dart';
import 'ui/theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Prefs.init();
  if (Native.isDesktop) {
    await windowManager.ensureInitialized();
    const opts = WindowOptions(size: Size(1100, 720), minimumSize: Size(380, 600), center: true, title: 'NQ Call');
    windowManager.waitUntilReadyToShow(opts, () async {
      await windowManager.show();
      await windowManager.focus();
    });
  }
  runApp(const NQApp());
  final a = Account.load();
  if (a != null) PhoneEngine.I.signIn(a);
  Native.askPermissions();
}

class NQApp extends StatefulWidget {
  const NQApp({super.key});
  static final themeMode = ValueNotifier<ThemeMode>(_loadTheme());
  static ThemeMode _loadTheme() => switch (Prefs.str('theme', 'system')) { 'light' => ThemeMode.light, 'dark' => ThemeMode.dark, _ => ThemeMode.system };
  static Future<void> setTheme(String t) async {
    await Prefs.setStr('theme', t);
    themeMode.value = _loadTheme();
  }

  @override
  State<NQApp> createState() => _NQAppState();
}

class _NQAppState extends State<NQApp> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    // رجع للتطبيق والخط واقع؟ نتصل تاني على طول
    final e = PhoneEngine.I;
    if (s == AppLifecycleState.resumed && e.account != null && e.line != LineState.online && e.current == null) e.reconnect();
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: NQApp.themeMode,
      builder: (_, mode, __) => MaterialApp(
        title: 'NQ Call',
        debugShowCheckedModeBanner: false,
        locale: const Locale('ar'),
        supportedLocales: const [Locale('ar'), Locale('en')],
        localizationsDelegates: const [GlobalMaterialLocalizations.delegate, GlobalWidgetsLocalizations.delegate, GlobalCupertinoLocalizations.delegate],
        theme: buildTheme(Brightness.light),
        darkTheme: buildTheme(Brightness.dark),
        themeMode: mode,
        home: const _Root(),
      ),
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
        if (e.account == null) return const LoginPage();
        return Stack(children: [
          const HomeShell(),
          if (e.current != null) Positioned.fill(child: CallScreen(call: e.current!)),
        ]);
      },
    );
  }
}

/// ويندوز: مفيش حاجة تانية — بس عشان الـ import يفضل مستخدم في كل الأنظمة
bool get isMobile => Platform.isAndroid || Platform.isIOS;
