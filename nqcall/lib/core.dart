// الإعدادات المشتركة + التخزين + كلام السيرفر
import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

const String kAppVersion = '1.2.0';
const String kDefaultHost = '3.89.245.93.sslip.io';

/// التخزين المحلي (اسم الدخول، النغمة، الثيم...)
class Prefs {
  static late SharedPreferences _p;
  static Future<void> init() async => _p = await SharedPreferences.getInstance();

  static String str(String k, [String d = '']) => _p.getString(k) ?? d;
  static Future<void> setStr(String k, String v) => _p.setString(k, v);
  static double dbl(String k, double d) => _p.getDouble(k) ?? d;
  static Future<void> setDbl(String k, double v) => _p.setDouble(k, v);
  static bool flag(String k, [bool d = false]) => _p.getBool(k) ?? d;
  static Future<void> setFlag(String k, bool v) => _p.setBool(k, v);
  static Future<void> del(String k) => _p.remove(k);

  static String get host => str('host', kDefaultHost);
}

/// بيانات الدخول بعد ما السيرفر يرجّع التحويلة
class Account {
  final String ext, password, username, name;
  const Account({required this.ext, required this.password, this.username = '', this.name = ''});

  Map<String, dynamic> toJson() => {'ext': ext, 'password': password, 'username': username, 'name': name};
  static Account? fromJson(String s) {
    if (s.isEmpty) return null;
    try {
      final j = jsonDecode(s) as Map<String, dynamic>;
      if ((j['ext'] ?? '').toString().isEmpty) return null;
      return Account(ext: '${j['ext']}', password: '${j['password']}', username: '${j['username'] ?? ''}', name: '${j['name'] ?? ''}');
    } catch (_) {
      return null;
    }
  }

  static Account? load() => fromJson(Prefs.str('account'));
  Future<void> save() => Prefs.setStr('account', jsonEncode(toJson()));
  static Future<void> clear() => Prefs.del('account');
}

class ApiError implements Exception {
  final String message;
  ApiError(this.message);
  @override
  String toString() => message;
}

/// كلام سيرفر NQ (نفس الـ API بتاع التطبيق القديم)
class Api {
  static String get base => 'https://${Prefs.host}/calls-app';

  static Future<Map<String, dynamic>> _post(String path, Map<String, dynamic> body, {int timeout = 10}) async {
    final http.Response r;
    try {
      r = await http
          .post(Uri.parse('$base$path'), headers: {'Content-Type': 'application/json'}, body: jsonEncode(body))
          .timeout(Duration(seconds: timeout));
    } on TimeoutException {
      throw ApiError('السيرفر مش راد — اتأكد من النت');
    } catch (e) {
      throw ApiError('مش قادر يوصل للسيرفر — اتأكد من النت');
    }
    Map<String, dynamic> j = {};
    try {
      j = jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
    } catch (_) {}
    if (r.statusCode >= 400) throw ApiError('${j['detail'] ?? 'خطأ من السيرفر (${r.statusCode})'}');
    return j;
  }

  /// اسم المستخدم + كلمة السر → رقم التحويلة وباسوردها
  static Future<Account> login(String user, String pass) async {
    final j = await _post('/login', {'username': user.trim(), 'password': pass});
    if (j['simOnly'] == true) {
      throw ApiError('الحساب ده للاتصال من الشريحة بس — التطبيق ده للسنترال. استخدم تطبيق NQ Phone.');
    }
    if ((j['ext'] ?? '').toString().isEmpty) throw ApiError('السيرفر مارجّعش رقم التحويلة');
    return Account(ext: '${j['ext']}', password: '${j['password']}', username: '${j['username'] ?? user}', name: '${j['name'] ?? ''}');
  }

  /// التحويلات الداخلية وحالتها (متاح / في مكالمة / بيرن / مش متصل)
  static Future<List<Person>> people(Account a) async {
    final http.Response r;
    try {
      r = await http.get(Uri.parse('$base/files/people'), headers: {'X-Ext': a.ext, 'X-Pass': a.password}).timeout(const Duration(seconds: 10));
    } catch (_) {
      throw ApiError('مش قادر يجيب التحويلات — اتأكد من النت');
    }
    if (r.statusCode >= 400) throw ApiError('مش قادر يجيب التحويلات (${r.statusCode})');
    final j = jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
    return [
      for (final p in (j['people'] as List? ?? []))
        if (RegExp(r'^\d+$').hasMatch('${p['ext']}')) Person('${p['ext']}', '${p['name'] ?? ''}', '${p['state'] ?? 'off'}')
    ];
  }

  /// صاحب الرقم: من الـ CRM ومكالماتنا
  static Future<String> lookupName(Account a, String number) async {
    if (number.replaceAll(RegExp(r'\D'), '').length < 7) return '';
    try {
      final j = await _post('/lookup', {'ext': a.ext, 'password': a.password, 'number': number}, timeout: 4);
      return '${j['name'] ?? ''}';
    } catch (_) {
      return '';
    }
  }

  /// كل اللي السيرفر يعرفه عن الرقم: الاسم، الـ CRM، صاحب العميل، آخر مكالمة وآخر ملاحظة
  static Future<Map<String, dynamic>> lookup(Account a, String number) async {
    if (number.replaceAll(RegExp(r'\D'), '').length < 7) return {};
    try {
      return await _post('/lookup', {'ext': a.ext, 'password': a.password, 'number': number}, timeout: 4);
    } catch (_) {
      return {};
    }
  }

