// Spyglass, Altis Life's built-in anti-cheat: what it does, whether it's on, who it threw out, and one Spyglass button
// with the options (fix wrongly flagged parts, pause, turn back on). Admins only. Every change backs up the mission
// first and is applied for you: a running instance restarts, a stopped one uses it on its next start.
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';

class SpyglassCard extends StatefulWidget {
  const SpyglassCard({super.key, required this.api, required this.server, this.onChanged});
  final Api api;
  final String server;

  /// Called after a change, so the instance page can refresh its status.
  final VoidCallback? onChanged;
  @override
  State<SpyglassCard> createState() => _SpyglassCardState();
}

class _SpyglassCardState extends State<SpyglassCard> {
  Map<String, dynamic>? r;
  String? error;
  bool busy = false;
  bool explain = false; // "What is Spyglass?" folded open

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final d = await widget.api.spyglass(widget.server);
      if (mounted) {
        setState(() {
          r = d;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> options() async {
    final action = await showDialog<String>(context: context, builder: (_) => SpyglassOptions(status: r!));
    if (action == null || !mounted) return;
    setState(() => busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.action(widget.server, action, confirm: action);
      messenger.showSnackBar(const SnackBar(content: Text('Spyglass: working on it. Watch the events list.')));
    } on ApiError catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message), backgroundColor: C.bad));
    }
    // The change runs in the background (and may restart the instance): look again a few times.
    for (final s in [3, 6, 15]) {
      await Future.delayed(Duration(seconds: s));
      if (!mounted) return;
      await load();
    }
    widget.onChanged?.call();
    if (mounted) setState(() => busy = false);
  }

  @override
  Widget build(BuildContext context) {
    if (r != null && r!['present'] != true) return const SizedBox.shrink();
    final on = r?['on'];
    final flags = ((r?['flags'] as List?) ?? []).cast<Map>();
    final official = ((r?['official'] as List?) ?? []).cast<String>();
    final other = ((r?['other'] as List?) ?? []).cast<String>();
    final missing = (r?['missing'] as num?)?.toInt() ?? 0;
    final pending = r?['pending'] == true;
    final (color, state) = r == null
        ? (C.dim, 'Checking…')
        : pending
            ? (C.warn, 'Changed: waiting for a restart')
            : on == false
                ? (C.warn, 'Paused: players are not checked')
                : official.isNotEmpty
                    ? (C.bad, 'On, and it threw out honest players')
                    : missing > 0
                        ? (C.warn, 'On, but it doesn\'t know $missing official Arma 3 parts')
                        : (C.ok, 'On: allows ${r!['allowed']} Arma 3 parts');
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(18, 14, 12, 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          CardHeader(
            icon: Icons.policy_outlined,
            title: 'Spyglass anti-cheat',
            status: error ?? state,
            color: color,
            statusColor: error != null ? C.bad : color,
            trailing: busy
                ? const Padding(
                    padding: EdgeInsets.all(12),
                    child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)))
                : FilledButton.tonalIcon(
                    onPressed: r == null ? null : options,
                    icon: const Icon(Icons.tune, size: 17),
                    label: const Text('Spyglass'),
                  ),
          ),
          const SizedBox(height: 4),
          TextButton.icon(
            onPressed: () => setState(() => explain = !explain),
            icon: Icon(explain ? Icons.expand_less : Icons.help_outline, size: 18),
            label: const Text('What is Spyglass?'),
          ),
          if (explain)
            const Text(
                'Spyglass comes with Altis Life. When a player joins, it checks their game for cheat menus and for '
                'game parts it doesn\'t know, and sends anyone suspicious back to the menu ("The SpyGlass sees you!"). '
                'Its list of allowed parts is from 2019, so newer Arma 3 parts can get honest players thrown out.',
                style: TextStyle(color: C.muted)),
          if (official.isNotEmpty || missing > 0) ...[
            const SizedBox(height: 10),
            Text(
                'Spyglass is missing ${missing > official.length ? missing : official.length} official Arma 3 '
                'part${(missing > official.length ? missing : official.length) == 1 ? '' : 's'}, so honest players '
                'can be thrown out. Press Spyglass → Fix for this Arma 3.',
                style: TextStyle(color: official.isNotEmpty ? C.bad : C.warn, fontSize: 13)),
          ],
          if (other.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text('Also flagged (mods or cheats, never added for you): ${other.join(', ')}',
                style: const TextStyle(color: C.warn, fontSize: 13)),
          ],
          if (flags.isNotEmpty) ...[
            const SizedBox(height: 10),
            const Text('Recently thrown out', style: TextStyle(fontWeight: FontWeight.w600)),
            for (final f in flags.reversed.take(3))
              Text('${f['time']}  ${f['player']}',
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 12, color: C.muted)),
          ],
        ]),
      ),
    );
  }
}

