// HostSpawn look: the same colours as the built-in page (frontend/web/index.html), dark and sleek.
import 'package:flutter/material.dart';

/// A colour theme: its two accent colours. Everything else (background, text) stays the same.
class AppTheme {
  const AppTheme(this.id, this.name, this.accent, this.accent2);
  final String id, name;
  final Color accent, accent2;
}

const themes = [
  AppTheme('aurora', 'Aurora', Color(0xFF3EE6C4), Color(0xFF7C5CFF)),
  AppTheme('ember', 'Ember', Color(0xFFFF8A4C), Color(0xFFFF4D8D)),
  AppTheme('ocean', 'Ocean', Color(0xFF4CC9F0), Color(0xFF4361EE)),
  AppTheme('forest', 'Forest', Color(0xFF5EE07A), Color(0xFF1FB5A8)),
  AppTheme('royal', 'Royal', Color(0xFFFFC94A), Color(0xFF9D4EDD)),
];

/// The chosen theme. Changing it rebuilds the app (HostSpawnApp listens); the choice is saved per device.
final themeNotifier = ValueNotifier<AppTheme>(themes.first);

AppTheme themeById(String? id) => themes.firstWhere((x) => x.id == id, orElse: () => themes.first);

class C {
  static const bg = Color(0xFF07090D), bg2 = Color(0xFF0B0F16);
  static const panel = Color(0xC711161F), panel2 = Color(0xB31A212D);
  static const line = Color(0x1F94A3B8), line2 = Color(0x3894A3B8);
  static const text = Color(0xFFE6EDF6), muted = Color(0xFF8B97A8), dim = Color(0xFF5D6878);
  // the two theme colours follow the chosen theme (see themes below)
  static Color get accent => themeNotifier.value.accent;
  static Color get accent2 => themeNotifier.value.accent2;
  static const ok = Color(0xFF3DDC97), warn = Color(0xFFF5B74F), bad = Color(0xFFFF5C7A), info = Color(0xFF5CB8FF);
}

ThemeData hostspawnTheme() {
  final base = ThemeData.dark(useMaterial3: true);
  return base.copyWith(
    scaffoldBackgroundColor: C.bg,
    colorScheme: ColorScheme.dark(primary: C.accent, secondary: C.accent2, surface: C.bg2, error: C.bad),
    textTheme: base.textTheme.apply(bodyColor: C.text, displayColor: C.text),
    cardTheme: CardThemeData(
      color: C.panel,
      elevation: 0,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: const BorderSide(color: C.line2)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: C.panel2,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: C.line2)),
      enabledBorder:
          OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: C.line2)),
      focusedBorder:
          OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide(color: C.accent)),
      labelStyle: const TextStyle(color: C.muted),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: C.accent,
        foregroundColor: C.bg,
        padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 16),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    ),
    dividerColor: C.line,
    // notifications at the bottom: light text on a solid dark card (the default text was dark on dark)
    snackBarTheme: SnackBarThemeData(
      backgroundColor: const Color(0xFF1C2433),
      contentTextStyle: const TextStyle(color: Colors.white, fontSize: 14.5, height: 1.35),
      actionTextColor: C.accent,
      behavior: SnackBarBehavior.floating,
      elevation: 8,
      shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12), side: const BorderSide(color: Color(0x3894A3B8))),
    ),
    appBarTheme: const AppBarTheme(surfaceTintColor: Colors.transparent, elevation: 0, scrolledUnderElevation: 0),
    tabBarTheme: TabBarThemeData(labelColor: C.accent, unselectedLabelColor: C.muted, dividerColor: C.line),
    floatingActionButtonTheme: FloatingActionButtonThemeData(
        elevation: 6, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14))),
  );
}

Color stateColor(String? state) => switch (state) {
      'running' => C.ok,
      'starting' || 'updating' || 'stopping' => C.warn,
      'crashed' || 'down' || 'failed' => C.bad,
      _ => C.dim,
    };

/// The top of a tool card (Server Doctor, Spyglass, the Tools tab): an icon in a tinted square, a title, a one-line
/// status in [color], and an optional button on the right.
class CardHeader extends StatelessWidget {
  const CardHeader(
      {super.key,
      required this.icon,
      required this.title,
      required this.status,
      required this.color,
      this.statusColor,
      this.trailing});
  final IconData icon;
  final String title, status;
  final Color color;
  final Color? statusColor; // defaults to [color]
  final Widget? trailing;
  @override
  Widget build(BuildContext context) => Row(children: [
        Container(
          padding: const EdgeInsets.all(8),
          decoration: BoxDecoration(color: color.withValues(alpha: .15), borderRadius: BorderRadius.circular(10)),
          child: Icon(icon, color: color, size: 20),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
            Text(status, style: TextStyle(color: statusColor ?? color, fontSize: 13)),
          ]),
        ),
        if (trailing != null) trailing!,
      ]);
}

/// A fade-and-rise entrance (transform/opacity only, like the page's animations).
class Entrance extends StatelessWidget {
  const Entrance({super.key, required this.child, this.delayMs = 0});
  final Widget child;
  final int delayMs;
  @override
  Widget build(BuildContext context) => TweenAnimationBuilder<double>(
        tween: Tween(begin: 0, end: 1),
        duration: Duration(milliseconds: 420 + delayMs),
        curve: Interval(delayMs / (420 + delayMs), 1, curve: Curves.easeOutCubic),
        builder: (_, t, c) =>
            Opacity(opacity: t, child: Transform.translate(offset: Offset(0, 14 * (1 - t)), child: c)),
        child: child,
      );
}

