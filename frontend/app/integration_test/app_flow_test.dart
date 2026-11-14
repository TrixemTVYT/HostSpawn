// The real app, in a real window, against a SANDBOX agent (backend/tests/app_sandbox.py on port 8199, fake servers only).
// Run: HOSTSPAWN_PRACTICE=1 python3 -I backend/tests/app_sandbox.py <dir>   then   flutter test integration_test -d linux
// HOSTSPAWN_PAUSE=3 holds each screen for 3 s (for screenshots).
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/main.dart';
import 'package:hostspawn/store.dart';
import 'package:integration_test/integration_test.dart';

final agent = Platform.environment['HOSTSPAWN_AGENT'] ?? 'http://127.0.0.1:8199';

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive; // draw like the real app (animations run)
  final pause = Duration(seconds: int.tryParse(Platform.environment['HOSTSPAWN_PAUSE'] ?? '') ?? 0);

  Future<void> waitFor(WidgetTester t, Finder f, {int secs = 20}) async {
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

  testWidgets('first run → dashboard → server panel → logs → devices → pairing code → sign out', (t) async {
    final store = MemoryKeyStore()..data['hostspawn.agent'] = agent; // never the real keychain or live agent
    await t.pumpWidget(HostSpawnApp(saved: Saved(store)));

    // first run: create the owner
    await waitFor(t, find.text('Welcome to HostSpawn'));
    final fields = find.byType(TextField);
    await t.enterText(fields.at(0), 'sandboxowner');
    await t.enterText(fields.at(1), 'sandbox horse battery');
    await t.enterText(fields.at(2), 'sandbox horse battery');
    await hold(t);
    await t.tap(find.text('Create account'));

    // dashboard with the sandbox's three servers
    await waitFor(t, find.text('Sandbox Rust'));
    await waitFor(t, find.text('Sandbox Reforger'));
    expect(find.text('Sandbox Arma 3'), findsOneWidget);
    expect(find.text('Servers running'), findsOneWidget);
    expect(store.data['hostspawn.token'], isNotNull); // the device key is kept (here: in the test's memory store)
    expect(store.data.values.any((v) => v.contains('horse')), isFalse); // the password never is
    await hold(t);

    // server panel
    await t.tap(find.text('Sandbox Rust'));
    await waitFor(t, find.text('Server Doctor'));
    expect(find.text('Restart'), findsOneWidget);
    await hold(t);
    await t.tap(find.widgetWithText(Tab, 'Console'));
    await waitFor(t, find.byType(ChoiceChip));
    await hold(t);
    await t.pageBack();
    await t.pump(const Duration(milliseconds: 500));

    // devices: this app is listed; make a pairing code
    await t.tap(find.byIcon(Icons.account_circle_outlined));
    await t.pump(const Duration(milliseconds: 500));
    await t.tap(find.text('Devices'));
    await waitFor(t, find.textContaining('(this app)'));
    await t.tap(find.text('Pair a device'));
    await waitFor(t, find.textContaining('Works once, for 5 minutes'));
    final code = find.byWidgetPredicate((w) => w is SelectableText && RegExp(r'^\d{6}$').hasMatch(w.data ?? ''));
    expect(code, findsOneWidget);
    await hold(t);
    await t.tap(find.text('Done'));
    await t.pump(const Duration(milliseconds: 500));
    await t.pageBack();
    await t.pump(const Duration(milliseconds: 500));

    // sign out: back to sign-in, key forgotten
    await t.tap(find.byIcon(Icons.account_circle_outlined));
    await t.pump(const Duration(milliseconds: 500));
    await t.tap(find.text('Sign out'));
    await waitFor(t, find.text('Use a pairing code instead'));
    expect(store.data['hostspawn.token'], isNull);
    expect(store.data['hostspawn.last_user'], 'sandboxowner'); // username remembered for next time
    await hold(t);
  });
}
