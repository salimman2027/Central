// الصفحات اللي بتتفتح من القائمة: الإعدادات، الصوت، السجل، التحويلات، التارجت، إنجازي، البحث، التذكير
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:url_launcher/url_launcher.dart';

import '../core.dart';
import '../native.dart';
import '../phone.dart';
import '../ringer.dart';
import '../store.dart';
import 'theme.dart';

typedef OnPick = void Function(String number, String name);

Future<void> _push(BuildContext context, String title, Widget body) => Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => Scaffold(
        appBar: AppBar(
          title: Text(title, style: const TextStyle(fontWeight: FontWeight.w800)),
          flexibleSpace: Container(decoration: const BoxDecoration(gradient: kHeaderGradient)),
        ),
        body: body,
      ),
    ));

Widget _card(String title, List<Widget> children, {IconData? icon}) => Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Row(children: [
              if (icon != null) ...[Icon(icon, color: kGreen, size: 20), const SizedBox(width: 8)],
              Expanded(child: Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800))),
            ]),
            const SizedBox(height: 10),
            ...children,
          ]),
        ),
      ),
    );

Widget _page(List<Widget> children) => ListView(
      padding: const EdgeInsets.all(16),
      children: [Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 640), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: children)))],
    );

void toast(BuildContext context, String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

// ======================= الإعدادات =======================
void openSettings(BuildContext context) => _push(context, 'الإعدادات', const _Settings());

class _Settings extends StatefulWidget {
  const _Settings();
  @override
  State<_Settings> createState() => _SettingsState();
}

class _SettingsState extends State<_Settings> {
  final r = Ringer.I;
  @override
  Widget build(BuildContext context) {
    final a = PhoneEngine.I.account;
    return _page([
      _card('نغمة الرنين', icon: Icons.music_note_rounded, [
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final t in Ringer.tones)
            ChoiceChip(
              label: Text(t.name),
              selected: r.current == t.id,
              onSelected: (_) async {
                await r.select(t.id);
                r.preview(t.id);
                setState(() {});
              },
            ),
          if (r.customName.isNotEmpty)
            ChoiceChip(
              label: Text('📂 ${r.customName}'),
              selected: r.current == 'custom',
              onSelected: (_) async {
                await r.select('custom');
                r.preview('custom');
                setState(() {});
              },
            ),
          ChoiceChip(
            label: const Text('🔕 صامت'),
            selected: r.current == 'silent',
            onSelected: (_) async {
              await r.select('silent');
              setState(() {});
            },
          ),
        ]),
        const SizedBox(height: 12),
        Row(children: [
          OutlinedButton.icon(
            onPressed: () async {
              final n = await r.pickCustom();
              if (n != null) {
                r.preview('custom');
                setState(() {});
              }
            },
            icon: const Icon(Icons.library_music_outlined),
            label: const Text('نغمة من جهازك'),
          ),
          const SizedBox(width: 8),
          const Icon(Icons.volume_down_rounded, size: 20, color: kMuted),
          Expanded(
            child: Slider(
              value: r.volume,
              min: .1,
              max: 1,
              onChanged: (v) {
                r.setVolume(v);
                setState(() {});
              },
              onChangeEnd: (_) => r.preview(r.current),
            ),
          ),
          const Icon(Icons.volume_up_rounded, size: 20, color: kMuted),
        ]),
      ]),
      _card('وانت في مكالمة', icon: Icons.phone_in_talk_outlined, [
        ListenableBuilder(
          listenable: PhoneEngine.I,
          builder: (context, _) => SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: PhoneEngine.I.callWaiting,
            onChanged: (v) => PhoneEngine.I.setCallWaiting(v),
            title: const Text('انتظار المكالمات', style: TextStyle(fontWeight: FontWeight.w700)),
            subtitle: Text(
              PhoneEngine.I.callWaiting
                  ? 'لو حد اتصل وانت في مكالمة: يظهرلك كارت صغير (تيت خفيفة) وتختار — ترد وتخلي اللي معاك انتظار، أو ترفض، أو «هرن عليه بعدين». المكالمة اللي معاك عمرها ما تتقطع.'
                  : 'مقفول: لو حد اتصل وانت في مكالمة ميرنش عندك خالص — المتصل ياخد مشغول، وبيظهرلك بعد المكالمة في «فاتتك» عشان ترن عليه.',
              style: const TextStyle(color: kMuted, height: 1.5),
            ),
          ),
        ),
      ]),
      _card('الصوت', icon: Icons.headset_mic_outlined, [
        const Text('اختيار السماعة والمايك واختبارهم', style: TextStyle(color: kMuted)),
        const SizedBox(height: 8),
        OutlinedButton.icon(onPressed: () => openAudio(context), icon: const Icon(Icons.tune_rounded), label: const Text('إعدادات الصوت')),
      ]),
      if (Platform.isAndroid)
        _card('عشان المكالمات توصلك والتطبيق في الخلفية', icon: Icons.battery_charging_full_rounded, [
          const Text('اسمح للتطبيق يشتغل في الخلفية من غير قيود بطارية (مهم جدًا في شاومي وسامسونج).', style: TextStyle(color: kMuted)),
          const SizedBox(height: 8),
          FilledButton.icon(onPressed: Native.ignoreBatteryOptimizations, icon: const Icon(Icons.battery_saver_rounded), label: const Text('شيل قيود البطارية')),
        ]),
      _card('الحساب', icon: Icons.person_outline_rounded, [
        if (a != null) ...[
          Text(a.name.isNotEmpty ? a.name : a.username, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
          Text('تحويلة ${a.ext} · ${Prefs.host}', style: const TextStyle(color: kMuted)),
        ],
        const SizedBox(height: 10),
        OutlinedButton.icon(
          style: OutlinedButton.styleFrom(foregroundColor: kRed),
          onPressed: () async {
            final ok = await showDialog<bool>(
              context: context,
              builder: (c) => AlertDialog(
                title: const Text('تسجيل خروج؟'),
                content: const Text('مش هتوصلك مكالمات على الجهاز ده لحد ما تدخل تاني.'),
                actions: [
                  TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('لأ')),
                  FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('خروج')),
                ],
              ),
            );
            if (ok == true && context.mounted) {
              Navigator.of(context).popUntil((r) => r.isFirst);
              PhoneEngine.I.signOut();
            }
          },
          icon: const Icon(Icons.logout_rounded),
          label: const Text('تسجيل خروج'),
        ),
      ]),
      Center(child: Text('NQ Phone $kAppVersion · ${Platform.operatingSystem}', style: const TextStyle(color: kMuted, fontSize: 12))),
    ]);
  }
}

