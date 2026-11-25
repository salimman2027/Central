// الشاشة الرئيسية (حسب مستند التسليم): الهيدر الأخضر، كارت الاتصال، المكالمات الفايتة،
// المفضلة وحالة الفريق، التبويبات، آخر المكالمات، لوحة الأرقام، وشريط الاختصارات
import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core.dart';
import '../native.dart';
import '../phone.dart';
import '../store.dart';
import '../win.dart';
import 'menu.dart';
import 'pages.dart';
import 'theme.dart';

class Dial {
  static final ctrl = TextEditingController();
  static final focus = FocusNode();
  static void set(String n) {
    ctrl.text = n;
    ctrl.selection = TextSelection.collapsed(offset: n.length);
  }
}

class CallIntent extends Intent {
  const CallIntent();
}

class PasteIntent extends Intent {
  const PasteIntent();
}

class CompactIntent extends Intent {
  const CompactIntent();
}

class HelpIntent extends Intent {
  const HelpIntent();
}

class MainScreen extends StatefulWidget {
  const MainScreen({super.key});
  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  String _tab = 'pbx', _err = '';
  Map<String, dynamic> _who = {};
  Timer? _t;
  bool get _mobile => Platform.isAndroid || Platform.isIOS;

  @override
  void initState() {
    super.initState();
    HomeData.I.start();
    Dial.ctrl.addListener(_changed);
    Win.incomingNumber.addListener(_fromCrm);
    _fromCrm();
    if (!_mobile) WidgetsBinding.instance.addPostFrameCallback((_) => Dial.focus.requestFocus());
  }

  @override
  void dispose() {
    Dial.ctrl.removeListener(_changed);
    Win.incomingNumber.removeListener(_fromCrm);
    _t?.cancel();
    super.dispose();
  }

  /// Click-to-Call من الـ CRM: الرقم بيتحط في الخانة وكارت العميل يظهر
  void _fromCrm() {
    final n = Win.incomingNumber.value;
    if (n.isEmpty) return;
    Win.incomingNumber.value = '';
    Win.toggleCompact(false);
    Dial.set(n);
    Dial.focus.requestFocus();
  }

  void _changed() {
    if (_err.isNotEmpty || PhoneEngine.I.lastOutcome != null || PhoneEngine.I.notice.isNotEmpty) {
      _err = '';
      PhoneEngine.I.clearNotice();
    }
    _t?.cancel();
    final n = Dial.ctrl.text;
    if (n.replaceAll(RegExp(r'\D'), '').length < 7) {
      if (_who.isNotEmpty) setState(() => _who = {});
      return;
    }
    _t = Timer(const Duration(milliseconds: 450), () async {
      final a = PhoneEngine.I.account;
      if (a == null) return;
      final v = await Api.lookup(a, n);
      if (mounted && Dial.ctrl.text == n) setState(() => _who = v);
    });
    setState(() {});
  }

  Future<void> _call([String? n, String name = '']) async {
    final num = (n ?? Dial.ctrl.text).trim();
    if (num.isEmpty) {
      if (PhoneEngine.I.lastNumber.isNotEmpty) Dial.set(PhoneEngine.I.lastNumber);
      return;
    }
    if (n != null) Dial.set(num);
    final err = await PhoneEngine.I.dial(num, name: name.isNotEmpty ? name : '${_who['name'] ?? ''}');
    if (mounted) setState(() => _err = err ?? '');
  }

  Future<void> _paste() async {
    final d = await Clipboard.getData(Clipboard.kTextPlain);
    final n = cleanPhone(d?.text ?? '');
    if (n.length < 3) {
      _toast('مفيش رقم في الحاجة اللي اتنسخت');
      return;
    }
    Dial.set(n);
    Dial.focus.requestFocus();
  }

  void _key(String d) {
    final c = Dial.ctrl, t = c.text, s = c.selection;
    final a = s.isValid ? s.start : t.length, b = s.isValid ? s.end : t.length;
    c.text = t.substring(0, a) + d + t.substring(b);
    c.selection = TextSelection.collapsed(offset: a + d.length);
    HapticFeedback.selectionClick();
  }

  void _del({bool all = false}) {
    final c = Dial.ctrl;
    if (all) return c.clear();
    final t = c.text, s = c.selection;
    var a = s.isValid ? s.start : t.length, b = s.isValid ? s.end : t.length;
    if (a == b) {
      if (a == 0) return;
      a -= 1;
    }
    c.text = t.substring(0, a) + t.substring(b);
    c.selection = TextSelection.collapsed(offset: a);
  }