  /// "ذكّرني": مهمة متابعة في مهامي (بتظهر في التطبيق واللوحة)
  static Future<void> addTask(Account a, {required String text, required DateTime when}) async {
    final d = '${when.year}-${when.month.toString().padLeft(2, '0')}-${when.day.toString().padLeft(2, '0')}';
    final t = '${when.hour.toString().padLeft(2, '0')}:${when.minute.toString().padLeft(2, '0')}';
    await _post('/todo', {'ext': a.ext, 'password': a.password, 'action': 'add', 'date': d, 'time': t, 'text': text});
  }

  /// الاتصال من شريحة الموبايل: الطلب بيروح لتطبيق NQ Phone على موبايل الموظف وهو اللي يتصل
  static Future<void> simDial(Account a, String number, {String name = ''}) async {
    await _post('/rdial/send', {'ext': a.ext, 'password': a.password, 'number': number, 'name': name});
  }

  static Future<bool> simPhoneOnline(Account a) async {
    try {
      final j = await _post('/rdial/status', {'ext': a.ext, 'password': a.password}, timeout: 5);
      return j['phone'] == true;
    } catch (_) {
      return false;
    }
  }

  static Future<Map<String, dynamic>> goals(Account a) => _post('/goals', {'ext': a.ext, 'password': a.password}, timeout: 15);
  static Future<Map<String, dynamic>> me(Account a, String period) => _post('/me', {'ext': a.ext, 'password': a.password, 'period': period}, timeout: 20);

  /// زمن الوصول للسيرفر بالمللي ثانية (لمؤشر جودة الاتصال) — null لو مش واصل
  static Future<int?> ping() async {
    final sw = Stopwatch()..start();
    try {
      await http.head(Uri.parse('https://${Prefs.host}/calls/privacy')).timeout(const Duration(seconds: 5));
      return sw.elapsedMilliseconds;
    } catch (_) {
      return null;
    }
  }

  static Future<String> serverVersion() async {
    try {
      final r = await http.get(Uri.parse('$base/version')).timeout(const Duration(seconds: 6));
      return '${(jsonDecode(r.body) as Map)['server'] ?? ''}';
    } catch (_) {
      return '';
    }
  }

  /// سجل مكالماتي من السنترال (نفس السجل على كل الأجهزة)
  static Future<List<HistoryItem>> history(Account a, {int days = 30}) async {
    final j = await _post('/history', {'ext': a.ext, 'password': a.password, 'days': days}, timeout: 15);
    return [for (final x in (j['items'] as List? ?? [])) HistoryItem.fromJson(x as Map<String, dynamic>)];
  }
}

class Person {
  final String ext, name, state;
  Person(this.ext, this.name, this.state);
}

class HistoryItem {
  final String number, direction, customer, via;
  final DateTime started;
  final bool answered;
  final int duration;
  HistoryItem({required this.number, required this.direction, required this.started, required this.answered,
      required this.duration, this.customer = '', this.via = 'pbx'});

  factory HistoryItem.fromJson(Map<String, dynamic> j) => HistoryItem(
        number: '${j['number'] ?? ''}',
        direction: '${j['direction'] ?? 'out'}',
        started: DateTime.tryParse('${j['started']}')?.toLocal() ?? DateTime.now(),
        answered: j['answered'] == true,
        duration: (j['duration'] as num?)?.toInt() ?? 0,
        customer: '${j['customer'] ?? ''}',
        via: '${j['via'] ?? 'pbx'}',
      );
}

/// الرقم بالشكل اللي السنترال/الجيت واي بيفهمه (نفس منطق التطبيق القديم)
String pbxNumber(String n) {
  n = n.replaceAll(RegExp(r'[^\d+*#]'), '');
  n = n.replaceFirst(RegExp(r'^\+'), '00');
  if (RegExp(r'^01[0125]\d{8}$').hasMatch(n)) return '0020${n.substring(1)}'; // موبايل مصري
  if (RegExp(r'^20?1[0125]\d{8}$').hasMatch(n) && n.length == 12) return '00$n';
  if (RegExp(r'^009665\d{8}$').hasMatch(n)) return '0${n.substring(5)}'; // +9665… → 05…
  if (RegExp(r'^9665\d{8}$').hasMatch(n)) return '0${n.substring(3)}';
  if (RegExp(r'^5\d{8}$').hasMatch(n)) return '0$n';
  return n;
}

/// تنضيف رقم ملصوق (أرقام عربي، مسافات، شرط...)
String cleanPhone(String t) {
  const ar = '٠١٢٣٤٥٦٧٨٩', fa = '۰۱۲۳۴۵۶۷۸۹';
  final b = StringBuffer();
  for (final ch in t.split('')) {
    final i = ar.indexOf(ch), k = fa.indexOf(ch);
    b.write(i >= 0 ? '$i' : (k >= 0 ? '$k' : ch));
  }
  final s = b.toString().trim();
  final lead = s.startsWith('+') ? '+' : '';
  return lead + s.replaceAll(RegExp(r'[^\d*#]'), '');
}

String fmtDur(int s) => '${(s ~/ 60).toString().padLeft(2, '0')}:${(s % 60).toString().padLeft(2, '0')}';

void logd(Object o) {
  if (kDebugMode) debugPrint('[nq] $o');
}
