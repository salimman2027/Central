import 'package:flutter/material.dart';

const kBrand = Color(0xFF0AA58C);
const kBrand2 = Color(0xFF2B7DE0);
const kGreen = Color(0xFF16A34A);
const kRed = Color(0xFFDC2626);
const kAmber = Color(0xFFF59E0B);

ThemeData buildTheme(Brightness b) {
  final dark = b == Brightness.dark;
  final scheme = ColorScheme.fromSeed(seedColor: kBrand, brightness: b).copyWith(
    primary: dark ? const Color(0xFF19E6C1) : kBrand,
    surface: dark ? const Color(0xFF0B1220) : const Color(0xFFF4F7FA),
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    brightness: b,
    fontFamily: 'Segoe UI',
    scaffoldBackgroundColor: scheme.surface,
    cardTheme: CardThemeData(
      elevation: 0,
      color: dark ? const Color(0xFF131C2E) : Colors.white,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: BorderSide(color: dark ? const Color(0x22FFFFFF) : const Color(0xFFE3E8EE))),
      margin: EdgeInsets.zero,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: dark ? const Color(0xFF131C2E) : Colors.white,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: dark ? const Color(0x22FFFFFF) : const Color(0xFFE3E8EE))),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: dark ? const Color(0x22FFFFFF) : const Color(0xFFE3E8EE))),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(minimumSize: const Size(0, 50), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14))),
    ),
  );
}
