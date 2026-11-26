// لوحة التحكم جوه البرنامج: تسمع المكالمات، التقارير، التحليل… من غير ما تفتح المتصفح
import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:webview_flutter/webview_flutter.dart' as mob;
import 'package:webview_windows/webview_windows.dart' as win;
import 'package:window_manager/window_manager.dart';

import '../core.dart';
import '../native.dart';
import 'theme.dart';

String get dashboardUrl => 'https://${Prefs.host}/calls/';

Future<void> openDashboard(BuildContext context) async {
  Size? before;
  bool wasMax = false;
  if (Native.isDesktop) {
    // اللوحة محتاجة شاشة عريضة: نكبّر الشباك ونرجّعه زي ما كان بعد ما تقفلها
    try {
      wasMax = await windowManager.isMaximized();
      before = await windowManager.getSize();
      if (!wasMax && before.width < 1200) {
        await windowManager.setSize(const Size(1360, 860), animate: true);
        await windowManager.center(animate: true);
      }
    } catch (_) {}
  }
  if (!context.mounted) return;
  await Navigator.of(context).push(MaterialPageRoute(builder: (_) => const DashboardPage()));
  if (Native.isDesktop && before != null && !wasMax) {
    try {
      if (await windowManager.isMaximized()) await windowManager.unmaximize();
      await windowManager.setSize(before, animate: true);
    } catch (_) {}
  }
}

class DashboardPage extends StatefulWidget {
  const DashboardPage({super.key});
  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  // ويندوز: WebView2 (نفس محرك Edge) — أندرويد/آيفون: WebView بتاع النظام
  win.WebviewController? _w;
  mob.WebViewController? _m;
  bool _loading = true, _ready = false;
  String _err = '';
  final List<StreamSubscription> _subs = [];

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    try {
      if (Platform.isWindows) {
        final ver = await win.WebviewController.getWebViewVersion();
        if (ver == null) {
          setState(() => _err = 'لوحة التحكم محتاجة "Microsoft Edge WebView2" — مش متسطّب على الجهاز ده.');
          return;
        }
        if (!_envReady) {
          final dir = await getApplicationSupportDirectory();
          await win.WebviewController.initializeEnvironment(userDataPath: '${dir.path}${Platform.pathSeparator}webview');
          _envReady = true;
        }
        final c = win.WebviewController();
        await c.initialize();
        await c.setBackgroundColor(kBg);
        await c.setPopupWindowPolicy(win.WebviewPopupWindowPolicy.sameWindow);
        _subs.add(c.loadingState.listen((s) {
          if (mounted) setState(() => _loading = s == win.LoadingState.loading);
        }));
        await c.loadUrl(dashboardUrl);
        _w = c;
      } else if (Platform.isAndroid || Platform.isIOS) {
        final c = mob.WebViewController()
          ..setJavaScriptMode(mob.JavaScriptMode.unrestricted)
          ..setBackgroundColor(kBg)
          ..setNavigationDelegate(mob.NavigationDelegate(
            onPageStarted: (_) {
              if (mounted) setState(() => _loading = true);
            },
            onPageFinished: (_) {
              if (mounted) setState(() => _loading = false);
            },
            onWebResourceError: (e) {
              if (mounted && e.isForMainFrame == true) setState(() => _err = 'مش قادر يفتح اللوحة — اتأكد من النت');
            },
          ))
          ..loadRequest(Uri.parse(dashboardUrl));
        _m = c;
      } else {
        await launchUrl(Uri.parse(dashboardUrl), mode: LaunchMode.externalApplication);
        if (mounted) Navigator.pop(context);
        return;
      }
      if (mounted) setState(() => _ready = true);
    } catch (e) {
      if (mounted) setState(() => _err = 'مش قادر يفتح اللوحة: $e');
    }
  }

  static bool _envReady = false;

  @override
  void dispose() {
    for (final s in _subs) {
      s.cancel();
    }
    _w?.dispose();
    super.dispose();
  }

  Future<void> _reload() async {
    setState(() => _err = '');
    if (_w != null) await _w!.reload();
    if (_m != null) await _m!.reload();
  }

  Future<void> _back() async {
    if (_w != null) {
      await _w!.goBack();
    } else if (_m != null && await _m!.canGoBack()) {
      await _m!.goBack();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Column(children: [
        // شريط علوي بنفس هوية البرنامج
        Container(
          decoration: const BoxDecoration(gradient: kHeaderGradient),
          child: SafeArea(
            bottom: false,
            child: Column(children: [
              if (Native.isDesktop) const _DragBar(),
              Padding(
                padding: const EdgeInsets.fromLTRB(10, 4, 10, 10),
                child: Row(children: [
                  _HBtn(icon: Icons.arrow_forward_rounded, tip: 'رجوع للتليفون', onTap: () => Navigator.pop(context)),
                  const SizedBox(width: 8),
                  Image.asset('assets/logo.png', width: 30, height: 30),
                  const SizedBox(width: 10),
                  const Text('لوحة التحكم', style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w800)),
                  const SizedBox(width: 10),
                  if (_loading && _err.isEmpty) const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)),
                  const Spacer(),
                  _HBtn(icon: Icons.undo_rounded, tip: 'الصفحة اللي قبلها', onTap: _back),
                  const SizedBox(width: 6),
                  _HBtn(icon: Icons.refresh_rounded, tip: 'تحديث', onTap: _reload),
                  const SizedBox(width: 6),
                  _HBtn(
                    icon: Icons.open_in_new_rounded,
                    tip: 'افتحها في المتصفح',
                    onTap: () => launchUrl(Uri.parse(dashboardUrl), mode: LaunchMode.externalApplication),
                  ),
                  if (Native.isDesktop) ...[
                    const SizedBox(width: 6),
                    _HBtn(
                      icon: Icons.fullscreen_rounded,
                      tip: 'ملء الشاشة',
                      onTap: () async => (await windowManager.isMaximized()) ? windowManager.unmaximize() : windowManager.maximize(),
                    ),
                  ],
                ]),
              ),
            ]),
          ),
        ),
        Expanded(child: _body()),
      ]),
    );
  }

  Widget _body() {
    if (_err.isNotEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.wifi_off_rounded, size: 46, color: kMuted),
            const SizedBox(height: 12),
            Text(_err, textAlign: TextAlign.center, style: const TextStyle(fontSize: 15)),
            const SizedBox(height: 16),
            Wrap(spacing: 10, children: [
              FilledButton.icon(onPressed: _reload, icon: const Icon(Icons.refresh_rounded), label: const Text('جرّب تاني')),
              OutlinedButton.icon(
                onPressed: () => launchUrl(Uri.parse(dashboardUrl), mode: LaunchMode.externalApplication),
                icon: const Icon(Icons.open_in_new_rounded),
                label: const Text('افتحها في المتصفح'),
              ),
            ]),
          ]),
        ),
      );
    }
    if (!_ready) return const Center(child: CircularProgressIndicator());
    if (_w != null) return win.Webview(_w!);
    if (_m != null) return mob.WebViewWidget(controller: _m!);
    return const SizedBox.shrink();
  }
}