  void _toast(String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m), duration: const Duration(seconds: 2)));

  @override
  Widget build(BuildContext context) {
    return Shortcuts(
      shortcuts: const {
        SingleActivator(LogicalKeyboardKey.enter, control: true): CallIntent(),
        SingleActivator(LogicalKeyboardKey.numpadEnter, control: true): CallIntent(),
        SingleActivator(LogicalKeyboardKey.keyL, control: true): PasteIntent(),
        SingleActivator(LogicalKeyboardKey.keyM, control: true): CompactIntent(),
        SingleActivator(LogicalKeyboardKey.slash, control: true): HelpIntent(),
      },
      child: Actions(
        actions: {
          CallIntent: CallbackAction<CallIntent>(onInvoke: (_) => _call()),
          PasteIntent: CallbackAction<PasteIntent>(onInvoke: (_) => _paste()),
          CompactIntent: CallbackAction<CompactIntent>(onInvoke: (_) => Win.toggleCompact()),
          HelpIntent: CallbackAction<HelpIntent>(onInvoke: (_) => showShortcuts(context)),
        },
        child: Focus(
          autofocus: true,
          child: ValueListenableBuilder<bool>(
            valueListenable: Win.compact,
            builder: (context, compact, _) => compact ? _compactBar() : _full(),
          ),
        ),
      ),
    );
  }

  // ======================= الوضع المصغّر =======================
  Widget _compactBar() {
    return ListenableBuilder(
      listenable: PhoneEngine.I,
      builder: (context, _) {
        final e = PhoneEngine.I;
        return Scaffold(
          backgroundColor: Colors.transparent,
          body: Container(
            decoration: const BoxDecoration(gradient: kHeaderGradient),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
            child: Row(children: [
              _LineDot(state: e.line),
              const SizedBox(width: 10),
              Expanded(child: _numberField(compact: true)),
              const SizedBox(width: 8),
              _CallButton(size: 42, onTap: () => _call()),
              const SizedBox(width: 4),
              IconButton(
                tooltip: 'رجوع للشاشة الكاملة (Ctrl+M)',
                onPressed: () => Win.toggleCompact(false),
                icon: const Icon(Icons.open_in_full_rounded, color: Colors.white, size: 20),
              ),
            ]),
          ),
        );
      },
    );
  }

  // ======================= الشاشة الكاملة =======================
  Widget _full() {
    return Scaffold(
      body: ListenableBuilder(
        listenable: Listenable.merge([PhoneEngine.I, HomeData.I]),
        builder: (context, _) {
          final e = PhoneEngine.I;
          return LayoutBuilder(builder: (context, box) {
            final wide = box.maxWidth > 760;
            final content = Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              for (final m in e.missed.reversed.take(1)) _MissedBanner(m: m, onCall: () => _call(m.number, m.name)),
              _favorites(),
              const SizedBox(height: 14),
              _tabs(),
              const SizedBox(height: 12),
              _tabBody(),
            ]);
            final pad = _keypadBlock();
            return Column(children: [
              _Header(),
              Expanded(
                child: Stack(children: [
                  // الهيدر بيكمّل تحت كارت الاتصال
                  Container(height: 70, decoration: const BoxDecoration(gradient: LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [Color(0xFF075F30), kHeaderBottom]))),
                  SingleChildScrollView(
                    padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                    child: Center(
                      child: ConstrainedBox(
                        constraints: BoxConstraints(maxWidth: wide ? 1000 : 560),
                        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                          _dialCard(),
                          const SizedBox(height: 14),
                          if (wide)
                            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                              Expanded(child: content),
                              const SizedBox(width: 20),
                              SizedBox(width: 380, child: pad),
                            ])
                          else ...[
                            content,
                            const SizedBox(height: 16),
                            pad,
                          ],
                        ]),
                      ),
                    ),
                  ),
                ]),
              ),
            ]);
          });
        },
      ),
    );
  }

  Widget _numberField({bool compact = false}) {
    return Shortcuts(
      shortcuts: const {SingleActivator(LogicalKeyboardKey.enter): CallIntent(), SingleActivator(LogicalKeyboardKey.numpadEnter): CallIntent()},
      child: TextField(
        controller: Dial.ctrl,
        focusNode: Dial.focus,
        textDirection: TextDirection.ltr,
        textAlign: TextAlign.right,
        keyboardType: _mobile ? TextInputType.none : TextInputType.phone,
        showCursor: true,
        inputFormatters: [_PhonePasteFormatter()],
        style: TextStyle(fontSize: compact ? 18 : 24, fontWeight: FontWeight.w600, letterSpacing: .8, color: compact ? kText : kText),
        decoration: InputDecoration(
          hintText: 'اكتب الرقم أو الصقه هنا',
          hintTextDirection: TextDirection.rtl,
          hintStyle: TextStyle(fontSize: compact ? 14 : 17, color: kMuted, fontWeight: FontWeight.w400, letterSpacing: 0),
          isDense: true,
          contentPadding: EdgeInsets.symmetric(horizontal: compact ? 12 : 4, vertical: compact ? 10 : 8),
          filled: compact,
          fillColor: Colors.white,
          border: compact ? OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none) : InputBorder.none,
          enabledBorder: compact ? OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none) : InputBorder.none,
          focusedBorder: compact ? OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none) : InputBorder.none,
        ),
      ),
    );
  }

  // ---------- 3.3 كارت الاتصال ----------
  Widget _dialCard() {
    final e = PhoneEngine.I, o = e.lastOutcome;
    final name = '${_who['name'] ?? ''}';
    return Container(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 14),
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(22), boxShadow: softShadow(.10)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Expanded(child: _numberField()),
          if (Dial.ctrl.text.isNotEmpty)
            IconButton(tooltip: 'مسح', onPressed: () => Dial.ctrl.clear(), icon: const Icon(Icons.close_rounded, color: kMuted, size: 20)),
          const SizedBox(width: 6),
          _SoftBtn(icon: Icons.content_paste_rounded, label: 'لصق', onTap: _paste),
          const SizedBox(width: 8),
          _SoftBtn(icon: Icons.search_rounded, label: 'بحث', onTap: () => openSearch(context, onPick: (n, nm) => _call(n, nm))),
        ]),
        if (name.isNotEmpty) _WhoCard(who: _who, onCall: () => _call()),
        const SizedBox(height: 12),
        Wrap(alignment: _mobile ? WrapAlignment.spaceBetween : WrapAlignment.end, crossAxisAlignment: WrapCrossAlignment.center, spacing: 10, runSpacing: 8, children: [
          if (_mobile) _LinePicker(via: e.via, onChanged: e.setVia),
          _QualityBadge(q: e.quality, rtt: e.rttMs, online: e.line == LineState.online),
        ]),
        if (_err.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 10), child: Text(_err, textAlign: TextAlign.center, style: const TextStyle(color: kRed, fontWeight: FontWeight.w600))),
        if (e.notice.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 10), child: Text(e.notice, textAlign: TextAlign.center, style: const TextStyle(color: kGreen, fontWeight: FontWeight.w600))),
        if (o != null && _err.isEmpty) _OutcomeCard(o: o, onRetry: () => _call(e.lastNumber)),
      ]),
    );
  }

  // ---------- 3.5 المفضلة وحالة الفريق ----------
  Widget _favorites() {
    final favs = HomeData.I.favorites;
    if (favs.isEmpty) return const SizedBox.shrink();
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      const SizedBox(height: 14),
      Row(children: [
        const Text('المفضلة وحالة الفريق', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5, color: kText)),
        const Spacer(),
        _legend(kGreen, 'متاح'),
        const SizedBox(width: 12),
        _legend(kAmber, 'في مكالمة'),
      ]),
      const SizedBox(height: 10),
      SizedBox(
        height: 132,
        child: ListView.separated(
          scrollDirection: Axis.horizontal,
          itemCount: favs.length,
          separatorBuilder: (_, __) => const SizedBox(width: 10),
          itemBuilder: (context, i) {
            final f = favs[i];
            final p = f.kind == 'team' ? HomeData.I.personByExt(f.number) : null;
            final (col, st) = p == null
                ? (null, 'عميل')
                : switch (p.state) { 'idle' => (kGreen, 'متاحة'), 'talk' => (kAmber, 'في مكالمة'), 'ring' => (kAmber, 'بيرن'), _ => (kMuted, 'مش متصل') };
            return _FavCard(
              name: f.name,
              status: st,
              statusColor: p == null ? kGreen : (p.state == 'idle' ? kGreen : (p.state == 'off' ? kMuted : kAmber)),
              dot: col,
              pinned: HomeData.I.isPinned(f.number),
              onTap: () => _call(f.number, f.name),
              onLong: () => HomeData.I.togglePin(f),
            );
          },
        ),
      ),
    ]);
  }

  Widget _legend(Color c, String t) => Row(children: [
        Container(width: 7, height: 7, decoration: BoxDecoration(color: c, shape: BoxShape.circle)),
        const SizedBox(width: 5),
        Text(t, style: const TextStyle(fontSize: 12, color: kMuted)),
      ]);

  // ---------- 3.6 التبويبات ----------
  Widget _tabs() {
    Widget seg(String k, String t) {
      final on = _tab == k;
      return Expanded(
        child: GestureDetector(
          onTap: () => setState(() => _tab = k),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 180),
            height: 42,
            decoration: BoxDecoration(color: on ? Colors.white : Colors.transparent, borderRadius: BorderRadius.circular(12), boxShadow: on ? softShadow(.08) : null),
            child: Center(child: Text(t, style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: on ? kGreen : const Color(0xFF52615A)))),
          ),
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(color: const Color(0xFFE9EEEC), borderRadius: BorderRadius.circular(15)),
      child: Row(children: [seg('pbx', 'السنترال'), seg('ext', 'التحويلات'), if (_mobile) seg('sim', 'من شريحتي')]),
    );
  }

  Widget _tabBody() {
    final d = HomeData.I;
    if (_tab == 'ext') {
      final list = [...d.people]..sort((a, b) => _ord(a.state).compareTo(_ord(b.state)));
      return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _sectionHead(Icons.people_alt_outlined, 'التحويلات الداخلية', () => openDirectory(context, onPick: (n, nm) => _call(n, nm))),
        if (d.peopleErr.isNotEmpty && list.isEmpty) _emptyNote(d.peopleErr),
        for (final p in list.take(5))
          _RecentCard(
            name: p.name.isNotEmpty ? p.name : 'تحويلة ${p.ext}',
            sub: 'تحويلة ${p.ext}',
            right: switch (p.state) { 'idle' => 'متاح', 'talk' => 'في مكالمة', 'ring' => 'بيرن', _ => 'مش متصل' },
            rightColor: p.state == 'idle' ? kGreen : (p.state == 'off' ? kMuted : kAmber),
            dot: p.state == 'idle' ? kGreen : (p.state == 'off' ? null : kAmber),
            onCall: () => _call(p.ext, p.name),
            onLong: () => HomeData.I.togglePin(Fav(p.ext, p.name.isNotEmpty ? p.name : 'تحويلة ${p.ext}', 'team')),
          ),
      ]);
    }
    final sim = _tab == 'sim';
    final items = d.history.where((h) => sim ? h.via == 'sim' : h.via != 'sim').take(3).toList();
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      _sectionHead(Icons.schedule_rounded, sim ? 'آخر مكالمات الشريحة' : 'آخر المكالمات', () => openHistory(context, sim: sim, onCall: (n, nm) => _call(n, nm))),
      if (items.isEmpty)
        _emptyNote(d.historyErr.isNotEmpty ? d.historyErr : (d.loadingHistory ? 'بيحمّل…' : (sim ? 'مفيش مكالمات شريحة متسجلة' : 'مفيش مكالمات لسه')))
      else
        for (final h in items)
          _RecentCard(
            name: h.customer.isNotEmpty ? h.customer : (HomeData.I.personByExt(h.number)?.name ?? h.number),
            sub: h.number.length <= 5 ? 'تحويلة ${h.number}' : h.number,
            right: h.answered ? '${fmtDur(h.duration)} د' : (h.direction == 'in' ? 'فائتة' : 'مردش'),
            rightColor: h.answered ? kGreen : kRed,
            time: _hm(h.started),
            dirIcon: h.direction == 'in' ? (h.answered ? Icons.call_received_rounded : Icons.call_missed_rounded) : Icons.call_made_rounded,
            dirColor: h.answered ? kGreen : kRed,
            onCall: () => _call(h.number, h.customer),
            onLong: () => HomeData.I.togglePin(Fav(h.number, h.customer.isNotEmpty ? h.customer : h.number, h.number.length <= 5 ? 'team' : 'customer')),
          ),
    ]);
  }

  int _ord(String s) => const {'idle': 0, 'ring': 1, 'talk': 2}[s] ?? 3;

  Widget _sectionHead(IconData ic, String t, VoidCallback all) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Row(children: [
          Icon(ic, size: 18, color: kMuted),
          const SizedBox(width: 6),
          Text(t, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5, color: Color(0xFF52615A))),
          const Spacer(),
          TextButton.icon(onPressed: all, iconAlignment: IconAlignment.end, icon: const Icon(Icons.west_rounded, size: 16, color: kGreen), label: const Text('عرض الكل', style: TextStyle(color: kGreen, fontWeight: FontWeight.w700))),
        ]),
      );

  Widget _emptyNote(String t) => Padding(padding: const EdgeInsets.all(18), child: Text(t, textAlign: TextAlign.center, style: const TextStyle(color: kMuted)));

  // ---------- 3.8 لوحة الأرقام + 3.9 الاختصارات ----------
  Widget _keypadBlock() {
    const rows = [
      [('1', ''), ('2', 'ABC'), ('3', 'DEF')],
      [('4', 'GHI'), ('5', 'JKL'), ('6', 'MNO')],
      [('7', 'PQRS'), ('8', 'TUV'), ('9', 'WXYZ')],
      [('*', ''), ('0', '+'), ('#', '')],
    ];
    return Directionality(
      textDirection: TextDirection.ltr,
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 380),
          child: Column(children: [
            for (final r in rows)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Row(children: [
                  for (var i = 0; i < 3; i++) ...[
                    if (i > 0) const SizedBox(width: 12),
                    Expanded(child: _Key(d: r[i].$1, sub: r[i].$2, onTap: () => _key(r[i].$1), onLong: r[i].$1 == '0' ? () => _key('+') : null)),
                  ],
                ]),
              ),
            const SizedBox(height: 8),
            Row(children: [
              const Expanded(child: SizedBox()),
              _CallButton(size: 78, onTap: () => _call()),
              Expanded(
                child: Align(
                  alignment: const Alignment(.45, 0),
                  child: GestureDetector(
                    onLongPress: () => _del(all: true),
                    child: Tooltip(
                      message: 'مسح (ضغطة طويلة = مسح الكل)',
                      child: InkWell(
                        onTap: _del,
                        customBorder: const CircleBorder(),
                        child: Container(
                          width: 58,
                          height: 58,
                          decoration: BoxDecoration(shape: BoxShape.circle, color: Colors.white, border: Border.all(color: kLine, width: 1.4)),
                          child: const Icon(Icons.backspace_outlined, color: Color(0xFF52615A), size: 22),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ]),
          ]),
        ),
      ),
    );
  }

  static String _hm(DateTime t) {
    final h = t.hour % 12 == 0 ? 12 : t.hour % 12;
    final now = DateTime.now();
    final same = t.year == now.year && t.month == now.month && t.day == now.day;
    final s = '$h:${t.minute.toString().padLeft(2, '0')} ${t.hour < 12 ? 'ص' : 'م'}';
    return same ? s : '${t.day}/${t.month} $s';
  }
}

/// اللصق في الخانة بينضّف الرقم (مسافات، شرط، أرقام عربي…)
class _PhonePasteFormatter extends TextInputFormatter {
  @override
  TextEditingValue formatEditUpdate(TextEditingValue o, TextEditingValue n) {
    final clean = n.text.replaceAll(RegExp(r'[^\d+*#٠-٩۰-۹]'), '');
    final t = cleanPhone(clean).isEmpty && clean.isNotEmpty ? clean : cleanPhone(clean);
    if (t == n.text) return n;
    final off = (n.selection.end - (n.text.length - t.length)).clamp(0, t.length);
    return TextEditingValue(text: t, selection: TextSelection.collapsed(offset: off));
  }
}

// ======================= 3.1 + 3.2 الهيدر =======================
class _Header extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final e = PhoneEngine.I, a = e.account, d = HomeData.I;
    final plat = Platform.isWindows ? 'Win' : (Platform.isAndroid ? 'Android' : (Platform.isIOS ? 'iOS' : 'App'));
    final ver = '$plat $kAppVersion${d.serverVer.isNotEmpty ? ' - ${d.serverVer}' : ''}';
    return Container(
      decoration: const BoxDecoration(gradient: LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [kHeaderTop, Color(0xFF075F30)])),
      child: SafeArea(
        bottom: false,
        child: Column(children: [
          const TitleBar(),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 6, 16, 14),
            child: LayoutBuilder(builder: (context, box) => Row(children: [
              if (!Native.isDesktop && box.maxWidth > 440) ...[
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  decoration: BoxDecoration(color: Colors.white.withValues(alpha: .1), borderRadius: BorderRadius.circular(99), border: Border.all(color: Colors.white24)),
                  child: Text(ver, textDirection: TextDirection.ltr, style: const TextStyle(color: Colors.white, fontSize: 12.5)),
                ),
                const SizedBox(width: 10),
              ],
              if (a != null)
                Expanded(
                  child: Tooltip(
                    message: ver,
                    child: Text('تحويلة ${a.ext}${a.name.isNotEmpty ? ' · ${a.name}' : ''}',
                        overflow: TextOverflow.ellipsis, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 16)),
                  ),
                )
              else
                const Spacer(),
              const SizedBox(width: 8),
              _LinePill(state: e.line, text: e.lineText, onRetry: e.reconnect, quality: e.quality),
              const SizedBox(width: 10),
              const NQMenuButton(),
            ])),
          ),
        ]),
      ),
    );
  }
}

