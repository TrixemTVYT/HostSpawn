// The server panel's Installation tab: version and update check, a safe update, check and repair, reinstall keeping
// saves, clean up, disk space and the history of all of it (GET/POST /api/install).
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'players.dart' show toast;

class InstallTab extends StatefulWidget {
  const InstallTab({super.key, required this.api, required this.me, required this.s});
  final Api api;
  final Me me;
  final Map<String, dynamic> s;
  @override
  State<InstallTab> createState() => _InstallTabState();
}

class _InstallTabState extends State<InstallTab> {
  Map<String, dynamic>? info;
  String? error;
  Timer? timer;
  bool sending = false;

  String get sid => '${widget.s['id']}';
  Map<String, dynamic>? get job => (info?['job'] as Map?)?.cast<String, dynamic>();
  bool get busy => job?['state'] == 'running';

  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> load() async {
    try {
      final i = await widget.api.install(sid);
      if (!mounted) return;
      setState(() {
        info = i;
        error = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    }
    timer?.cancel();
    if (mounted && busy) timer = Timer(const Duration(milliseconds: 1500), load); // follow a running job
  }

  Future<void> act(String action, {String confirm = '', int warnS = 0}) async {
    setState(() => sending = true);
    try {
      await widget.api.installAction(sid, action, confirm: confirm, warnS: warnS);
      if (action == 'check' && mounted) toast(context, 'Asking Steam for the newest version…');
      await Future.delayed(Duration(milliseconds: action == 'check' ? 2500 : 300));
    } on ApiError catch (e) {
      if (mounted) toast(context, e.message, bad: true);
    }
    if (mounted) setState(() => sending = false);
    await load();
  }

  // ---- confirmations ----
  Future<void> askUpdate() async {
    final online = ((widget.s['status'] as Map?)?['players'] as num? ?? 0) > 0;
    var warn = online && widget.s['can_say'] == true;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, set) => AlertDialog(
          backgroundColor: C.bg2,
          title: const Text('Update now?'),
          content: SizedBox(
            width: 460,
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Text(
                  'HostSpawn backs up the settings and saves, stops the server, downloads the update, starts it and '
                  'checks it answers. If it doesn\'t come back, the settings and saves are put back automatically.',
                  style: TextStyle(color: C.muted, height: 1.4)),
              if (widget.s['can_say'] == true)
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: warn,
                  onChanged: (v) => set(() => warn = v),
                  title: const Text('Warn players first (5-minute countdown)'),
                ),
            ]),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Update now')),
          ],
        ),
      ),
    );
    if (ok == true) await act('update', warnS: warn ? 300 : 0);
  }

  Future<void> askSimple(String action, String title, String text, String button) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: C.bg2,
        title: Text(title),
        content: SizedBox(width: 460, child: Text(text, style: const TextStyle(color: C.muted, height: 1.4))),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: Text(button)),
        ],
      ),
    );
    if (ok == true) await act(action);
  }

  Future<void> askReinstall() async {
    final name = '${widget.s['name']}';
    final typed = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, set) => AlertDialog(
          backgroundColor: C.bg2,
          title: const Text('Reinstall the game files?'),
          content: SizedBox(
            width: 460,
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Text(
                  'The game files are deleted and downloaded again. Your saves and settings are kept (and backed up '
                  'first). The server is offline while it downloads.',
                  style: TextStyle(color: C.muted, height: 1.4)),
              const SizedBox(height: 14),
              Text('Type "$name" to confirm.', style: const TextStyle(color: C.muted)),
              const SizedBox(height: 8),
              TextField(controller: typed, autofocus: true, onChanged: (_) => set(() {})),
            ]),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
            FilledButton(
                onPressed: typed.text == name ? () => Navigator.pop(c, true) : null, child: const Text('Reinstall')),
          ],
        ),
      ),
    );
    if (ok == true) await act('reinstall', confirm: name);
  }

  // ---- view ----
  @override
  Widget build(BuildContext context) {
    if (error != null && info == null) {
      return Padding(
          padding: const EdgeInsets.all(24), child: Text(error!, style: const TextStyle(color: C.muted, height: 1.4)));
    }
    if (info == null) return Center(child: CircularProgressIndicator(color: C.accent));
    final i = info!;
    final sizes = (i['sizes'] as Map?) ?? const {};
    final history = ((i['history'] as List?) ?? const []).cast<Map>();
    final canRun = widget.me.canRun && widget.s['controllable'] == true;
    final admin = widget.me.isAdmin && widget.s['controllable'] == true;
    return RefreshIndicator(
      onRefresh: load,
      child: ListView(padding: const EdgeInsets.all(20), children: [
        if (job != null) _JobCard(job!),
        _versionCard(i, canRun),
        const SizedBox(height: 14),
        _card('Space used', [
          _row('Game files', size(sizes['game'])),
          _row('Saves and settings', size(sizes['saves'])),
          _row('Logs', size(sizes['logs'])),
          _row('Backups', size(sizes['backups'])),
          const Divider(),
          _row('Total', size(sizes['total']), bold: true),
          _row('Free on this disk', '${i['disk_free_gb']} GB'),
          const SizedBox(height: 8),
          SelectableText('${i['folder']}', style: const TextStyle(color: C.dim, fontSize: 12)),
        ]),
        const SizedBox(height: 14),
        _card('Maintenance', [
          _action(
              Icons.build_circle_outlined,
              'Check and repair',
              'Checks every game file with Steam, downloads any broken or missing ones (this also brings it up to '
                  'date), and checks HostSpawn\'s own setup: settings files, permissions and ports.',
              'Check and repair',
              admin && !busy
                  ? () => askSimple(
                      'repair',
                      'Check and repair?',
                      'The server is stopped while its files are checked (a few minutes), then started again. '
                          'This also installs the newest version.',
                      'Check and repair')
                  : null),
          _action(Icons.restart_alt, 'Reinstall', 'Fresh game files; your saves and settings are kept.', 'Reinstall…',
              admin && !busy ? askReinstall : null,
              danger: true),
          _action(
              Icons.cleaning_services_outlined,
              'Clean up',
              'Removes old logs, download leftovers and backups beyond the number to keep. The server keeps running.',
              'Clean up',
              admin && !busy
                  ? () => askSimple(
                      'cleanup',
                      'Clean up?',
                      'Old logs (the newest 5 always stay), SteamCMD download leftovers and backups beyond your '
                          '"backups to keep" number are deleted.',
                      'Clean up')
                  : null),
          if (!admin)
            const Padding(
              padding: EdgeInsets.only(top: 6),
              child:
                  Text('Only admins can repair, reinstall or clean up.', style: TextStyle(color: C.dim, fontSize: 12)),
            ),
        ]),
        const SizedBox(height: 14),
        _card('History', [
          if (history.isEmpty) const Text('Nothing yet.', style: TextStyle(color: C.muted)),
          for (final h in history)
            ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(h['result'] == 'ok' ? Icons.check_circle : Icons.error_outline,
                  color: h['result'] == 'ok' ? C.ok : (h['result'] == 'interrupted' ? C.warn : C.bad), size: 20),
              title: Text(
                  '${_opName(h['op'])} · ${_when(h['started'])}${h['by'] != null && h['by'] != '' ? ' · ${h['by']}' : ''}'),
              subtitle: Text('${h['summary'] ?? ''}', style: const TextStyle(color: C.muted)),
            ),
        ]),
      ]),
    );
  }

  Widget _versionCard(Map<String, dynamic> i, bool canRun) {
    final avail = i['update_available'];
    final (IconData icon, Color color, String title) = avail == true
        ? (Icons.system_update_alt, C.accent, 'Update available')
        : avail == false
            ? (Icons.verified_outlined, C.ok, 'Up to date')
            : (
                Icons.help_outline,
                C.muted,
                i['check_error'] != null ? 'Couldn\'t check for updates' : 'Not checked yet'
              );
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: glass(glow: avail == true ? C.accent : null),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(icon, color: color, size: 28),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: color)),
              Text(
                  [
                    '${i['game']}',
                    if (i['mode'] != null && i['mode'] != i['game']) '${i['mode']}',
                    if (i['framework'] != null && i['framework'] != 'vanilla') '${i['framework']}',
                  ].join(' · '),
                  style: const TextStyle(color: C.muted)),
            ]),
          ),
          if (canRun)
            TextButton.icon(
                onPressed: busy || sending ? null : () => act('check'),
                icon: const Icon(Icons.refresh, size: 18),
                label: const Text('Check now')),
          if (canRun) const SizedBox(width: 6),
          if (canRun)
            FilledButton.icon(
                onPressed: busy || sending || i['installed'] != true ? null : askUpdate,
                icon: const Icon(Icons.download, size: 18),
                label: const Text('Update now')),
        ]),
        const SizedBox(height: 12),
        Wrap(spacing: 28, runSpacing: 8, children: [
          _fact('Installed version', i['build'] == null ? 'unknown' : 'build ${i['build']}'),
          _fact('Newest on Steam', i['latest_build'] == null ? '–' : 'build ${i['latest_build']}'),
          _fact('Last updated', i['updated'] == null ? '–' : _when(i['updated'])),
          _fact('Last checked', i['checked'] == null ? 'not yet' : _when(i['checked'])),
        ]),
        if (i['check_error'] != null)
          Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text('${i['check_error']}', style: const TextStyle(color: C.warn, fontSize: 13))),
        if (avail == true && i['next_restart'] != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
                'Or leave it: it updates by itself at the next scheduled restart (${'${i['next_restart']}'.length >= 16 ? '${i['next_restart']}'.substring(11, 16) : i['next_restart']}).',
                style: const TextStyle(color: C.muted, fontSize: 13)),
          ),
        const Padding(
          padding: EdgeInsets.only(top: 8),
          child: Text(
              'Steam only offers the newest game files, so an update can\'t be undone. Settings and saves are '
              'backed up first and put back if the new version won\'t start.',
              style: TextStyle(color: C.dim, fontSize: 12)),
        ),
      ]),
    );
  }

  Widget _card(String title, List<Widget> children) => Container(
        padding: const EdgeInsets.all(18),
        decoration: glass(),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
          const SizedBox(height: 10),
          ...children,
        ]),
      );

  Widget _row(String a, String b, {bool bold = false}) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(children: [
          Expanded(child: Text(a, style: TextStyle(color: bold ? C.text : C.muted))),
          Text(b, style: TextStyle(fontWeight: bold ? FontWeight.w700 : FontWeight.w500)),
        ]),
      );

  Widget _fact(String label, String value) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: const TextStyle(color: C.dim, fontSize: 12)),
        Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
      ]);

  Widget _action(IconData icon, String title, String text, String button, VoidCallback? onTap, {bool danger = false}) =>
      Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(children: [
          Icon(icon, color: danger ? C.bad : C.accent),
          const SizedBox(width: 14),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
              Text(text, style: const TextStyle(color: C.muted, fontSize: 13)),
            ]),
          ),
          const SizedBox(width: 12),
          OutlinedButton(
              onPressed: onTap,
              style: danger ? OutlinedButton.styleFrom(foregroundColor: C.bad) : null,
              child: Text(button)),
        ]),
      );
}

