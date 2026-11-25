import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core.dart';
import '../phone.dart';
import 'theme.dart';

class DialPage extends StatefulWidget {
  const DialPage({super.key});
  static final ctrl = TextEditingController();
  static final focus = FocusNode();
  static void setNumber(String n) {
    ctrl.text = n;
    ctrl.selection = TextSelection.collapsed(offset: n.length);
  }

  @override
  State<DialPage> createState() => _DialPageState();
}

class _DialPageState extends State<DialPage> {
  final _c = DialPage.ctrl;
  String _name = '', _err = '';
  Timer? _t;
  bool get _mobile => Platform.isAndroid || Platform.isIOS;

  @override
  void initState() {
    super.initState();
    _c.addListener(_changed);
    if (!_mobile) WidgetsBinding.instance.addPostFrameCallback((_) => DialPage.focus.requestFocus());
  }

  @override
  void dispose() {
    _c.removeListener(_changed);
    _t?.cancel();
    super.dispose();
  }

  void _changed() {
    if (_err.isNotEmpty) setState(() => _err = '');
    _t?.cancel();
    final n = _c.text;
    if (n.replaceAll(RegExp(r'\D'), '').length < 9) {
      if (_name.isNotEmpty) setState(() => _name = '');
      return;
    }
    _t = Timer(const Duration(milliseconds: 450), () async {
      final a = PhoneEngine.I.account;
      if (a == null) return;
      final v = await Api.lookupName(a, n);
      if (mounted && _c.text == n) setState(() => _name = v);
    });
  }

  void _key(String d) {
    final t = _c.text, s = _c.selection;
    final a = s.isValid ? s.start : t.length, b = s.isValid ? s.end : t.length;
    _c.text = t.substring(0, a) + d + t.substring(b);
    _c.selection = TextSelection.collapsed(offset: a + d.length);
    HapticFeedback.lightImpact();
  }

  void _del({bool all = false}) {
    if (all) return _c.clear();
    final t = _c.text, s = _c.selection;
    var a = s.isValid ? s.start : t.length, b = s.isValid ? s.end : t.length;
    if (a == b) {
      if (a == 0) return;
      a -= 1;
    }
    _c.text = t.substring(0, a) + t.substring(b);
    _c.selection = TextSelection.collapsed(offset: a);
  }

  Future<void> _paste() async {
    final d = await Clipboard.getData(Clipboard.kTextPlain);
    final n = cleanPhone(d?.text ?? '');
    if (n.length < 3) {
      _toast('مفيش رقم في الحاجة اللي اتنسخت');
      return;
    }
    DialPage.setNumber(n);
  }

  Future<void> _call([String? n]) async {
    final num = (n ?? _c.text).trim();
    if (num.isEmpty) {
      // زرار الاتصال والخانة فاضية = آخر رقم
      if (PhoneEngine.I.lastNumber.isNotEmpty) DialPage.setNumber(PhoneEngine.I.lastNumber);
      return;
    }
    final err = await PhoneEngine.I.dial(num);
    if (err != null && mounted) setState(() => _err = err);
  }

