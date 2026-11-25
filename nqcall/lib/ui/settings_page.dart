import 'dart:io';

import 'package:flutter/material.dart';

import '../core.dart';
import '../main.dart';
import '../native.dart';
import '../phone.dart';
import '../ringer.dart';
import 'theme.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});
  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final r = Ringer.I;

  Widget _section(String title, List<Widget> children) => Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
              const SizedBox(height: 10),
              ...children,
            ]),
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final a = PhoneEngine.I.account;
    final theme = Prefs.str('theme', 'system');
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 640),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              _section('🎵 نغمة الرنين', [
                Wrap(spacing: 8, runSpacing: 8, children: [
                  for (final tone in Ringer.tones)
                    ChoiceChip(
                      label: Text(tone.name),
                      selected: r.current == tone.id,
                      onSelected: (_) async {
                        await r.select(tone.id);
                        r.preview(tone.id);
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
                  const SizedBox(width: 12),
                  const Icon(Icons.volume_down, size: 20),
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
                  const Icon(Icons.volume_up, size: 20),
                ]),
              ]),
              _section('🎨 الشكل', [
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(value: 'light', label: Text('☀️ فاتح')),
                    ButtonSegment(value: 'dark', label: Text('🌙 غامق')),
                    ButtonSegment(value: 'system', label: Text('🌓 زي الجهاز')),
                  ],
                  selected: {theme},
                  onSelectionChanged: (s) async {
                    await NQApp.setTheme(s.first);
                    setState(() {});
                  },
                ),
              ]),
              if (Platform.isAndroid)
                _section('🔋 عشان المكالمات توصلك والتطبيق في الخلفية', [
                  Text('اسمح للتطبيق يشتغل في الخلفية من غير قيود بطارية (مهم جدًا في شاومي وسامسونج).', style: TextStyle(color: t.hintColor)),
                  const SizedBox(height: 8),
                  FilledButton.tonalIcon(onPressed: Native.ignoreBatteryOptimizations, icon: const Icon(Icons.battery_charging_full), label: const Text('شيل قيود البطارية')),
                ]),
              _section('👤 الحساب', [
                if (a != null) ...[
                  Text(a.name.isNotEmpty ? a.name : a.username, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                  Text('تحويلة ${a.ext} · ${Prefs.host}', style: TextStyle(color: t.hintColor)),
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
                    if (ok == true) PhoneEngine.I.signOut();
                  },
                  icon: const Icon(Icons.logout),
                  label: const Text('تسجيل خروج'),
                ),
              ]),
              Center(child: Text('NQ Call $kAppVersion · ${Platform.operatingSystem}', style: TextStyle(color: t.hintColor, fontSize: 12))),
            ]),
          ),
        ),
      ],
    );
  }
}