// ======================= إعدادات الصوت =======================
void openAudio(BuildContext context) => _push(context, 'إعدادات الصوت', const _Audio());

class _Audio extends StatefulWidget {
  const _Audio();
  @override
  State<_Audio> createState() => _AudioState();
}

class _AudioState extends State<_Audio> {
  List<MediaDeviceInfo>? _devs;
  bool get _mobile => Platform.isAndroid || Platform.isIOS;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final d = await PhoneEngine.devices();
    if (mounted) setState(() => _devs = d);
  }

  Widget _list(String kind, String sel, Future<void> Function(String) pick) {
    final l = (_devs ?? []).where((d) => d.kind == kind).toList();
    if (_devs == null) return const Padding(padding: EdgeInsets.all(12), child: Center(child: CircularProgressIndicator()));
    if (l.isEmpty) return const Text('مفيش أجهزة ظاهرة — وصّل سماعة وجرّب «تحديث»', style: TextStyle(color: kMuted));
    final opts = <(String, String)>[('', 'الافتراضي بتاع الجهاز'), for (final d in l) (d.deviceId, d.label.isNotEmpty ? d.label : 'جهاز ${d.deviceId.length > 6 ? d.deviceId.substring(0, 6) : d.deviceId}')];
    return Column(children: [
      for (final o in opts)
        RadioListTile<String>(
          value: o.$1,
          groupValue: sel,
          dense: true,
          contentPadding: EdgeInsets.zero,
          title: Text(o.$2, style: const TextStyle(fontSize: 14)),
          onChanged: (v) async {
            await pick(v ?? '');
            setState(() {});
          },
        ),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    final e = PhoneEngine.I;
    return _page([
      if (!_mobile || (_devs ?? []).any((d) => d.kind == 'audiooutput'))
        _card('السماعة (الصوت بيطلع منين)', icon: Icons.headphones_rounded, [
          _list('audiooutput', Prefs.str('spkId'), e.setOutput),
        ]),
      _card('المايك', icon: Icons.mic_none_rounded, [
        _list('audioinput', Prefs.str('micId'), e.setMic),
      ]),
      _card('اختبار', icon: Icons.fact_check_outlined, [
        const Text('«مكالمة اختبار» بتتصل بخدمة الصدى في السنترال: اتكلم وهتسمع صوتك راجع — كده المايك والسماعة والخط كلهم شغالين.', style: TextStyle(color: kMuted)),
        const SizedBox(height: 10),
        Wrap(spacing: 8, runSpacing: 8, children: [
          FilledButton.icon(
            onPressed: () async {
              final err = await e.dial('*43');
              if (err != null && context.mounted) toast(context, err);
              if (err == null && context.mounted) Navigator.pop(context);
            },
            icon: const Icon(Icons.record_voice_over_rounded),
            label: const Text('مكالمة اختبار (صدى)'),
          ),
          OutlinedButton.icon(onPressed: () => Ringer.I.preview(Ringer.I.current), icon: const Icon(Icons.volume_up_rounded), label: const Text('اسمع نغمة الرنين')),
          OutlinedButton.icon(onPressed: _load, icon: const Icon(Icons.refresh_rounded), label: const Text('تحديث الأجهزة')),
        ]),
      ]),
    ]);
  }
}

/// وسط المكالمة: تختار الصوت يطلع من أنهي سماعة (كمبيوتر)
Future<void> pickOutputDuringCall(BuildContext context) async {
  final devs = (await PhoneEngine.devices()).where((d) => d.kind == 'audiooutput').toList();
  if (!context.mounted) return;
  final cur = Prefs.str('spkId');
  await showModalBottomSheet(
    context: context,
    showDragHandle: true,
    builder: (c) => SafeArea(
      child: ListView(shrinkWrap: true, children: [
        const Padding(padding: EdgeInsets.fromLTRB(20, 0, 20, 8), child: Text('الصوت يطلع من', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800))),
        if (devs.isEmpty) const ListTile(title: Text('مفيش سماعات ظاهرة')),
        for (final d in devs)
          ListTile(
            leading: Icon(d.label.toLowerCase().contains('head') || d.label.contains('سماعة') ? Icons.headphones_rounded : Icons.speaker_rounded, color: kGreen),
            title: Text(d.label.isNotEmpty ? d.label : d.deviceId),
            trailing: d.deviceId == cur ? const Icon(Icons.check_circle, color: kGreen) : null,
            onTap: () {
              PhoneEngine.I.setOutput(d.deviceId);
              Navigator.pop(c);
            },
          ),
      ]),
    ),
  );
}

