// Server Doctor card (all good, findings with a fix button, fixes hidden from roles that can't run) and the Database
// tab (tables, read-only by default, edit a cell only after typing CHANGE, refused queries show the reason).
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/database_tab.dart';
import 'package:hostspawn/screens/doctor.dart';
import 'package:hostspawn/screens/open_folder.dart';
import 'package:hostspawn/theme.dart';

class FakeApi extends Api {
  FakeApi({this.findings = const [], String host = '127.0.0.1'}) : super(Uri.parse('http://$host:9'));
  final List<Map<String, dynamic>> findings;
  final calls = <String>[];

  @override
  Future<Map<String, dynamic>> doctor(String server) async =>
      {'findings': findings, 'checked': 14, 'ok': !findings.any((f) => f['level'] == 'problem')};

  @override
  Future<List<dynamic>> dbSources(String server) async => [
        {'id': 'mariadb', 'kind': 'mariadb', 'name': 'altislife', 'label': 'Altis Life database (MariaDB)'}
      ];
  @override
  Future<List<dynamic>> dbTables(String server, String source) async => [
        {'name': 'players', 'rows': 2, 'approx': true},
        {'name': 'vehicles', 'rows': 0, 'approx': true},
      ];
  @override
  Future<Map<String, dynamic>> dbRows(String server, String source, String table,
          {int page = 0, String search = '', String order = '', bool desc = false}) async =>
      {
        'columns': ['uid', 'name', 'cash'],
        'key': ['uid'],
        'rows': [
          ['1', 'Alice', '100'],
          ['2', null, '5'],
        ],
        'total': 2,
      };
  @override
  Future<Map<String, dynamic>> dbQuery(String server, String source, String sql) async {
    calls.add('query $sql');
    throw ApiError(400, 'That query changes data. Turn on Changes to run it (a backup is made first).');
  }

  @override
  Future<Map<String, dynamic>> dbChange(String server, String source,
      {String sql = '', Map<String, dynamic>? edit}) async {
    calls.add('change $edit');
    return {'changed': 1, 'backup': 'altislife_x.sql'};
  }
}

const _port = {
  'id': 'port_taken',
  'level': 'problem',
  'title': 'Port 28015 is already in use by another program',
  'why': 'Something else has it.',
  'fix': 'Stop the other program.',
  'where': '',
};
const _repair = {
  'id': 'segfault',
  'level': 'warning',
  'title': 'The game crashed inside its own code',
  'why': 'Usually a broken file.',
  'fix': 'Try Repair first.',
  'where': 'console log: Segmentation fault',
  'action': {'kind': 'install', 'op': 'repair', 'label': 'Repair'},
};

Future<void> showDoctor(WidgetTester t, FakeApi api, {bool canFix = true, List<Map>? fixed}) async {
  await t.binding.setSurfaceSize(const Size(1200, 900));
  await t.pumpWidget(MaterialApp(
      theme: hostspawnTheme(),
      home: Scaffold(
          body: SingleChildScrollView(
              child: DoctorCard(
                  api: api,
                  s: const {
                    'id': 'r1',
                    'status': {'state': 'stopped'}
                  },
                  canFix: canFix,
                  fix: (a) async => fixed?.add(a))))));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('doctor: all good', (t) async {
    await showDoctor(t, FakeApi());
    expect(find.text('All good: 14 checks passed'), findsOneWidget);
  });

  testWidgets('doctor: problems first, with a fix button that runs the fix', (t) async {
    final fixed = <Map>[];
    await showDoctor(t, FakeApi(findings: [_port, _repair]), fixed: fixed);
    expect(find.text('1 problem found'), findsOneWidget);
    expect(find.text('Port 28015 is already in use by another program'), findsOneWidget);
    expect(find.text('console log: Segmentation fault'), findsOneWidget);
    await t.tap(find.text('Repair'));
    await t.pump();
    expect(fixed.single['op'], 'repair');
    await t.pump(const Duration(seconds: 5)); // the card checks again after a fix
  });

  testWidgets('doctor: roles that can\'t run the instance see no fix buttons', (t) async {
    await showDoctor(t, FakeApi(findings: [_repair]), canFix: false);
    expect(find.text('Repair'), findsNothing);
    expect(find.textContaining('Try Repair first.', findRichText: true), findsOneWidget);
  });

  test('open folder: only on this computer; file folders', () {
    // HostSpawn runs on Linux: 127.0.0.1 is "this computer" only there (on Windows it's the SSH tunnel to a server)
    expect(isLocal(FakeApi()), Platform.isLinux);
    expect(isLocal(FakeApi(host: '192.0.2.10')), isFalse);
    expect(folderOf('/srv/i1', 'server/cfg/server.cfg'), '/srv/i1/server/cfg');
    expect(folderOf('/srv/i1', 'notes.txt'), '/srv/i1');
    expect(folderOf('/srv/i1'), '/srv/i1');
  });

  Future<FakeApi> showDb(WidgetTester t) async {
    final api = FakeApi();
    await t.binding.setSurfaceSize(const Size(1400, 900));
    await t.pumpWidget(
        MaterialApp(theme: hostspawnTheme(), home: Scaffold(body: DatabaseTab(api: api, s: const {'id': 'a1'}))));
    await t.pumpAndSettle();
    return api;
  }

  testWidgets('database: tables and rows, read-only by default', (t) async {
    final api = await showDb(t);
    expect(find.text('vehicles'), findsOneWidget);
    expect(find.text('Alice'), findsOneWidget);
    expect(find.text('NULL'), findsOneWidget);
    expect(find.text('Read-only'), findsOneWidget);
    expect(find.byTooltip('Delete row…'), findsNothing);
    await t.tap(find.text('SQL'));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField).last, 'DELETE FROM players');
    await t.tap(find.text('Run'));
    await t.pumpAndSettle();
    expect(find.textContaining('Turn on Changes'), findsOneWidget);
    expect(api.calls.where((c) => c.startsWith('change')), isEmpty);
  });

  testWidgets('database: edit a cell after typing CHANGE', (t) async {
    final api = await showDb(t);
    await t.tap(find.text('Read-only'));
    await t.pumpAndSettle();
    expect(find.byTooltip('Delete row…'), findsNWidgets(2));
    await t.tap(find.text('Alice'));
    await t.pump(const Duration(milliseconds: 50));
    await t.tap(find.text('Alice'));
    await t.pumpAndSettle();
    expect(find.text('Edit name'), findsOneWidget);
    await t.enterText(find.byType(TextField).last, 'Alicia');
    await t.tap(find.text('Next'));
    await t.pumpAndSettle();
    expect(find.text('Type CHANGE to confirm.'), findsOneWidget);
    final btn = find.widgetWithText(FilledButton, 'Change');
    expect(t.widget<FilledButton>(btn).onPressed, isNull);
    await t.enterText(find.byType(TextField).last, 'CHANGE');
    await t.pump();
    await t.tap(btn);
    await t.pumpAndSettle();
    expect(api.calls.single, contains('{op: update, table: players, key: {uid: 1}, set: {name: Alicia}}'));
    expect(find.textContaining('Backup: altislife_x.sql'), findsOneWidget);
  });
}
