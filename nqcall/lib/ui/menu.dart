// القائمة المنسدلة (الهامبرجر) — بدل الشريط السفلي القديم
import 'package:flutter/material.dart';

import '../native.dart';
import '../win.dart';
import 'dashboard_page.dart';
import 'pages.dart';
import 'theme.dart';

class NQMenuButton extends StatelessWidget {
  const NQMenuButton({super.key});

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: 'القائمة',
      child: Material(
        color: Colors.white.withValues(alpha: .12),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12), side: const BorderSide(color: Colors.white24)),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () => _open(context),
          child: const SizedBox(width: 44, height: 40, child: Icon(Icons.menu_rounded, color: Colors.white)),
        ),
      ),
    );
  }

  Future<void> _open(BuildContext context) async {
    final box = context.findRenderObject() as RenderBox;
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
    final pos = box.localToGlobal(Offset(0, box.size.height + 6), ancestor: overlay);
    final v = await showMenu<String>(
      context: context,
      color: Colors.white,
      elevation: 10,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      constraints: const BoxConstraints(minWidth: 300, maxWidth: 340),
      position: RelativeRect.fromLTRB(pos.dx, pos.dy, overlay.size.width - pos.dx - box.size.width, 0),
      items: [
        _item('dash', Icons.space_dashboard_outlined, 'لوحة التحكم', const Color(0xFFE7F6EE), kGreen, trailing: 'المكالمات\nوالتقارير'),
        _item('settings', Icons.settings_outlined, 'إعدادات', const Color(0xFFEEF1F0), kText),
        _item('files', Icons.folder_outlined, 'الملفات', const Color(0xFFFFF4E0), const Color(0xFFD9900F)),
        _item('wa', Icons.chat_bubble_outline_rounded, 'واتساب', const Color(0xFFFFEAF3), const Color(0xFFDB2777)),
        _item('perf', Icons.bar_chart_rounded, 'إنجازي', const Color(0xFFEEF1F0), kText),
        _item('target', Icons.track_changes_rounded, 'التارجت', const Color(0xFFFFE8E8), kRed),
        _item('history', Icons.history_rounded, 'السجل', const Color(0xFFEEF1F0), kText),
        const PopupMenuDivider(),
        _item('audio', Icons.headset_mic_outlined, 'إعدادات الصوت', const Color(0xFFE8F0FE), kBlue, trailing: 'سماعة\nومايك'),
      ],
    );
    if (v == null || !context.mounted) return;
    switch (v) {
      case 'dash':
        openDashboard(context);
      case 'settings':
        openSettings(context);
      case 'files':
        openWeb(context, 'files', 'الملفات');
      case 'wa':
        openWeb(context, 'wa', 'واتساب');
      case 'perf':
        openPerf(context);
      case 'target':
        openGoals(context);
      case 'history':
        openHistory(context);
      case 'audio':
        openAudio(context);
      case 'keys':
        showShortcuts(context);
      case 'compact':
        Win.toggleCompact(true);
    }
  }

  PopupMenuItem<String> _item(String v, IconData ic, String t, Color bg, Color fg, {String? trailing}) => PopupMenuItem<String>(
        value: v,
        height: 56,
        child: Row(children: [
          Container(width: 38, height: 38, decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(11)), child: Icon(ic, color: fg, size: 21)),
          const SizedBox(width: 14),
          Expanded(child: Text(t, style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w700, color: kText))),
          if (trailing != null)
            trailing.contains('Ctrl')
                ? Kbd(trailing)
                : Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    decoration: BoxDecoration(color: const Color(0xFFF1F4F3), borderRadius: BorderRadius.circular(8), border: Border.all(color: kLine)),
                    child: Text(trailing, textAlign: TextAlign.center, style: const TextStyle(fontSize: 11.5, color: kMuted, height: 1.3)),
                  ),
        ]),
      );
}