class _LineDot extends StatefulWidget {
  final LineState state;
  const _LineDot({required this.state});
  @override
  State<_LineDot> createState() => _LineDotState();
}

class _LineDotState extends State<_LineDot> with SingleTickerProviderStateMixin {
  late final AnimationController _a = AnimationController(vsync: this, duration: const Duration(milliseconds: 1400))..repeat(reverse: true);
  @override
  void dispose() {
    _a.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = switch (widget.state) { LineState.online => const Color(0xFF4ADE80), LineState.connecting => kAmber, _ => const Color(0xFFFF6B6B) };
    return AnimatedBuilder(
      animation: _a,
      builder: (_, __) => Container(
        width: 10,
        height: 10,
        decoration: BoxDecoration(color: c, shape: BoxShape.circle, boxShadow: [BoxShadow(color: c.withValues(alpha: .25 + .5 * _a.value), blurRadius: 4 + 6 * _a.value, spreadRadius: 1.5 * _a.value)]),
      ),
    );
  }
}

class _LinePill extends StatelessWidget {
  final LineState state;
  final String text;
  final VoidCallback onRetry;
  final Quality quality;
  const _LinePill({required this.state, required this.text, required this.onRetry, required this.quality});
  @override
  Widget build(BuildContext context) {
    final bad = state == LineState.failed || state == LineState.off;
    final weak = state == LineState.online && (quality == Quality.bad || quality == Quality.weak);
    return Tooltip(
      message: bad ? 'دوس عشان يحاول يتصل تاني' : text,
      child: InkWell(
        onTap: bad ? onRetry : null,
        borderRadius: BorderRadius.circular(99),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
          decoration: BoxDecoration(
            color: bad ? kRed.withValues(alpha: .9) : Colors.white.withValues(alpha: .1),
            borderRadius: BorderRadius.circular(99),
            border: Border.all(color: bad ? Colors.transparent : Colors.white24),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            _LineDot(state: state),
            const SizedBox(width: 8),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 130),
              child: Text(bad ? 'السنترال مقطوع' : (state == LineState.connecting ? 'بيتصل…' : 'متصل بالسنترال'),
                  overflow: TextOverflow.ellipsis, style: const TextStyle(color: Colors.white, fontSize: 13.5, fontWeight: FontWeight.w600)),
            ),
            if (weak) ...[
              const SizedBox(width: 6),
              Container(padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1), decoration: BoxDecoration(color: kRed, borderRadius: BorderRadius.circular(99)), child: const Text('ضعيف', style: TextStyle(color: Colors.white, fontSize: 11))),
            ],
          ]),
        ),
      ),
    );
  }
}

