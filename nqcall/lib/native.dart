// حاجات خاصة بكل نظام: خدمة أندرويد اللي بتخلي التطبيق صاحي، شاشة المكالمة الواردة، شباك الويندوز
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:window_manager/window_manager.dart';

import 'core.dart';

class Native {
  static const _ch = MethodChannel('nq/native');
  static bool get isDesktop => Platform.isWindows || Platform.isLinux || Platform.isMacOS;

  static Future<void> _call(String m, [Map<String, dynamic>? a]) async {
    try {
      await _ch.invokeMethod(m, a);
    } catch (e) {
      logd('native $m: $e');
    }
  }

  /// أول تشغيل: المايك + الإشعارات (الموبايل)
  static Future<void> askPermissions() async {
    if (!(Platform.isAndroid || Platform.isIOS)) return;
    try {
      await [Permission.microphone, Permission.notification].request();
    } catch (_) {}
  }

  /// أندرويد: إشعار ثابت "متصل بالسنترال" عشان النظام مايقفلش التطبيق
  static Future<void> keepAlive(bool on, {String text = ''}) async {
    if (!Platform.isAndroid) return;
    await _call(on ? 'startService' : 'stopService', {'text': text});
  }

  /// مكالمة من شريحة الموبايل (مش السنترال)
  static Future<bool> simCall(String number) async {
    if (Platform.isIOS) {
      return launchUrl(Uri(scheme: 'tel', path: number));
    }
    if (!Platform.isAndroid) return false;
    try {
      await Permission.phone.request();
    } catch (_) {}
    try {
      return await _ch.invokeMethod<bool>('simCall', {'number': number}) ?? false;
    } catch (e) {
      logd('simCall $e');
      return false;
    }
  }

  /// أندرويد: الموظف في مكالمة شريحة دلوقتي؟
  static Future<bool> inSimCall() async {
    if (!Platform.isAndroid) return false;
    try {
      return await _ch.invokeMethod<bool>('inSimCall') ?? false;
    } catch (_) {
      return false;
    }
  }

  static Future<void> ignoreBatteryOptimizations() async {
    if (!Platform.isAndroid) return;
    await _call('batteryExempt');
  }

  /// مكالمة واردة: الموبايل يصحى وتظهر الشاشة، الكمبيوتر الشباك يطلع قدام
  static Future<void> incoming(String title, String body) async {
    if (Platform.isAndroid) await _call('incoming', {'title': title, 'body': body});
    if (isDesktop) {
      try {
        await windowManager.show();
        await windowManager.restore();
        await windowManager.setAlwaysOnTop(true);
        await windowManager.focus();
      } catch (_) {}
    }
  }

  static Future<void> incomingDone() async {
    if (Platform.isAndroid) await _call('cancelIncoming');
    if (isDesktop) {
      try {
        await windowManager.setAlwaysOnTop(false);
      } catch (_) {}
    }
  }
}
