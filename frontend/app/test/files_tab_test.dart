// Files tab: open a file, read-only generated files, a refused save shows the reason and a Go to line button,
// mistakes are found while typing, versions, and Arma 3 mission packing.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/files_tab.dart';
import 'package:hostspawn/theme.dart';

class FakeApi extends Api {
  FakeApi() : super(Uri.parse('http://127.0.0.1:9'));
  final calls = <String>[], dirs = <String>[];
  String? refuse;
  @override
  Future<Map<String, dynamic>> files(String server) async => {
        'files': [
          {'path': 'server/oxide/config/Kits.json', 'read_only': false},
          {'path': 'server/server/server/cfg/server.cfg', 'read_only': true},
        ],
        'missions': [
          {'folder': 'Training.Stratis', 'packed': false, 'packed_at': null}
        ],
      };
  // The folder view: starts in the game server's folder.
  @override
  Future<Map<String, dynamic>> filesDir(String server, String? path) async {
    dirs.add(path ?? '(start)');
    if (path == 'server/@extdb3') {
      return {
        'dir': 'server/@extdb3',
        'up': 'server',
        'start': 'server',
        'entries': [
          {
            'name': 'extdb3-conf.ini',
            'path': 'server/@extdb3/extdb3-conf.ini',
            'dir': false,
            'size': 900,
            'editable': true
          },
        ],
      };
    }
    return {
      'dir': 'server',
      'up': '',
      'start': 'server',
      'entries': [
        {'name': '@extdb3', 'path': 'server/@extdb3', 'dir': true, 'size': 0},
        {'name': 'mpmissions', 'path': 'server/mpmissions', 'dir': true, 'size': 0},
        {'name': 'Kits.json', 'path': 'server/oxide/config/Kits.json', 'dir': false, 'size': 9, 'editable': true},
        {
          'name': 'server.cfg',
          'path': 'server/server/server/cfg/server.cfg',
          'dir': false,
          'size': 20,
          'editable': true,
          'read_only': true
        },
        {'name': 'arma3server_x64', 'path': 'server/arma3server_x64', 'dir': false, 'size': 52428800},
      ],
    };
  }

  @override
  Future<Map<String, dynamic>> file(String server, String path) async => {
        'path': path,
        'text': path.endsWith('.json') ? '{"a": 1}\n' : 'server.hostname "x"\n',
        'sha': 'abc',
        'read_only': path.endsWith('.cfg'),
        'versions': [
          {'id': '1.000', 'at': 1700000000, 'by': 'owner', 'size': 9}
        ],
      };
  @override
  Future<Map<String, dynamic>> saveFile(String server, String path, String text, String sha) async {
    calls.add('save $path $sha');
    if (refuse != null) throw ApiError(400, refuse!);
    return {'path': path, 'text': text, 'sha': 'def', 'read_only': false, 'versions': []};
  }

  @override
  Future<String> packMission(String server, String folder) async {
    calls.add('pack $folder');
    return 'Packed 2 files into $folder.pbo.';
  }
}

Future<FakeApi> show(WidgetTester t) async {
  final api = FakeApi();
  await t.binding.setSurfaceSize(const Size(1400, 900));
  await t.pumpWidget(MaterialApp(
      theme: hostspawnTheme(),
      home: Scaffold(body: FilesTab(api: api, s: {'id': 'a1', 'game': 'Arma 3', 'name': 'Altis'}))));
  await t.pumpAndSettle();
  return api;
}

void main() {
  testWidgets('open a file; generated files are read-only', (t) async {
    await show(t);
    expect(find.text('Pick a file on the left to open it.'), findsOneWidget);
    await t.tap(find.text('Kits.json'));
    await t.pumpAndSettle();
    expect(find.text('server/oxide/config/Kits.json'), findsOneWidget);
    expect(find.text('Versions (1)'), findsOneWidget);
    await t.tap(find.text('server.cfg'));
    await t.pumpAndSettle();
    expect(find.textContaining('read-only here'), findsOneWidget);
  });

  testWidgets('browse the server folder like a file manager', (t) async {
    final api = await show(t);
    expect(find.text('@extdb3'), findsOneWidget);
    expect(find.text('mpmissions'), findsOneWidget);
    expect(find.text('50.0 MB'), findsOneWidget);
    await t.tap(find.text('arma3server_x64'));
    await t.pump();
    expect(find.textContaining('isn\'t a text file'), findsOneWidget);
    await t.tap(find.text('@extdb3'));
    await t.pumpAndSettle();
    expect(find.text('server/@extdb3'), findsOneWidget);
    expect(find.text('.. (up one folder)'), findsOneWidget);
    await t.tap(find.text('extdb3-conf.ini'));
    await t.pumpAndSettle();
    expect(find.text('server/@extdb3/extdb3-conf.ini'), findsOneWidget);
    await t.tap(find.byTooltip('Back to the server folder'));
    await t.pumpAndSettle();
    expect(api.dirs, ['(start)', 'server/@extdb3', 'server']);
  });

  testWidgets('pack a mission', (t) async {
    final api = await show(t);
    expect(find.text('Missions (mpmissions)'), findsOneWidget);
    await t.tap(find.text('Pack to PBO'));
    await t.pumpAndSettle();
    expect(api.calls, ['pack Training.Stratis']);
    expect(find.textContaining('Packed 2 files'), findsOneWidget);
  });

  testWidgets('new file from a template is saved where you say', (t) async {
    final api = await show(t);
    await t.tap(find.byTooltip('New file from a template'));
    await t.pumpAndSettle();
    await t.tap(find.textContaining('initServer.sqf'));
    await t.pump();
    await t.tap(find.widgetWithText(FilledButton, 'Create'));
    await t.pumpAndSettle();
    expect(api.calls, ['save server/mpmissions/MyMission.Stratis/initServer.sqf ']);
  });

  test('mistakes found while typing', () {
    expect(problemIn('a.json', '{"a": 1,}'), contains('Not valid JSON'));
    expect(problemIn('a.json', '{"a": 1}'), isNull);
    expect(problemIn('init.sqf', 'if (a) then {\n hint "x";\n'), contains('never closed'));
    expect(problemIn('init.sqf', 'x = "say ""hi"" (";'), isNull);
    expect(problemIn('notes.txt', '((('), isNull);
    expect(sqfProblem('f = { (a] };'), contains("doesn't match"));
  });
}
