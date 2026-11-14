// A server being set up: every step live, download progress, and guided steps (things you do, e.g. open ports)
// with their commands and Continue / Back / Skip. Failed: Retry or Discard. Done: open the server panel.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../theme.dart';
import 'open_folder.dart';
import 'server.dart';

class SetupJobScreen extends StatefulWidget {
  const SetupJobScreen({super.key, required this.api, required this.me, required this.jobId});
  final Api api;
  final Me me;
  final String jobId;
  @override
  State<SetupJobScreen> createState() => _SetupJobScreenState();
}

class _SetupJobScreenState extends State<SetupJobScreen> {
  Map<String, dynamic>? job;
  String? error;
  bool busy = false;
  Timer? timer;

  @override
  void initState() {
    super.initState();
    load();
    timer = Timer.periodic(const Duration(seconds: 1), (_) => load());
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> load() async {
    try {
      final j = await widget.api.job(widget.jobId);
      if (mounted) {
        setState(() {
          job = j;
          error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } catch (e) {
      if (mounted) setState(() => error = 'Lost contact with the agent. Retrying… ($e)');
    }
  }

  Future<void> act(String action) async {
    if (action == 'cancel' || action == 'discard') {
      final ok = await showDialog<bool>(
        context: context,
        builder: (_) => AlertDialog(
          backgroundColor: C.bg2,
          title: Text(action == 'cancel' ? 'Cancel this setup?' : 'Discard this attempt?'),
          content: Text(action == 'cancel'
              ? 'Downloads stop. You can retry later from where it stopped.'
              : 'Deletes the half-made instance folder and its settings. Nothing else is touched.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Keep going')),
            FilledButton(
                style: FilledButton.styleFrom(backgroundColor: C.bad),
                onPressed: () => Navigator.pop(context, true),
                child: Text(action == 'cancel' ? 'Cancel setup' : 'Discard')),
          ],
        ),
      );
      if (ok != true) return;
    }
    setState(() => busy = true);
    try {
      await widget.api.jobAction(widget.jobId, action);
      if (action == 'discard' && mounted) {
        Navigator.pop(context);
        return;
      }
      await load();
    } on ApiError catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message), backgroundColor: C.bad));
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final j = job;
    final status = j?['status'] as String? ?? '';
    final steps = ((j?['steps'] as List?) ?? []).cast<Map<String, dynamic>>();
    final current = (j?['current'] as int?) ?? 0;
    final waiting = status == 'waiting' && current < steps.length ? steps[current] : null;
    final done = steps.where((s) => s['state'] == 'done' || s['state'] == 'skipped').length;
    if (status == 'done') timer?.cancel();
    return Scaffold(
      appBar: AppBar(backgroundColor: C.bg2, title: Text(j == null ? 'Setting up…' : 'Setting up ${j['name']}')),
      body: j == null
          ? Center(
              child: error == null
                  ? CircularProgressIndicator(color: C.accent)
                  : Text(error!, style: const TextStyle(color: C.bad)))
          : ListView(padding: const EdgeInsets.all(20), children: [
              Entrance(
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Row(children: [
                        Expanded(
                          child: Text(_headline(status),
                              style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700, color: _statusColor(status))),
                        ),
                        Text('$done / ${steps.length} steps', style: const TextStyle(color: C.muted)),
                      ]),
                      const SizedBox(height: 6),
                      Text('${j['game_name']} · ${j['mode_name']}', style: const TextStyle(color: C.muted)),
                      const SizedBox(height: 14),
                      ClipRRect(
                        borderRadius: BorderRadius.circular(6),
                        child: TweenAnimationBuilder<double>(
                          tween: Tween(end: steps.isEmpty ? 0 : done / steps.length),
                          duration: const Duration(milliseconds: 500),
                          builder: (_, v, __) => LinearProgressIndicator(
                              value: v, minHeight: 8, color: _statusColor(status), backgroundColor: C.panel2),
                        ),
                      ),
                      if (error != null)
                        Padding(
                            padding: const EdgeInsets.only(top: 10),
                            child: Text(error!, style: const TextStyle(color: C.warn))),
                    ]),
                  ),
                ),
              ),
              const SizedBox(height: 14),
              if (waiting != null && waiting['guide'] is Map) _Guide(step: waiting, busy: busy, act: act),
              if (status == 'failed')
                _Banner(
                  color: C.bad,
                  title: 'This step failed',
                  text: '${j['error'] ?? 'Unknown error'}',
                  actions: [
                    FilledButton(onPressed: busy ? null : () => act('retry'), child: const Text('Retry')),
                    if (widget.me.isAdmin)
                      TextButton(
                          onPressed: busy ? null : () => act('discard'),
                          child: const Text('Discard attempt', style: TextStyle(color: C.bad))),
                  ],
                ),
              if (status == 'cancelled')
                _Banner(
                  color: C.warn,
                  title: 'Setup cancelled',
                  text: j['back_to_settings'] == true
                      ? 'Go back to change the settings, or retry.'
                      : 'You can carry on from where it stopped.',
                  actions: [
                    FilledButton(onPressed: busy ? null : () => act('retry'), child: const Text('Retry')),
                    if (widget.me.isAdmin)
                      TextButton(
                          onPressed: busy ? null : () => act('discard'),
                          child: const Text('Discard attempt', style: TextStyle(color: C.bad))),
                  ],
                ),
              if (status == 'done')
                _Banner(
                  color: C.ok,
                  title: j['practice'] == true ? 'Your practice instance is ready' : 'Your instance is ready',
                  text: j['practice'] == true
                      ? 'This sandbox made a small pretend game (nothing was downloaded), so players can\'t join it. '
                          'Install HostSpawn on your real server to make one players can join.'
                      : j['connect'] != null
                          ? 'Players connect to ${j['connect']}'
                          : 'It is running and watched by HostSpawn.',
                  actions: [
                    FilledButton.icon(
                      icon: const Icon(Icons.dashboard_customize_outlined),
                      label: const Text('Open instance panel'),
                      onPressed: () => Navigator.pushReplacement(
                          context,
                          MaterialPageRoute(
                              builder: (_) =>
                                  ServerScreen(api: widget.api, me: widget.me, id: j['server_id'] as String))),
                    ),
                  ],
                ),
              if (j['root'] != null && j['practice'] != true)
                Card(
                  child: ListTile(
                    leading: Icon(Icons.folder_outlined, color: C.accent),
                    title: const Text('Your server\'s files'),
                    subtitle: SelectableText('${j['root']}',
                        style: const TextStyle(fontFamily: 'monospace', fontSize: 12.5, color: C.muted)),
                    trailing: OpenFolderButton(api: widget.api, dir: '${j['root']}'),
                  ),
                ),
              Card(
                child: Column(children: [
                  for (final (i, s) in steps.indexed) _StepTile(step: s, active: i == current && status != 'done'),
                ]),
              ),
              const SizedBox(height: 14),
              if (status == 'running' || status == 'queued' || status == 'checking' || status == 'waiting')
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: busy ? null : () => act('cancel'),
                    icon: const Icon(Icons.close, color: C.bad),
                    label: const Text('Cancel setup', style: TextStyle(color: C.bad)),
                  ),
                ),
              ExpansionTile(
                title: const Text('Setup log', style: TextStyle(color: C.muted)),
                children: [
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(color: Colors.black, borderRadius: BorderRadius.circular(10)),
                    child: SelectableText(((j['log'] as List?) ?? []).join('\n'),
                        style: const TextStyle(fontFamily: 'monospace', fontSize: 12, height: 1.35)),
                  ),
                ],
              ),
            ]),
    );
  }
}

