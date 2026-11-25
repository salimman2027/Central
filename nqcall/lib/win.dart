// الشباك على الكمبيوتر: شريط عنوان خاص، تثبيت فوق النوافذ، الوضع المصغّر،
// نسخة واحدة بس من البرنامج، والاتصال من الـ CRM (روابط tel: و nqcall:)
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:window_manager/window_manager.dart';

import 'core.dart';
import 'native.dart';
import 'ui/theme.dart';

class Win {
  static final compact = ValueNotifier<bool>(false);
  static final pinned = ValueNotifier<bool>(Prefs.flag('pinned'));
  static final incomingNumber = ValueNotifier<String>(''); // رقم جاي من الـ CRM
  static Size _full = const Size(500, 900);
  static const _port = 47321;
  static ServerSocket? _server;

  static Size _startSize() {
    final w = Prefs.dbl('winW', 500), h = Prefs.dbl('winH', 0);
    return Size(w.clamp(420, 1400).toDouble(), h > 0 ? h.clamp(560, 1600).toDouble() : 920);
  }

  /// قبل runApp: لو البرنامج شغال أصلًا نبعتله الرقم ونقفل النسخة دي
  static Future<bool> ensureSingle(List<String> args) async {
    if (!Native.isDesktop) return true;
    final num = parseLink(args);
    try {
      final s = await Socket.connect(InternetAddress.loopbackIPv4, _port, timeout: const Duration(milliseconds: 400));
      s.write(jsonEncode({'show': true, 'number': num}));
      await s.flush();
      await s.close();
      return false; // فيه نسخة شغالة
    } catch (_) {}
    try {
      _server = await ServerSocket.bind(InternetAddress.loopbackIPv4, _port);
      _server!.listen((c) {
        utf8.decoder.bind(c).join().then((txt) async {
          try {
            final j = jsonDecode(txt) as Map;
            await bringToFront();
            final n = '${j['number'] ?? ''}';
            if (n.isNotEmpty) incomingNumber.value = n;
          } catch (_) {}
        });
      });
    } catch (_) {}
    if (num.isNotEmpty) incomingNumber.value = num;
    return true;
  }

  /// tel:+9665... / callto:... / nqcall:0555... / nqcall://dial/0555...
  static String parseLink(List<String> args) {
    for (final a in args) {
      final m = RegExp(r'^(?:tel|callto|sip|nqcall):(?://)?(?:dial/)?(.+)$', caseSensitive: false).firstMatch(a.trim());
      if (m != null) return cleanPhone(Uri.decodeComponent(m.group(1)!.split('@').first));
      if (RegExp(r'^\+?[\d\s\-()]{5,}$').hasMatch(a)) return cleanPhone(a);
    }
    return '';
  }

  static Future<void> init() async {
    if (!Native.isDesktop) return;
    await windowManager.ensureInitialized();
    final size = _startSize();
    _full = size;
    final opts = WindowOptions(
      size: size,
      minimumSize: const Size(420, 560),
      center: true,
      title: 'NQ Phone',
      titleBarStyle: TitleBarStyle.hidden,
      backgroundColor: Colors.transparent,
    );
    await windowManager.waitUntilReadyToShow(opts, () async {
      await windowManager.setPreventClose(true);
      if (pinned.value) await windowManager.setAlwaysOnTop(true);
      await windowManager.show();
      await windowManager.focus();
    });
  }

  static Future<void> bringToFront() async {
    if (!Native.isDesktop) return;
    try {
      if (await windowManager.isMinimized()) await windowManager.restore();
      await windowManager.show();
      await windowManager.focus();
    } catch (_) {}
  }

  static Future<void> togglePin() async {
    pinned.value = !pinned.value;
    await Prefs.setFlag('pinned', pinned.value);
    if (Native.isDesktop) await windowManager.setAlwaysOnTop(pinned.value);
  }

  /// الوضع المصغّر: شريط صغير فيه الحالة وخانة الرقم وزرار الاتصال
  static Future<void> toggleCompact([bool? on]) async {
    final v = on ?? !compact.value;
    if (v == compact.value) return;
    if (!Native.isDesktop) {
      compact.value = false;
      return;
    }
    if (v) {
      _full = await windowManager.getSize();
      await windowManager.setMinimumSize(const Size(420, 64));
      await windowManager.setSize(Size(_full.width, 64));
      await windowManager.setAlwaysOnTop(true);
      compact.value = true;
    } else {
      compact.value = false;
      await windowManager.setSize(_full);
      await windowManager.setMinimumSize(const Size(420, 560));
      await windowManager.setAlwaysOnTop(pinned.value);
    }
  }

  static Future<void> rememberSize() async {
    if (!Native.isDesktop || compact.value) return;
    try {
      final s = await windowManager.getSize();
      await Prefs.setDbl('winW', s.width);
      await Prefs.setDbl('winH', s.height);
    } catch (_) {}
  }
}

/// شريط العنوان: الشعار والاسم شمال، وأزرار الشباك يمين
class TitleBar extends StatelessWidget {
  const TitleBar({super.key});

  @override
  Widget build(BuildContext context) {
    if (!Native.isDesktop) return const SizedBox.shrink();
    return Directionality(
      textDirection: TextDirection.ltr,
      child: SizedBox(
        height: 44,
        child: Row(children: [
          Expanded(
            child: DragToMoveArea(
              child: Padding(
                padding: const EdgeInsets.only(left: 14),
                child: Row(children: [
                  Container(
                    width: 26,
                    height: 26,
                    decoration: BoxDecoration(color: Colors.white.withValues(alpha: .14), borderRadius: BorderRadius.circular(7), border: Border.all(color: Colors.white54)),
                    child: const Center(child: Text('N', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 14))),
                  ),
                  const SizedBox(width: 10),
                  const Text('NQ Phone', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 15.5)),
                ]),
              ),
            ),
          ),
          _Btn(icon: Icons.close_fullscreen_rounded, tip: 'وضع مصغّر (Ctrl+M)', onTap: () => Win.toggleCompact()),
          ValueListenableBuilder<bool>(
            valueListenable: Win.pinned,
            builder: (_, p, __) => _Btn(icon: p ? Icons.push_pin : Icons.push_pin_outlined, tip: p ? 'إلغاء التثبيت فوق النوافذ' : 'تثبيت فوق النوافذ', onTap: Win.togglePin, on: p),
          ),
          _Btn(icon: Icons.remove_rounded, tip: 'تصغير', onTap: () => windowManager.minimize()),
          _Btn(
            icon: Icons.crop_square_rounded,
            tip: 'تكبير',
            onTap: () async => (await windowManager.isMaximized()) ? windowManager.unmaximize() : windowManager.maximize(),
          ),
          _Btn(icon: Icons.close_rounded, tip: 'قفل', onTap: () => windowManager.close(), danger: true),
          const SizedBox(width: 4),
        ]),
      ),
    );
  }
}

class _Btn extends StatelessWidget {
  final IconData icon;
  final String tip;
  final VoidCallback onTap;
  final bool on, danger;
  const _Btn({required this.icon, required this.tip, required this.onTap, this.on = false, this.danger = false});
  @override
  Widget build(BuildContext context) => Tooltip(
        message: tip,
        waitDuration: const Duration(milliseconds: 500),
        child: InkWell(
          onTap: onTap,
          hoverColor: danger ? kRed : Colors.white.withValues(alpha: .12),
          borderRadius: BorderRadius.circular(8),
          child: SizedBox(width: 40, height: 36, child: Icon(icon, size: 18, color: on ? const Color(0xFFB7F5CF) : Colors.white)),
        ),
      );
}