// ======================= السجل =======================
void openHistory(BuildContext context, {bool sim = false, OnPick? onCall}) =>
    _push(context, sim ? 'مكالمات الشريحة' : 'سجل المكالمات', _History(initial: sim ? 'sim' : 'all', onCall: onCall));

class _History extends StatefulWidget {
  final String initial;
  final OnPick? onCall;
  const _History({required this.initial, this.onCall});
  @override
  State<_History> createState() => _HistoryState();
}

class _HistoryState extends State<_History> {
  late String _f = widget.initial;
  String _q = '';

  @override
  void initState() {
    super.initState();
    HomeData.I.refreshHistory();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: HomeData.I,
      builder: (context, _) {
        final d = HomeData.I, q = _q.trim();
        final list = d.history.where((x) {
          final ok = switch (_f) {
            'in' => x.direction == 'in',
            'out' => x.direction == 'out',
            'missed' => !x.answered,
            'sim' => x.via == 'sim',
            _ => true,
          };
          return ok && (q.isEmpty || x.number.contains(q) || x.customer.contains(q));
        }).toList();
        return Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: TextField(onChanged: (v) => setState(() => _q = v), decoration: const InputDecoration(hintText: 'دوّر برقم أو اسم عميل', prefixIcon: Icon(Icons.search), isDense: true)),
          ),
          SizedBox(
            height: 48,
            child: ListView(scrollDirection: Axis.horizontal, padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6), children: [
              for (final f in const [('all', 'الكل'), ('out', 'صادرة'), ('in', 'واردة'), ('missed', 'مردوش / فايتة'), ('sim', 'من الشريحة')])
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: ChoiceChip(label: Text(f.$2), selected: _f == f.$1, onSelected: (_) => setState(() => _f = f.$1)),
                ),
            ]),
          ),
          if (d.historyErr.isNotEmpty) Padding(padding: const EdgeInsets.all(8), child: Text(d.historyErr, style: const TextStyle(color: kRed))),
          Expanded(
            child: RefreshIndicator(
              onRefresh: d.refreshHistory,
              child: list.isEmpty
                  ? ListView(children: [Padding(padding: const EdgeInsets.all(40), child: Center(child: Text(d.loadingHistory ? 'بيحمّل…' : 'مفيش مكالمات', style: const TextStyle(color: kMuted))))])
                  : ListView.separated(
                      padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
                      itemCount: list.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 8),
                      itemBuilder: (context, i) {
                        final x = list[i];
                        final miss = !x.answered;
                        final (ic, col) = x.direction == 'in'
                            ? (miss ? (Icons.call_missed_rounded, kRed) : (Icons.call_received_rounded, kGreen))
                            : (Icons.call_made_rounded, miss ? kAmber : kGreen);
                        final name = x.customer.isNotEmpty ? x.customer : (d.personByExt(x.number)?.name ?? '');
                        final t = x.started;
                        return Card(
                          child: ListTile(
                            leading: NameAvatar(name.isNotEmpty ? name : x.number, size: 42),
                            title: Row(children: [
                              Icon(ic, size: 16, color: col),
                              const SizedBox(width: 6),
                              Expanded(child: Text(name.isNotEmpty ? name : x.number, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w700))),
                            ]),
                            subtitle: Text([
                              if (name.isNotEmpty) x.number,
                              '${t.day}/${t.month} ${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}',
                              if (x.duration > 0) fmtDur(x.duration) else (x.direction == 'in' ? 'فايتة' : 'مردش'),
                              if (x.via == 'sim') '📱 شريحة',
                            ].join(' · ')),
                            trailing: IconButton.filled(
                              style: IconButton.styleFrom(backgroundColor: kGreen),
                              icon: const Icon(Icons.call, size: 18, color: Colors.white),
                              onPressed: () {
                                Navigator.pop(context);
                                (widget.onCall ?? (n, m) => PhoneEngine.I.dial(n, name: m))(x.number, name);
                              },
                            ),
                          ),
                        );
                      },
                    ),
            ),
          ),
        ]);
      },
    );
  }
}