String _headline(String status) => switch (status) {
      'done' => 'Ready',
      'failed' => 'Needs attention',
      'cancelled' => 'Cancelled',
      'waiting' => 'Your turn',
      'checking' => 'Checking…',
      _ => 'Working…',
    };

Color _statusColor(String status) => switch (status) {
      'done' => C.ok,
      'failed' => C.bad,
      'cancelled' || 'waiting' => C.warn,
      _ => C.accent,
    };

class _StepTile extends StatelessWidget {
  const _StepTile({required this.step, required this.active});
  final Map<String, dynamic> step;
  final bool active;
  @override
  Widget build(BuildContext context) {
    final state = step['state'] as String? ?? 'pending';
    final pct = (step['percent'] as num?)?.toDouble();
    final icon = switch (state) {
      'done' => const Icon(Icons.check_circle, color: C.ok),
      'skipped' => const Icon(Icons.redo, color: C.dim),
      'failed' => const Icon(Icons.error, color: C.bad),
      'waiting' => const Icon(Icons.pan_tool_alt_outlined, color: C.warn),
      'running' => SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.4, color: C.accent)),
      _ => const Icon(Icons.radio_button_unchecked, color: C.dim),
    };
    return ListTile(
      leading: icon,
      title: Text('${step['title']}', style: TextStyle(fontWeight: active ? FontWeight.w600 : FontWeight.normal)),
      subtitle: state == 'running' && (pct != null || step['detail'] != null)
          ? Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              if (step['detail'] != null) Text('${step['detail']}', style: const TextStyle(color: C.muted)),
              if (pct != null)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: LinearProgressIndicator(value: pct / 100, color: C.accent, backgroundColor: C.panel2),
                ),
            ])
          : step['kind'] == 'guided' && state == 'pending'
              ? const Text('You do this one (HostSpawn shows you how)', style: TextStyle(color: C.dim))
              : null,
      trailing: pct != null && state == 'running' ? Text('${pct.round()}%') : null,
    );
  }
}

