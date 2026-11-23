// NQ Phone (Flutter) — صفحة تليفون NQ من السيرفر جوه WebView، والمكالمات WebRTC على السنترال بس.
// أي تحديث على السيرفر بيوصل التطبيق على طول. الجسر window.NQiOS.post(type, data) هو نفسه اللي الصفحة بتستخدمه.
import 'dart:collection';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:share_plus/share_plus.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

/// عنوان صفحة التليفون على السيرفر
const String kServerUrl = 'https://3.89.245.93.sslip.io/phone/';
const String kAppVersion = '1.0.0';

/// قناة native صغيرة: جلسة الصوت والسبيكر وحساس القرب (iOS: AppDelegate.swift / Android: MainActivity.kt)
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
  Widget build(BuildContext context) => const MaterialApp(
        title: 'NQ Phone',
        debugShowCheckedModeBanner: false,
        home: PhonePage(),
      );
}

class PhonePage extends StatefulWidget {
  const PhonePage({super.key});
  @override
  State<PhonePage> createState() => _PhonePageState();
}

class _PhonePageState extends State<PhonePage> with WidgetsBindingObserver {
  InAppWebViewController? web;
  PullToRefreshController? ptr;
  bool inCall = false;
  bool offline = false;
  final Uri server = Uri.parse(kServerUrl);

  /// الجسر + تحويل ملفات blob (تصدير Excel/PDF من الصفحة) لملف نشاركه
  static const String bridgeJs = r'''
window.NQiOS = {
  version: (Platform_OS) + ' ' + APP_VER_FLUTTER,
  post: function (t, d) { try { window.flutter_inappwebview.callHandler('nq', t, d === undefined ? null : d); } catch (e) {} }
};
document.addEventListener('click', function (e) {
  var a = e.target && e.target.closest ? e.target.closest('a[download]') : null;
  if (!a || !/^(blob|data):/.test(a.href)) return;
  e.preventDefault();
  fetch(a.href).then(function (r) { return r.blob(); }).then(function (b) {
    var fr = new FileReader();
    fr.onload = function () { NQiOS.post('file', { name: a.getAttribute('download') || 'file', data: String(fr.result).split(',')[1] }); };
    fr.readAsDataURL(b);
  });
}, true);
''';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WakelockPlus.enable(); // الشاشة ماتطفيش والتطبيق مفتوح (عشان المكالمات الواردة)
    [Permission.microphone, Permission.notification].request();
    ptr = PullToRefreshController(
      settings: PullToRefreshSettings(color: const Color(0xFF14B8A6)),
      onRefresh: () async {
        if (inCall) { ptr?.endRefreshing(); return; } // مانعملش ريفريش وسط مكالمة
        await web?.reload();
      },
    );
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
      web?.evaluateJavascript(source: "try{ if (window.NQPhone && NQPhone.ua && !NQPhone.ua.isConnected()) NQPhone.ua.start(); }catch(e){}");
    }
  }

  Future<dynamic> onBridge(List<dynamic> args) async {
    final String t = args.isNotEmpty ? '${args[0]}' : '';
    final dynamic d = args.length > 1 ? args[1] : null;
    switch (t) {
      case 'call':
        inCall = d == true;
        await audio.invokeMethod('call', inCall).catchError((_) {});
        break;
      case 'speaker':
        await audio.invokeMethod('speaker', d == true).catchError((_) {});
        break;
      case 'notify':
        final m = (d is Map) ? d : {};
        await notes.show(DateTime.now().millisecondsSinceEpoch ~/ 1000 % 100000, '${m['title'] ?? 'NQ Phone'}', '${m['body'] ?? ''}',
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
      case 'file':
        if (d is Map && d['data'] is String) {
          final dir = await getTemporaryDirectory();
          final f = File('${dir.path}/${d['name']}');
          await f.writeAsBytes(base64Decode(d['data']));
          await Share.shareXFiles([XFile(f.path)]);
        }
        break;
      case 'haptic':
        HapticFeedback.mediumImpact();
        break;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF4F7F9),
      body: SafeArea(
        bottom: false,
        child: offline
            ? _offline()
            : InAppWebView(
                initialUrlRequest: URLRequest(url: WebUri(kServerUrl)),
                pullToRefreshController: ptr,
                initialUserScripts: UnmodifiableListView([
                  UserScript(
                    source: bridgeJs
                        .replaceAll('(Platform_OS)', Platform.isIOS ? "'iOS'" : "'Android'")
                        .replaceAll('APP_VER_FLUTTER', "'$kAppVersion'"),
                    injectionTime: UserScriptInjectionTime.AT_DOCUMENT_START,
                  ),
                ]),
                initialSettings: InAppWebViewSettings(
                  javaScriptEnabled: true,
                  mediaPlaybackRequiresUserGesture: false,
                  allowsInlineMediaPlayback: true,
                  useShouldOverrideUrlLoading: true,
                  useOnDownloadStart: true,
                  transparentBackground: true,
                  supportZoom: false,
                  disallowOverScroll: false,
                  isInspectable: true,
                ),
                onWebViewCreated: (c) {
                  web = c;
                  c.addJavaScriptHandler(handlerName: 'nq', callback: onBridge);
                },
                // المايك: نسمح لصفحة السيرفر بتاعنا بس
                onPermissionRequest: (c, req) async => PermissionResponse(
                  resources: req.resources,
                  action: req.origin.host == server.host ? PermissionResponseAction.GRANT : PermissionResponseAction.PROMPT,
                ),
                // صفحات السيرفر جوه التطبيق؛ واتساب/تليفون/مواقع تانية بره
                shouldOverrideUrlLoading: (c, nav) async {
                  final u = nav.request.url;
                  if (u == null) return NavigationActionPolicy.CANCEL;
                  final s = u.scheme.toLowerCase();
                  if (s == 'blob' || s == 'data' || s == 'about') return NavigationActionPolicy.ALLOW;
                  if ((s != 'http' && s != 'https') || (u.host != server.host && nav.isForMainFrame)) {
                    await launchUrl(Uri.parse(u.toString()), mode: LaunchMode.externalApplication);
                    return NavigationActionPolicy.CANCEL;
                  }
                  return NavigationActionPolicy.ALLOW;
                },
                onDownloadStartRequest: (c, req) async {
                  await launchUrl(Uri.parse(req.url.toString()), mode: LaunchMode.externalApplication);
                },
                onLoadStop: (c, u) => ptr?.endRefreshing(),
                onReceivedError: (c, req, err) {
                  if (req.isForMainFrame == true) setState(() => offline = true);
                },
                onCreateWindow: (c, act) async {
                  final u = act.request.url;
                  if (u != null) {
                    if (u.host == server.host) { c.loadUrl(urlRequest: URLRequest(url: u)); }
                    else { await launchUrl(Uri.parse(u.toString()), mode: LaunchMode.externalApplication); }
                  }
                  return false;
                },
              ),
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
              onPressed: () => setState(() => offline = false),
              child: const Text('حاول تاني'),
            ),
          ]),
        ),
      );
}
