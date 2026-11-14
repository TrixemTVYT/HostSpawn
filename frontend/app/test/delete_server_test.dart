// Delete server (Settings › Delete on the server panel): only for servers HostSpawn created, only admins, and only
// after typing the name.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/server.dart';
import 'package:hostspawn/theme.dart';

class FakeApi extends Api {
  FakeApi({required this.deletable}) : super(Uri.parse('http://127.0.0.1:9'));
  final bool deletable;
  final deleted = <(String, String, bool)>[];

  @override
  Future<Map<String, dynamic>> server(String id) async => {
        'id': id,
        'name': 'My Rust',
        'game': 'rust',
        'actions': <String>[],
        'confirm': <String>[],
        'controllable': true,
        'deletable': deletable,
        'status': {'state': 'running'},
        'checks': {},
      };

  @override
  Future<String> deleteServer(String server, String confirm, {bool keepBackups = true}) async {
    deleted.add((server, confirm, keepBackups));
    return 'deleted';
  }
}

Future<void> open(WidgetTester t, Api api, Me me) async {
  await t.binding.setSurfaceSize(const Size(1000, 1400));
  await t.pumpWidget(MaterialApp(
    theme: hostspawnTheme(),
    home: Builder(
      builder: (c) => Scaffold(
        body: TextButton(
          onPressed: () =>
              Navigator.push(c, MaterialPageRoute(builder: (_) => ServerScreen(api: api, me: me, id: 'rust-1'))),
          child: const Text('open'),
        ),
      ),
    ),
  ));
  await t.tap(find.text('open'));
  await t.pumpAndSettle();
  await t.tap(find.widgetWithText(Tab, 'Settings'));
  await t.pumpAndSettle();
  if (me.isAdmin) {
    await t.tap(find.text('Delete'));
    await t.pumpAndSettle();
  }
}

void main() {
  final admin = Me('owner', 'admin', null), viewer = Me('v', 'viewer', null);

  testWidgets('admin deletes a HostSpawn server after typing its name; backups kept by default', (t) async {
    final api = FakeApi(deletable: true);
    await open(t, api, admin);
    await t.tap(find.text('Delete…'));
    await t.pumpAndSettle();
    final button = find.widgetWithText(FilledButton, 'Delete instance');
    expect(t.widget<FilledButton>(button).onPressed, isNull);
    await t.enterText(find.byType(TextField), 'my rust'); // must match exactly
    await t.pump();
    expect(t.widget<FilledButton>(button).onPressed, isNull);
    await t.enterText(find.byType(TextField), 'My Rust');
    await t.pump();
    await t.tap(button);
    await t.pumpAndSettle();
    expect(api.deleted, [('rust-1', 'My Rust', true)]);
    expect(find.byType(ServerScreen), findsNothing); // back to the dashboard
    expect(find.text('My Rust: deleted'), findsOneWidget);
  });

  testWidgets('untick Keep its backups; Cancel deletes nothing', (t) async {
    final api = FakeApi(deletable: true);
    await open(t, api, admin);
    await t.tap(find.text('Delete…'));
    await t.pumpAndSettle();
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    expect(api.deleted, isEmpty);
    await t.tap(find.text('Delete…'));
    await t.pumpAndSettle();
    await t.tap(find.text('Keep its backups'));
    await t.enterText(find.byType(TextField), 'My Rust');
    await t.pump();
    await t.tap(find.widgetWithText(FilledButton, 'Delete instance'));
    await t.pumpAndSettle();
    expect(api.deleted, [('rust-1', 'My Rust', false)]);
  });

  testWidgets('a server HostSpawn did not create explains why it has no Delete button', (t) async {
    await open(t, FakeApi(deletable: false), admin);
    expect(find.text('Delete…'), findsNothing);
    expect(find.textContaining("HostSpawn didn't create this instance"), findsOneWidget);
  });

  testWidgets('viewers do not see Delete at all', (t) async {
    await open(t, FakeApi(deletable: true), viewer);
    expect(find.text('Delete'), findsNothing);
    expect(find.text('Delete instance'), findsNothing);
  });
}