// ======================= التحويلات =======================
void openDirectory(BuildContext context, {OnPick? onPick}) => _push(context, 'التحويلات الداخلية', _Directory(onPick: onPick));

class _Directory extends StatefulWidget {
  final OnPick? onPick;
  const _Directory({this.onPick});
  @override
  State<_Directory> createState() => _DirectoryState();
}

class _DirectoryState extends State<_Directory> {
  String _q = '';
  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: HomeData.I,
      builder: (context, _) {
        final q = _q.trim().toLowerCase();
        const ord = {'idle': 0, 'ring': 1, 'talk': 2};
        final l = HomeData.I.people.where((p) => q.isEmpty || p.name.toLowerCase().contains(q) || p.ext.contains(q)).toList()
          ..sort((a, b) => (ord[a.state] ?? 3).compareTo(ord[b.state] ?? 3));
        return Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
            child: TextField(autofocus: true, onChanged: (v) => setState(() => _q = v), decoration: const InputDecoration(hintText: 'دوّر بالاسم أو رقم التحويلة', prefixIcon: Icon(Icons.search), isDense: true)),
          ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: HomeData.I.refreshPeople,
              child: ListView.separated(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
                itemCount: l.length,
                separatorBuilder: (_, __) => const SizedBox(height: 8),
                itemBuilder: (context, i) {
                  final p = l[i];
                  final (col, st) = switch (p.state) { 'idle' => (kGreen, 'متاح'), 'talk' => (kAmber, 'في مكالمة'), 'ring' => (kAmber, 'بيرن'), _ => (kMuted, 'مش متصل') };
                  final nm = p.name.isNotEmpty ? p.name : 'تحويلة ${p.ext}';
                  final pinned = HomeData.I.isPinned(p.ext);
                  return Card(
                    child: ListTile(
                      leading: NameAvatar(nm, size: 42, dot: p.state == 'off' ? null : col),
                      title: Text(nm, style: const TextStyle(fontWeight: FontWeight.w700)),
                      subtitle: Text('$st · ${p.ext}', style: TextStyle(color: col == kMuted ? kMuted : col)),
                      trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                        IconButton(
                          tooltip: pinned ? 'شيله من المفضلة' : 'ضيفه للمفضلة',
                          icon: Icon(pinned ? Icons.star_rounded : Icons.star_outline_rounded, color: pinned ? kAmber : kMuted),
                          onPressed: () => HomeData.I.togglePin(Fav(p.ext, nm, 'team')),
                        ),
                        IconButton.filled(
                          style: IconButton.styleFrom(backgroundColor: kGreen),
                          icon: const Icon(Icons.call, size: 18, color: Colors.white),
                          onPressed: () {
                            Navigator.pop(context);
                            (widget.onPick ?? (n, m) => PhoneEngine.I.dial(n, name: m))(p.ext, nm);
                          },
                        ),
                      ]),
                    ),
                  );
                },
              ),
            ),
          ),
        ]);
      },
    );
  }
}

