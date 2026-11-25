// محرك المكالمات: SIP على WebSocket (نفس السنترال) — من غير أي صفحة ويب
import 'dart:async';
import 'dart:io';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:sip_ua/sip_ua.dart';

import 'core.dart';
import 'native.dart';
import 'ringer.dart';

enum LineState { off, connecting, online, failed }

enum CallPhase { dialing, ringing, incoming, active, ended }

/// نتيجة المكالمة اللي مكملتش
class Outcome {
  final String key, icon, title, detail;
  final bool retry;
  const Outcome(this.key, this.icon, this.title, {this.detail = '', this.retry = false});
}

class ActiveCall {
  final dynamic call; // Call بتاع sip_ua
  final bool incoming;
  final String number;
  String name = '';
  CallPhase phase;
  DateTime? answeredAt, ringAt;
  final DateTime started = DateTime.now();
  bool muted = false, speaker = false, held = false;
  String dtmf = '';
  ActiveCall(this.call, this.incoming, this.number, this.phase);

  int get seconds => answeredAt == null ? 0 : DateTime.now().difference(answeredAt!).inSeconds;
}

/// بيقرا حقل من أوبجكت sip_ua بأكتر من اسم (الأسماء اتغيرت بين النسخ)
dynamic _f(dynamic o, List<String> names) {
  for (final n in names) {
    try {
      switch (n) {
        case 'remote_identity':
          return o.remote_identity;
        case 'remoteIdentity':
          return o.remoteIdentity;
        case 'remote_display_name':
          return o.remote_display_name;
        case 'remoteDisplayName':
          return o.remoteDisplayName;
        case 'status_code':
          return o.status_code;
        case 'statusCode':
          return o.statusCode;
        case 'reason_phrase':
          return o.reason_phrase;
        case 'reasonPhrase':
          return o.reasonPhrase;
        case 'cause':
          return o.cause;
        case 'direction':
          return o.direction;
        case 'id':
          return o.id;
      }
    } catch (_) {}
  }
  return null;
}

String _enumName(dynamic e) => e.toString().split('.').last.toUpperCase();

class PhoneEngine extends ChangeNotifier implements SipUaHelperListener {
  PhoneEngine._();
  static final PhoneEngine I = PhoneEngine._();

  SIPUAHelper? _ua;
  Account? account;
  LineState line = LineState.off;
  String lineText = 'مش متصل';
  ActiveCall? current;
  Outcome? lastOutcome;
  String lastNumber = '';
  final List<String> _missed = [];
  Timer? _watch, _tick;
  StreamSubscription? _net;
  int _restarts = 0;
  DateTime _lastStart = DateTime(2000);

  List<String> get missed => List.unmodifiable(_missed);

  // ---------------- الدخول والاتصال بالسنترال ----------------
  Future<void> signIn(Account a) async {
    account = a;
    await a.save();
    _connect();
    _watch?.cancel();
    // حارس: لو الخط وقع لأي سبب يرجع يتصل لوحده
    _watch = Timer.periodic(const Duration(seconds: 15), (_) {
      if (account == null) return;
      final stuck = line == LineState.connecting && DateTime.now().difference(_lastStart).inSeconds > 25;
      if (line == LineState.off || line == LineState.failed || stuck) {
        if (current == null) _connect();
      }
    });
    _net ??= Connectivity().onConnectivityChanged.listen((_) {
      // الشبكة اتغيرت (واي فاي ↔ داتا): نعيد الاتصال على طول
      if (account != null && current == null) Future.delayed(const Duration(seconds: 1), _connect);
    });
  }

  void _connect() {
    final a = account;
    if (a == null) return;
    _lastStart = DateTime.now();
    _restarts++;
    try {
      _ua?.removeSipUaHelperListener(this);
      _ua?.stop();
    } catch (_) {}
    final host = Prefs.host;
    final ua = SIPUAHelper();
    ua.addSipUaHelperListener(this);
    final s = UaSettings();
    s.webSocketUrl = 'wss://$host/ws';
    s.webSocketSettings.allowBadCertificate = false;
    s.uri = 'sip:${a.ext}@$host';
    s.authorizationUser = a.ext;
    s.password = a.password;
    s.displayName = a.ext;
    s.userAgent = 'NQ Call $kAppVersion (${Platform.operatingSystem})';
    s.dtmfMode = DtmfMode.RFC2833;
    s.transportType = TransportType.WS;
    s.iceServers = [
      {'urls': 'stun:stun.l.google.com:19302'}
    ];
    _ua = ua;
    _setLine(LineState.connecting, 'بيتصل بالسنترال…');
    try {
      ua.start(s);
    } catch (e) {
      _setLine(LineState.failed, 'مش قادر يتصل: $e');
    }
  }

