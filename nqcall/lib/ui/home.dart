import 'package:flutter/material.dart';

import '../core.dart';
import '../phone.dart';
import 'dial_page.dart';
import 'directory_page.dart';
import 'history_page.dart';
import 'settings_page.dart';
import 'theme.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});
  static final tab = ValueNotifier<int>(0);
  static void openDial(String number) {
    DialPage.setNumber(number);
    tab.value = 0;
  }

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  static const _items = [
    (Icons.dialpad, Icons.dialpad_outlined, 'اتصال'),
    (Icons.history, Icons.history_outlined, 'السجل'),
    (Icons.people_alt, Icons.people_alt_outlined, 'التحويلات'),
    (Icons.settings, Icons.settings_outlined, 'الإعدادات'),
  ];

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<int>(
      valueListenable: HomeShell.tab,
      builder: (context, i, _) {
        final pages = const [DialPage(), HistoryPage(), DirectoryPage(), SettingsPage()];
        final body = Column(children: [const LineBar(), Expanded(child: IndexedStack(index: i, children: pages))]);
        return LayoutBuilder(builder: (context, c) {
          final wide = c.maxWidth >= 760;
          if (wide) {
            return Scaffold(
              body: Row(children: [
                NavigationRail(
                  selectedIndex: i,
                  onDestinationSelected: (v) => HomeShell.tab.value = v,
                  labelType: NavigationRailLabelType.all,
                  leading: Padding(
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    child: Container(
                      width: 46,
                      height: 46,
                      decoration: BoxDecoration(gradient: const LinearGradient(colors: [kBrand, kBrand2]), borderRadius: BorderRadius.circular(14)),
                      child: const Center(child: Text('NQ', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 17))),
                    ),
                  ),
                  destinations: [for (final it in _items) NavigationRailDestination(icon: Icon(it.$2), selectedIcon: Icon(it.$1), label: Text(it.$3))],
                ),
                const VerticalDivider(width: 1),
                Expanded(child: body),
              ]),
            );
          }
          return Scaffold(
            body: SafeArea(bottom: false, child: body),
            bottomNavigationBar: NavigationBar(
              selectedIndex: i,
              onDestinationSelected: (v) => HomeShell.tab.value = v,
              destinations: [for (final it in _items) NavigationDestination(icon: Icon(it.$2), selectedIcon: Icon(it.$1), label: it.$3)],
            ),
          );
        });
      },
    );
  }
}

/// شريط حالة الخط: متصل / بيتصل / مقطوع + زرار اتصل تاني
class LineBar extends StatelessWidget {
  const LineBar({super.key});
  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: PhoneEngine.I,
      builder: (context, _) {
        final e = PhoneEngine.I, a = e.account;
        final (color, icon) = switch (e.line) {
          LineState.online => (kGreen, Icons.check_circle),
          LineState.connecting => (kAmber, Icons.sync),
          _ => (kRed, Icons.error_outline),
        };
        return Container(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
          decoration: BoxDecoration(border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor.withValues(alpha: .2)))),
          child: Row(children: [
            Icon(icon, color: color, size: 18),
            const SizedBox(width: 8),
            Expanded(child: Text(e.lineText, style: TextStyle(color: color, fontWeight: FontWeight.w600), overflow: TextOverflow.ellipsis)),
            if (e.line != LineState.online && e.line != LineState.connecting)
              TextButton.icon(onPressed: e.reconnect, icon: const Icon(Icons.refresh, size: 18), label: const Text('اتصل تاني')),
            if (a != null)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(color: kBrand.withValues(alpha: .1), borderRadius: BorderRadius.circular(99)),
                child: Text(a.name.isNotEmpty ? '${a.name} · ${a.ext}' : 'تحويلة ${a.ext}', style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
              ),
          ]),
        );
      },
    );
  }
}

String agoText(DateTime t) {
  final d = DateTime.now().difference(t);
  if (d.inMinutes < 1) return 'دلوقتي';
  if (d.inMinutes < 60) return 'من ${d.inMinutes} دقيقة';
  final today = DateTime.now();
  final hm = '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
  if (t.year == today.year && t.month == today.month && t.day == today.day) return 'النهارده $hm';
  final y = today.subtract(const Duration(days: 1));
  if (t.year == y.year && t.month == y.month && t.day == y.day) return 'امبارح $hm';
  return '${t.day}/${t.month} $hm';
}

String shortDur(int s) => s <= 0 ? '' : fmtDur(s);