class _SoftBtn extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  const _SoftBtn({required this.icon, required this.label, required this.onTap});
  @override
  Widget build(BuildContext context) => Material(
        color: const Color(0xFFF3F6F5),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14), side: const BorderSide(color: kLine)),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(icon, size: 18, color: kText),
              const SizedBox(width: 6),
              Text(label, style: const TextStyle(fontWeight: FontWeight.w600, color: kText)),
            ]),
          ),
        ),
      );
}

class _LinePicker extends StatelessWidget {
  final String via;
  final ValueChanged<String> onChanged;
  const _LinePicker({required this.via, required this.onChanged});
  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<String>(
      tooltip: 'الخط الصادر',
      onSelected: onChanged,
      position: PopupMenuPosition.under,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      itemBuilder: (_) => const [
        PopupMenuItem(value: 'pbx', child: ListTile(leading: Icon(Icons.business_rounded, color: kGreen), title: Text('السنترال'), subtitle: Text('من الكمبيوتر بخط الشركة'), dense: true)),
        PopupMenuItem(value: 'sim', child: ListTile(leading: Icon(Icons.smartphone_rounded, color: kGreen), title: Text('شريحة الموبايل'), subtitle: Text('موبايلك يتصل (تطبيق NQ Phone)'), dense: true)),
      ],
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(borderRadius: BorderRadius.circular(12), border: Border.all(color: kLine)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.call_outlined, size: 17, color: kMuted),
          const SizedBox(width: 6),
          const Text('الخط الصادر: ', style: TextStyle(color: kText)),
          Text(via == 'sim' ? 'شريحتي' : 'السنترال', style: const TextStyle(color: kGreen, fontWeight: FontWeight.w800)),
          const SizedBox(width: 4),
          const Icon(Icons.arrow_drop_down_rounded, color: kText),
        ]),
      ),
    );
  }
}

