// HostSpawn Setup: the screens in order, terms must be accepted, a failed check blocks installing, the options reach
// the installer, the installer's output becomes steps and results, and Open HostSpawn runs the installed app.
// A fake installer is used: nothing is installed.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn_setup/installer.dart';
import 'package:hostspawn_setup/main.dart';

class FakeInstaller extends Installer {
  FakeInstaller({this.checkFails = false}) : super('/nowhere');
  final bool checkFails;
  final calls = <String>[];

  @override
  String get version => '0.9.0';
  @override
  String get terms => 'HostSpawn: Terms of Use (DRAFT)\n1. What HostSpawn does.';

  void say(void Function(Line) on, List<String> raw) => raw.map(parse).forEach(on);

  @override
  Future<int> check(Options o, void Function(Line) onLine) async {
    calls.add('check ${o.args().join(' ')}');
    say(onLine, [
      '\x1B[36m[1/6]\x1B[0m Checking this machine',
      '  ✓ Python 3.12',
      '[2/6] Checking the download',
      '  ✓ Signed by HostSpawn (key 1a2b3c4d-...)',
      if (checkFails) '  ✗ Python 3.11 or newer is needed (python3 --version).',
      '[4/6] Dependencies',
      '  ! lib32gcc-s1 is missing (SteamCMD (the game downloader) is 32-bit)',
    ]);
    return checkFails ? 1 : 0;
  }

  @override
  Future<int> install(Options o, void Function(Line) onLine) async {
    calls.add('install ${o.args().join(' ')}');
    say(onLine, [
      '[5/6] Installing',
      '  ✓ Installed to ${o.folder}',
      '[6/6] Starting',
      'OPEN: env HOSTSPAWN_AGENT=http://127.0.0.1:8102 ${o.folder}/app/hostspawn',
    ]);
    return 0;
  }

  @override
  Future<void> open(String command) async => calls.add('open $command');
}

Future<FakeInstaller> show(WidgetTester t, {bool checkFails = false}) async {
  final f = FakeInstaller(checkFails: checkFails);
  await t.binding.setSurfaceSize(const Size(900, 640));
  await t.pumpWidget(SetupApp(installer: f));
  await t.pumpAndSettle();
  return f;
}

Future<void> next(WidgetTester t, String label) async {
  // the button, not the step name on the left
  await t.tap(find.ancestor(of: find.text(label), matching: find.byWidgetPredicate((w) => w is ButtonStyleButton)));
  await t.pumpAndSettle();
}

void main() {
  test('output lines are sorted, colours removed', () {
    expect(parse('\x1B[1m[3/6]\x1B[0m Terms of use').step, 3);
    expect(parse('  ✓ All 412 files match').kind, Kind.ok);
    expect(parse('  ! Not installed').kind, Kind.warn);
    expect(parse('  ✗ Not installing.').text, 'Not installing.');
    expect(parse('    plain').kind, Kind.text);
  });

  testWidgets('the whole way through, with the chosen options', (t) async {
    final f = await show(t);
    expect(find.text('by TrixemTV · Setup · version 0.9.0'), findsOneWidget);
    expect(find.text('Is it safe?'), findsOneWidget);
    await next(t, 'Next');
    expect(find.textContaining('Terms of Use'), findsOneWidget);
    final accept = find.widgetWithText(FilledButton, 'I accept, next');
    expect(t.widget<FilledButton>(accept).onPressed, isNull); // can't go on without accepting
    await t.tap(find.text('I have read and accept the terms'));
    await t.pump();
    await next(t, 'I accept, next');
    await t.enterText(find.byType(TextField), '/home/me/hs');
    await t.tap(find.text('Start HostSpawn automatically')); // off
    await t.pump();
    await next(t, 'Check this computer');
    expect(find.text('Signed by HostSpawn (key 1a2b3c4d-...)'), findsOneWidget);
    expect(find.textContaining('Ready to install'), findsOneWidget);
    await next(t, 'Install');
    expect(find.text('HostSpawn is installed'), findsOneWidget);
    await next(t, 'Open HostSpawn');
    expect(f.calls, [
      'check --prefix /home/me/hs --no-service',
      'install --prefix /home/me/hs --no-service',
      'open env HOSTSPAWN_AGENT=http://127.0.0.1:8102 /home/me/hs/app/hostspawn',
    ]);
  });

  testWidgets('a failed check says why and blocks installing', (t) async {
    final f = await show(t, checkFails: true);
    await next(t, 'Next');
    await t.tap(find.text('I have read and accept the terms'));
    await t.pump();
    await next(t, 'I accept, next');
    await next(t, 'Check this computer');
    expect(find.textContaining('can\'t be installed yet: Python 3.11 or newer is needed'), findsOneWidget);
    expect(t.widget<FilledButton>(find.widgetWithText(FilledButton, 'Install')).onPressed, isNull);
    expect(f.calls.where((c) => c.startsWith('install')), isEmpty);
  });
}
