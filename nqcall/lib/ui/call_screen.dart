import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core.dart';
import '../phone.dart';
import '../win.dart';
import 'pages.dart';
import 'theme.dart';

/// شاشة المكالمة (واردة / بيتصل / شغالة) — فيها بطاقة العميل قبل الرد
class CallScreen extends StatefulWidget {
  final ActiveCall call;
  const CallScreen({super.key, required this.call});
  @override
  State<CallScreen> createState() => _CallScreenState();
}

class _CallScreenState extends State<CallScreen> {
  bool _pad = false;
  final _f = FocusNode();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _f.requestFocus());
  }

  @override
  void dispose() {
    _f.dispose();
    super.dispose();
  }

  // كيبورد الكمبيوتر: Enter يرد، Esc يقفل، أرقام = DTMF
  KeyEventResult _onKey(FocusNode n, KeyEvent ev) {
    if (ev is! KeyDownEvent) return KeyEventResult.ignored;
    final e = PhoneEngine.I, c = widget.call, k = ev.logicalKey;
    if ((k == LogicalKeyboardKey.enter || k == LogicalKeyboardKey.numpadEnter) && c.phase == CallPhase.incoming) {
      e.answer();
      return KeyEventResult.handled;
    }
    if (k == LogicalKeyboardKey.escape) {
      e.hangup();
      return KeyEventResult.handled;
    }
    final ch = ev.character;
    if (ch != null && RegExp(r'^[0-9*#]$').hasMatch(ch) && c.phase == CallPhase.active) {
      e.sendDtmf(ch);
      return KeyEventResult.handled;
    }
    return KeyEventResult.ignored;
  }

  @override
  Widget build(BuildContext context) {
    final e = PhoneEngine.I, c = widget.call;
    final mobile = Platform.isAndroid || Platform.isIOS;
    final status = switch (c.phase) {
      CallPhase.incoming => 'مكالمة واردة…',
      CallPhase.dialing => 'بيتصل…',
      CallPhase.ringing => 'بيرن…',
      CallPhase.active => c.held ? 'انتظار ⏸  ${fmtDur(c.seconds)}' : fmtDur(c.seconds),
      CallPhase.ended => 'انتهت',
    };
    final title = c.name.isNotEmpty ? c.name : (c.number == '*43' ? 'مكالمة اختبار الصوت' : c.number);
    final ctx = c.context;
    final note = c.info['note'] is Map ? '${(c.info['note'] as Map)['text'] ?? (c.info['note'] as Map)['summary'] ?? ''}' : '';
    final crm = c.info['crm'] is Map ? '${(c.info['crm'] as Map)['name'] ?? ''}' : '';
    return Focus(
      focusNode: _f,
      onKeyEvent: _onKey,
      child: Material(
        child: Container(
          decoration: const BoxDecoration(
            gradient: LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [kHeaderTop, kHeaderBottom, Color(0xFF022A14)]),
          ),
          child: SafeArea(
            child: DefaultTextStyle(
              style: const TextStyle(color: Colors.white),
              child: Column(children: [
                const TitleBar(),
                Expanded(
                  child: Center(
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 460),
                      child: Column(children: [
                        const SizedBox(height: 24),
                        Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                          _QualityChip(e: e, active: c.phase == CallPhase.active),
                        ]),
                        const SizedBox(height: 18),
                        Container(
                          padding: const EdgeInsets.all(4),
                          decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: Colors.white24, width: 2)),
                          child: NameAvatar(c.name.isNotEmpty ? c.name : '#', size: 92),
                        ),
                        const SizedBox(height: 16),
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 20),
                          child: Text(title, textAlign: TextAlign.center, style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
                        ),
                        if (c.name.isNotEmpty) Text(c.number, textDirection: TextDirection.ltr, style: const TextStyle(fontSize: 16, color: Colors.white70)),
                        const SizedBox(height: 8),
                        Text(status, style: TextStyle(fontSize: 18, color: c.phase == CallPhase.active ? const Color(0xFFB7F5CF) : Colors.white70, fontWeight: FontWeight.w600)),
                        if (c.dtmf.isNotEmpty) Text(c.dtmf, textDirection: TextDirection.ltr, style: const TextStyle(fontSize: 20, letterSpacing: 3, color: Colors.white54)),
                        // بطاقة العميل (قبل الرد ووقت المكالمة)
                        if (ctx.isNotEmpty || note.isNotEmpty || (crm.isNotEmpty && crm != c.name))
                          Container(
                            margin: const EdgeInsets.fromLTRB(24, 16, 24, 0),
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(color: Colors.white.withValues(alpha: .08), borderRadius: BorderRadius.circular(14), border: Border.all(color: Colors.white12)),
                            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                              if (crm.isNotEmpty && crm != c.name) Text('🏢 $crm', style: const TextStyle(fontWeight: FontWeight.w700)),
                              if (ctx.isNotEmpty) Text(ctx, style: const TextStyle(color: Colors.white70, fontSize: 13)),
                              if (note.isNotEmpty) Text('📝 $note', maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(color: Colors.white70, fontSize: 13)),
                            ]),
                          ),
                        const Spacer(),
                        if (c.phase == CallPhase.incoming)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 44),
                            child: Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
                              _Round(color: kRed, icon: Icons.call_end, label: 'رفض (Esc)', onTap: e.hangup),
                              _Round(gradient: kCallGradient, icon: Icons.call, label: 'رد (Enter)', onTap: e.answer, big: true),
                            ]),
                          )
                        else ...[
                          if (_pad && c.phase == CallPhase.active) _DtmfPad(onKey: e.sendDtmf),
                          Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 12),
                            child: Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
                              _Toggle(icon: c.muted ? Icons.mic_off_rounded : Icons.mic_none_rounded, label: c.muted ? 'المايك مقفول' : 'كتم', on: c.muted, danger: true, onTap: e.toggleMute),
                              _Toggle(icon: Icons.dialpad_rounded, label: 'أرقام', on: _pad, onTap: () => setState(() => _pad = !_pad)),
                              if (mobile)
                                _Toggle(icon: Icons.volume_up_rounded, label: 'سبيكر', on: c.speaker, onTap: e.toggleSpeaker)
                              else
                                _Toggle(icon: Icons.speaker_group_outlined, label: 'السماعة', on: false, onTap: () => pickOutputDuringCall(context)),
                              _Toggle(icon: Icons.pause_rounded, label: 'انتظار', on: c.held, onTap: e.toggleHold),
                            ]),
                          ),
                          const SizedBox(height: 26),
                          Padding(padding: const EdgeInsets.only(bottom: 36), child: _Round(color: kRed, icon: Icons.call_end, label: 'إنهاء (Esc)', onTap: e.hangup, big: true)),
                        ],
                      ]),
                    ),
                  ),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

