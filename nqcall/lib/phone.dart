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

enum Quality { unknown, excellent, good, weak, bad }

/// نتيجة المكالمة اللي مكملتش
class Outcome {
  final String key, icon, title, detail;
  final bool retry;
  const Outcome(this.key, this.icon, this.title, {this.detail = '', this.retry = false});
}

/// مكالمة فايتة (للبانر الأحمر)
class Missed {
  final String number;
  String name;
  final DateTime at;
  final int ringSec;
  String context;
  Missed(this.number, this.name, this.at, this.ringSec, [this.context = '']);
}

class ActiveCall {
  final dynamic call; // Call بتاع sip_ua
  final bool incoming;
  final String number;
  String name = '';
  Map<String, dynamic> info = {}; // بيانات العميل من السيرفر (CRM، صاحبه، آخر ملاحظة)
  CallPhase phase;
  DateTime? answeredAt, ringAt;
  final DateTime started = DateTime.now();
  bool muted = false, speaker = false, held = false;
  String dtmf = '';
  MediaStream? local;
  bool rejected = false; // اترفضت بإيدك (متتحسبش فايتة)
  ActiveCall(this.call, this.incoming, this.number, this.phase);

  int get seconds => answeredAt == null ? 0 : DateTime.now().difference(answeredAt!).inSeconds;

  /// سطر وصف العميل: الشركة / المرحلة / صاحبه
  String get context => describe(info);

  static String describe(Map<String, dynamic> j) {
    final bits = <String>[];
    final crm = j['crm'];
    if (crm is Map && '${crm['potential'] ?? ''}'.isNotEmpty) bits.add('مرحلة: ${crm['potential']}');
    final own = j['owner'];
    if (own is Map && '${own['name'] ?? ''}'.isNotEmpty) bits.add('مسؤول: ${own['name']}');
    final n = j['calls'];
    if (n is num && n > 0) bits.add('$n مكالمة قبل كده');
    return bits.join(' · ');
  }
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
        case 'peerConnection':
          return o.peerConnection;
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
  ActiveCall? waiting; // مكالمة تانية بترن وانت في مكالمة (انتظار المكالمات)
  ActiveCall? parked; // مكالمة على الانتظار (Hold) وانت بتكلم في التانية
  Outcome? lastOutcome;
  String lastNumber = '', notice = '';
  final List<Missed> missed = [];
  Quality quality = Quality.unknown;
  int? rttMs;
  double lossPct = 0;
  int historyVersion = 0; // بيزيد بعد كل مكالمة عشان السجل يتحدث
  Timer? _watch, _tick, _qTimer, _statsTimer;
  StreamSubscription? _net;
  DateTime _lastStart = DateTime(2000);

