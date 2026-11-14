// The instance page's layout: six tabs (Files for admins only), the top bar's Start/Restart, Stop and More, the Tools
// tab, and a Server Doctor fix that opens the right page inside Settings.
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/server.dart';
import 'package:hostspawn/theme.dart';

class FakeApi extends Api {
  FakeApi(this.state, {this.findings = const []}) : super(Uri.parse('http://127.0.0.1:9'));
  final String state;
  final List<Map<String, dynamic>> findings;
  final done = <String>[];

  @override
  Future<Map<String, dynamic>> server(String id) async => {
        'id': id,
        'name': 'Riverside',
        'game': 'Arma 3',
        'mode_name': 'Altis Life',
        'actions': ['start', 'stop', 'restart', 'update', 'backup', 'spyglass', 'spyglass_off', 'spyglass_on'],
        'confirm': <String>[],
        'controllable': true,
        'folder': '/srv/riverside',
        'status': {'state': state, 'players': 3},
        'settings': {'max_players': 32},
        'checks': {'process': state == 'running'},
      };

  @override
  Future<Map<String, dynamic>> doctor(String server) async => {'findings': findings, 'checked': 12, 'ok': true};

  @override
  Future<Map<String, dynamic>> spyglass(String server) async =>
      {'present': true, 'on': true, 'allowed': 1456, 'official': [], 'other': [], 'flags': []};

  @override
  Future<Map<String, dynamic>> logs(String id) async => {'console': 'ok'};

  @override
  Future<String> action(String server, String action, {String arg = '', String? confirm}) async {
    done.add(action);
    return 'done';
  }

  @override
  Future<Map<String, dynamic>> installAction(String server, String action, {String confirm = '', int warnS = 0}) async {
    done.add('install $action');
    return {};
  }
}

Future<void> open(WidgetTester t, Api api, Me me) async {
  await t.binding.setSurfaceSize(const Size(1300, 900));
  await t.pumpWidget(MaterialApp(theme: hostspawnTheme(), home: ServerScreen(api: api, me: me, id: 'a3')));
  await t.pump(const Duration(milliseconds: 500));
  await t.pumpAndSettle(); // the Doctor and Spyglass load after the instance
}

List<String> tabNames(WidgetTester t) => t.widgetList<Tab>(find.byType(Tab)).map((x) => x.text ?? '').toList();

void main() {
  final admin = Me('owner', 'admin', null), mod = Me('m', 'moderator', null);

  testWidgets('running, admin: six tabs, Restart is the main button, the rest is under More', (t) async {
    final api = FakeApi('running');
    await open(t, api, admin);
    expect(tabNames(t), ['Overview', 'Players', 'Console', 'Files', 'Settings', 'Tools']);
    expect(find.widgetWithText(FilledButton, 'Restart'), findsOneWidget);
    expect(find.text('Start'), findsNothing);
    expect(find.text('3 / 32'), findsWidgets); // players in the top bar and on Overview
    await t.tap(find.text('More'));
    await t.pumpAndSettle();
    expect(find.text('Update'), findsOneWidget);
    expect(find.text('Spyglass'), findsNothing); // Spyglass is on the Tools tab
    await t.tap(find.text('Back up now'));
    await t.pumpAndSettle();
    expect(api.done, ['backup']);
    await t.pump(const Duration(seconds: 4)); // the reload after an action
    await t.pumpWidget(const SizedBox()); // stop the timers
  });

  testWidgets('stopped, moderator: no Files tab, Start is the main button, Stop is greyed out', (t) async {
    await open(t, FakeApi('stopped'), mod);
    expect(tabNames(t), ['Overview', 'Players', 'Console', 'Settings', 'Tools']);
    expect(find.widgetWithText(FilledButton, 'Start'), findsOneWidget);
    final stop = find.ancestor(of: find.text('Stop'), matching: find.byWidgetPredicate((w) => w is ButtonStyleButton));
    expect(t.widget<ButtonStyleButton>(stop).onPressed, isNull);
    await t.tap(find.widgetWithText(Tab, 'Settings'));
    await t.pumpAndSettle();
    expect(find.text('Backups'), findsWidgets);
    expect(find.text('Delete'), findsNothing); // admins only
    await t.pumpWidget(const SizedBox());
  });

  testWidgets('Tools tab: Spyglass, Copy for AI, Open folder and PC check', (t) async {
    await open(t, FakeApi('running'), admin);
    await t.tap(find.widgetWithText(Tab, 'Tools'));
    await t.pumpAndSettle();
    expect(find.text('Spyglass anti-cheat'), findsOneWidget);
    expect(find.text('Copy for AI'), findsWidgets);
    // on Linux the folder is on this computer; elsewhere (Windows through a tunnel) it's on the server
    expect(find.text(Platform.isLinux ? 'Open folder' : 'Copy folder path'), findsWidgets);
    expect(find.text('Run PC check'), findsOneWidget);
    await t.pumpWidget(const SizedBox());
  });

  testWidgets('a Server Doctor repair opens Settings › Installation', (t) async {
    final api = FakeApi('running', findings: [
      {
        'id': 'segfault',
        'level': 'warning',
        'title': 'The server crashed',
        'why': 'Broken files.',
        'fix': 'Repair.',
        'where': '',
        'action': {'kind': 'install', 'op': 'repair', 'label': 'Repair'},
      }
    ]);
    await open(t, api, admin);
    await t.tap(find.text('Repair'));
    await t.pumpAndSettle();
    await t.tap(find.descendant(of: find.byType(AlertDialog), matching: find.text('Repair')));
    await t.pumpAndSettle();
    expect(api.done, ['install repair']);
    final tabs = t.widget<TabBar>(find.byType(TabBar)).controller!;
    expect(tabs.index, 4); // Settings
    final menu = t.widget<ListTile>(find.ancestor(of: find.text('Installation'), matching: find.byType(ListTile)));
    expect(menu.selected, isTrue);
    await t.pump(const Duration(seconds: 5)); // the Doctor looks again after a fix
    await t.pumpWidget(const SizedBox());
  });
}