// ======================= البحث بالاسم أو الرقم =======================
Future<void> openSearch(BuildContext context, {required OnPick onPick}) async {
  await showDialog(context: context, builder: (c) => _SearchDialog(onPick: onPick));
}

class _SearchDialog extends StatefulWidget {
  final OnPick onPick;
  const _SearchDialog({required this.onPick});
  @override
  State<_SearchDialog> createState() => _SearchDialogState();
}

class _SearchDialogState extends State<_SearchDialog> {
  String _q = '';
  Map<String, dynamic> _remote = {};

  Future<void> _lookup(String q) async {
    final a = PhoneEngine.I.account;
    if (a == null || q.replaceAll(RegExp(r'\D'), '').length < 7) {
      setState(() => _remote = {});
      return;
    }
    final r = await Api.lookup(a, q);
    if (mounted && _q == q) setState(() => _remote = r);
  }

  @override
  Widget build(BuildContext context) {
    final q = _q.trim().toLowerCase();
    final d = HomeData.I;
    final res = <(String, String, String)>[]; // رقم، اسم، نوع
    final seen = <String>{};
    if (q.isNotEmpty) {
      for (final p in d.people) {
        if ((p.name.toLowerCase().contains(q) || p.ext.contains(q)) && seen.add(p.ext)) res.add((p.ext, p.name.isNotEmpty ? p.name : 'تحويلة ${p.ext}', 'تحويلة ${p.ext}'));
      }
      for (final h in d.history) {
        if ((h.customer.toLowerCase().contains(q) || h.number.contains(q)) && seen.add(h.number)) res.add((h.number, h.customer.isNotEmpty ? h.customer : h.number, 'من السجل'));
      }
      final rn = '${_remote['name'] ?? ''}';
      if (rn.isNotEmpty && seen.add(_q.trim())) res.insert(0, (_q.trim(), rn, 'من الـ CRM'));
    }
    return Dialog(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 460, maxHeight: 560),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            TextField(
              autofocus: true,
              onChanged: (v) {
                setState(() => _q = v);
                _lookup(v);
              },
              decoration: const InputDecoration(hintText: 'اسم موظف أو عميل، أو رقم', prefixIcon: Icon(Icons.search)),
            ),
            const SizedBox(height: 10),
            Flexible(
              child: res.isEmpty
                  ? Padding(padding: const EdgeInsets.all(24), child: Text(q.isEmpty ? 'اكتب عشان تدوّر في التحويلات والعملاء' : 'مفيش نتايج', style: const TextStyle(color: kMuted)))
                  : ListView(shrinkWrap: true, children: [
                      for (final r in res.take(30))
                        ListTile(
                          leading: NameAvatar(r.$2, size: 38),
                          title: Text(r.$2, style: const TextStyle(fontWeight: FontWeight.w700)),
                          subtitle: Text('${r.$3}${r.$3.startsWith('تحويلة') ? '' : ' · ${r.$1}'}'),
                          trailing: const Icon(Icons.call, color: kGreen),
                          onTap: () {
                            Navigator.pop(context);
                            widget.onPick(r.$1, r.$2);
                          },
                        ),
                    ]),
            ),
          ]),
        ),
      ),
    );
  }
}