class _Guide extends StatelessWidget {
  const _Guide({required this.step, required this.busy, required this.act});
  final Map<String, dynamic> step;
  final bool busy;
  final Future<void> Function(String) act;
  @override
  Widget build(BuildContext context) {
    final g = step['guide'] as Map;
    final commands = ((g['commands'] as List?) ?? []).cast<String>();
    return Entrance(
      child: Card(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14), side: const BorderSide(color: C.warn)),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              const Icon(Icons.pan_tool_alt_outlined, color: C.warn),
              const SizedBox(width: 10),
              Expanded(child: Text('${g['title']}', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600))),
            ]),
            const SizedBox(height: 10),
            Text('${g['text']}', style: const TextStyle(color: C.muted, height: 1.45)),
            if (commands.isNotEmpty) ...[
              const SizedBox(height: 14),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
                decoration: BoxDecoration(
                    color: Colors.black, borderRadius: BorderRadius.circular(10), border: Border.all(color: C.line)),
                child: Row(children: [
                  Expanded(
                    child: SelectableText(commands.join('\n'),
                        style: TextStyle(fontFamily: 'monospace', fontSize: 13, color: C.accent, height: 1.5)),
                  ),
                  IconButton(
                    tooltip: 'Copy',
                    icon: const Icon(Icons.copy, size: 18),
                    onPressed: () {
                      Clipboard.setData(ClipboardData(text: commands.join('\n')));
                      ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('Copied. Paste it into a terminal on the server.')));
                    },
                  ),
                ]),
              ),
            ],
            if (step['check_message'] != null) ...[
              const SizedBox(height: 12),
              Text('${step['check_message']}', style: const TextStyle(color: C.bad)),
            ],
            const SizedBox(height: 16),
            Wrap(spacing: 10, runSpacing: 10, children: [
              FilledButton(onPressed: busy ? null : () => act('continue'), child: const Text("I've done it: Continue")),
              if (step['skippable'] == true)
                OutlinedButton(onPressed: busy ? null : () => act('skip'), child: const Text('Skip for now')),
              TextButton(onPressed: busy ? null : () => act('back'), child: const Text('Back')),
            ]),
          ]),
        ),
      ),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({required this.color, required this.title, required this.text, required this.actions});
  final Color color;
  final String title, text;
  final List<Widget> actions;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Entrance(
          child: Card(
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14), side: BorderSide(color: color)),
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(title, style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600, color: color)),
                const SizedBox(height: 8),
                SelectableText(text, style: const TextStyle(color: C.text, height: 1.4)),
                const SizedBox(height: 14),
                Wrap(spacing: 10, runSpacing: 10, children: actions),
              ]),
            ),
          ),
        ),
      );
}