  Future<void> signOut() async {
    account = null;
    _watch?.cancel();
    _watch = null;
    try {
      _ua?.unregister();
    } catch (_) {}
    try {
      _ua?.stop();
    } catch (_) {}
    _ua = null;
    await Account.clear();
    await Native.keepAlive(false);
    _setLine(LineState.off, 'مش متصل');
  }

  /// "اتصل تاني" يدوي من شريط الحالة
  void reconnect() => _connect();

  void _setLine(LineState s, String t) {
    line = s;
    lineText = t;
    notifyListeners();
  }

  // ---------------- المكالمات ----------------
  Future<String?> dial(String raw) async {
    final number = pbxNumber(cleanPhone(raw));
    if (number.isEmpty) return 'اكتب الرقم الأول';
    if (current != null) return 'فيه مكالمة شغالة';
    final ua = _ua;
    if (ua == null || line != LineState.online) {
      if (account != null) _connect();
      return 'مش متصل بالسنترال — بيحاول يتصل… جرّب كمان ثانيتين';
    }
    lastOutcome = null;
    lastNumber = raw;
    try {
      final stream = await navigator.mediaDevices.getUserMedia({'audio': true, 'video': false});
      final target = 'sip:$number@${Prefs.host}';
      bool ok;
      try {
        ok = await (ua as dynamic).call(target, voiceonly: true, mediaStream: stream) as bool;
      } on NoSuchMethodError {
        ok = await (ua as dynamic).call(target, voiceOnly: true, mediaStream: stream) as bool;
      }
      if (!ok) return 'مقدرش يبدأ المكالمة';
    } catch (e) {
      final m = '$e';
      if (m.contains('Permission') || m.contains('NotAllowed')) return 'المايك مش مسموح للتطبيق — اسمح بيه من الإعدادات';
      return 'المكالمة مبدأتش: $m';
    }
    return null;
  }

  Future<void> answer() async {
    final c = current;
    if (c == null || c.phase != CallPhase.incoming) return;
    await Ringer.I.stop();
    await Native.incomingDone();
    try {
      final stream = await navigator.mediaDevices.getUserMedia({'audio': true, 'video': false});
      c.call.answer(_ua!.buildCallOptions(true), mediaStream: stream);
    } catch (e) {
      logd('answer $e');
    }
  }

  void hangup() {
    final c = current;
    if (c == null) return;
    try {
      if (c.phase == CallPhase.incoming) {
        c.call.hangup({'status_code': 486});
      } else {
        c.call.hangup();
      }
    } catch (_) {
      _finish(c, null);
    }
  }

  void toggleMute() {
    final c = current;
    if (c == null) return;
    try {
      c.muted ? c.call.unmute(true, false) : c.call.mute(true, false);
      c.muted = !c.muted;
    } catch (_) {}
    notifyListeners();
  }

  void toggleSpeaker() {
    final c = current;
    if (c == null) return;
    c.speaker = !c.speaker;
    _speaker(c.speaker);
    notifyListeners();
  }

  void _speaker(bool on) {
    if (!(Platform.isAndroid || Platform.isIOS)) return;
    Helper.setSpeakerphoneOn(on).catchError((_) {});
  }

  void toggleHold() {
    final c = current;
    if (c == null || c.phase != CallPhase.active) return;
    try {
      c.held ? c.call.unhold() : c.call.hold();
      c.held = !c.held;
    } catch (_) {}
    notifyListeners();
  }

  void sendDtmf(String d) {
    final c = current;
    if (c == null || c.phase != CallPhase.active) return;
    try {
      c.call.sendDTMF(d);
      c.dtmf += d;
    } catch (_) {}
    notifyListeners();
  }

  void clearMissed() {
    _missed.clear();
    notifyListeners();
  }