class _QualityChip extends StatelessWidget {
  final PhoneEngine e;
  final bool active;
  const _QualityChip({required this.e, required this.active});
  @override
  Widget build(BuildContext context) {
    final (txt, col) = switch (e.quality) {
      Quality.excellent => ('صوت ممتاز', const Color(0xFF4ADE80)),
      Quality.good => ('صوت كويس', const Color(0xFF4ADE80)),
      Quality.weak => ('الشبكة ضعيفة — الصوت ممكن يقطّع', kAmber),
      Quality.bad => ('الشبكة سيئة جدًا', const Color(0xFFFF8A8A)),
      Quality.unknown => ('', Colors.white54),
    };
    if (!active || txt.isEmpty) return const SizedBox(height: 26);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
      decoration: BoxDecoration(color: Colors.white.withValues(alpha: .08), borderRadius: BorderRadius.circular(99)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(Icons.network_check_rounded, size: 15, color: col),
        const SizedBox(width: 6),
        Text(txt, style: TextStyle(color: col, fontSize: 12.5, fontWeight: FontWeight.w600)),
      ]),
    );
  }
}

class _Round extends StatelessWidget {
  final Color? color;
  final Gradient? gradient;
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool big;
  const _Round({this.color, this.gradient, required this.icon, required this.label, required this.onTap, this.big = false});
  @override
  Widget build(BuildContext context) {
    final s = big ? 76.0 : 66.0;
    final glow = (gradient != null ? kCallA : (color ?? kRed)).withValues(alpha: .4);
    return Column(mainAxisSize: MainAxisSize.min, children: [
      Container(
        width: s,
        height: s,
        decoration: BoxDecoration(shape: BoxShape.circle, color: color, gradient: gradient, boxShadow: [BoxShadow(color: glow, blurRadius: 24, offset: const Offset(0, 6))]),
        child: Material(
          type: MaterialType.transparency,
          child: InkWell(customBorder: const CircleBorder(), onTap: onTap, child: Icon(icon, color: Colors.white, size: s * .42)),
        ),
      ),
      const SizedBox(height: 8),
      Text(label, style: const TextStyle(color: Colors.white70)),
    ]);
  }
}

class _Toggle extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool on, danger;
  final VoidCallback onTap;
  const _Toggle({required this.icon, required this.label, required this.on, required this.onTap, this.danger = false});
  @override
  Widget build(BuildContext context) {
    final bg = on ? (danger ? kRed : Colors.white) : Colors.white.withValues(alpha: .1);
    final fg = on ? (danger ? Colors.white : kHeaderBottom) : Colors.white;
    return Column(mainAxisSize: MainAxisSize.min, children: [
      Material(
        color: bg,
        shape: CircleBorder(side: BorderSide(color: on && danger ? Colors.white : Colors.white12, width: on && danger ? 2 : 1)),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: onTap,
          child: SizedBox(
            width: 62,
            height: 62,
            child: Stack(alignment: Alignment.center, children: [
              Icon(icon, color: fg, size: 27),
              // علامة واضحة إن المايك مقفول
              if (on && danger) Transform.rotate(angle: -.8, child: Container(width: 34, height: 3, color: Colors.white)),
            ]),
          ),
        ),
      ),
      const SizedBox(height: 6),
      Text(label, style: TextStyle(color: on && danger ? const Color(0xFFFCA5A5) : Colors.white70, fontSize: 12.5, fontWeight: on ? FontWeight.w700 : FontWeight.w400)),
    ]);
  }
}

class _DtmfPad extends StatelessWidget {
  final void Function(String) onKey;
  const _DtmfPad({required this.onKey});
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Directionality(
        textDirection: TextDirection.ltr,
        child: Wrap(
          alignment: WrapAlignment.center,
          spacing: 14,
          runSpacing: 10,
          children: [
            for (final d in const ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#'])
              SizedBox(
                width: 86,
                child: Material(
                  color: Colors.white.withValues(alpha: .09),
                  borderRadius: BorderRadius.circular(14),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(14),
                    onTap: () {
                      HapticFeedback.lightImpact();
                      onKey(d);
                    },
                    child: Padding(padding: const EdgeInsets.symmetric(vertical: 10), child: Center(child: Text(d, style: const TextStyle(fontSize: 24, color: Colors.white, fontWeight: FontWeight.w700)))),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
