import 'package:flutter/material.dart';

// الهوية الخضراء (من مستند التسليم)
const kGreen = Color(0xFF0AA14F); // الأساسي: أزرار، تبويب نشط، حالات
const kHeaderTop = Color(0xFF0C8A44);
const kHeaderBottom = Color(0xFF044A23);
const kCallA = Color(0xFF16C25F);
const kCallB = Color(0xFF07803C);
const kBg = Color(0xFFF7F9FA);
const kText = Color(0xFF1C2B36);
const kMuted = Color(0xFF82918A);
const kRed = Color(0xFFDC2626);
const kRedBg = Color(0xFFFFF5F5);
const kAmber = Color(0xFFF59E0B);
const kLine = Color(0xFFE6ECEA);
const kBlue = Color(0xFF2563EB);

// أسامي قديمة لسه مستخدمة في بعض الشاشات
const kBrand = kGreen;
const kBrand2 = kHeaderTop;

const kHeaderGradient = LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [kHeaderTop, kHeaderBottom]);
const kCallGradient = LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [kCallA, kCallB]);

List<BoxShadow> softShadow([double a = .06]) => [
      BoxShadow(color: const Color(0xFF0B2A1A).withValues(alpha: a), blurRadius: 18, offset: const Offset(0, 6)),
      BoxShadow(color: const Color(0xFF0B2A1A).withValues(alpha: a / 2), blurRadius: 3, offset: const Offset(0, 1)),
    ];

ThemeData buildTheme(Brightness b) {
  final scheme = ColorScheme.fromSeed(seedColor: kGreen, brightness: Brightness.light).copyWith(
    primary: kGreen,
    onPrimary: Colors.white,
    surface: kBg,
    onSurface: kText,
    error: kRed,
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    brightness: Brightness.light,
    fontFamily: 'Segoe UI',
    fontFamilyFallback: const ['Noto Sans Arabic', 'Tahoma', 'Arial'],
    scaffoldBackgroundColor: kBg,
    hintColor: kMuted,
    dividerColor: kLine,
    textTheme: Typography.blackMountainView.apply(bodyColor: kText, displayColor: kText),
    cardTheme: CardThemeData(
      elevation: 0,
      color: Colors.white,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: const BorderSide(color: kLine)),
      margin: EdgeInsets.zero,
    ),
    appBarTheme: const AppBarTheme(backgroundColor: kHeaderTop, foregroundColor: Colors.white, elevation: 0, centerTitle: false),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: Colors.white,
      hintStyle: const TextStyle(color: kMuted),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: kLine)),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: kLine)),
      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: kGreen, width: 1.6)),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: kGreen,
        foregroundColor: Colors.white,
        minimumSize: const Size(0, 46),
        textStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: kText,
        side: const BorderSide(color: kLine),
        minimumSize: const Size(0, 42),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
    ),
    snackBarTheme: SnackBarThemeData(behavior: SnackBarBehavior.floating, backgroundColor: kText, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12))),
  );
}

/// أفاتار بحرف الاسم ولون ثابت لكل اسم
class NameAvatar extends StatelessWidget {
  final String name;
  final double size;
  final Color? dot;
  const NameAvatar(this.name, {super.key, this.size = 44, this.dot});

  static const _pal = [
    (Color(0xFFE7F6EE), Color(0xFF0A8F47)),
    (Color(0xFFE8F0FE), Color(0xFF2563EB)),
    (Color(0xFFFFF1E6), Color(0xFFD9720F)),
    (Color(0xFFF3E8FF), Color(0xFF9333EA)),
    (Color(0xFFFFE8EE), Color(0xFFDB2777)),
    (Color(0xFFE6F7F7), Color(0xFF0E8C8C)),
  ];

  @override
  Widget build(BuildContext context) {
    final n = name.trim();
    final ch = n.isEmpty ? '#' : (RegExp(r'^[\d+]').hasMatch(n) ? '#' : n.characters.first);
    final p = _pal[n.codeUnits.fold<int>(0, (a, b) => a + b) % _pal.length];
    return SizedBox(
      width: size,
      height: size,
      child: Stack(clipBehavior: Clip.none, children: [
        Container(
          width: size,
          height: size,
          decoration: BoxDecoration(color: p.$1, shape: BoxShape.circle),
          child: Center(child: Text(ch, style: TextStyle(color: p.$2, fontWeight: FontWeight.w800, fontSize: size * .42))),
        ),
        if (dot != null)
          Positioned(
            top: 0,
            left: 0,
            child: Container(
              width: size * .28,
              height: size * .28,
              decoration: BoxDecoration(color: dot, shape: BoxShape.circle, border: Border.all(color: Colors.white, width: 2)),
            ),
          ),
      ]),
    );
  }
}

/// زرار كيبورد صغير (Ctrl+Enter)
class Kbd extends StatelessWidget {
  final String text;
  const Kbd(this.text, {super.key});
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
        decoration: BoxDecoration(color: const Color(0xFFF1F4F3), borderRadius: BorderRadius.circular(6), border: Border.all(color: kLine)),
        child: Text(text, textDirection: TextDirection.ltr, style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: kText)),
      );
}