// ======================= ذكّرني =======================
Future<void> remindLater(BuildContext context, Missed m) async {
  final a = PhoneEngine.I.account;
  if (a == null) return;
  final now = DateTime.now();
  final choice = await showModalBottomSheet<DateTime>(
    context: context,
    showDragHandle: true,
    builder: (c) {
      DateTime at(int mins) => now.add(Duration(minutes: mins));
      final tomorrow10 = DateTime(now.year, now.month, now.day + 1, 10);
      return SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text('افكّرك تكلّم ${m.name.isNotEmpty ? m.name : m.number} إمتى؟', style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800)),
          const SizedBox(height: 8),
          ListTile(leading: const Icon(Icons.timer_outlined), title: const Text('بعد ربع ساعة'), onTap: () => Navigator.pop(c, at(15))),
          ListTile(leading: const Icon(Icons.timer_outlined), title: const Text('بعد ساعة'), onTap: () => Navigator.pop(c, at(60))),
          ListTile(leading: const Icon(Icons.wb_sunny_outlined), title: const Text('بكرة الساعة 10 الصبح'), onTap: () => Navigator.pop(c, tomorrow10)),
          ListTile(
            leading: const Icon(Icons.event_rounded),
            title: const Text('أختار اليوم والساعة…'),
            onTap: () async {
              final d = await showDatePicker(context: c, firstDate: now, lastDate: now.add(const Duration(days: 90)), initialDate: now);
              if (d == null || !c.mounted) return;
              final t = await showTimePicker(context: c, initialTime: TimeOfDay.fromDateTime(now.add(const Duration(hours: 1))));
              if (t == null || !c.mounted) return;
              Navigator.pop(c, DateTime(d.year, d.month, d.day, t.hour, t.minute));
            },
          ),
          const SizedBox(height: 8),
        ]),
      );
    },
  );
  if (choice == null) return;
  try {
    await Api.addTask(a, text: '📞 متابعة: ${m.name.isNotEmpty ? '${m.name} — ' : ''}${m.number} (مكالمة فايتة)', when: choice);
    PhoneEngine.I.dismissMissed(m);
    if (context.mounted) toast(context, '✔ اتسجل في مهامك — ${choice.day}/${choice.month} ${choice.hour.toString().padLeft(2, '0')}:${choice.minute.toString().padLeft(2, '0')}');
  } catch (e) {
    if (context.mounted) toast(context, '$e');
  }
}

