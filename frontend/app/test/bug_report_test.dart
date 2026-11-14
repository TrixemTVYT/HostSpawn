// Report a bug: the copied details hold only safe things (version, system, game, type, state), never instance
// names, folders, addresses or players.
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/screens/bug_report.dart';

void main() {
  test('details are safe to post publicly', () {
    final d = bugDetails('0.9.0', [
      {
        'id': 'rust-vanilla-2521',
        'name': 'My Secret Clan Server',
        'game': 'Rust',
        'mode_name': 'Rust',
        'folder': '/home/someone/hostspawn/servers/rust/x',
        'ports': {'port': 28315},
        'status': {
          'state': 'running',
          'flags': ['Last update failed'],
          'players': 3
        },
        'host': '203.0.113.10',
      },
    ]);
    expect(d, contains('HostSpawn app: 0.9.0'));
    expect(d, contains('- Rust · Rust · running · 1 warning(s)'));
    for (final secret in ['My Secret Clan Server', '/home/someone', '203.0.113.10', '28315', 'rust-vanilla-2521']) {
      expect(d.contains(secret), isFalse, reason: secret);
    }
    expect(bugFormUrl, contains('github.com/TrixemTVYT/HostSpawn/issues/new'));
  });
}