class _JobCard extends StatelessWidget {
  const _JobCard(this.j);
  final Map<String, dynamic> j;
  @override
  Widget build(BuildContext context) {
    final running = j['state'] == 'running';
    final ok = j['result'] == 'ok';
    final color = running ? C.accent : (ok ? C.ok : C.bad);
    final log = ((j['log'] as List?) ?? const []).cast<String>();
    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.all(18),
      decoration: glass(glow: color),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(running ? Icons.sync : (ok ? Icons.check_circle : Icons.error_outline), color: color),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
                running
                    ? '${_opName(j['op'])}: ${j['step'] ?? ''}'
                    : '${_opName(j['op'])} ${ok ? 'finished' : 'failed'}',
                style: TextStyle(fontWeight: FontWeight.w700, color: color)),
          ),
        ]),
        if (running) ...[
          const SizedBox(height: 10),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
                value: j['progress'] is num ? (j['progress'] as num) / 100 : null,
                minHeight: 6,
                backgroundColor: C.line,
                valueColor: AlwaysStoppedAnimation(C.accent)),
          ),
        ],
        if (!running && '${j['summary'] ?? ''}'.isNotEmpty)
          Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text('${j['summary']}', style: const TextStyle(height: 1.4))),
        if (log.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: Text(log.reversed.take(running ? 4 : 8).toList().reversed.join('\n'),
                style: const TextStyle(color: C.dim, fontSize: 12, fontFamily: 'monospace', height: 1.4)),
          ),
      ]),
    );
  }
}

String _opName(dynamic op) => switch (op) {
      'update' => 'Update',
      'repair' => 'Check and repair',
      'reinstall' => 'Reinstall',
      'cleanup' => 'Clean up',
      _ => '$op',
    };

String _when(dynamic t) {
  if (t is! num) return '–';
  final d = DateTime.fromMillisecondsSinceEpoch((t * 1000).round());
  String two(int n) => n.toString().padLeft(2, '0');
  return '${d.day}/${d.month}/${d.year} ${two(d.hour)}:${two(d.minute)}';
}

/// Bytes as MB under a gigabyte, GB above.
String size(dynamic bytes) {
  if (bytes is! num) return '–';
  final gb = bytes / (1 << 30);
  return gb >= 1
      ? '${gb.toStringAsFixed(1)} GB'
      : '${(bytes / (1 << 20)).toStringAsFixed(bytes < 10 << 20 ? 1 : 0)} MB';
}