// ======================= الاختصارات =======================
void showShortcuts(BuildContext context) {
  const rows = [
    ('Ctrl+Enter', 'اتصال بالرقم اللي في الخانة'),
    ('Enter', 'اتصال (والمؤشر في خانة الرقم) · رد على مكالمة واردة'),
    ('Esc', 'قفل / رفض المكالمة'),
    ('Ctrl+L', 'لصق رقم من الحافظة'),
    ('Ctrl+M', 'وضع مصغّر'),
    ('Ctrl+/', 'القائمة دي'),
    ('0-9 * #', 'جوه المكالمة: تبعت الأرقام (التحويلات عند العميل)'),
  ];
  showDialog(
    context: context,
    builder: (c) => AlertDialog(
      title: const Text('اختصارات الكيبورد'),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        for (final r in rows)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(children: [SizedBox(width: 100, child: Align(alignment: AlignmentDirectional.centerStart, child: Kbd(r.$1))), Expanded(child: Text(r.$2))]),
          ),
      ]),
      actions: [TextButton(onPressed: () => Navigator.pop(c), child: const Text('تمام'))],
    ),
  );
}

// ======================= التارجت =======================
void openGoals(BuildContext context) => _push(context, 'التارجت', const _Goals());

class _Goals extends StatefulWidget {
  const _Goals();
  @override
  State<_Goals> createState() => _GoalsState();
}

class _GoalsState extends State<_Goals> {
  Map<String, dynamic>? _j;
  String _err = '';
  @override
  void initState() {
    super.initState();
    final a = PhoneEngine.I.account;
    if (a != null) {
      Api.goals(a).then((j) {
        if (mounted) setState(() => _j = j);
      }).catchError((Object e) {
        if (mounted) setState(() => _err = '$e');
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_err.isNotEmpty) return Center(child: Text(_err, style: const TextStyle(color: kRed)));
    final j = _j;
    if (j == null) return const Center(child: CircularProgressIndicator());
    final metrics = (j['metrics'] as List? ?? []).cast<Map>();
    Widget period(String k, String title) {
      final p = j[k] as Map?;
      final me = p?['me'] as Map?;
      final m = (me?['m'] as Map?) ?? {};
      final rows = <Widget>[];
      for (final x in metrics) {
        final v = m[x['k']] as Map?;
        if (v == null) continue;
        final target = (v['target'] as num?) ?? 0, done = (v['done'] as num?) ?? 0;
        if (target <= 0 && done <= 0) continue;
        final pct = target > 0 ? (done / target).clamp(0, 1).toDouble() : 0.0;
        rows.add(Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Row(children: [
              Expanded(child: Text('${x['name']}', style: const TextStyle(fontWeight: FontWeight.w700))),
              Text('${_n(done)} / ${_n(target)}', textDirection: TextDirection.ltr, style: TextStyle(fontWeight: FontWeight.w800, color: pct >= 1 ? kGreen : kText)),
            ]),
            const SizedBox(height: 6),
            ClipRRect(
              borderRadius: BorderRadius.circular(99),
              child: LinearProgressIndicator(value: pct, minHeight: 9, backgroundColor: const Color(0xFFE9EEEC), color: pct >= 1 ? kGreen : (pct >= .6 ? kGreen.withValues(alpha: .75) : kAmber)),
            ),
          ]),
        ));
      }
      final rem = p?['remDays'];
      return _card(title, icon: Icons.track_changes_rounded, [
        if (rows.isEmpty) const Text('مفيش تارجت متحدد للفترة دي', style: TextStyle(color: kMuted)) else ...rows,
        if (rem != null && k != 'day') Text('باقي $rem يوم شغل', style: const TextStyle(color: kMuted, fontSize: 12.5)),
      ]);
    }