/// The Spyglass button's options. Returns the action to run (spyglass, spyglass_off, spyglass_on) or null.
class SpyglassOptions extends StatefulWidget {
  const SpyglassOptions({super.key, required this.status});
  final Map<String, dynamic> status;
  @override
  State<SpyglassOptions> createState() => _SpyglassOptionsState();
}

class _SpyglassOptionsState extends State<SpyglassOptions> {
  String? pick;

  @override
  Widget build(BuildContext context) {
    final s = widget.status;
    final on = s['on'] != false;
    final official = ((s['official'] as List?) ?? []).length;
    final missing = (s['missing'] as num?)?.toInt() ?? 0;
    final toFix = missing > official ? missing : official;
    final running = s['running'] == true;
    final choices = [
      (
        'spyglass',
        Icons.playlist_add_check,
        'Fix for this Arma 3 (best)',
        toFix > 0
            ? 'Adds the $toFix official Arma 3 part${toFix == 1 ? '' : 's'} Spyglass doesn\'t know (read from this '
                'server\'s own game files) to its allowed list, all in one go. You stay protected.'
            : 'Nothing to fix: Spyglass knows every official Arma 3 part on this server.',
        toFix > 0,
      ),
      if (on)
        (
          'spyglass_off',
          Icons.pause_circle_outline,
          'Pause Spyglass (turn off)',
          'Stops Spyglass checking players, using Altis Life\'s own switch. BattlEye still protects the server. '
              'Turn it back on here any time.',
          true,
        )
      else
        (
          'spyglass_on',
          Icons.shield_outlined,
          'Turn Spyglass back on',
          'Players are checked again when they join.',
          true,
        ),
    ];
    return AlertDialog(
      backgroundColor: C.bg2,
      title: const Text('Spyglass'),
      content: SizedBox(
        width: 460,
        child: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          for (final (action, icon, title, about, enabled) in choices)
            Card(
              color: pick == action ? C.accent.withValues(alpha: .15) : null,
              child: ListTile(
                enabled: enabled,
                leading: Icon(icon),
                title: Text(title),
                subtitle: Text(about, style: const TextStyle(color: C.muted, fontSize: 12)),
                trailing: pick == action ? Icon(Icons.check_circle, color: C.accent) : null,
                onTap: () => setState(() => pick = action),
              ),
            ),
          const SizedBox(height: 8),
          Text(
              running
                  ? 'The instance restarts to apply the change, so players are disconnected for about a minute. '
                      'The mission is backed up first.'
                  : 'The instance is stopped: the change is used when it next starts. The mission is backed up first.',
              style: const TextStyle(color: C.dim, fontSize: 12)),
          const ExpansionTile(
            tilePadding: EdgeInsets.zero,
            title: Text('Doing it by hand', style: TextStyle(fontSize: 13)),
            children: [
              Text(
                  'Inside the mission (.pbo): turn it off or on with spyGlass_toggle = false / true in '
                  'config/Config_Master.hpp. Allowed parts are the _patchList in SpyGlass/fn_initSpy.sqf; mods you '
                  'allow on purpose go there too. Turning it off is better than deleting the Spyglass files, which '
                  'breaks parts of the mission. Restart after any change.',
                  style: TextStyle(color: C.muted, fontSize: 12)),
            ],
          ),
        ])),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(
          onPressed: pick == null ? null : () => Navigator.pop(context, pick),
          child: Text(running ? 'Apply and restart' : 'Apply'),
        ),
      ],
    );
  }
}
