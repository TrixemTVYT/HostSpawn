// Runs the release's own installer script (packaging/installer/install.sh) and turns its output into steps and
// results. The script does the real work and all the safety checks (checksums, HostSpawn's signature, the terms,
// never installing over someone else's folder); Setup is a friendly window around it.
import 'dart:convert';
import 'dart:io';

/// One line of the installer's output, sorted: a new step, a ✓, a ! or a ✗, or plain text.
enum Kind { step, ok, warn, fail, text }

class Line {
  const Line(this.kind, this.text, {this.step = 0});
  final Kind kind;
  final String text;
  final int step; // for Kind.step: 1–6
}

final _ansi = RegExp(r'\x1B\[[0-9;]*m');
final _step = RegExp(r'^\[(\d+)/6\]\s+(.*)$');

/// Sorts one raw output line ("[2/6] Checking the download", "  ✓ Signed by HostSpawn", ...).
Line parse(String raw) {
  final s = raw.replaceAll(_ansi, '').trimRight();
  final t = s.trimLeft();
  final m = _step.firstMatch(t);
  if (m != null) return Line(Kind.step, m.group(2)!, step: int.parse(m.group(1)!));
  if (t.startsWith('✓ ')) return Line(Kind.ok, t.substring(2));
  if (t.startsWith('! ')) return Line(Kind.warn, t.substring(2));
  if (t.startsWith('✗ ')) return Line(Kind.fail, t.substring(2));
  return Line(Kind.text, t);
}

/// Where the unpacked release is: $HOSTSPAWN_RELEASE_DIR, or the folder above Setup's own folder (`<release>/setup/`).
String releaseDir() {
  final env = Platform.environment['HOSTSPAWN_RELEASE_DIR'];
  if (env != null && env.isNotEmpty) return env;
  return File(Platform.resolvedExecutable).parent.parent.path;
}

/// What Setup asks the installer to do.
class Options {
  String folder = '${Platform.environment['HOME'] ?? ''}/.local/share/hostspawn';
  bool autostart = true, shortcut = true, packages = true;

  List<String> args() => [
    '--prefix',
    folder,
    if (!autostart) '--no-service',
    if (!shortcut) '--no-desktop',
    if (!packages) '--no-deps',
  ];
}

/// Runs install.sh. Swappable in tests.
class Installer {
  Installer(this.release);
  final String release;

  String get script => '$release/packaging/installer/install.sh';
  String get version {
    try {
      return File('$release/VERSION').readAsStringSync().trim();
    } catch (_) {
      return '';
    }
  }

  String get terms {
    try {
      return File('$release/docs/TERMS_DRAFT.md').readAsStringSync();
    } catch (_) {
      return 'The terms file is missing from this download. Please download HostSpawn Setup again.';
    }
  }

  /// Only checks (changes nothing): this machine, the download and its signature, the packages.
  Future<int> check(Options o, void Function(Line) onLine) => _run([...o.args(), '--check'], onLine);

  /// Installs. The terms were accepted in Setup's window; packages ask for the password with pkexec (a window).
  Future<int> install(Options o, void Function(Line) onLine) =>
      _run([...o.args(), '--yes', '--sudo', 'pkexec'], onLine);

  Future<int> _run(List<String> args, void Function(Line) onLine) async {
    final p = await Process.start('bash', [script, ...args], environment: {'TERM': 'dumb'});
    void lines(Stream<List<int>> s) =>
        s.transform(utf8.decoder).transform(const LineSplitter()).listen((l) => onLine(parse(l)));
    lines(p.stdout);
    lines(p.stderr);
    return p.exitCode;
  }

  /// Opens the installed HostSpawn app (the command install.sh printed as "OPEN: ...").
  Future<void> open(String command) async {
    await Process.start('sh', ['-c', '$command >/dev/null 2>&1 &'], mode: ProcessStartMode.detached);
  }
}
