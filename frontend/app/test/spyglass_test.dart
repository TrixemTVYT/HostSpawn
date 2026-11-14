// Spyglass card: explains itself, one Spyglass button opens the options, and the chosen option is sent as a confirmed
// action; hidden when the mission has no Spyglass.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/spyglass.dart';
import 'package:hostspawn/theme.dart';

class FakeApi extends Api {
  FakeApi(this.status) : super(Uri.parse('http://127.0.0.1:9'));
  final Map<String, dynamic> status;
  final ran = <String>[];

  @override
  Future<Map<String, dynamic>> spyglass(String server) async => status;

  @override
  Future<String> action(String server, String action, {String arg = '', String? confirm}) async {
    ran.add('$action confirm=$confirm');
    return 'accepted';
  }
}

Future<FakeApi> pump(WidgetTester t, Map<String, dynamic> status) async {
  final api = FakeApi(status);
  await t.pumpWidget(MaterialApp(
      theme: hostspawnTheme(),
      home: Scaffold(body: SingleChildScrollView(child: SpyglassCard(api: api, server: 'i')))));
  await t.pumpAndSettle();
  return api;
}

Future<void> choose(WidgetTester t, String option, String apply) async {
  await t.tap(find.widgetWithText(FilledButton, 'Spyglass'));
  await t.pumpAndSettle();
  await t.tap(find.text(option));
  await t.pumpAndSettle();
  await t.tap(find.text(apply));
  await t.pump();
  await t.pump(const Duration(seconds: 30)); // let the card look again after the change
}

void main() {
  testWidgets('running, with wrongly flagged parts: fix them and restart', (t) async {
    final api = await pump(t, {
      'present': true,
      'on': true,
      'running': true,
      'allowed': 1456,
      'official': ['A3_Data_F_AoW_Loadorder'],
      'other': ['cheat_menu'],
      'flags': [
        {'time': '10:14:27', 'player': 'cc', 'reason': '["A3_Data_F_AoW_Loadorder"]'}
      ],
    });
    expect(find.text('On, and it threw out honest players'), findsOneWidget);
    expect(find.textContaining('missing 1 official Arma 3 part,'), findsOneWidget);
    expect(find.textContaining('The SpyGlass sees you!'), findsNothing); // folded away until asked
    await t.tap(find.text('What is Spyglass?'));
    await t.pump();
    expect(find.textContaining('The SpyGlass sees you!'), findsOneWidget);
    expect(find.textContaining('cheat_menu'), findsOneWidget);
    await choose(t, 'Fix for this Arma 3 (best)', 'Apply and restart');
    expect(api.ran, ['spyglass confirm=spyglass']);
  });

  testWidgets('pause, and nothing to fix yet', (t) async {
    final api = await pump(
        t, {'present': true, 'on': true, 'running': false, 'allowed': 1456, 'official': [], 'other': [], 'flags': []});
    await t.tap(find.widgetWithText(FilledButton, 'Spyglass'));
    await t.pumpAndSettle();
    expect(find.textContaining('Nothing to fix'), findsOneWidget);
    expect(t.widget<ListTile>(find.widgetWithText(ListTile, 'Fix for this Arma 3 (best)')).enabled, isFalse);
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    await choose(t, 'Pause Spyglass (turn off)', 'Apply');
    expect(api.ran, ['spyglass_off confirm=spyglass_off']);
  });

  testWidgets('paused: offers turning it back on', (t) async {
    final api = await pump(t, {
      'present': true,
      'on': false,
      'running': true,
      'pending': false,
      'allowed': 1456,
      'official': [],
      'other': [],
      'flags': []
    });
    expect(find.text('Paused: players are not checked'), findsOneWidget);
    await choose(t, 'Turn Spyglass back on', 'Apply and restart');
    expect(api.ran, ['spyglass_on confirm=spyglass_on']);
  });

  testWidgets('parts missing before anyone is thrown out can be fixed', (t) async {
    final api = await pump(
        t, {'present': true, 'on': true, 'running': true, 'missing': 43, 'official': [], 'other': [], 'flags': []});
    expect(find.text('On, but it doesn\'t know 43 official Arma 3 parts'), findsOneWidget);
    await choose(t, 'Fix for this Arma 3 (best)', 'Apply and restart');
    expect(api.ran, ['spyglass confirm=spyglass']);
  });

  testWidgets('a change the running server has not loaded yet is shown', (t) async {
    await pump(t, {'present': true, 'on': true, 'pending': true, 'official': [], 'other': [], 'flags': []});
    expect(find.text('Changed: waiting for a restart'), findsOneWidget);
  });

  testWidgets('no Spyglass in the mission: card hidden', (t) async {
    await pump(t, {'present': false});
    expect(find.text('Spyglass anti-cheat'), findsNothing);
  });
}
