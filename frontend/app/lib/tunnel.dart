// Connecting to HostSpawn on a server (from Windows, or any other computer) through an SSH tunnel. HostSpawn only
// listens on the server's own 127.0.0.1, so nothing is opened to the internet: ssh (built into Windows 10 and 11)
// carries the connection, and asks for the server password (or uses your SSH key) in its own small window.
import 'dart:io';

/// Where HostSpawn runs: user@host, the SSH port and HostSpawn's port on the server.
class Server {
  const Server(this.user, this.host, {this.sshPort = 22, this.port = 8100});
  final String user, host;
  final int sshPort, port;

  static final _host = RegExp(r'^[A-Za-z0-9]([A-Za-z0-9.-]{0,252})$|^\[?[0-9A-Fa-f:]{2,45}\]?$');
  static final _user = RegExp(r'^[a-z_][a-z0-9_.-]{0,31}$');

  /// A plain reason if something isn't right, or null. (Strict, because the values go on a command line.)
  String? problem() {
    if (!_host.hasMatch(host)) {
      return 'Server address: a name like myserver.example.com or an address like 203.0.113.10';
    }
    if (!_user.hasMatch(user)) return 'Username: the one you log in to the server with, for example ubuntu';
    if (sshPort < 1 || sshPort > 65535 || port < 1 || port > 65535) return 'Ports are numbers from 1 to 65535';
    return null;
  }

  /// The local port the tunnel uses on this computer (HostSpawn's usual one).
  static const localPort = 8100;

  List<String> sshArgs() => [
        '-N', // just the tunnel, no shell
        '-o', 'ServerAliveInterval=30',
        '-o', 'ExitOnForwardFailure=yes',
        '-L', '$localPort:127.0.0.1:$port',
        '-p', '$sshPort',
        '$user@$host',
      ];

  /// The same command to type by hand.
  String command() => 'ssh ${sshArgs().join(' ')}';

  String save() => '$user@$host:$sshPort:$port';

  static Server? load(String? s) {
    final m = RegExp(r'^(.+)@(.+):(\d+):(\d+)$').firstMatch(s ?? '');
    if (m == null) return null;
    return Server(m.group(1)!, m.group(2)!, sshPort: int.parse(m.group(3)!), port: int.parse(m.group(4)!));
  }
}

/// Opens the tunnel in its own window (it asks for the password there). Returns false if no window could be opened:
/// then show Server.command() to run by hand.
Future<bool> openTunnel(Server s) async {
  if (s.problem() != null) return false;
  try {
    if (Platform.isWindows) {
      // start "title" cmd /k ssh ...: a console window that stays open while connected (closing it disconnects)
      await Process.start('cmd', ['/c', 'start', 'HostSpawn connection', 'cmd', '/k', 'ssh', ...s.sshArgs()],
          mode: ProcessStartMode.detached);
      return true;
    }
    if (Platform.isLinux) {
      await Process.start('x-terminal-emulator', ['-e', 'ssh', ...s.sshArgs()], mode: ProcessStartMode.detached);
      return true;
    }
  } on ProcessException {
    return false;
  }
  return false;
}
