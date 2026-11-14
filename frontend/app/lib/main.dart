// HostSpawn app. Starts by finding the agent, then shows first-run setup, sign-in or the dashboard.
import 'dart:io';

import 'package:flutter/material.dart';

import 'api.dart';
import 'screens/home.dart';
import 'screens/sign_in.dart';
import 'store.dart';
import 'theme.dart';
import 'tunnel.dart';

void main() => runApp(HostSpawnApp(saved: Saved(const SystemKeyStore())));

String deviceName() {
  final os = Platform.isWindows
      ? 'Windows'
      : Platform.isMacOS
          ? 'Mac'
          : Platform.isLinux
              ? 'Linux'
              : Platform.operatingSystem;
  final host = Platform.localHostname.replaceAll(RegExp(r'[\x00-\x1f]'), '');
  final name = 'HostSpawn app on $host ($os)';
  return name.length > 40 ? name.substring(0, 40) : name;
}

class HostSpawnApp extends StatefulWidget {
  const HostSpawnApp({super.key, required this.saved});
  final Saved saved;
  @override
  State<HostSpawnApp> createState() => _HostSpawnAppState();
}

class _HostSpawnAppState extends State<HostSpawnApp> {
  @override
  void initState() {
    super.initState();
    themeNotifier.addListener(_changed);
    widget.saved.theme().then((id) {
      if (id != null) themeNotifier.value = themeById(id);
    });
  }

  @override
  void dispose() {
    themeNotifier.removeListener(_changed);
    super.dispose();
  }

  void _changed() {
    widget.saved.setTheme(themeNotifier.value.id);
    setState(() {}); // every screen reads the theme colours when it builds
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'HostSpawn',
        debugShowCheckedModeBanner: false,
        theme: hostspawnTheme(),
        home: Boot(saved: widget.saved),
      );
}

/// Decides the first screen.
class Boot extends StatefulWidget {
  const Boot({super.key, required this.saved});
  final Saved saved;
  @override
  State<Boot> createState() => _BootState();
}

class _BootState extends State<Boot> {
  Api? api;
  AgentInfo? info;
  Me? me;
  String? error;
  bool busy = true;

  @override
  void initState() {
    super.initState();
    start();
  }

  Future<void> start() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final url = Uri.parse(await widget.saved.agent());
      api?.close();
      api = Api(url, token: await widget.saved.token());
      info = await api!.info();
      me = null;
      if (!info!.setupNeeded && api!.token != null) {
        try {
          me = await api!.me();
        } on ApiError catch (e) {
          if (!e.signedOut) rethrow;
          await widget.saved.setToken(null); // the key was signed out or expired: sign in again
          api!.token = null;
        }
      }
    } on SocketException {
      error = "HostSpawn's agent isn't answering at ${await widget.saved.agent()}. Is it running?";
    } on ApiError catch (e) {
      error = e.message;
    } catch (e) {
      error = 'Something went wrong: $e';
    }
    if (mounted) setState(() => busy = false);
  }

  Future<void> signedIn(Me who) async {
    await widget.saved.setToken(api!.token);
    await widget.saved.setLastUser(who.user);
    setState(() => me = who);
  }

  Future<void> signOut() async {
    try {
      await api!.logout();
    } catch (_) {}
    await widget.saved.setToken(null);
    setState(() => me = null);
    start();
  }

  @override
  Widget build(BuildContext context) {
    if (busy) {
      return Scaffold(
          body: Center(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
        Logo(size: 64),
        SizedBox(height: 22),
        SizedBox(width: 160, child: LinearProgressIndicator(color: C.accent)),
      ])));
    }
    if (error != null) {
      return ConnectScreen(saved: widget.saved, error: error!, retry: start);
    }
    if (me != null) {
      return HomeScreen(api: api!, me: me!, version: info!.version, signOut: signOut);
    }
    return SignInScreen(
        api: api!, setupNeeded: info!.setupNeeded, saved: widget.saved, device: deviceName(), done: signedIn);
  }
}