  void _toast(String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m), duration: const Duration(seconds: 2)));

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return ListenableBuilder(
      listenable: PhoneEngine.I,
      builder: (context, _) {
        final e = PhoneEngine.I, o = e.lastOutcome;
        return LayoutBuilder(builder: (context, box) {
          final k = ((box.maxHeight - 250) / 6.0).clamp(40.0, 76.0);
          return Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: Column(children: [
                  const SizedBox(height: 8),
                  if (e.missed.isNotEmpty)
                    ActionChip(
                      avatar: const Icon(Icons.call_missed, color: kRed, size: 18),
                      label: Text('${e.missed.length} مكالمة فايتة — آخرها ${e.missed.last}'),
                      onPressed: () {
                        DialPage.setNumber(e.missed.last);
                        e.clearMissed();
                      },
                    ),
                  const Spacer(),
                  // ---------- خانة الرقم ----------
                  Shortcuts(
                    shortcuts: const {SingleActivator(LogicalKeyboardKey.enter): ActivateIntent(), SingleActivator(LogicalKeyboardKey.numpadEnter): ActivateIntent()},
                    child: Actions(
                      actions: {ActivateIntent: CallbackAction<ActivateIntent>(onInvoke: (_) => _call())},
                      child: TextField(
                        controller: _c,
                        focusNode: DialPage.focus,
                        textAlign: TextAlign.center,
                        textDirection: TextDirection.ltr,
                        keyboardType: _mobile ? TextInputType.none : TextInputType.phone,
                        showCursor: true,
                        inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9+*#]'))],
                        style: TextStyle(fontSize: k * .5, fontWeight: FontWeight.w300, letterSpacing: 1.5),
                        decoration: InputDecoration(
                          hintText: 'اكتب الرقم',
                          hintStyle: TextStyle(fontSize: 18, color: t.hintColor.withValues(alpha: .5), letterSpacing: 0),
                          filled: false,
                          border: UnderlineInputBorder(borderSide: BorderSide(color: t.dividerColor)),
                          enabledBorder: UnderlineInputBorder(borderSide: BorderSide(color: t.dividerColor.withValues(alpha: .4))),
                          focusedBorder: UnderlineInputBorder(borderSide: BorderSide(color: t.colorScheme.primary, width: 2)),
                          prefixIcon: IconButton(tooltip: 'لصق رقم', icon: const Icon(Icons.content_paste_rounded), onPressed: _paste),
                          suffixIcon: _c.text.isEmpty ? null : IconButton(tooltip: 'مسح', icon: const Icon(Icons.close_rounded), onPressed: () => _c.clear()),
                        ),
                      ),
                    ),
                  ),
                  SizedBox(
                    height: 26,
                    child: _name.isNotEmpty ? Center(child: Text('👤 $_name', style: TextStyle(color: t.colorScheme.primary, fontWeight: FontWeight.w600))) : null,
                  ),
                  if (_err.isNotEmpty) Text(_err, textAlign: TextAlign.center, style: const TextStyle(color: kRed)),
                  if (o != null && _err.isEmpty) _OutcomeCard(o: o, onRetry: () => _call(e.lastNumber)),
                  const SizedBox(height: 10),
                  // ---------- لوحة الأرقام ----------
                  Directionality(
                    textDirection: TextDirection.ltr,
                    child: Column(children: [
                      for (final row in const [
                        [('1', ''), ('2', 'ABC'), ('3', 'DEF')],
                        [('4', 'GHI'), ('5', 'JKL'), ('6', 'MNO')],
                        [('7', 'PQRS'), ('8', 'TUV'), ('9', 'WXYZ')],
                        [('*', ''), ('0', '+'), ('#', '')],
                      ])
                        Padding(
                          padding: EdgeInsets.symmetric(vertical: k * .1),
                          child: Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
                            for (final kk in row) _Key(size: k, d: kk.$1, sub: kk.$2, onTap: () => _key(kk.$1), onLong: kk.$1 == '0' ? () => _key('+') : null),
                          ]),
                        ),
                      SizedBox(height: k * .2),
                      Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
                        SizedBox(width: k),
                        SizedBox(
                          width: k * 1.1,
                          height: k * 1.1,
                          child: FilledButton(
                            style: FilledButton.styleFrom(backgroundColor: kGreen, shape: const CircleBorder(), padding: EdgeInsets.zero),
                            onPressed: () => _call(),
                            child: Icon(Icons.call, size: k * .45, color: Colors.white),
                          ),
                        ),
                        SizedBox(
                          width: k,
                          height: k,
                          child: GestureDetector(
                            onLongPress: () => _del(all: true),
                            child: IconButton(tooltip: 'مسح (ضغطة طويلة = مسح الكل)', onPressed: _del, icon: Icon(Icons.backspace_outlined, size: k * .36)),
                          ),
                        ),
                      ]),
                    ]),
                  ),
                  const Spacer(),
                ]),
              ),
            ),
          );
        });
      },
    );
  }
}

class _Key extends StatelessWidget {
  final double size;
  final String d, sub;
  final VoidCallback onTap;
  final VoidCallback? onLong;
  const _Key({required this.size, required this.d, required this.sub, required this.onTap, this.onLong});

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    return SizedBox(
      width: size,
      height: size,
      child: Material(
        color: dark ? const Color(0x14FFFFFF) : const Color(0xFFEFF3F6),
        shape: const CircleBorder(),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: onTap,
          onLongPress: onLong,
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Text(d, style: TextStyle(fontSize: size * .38, height: 1.1)),
            if (sub.isNotEmpty) Text(sub, style: TextStyle(fontSize: size * .13, letterSpacing: 1.5, color: Theme.of(context).hintColor)),
          ]),
        ),
      ),
    );
  }
}

class _OutcomeCard extends StatelessWidget {
  final Outcome o;
  final VoidCallback onRetry;
  const _OutcomeCard({required this.o, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    final c = switch (o.key) { 'busy' || 'reject' => kRed, 'noans' || 'net' => kAmber, _ => Colors.blueGrey };
    return Container(
      margin: const EdgeInsets.only(top: 6),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      decoration: BoxDecoration(color: c.withValues(alpha: .12), border: Border.all(color: c.withValues(alpha: .45)), borderRadius: BorderRadius.circular(16)),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Text('${o.icon} ${o.title}', style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
        if (o.detail.isNotEmpty) Text(o.detail, style: TextStyle(fontSize: 12.5, color: Theme.of(context).hintColor)),
        if (o.retry) ...[
          const SizedBox(height: 6),
          FilledButton.tonalIcon(onPressed: onRetry, icon: const Icon(Icons.replay, size: 18), label: const Text('اتصل تاني'), style: FilledButton.styleFrom(minimumSize: const Size(0, 36))),
        ],
      ]),
    );
  }
}