class _QualityBadge extends StatelessWidget {
  final Quality q;
  final int? rtt;
  final bool online;
  const _QualityBadge({required this.q, required this.rtt, required this.online});
  @override
  Widget build(BuildContext context) {
    final (bars, col, txt) = !online
        ? (0, kRed, 'مفيش اتصال')
        : switch (q) {
            Quality.excellent => (4, kGreen, 'جودة الاتصال ممتازة'),
            Quality.good => (3, kGreen, 'جودة الاتصال كويسة'),
            Quality.weak => (2, kAmber, 'الاتصال ضعيف'),
            Quality.bad => (1, kRed, 'الاتصال سيء جدًا'),
            Quality.unknown => (0, kMuted, 'بيقيس الجودة…'),
          };
    return Tooltip(
      message: rtt == null ? txt : '$txt — التأخير $rtt ms',
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Directionality(
          textDirection: TextDirection.ltr,
          child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
            for (var i = 0; i < 4; i++)
              Container(
                margin: const EdgeInsets.only(right: 2),
                width: 4,
                height: 6.0 + i * 3,
                decoration: BoxDecoration(color: i < bars ? col : kLine, borderRadius: BorderRadius.circular(1.5)),
              ),
          ]),
        ),
        const SizedBox(width: 6),
        Text(txt, style: TextStyle(color: col, fontWeight: FontWeight.w700, fontSize: 13)),
      ]),
    );
  }
}

