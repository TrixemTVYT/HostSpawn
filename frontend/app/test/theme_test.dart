// Themes: picking one changes the accent colours everywhere, and the choice is saved on this device.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/store.dart';
import 'package:hostspawn/theme.dart';

void main() {
  tearDown(() => themeNotifier.value = themes.first);

  testWidgets('pick a theme: accent colours follow it', (t) async {
    await t.pumpWidget(MaterialApp(
        theme: hostspawnTheme(),
        home: Builder(builder: (c) => TextButton(onPressed: () => pickTheme(c), child: const Text('open')))));
    await t.tap(find.text('open'));
    await t.pumpAndSettle();
    for (final th in themes) {
      expect(find.text(th.name), findsOneWidget);
    }
    await t.tap(find.text('Ember'));
    await t.pumpAndSettle();
    expect(themeNotifier.value.id, 'ember');
    expect(C.accent, themeById('ember').accent);
    await t.tap(find.text('Done'));
    await t.pumpAndSettle();
  });

  testWidgets('bottom notifications use light text on a solid dark card', (t) async {
    await t.pumpWidget(MaterialApp(
        theme: hostspawnTheme(),
        home: Scaffold(
            body: Builder(
                builder: (c) => TextButton(
                    onPressed: () => ScaffoldMessenger.of(c).showSnackBar(const SnackBar(content: Text('Saved.'))),
                    child: const Text('go'))))));
    await t.tap(find.text('go'));
    await t.pumpAndSettle();
    final style = DefaultTextStyle.of(t.element(find.text('Saved.'))).style;
    expect(style.color, Colors.white);
    expect(t.widget<Material>(find.descendant(of: find.byType(SnackBar), matching: find.byType(Material)).first).color,
        const Color(0xFF1C2433));
  });

  test('saved per device; an unknown id falls back to the default', () async {
    final saved = Saved(MemoryKeyStore());
    expect(await saved.theme(), isNull);
    await saved.setTheme('ocean');
    expect(themeById(await saved.theme()).name, 'Ocean');
    expect(themeById('nope').id, 'aurora');
  });
}