  // ---------------- أحداث sip_ua ----------------
  @override
  void transportStateChanged(dynamic state) {
    final s = _enumName(state.state);
    if (s.contains('DISCONNECTED')) {
      _setLine(LineState.failed, 'الاتصال بالسنترال اتقطع — بيرجع لوحده…');
    } else if (s.contains('CONNECTING')) {
      if (line != LineState.online) _setLine(LineState.connecting, 'بيتصل بالسنترال…');
    }
  }

  @override
  void registrationStateChanged(dynamic state) {
    final s = _enumName(state.state);
    if (s == 'REGISTERED') {
      _restarts = 0;
      _setLine(LineState.online, 'متصل ✓');
      Native.keepAlive(true, text: 'متصل بالسنترال — تحويلة ${account?.ext ?? ''}');
    } else if (s.contains('FAILED')) {
      final cause = '${_f(state.cause, ['cause']) ?? state.cause ?? ''}';
      final bad = cause.contains('Authentication') || cause.contains('Rejected') || cause.contains('Forbidden');
      _setLine(LineState.failed, bad ? 'الحساب اتغيّر — اخرج وادخل تاني' : 'فشل الاتصال: $cause');
    } else if (s == 'UNREGISTERED') {
      if (account != null) _setLine(LineState.failed, 'مش متسجّل على السنترال — بيحاول تاني…');
    }
  }

  @override
  void callStateChanged(dynamic call, dynamic state) {
    final s = _enumName(state.state);
    final id = '${_f(call, ['id'])}';
    var c = current;

    if (s == 'CALL_INITIATION') {
      final incoming = '${_f(call, ['direction'])}'.toUpperCase().contains('INCOMING');
      if (c != null && '${_f(c.call, ['id'])}' != id) {
        // مكالمة تانية وإنت في مكالمة: مشغول
        try {
          call.hangup({'status_code': 486});
        } catch (_) {}
        if (incoming) _missed.add(_peer(call));
        notifyListeners();
        return;
      }
      final num = incoming ? _peer(call) : _target(call);
      c = ActiveCall(call, incoming, num, incoming ? CallPhase.incoming : CallPhase.dialing);
      current = c;
      final disp = '${_f(call, ['remote_display_name', 'remoteDisplayName']) ?? ''}'.replaceAll('"', '');
      if (incoming && disp.isNotEmpty && !RegExp(r'^\+?\d+$').hasMatch(disp) && !disp.startsWith('dinastar')) c.name = disp;
      final a = account;
      if (a != null) {
        Api.lookupName(a, num).then((n) {
          if (n.isNotEmpty && current == c) {
            c!.name = n;
            notifyListeners();
          }
        });
      }
      if (incoming) {
        Ringer.I.start();
        Native.incoming('📞 مكالمة واردة', c.name.isNotEmpty ? '${c.name} · $num' : num);
      }
      notifyListeners();
      return;
    }
    if (c == null || '${_f(c.call, ['id'])}' != id) return;

    switch (s) {
      case 'PROGRESS':
        if (!c.incoming && c.phase == CallPhase.dialing) {
          c.phase = CallPhase.ringing;
          c.ringAt = DateTime.now();
        }
        break;
      case 'ACCEPTED':
      case 'CONFIRMED':
        if (c.phase != CallPhase.active) {
          c.phase = CallPhase.active;
          c.answeredAt = DateTime.now();
          Ringer.I.stop();
          Native.incomingDone();
          _tick?.cancel();
          _tick = Timer.periodic(const Duration(seconds: 1), (_) => notifyListeners());
        }
        break;
      case 'HOLD':
        c.held = true;
        break;
      case 'UNHOLD':
        c.held = false;
        break;
      case 'MUTED':
        c.muted = true;
        break;
      case 'UNMUTED':
        c.muted = false;
        break;
      case 'ENDED':
      case 'FAILED':
        _finish(c, s == 'FAILED' ? state.cause : null, failed: s == 'FAILED');
        return;
    }
    notifyListeners();
  }

  void _finish(ActiveCall c, dynamic cause, {bool failed = false}) {
    _tick?.cancel();
    Ringer.I.stop();
    Native.incomingDone();
    if (c.speaker) _speaker(false);
    if (c.incoming && c.answeredAt == null) _missed.add(c.number);
    if (!c.incoming && c.answeredAt == null && failed) {
      lastOutcome = outcomeOf(cause, c);
    } else {
      lastOutcome = null;
    }
    c.phase = CallPhase.ended;
    current = null;
    notifyListeners();
  }

