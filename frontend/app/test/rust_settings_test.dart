// Settings tab, Rust: the Server browser section sends only what changed.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/server_tabs.dart';
import 'package:hostspawn/theme.dart';

class FakeApi extends Api {
  FakeApi() : super(Uri.parse('http://127.0.0.1:9'));
  final saved = <Map<String, dynamic>>[];
  @override
  Future<String> saveSettings(String server, Map<String, dynamic> changes) async {
    saved.add(changes);
    return 'Saved.';
  }
}

void main() {
  testWidgets('description, website and PvE are saved; untouched fields are not sent', (t) async {
    final api = FakeApi();
    await t.binding.setSurfaceSize(const Size(1000, 3000));
    await t.pumpWidget(MaterialApp(
        theme: hostspawnTheme(),
        home: Scaffold(
            body: SettingsTab(api: api, me: Me('owner', 'admin', null), saved: () {}, s: {
          'id': 'r1',
          'name': 'Riverside',
          'game': 'Rust',
          'managed': true,
          'can_say': false,
          'settings': {'max_players': 50, 'restart_times': [], 'global_chat': true},
          'wipes': {'mode': 'monthly', 'time': '20:00', 'bp': 'forced', 'new_seed': true, 'next': []},
        }))));
    await t.pumpAndSettle();
    expect(find.text('Server browser (Rust)'), findsOneWidget);
    await t.enterText(find.widgetWithText(TextField, 'Description'), 'Be nice. Active admins.');
    await t.enterText(find.widgetWithText(TextField, 'Website or Discord (https://…)'), 'https://riverside.example');
    await t.tap(find.text("PvE (players can't hurt each other)"));
    await t.pump();
    await t.tap(find.text('Save changes'));
    await t.pumpAndSettle();
    expect(
        api.saved.single, {'description': 'Be nice. Active admins.', 'url': 'https://riverside.example', 'pve': true});
  });
}
