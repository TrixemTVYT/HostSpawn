// Players in the real app, against the SANDBOX agent (backend/tests/app_sandbox.py; HOSTSPAWN_AGENT picks it): a fake Rust
// server is made through the API, then the app opens its Players tab, bans a fake player and finds them on Bans.
// HOSTSPAWN_PAUSE=3 holds key screens for 3 s (for screenshots).
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/main.dart';
import 'package:hostspawn/screens/players.dart';
import 'package:hostspawn/store.dart';
import 'package:integration_test/integration_test.dart';

final agent = Platform.environment['HOSTSPAWN_AGENT'] ?? 'http://127.0.0.1:8199';
const user = 'sandboxowner', password = 'sandbox horse battery';

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;
  final pause = Duration(seconds: int.tryParse(Platform.environment['HOSTSPAWN_PAUSE'] ?? '') ?? 0);

  Future<void> waitFor(WidgetTester t, Finder f, {int secs = 30}) async {
    for (var i = 0; i < secs * 10; i++) {
      await t.pump(const Duration(milliseconds: 100));
      if (f.evaluate().isNotEmpty) return;
    }
    throw TestFailure('Timed out waiting for $f');
  }

  Future<void> hold(WidgetTester t) async {
    if (pause > Duration.zero) await t.runAsync(() => Future.delayed(pause));
    await t.pump();
  }

  testWidgets('Players tab → ban a fake player → Bans page → unban', (t) async {
    // a fake Rust server with fake players, made through the API (the wizard has its own test)
    final api = Api(Uri.parse(agent));
    final info = await t.runAsync(() => api.info());
    await t.runAsync(() =>
        info!.setupNeeded ? api.setup(user, password, 'players test') : api.login(user, password, 'players test'));
    final terms = (await t.runAsync(() => api.terms()))!['version'] as String;
    var job = (await t.runAsync(() => api.deploy('rust', 'vanilla', {'name': 'Players App Rust'},
        requestId: 'players-${DateTime.now().millisecondsSinceEpoch}', acceptedTerms: terms)))!;
    for (var i = 0; i < 240 && job['status'] != 'done'; i++) {
      if (job['status'] == 'waiting') await t.runAsync(() => api.jobAction('${job['id']}', 'skip'));
      if (job['status'] == 'failed') fail('setup failed: ${job['log']}');
      await t.runAsync(() => Future.delayed(const Duration(milliseconds: 500)));
      job = (await t.runAsync(() => api.job('${job['id']}')))!;
    }
    final sid = '${job['server_id']}';
    try {
      final store = MemoryKeyStore()..data['hostspawn.agent'] = agent;
      await t.pumpWidget(HostSpawnApp(saved: Saved(store)));
      await waitFor(t, find.byType(TextField));
      final fields = find.byType(TextField);
      await t.enterText(fields.at(0), user);
      await t.enterText(fields.at(1), password);
      await t.tap(find.widgetWithText(FilledButton, 'Sign in'));

      await waitFor(t, find.textContaining('Players App Rust'));
      await t.tap(find.textContaining('Players App Rust').last); // the finished setup is listed too
      await waitFor(t, find.text('Server Doctor'));
      await t.tap(find.widgetWithText(Tab, 'Players'));
      // fake players appear within a few seconds (the sandbox checks every 2 s)
      for (var i = 0; i < 30 && find.text('Kick').evaluate().isEmpty; i++) {
        await t.runAsync(() => Future.delayed(const Duration(seconds: 1)));
        await t.tap(find.byTooltip('Refresh'));
        await t.pump(const Duration(milliseconds: 500));
      }
      expect(find.textContaining('online'), findsWidgets);
      await hold(t);
      final first = find.descendant(of: find.byType(PlayersTab), matching: find.byType(ListTile)).first;
      final name = (t.widget<ListTile>(first).title as Text).data!;

      await t.tap(find.text('Ban').first);
      await t.pump(const Duration(milliseconds: 500));
      await t.tap(find.text('Permanent'));
      await t.enterText(
          find.descendant(of: find.byType(AlertDialog), matching: find.byType(TextField)), 'app test ban');
      await t.pump();
      await hold(t);
      await t.tap(find.widgetWithText(FilledButton, 'Ban'));
      await waitFor(t, find.text('Banned on every server'));
      await t.pump(const Duration(seconds: 1));
      expect(find.text(name), findsNothing); // kicked straight away

      // Bans page from the dashboard: there, then unban
      await t.pageBack();
      await t.pump(const Duration(seconds: 1)); // let the server page slide away
      await waitFor(t, find.widgetWithText(TextButton, 'Players'));
      await t.tap(find.widgetWithText(TextButton, 'Players'));
      await waitFor(t, find.text('Bans'));
      await t.tap(find.text('Bans'));
      await waitFor(t, find.text('$name: app test ban'));
      await hold(t);
      final row = find.ancestor(of: find.text('$name: app test ban'), matching: find.byType(ListTile));
      await t.tap(find.descendant(of: row, matching: find.widgetWithText(TextButton, 'Unban')));
      for (var i = 0; i < 50 && find.text('$name: app test ban').evaluate().isNotEmpty; i++) {
        await t.pump(const Duration(milliseconds: 100));
      }
      expect(find.text('$name: app test ban'), findsNothing); // only that ban was lifted
    } finally {
      final s = await t.runAsync(() => api.server(sid));
      await t.runAsync(() => api.deleteServer(sid, '${s!['name']}', keepBackups: false));
    }
  });
}
