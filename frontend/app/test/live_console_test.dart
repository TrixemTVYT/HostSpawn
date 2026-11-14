// The console is live: Start opens it and new lines keep arriving every 2 seconds; Pause stops following.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/server.dart';
import 'package:hostspawn/theme.dart';

class FakeApi extends Api {
  FakeApi() : super(Uri.parse('http://127.0.0.1:9'));
  int fetched = 0;
  final done = <String>[];
  @override
  Future<Map<String, dynamic>> server(String id) async => {
        'id': id,
        'name': 'Riverside',
        'game': 'Rust',
        'actions': ['start', 'stop'],
        'confirm': <String>[],
        'controllable': true,
        'status': {'state': 'stopped'},
        'checks': {},
      };
  @override
  Future<Map<String, dynamic>> logs(String id) async {
    fetched++;
    return {
      'console': [for (var i = 1; i <= fetched; i++) 'boot step $i'].join('\n')
    };
  }

  @override
  Future<String> action(String server, String action, {String arg = '', String? confirm}) async {
    done.add(action);
    return 'started';
  }
}

void main() {
  testWidgets('Start opens the live console and it follows new lines', (t) async {
    final api = FakeApi();
    await t.binding.setSurfaceSize(const Size(1200, 900));
    await t.pumpWidget(
        MaterialApp(theme: hostspawnTheme(), home: ServerScreen(api: api, me: Me('owner', 'admin', null), id: 'r1')));
    await t.pump(const Duration(milliseconds: 500));
    await t.tap(find.text('Start'));
    await t.pumpAndSettle(); // the console tab slides over and its first lines arrive
    expect(api.done, ['start']);
    expect(find.text('Live'), findsOneWidget);
    await t.pump(const Duration(seconds: 2));
    await t.pump(const Duration(seconds: 2));
    expect(find.textContaining('boot step 3'), findsOneWidget); // fetched again without pressing anything
    await t.tap(find.text('Live'));
    await t.pump();
    final before = api.fetched;
    await t.pump(const Duration(seconds: 5));
    expect(api.fetched, before); // paused: no more fetching
    expect(find.text('Paused'), findsOneWidget);
    await t.pumpWidget(const SizedBox()); // stop the timers
  });
}
