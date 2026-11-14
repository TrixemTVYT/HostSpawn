// PC check screen: shows the machine, a verdict per game with reasons, the double-check advice and the disclaimer.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/pc_check.dart';
import 'package:hostspawn/theme.dart';

class FakeApi extends Api {
  FakeApi() : super(Uri.parse('http://127.0.0.1:9'));
  @override
  Future<Map<String, dynamic>> hostCheck() async => {
        'machine': {
          'os': 'Linux',
          'arch': 'x86_64',
          'cores': 4,
          'ram_total_gb': 10.0,
          'ram_free_gb': 8.0,
          'swap_gb': 0.0,
          'disk_free_gb': 90.0,
          'disk_total_gb': 250.0
        },
        'games': [
          {
            'id': 'reforger',
            'name': 'Arma Reforger',
            'verdict': 'ok',
            'summary': 'This machine can run it comfortably.',
            'reasons': ['4 CPU cores (recommended: 4).'],
            'needs': {
              'cores': {'min': 2, 'recommended': 4},
              'ram_gb': {'min': 4, 'recommended': 8},
              'disk_gb': 13
            }
          },
          {
            'id': 'rust',
            'name': 'Rust',
            'verdict': 'tight',
            'summary': 'This machine can run it, with the notes below.',
            'reasons': ['10 GB of memory: enough for a small server, 12 GB is recommended.'],
            'needs': {
              'cores': {'min': 2, 'recommended': 4},
              'ram_gb': {'min': 8, 'recommended': 12},
              'disk_gb': 20
            }
          },
        ],
        'notes': ['No swap space: add some.'],
        'advice': 'Please double-check before you start: look up the official server requirements.',
        'disclaimer': 'This check is a guide only. HostSpawn is free software provided "as is", without any warranty.',
      };
}

void main() {
  testWidgets('verdicts, reasons, advice and disclaimer', (t) async {
    await t.binding.setSurfaceSize(const Size(1000, 2000));
    await t.pumpWidget(MaterialApp(theme: hostspawnTheme(), home: PcCheckScreen(api: FakeApi())));
    await t.pumpAndSettle();
    expect(find.text('Arma Reforger Server'), findsOneWidget);
    expect(find.text('Good to go'), findsOneWidget);
    expect(find.text('Will run, with notes'), findsOneWidget);
    expect(find.textContaining('12 GB is recommended'), findsOneWidget);
    expect(find.textContaining('official server requirements'), findsOneWidget);
    expect(find.textContaining('without any warranty'), findsOneWidget);
    expect(find.text('10.0 GB'), findsOneWidget);
  });
}