class _HBtn extends StatelessWidget {
  final IconData icon;
  final String tip;
  final VoidCallback onTap;
  const _HBtn({required this.icon, required this.tip, required this.onTap});
  @override
  Widget build(BuildContext context) => Tooltip(
        message: tip,
        child: Material(
          color: Colors.white.withValues(alpha: .12),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(11), side: const BorderSide(color: Colors.white24)),
          child: InkWell(
            borderRadius: BorderRadius.circular(11),
            onTap: onTap,
            child: SizedBox(width: 40, height: 38, child: Icon(icon, color: Colors.white, size: 20)),
          ),
        ),
      );
}

/// شريط للسحب + أزرار الشباك فوق اللوحة (لأن الشباك من غير إطار)
class _DragBar extends StatelessWidget {
  const _DragBar();
  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.ltr,
      child: SizedBox(
        height: 34,
        child: Row(children: [
          const Expanded(child: DragToMoveArea(child: SizedBox.expand())),
          for (final b in [
            (Icons.remove_rounded, () => windowManager.minimize()),
            (Icons.crop_square_rounded, () async => (await windowManager.isMaximized()) ? windowManager.unmaximize() : windowManager.maximize()),
            (Icons.close_rounded, () => windowManager.close()),
          ])
            InkWell(
              onTap: b.$2,
              hoverColor: b.$1 == Icons.close_rounded ? kRed : Colors.white.withValues(alpha: .12),
              child: SizedBox(width: 42, height: 34, child: Icon(b.$1, size: 17, color: Colors.white)),
            ),
        ]),
      ),
    );
  }
}
