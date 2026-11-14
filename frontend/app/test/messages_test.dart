// In-game messages in the Settings tab: countdown length and scheduled messages are sent only when changed.
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

Map<String, dynamic> server({bool canSay = true}) => {
      'id': 'r1',
      'name': 'My Rust',
      'managed': true,
      'can_say': canSay,
      'settings': {
        'max_players': 50,
        'restart_times': ['06:00'],
        'restart_warn_s': 300,
        'messages': [
          {'text': 'Rules: be nice', 'every_min': 30}
        ],
      },
    };

Future<void> show(WidgetTester t, Api api, Map<String, dynamic> s) async {
  await t.binding.setSurfaceSize(const Size(1000, 2400));
  await t.pumpWidget(MaterialApp(
      theme: hostspawnTheme(),
      home: Scaffold(body: SettingsTab(api: api, me: Me('owner', 'admin', null), s: s, saved: () {}))));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('add a repeating message, remove the old one, change the countdown, save', (t) async {
    final api = FakeApi();
    await show(t, api, server());
    expect(find.text('Rules: be nice'), findsOneWidget);
    expect(find.text('every 30 min'), findsOneWidget);
    await t.tap(find.text('Add a message'));
    await t.pumpAndSettle();
    await t.enterText(
        find.descendant(of: find.byType(AlertDialog), matching: find.byType(TextField)), 'Join our Discord');
    await t.pump();
    await t.tap(find.widgetWithText(FilledButton, 'Add'));
    await t.pumpAndSettle();
    await t.tap(find.byTooltip('Remove').first);
    await t.pump();
    await t.tap(find.text('5 minutes').first);
    await t.pumpAndSettle();
    await t.tap(find.text('10 minutes').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Save changes'));
    await t.pumpAndSettle();
    expect(api.saved.single, {
      'restart_warn_s': 600,
      'messages': [
        {'text': 'Join our Discord', 'every_min': 30}
      ],
    });
  });

  testWidgets('servers that cannot show messages have no messages section', (t) async {
    await show(t, FakeApi(), server(canSay: false));
    expect(find.text('In-game messages'), findsNothing);
  });

  testWidgets('a message at set times needs at least one time', (t) async {
    await show(t, FakeApi(), server());
    await t.tap(find.text('Add a message'));
    await t.pumpAndSettle();
    await t.enterText(find.descendant(of: find.byType(AlertDialog), matching: find.byType(TextField)), 'Wipe tonight');
    await t.tap(find.text('At set times'));
    await t.pumpAndSettle();
    expect(t.widget<FilledButton>(find.widgetWithText(FilledButton, 'Add')).onPressed, isNull);
  });
}