/// بطاقة تعرّف: لما الرقم المكتوب يبقى عميل مسجّل
class _WhoCard extends StatelessWidget {
  final Map<String, dynamic> who;
  final VoidCallback onCall;
  const _WhoCard({required this.who, required this.onCall});
  @override
  Widget build(BuildContext context) {
    final name = '${who['name'] ?? ''}';
    final ctx = ActiveCall.describe(who);
    final note = who['note'] is Map ? '${(who['note'] as Map)['text'] ?? (who['note'] as Map)['summary'] ?? ''}' : '';
    return Container(
      margin: const EdgeInsets.only(top: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: const Color(0xFFF1FAF5), borderRadius: BorderRadius.circular(14), border: Border.all(color: const Color(0xFFCDEBD9))),
      child: Row(children: [
        NameAvatar(name, size: 40),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(name, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15)),
            if (ctx.isNotEmpty) Text(ctx, style: const TextStyle(color: kMuted, fontSize: 12.5)),
            if (note.isNotEmpty) Text('📝 $note', maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: kMuted, fontSize: 12.5)),
          ]),
        ),
        FilledButton.icon(onPressed: onCall, icon: const Icon(Icons.call, size: 18), label: const Text('اتصال')),
      ]),
    );
  }
}

class _OutcomeCard extends StatelessWidget {
  final Outcome o;
  final VoidCallback onRetry;
  const _OutcomeCard({required this.o, required this.onRetry});
  @override
  Widget build(BuildContext context) {
    final c = switch (o.key) { 'busy' || 'reject' => kRed, 'noans' || 'net' => kAmber, _ => const Color(0xFF64748B) };
    return Container(
      margin: const EdgeInsets.only(top: 12),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(color: c.withValues(alpha: .08), border: Border.all(color: c.withValues(alpha: .4)), borderRadius: BorderRadius.circular(14)),
      child: Row(children: [
        Text(o.icon, style: const TextStyle(fontSize: 22)),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(o.title, style: TextStyle(fontSize: 15, fontWeight: FontWeight.w800, color: c == kAmber ? const Color(0xFFB45309) : c)),
            if (o.detail.isNotEmpty) Text(o.detail, style: const TextStyle(fontSize: 12.5, color: kMuted)),
          ]),
        ),
        if (o.retry) FilledButton.icon(onPressed: onRetry, icon: const Icon(Icons.replay_rounded, size: 18), label: const Text('اتصل تاني')),
      ]),
    );
  }
}

