import 'dart:async';

import 'package:flutter/material.dart';

import '../core.dart';
import '../phone.dart';
import 'home.dart';
import 'theme.dart';

/// التحويلات الداخلية: اسم الموظف + حالته — دوسة واحدة تتصل
class DirectoryPage extends StatefulWidget {
  const DirectoryPage({super.key});
  @override
  State<DirectoryPage> createState() => _DirectoryPageState();
}

class _DirectoryPageState extends State<DirectoryPage> {
  List<Person>? _people;
  String _q = '', _err = '';
  Timer? _t;

  @override
  void initState() {
    super.initState();
    _load();
    _t = Timer.periodic(const Duration(seconds: 20), (_) {
      if (HomeShell.tab.value == 2) _load();
    });
  }

  @override
  void dispose() {
    _t?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final a = PhoneEngine.I.account;
    if (a == null) return;
    try {
      final v = await Api.people(a);
      if (mounted) setState(() {
        _people = v;
        _err = '';
      });
    } catch (e) {
      if (mounted) setState(() => _err = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final q = _q.trim().toLowerCase();
    const order = {'idle': 0, 'ring': 1, 'talk': 2, 'off': 3};
    final list = (_people ?? []).where((p) => q.isEmpty || p.name.toLowerCase().contains(q) || p.ext.contains(q)).toList()
      ..sort((a, b) => (order[a.state] ?? 3).compareTo(order[b.state] ?? 3));
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
        child: TextField(
          onChanged: (v) => setState(() => _q = v),
          decoration: const InputDecoration(hintText: 'دوّر بالاسم أو رقم التحويلة', prefixIcon: Icon(Icons.search), isDense: true),
        ),
      ),
      if (_err.isNotEmpty) Padding(padding: const EdgeInsets.all(8), child: Text(_err, style: const TextStyle(color: kRed))),
      Expanded(
        child: _people == null && _err.isEmpty
            ? const Center(child: CircularProgressIndicator())
            : RefreshIndicator(
                onRefresh: _load,
                child: ListView.separated(
                  padding: const EdgeInsets.fromLTRB(12, 0, 12, 24),
                  itemCount: list.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 6),
                  itemBuilder: (context, i) {
                    final p = list[i];
                    final (col, st) = switch (p.state) {
                      'idle' => (kGreen, 'متاح'),
                      'talk' => (kRed, 'في مكالمة'),
                      'ring' => (kAmber, 'بيرن'),
                      _ => (Colors.grey, 'مش متصل'),
                    };
                    return Card(
                      child: ListTile(
                        leading: CircleAvatar(
                          backgroundColor: kBrand.withValues(alpha: .12),
                          child: Text(p.name.isNotEmpty ? p.name.characters.first : '#', style: const TextStyle(color: kBrand, fontWeight: FontWeight.w700)),
                        ),
                        title: Text(p.name.isNotEmpty ? p.name : 'تحويلة ${p.ext}', style: const TextStyle(fontWeight: FontWeight.w600)),
                        subtitle: Row(children: [
                          Container(width: 8, height: 8, decoration: BoxDecoration(color: col, shape: BoxShape.circle)),
                          const SizedBox(width: 6),
                          Text('$st · ${p.ext}'),
                        ]),
                        trailing: IconButton.filledTonal(
                          tooltip: 'اتصل',
                          icon: const Icon(Icons.call, size: 20),
                          onPressed: () {
                            HomeShell.openDial(p.ext);
                            PhoneEngine.I.dial(p.ext);
                          },
                        ),
                      ),
                    );
                  },
                ),
              ),
      ),
    ]);
  }
}