  String _peer(dynamic call) {
    final u = '${_f(call, ['remote_identity', 'remoteIdentity']) ?? ''}';
    final user = RegExp(r'sip:([^@;>]+)').firstMatch(u)?.group(1) ?? u;
    final dn = '${_f(call, ['remote_display_name', 'remoteDisplayName']) ?? ''}'.replaceAll(RegExp(r'[\s"]'), '');
    // الجيت واي ساعات بيبعت اسم حسابه مكان الرقم والرقم الحقيقي في اسم العرض
    if (RegExp(r'^\+?\d{3,}$').hasMatch(user)) return user;
    if (RegExp(r'^\+?\d{6,}$').hasMatch(dn)) return dn;
    return user;
  }

  String _target(dynamic call) {
    final u = '${_f(call, ['remote_identity', 'remoteIdentity']) ?? ''}';
    final n = RegExp(r'sip:([^@;>]+)').firstMatch(u)?.group(1);
    return n ?? (lastNumber.isNotEmpty ? lastNumber : u);
  }

  /// مشغول / مقفول / لم يرد / رقم غلط — من كود السنترال ومدة الرنين
  static Outcome? outcomeOf(dynamic cause, ActiveCall c) {
    int code = 0;
    String why = '';
    try {
      code = int.tryParse('${_f(cause, ['status_code', 'statusCode']) ?? 0}') ?? 0;
      why = '${_f(cause, ['cause']) ?? ''} ${_f(cause, ['reason_phrase', 'reasonPhrase']) ?? ''}'.trim();
    } catch (_) {}
    final rang = c.ringAt == null ? 0 : DateTime.now().difference(c.ringAt!).inSeconds;
    final w = why.toLowerCase();
    if (w.contains('canceled') && code == 0) {
      return rang >= 20 ? Outcome('noans', '⏱️', 'لم يرد', detail: 'رن $rang ثانية وقفلت', retry: true) : null;
    }
    if (code == 487) return null;
    if (code == 486 || code == 600 || w.contains('busy')) {
      return const Outcome('busy', '🔴', 'الرقم مشغول', detail: 'العميل في مكالمة تانية — جرّب بعد شوية', retry: true);
    }
    if (code == 603 || w.contains('rejected')) return const Outcome('reject', '🚫', 'العميل رفض المكالمة', retry: true);
    if (code == 408 || (code == 480 && rang >= 15) || w.contains('no answer') || w.contains('timeout')) {
      return Outcome('noans', '⏱️', 'لم يرد', detail: rang > 0 ? 'رن $rang ثانية من غير رد' : '', retry: true);
    }
    if (code == 480 || w.contains('unavailable')) return const Outcome('off', '📴', 'الموبايل مقفول أو خارج التغطية', retry: true);
    if (code == 404 || code == 484 || w.contains('not found') || w.contains('incomplete')) {
      return const Outcome('bad', '❌', 'الرقم غلط أو مش موجود', detail: 'اتأكد من الرقم (ومن كود الدولة لو دولي)');
    }
    if (code == 502 || code == 503) return const Outcome('net', '📶', 'الخط مشغول أو مفيش شبكة على الشريحة', detail: 'جرّب تاني بعد دقيقة', retry: true);
    if (code == 403) return const Outcome('deny', '⛔', 'التحويلة مش مسموحلها تتصل بره');
    if (code == 488) return const Outcome('codec', '🔇', 'مشكلة في الصوت (codec)');
    if (w.contains('user media') || w.contains('permission')) return const Outcome('mic', '🎙️', 'المايك مش مسموح للتطبيق');
    return Outcome('fail', '⚠️', 'المكالمة مكملتش', detail: [why, if (code > 0) '($code)'].where((x) => x.isNotEmpty).join(' '), retry: true);
  }

  @override
  void onNewMessage(dynamic msg) {}

  @override
  void onNewNotify(dynamic ntf) {}

  // موجودة في نسخ sip_ua الأحدث بس
  void onNewReinvite(dynamic event) {}
}