/// Shown when the agent can't be reached: change its address or try again.
class ConnectScreen extends StatefulWidget {
  const ConnectScreen({super.key, required this.saved, required this.error, required this.retry});
  final Saved saved;
  final String error;
  final Future<void> Function() retry;
  @override
  State<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends State<ConnectScreen> {
  final url = TextEditingController();
  final host = TextEditingController(), user = TextEditingController(text: 'ubuntu');
  final sshPort = TextEditingController(text: '22'), port = TextEditingController(text: '8100');
  String? problem, serverProblem, waiting;
  bool showAddress = false;

  @override
  void initState() {
    super.initState();
    widget.saved.agent().then((v) => url.text = v);
    widget.saved.server().then((v) {
      final s = Server.load(v);
      if (s != null && mounted) {
        setState(() {
          host.text = s.host;
          user.text = s.user;
          sshPort.text = '${s.sshPort}';
          port.text = '${s.port}';
        });
      }
    });
  }

  Server get server => Server(user.text.trim(), host.text.trim(),
      sshPort: int.tryParse(sshPort.text.trim()) ?? 0, port: int.tryParse(port.text.trim()) ?? 0);

  /// Opens the SSH tunnel in its own window, then keeps trying HostSpawn through it for up to 2 minutes.
  Future<void> connectServer() async {
    final s = server;
    final p = s.problem();
    setState(() => serverProblem = p);
    if (p != null) return;
    await widget.saved.setServer(s.save());
    await widget.saved.setAgent('http://127.0.0.1:${Server.localPort}');
    final opened = await openTunnel(s);
    setState(() => waiting = opened
        ? 'A "HostSpawn connection" window opened: type your server password there (the first time, type yes to '
            'trust the server). Keep that window open while you use HostSpawn (you can minimise it).'
        : 'Run this in a terminal, then press Try again:\n${s.command()}');
    for (var i = 0; i < 40 && mounted; i++) {
      await Future.delayed(const Duration(seconds: 3));
      if (!mounted) return;
      await widget.retry(); // leaves this screen as soon as HostSpawn answers
    }
  }

  Future<void> save() async {
    final u = Uri.tryParse(url.text.trim());
    if (u == null || !(u.scheme == 'http' || u.scheme == 'https') || u.host.isEmpty) {
      setState(() => problem = 'Use an address like http://127.0.0.1:8100');
      return;
    }
    await widget.saved.setAgent(u.toString().replaceAll(RegExp(r'/+$'), ''));
    await widget.retry();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        body: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: Entrance(
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(28),
                  child:
                      Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Row(children: [
                      Logo(size: 34),
                      SizedBox(width: 12),
                      Text('HostSpawn', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700))
                    ]),
                    const SizedBox(height: 18),
                    Text(widget.error, style: const TextStyle(color: C.warn)),
                    const SizedBox(height: 18),
                    const Text('Connect to my server', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 4),
                    const Text(
                        'HostSpawn runs on your Linux server. Enter how you log in to it (the same as SSH or PuTTY). '
                        'The connection is encrypted and nothing is opened to the internet.',
                        style: TextStyle(color: C.muted, fontSize: 13)),
                    const SizedBox(height: 12),
                    TextField(
                        controller: host,
                        decoration: InputDecoration(
                            labelText: 'Server address',
                            hintText: 'myserver.example.com or 203.0.113.10',
                            errorText: serverProblem)),
                    const SizedBox(height: 10),
                    Row(children: [
                      Expanded(
                          flex: 3,
                          child: TextField(controller: user, decoration: const InputDecoration(labelText: 'Username'))),
                      const SizedBox(width: 10),
                      Expanded(
                          child:
                              TextField(controller: sshPort, decoration: const InputDecoration(labelText: 'SSH port'))),
                      const SizedBox(width: 10),
                      Expanded(
                          child: TextField(
                              controller: port, decoration: const InputDecoration(labelText: 'HostSpawn port'))),
                    ]),
                    const SizedBox(height: 14),
                    FilledButton.icon(
                        onPressed: connectServer,
                        icon: const Icon(Icons.lan_outlined, size: 18),
                        label: const Text('Connect to my server')),
                    if (waiting != null) ...[
                      const SizedBox(height: 12),
                      SelectableText(waiting!, style: const TextStyle(color: C.muted, fontSize: 13)),
                      const SizedBox(height: 6),
                      TextButton(onPressed: widget.retry, child: const Text('Try again')),
                    ],
                    const SizedBox(height: 10),
                    TextButton(
                        onPressed: () => setState(() => showAddress = !showAddress),
                        child: Text(showAddress ? 'Hide advanced' : 'Advanced: type HostSpawn\'s address')),
                    if (showAddress) ...[
                      TextField(
                          controller: url,
                          decoration: InputDecoration(labelText: 'HostSpawn address', errorText: problem)),
                      const SizedBox(height: 12),
                      FilledButton(onPressed: save, child: const Text('Connect')),
                    ],
                  ]),
                ),
              ),
            ),
          ),
        ),
      );
}
