// Screenshots for the README (docs/images), taken from the real app against a SANDBOX agent
// (backend/tests/app_sandbox.py; fake servers and fake players, nothing real touched).
// The Doctor and Database pictures only: add --plain-name 'Doctor and Database screenshots'.
// Run: HOSTSPAWN_AGENT=http://127.0.0.1:8197 HOSTSPAWN_SHOTS=$PWD/../../docs/images \
//      flutter test integration_test/screenshots_test.dart -d linux
// Needs X11 with wmctrl and ImageMagick's import (it captures this app's own window, nothing else).
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/main.dart';
import 'package:hostspawn/screens/files_tab.dart';
import 'package:hostspawn/screens/players.dart';
import 'package:hostspawn/screens/server_tabs.dart';
import 'package:hostspawn/store.dart';
import 'package:hostspawn/theme.dart';
import 'package:integration_test/integration_test.dart';

final agent = Platform.environment['HOSTSPAWN_AGENT'] ?? 'http://127.0.0.1:8199';
final shots = Platform.environment['HOSTSPAWN_SHOTS'] ?? '../../docs/images';
const user = 'sandboxowner', password = 'sandbox horse battery';

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;

  Future<void> waitFor(WidgetTester t, Finder f, {int secs = 30}) async {
    for (var i = 0; i < secs * 10; i++) {
      await t.pump(const Duration(milliseconds: 100));
      if (f.evaluate().isNotEmpty) return;
    }
    throw TestFailure('Timed out waiting for $f');
  }

  Future<void> settle(WidgetTester t, [int ms = 1500]) async {
    await t.runAsync(() => Future.delayed(Duration(milliseconds: ms)));
    await t.pump();
  }

  // this process's own window only (wmctrl -lp lists the owning pid)
  Future<void> shot(WidgetTester t, String name) async {
    await settle(t);
    final r = await t.runAsync(() => Process.run('bash', [
          '-c',
          'id=\$(wmctrl -lp | awk -v p=$pid \'\$3==p{print \$1}\' | tail -1); '
              // fully on screen first (a window hanging off the left edge was captured cut off)
              '[ -n "\$id" ] && wmctrl -i -r "\$id" -e 0,60,60,-1,-1 && sleep 0.5; '
              '[ -n "\$id" ] && import -window "\$id" "$shots/$name.png" && '
              // cut off the see-through shadow round the window (its size is in _GTK_FRAME_EXTENTS: left, right, top, bottom)
              'e=(\$(xprop -id "\$id" _GTK_FRAME_EXTENTS | grep -o "[0-9].*" | tr -d ,)); '
              'W=\$(xwininfo -id "\$id" | awk "/Width:/{print \\\$2}"); H=\$(xwininfo -id "\$id" | awk "/Height:/{print \\\$2}"); '
              'if [ \${#e[@]} = 4 ]; then convert "$shots/$name.png" -crop '
              '"\$((W - e[0] - e[1]))x\$((H - e[2] - e[3]))+\${e[0]}+\${e[2]}" '
              '+repage "$shots/$name.png"; fi'
        ]));
    if (r!.exitCode != 0) throw TestFailure('screenshot $name failed: ${r.stderr}');
  }

  Future<String> make(Api api, String terms, String game, String mode, Map<String, dynamic> opts) async {
    final have = ((await api.overview())['servers'] as List).cast<Map>().where((s) => s['name'] == opts['name']);
    if (have.isNotEmpty) return '${have.first['id']}'; // made by an earlier run
    var job = (await api.deploy(game, mode, opts,
        requestId: 'shots-$game-${DateTime.now().millisecondsSinceEpoch}', acceptedTerms: terms));
    for (var i = 0; i < 360 && job['status'] != 'done'; i++) {
      if (job['status'] == 'failed') fail('setup failed: ${job['log']}');
      if (job['status'] == 'waiting') {
        try {
          await api.jobAction('${job['id']}', 'skip');
        } on ApiError {
          await api.jobAction('${job['id']}', 'continue');
        }
      }
      await Future.delayed(const Duration(milliseconds: 500));
      job = await api.job('${job['id']}');
    }
    return '${job['server_id']}';
  }

  testWidgets('README screenshots', (t) async {
    Directory(shots).createSync(recursive: true);
    final api = Api(Uri.parse(agent));
    final info = await t.runAsync(() => api.info());
    const device = 'HostSpawn app (Linux)'; // not this machine's name: the pictures go on GitHub
    await t.runAsync(() => info!.setupNeeded ? api.setup(user, password, device) : api.login(user, password, device));
    await t.runAsync(() => api.post('settings', {'community': 'Riverside Gaming'}));
    final terms = (await t.runAsync(() => api.terms()))!['version'] as String;
    final rust = (await t.runAsync(() => make(api, terms, 'rust', 'vanilla', {'name': 'Riverside Rust 2x'})))!;
    await t.runAsync(() => make(api, terms, 'arma3', 'vanilla', {'name': 'Altis Frontier', 'steam_user': 'fakeuser'}));
    await t.runAsync(() => api.saveSettings(rust, {
          'messages': [
            {'text': 'Welcome to {server}! {players}/{max} online. Next restart {next_restart}.', 'every_min': 30},
            {'text': 'Join our Discord for events and support.', 'every_min': 45},
            {
              'text': 'Wipe tonight at 20:00. Good luck!',
              'times': ['19:00', '19:30']
            },
          ],
        }));

    await t.pumpWidget(HostSpawnApp(saved: Saved(MemoryKeyStore()..data['hostspawn.agent'] = agent)));
    await waitFor(t, find.byType(TextField));
    await shot(t, '00-sign-in');
    // then signed in with the token made above (signing in through the screen would log this machine's name)
    final store = MemoryKeyStore()
      ..data['hostspawn.agent'] = agent
      ..data['hostspawn.token'] = api.token!;
    await t.pumpWidget(HostSpawnApp(key: UniqueKey(), saved: Saved(store)));

    // dashboard, once fake players have joined
    await waitFor(t, find.textContaining('Riverside Rust 2x · '));
    await settle(t, 8000); // the dashboard refreshes itself
    await shot(t, '01-dashboard');

    // the new-instance wizard
    await t.tap(find.textContaining('New instance'));
    await waitFor(t, find.text('Choose a game'));
    await shot(t, '02-new-instance');
    await t.pageBack();
    await settle(t, 800);

    // one instance: overview, players, in-game messages
    await t.tap(find.textContaining('Riverside Rust 2x · ').last);
    await waitFor(t, find.text('Server Doctor'));
    await shot(t, '03-instance');
    await t.tap(find.widgetWithText(Tab, 'Players'));
    for (var i = 0; i < 30 && find.text('Kick').evaluate().isEmpty; i++) {
      await settle(t, 1000);
      await t.tap(find.byTooltip('Refresh').first, warnIfMissed: false);
    }
    await shot(t, '04-players');
    await t.tap(find.widgetWithText(Tab, 'Settings'));
    await settle(t, 800);
    final list = find.descendant(of: find.byType(SettingsTab), matching: find.byType(Scrollable)).first;
    await t.scrollUntilVisible(find.text('Add a message'), 200, scrollable: list);
    await shot(t, '05-messages');
    await t.tap(find.text('Installation').first);
    await settle(t, 2500);
    await shot(t, '09-installation');
    await t.tap(find.text('Game settings').first); // back to the first page for the next run

    // everyone ever seen, and one player's profile
    await t.pageBack();
    await settle(t, 800);
    await t.tap(find.widgetWithText(TextButton, 'Players'));
    await waitFor(t, find.byType(PlayersScreen));
    await settle(t);
    await shot(t, '06-players-all');

    // themes: the picker, then the dashboard in another theme
    await t.pageBack();
    await settle(t, 800);
    await t.tap(find.byTooltip(user));
    await settle(t, 600);
    await t.tap(find.text('Theme'));
    await settle(t, 600);
    await t.tap(find.text('Ember'));
    await shot(t, '07-themes');
    await t.tap(find.text('Done'));
    await shot(t, '08-dashboard-ember');
    themeNotifier.value = themes.first;

    // the Files tab on the Arma 3 instance, with a mission script open
    final arma = ((await t.runAsync(() => api.overview()))!['servers'] as List)
        .cast<Map>()
        .firstWhere((s) => s['name'] == 'Altis Frontier')['id'] as String;
    for (final (path, text) in [
      ('server/mpmissions/Training.Stratis/mission.sqm', 'version=54;\nclass Mission\n{\n};\n'),
      ('server/mpmissions/Training.Stratis/init.sqf', armaTemplates.values.first),
      ('server/mpmissions/Training.Stratis/description.ext', armaTemplates.values.elementAt(2)),
    ]) {
      await t.runAsync(() async {
        try {
          await api.saveFile(arma, path, text, '');
        } on ApiError {
          // already made by an earlier run (caught in here: runAsync reports errors that escape it)
        }
      });
    }
    await settle(t, 800);
    await t.tap(find.textContaining('Altis Frontier · ').last);
    await waitFor(t, find.text('Server Doctor'));
    await t.tap(find.widgetWithText(Tab, 'Files'));
    await settle(t, 1500);
    await t.tap(find.text('description.ext'));
    await settle(t, 1500);
    await shot(t, '10-files');
    await t.tap(find.widgetWithText(Tab, 'Tools'));
    await settle(t, 2000);
    await shot(t, '13-tools');
  });

  testWidgets('Doctor and Database screenshots', (t) async {
    Directory(shots).createSync(recursive: true);
    final api = Api(Uri.parse(agent));
    final info = await t.runAsync(() => api.info());
    const device = 'HostSpawn app (Linux)';
    await t.runAsync(() => info!.setupNeeded ? api.setup(user, password, device) : api.login(user, password, device));
    await t.runAsync(() => api.post('settings', {'community': 'Riverside Gaming'}));
    final terms = (await t.runAsync(() => api.terms()))!['version'] as String;
    final rust = (await t.runAsync(() => make(api, terms, 'rust', 'vanilla', {'name': 'Riverside Rust 2x'})))!;
    final s = (await t.runAsync(() => api.server(rust)))!;
    final folder = '${s['folder']}', port = (s['ports'] as Map)['port'] as int;

    // A plugin's database (Economics) with a few made-up balances.
    await t.runAsync(() => Process.run('python3', [
          '-I',
          '-c',
          'import sqlite3, sys, pathlib; p = pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True); '
              'c = sqlite3.connect(p); c.execute("CREATE TABLE IF NOT EXISTS balances (player TEXT PRIMARY KEY, name TEXT, money INTEGER, last_seen TEXT)"); '
              'c.executemany("INSERT OR REPLACE INTO balances VALUES (?, ?, ?, ?)", [("p%02d" % i, n, m, "2026-11-0%d" % (1 + i % 7)) '
              'for i, (n, m) in enumerate([("Ash", 15200), ("Bramble", 9800), ("Cinder", 7450), ("Dune", 5120), ("Ember", 4300), '
              '("Flint", 2950), ("Grove", 1800), ("Hollow", 940), ("Iris", 610), ("Juniper", 120)])]); c.commit()',
          '$folder/server/oxide/data/Economics.db'
        ]));

    // Break it on purpose: stopped, another program holding its game port, and a plugin that doesn't compile.
    await t.runAsync(() => api.action(rust, 'stop', confirm: 'stop'));
    await settle(t, 4000);
    final holder = (await t.runAsync(() => RawDatagramSocket.bind(InternetAddress.anyIPv4, port)))!;
    final logs = Directory('$folder/logs').listSync().whereType<File>().where((f) => f.path.endsWith('.log')).toList()
      ..sort((a, b) => a.lastModifiedSync().compareTo(b.lastModifiedSync()));
    if (logs.isNotEmpty) {
      logs.last.writeAsStringSync(
          'Error while compiling: Kits.cs(41,12): error CS0117: BasePlayer has no member Heal\n',
          mode: FileMode.append);
    }
    try {
      final store = MemoryKeyStore()
        ..data['hostspawn.agent'] = agent
        ..data['hostspawn.token'] = api.token!;
      await t.pumpWidget(HostSpawnApp(key: UniqueKey(), saved: Saved(store)));
      await waitFor(t, find.textContaining('Riverside Rust 2x · '));
      await settle(t, 1500);
      await t.tap(find.textContaining('Riverside Rust 2x · ').last);
      await waitFor(t, find.textContaining('problem'));
      await shot(t, '11-server-doctor');
      await t.tap(find.widgetWithText(Tab, 'Files'));
      await settle(t, 800);
      await t.tap(find.widgetWithText(ChoiceChip, 'Database'));
      await waitFor(t, find.text('balances'));
      await waitFor(t, find.text('money'));
      await t.tap(find.text('money'));
      await settle(t, 600);
      await t.tap(find.text('money'));
      await settle(t, 1200);
      await shot(t, '12-database');
    } finally {
      holder.close();
      await t.runAsync(() => api.action(rust, 'start'));
    }
  });
}
