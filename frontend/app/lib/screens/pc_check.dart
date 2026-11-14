// PC check: can this machine run each game's server? The agent reads the specs and compares them with each game's
// needs (GET /api/hostcheck). A guide only, with a disclaimer: it can't promise how a server will run.
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';

const verdictText = {'ok': 'Good to go', 'tight': 'Will run, with notes', 'no': 'Not recommended'};
const verdictColor = {'ok': C.ok, 'tight': C.warn, 'no': C.bad};
const verdictIcon = {'ok': Icons.check_circle, 'tight': Icons.error_outline, 'no': Icons.cancel_outlined};

class PcCheckScreen extends StatefulWidget {
  const PcCheckScreen({super.key, required this.api});
  final Api api;
  @override
  State<PcCheckScreen> createState() => _PcCheckScreenState();
}

class _PcCheckScreenState extends State<PcCheckScreen> {
  Map<String, dynamic>? r;
  String? error;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() => error = null);
    try {
      final got = await widget.api.hostCheck();
      if (mounted) setState(() => r = got);
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = (r?['machine'] as Map?) ?? const {};
    final games = ((r?['games'] as List?) ?? const []).cast<Map<String, dynamic>>();
    final notes = ((r?['notes'] as List?) ?? const []).cast<String>();
    return Backdrop(
        child: Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(backgroundColor: C.bg2, title: const Text('PC check'), actions: [
        IconButton(tooltip: 'Check again', icon: const Icon(Icons.refresh), onPressed: load),
      ]),
      body: r == null
          ? Center(
              child: error == null
                  ? CircularProgressIndicator(color: C.accent)
                  : Text(error!, style: const TextStyle(color: C.bad)))
          : ListView(padding: const EdgeInsets.all(20), children: [
              const Text('Can this machine run a game server safely?',
                  style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              const Text('HostSpawn looked at this machine and compared it with what each game needs.',
                  style: TextStyle(color: C.muted)),
              const SizedBox(height: 14),
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                    color: C.warn.withValues(alpha: .10),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: C.warn.withValues(alpha: .35))),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Icon(Icons.fact_check_outlined, color: C.warn, size: 20),
                  const SizedBox(width: 12),
                  Expanded(child: Text('${r!['advice'] ?? ''}')),
                ]),
              ),
              const SizedBox(height: 16),
              Wrap(spacing: 12, runSpacing: 12, children: [
                _Spec(Icons.memory, 'CPU cores', '${m['cores']}'),
                _Spec(Icons.storage, 'Memory', '${m['ram_total_gb']} GB', '${m['ram_free_gb']} GB free now'),
                _Spec(Icons.save_outlined, 'Disk free', '${m['disk_free_gb']} GB', 'of ${m['disk_total_gb']} GB'),
                _Spec(Icons.computer, 'System', '${m['os']}', '${m['arch']}'),
              ]),
              const SizedBox(height: 22),
              for (final g in games) _GameCard(g),
              for (final n in notes)
                Card(
                    child: ListTile(
                        leading: const Icon(Icons.info_outline, color: C.info),
                        title: Text(n, style: const TextStyle(color: C.muted)))),
              const SizedBox(height: 10),
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                    border: Border.all(color: C.line2), borderRadius: BorderRadius.circular(12), color: C.panel2),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Icon(Icons.gavel_outlined, color: C.muted, size: 20),
                  const SizedBox(width: 12),
                  Expanded(child: Text('${r!['disclaimer']}', style: const TextStyle(color: C.muted, fontSize: 13))),
                ]),
              ),
            ]),
    ));
  }
}

class _Spec extends StatelessWidget {
  const _Spec(this.icon, this.label, this.value, [this.sub]);
  final IconData icon;
  final String label, value;
  final String? sub;
  @override
  Widget build(BuildContext context) => SizedBox(
        width: 200,
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Row(children: [
              Icon(icon, color: C.accent),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(label, style: const TextStyle(color: C.muted, fontSize: 12)),
                  Text(value, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
                  if (sub != null) Text(sub!, style: const TextStyle(color: C.dim, fontSize: 12)),
                ]),
              ),
            ]),
          ),
        ),
      );
}

class _GameCard extends StatelessWidget {
  const _GameCard(this.g);
  final Map<String, dynamic> g;
  @override
  Widget build(BuildContext context) {
    final v = '${g['verdict']}';
    final color = verdictColor[v] ?? C.muted;
    final needs = (g['needs'] as Map?) ?? const {};
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(verdictIcon[v] ?? Icons.help_outline, color: color),
            const SizedBox(width: 10),
            Expanded(
                child: Text('${g['name']} Server', style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600))),
            Chip(
                label: Text(verdictText[v] ?? v, style: TextStyle(color: color)),
                backgroundColor: color.withValues(alpha: .12),
                side: BorderSide.none),
          ]),
          const SizedBox(height: 4),
          Text('${g['summary']}', style: const TextStyle(color: C.muted)),
          const SizedBox(height: 10),
          for (final reason in ((g['reasons'] as List?) ?? const []))
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Text('•  ', style: TextStyle(color: C.dim)),
                Expanded(child: Text('$reason')),
              ]),
            ),
          const SizedBox(height: 8),
          Text(
              'Needs: ${needs['cores']?['min']} CPU cores (${needs['cores']?['recommended']} recommended), '
              '${needs['ram_gb']?['min']} GB memory (${needs['ram_gb']?['recommended']} GB recommended), '
              'about ${needs['disk_gb']} GB of disk.',
              style: const TextStyle(color: C.dim, fontSize: 12)),
        ]),
      ),
    );
  }
}