  /// الخط الصادر: السنترال أو شريحة الموبايل (عن طريق تطبيق NQ Phone على الموبايل)
  String get via => Prefs.str('via', 'pbx');
  Future<void> setVia(String v) async {
    await Prefs.setStr('via', v);
    notifyListeners();
  }

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
    _qTimer?.cancel();
    _measure();
    _qTimer = Timer.periodic(const Duration(seconds: 30), (_) => _measure());
  }

  void _connect() {
    final a = account;
    if (a == null) return;
    _lastStart = DateTime.now();
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
    _qTimer?.cancel();
    _watch = null;
    try {
      _ua?.unregister();
    } catch (_) {}
    try {
      _ua?.stop();
    } catch (_) {}
    _ua = null;
    missed.clear();
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

  // ---------------- جودة الاتصال ----------------
  Future<void> _measure() async {
    if (current != null) return; // وقت المكالمة القياس من إحصائيات المكالمة نفسها
    final ms = await Api.ping();
    rttMs = ms;
    lossPct = 0;
    quality = ms == null ? Quality.bad : (ms < 180 ? Quality.excellent : (ms < 350 ? Quality.good : Quality.weak));
    notifyListeners();
  }

  /// وقت المكالمة: التأخير وضياع الصوت من WebRTC كل 3 ثواني
  void _startStats(ActiveCall c) {
    _statsTimer?.cancel();
    int lastLost = 0, lastRecv = 0;
    _statsTimer = Timer.periodic(const Duration(seconds: 3), (_) async {
      final pc = _f(c.call, ['peerConnection']);
      if (pc == null || current != c) return;
      try {
        final List<StatsReport> st = await (pc as RTCPeerConnection).getStats();
        double? rtt;
        int lost = 0, recv = 0;
        for (final r in st) {
          final v = r.values;
          if (r.type == 'remote-inbound-rtp' && v['roundTripTime'] != null) rtt = double.tryParse('${v['roundTripTime']}');
          if (r.type == 'candidate-pair' && rtt == null && v['currentRoundTripTime'] != null && '${v['state']}' == 'succeeded') {
            rtt = double.tryParse('${v['currentRoundTripTime']}');
          }
          if (r.type == 'inbound-rtp' && '${v['kind'] ?? v['mediaType']}' == 'audio') {
            lost = int.tryParse('${v['packetsLost'] ?? 0}') ?? 0;
            recv = int.tryParse('${v['packetsReceived'] ?? 0}') ?? 0;
          }
        }
        final dl = lost - lastLost, dr = recv - lastRecv;
        lastLost = lost;
        lastRecv = recv;
        lossPct = (dl + dr) > 0 ? 100 * dl / (dl + dr) : 0;
        if (rtt != null) rttMs = (rtt * 1000).round();
        final r = rttMs ?? 0;
        quality = (lossPct > 8 || r > 600)
            ? Quality.bad
            : (lossPct > 3 || r > 350)
                ? Quality.weak
                : (lossPct > 1 || r > 180)
                    ? Quality.good
                    : Quality.excellent;
        notifyListeners();
      } catch (_) {}
    });
  }

  // ---------------- الصوت (المايك والسماعة) ----------------
  Map<String, dynamic> get _audio {
    final mic = Prefs.str('micId');
    if (mic.isEmpty) return {'audio': true, 'video': false};
    return {
      'audio': {
        'deviceId': mic,
        'optional': [
          {'sourceId': mic}
        ]
      },
      'video': false
    };
  }

  Future<MediaStream> _mic() async {
    try {
      return await navigator.mediaDevices.getUserMedia(_audio);
    } catch (_) {
      return navigator.mediaDevices.getUserMedia({'audio': true, 'video': false}); // المايك المختار اتشال؟ الافتراضي
    }
  }

  /// السماعة اللي هتطلع منها المكالمة (كمبيوتر: سماعة رأس / سبيكر)
  Future<void> applyOutput([String? id]) async {
    final out = id ?? Prefs.str('spkId');
    if (out.isEmpty) return;
    try {
      await Helper.selectAudioOutput(out);
    } catch (e) {
      logd('output $e');
    }
  }

  Future<void> setOutput(String id) async {
    await Prefs.setStr('spkId', id);
    await applyOutput(id);
    notifyListeners();
  }

  Future<void> setMic(String id) async {
    await Prefs.setStr('micId', id);
    notifyListeners();
  }

  static Future<List<MediaDeviceInfo>> devices() async {
    try {
      // على بعض الأجهزة الأسامي مش بتظهر غير بعد إذن المايك
      final s = await navigator.mediaDevices.getUserMedia({'audio': true, 'video': false});
      final l = await navigator.mediaDevices.enumerateDevices();
      for (final t in s.getTracks()) {
        t.stop();
      }
      await s.dispose();
      return l;
    } catch (_) {
      try {
        return await navigator.mediaDevices.enumerateDevices();
      } catch (_) {
        return [];
      }
    }
  }

  // ---------------- المكالمات ----------------
  Future<String?> dial(String raw, {String name = ''}) async {
    final number = pbxNumber(cleanPhone(raw));
    if (number.isEmpty) return 'اكتب الرقم الأول';
    if (current != null) return 'فيه مكالمة شغالة';
    final a = account;
    if (a == null) return 'ادخل بحسابك الأول';
    lastOutcome = null;
    lastNumber = raw;

    // من شريحتي (الموبايل بس): المكالمة من شريحة الموبايل نفسه — الكمبيوتر دايمًا من السنترال
    if (via == 'sim' && (Platform.isAndroid || Platform.isIOS) && number.length > 5) {
      final ok = await Native.simCall(cleanPhone(raw));
      if (!ok) return 'مقدرش يفتح الاتصال من الشريحة — اسمح للتطبيق بالمكالمات من الإعدادات';
      notice = '📱 بيتصل من شريحتك…';
      notifyListeners();
      return null;
    }

    final ua = _ua;
    if (ua == null || line != LineState.online) {
      _connect();
      return 'مش متصل بالسنترال — بيحاول يتصل… جرّب كمان ثانيتين';
    }
    try {
      final stream = await _mic();
      _pendingLocal = stream;
      final target = 'sip:$number@${Prefs.host}';
      bool ok;
      try {
        ok = await (ua as dynamic).call(target, voiceonly: true, mediaStream: stream) as bool;
      } on NoSuchMethodError {
        ok = await (ua as dynamic).call(target, voiceOnly: true, mediaStream: stream) as bool;
      }
      if (!ok) {
        _stopStream(stream);
        return 'مقدرش يبدأ المكالمة';
      }
    } catch (e) {
      final m = '$e';
      if (m.contains('Permission') || m.contains('NotAllowed')) return 'المايك مش مسموح للتطبيق — اسمح بيه من الإعدادات';
      if (m.contains('NotFound') || m.contains('device')) return 'مفيش مايك متوصل — وصّل سماعة أو اختار المايك من إعدادات الصوت';
      return 'المكالمة مبدأتش: $m';
    }
    return null;
  }

  MediaStream? _pendingLocal;

  void _stopStream(MediaStream? s) {
    if (s == null) return;
    try {
      for (final t in s.getTracks()) {
        t.stop();
      }
      s.dispose();
    } catch (_) {}
  }

  Future<void> answer() async {
    final c = current;
    if (c == null || c.phase != CallPhase.incoming) return;
    await Ringer.I.stop();
    await Native.incomingDone();
    try {
      final stream = await _mic();
      c.local = stream;
      c.call.answer(_ua!.buildCallOptions(true), mediaStream: stream);
    } catch (e) {
      logd('answer $e');
    }
  }

  final Set<String> _simChecked = {}, _dead = {};

  /// انتظار المكالمات (اختياري): لو مقفول، اللي يتصل وانت مشغول ياخد مشغول
  bool get callWaiting => Prefs.flag('callWaiting', false);
  Future<void> setCallWaiting(bool v) async {
    await Prefs.setFlag('callWaiting', v);
    notifyListeners();
  }

  /// آخر واحد اتصل وانت مشغول (رسالة صغيرة في شاشة المكالمة من غير ما تقاطعك)
  String busyNote = '';

  void _busyReject(dynamic call, bool incoming, {String ctx = 'اتصل وانت في مكالمة'}) {
    try {
      call.hangup({'status_code': 486});
    } catch (_) {}
    if (incoming) {
      final num = _peer(call);
      _addMissed(num, '', 0, ctx);
      busyNote = '📞 $num $ctx — هتلاقيه في «فاتتك»';
      final a = account;
      if (a != null) {
        Api.lookup(a, num).then((j) {
          final n = '${j['name'] ?? ''}';
          if (n.isEmpty) return;
          for (final m in missed) {
            if (m.number == num && m.name.isEmpty) m.name = n;
          }
          if (busyNote.contains(num)) busyNote = '📞 $n $ctx — هتلاقيه في «فاتتك»';
          notifyListeners();
        });
      }
      Future.delayed(const Duration(seconds: 12), () {
        if (busyNote.contains(num)) {
          busyNote = '';
          notifyListeners();
        }
      });
    }
    notifyListeners();
  }

  // ---------------- انتظار المكالمات ----------------
  /// ترد على المكالمة اللي في الانتظار: الحالية تروح Hold (العميل يسمع موسيقى الانتظار) أو تتقفل
  Future<void> answerWaiting({bool endCurrent = false}) async {
    final w = waiting, c = current;
    if (w == null) return;
    await Ringer.I.stop();
    await Native.incomingDone();
    if (c != null) {
      if (endCurrent) {
        try {
          c.call.hangup();
        } catch (_) {}
        _finish(c, null);
      } else {
        try {
          if (!c.held) c.call.hold();
          c.held = true;
        } catch (_) {}
        _statsTimer?.cancel();
        parked = c;
      }
    }
    waiting = null;
    current = w;
    try {
      final stream = await _mic();
      w.local = stream;
      w.call.answer(_ua!.buildCallOptions(true), mediaStream: stream);
    } catch (e) {
      logd('answerWaiting $e');
    }
    notifyListeners();
  }

  /// رفض المكالمة التانية (المتصل يسمع مشغول). callBack = تتحط في "مكالمات فايتة" عشان ترن عليه بعدين
  void rejectWaiting({bool callBack = false}) {
    final w = waiting;
    if (w == null) return;
    Ringer.I.stop();
    w.rejected = !callBack;
    try {
      w.call.hangup({'status_code': 486});
    } catch (_) {}
    if (callBack) _addMissed(w.number, w.name, DateTime.now().difference(w.started).inSeconds, w.context.isNotEmpty ? w.context : 'رفضتها وانت في مكالمة');
    waiting = null;
    notifyListeners();
  }

  /// تبديل بين المكالمة الحالية واللي على الانتظار
  void swapCalls() {
    final c = current, p = parked;
    if (c == null || p == null) return;
    try {
      if (!c.held) c.call.hold();
      c.held = true;
      if (p.held) p.call.unhold();
      p.held = false;
    } catch (_) {}
    current = p;
    parked = c;
    _startStats(p);
    notifyListeners();
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
    // أمان: لو السنترال مارجّعش رد في 4 ثواني نقفل الشاشة إحنا
    Future.delayed(const Duration(seconds: 4), () {
      if (current == c) _finish(c, null);
    });
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

  /// موبايل: سبيكر ↔ سماعة الموبايل
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

  void dismissMissed(Missed m) {
    missed.remove(m);
    notifyListeners();
  }

  void clearNotice() {
    notice = '';
    lastOutcome = null;
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
      _setLine(LineState.online, 'متصل بالسنترال');
      Native.keepAlive(true, text: 'متصل بالسنترال — تحويلة ${account?.ext ?? ''}');
      applyOutput();
    } else if (s.contains('FAILED')) {
      final cause = '${_f(state.cause, ['cause']) ?? state.cause ?? ''}';
      final bad = cause.contains('Authentication') || cause.contains('Rejected') || cause.contains('Forbidden');
      _setLine(LineState.failed, bad ? 'الحساب اتغيّر — اخرج وادخل تاني' : 'فشل الاتصال بالسنترال');
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
        // مكالمة تانية وانت في مكالمة: متقطعش اللي معاك أبدًا.
        // الافتراضي: المتصل ياخد مشغول وتظهرلك بعدين في "فاتتك" عشان ترن عليه. (انتظار المكالمات اختياري من الإعدادات)
        if (incoming && callWaiting && waiting == null && parked == null) {
          final w = ActiveCall(call, true, _peer(call), CallPhase.incoming);
          final disp = '${_f(call, ['remote_display_name', 'remoteDisplayName']) ?? ''}'.replaceAll('"', '');
          if (disp.isNotEmpty && !RegExp(r'^\+?\d+$').hasMatch(disp) && !disp.startsWith('dinastar')) w.name = disp;
          waiting = w;
          Ringer.I.startWaiting();
          Native.incoming('📞 مكالمة تانية في الانتظار', w.name.isNotEmpty ? '${w.name} · ${w.number}' : w.number);
          final a = account;
          if (a != null) {
            Api.lookup(a, w.number).then((j) {
              if (waiting != w) return;
              w.info = j;
              final n = '${j['name'] ?? ''}';
              if (n.isNotEmpty) w.name = n;
              notifyListeners();
            });
          }
          notifyListeners();
          return;
        }
        _busyReject(call, incoming);
        return;
      }
      // أندرويد: لو الموظف في مكالمة من الشريحة، مكالمة السنترال متقاطعهاش
      if (incoming && Platform.isAndroid && !_simChecked.contains(id)) {
        _simChecked.add(id);
        Native.inSimCall().then((busy) {
          if (_dead.contains(id)) return; // المتصل قفل قبل ما نخلص الفحص
          if (busy) {
            _busyReject(call, true, ctx: 'اتصل وانت في مكالمة شريحة');
          } else {
            callStateChanged(call, state);
          }
        });
        return;
      }
      final num = incoming ? _peer(call) : _target(call);
      c = ActiveCall(call, incoming, num, incoming ? CallPhase.incoming : CallPhase.dialing);
      if (!incoming) {
        c.local = _pendingLocal;
        _pendingLocal = null;
      }
      current = c;
      notice = '';
      final disp = '${_f(call, ['remote_display_name', 'remoteDisplayName']) ?? ''}'.replaceAll('"', '');
      if (incoming && disp.isNotEmpty && !RegExp(r'^\+?\d+$').hasMatch(disp) && !disp.startsWith('dinastar')) c.name = disp;
      final a = account;
      if (a != null) {
        Api.lookup(a, num).then((j) {
          if (current != c) return;
          c!.info = j;
          final n = '${j['name'] ?? ''}';
          if (n.isNotEmpty) c.name = n;
          notifyListeners();
          if (c.incoming && c.phase == CallPhase.incoming && n.isNotEmpty) Native.incoming('📞 مكالمة واردة', '$n · $num');
        });
      }
      if (incoming) {
        Ringer.I.start();
        Native.incoming('📞 مكالمة واردة', c.name.isNotEmpty ? '${c.name} · $num' : num);
      }
      notifyListeners();
      return;
    }
    String cid(ActiveCall? x) => x == null ? '' : '${_f(x.call, ['id'])}';
    if (cid(c) != id) {
      c = cid(waiting) == id ? waiting : (cid(parked) == id ? parked : null);
    }
    if (c == null) {
      if (s == 'ENDED' || s == 'FAILED') _dead.add(id);
      return;
    }

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
          applyOutput();
          if (c == current) _startStats(c);
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

  void _addMissed(String number, String name, int ringSec, [String ctx = '']) {
    missed.removeWhere((m) => m.number == number);
    missed.add(Missed(number, name, DateTime.now(), ringSec, ctx));
    if (missed.length > 5) missed.removeAt(0);
  }

  void _finish(ActiveCall c, dynamic cause, {bool failed = false}) {
    if (c.phase == CallPhase.ended) return;
    final wasMissed = c.incoming && c.answeredAt == null;
    c.phase = CallPhase.ended;
    _stopStream(c.local);
    // المكالمة اللي كانت في الانتظار أو على الـ Hold خلصت: الحالية تفضل زي ما هي
    if (c == waiting || c == parked) {
      if (c == waiting) {
        waiting = null;
        Ringer.I.stop();
        if (wasMissed && !c.rejected) _addMissed(c.number, c.name, DateTime.now().difference(c.started).inSeconds, c.context);
      } else {
        parked = null;
      }
      historyVersion++;
      notifyListeners();
      return;
    }
    if (current != c) return;
    _tick?.cancel();
    _statsTimer?.cancel();
    Ringer.I.stop();
    Native.incomingDone();
    if (c.speaker) _speaker(false);
    if (wasMissed && !c.rejected) {
      _addMissed(c.number, c.name, DateTime.now().difference(c.started).inSeconds, c.context);
    }
    lastOutcome = (!c.incoming && c.answeredAt == null && failed) ? outcomeOf(cause, c) : null;
    current = null;
    // كان فيه عميل على الانتظار؟ يرجع هو المكالمة الحالية (لسه على الـ Hold لحد ما تدوس "استكمال")
    if (parked != null) {
      current = parked;
      parked = null;
      _tick = Timer.periodic(const Duration(seconds: 1), (_) => notifyListeners());
      _startStats(current!);
    } else if (waiting != null) {
      // الحالية خلصت والتانية لسه بترن: ترجع رنة عادية
      current = waiting;
      waiting = null;
      Ringer.I.start();
    }
    historyVersion++;
    notifyListeners();
    Future.delayed(const Duration(seconds: 2), _measure);
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
  @override
  void onNewReinvite(dynamic event) {}
}