// ======================= 3.4 بانر المكالمة الفايتة =======================
class _MissedBanner extends StatelessWidget {
  final Missed m;
  final VoidCallback onCall;
  const _MissedBanner({required this.m, required this.onCall});
  @override
  Widget build(BuildContext context) {
    final t = _MainScreenState._hm(m.at);
    final rings = (m.ringSec / 4).round().clamp(1, 20);
    final ringTxt = rings == 1 ? 'رنّت مرة' : (rings == 2 ? 'رنّت مرتين' : 'رنّت $rings مرات');
    final who = m.name.isNotEmpty ? m.name : m.number;
    return Container(
      margin: const EdgeInsets.only(bottom: 4),
      decoration: BoxDecoration(color: kRedBg, borderRadius: BorderRadius.circular(16), border: Border.all(color: const Color(0xFFF5C2C2))),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(16),
        child: Container(
          decoration: const BoxDecoration(border: BorderDirectional(start: BorderSide(color: kRed, width: 4))),
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 12),
          child: LayoutBuilder(builder: (context, box) {
            final narrow = box.maxWidth < 480;
            final buttons = Row(mainAxisSize: MainAxisSize.min, children: [
              FilledButton(onPressed: onCall, style: FilledButton.styleFrom(minimumSize: const Size(0, 40), padding: const EdgeInsets.symmetric(horizontal: 14)), child: const Text('اتصل الآن')),
              const SizedBox(width: 6),
              OutlinedButton(
                onPressed: () => remindLater(context, m),
                style: OutlinedButton.styleFrom(foregroundColor: const Color(0xFF991B1B), side: const BorderSide(color: Color(0xFFF5C2C2)), minimumSize: const Size(0, 40), backgroundColor: Colors.white),
                child: const Text('ذكّرني'),
              ),
            ]);
            final info = Row(children: [
              Container(
                width: 44,
                height: 44,
                decoration: const BoxDecoration(color: Color(0xFFFFE1E1), shape: BoxShape.circle),
                child: const Icon(Icons.phone_missed_rounded, color: kRed),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('مكالمة فائتة: $who', maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: Color(0xFF991B1B), fontWeight: FontWeight.w800, fontSize: 15.5)),
                  const SizedBox(height: 2),
                  Text([t, ringTxt, if (m.context.isNotEmpty) m.context, if (m.name.isNotEmpty) m.number].join(' · '),
                      style: const TextStyle(color: Color(0xFF8A6A6A), fontSize: 12.5)),
                ]),
              ),
              if (!narrow) ...[const SizedBox(width: 8), buttons],
              InkWell(
                onTap: () => PhoneEngine.I.dismissMissed(m),
                customBorder: const CircleBorder(),
                child: const Padding(padding: EdgeInsets.all(6), child: Icon(Icons.close_rounded, size: 18, color: Color(0xFF8A6A6A))),
              ),
            ]);
            if (!narrow) return info;
            return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              info,
              const SizedBox(height: 10),
              Align(alignment: AlignmentDirectional.centerEnd, child: buttons),
            ]);
          }),
        ),
      ),
    );
  }
}

