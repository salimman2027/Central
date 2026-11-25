import 'package:flutter/material.dart';

import '../core.dart';
import '../phone.dart';
import 'home.dart';
import 'theme.dart';

class HistoryPage extends StatefulWidget {
  const HistoryPage({super.key});
  @override
  State<HistoryPage> createState() => _HistoryPageState();
}

class _HistoryPageState extends State<HistoryPage> {
  List<HistoryItem>? _items;
  String _err = '', _filter = 'all';
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    HomeShell.tab.addListener(_onTab);
    _load();
  }

  @override
  void dispose() {
    HomeShell.tab.removeListener(_onTab);
    super.dispose();
  }

  void _onTab() {
    if (HomeShell.tab.value == 1) _load();
  }

  Future<void> _load() async {
    final a = PhoneEngine.I.account;
    if (a == null || _loading) return;
    setState(() => _loading = true);
    try {
      final v = await Api.history(a);
      if (mounted) setState(() {
        _items = v;
        _err = '';
      });
    } catch (e) {
      if (mounted) setState(() => _err = '$e');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final all = _items ?? [];
    final list = all.where((x) => switch (_filter) {
          'in' => x.direction == 'in',
          'out' => x.direction == 'out',
          'missed' => !x.answered,
          _ => true,
        }).toList();
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 6),
        child: Row(children: [
          Expanded(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: SegmentedButton<String>(
                showSelectedIcon: false,
                segments: const [
                  ButtonSegment(value: 'all', label: Text('الكل')),
                  ButtonSegment(value: 'out', label: Text('صادرة')),
                  ButtonSegment(value: 'in', label: Text('واردة')),
                  ButtonSegment(value: 'missed', label: Text('مردوش')),
                ],
                selected: {_filter},
                onSelectionChanged: (s) => setState(() => _filter = s.first),
              ),
            ),
          ),
          IconButton(tooltip: 'تحديث', onPressed: _load, icon: _loading ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)) : const Icon(Icons.refresh)),
        ]),
      ),
      if (_err.isNotEmpty) Padding(padding: const EdgeInsets.all(12), child: Text(_err, style: const TextStyle(color: kRed))),
      Expanded(
        child: _items == null && _err.isEmpty
            ? const Center(child: CircularProgressIndicator())
            : list.isEmpty
                ? Center(child: Text('مفيش مكالمات', style: TextStyle(color: t.hintColor)))
                : RefreshIndicator(
                    onRefresh: _load,
                    child: ListView.separated(
                      padding: const EdgeInsets.fromLTRB(12, 4, 12, 24),
                      itemCount: list.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 6),
                      itemBuilder: (context, i) {
                        final x = list[i];
                        final missed = !x.answered;
                        final (ic, col) = x.direction == 'in'
                            ? (missed ? (Icons.call_missed, kRed) : (Icons.call_received, kGreen))
                            : (missed ? (Icons.call_made, kAmber) : (Icons.call_made, kBrand));
                        return Card(
                          child: ListTile(
                            leading: CircleAvatar(backgroundColor: col.withValues(alpha: .12), child: Icon(ic, color: col, size: 20)),
                            title: Text(x.customer.isNotEmpty ? x.customer : x.number, textDirection: x.customer.isNotEmpty ? null : TextDirection.ltr,
                                textAlign: TextAlign.start, style: const TextStyle(fontWeight: FontWeight.w600)),
                            subtitle: Text([
                              if (x.customer.isNotEmpty) x.number,
                              agoText(x.started),
                              if (x.duration > 0) shortDur(x.duration) else (x.direction == 'in' ? 'فايتة' : 'مردش'),
                              if (x.via == 'sim') '📱 شريحة',
                            ].join(' · ')),
                            trailing: IconButton.filledTonal(
                              tooltip: 'اتصل',
                              icon: const Icon(Icons.call, size: 20),
                              onPressed: () {
                                HomeShell.openDial(x.number);
                                PhoneEngine.I.dial(x.number);
                              },
                            ),
                            onTap: () => HomeShell.openDial(x.number),
                          ),
                        );
                      },
                    ),
                  ),
      ),
    ]);
  }
}