    return _page([period('day', 'النهارده'), period('month', 'الشهر ده'), period('quarter', 'الربع ده')]);
  }

  String _n(num v) => v == v.roundToDouble() ? v.toInt().toString() : v.toStringAsFixed(1);
}

// ======================= إنجازي =======================
void openPerf(BuildContext context) => _push(context, 'إنجازي', const _Perf());

class _Perf extends StatefulWidget {
  const _Perf();
  @override
  State<_Perf> createState() => _PerfState();
}

class _PerfState extends State<_Perf> {
  String _p = 'day', _err = '';
  Map<String, dynamic>? _j;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final a = PhoneEngine.I.account;
    if (a == null) return;
    setState(() {
      _j = null;
      _err = '';
    });
    try {
      final j = await Api.me(a, _p);
      if (mounted) setState(() => _j = j);
    } catch (e) {
      if (mounted) setState(() => _err = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = (_j?['summary'] as Map?) ?? {};
    Widget tile(String l, String v, IconData ic) => Expanded(
          child: Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(ic, color: kGreen, size: 20),
                const SizedBox(height: 8),
                Text(v, style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
                Text(l, style: const TextStyle(color: kMuted)),
              ]),
            ),
          ),
        );
    return _page([
      SegmentedButton<String>(
        segments: const [ButtonSegment(value: 'day', label: Text('النهارده')), ButtonSegment(value: 'week', label: Text('الأسبوع')), ButtonSegment(value: 'month', label: Text('الشهر'))],
        selected: {_p},
        onSelectionChanged: (v) {
          _p = v.first;
          _load();
        },
      ),
      const SizedBox(height: 14),
      if (_err.isNotEmpty) Text(_err, style: const TextStyle(color: kRed)),
      if (_j == null && _err.isEmpty) const Padding(padding: EdgeInsets.all(30), child: Center(child: CircularProgressIndicator())),
      if (_j != null) ...[
        Row(children: [tile('مكالماتي', '${s['calls'] ?? 0}', Icons.call_rounded), const SizedBox(width: 10), tile('اترد عليها', '${s['answered'] ?? 0}', Icons.call_received_rounded)]),
        const SizedBox(height: 10),
        Row(children: [
          tile('اتحلّلت', '${s['analyzed'] ?? 0}', Icons.insights_rounded),
          const SizedBox(width: 10),
          tile('متوسط تقييمي', s['avgScore'] == null ? '—' : '${s['avgScore']}/100', Icons.star_rounded),
        ]),
        const SizedBox(height: 14),
        if (_j!['focus'] is Map)
          _card('أهم حاجة تشتغل عليها (من المدير)', icon: Icons.flag_rounded, [Text('${(_j!['focus'] as Map)['focus'] ?? ''}', style: const TextStyle(height: 1.7))]),
      ],
    ]);
  }
}

// ======================= الملفات / واتساب (مؤقتًا من نسخة الويب) =======================
Future<void> openWeb(BuildContext context, String what, String title) async {
  final ok = await showDialog<bool>(
    context: context,
    builder: (c) => AlertDialog(
      title: Text(title),
      content: Text('«$title» لسه هيتنقل للبرنامج الجديد في المرحلة الجاية.\nدلوقتي تقدر تفتحه من نسخة الويب في المتصفح.'),
      actions: [
        TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('إلغاء')),
        FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('افتح في المتصفح')),
      ],
    ),
  );
  if (ok == true) await launchUrl(Uri.parse('https://${Prefs.host}/phone/'), mode: LaunchMode.externalApplication);
}