/// The HostSpawn mark: a ring with a glowing core (drawn, no image files).
class Logo extends StatelessWidget {
  const Logo({super.key, this.size = 44});
  final double size;
  @override
  // listens to the theme itself: it's usually built as a const widget, which a rebuild from above skips
  Widget build(BuildContext context) => ValueListenableBuilder<AppTheme>(
        valueListenable: themeNotifier,
        builder: (_, th, __) => Container(
          width: size,
          height: size,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            border: Border.all(color: th.accent, width: size / 11),
            gradient: RadialGradient(colors: [th.accent.withValues(alpha: .33), th.accent2.withValues(alpha: 0)]),
          ),
          child: Center(
            child: Container(
              width: size / 3,
              height: size / 3,
              decoration: BoxDecoration(shape: BoxShape.circle, color: th.accent2),
            ),
          ),
        ),
      );
}

/// Each game's colour and short tag (cards, badges).
Color gameColor(String? game) => switch (game) {
      'Rust' => const Color(0xFFFF7A45),
      'Arma Reforger' => const Color(0xFF5CB8FF),
      'Arma 3' => const Color(0xFF9BD45A),
      _ => C.accent,
    };

String gameTag(String? game) => switch (game) {
      'Rust' => 'RU',
      'Arma Reforger' => 'AR',
      'Arma 3' => 'A3',
      _ => (game ?? '?').substring(0, (game ?? '?').length.clamp(1, 2)).toUpperCase(),
    };

/// The page background: deep navy with two soft glows (teal and violet) and a faint dot grid. Static, so tests that
/// wait for animations to settle still settle.
class Backdrop extends StatelessWidget {
  const Backdrop({super.key, required this.child});
  final Widget child;
  @override
  Widget build(BuildContext context) => Stack(children: [
        const Positioned.fill(child: ColoredBox(color: C.bg)),
        Positioned.fill(child: CustomPaint(painter: _Dots())),
        Positioned(top: -260, left: -180, child: _Glow(C.accent, 620)),
        Positioned(top: 120, right: -260, child: _Glow(C.accent2, 680)),
        Positioned.fill(child: child),
      ]);
}

class _Glow extends StatelessWidget {
  const _Glow(this.color, this.size);
  final Color color;
  final double size;
  @override
  Widget build(BuildContext context) => IgnorePointer(
        child: Container(
          width: size,
          height: size,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: RadialGradient(colors: [color.withValues(alpha: .16), color.withValues(alpha: 0)]),
          ),
        ),
      );
}

class _Dots extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()..color = const Color(0x0DFFFFFF);
    for (double y = 12; y < size.height; y += 28) {
      for (double x = 12; x < size.width; x += 28) {
        canvas.drawCircle(Offset(x, y), 1, p);
      }
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

/// A glass panel: a soft top-to-bottom gradient, a fine border and, optionally, a coloured glow.
BoxDecoration glass({Color? glow, double radius = 16}) => BoxDecoration(
      borderRadius: BorderRadius.circular(radius),
      gradient: const LinearGradient(
          begin: Alignment.topLeft, end: Alignment.bottomRight, colors: [Color(0xE6151C28), Color(0xD90D121A)]),
      border: Border.all(color: glow?.withValues(alpha: .45) ?? C.line2),
      boxShadow: [
        BoxShadow(
            color: (glow ?? Colors.black).withValues(alpha: glow == null ? .35 : .22),
            blurRadius: 28,
            spreadRadius: -6),
      ],
    );

/// Gradient text for headings (teal into violet).
class GradientText extends StatelessWidget {
  const GradientText(this.text, {super.key, this.style});
  final String text;
  final TextStyle? style;
  @override
  Widget build(BuildContext context) => ShaderMask(
        shaderCallback: (r) => LinearGradient(colors: [C.accent, C.accent2]).createShader(r),
        child: Text(text, style: (style ?? const TextStyle()).copyWith(color: Colors.white)),
      );
}

/// A status pill: coloured dot and word, on a tinted background.
class StatusPill extends StatelessWidget {
  const StatusPill(this.state, {super.key});
  final String state;
  @override
  Widget build(BuildContext context) {
    final c = stateColor(state);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: c.withValues(alpha: .13), borderRadius: BorderRadius.circular(20)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: c,
                boxShadow: [BoxShadow(color: c.withValues(alpha: .8), blurRadius: 8)])),
        const SizedBox(width: 7),
        Text(state, style: TextStyle(color: c, fontSize: 12, fontWeight: FontWeight.w600)),
      ]),
    );
  }
}

/// Pick a colour theme (saved on this device).
Future<void> pickTheme(BuildContext context) => showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: C.bg2,
        title: const Text('Theme'),
        content: SizedBox(
          width: 420,
          child: ValueListenableBuilder<AppTheme>(
            valueListenable: themeNotifier,
            builder: (_, current, __) => Wrap(spacing: 12, runSpacing: 12, children: [
              for (final th in themes)
                InkWell(
                  borderRadius: BorderRadius.circular(14),
                  onTap: () => themeNotifier.value = th,
                  child: Container(
                    width: 124,
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(14),
                      color: C.panel2,
                      border: Border.all(
                          color: th.id == current.id ? th.accent : C.line2, width: th.id == current.id ? 2 : 1),
                    ),
                    child: Column(children: [
                      Container(
                        height: 44,
                        width: double.infinity,
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(10),
                          gradient: LinearGradient(colors: [th.accent, th.accent2]),
                        ),
                        child: th.id == current.id ? const Icon(Icons.check, color: Colors.white) : null,
                      ),
                      const SizedBox(height: 8),
                      Text(th.name, style: const TextStyle(fontWeight: FontWeight.w600)),
                    ]),
                  ),
                ),
            ]),
          ),
        ),
        actions: [TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Done'))],
      ),
    );
