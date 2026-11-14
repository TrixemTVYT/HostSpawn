// Signed-in devices: see them, sign one out, or make a pairing code for a new one.
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'home.dart' show when;

class DevicesScreen extends StatefulWidget {
  const DevicesScreen({super.key, required this.api, required this.me});
  final Api api;
  final Me me;
  @override
  State<DevicesScreen> createState() => _DevicesScreenState();
}

class _DevicesScreenState extends State<DevicesScreen> {
  List<Map<String, dynamic>>? list;
  String? error;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final d = (await widget.api.devices()).cast<Map<String, dynamic>>();
      if (mounted) {
        setState(() {
          list = d;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> pair() async {
    try {
      final code = await widget.api.pairCode();
      if (!mounted) return;
      await showDialog(
        context: context,
        builder: (_) => AlertDialog(
          backgroundColor: C.bg2,
          title: const Text('Pair a device'),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            const Text('On the new device, open HostSpawn, choose "Use a pairing code" and type:',
                style: TextStyle(color: C.muted)),
            const SizedBox(height: 18),
            SelectableText(code,
                style: TextStyle(fontSize: 40, letterSpacing: 12, fontWeight: FontWeight.w700, color: C.accent)),
            const SizedBox(height: 12),
            Text('Works once, for 5 minutes. It signs in as ${widget.me.user} (${widget.me.role}).',
                style: const TextStyle(color: C.dim)),
          ]),
          actions: [FilledButton(onPressed: () => Navigator.pop(context), child: const Text('Done'))],
        ),
      );
      load();
    } on ApiError catch (e) {
      setState(() => error = e.message);
    }
  }

  Future<void> revoke(Map<String, dynamic> d) async {
    try {
      await widget.api.revokeDevice(d['id'] as String);
      load();
    } on ApiError catch (e) {
      setState(() => error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(backgroundColor: C.bg2, title: const Text('Devices')),
        floatingActionButton: FloatingActionButton.extended(
          heroTag: null, // each page's button animates on its own (two shared the default tag)
          onPressed: pair,
          backgroundColor: C.accent,
          foregroundColor: C.bg,
          icon: const Icon(Icons.add_link),
          label: const Text('Pair a device'),
        ),
        body: ListView(padding: const EdgeInsets.all(20), children: [
          if (error != null) Text(error!, style: const TextStyle(color: C.bad)),
          if (list == null) Center(child: CircularProgressIndicator(color: C.accent)),
          for (final d in list ?? <Map<String, dynamic>>[])
            Card(
              child: ListTile(
                leading: Icon(d['device'] == widget.me.device ? Icons.computer : Icons.devices, color: C.accent),
                title: Text('${d['device']}${d['device'] == widget.me.device ? '  (this app)' : ''}'),
                subtitle:
                    Text('${d['user']} · last used ${when(d['last_used'])}', style: const TextStyle(color: C.muted)),
                trailing: TextButton(
                    onPressed: () => revoke(d), child: const Text('Sign out', style: TextStyle(color: C.bad))),
              ),
            ),
          if (list != null && list!.isEmpty) const Text('No apps signed in.', style: TextStyle(color: C.muted)),
        ]),
      );
}
