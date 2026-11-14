// The Players tab, profile and bans against a fake API: who sees which buttons, and what kick/ban send.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/players.dart';
import 'package:hostspawn/theme.dart';

const key = 'steam:76561190000000001'; // made-up (test value)

class FakeApi extends Api {
  FakeApi() : super(Uri.parse('http://127.0.0.1:9'));
  final sent = <Map<String, dynamic>>[];
  bool banned = false;

  @override
  Future<List<dynamic>> onlinePlayers([String? server]) async => [
        {'key': key, 'name': 'Alice', 'ping': 30, 'since': 1790000000, 'playtime_s': 7200, 'server': server},
      ];

  @override
  Future<Map<String, dynamic>> player(String k) async => {
        'key': k,
        'name': 'Alice',
        'playtime_s': 7200,
        'sessions': 3,
        'first_seen': 1780000000,
        'last_seen': 1790000000,
        'online': [],
        'names': [
          {'name': 'Alice', 'last_seen': 1790000000}
        ],
        'history': [],
        'notes': [],
        'bans': [],
        'ban': banned ? {'by': 'owner', 'reason': 'cheating', 'until': null} : null,
        'watch': null,
      };

  @override
  Future<String> playerAction(String action,
      {String? key, String? server, String reason = '', int? minutes, String? text}) async {
    sent.add({'action': action, 'key': key, 'server': server, 'reason': reason, 'minutes': minutes, 'text': text});
    if (action == 'ban') banned = true;
    if (action == 'unban') banned = false;
    return 'Done';
  }
}

Map<String, dynamic> server({bool supported = true, bool tracked = true, bool canMod = true}) => {
      'id': 'r1',
      'name': 'My Rust',
      'players_supported': supported,
      'players_tracked': tracked,
      'can_moderate': canMod,
    };

Future<void> show(WidgetTester t, Widget w) async {
  await t.binding.setSurfaceSize(const Size(1000, 1200));
  await t.pumpWidget(MaterialApp(theme: hostspawnTheme(), home: Scaffold(body: w)));
  await t.pumpAndSettle();
}

void main() {
  final mod = Me('mod', 'moderator', null), viewer = Me('v', 'viewer', null);

  testWidgets('moderator kicks with a reason', (t) async {
    final api = FakeApi();
    await show(t, PlayersTab(api: api, me: mod, s: server()));
    expect(find.text('1 online'), findsOneWidget);
    expect(find.text('Alice'), findsOneWidget);
    await t.tap(find.text('Kick'));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'afk');
    await t.pump();
    await t.tap(find.widgetWithText(FilledButton, 'Kick'));
    await t.pumpAndSettle();
    expect(api.sent.single,
        {'action': 'kick', 'key': key, 'server': 'r1', 'reason': 'afk', 'minutes': null, 'text': null});
  });

  testWidgets('ban needs a reason; length picked (default 1 day, or permanent)', (t) async {
    final api = FakeApi();
    await show(t, PlayersTab(api: api, me: mod, s: server()));
    await t.tap(find.text('Ban'));
    await t.pumpAndSettle();
    final ban = find.widgetWithText(FilledButton, 'Ban');
    expect(t.widget<FilledButton>(ban).onPressed, isNull);
    await t.tap(find.text('Permanent'));
    await t.enterText(find.byType(TextField), 'cheating');
    await t.pump();
    await t.tap(ban);
    await t.pumpAndSettle();
    expect((api.sent.single['action'], api.sent.single['minutes'], api.sent.single['reason']), ('ban', 0, 'cheating'));
  });

  testWidgets('viewers see players but no Kick or Ban', (t) async {
    await show(t, PlayersTab(api: FakeApi(), me: viewer, s: server()));
    expect(find.text('Alice'), findsOneWidget);
    expect(find.text('Kick'), findsNothing);
    expect(find.text('Ban'), findsNothing);
  });

  testWidgets('a server HostSpawn only watches explains why there are no players', (t) async {
    await show(t, PlayersTab(api: FakeApi(), me: mod, s: server(tracked: false, canMod: false)));
    expect(find.textContaining('only watches this instance'), findsOneWidget);
  });

  testWidgets('profile: ban on every server, then unban', (t) async {
    final api = FakeApi();
    await show(t, ProfileScreen(api: api, me: mod, playerKey: key));
    expect(find.text('2.0 h'), findsOneWidget);
    await t.tap(find.text('Ban on every instance'));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'cheating');
    await t.pump();
    await t.tap(find.widgetWithText(FilledButton, 'Ban'));
    await t.pumpAndSettle();
    expect(find.text('Banned'), findsOneWidget);
    await t.tap(find.text('Unban'));
    await t.pumpAndSettle();
    expect(api.sent.map((s) => s['action']), ['ban', 'unban']);
    expect(find.text('Ban on every instance'), findsOneWidget);
  });
}
