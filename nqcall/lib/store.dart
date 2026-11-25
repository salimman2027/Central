// البيانات اللي الشاشة الرئيسية بتعرضها: الفريق وحالته، آخر المكالمات، المفضلة
import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import 'core.dart';
import 'phone.dart';

class Fav {
  final String number, name, kind; // kind: team | customer
  const Fav(this.number, this.name, this.kind);
  Map<String, String> toJson() => {'n': number, 'm': name, 'k': kind};
}

class HomeData extends ChangeNotifier {
  HomeData._();
  static final HomeData I = HomeData._();

  List<Person> people = [];
  List<HistoryItem> history = [];
  String serverVer = '';
  String peopleErr = '', historyErr = '';
  bool loadingHistory = false;
  Timer? _t;
  int _seenVersion = -1;

  void start() {
    _t?.cancel();
    refreshPeople();
    refreshHistory();
    Api.serverVersion().then((v) {
      serverVer = v;
      notifyListeners();
    });
    // حالة الفريق كل 20 ثانية، والسجل بعد كل مكالمة أو كل دقيقتين
    int n = 0;
    _t = Timer.periodic(const Duration(seconds: 20), (_) {
      n++;
      refreshPeople();
      final e = PhoneEngine.I;
      if (e.historyVersion != _seenVersion || n % 6 == 0) refreshHistory();
    });
  }

  void stop() => _t?.cancel();

  Future<void> refreshPeople() async {
    final a = PhoneEngine.I.account;
    if (a == null) return;
    try {
      people = await Api.people(a);
      peopleErr = '';
    } catch (e) {
      peopleErr = '$e';
    }
    notifyListeners();
  }

  Future<void> refreshHistory() async {
    final a = PhoneEngine.I.account;
    if (a == null || loadingHistory) return;
    _seenVersion = PhoneEngine.I.historyVersion;
    loadingHistory = true;
    try {
      // السنترال بيسجل المكالمة بعد ما تخلص بثواني
      history = await Api.history(a);
      historyErr = '';
    } catch (e) {
      historyErr = '$e';
    }
    loadingHistory = false;
    notifyListeners();
  }

  Person? personByExt(String ext) {
    for (final p in people) {
      if (p.ext == ext) return p;
    }
    return null;
  }

  // ---------- المفضلة ----------
  List<Fav> get pinned {
    try {
      return [for (final x in (jsonDecode(Prefs.str('favs', '[]')) as List)) Fav('${x['n']}', '${x['m']}', '${x['k']}')];
    } catch (_) {
      return [];
    }
  }

  bool isPinned(String number) => pinned.any((f) => f.number == number);

  Future<void> togglePin(Fav f) async {
    final l = pinned;
    if (l.any((x) => x.number == f.number)) {
      l.removeWhere((x) => x.number == f.number);
    } else {
      l.insert(0, f);
    }
    await Prefs.setStr('favs', jsonEncode([for (final x in l.take(12)) x.toJson()]));
    notifyListeners();
  }

  /// المثبّتين الأول، وبعدين أكتر عملاء بتكلمهم وزمايلك المتاحين
  List<Fav> get favorites {
    final out = <Fav>[...pinned];
    final seen = out.map((f) => f.number).toSet();
    final counts = <String, int>{}, names = <String, String>{};
    for (final h in history) {
      if (h.number.length < 7) continue;
      counts[h.number] = (counts[h.number] ?? 0) + 1;
      if (h.customer.isNotEmpty) names[h.number] = h.customer;
    }
    final top = counts.entries.where((e) => e.value >= 2).toList()..sort((a, b) => b.value.compareTo(a.value));
    final team = people.where((p) => p.state != 'off').toList();
    var ti = 0, ci = 0;
    while (out.length < 8 && (ti < team.length || ci < top.length)) {
      if (ti < team.length) {
        final p = team[ti++];
        if (seen.add(p.ext)) out.add(Fav(p.ext, p.name.isNotEmpty ? p.name : 'تحويلة ${p.ext}', 'team'));
      }
      if (ci < top.length && out.length < 8) {
        final n = top[ci++].key;
        if (seen.add(n)) out.add(Fav(n, names[n] ?? n, 'customer'));
      }
    }
    return out;
  }
}
