// Installation tab: version and update state, a confirmed update, reinstall only after typing the name, the running
// job, and admin-only maintenance.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/install_tab.dart';
import 'package:hostspawn/theme.dart';

class FakeApi extends Api {
  FakeApi({this.current}) : super(Uri.parse('http://127.0.0.1:9'));
  Map<String, dynamic>? current;
  final sent = <String>[];
  @override
  Future<Map<String, dynamic>> install(String server) async => {
        'game': 'Rust',
        'mode': 'Rust',
        'framework': null,
        'folder': '/srv/hostspawn/servers/rust/riverside-1a2b',
        'installed': true,
        'running': true,
        'disk_free_gb': 120.5,
        'sizes': {
          'game': 10522669875,
          'saves': 429496729,
          'logs': 10485760,
          'backups': 1288490188,
          'total': 12251152552
        },
        'build': '100',
        'latest_build': '101',
        'updated': 1700000000,
        'checked': 1700003600,
        'check_error': null,
        'update_available': true,
        'next_restart': '2026-11-08 06:00 UK',
        'job': current,
        'history': [
          {
            'op': 'update',
            'result': 'ok',
            'summary': 'Updated (version 99 → 100) and running.',
            'by': 'owner',
            'started': 1700000000
          },
        ],
      };
  @override
  Future<Map<String, dynamic>> installAction(String server, String action, {String confirm = '', int warnS = 0}) async {
    sent.add('$action|$confirm|$warnS');
    return {};
  }
}

Map<String, dynamic> server() => {
      'id': 'r1',
      'name': 'Riverside Rust',
      'controllable': true,
      'can_say': true,
      'status': {'players': 3}
    };

Future<void> show(WidgetTester t, Api api, {String role = 'admin'}) async {
  await t.binding.setSurfaceSize(const Size(1100, 2400));
  await t.pumpWidget(MaterialApp(
      theme: hostspawnTheme(), home: Scaffold(body: InstallTab(api: api, me: Me('owner', role, null), s: server()))));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('update available: confirm, players warned by default when online', (t) async {
    final api = FakeApi();
    await show(t, api);
    expect(find.text('Update available'), findsOneWidget);
    expect(find.text('build 100'), findsOneWidget);
    expect(find.text('build 101'), findsOneWidget);
    expect(find.textContaining('next scheduled restart (06:00)'), findsOneWidget);
    expect(find.text('11.4 GB'), findsOneWidget);
    expect(find.text('410 MB'), findsOneWidget);
    await t.tap(find.text('Update now'));
    await t.pumpAndSettle();
    await t.tap(find.widgetWithText(FilledButton, 'Update now').last);
    await t.pumpAndSettle();
    expect(api.sent, ['update||300']);
  });

  testWidgets('reinstall only after typing the exact name', (t) async {
    final api = FakeApi();
    await show(t, api);
    await t.tap(find.text('Reinstall…'));
    await t.pumpAndSettle();
    final button = find.widgetWithText(FilledButton, 'Reinstall');
    expect(t.widget<FilledButton>(button).onPressed, isNull);
    await t.enterText(
        find.descendant(of: find.byType(AlertDialog), matching: find.byType(TextField)), 'Riverside Rust');
    await t.pump();
    await t.tap(button);
    await t.pumpAndSettle();
    expect(api.sent, ['reinstall|Riverside Rust|0']);
  });

  testWidgets('a running job shows its step and blocks other actions', (t) async {
    final api = FakeApi(current: {
      'op': 'update',
      'state': 'running',
      'step': 'Downloading the update: 40%',
      'progress': 40,
      'log': ['12:00:00 Backing up settings and saves first']
    });
    await show(t, api);
    await t.pump(const Duration(seconds: 2)); // it polls while a job runs
    expect(find.text('Update: Downloading the update: 40%'), findsOneWidget);
    expect(t.widget<OutlinedButton>(find.widgetWithText(OutlinedButton, 'Clean up')).onPressed, isNull);
    api.current = null;
    await t.pump(const Duration(seconds: 2));
  });

  testWidgets('moderators can update but not repair, reinstall or clean up', (t) async {
    await show(t, FakeApi(), role: 'moderator');
    expect(find.text('Only admins can repair, reinstall or clean up.'), findsOneWidget);
    expect(t.widget<OutlinedButton>(find.widgetWithText(OutlinedButton, 'Reinstall…')).onPressed, isNull);
    expect(t.widget<FilledButton>(find.widgetWithText(FilledButton, 'Update now')).onPressed, isNotNull);
  });
}
