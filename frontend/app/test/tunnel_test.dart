// Connect to my server (SSH tunnel): the command, saving, and refusing anything that could sneak extra commands in
// (the values go on a command line on Windows).
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/tunnel.dart';

void main() {
  test('the tunnel command', () {
    const s = Server('ubuntu', 'myserver.example.com', sshPort: 2222, port: 8102);
    expect(s.problem(), isNull);
    expect(s.command(),
        'ssh -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -L 8100:127.0.0.1:8102 -p 2222 ubuntu@myserver.example.com');
  });

  test('saved and loaded', () {
    const s = Server('ubuntu', '203.0.113.10', port: 8100);
    final back = Server.load(s.save())!;
    expect((back.user, back.host, back.sshPort, back.port), ('ubuntu', '203.0.113.10', 22, 8100));
    expect(Server.load('nonsense'), isNull);
    expect(Server.load(null), isNull);
  });

  test('anything that could run another command is refused', () {
    for (final host in [
      'a & calc',
      'x;rm -rf /',
      'host|evil',
      '"quoted"',
      'a b',
      '',
      '-oProxyCommand=evil',
      '%PATH%'
    ]) {
      expect(Server('ubuntu', host).problem(), isNotNull, reason: host);
    }
    for (final user in ['root&calc', 'Ubuntu', '-o', 'a b', '', 'x|y']) {
      expect(Server(user, 'host').problem(), isNotNull, reason: user);
    }
    expect(const Server('ubuntu', 'host', sshPort: 0).problem(), isNotNull);
    expect(const Server('ubuntu', 'host', port: 70000).problem(), isNotNull);
    expect(const Server('ubuntu', '2001:db8::1').problem(), isNull); // IPv6 is fine
  });
}
