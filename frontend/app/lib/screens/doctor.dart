// Server Doctor: what's wrong with this instance, in plain English, and a button to fix it where that's safe.
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';

class DoctorCard extends StatefulWidget {
  const DoctorCard({super.key, required this.api, required this.s, required this.canFix, required this.fix});
  final Api api;
  final Map<String, dynamic> s; // the instance; its 'checks' are the quick health checks under "Show all checks"
  final bool canFix;

  /// Runs a finding's action: {kind: install|action|open, op?, action?, page?, label}.
  final Future<void> Function(Map<String, dynamic> action) fix;
  @override
  State<DoctorCard> createState() => _DoctorCardState();
}

class _DoctorCardState extends State<DoctorCard> {
  Map<String, dynamic>? r;
  String? error;
  bool busy = false, showChecks = false;

  String get state => '${(widget.s['status'] as Map?)?['state']}';

  @override
  void initState() {
    super.initState();
    check();
  }

  @override
  void didUpdateWidget(DoctorCard old) {
    super.didUpdateWidget(old);
    if ('${(old.s['status'] as Map?)?['state']}' != state) check(); // started, stopped or crashed: look again
  }

  Future<void> check() async {
    setState(() => busy = true);
    try {
      final d = await widget.api.doctor('${widget.s['id']}');
      if (mounted) {
        setState(() {
          r = d;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final findings = ((r?['findings'] as List?) ?? []).cast<Map>();
    final problems = findings.where((f) => f['level'] == 'problem').length;
    final checks = (widget.s['checks'] as Map?) ?? {};
    final (color, headline) = r == null
        ? (C.dim, 'Checking…')
        : findings.isEmpty
            ? (C.ok, 'All good: ${r!['checked']} checks passed')
            : problems > 0
                ? (C.bad, '$problems problem${problems == 1 ? '' : 's'} found')
                : (C.warn, '${findings.length} thing${findings.length == 1 ? '' : 's'} to look at');
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(18, 14, 12, 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          CardHeader(
            icon: Icons.medical_services_outlined,
            title: 'Server Doctor',
            status: error ?? headline,
            color: color,
            statusColor: error != null ? C.bad : color,
            trailing: busy
                ? const Padding(
                    padding: EdgeInsets.all(12),
                    child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)))
                : TextButton.icon(
                    onPressed: check, icon: const Icon(Icons.refresh, size: 18), label: const Text('Check now')),
          ),
          for (final f in findings) ...[
            const SizedBox(height: 12),
            _Finding(
                f: f.cast<String, dynamic>(),
                canFix: widget.canFix,
                fix: (a) async {
                  await widget.fix(a);
                  Future.delayed(const Duration(seconds: 4), () => mounted ? check() : null);
                }),
          ],
          if (checks.isNotEmpty) ...[
            const SizedBox(height: 6),
            TextButton.icon(
              onPressed: () => setState(() => showChecks = !showChecks),
              icon: Icon(showChecks ? Icons.expand_less : Icons.expand_more, size: 18),
              label: Text(showChecks ? 'Hide quick checks' : 'Show quick checks'),
            ),
            if (showChecks)
              for (final e in checks.entries)
                if (healthCheck(e.key, e.value) case (final name, final ok, final detail))
                  ListTile(
                    dense: true,
                    leading: Icon(
                        ok == null
                            ? Icons.remove_circle_outline
                            : ok
                                ? Icons.check_circle
                                : Icons.error,
                        color: ok == null
                            ? C.dim
                            : ok
                                ? C.ok
                                : C.bad),
                    title: Text(name),
                    subtitle: Text(detail, style: const TextStyle(color: C.muted)),
                  ),
          ],
        ]),
      ),
    );
  }
}

/// (name, ok? (null = not checked), detail) for one of the agent's quick checks (refreshed every few seconds).
(String, bool?, String) healthCheck(String key, dynamic v) {
  String ago(num secs) => secs < 90
      ? '${secs.round()} s ago'
      : secs < 5400
          ? '${(secs / 60).round()} min ago'
          : '${(secs / 3600).toStringAsFixed(1)} h ago';
  return switch (key) {
    'process' => ('Process running', v == true, v == true ? 'Running' : 'Not running'),
    'query' => (
        'Answers game queries',
        v as bool?,
        v == null
            ? 'Not checked (stopped or no query port)'
            : v == true
                ? 'Answering'
                : 'No answer'
      ),
    'rcon' => (
        'RCON',
        v == null ? null : (v as Map)['ok'] == true,
        v == null
            ? 'Not set up for this instance'
            : (v as Map)['ok'] == true
                ? 'Connected'
                : '${v['error'] ?? 'Not answering'}'
      ),
    'log_age_s' => (
        'Log is being written',
        v == null ? null : (v as num) < 600,
        v == null ? 'No log yet' : 'Last line ${ago(v as num)}'
      ),
    'last_backup' => (
        'Recent backup',
        v == null ? null : DateTime.now().millisecondsSinceEpoch / 1000 - (v as num) < 26 * 3600,
        v == null ? 'No backup yet' : 'Last backup ${ago(DateTime.now().millisecondsSinceEpoch / 1000 - (v as num))}'
      ),
    _ => (key, null, '$v'),
  };
}

class _Finding extends StatelessWidget {
  const _Finding({required this.f, required this.canFix, required this.fix});
  final Map<String, dynamic> f;
  final bool canFix;
  final Future<void> Function(Map<String, dynamic>) fix;

  @override
  Widget build(BuildContext context) {
    final level = '${f['level']}';
    final color = level == 'problem'
        ? C.bad
        : level == 'warning'
            ? C.warn
            : C.accent;
    final action = (f['action'] as Map?)?.cast<String, dynamic>();
    // Opening a page is always fine; changing something needs a role that may run the instance.
    final showFix = action != null && (action['kind'] == 'open' || canFix);
    return Container(
      decoration: BoxDecoration(
        color: color.withValues(alpha: .07),
        borderRadius: BorderRadius.circular(10),
        border: Border(left: BorderSide(color: color, width: 3)),
      ),
      padding: const EdgeInsets.fromLTRB(14, 12, 12, 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(level == 'problem' ? Icons.error_outline : Icons.warning_amber_rounded, color: color, size: 20),
          const SizedBox(width: 10),
          Expanded(child: Text('${f['title']}', style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15))),
          if (showFix)
            FilledButton.tonalIcon(
              onPressed: () => fix(action),
              icon: Icon(action['kind'] == 'open' ? Icons.open_in_new : Icons.build_outlined, size: 17),
              label: Text('${action['label'] ?? 'Fix it'}'),
            ),
        ]),
        const SizedBox(height: 6),
        Text('${f['why']}', style: const TextStyle(color: C.muted)),
        const SizedBox(height: 6),
        Text.rich(TextSpan(children: [
          const TextSpan(text: 'How to fix: ', style: TextStyle(fontWeight: FontWeight.w600)),
          TextSpan(text: '${f['fix']}'),
        ])),
        if ('${f['where'] ?? ''}'.isNotEmpty) ...[
          const SizedBox(height: 8),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(6)),
            child: SelectableText('${f['where']}',
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12, color: C.muted)),
          ),
        ],
      ]),
    );
  }
}
