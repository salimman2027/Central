import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core.dart';
import '../phone.dart';
import 'theme.dart';

/// شاشة المكالمة (واردة / بيتصل / شغالة)
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
      CallPhase.active => c.held ? 'انتظار ⏸ ${fmtDur(c.seconds)}' : fmtDur(c.seconds),
      CallPhase.ended => 'انتهت',
    };
    final title = c.name.isNotEmpty ? c.name : c.number;
    return Focus(
      focusNode: _f,
      onKeyEvent: _onKey,
      child: Material(
        child: Container(
          decoration: const BoxDecoration(
            gradient: LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [Color(0xFF0B1A2E), Color(0xFF06101D)]),
          ),
          child: SafeArea(
            child: DefaultTextStyle(
              style: const TextStyle(color: Colors.white),
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 440),
                  child: Column(children: [
                    const SizedBox(height: 40),
                    Container(
                      width: 96,
                      height: 96,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        gradient: const LinearGradient(colors: [kBrand, kBrand2]),
                        boxShadow: [BoxShadow(color: kBrand.withValues(alpha: .4), blurRadius: 30)],
                      ),
                      child: Center(
                        child: Text(c.name.isNotEmpty ? c.name.characters.first : '#', style: const TextStyle(fontSize: 40, color: Colors.white, fontWeight: FontWeight.w700)),
                      ),
                    ),
                    const SizedBox(height: 18),
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 20),
                      child: Text(title, textAlign: TextAlign.center, style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w700)),
                    ),
                    if (c.name.isNotEmpty) Text(c.number, textDirection: TextDirection.ltr, style: const TextStyle(fontSize: 16, color: Colors.white70)),
                    const SizedBox(height: 8),
                    Text(status, style: TextStyle(fontSize: 18, color: c.phase == CallPhase.active ? const Color(0xFF19E6C1) : Colors.white70)),
                    if (c.dtmf.isNotEmpty) Text(c.dtmf, textDirection: TextDirection.ltr, style: const TextStyle(fontSize: 20, letterSpacing: 3, color: Colors.white54)),
                    const Spacer(),
                    if (c.phase == CallPhase.incoming)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 48),
                        child: Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
                          _Round(color: kRed, icon: Icons.call_end, label: 'رفض', onTap: e.hangup),
                          _Round(color: kGreen, icon: Icons.call, label: 'رد', onTap: e.answer, big: true),
                        ]),
                      )
                    else ...[
                      if (_pad && c.phase == CallPhase.active) _DtmfPad(onKey: e.sendDtmf),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        child: Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
                          _Toggle(icon: c.muted ? Icons.mic_off : Icons.mic_none, label: c.muted ? 'المايك مقفول' : 'كتم', on: c.muted, danger: true, onTap: e.toggleMute),
                          _Toggle(icon: Icons.dialpad, label: 'أرقام', on: _pad, onTap: () => setState(() => _pad = !_pad)),
                          if (mobile) _Toggle(icon: Icons.volume_up, label: 'سبيكر', on: c.speaker, onTap: e.toggleSpeaker),
                          _Toggle(icon: Icons.pause, label: 'انتظار', on: c.held, onTap: e.toggleHold),
                        ]),
                      ),
                      const SizedBox(height: 28),
                      Padding(padding: const EdgeInsets.only(bottom: 40), child: _Round(color: kRed, icon: Icons.call_end, label: 'إنهاء', onTap: e.hangup, big: true)),
                    ],
                  ]),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Round extends StatelessWidget {
  final Color color;
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool big;
  const _Round({required this.color, required this.icon, required this.label, required this.onTap, this.big = false});
  @override
  Widget build(BuildContext context) {
    final s = big ? 76.0 : 66.0;
    return Column(mainAxisSize: MainAxisSize.min, children: [
      SizedBox(
        width: s,
        height: s,
        child: FilledButton(
          style: FilledButton.styleFrom(backgroundColor: color, shape: const CircleBorder(), padding: EdgeInsets.zero),
          onPressed: onTap,
          child: Icon(icon, color: Colors.white, size: s * .42),
        ),
      ),
      const SizedBox(height: 6),
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
    final fg = on ? (danger ? Colors.white : const Color(0xFF0B1A2E)) : Colors.white;
    return Column(mainAxisSize: MainAxisSize.min, children: [
      Material(
        color: bg,
        shape: const CircleBorder(),
        child: InkWell(customBorder: const CircleBorder(), onTap: onTap, child: SizedBox(width: 62, height: 62, child: Icon(icon, color: fg, size: 28))),
      ),
      const SizedBox(height: 6),
      Text(label, style: TextStyle(color: on && danger ? const Color(0xFFFCA5A5) : Colors.white70, fontSize: 12.5)),
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
          spacing: 18,
          runSpacing: 10,
          children: [
            for (final d in const ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#'])
              SizedBox(
                width: 80,
                child: Material(
                  color: Colors.white.withValues(alpha: .08),
                  borderRadius: BorderRadius.circular(14),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(14),
                    onTap: () {
                      HapticFeedback.lightImpact();
                      onKey(d);
                    },
                    child: Padding(padding: const EdgeInsets.symmetric(vertical: 10), child: Center(child: Text(d, style: const TextStyle(fontSize: 24, color: Colors.white)))),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