class _FavCard extends StatelessWidget {
  final String name, status;
  final Color statusColor;
  final Color? dot;
  final bool pinned;
  final VoidCallback onTap, onLong;
  const _FavCard({required this.name, required this.status, required this.statusColor, required this.dot, required this.pinned, required this.onTap, required this.onLong});
  @override
  Widget build(BuildContext context) {
    final first = name.split(RegExp(r'\s+')).first;
    return Tooltip(
      message: '$name — دوس تتصل، ضغطة طويلة ${pinned ? 'تشيله من' : 'تثبّته في'} المفضلة',
      waitDuration: const Duration(milliseconds: 600),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: onTap,
          onLongPress: onLong,
          onSecondaryTap: onLong,
          child: Container(
            width: 104,
            decoration: BoxDecoration(borderRadius: BorderRadius.circular(16), border: Border.all(color: kLine), boxShadow: softShadow(.04)),
            padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 6),
            child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
              NameAvatar(name, size: 46, dot: dot),
              const SizedBox(height: 8),
              Text(first, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
              const SizedBox(height: 2),
              Text(status, style: TextStyle(color: statusColor, fontSize: 12, fontWeight: FontWeight.w600)),
              if (pinned) const Icon(Icons.push_pin, size: 11, color: kMuted),
            ]),
          ),
        ),
      ),
    );
  }
}

class _RecentCard extends StatelessWidget {
  final String name, sub, right;
  final Color rightColor;
  final String? time;
  final IconData? dirIcon;
  final Color? dirColor, dot;
  final VoidCallback onCall;
  final VoidCallback? onLong;
  const _RecentCard({required this.name, required this.sub, required this.right, required this.rightColor, required this.onCall, this.time, this.dirIcon, this.dirColor, this.dot, this.onLong});
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: () => Dial.set(sub.startsWith('تحويلة ') ? sub.substring(7) : sub),
          onLongPress: onLong,
          onSecondaryTap: onLong,
          child: Container(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
            decoration: BoxDecoration(borderRadius: BorderRadius.circular(16), border: Border.all(color: kLine), boxShadow: softShadow(.035)),
            child: Row(children: [
              NameAvatar(name, size: 46, dot: dot),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    if (dirIcon != null) ...[Icon(dirIcon, size: 16, color: dirColor), const SizedBox(width: 4)],
                    Expanded(child: Text(name, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15.5))),
                  ]),
                  const SizedBox(height: 2),
                  Text(sub, textDirection: sub.startsWith('تحويلة') ? null : TextDirection.ltr, style: const TextStyle(color: kMuted, fontSize: 13)),
                ]),
              ),
              Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                Text(time ?? right, style: TextStyle(color: time == null ? rightColor : kMuted, fontSize: 12.5, fontWeight: FontWeight.w600)),
                if (time != null) Text(right, style: TextStyle(color: rightColor, fontSize: 12, fontWeight: FontWeight.w700)),
                const SizedBox(height: 6),
                Material(
                  color: kGreen,
                  shape: const CircleBorder(),
                  child: InkWell(customBorder: const CircleBorder(), onTap: onCall, child: const SizedBox(width: 38, height: 38, child: Icon(Icons.call, color: Colors.white, size: 18))),
                ),
              ]),
            ]),
          ),
        ),
      ),
    );
  }
}

class _Key extends StatelessWidget {
  final String d, sub;
  final VoidCallback onTap;
  final VoidCallback? onLong;
  const _Key({required this.d, required this.sub, required this.onTap, this.onLong});
  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        onLongPress: onLong,
        child: Container(
          height: 64,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: kLine),
            boxShadow: [BoxShadow(color: const Color(0xFF0B2A1A).withValues(alpha: .05), blurRadius: 0, offset: const Offset(0, 2))],
          ),
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Text(d, style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w800, height: 1.1, color: kText)),
            if (sub.isNotEmpty) Text(sub, style: const TextStyle(fontSize: 10, letterSpacing: 2.5, color: kMuted, fontWeight: FontWeight.w600)),
          ]),
        ),
      ),
    );
  }
}

class _CallButton extends StatelessWidget {
  final double size;
  final VoidCallback onTap;
  const _CallButton({required this.size, required this.onTap});
  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: 'اتصال (Ctrl+Enter)',
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: kCallGradient,
          boxShadow: [BoxShadow(color: kCallA.withValues(alpha: .45), blurRadius: size * .35, offset: Offset(0, size * .08))],
        ),
        child: Material(
          type: MaterialType.transparency,
          shape: const CircleBorder(),
          child: InkWell(customBorder: const CircleBorder(), onTap: onTap, child: Icon(Icons.call, color: Colors.white, size: size * .42)),
        ),
      ),
    );
  }
}
