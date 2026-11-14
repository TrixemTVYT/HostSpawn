// The HostSpawn look for Setup: the same colours as the app (frontend/app/lib/theme.dart, Aurora theme).
import 'package:flutter/material.dart';

class L {
  static const bg = Color(0xFF07090D), bg2 = Color(0xFF0B0F16), panel = Color(0xC711161F);
  static const line = Color(0x3894A3B8);
  static const text = Color(0xFFE6EDF6), muted = Color(0xFF8B97A8), dim = Color(0xFF5D6878);
  static const accent = Color(0xFF3EE6C4), accent2 = Color(0xFF7C5CFF);
  static const ok = Color(0xFF3DDC97), warn = Color(0xFFF5B74F), bad = Color(0xFFFF5C7A);
}

ThemeData look() {
  final base = ThemeData.dark(useMaterial3: true);
  return base.copyWith(
    scaffoldBackgroundColor: L.bg,
    colorScheme: const ColorScheme.dark(primary: L.accent, secondary: L.accent2, surface: L.bg2, error: L.bad),
    textTheme: base.textTheme.apply(bodyColor: L.text, displayColor: L.text),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: L.panel,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: L.line),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: L.line),
      ),
      labelStyle: const TextStyle(color: L.muted),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: L.accent,
        foregroundColor: L.bg,
        padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 16),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    ),
  );
}
