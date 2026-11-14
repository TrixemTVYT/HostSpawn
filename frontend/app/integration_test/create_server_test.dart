// Create a server from the app, start to finish, against the SANDBOX agent (backend/tests/app_sandbox.py, port 8199).
// The sandbox's fake SteamCMD "downloads" a tiny fake Rust server: nothing comes from Steam.
// HOSTSPAWN_PAUSE=3 holds key screens for 3 s (for screenshots).
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/main.dart';
import 'package:hostspawn/screens/server_tabs.dart';
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

  Future<void> tapText(WidgetTester t, String text) async {
    await waitFor(t, find.text(text));
    await t.ensureVisible(find.text(text).first);
    await t.pump(const Duration(milliseconds: 300));
    await t.tap(find.text(text).first);
    await t.pump(const Duration(milliseconds: 400));
  }

  testWidgets('New server → Rust → settings → terms → setup with a guided step → ready → server panel', (t) async {
    final store = MemoryKeyStore()..data['hostspawn.agent'] = agent;
    await t.pumpWidget(HostSpawnApp(saved: Saved(store)));

    // first run or sign in (works on a fresh sandbox or one used before)
    await waitFor(t, find.byType(TextField));
    final fields = find.byType(TextField);
    await t.enterText(fields.at(0), user);
    await t.enterText(fields.at(1), password);
    if (find.text('Create account').evaluate().isNotEmpty) {
      await t.enterText(fields.at(2), password);
      await t.tap(find.text('Create account'));
    } else {
      await t.tap(find.widgetWithText(FilledButton, 'Sign in'));
    }

    // dashboard → New server
    await waitFor(t, find.textContaining('New instance'));
    await t.tap(find.textContaining('New instance'));

    // 1. game (coming-soon games can't be picked)
    await waitFor(t, find.text('Choose a game'));
    expect(find.text('Rust'), findsOneWidget);
    await hold(t);
    await tapText(t, 'Rust');

    // 2. server type
    await waitFor(t, find.text('Choose a server type'));
    await hold(t);
    expect(find.text('Modded (Carbon)'), findsOneWidget); // coming soon: shown but can't be picked
    await tapText(t, 'Rust');

    // 3. settings: a name, the Oxide question, restarts and wipes
    await waitFor(t, find.text('Settings'));
    expect(find.textContaining('Install Oxide'), findsOneWidget);
    await t.enterText(find.byType(TextField).first, 'App Test Rust');
    await t.pump(const Duration(milliseconds: 500)); // let the page slide in
    final page = find.descendant(of: find.byType(ListView).last, matching: find.byType(Scrollable)).first;
    await t.scrollUntilVisible(find.text('Map wipe'), 200, scrollable: page);
    expect(find.text('Blueprints'), findsOneWidget);
    await hold(t);
    await tapText(t, 'Next: review');

    // 4. review: Create stays disabled until the terms are accepted
    await waitFor(t, find.text('Review and create'));
    expect(find.text('App Test Rust'), findsOneWidget);
    await t.pump(const Duration(milliseconds: 500));
    final review = find.descendant(of: find.byType(ListView).last, matching: find.byType(Scrollable)).first;
    await t.scrollUntilVisible(find.text('Create instance'), 200, scrollable: review);
    final create = find.ancestor(
        of: find.text('Create instance'), matching: find.byWidgetPredicate((w) => w is ButtonStyleButton));
    expect(t.widget<ButtonStyleButton>(create).onPressed, isNull);
    await tapText(t, 'I have read and accept these terms');
    expect(t.widget<ButtonStyleButton>(create).onPressed, isNotNull);
    await hold(t);
    await t.tap(create);

    // setup runs: download (fake), configure, start, wait for the server, register, then the guided ports step
    await waitFor(t, find.text('Setting up App Test Rust'));
    await hold(t);
    await waitFor(t, find.text('Let players in'), secs: 120);
    expect(find.textContaining('sudo ufw allow'), findsOneWidget); // the commands are shown, not run
    await hold(t);
    await tapText(t, 'Skip for now');

    // ready
    await waitFor(t, find.text('Your instance is ready'), secs: 60);
    expect(find.textContaining('Players connect to'), findsOneWidget);
    await hold(t);
    await tapText(t, 'Open instance panel');
    await waitFor(t, find.text('Server Doctor'));
    expect(find.text('App Test Rust'), findsWidgets);
    await waitFor(t, find.text('running'), secs: 30);
    await hold(t);

    // Settings tab: change the player limit, save, and see it saved (the panel reloads from the agent)
    await t.tap(find.widgetWithText(Tab, 'Settings'));
    await t.pump(const Duration(milliseconds: 600));
    final limit = find.widgetWithText(TextField, 'Player limit (1–500)');
    await waitFor(t, limit);
    await t.enterText(limit, '42');
    await t.pump();
    final settingsList = find.descendant(of: find.byType(SettingsTab), matching: find.byType(Scrollable)).first;
    await t.scrollUntilVisible(find.text('Save changes'), 200, scrollable: settingsList);
    await tapText(t, 'Save changes');
    await waitFor(t, find.textContaining('Saved.'), secs: 15);
    await t.pump(const Duration(seconds: 1));
    expect(t.widget<TextField>(limit).controller!.text, '42');
    await hold(t);

    // Settings › Backups: Back up now makes a backup that appears in the list
    await tapText(t, 'Backups');
    await t.pump(const Duration(milliseconds: 600));
    await waitFor(t, find.text('No backups yet'));
    await tapText(t, 'Back up now');
    await waitFor(t, find.textContaining('(newest first)'), secs: 60);
    await hold(t);

    // delete it again: type the exact name, keep backups (default), back to the dashboard without it
    await tapText(t, 'Delete');
    await t.pump(const Duration(milliseconds: 600));
    await tapText(t, 'Delete…');
    await waitFor(t, find.text('Delete App Test Rust?'));
    final button = find.widgetWithText(FilledButton, 'Delete instance');
    expect(t.widget<FilledButton>(button).onPressed, isNull);
    await t.enterText(find.descendant(of: find.byType(AlertDialog), matching: find.byType(TextField)), 'App Test Rust');
    await t.pump();
    await hold(t);
    await t.tap(button);
    await waitFor(t, find.textContaining('App Test Rust: deleted'), secs: 60);
    await waitFor(t, find.textContaining('New instance')); // back on the dashboard
    await t.pump(const Duration(seconds: 2));
    expect(find.textContaining('App Test Rust · '), findsNothing); // its card is gone
    await hold(t);
  });
}
