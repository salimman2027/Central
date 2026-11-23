// NQ Phone (Flutter) — صفحة تليفون NQ من السيرفر جوه WebView، والمكالمات WebRTC على السنترال بس.
// أي تحديث على السيرفر بيوصل التطبيق على طول. الصفحة بتلاقي قناة NQBridge وبتعمل window.NQiOS بنفسها.
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:share_plus/share_plus.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart';

/// عنوان صفحة التليفون على السيرفر
const String kServerUrl = 'https://3.89.245.93.sslip.io/phone/';

/// قناة native: جلسة الصوت والسبيكر وحساس القرب (iOS: AppDelegate.swift / Android: MainActivity.kt)
const MethodChannel audio = MethodChannel('nq/audio');
final FlutterLocalNotificationsPlugin notes = FlutterLocalNotificationsPlugin();

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await notes.initialize(const InitializationSettings(
    android: AndroidInitializationSettings('@mipmap/ic_launcher'),
    iOS: DarwinInitializationSettings(),
  ));
  runApp(const NQApp());
}

class NQApp extends StatelessWidget {
  const NQApp({super.key});
  @override
  Widget build(BuildContext context) =>
      const MaterialApp(title: 'NQ Phone', debugShowCheckedModeBanner: false, home: PhonePage());
}

class PhonePage extends StatefulWidget {
  const PhonePage({super.key});
  @override
  State<PhonePage> createState() => _PhonePageState();
}

class _PhonePageState extends State<PhonePage> with WidgetsBindingObserver {
  late final WebViewController web;
  final Uri server = Uri.parse(kServerUrl);
  bool inCall = false;
  bool offline = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WakelockPlus.enable(); // الشاشة ماتطفيش والتطبيق مفتوح (عشان المكالمات الواردة)
    [Permission.microphone, Permission.notification].request();

    final PlatformWebViewControllerCreationParams params = WebViewPlatform.instance is WebKitWebViewPlatform
        ? WebKitWebViewControllerCreationParams(allowsInlineMediaPlayback: true, mediaTypesRequiringUserAction: const <PlaybackMediaTypes>{})
        : const PlatformWebViewControllerCreationParams();
    web = WebViewController.fromPlatformCreationParams(
      params,
      // المايك/الكاميرا: نسمح لصفحة السيرفر بتاعنا بس
      onPermissionRequest: (req) => req.grant(),
    )
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0xFFF4F7F9))
      ..addJavaScriptChannel('NQBridge', onMessageReceived: (m) => onBridge(m.message))
      ..setNavigationDelegate(NavigationDelegate(
        onNavigationRequest: (req) async {
          final u = Uri.tryParse(req.url);
          if (u == null) return NavigationDecision.prevent;
          final s = u.scheme.toLowerCase();
          if (s == 'blob' || s == 'data' || s == 'about') return NavigationDecision.navigate;
          // صفحات السيرفر جوه التطبيق؛ واتساب/تليفون/مواقع تانية بره
          if ((s != 'http' && s != 'https') || (u.host != server.host && req.isMainFrame)) {
            await launchUrl(u, mode: LaunchMode.externalApplication);
            return NavigationDecision.prevent;
          }
          return NavigationDecision.navigate;
        },
        onWebResourceError: (err) {
          if (err.isForMainFrame == true) setState(() => offline = true);
        },
      ))
      ..loadRequest(server);

    final p = web.platform;
    if (p is AndroidWebViewController) {
      p.setMediaPlaybackRequiresUserGesture(false);
    }
    if (p is WebKitWebViewController) {
      p.setInspectable(true);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    if (s == AppLifecycleState.resumed) {
      WakelockPlus.enable();
      // رجع من الخلفية: يوصل بالسنترال تاني لو الاتصال وقع
      web.runJavaScript("try{ if (window.NQPhone && NQPhone.ua && !NQPhone.ua.isConnected()) NQPhone.ua.start(); }catch(e){}");
    }
  }

  Future<void> onBridge(String raw) async {
    Map<String, dynamic> m;
    try { m = jsonDecode(raw) as Map<String, dynamic>; } catch (_) { return; }
    final String t = '${m['t']}';
    final dynamic d = m['d'];
    try {
      switch (t) {
        case 'call':
          inCall = d == true;
          await audio.invokeMethod('call', inCall);
          break;
        case 'speaker':
          await audio.invokeMethod('speaker', d == true);
          break;
        case 'notify':
          final x = (d is Map) ? d : const {};
          await notes.show(DateTime.now().millisecondsSinceEpoch ~/ 1000 % 100000, '${x['title'] ?? 'NQ Phone'}', '${x['body'] ?? ''}',
              const NotificationDetails(
                android: AndroidNotificationDetails('calls', 'المكالمات', importance: Importance.max, priority: Priority.high),
                iOS: DarwinNotificationDetails(presentSound: true),
              ));
          break;
        case 'open':
          if (d is String) await launchUrl(Uri.parse(d), mode: LaunchMode.externalApplication);
          break;
        case 'share':
          if (d is String) await Share.share(d);
          break;
        case 'file': // تصدير Excel/PDF من الصفحة → حفظ/مشاركة
          if (d is Map && d['data'] is String) {
            final dir = await getTemporaryDirectory();
            final f = File('${dir.path}/${d['name']}');
            await f.writeAsBytes(base64Decode(d['data'] as String));
            await Share.shareXFiles([XFile(f.path)]);
          }
          break;
        case 'haptic':
          HapticFeedback.mediumImpact();
          break;
      }
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF4F7F9),
      body: SafeArea(
        bottom: false,
        child: offline ? _offline() : WebViewWidget(controller: web),
      ),
    );
  }

  Widget _offline() => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Text('📡', style: TextStyle(fontSize: 52)),
            const SizedBox(height: 8),
            const Text('مش قادر يوصل للسيرفر', textDirection: TextDirection.rtl, style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
            const SizedBox(height: 6),
            const Text('اتأكد إن النت شغال وجرّب تاني.', textDirection: TextDirection.rtl),
            const SizedBox(height: 18),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: const Color(0xFF14B8A6)),
              onPressed: () { setState(() => offline = false); web.loadRequest(server); },
              child: const Text('حاول تاني'),
            ),
          ]),
        ),
      );
}
