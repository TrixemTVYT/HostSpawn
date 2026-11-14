// Players: who's online on a server (tab), everyone ever seen (page), one player's profile, and the ban list.
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';

String playtime(num? secs) {
  final s = (secs ?? 0).round();
  if (s < 3600) return '${(s / 60).round()} min';
  if (s < 86400 * 2) return '${(s / 3600).toStringAsFixed(1)} h';
  return '${(s / 86400).toStringAsFixed(1)} days';
}

String when(num? t) =>
    t == null ? '–' : DateTime.fromMillisecondsSinceEpoch((t * 1000).round()).toString().substring(0, 16);

void toast(BuildContext context, String m, {bool bad = false}) =>
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m), backgroundColor: bad ? C.bad : null));

/// Asks for a reason and a length. Returns (minutes, reason); minutes 0 = permanent.
Future<(int, String)?> askBan(BuildContext context, String name) =>
    showDialog<(int, String)>(context: context, builder: (_) => _BanDialog(name: name));

Future<String?> askText(BuildContext context, String title, String label, {String button = 'Save'}) =>
    showDialog<String>(context: context, builder: (_) => _TextDialog(title: title, label: label, button: button));

/// The Players tab on a server's panel.
class PlayersTab extends StatefulWidget {
  const PlayersTab({super.key, required this.api, required this.me, required this.s});
  final Api api;
  final Me me;
  final Map<String, dynamic> s;
  @override
  State<PlayersTab> createState() => _PlayersTabState();
}

class _PlayersTabState extends State<PlayersTab> {
  List<dynamic>? online;
  String? error;

  String get sid => '${widget.s['id']}';
  bool get canMod => widget.me.canRun && widget.s['can_moderate'] == true;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final o = await widget.api.onlinePlayers(sid);
      if (!mounted) return;
      setState(() {
        online = o;
        error = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  Future<void> act(Future<String> Function() f) async {
    try {
      final m = await f();
      if (mounted) toast(context, m);
    } on ApiError catch (e) {
      if (mounted) toast(context, e.message, bad: true);
    }
    await load();
  }

  @override
  Widget build(BuildContext context) {
    if (widget.s['players_supported'] != true) {
      return const _Empty('This game doesn\'t report its players to HostSpawn yet.');
    }
    if (widget.s['players_tracked'] != true) {
      return const _Empty('HostSpawn only watches this instance, so it doesn\'t ask it for players.\n'
          'Instances HostSpawn created show their players here.');
    }
    final list = online;
    return RefreshIndicator(
      onRefresh: load,
      child: ListView(padding: const EdgeInsets.all(20), children: [
        Row(children: [
          Expanded(
            child: Text(list == null ? 'Loading…' : '${list.length} online',
                style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
          ),
          IconButton(tooltip: 'Refresh', onPressed: load, icon: const Icon(Icons.refresh)),
          if (canMod)
            FilledButton.tonalIcon(
              onPressed: () async {
                final t =
                    await askText(context, 'Message to everyone on ${widget.s['name']}', 'Message', button: 'Send');
                if (t != null) await act(() => widget.api.playerAction('say', server: sid, text: t));
              },
              icon: const Icon(Icons.campaign_outlined),
              label: const Text('Message'),
            ),
        ]),
        const SizedBox(height: 4),
        const Text('Updated about once a minute.', style: TextStyle(color: C.muted, fontSize: 12)),
        if (error != null)
          Padding(padding: const EdgeInsets.only(top: 8), child: Text(error!, style: const TextStyle(color: C.bad))),
        const SizedBox(height: 10),
        if (list != null && list.isEmpty) const _Empty('Nobody is on right now.'),
        if (list != null && list.isNotEmpty)
          Card(
            child: Column(children: [
              for (final p in list)
                ListTile(
                  leading: Icon(Icons.person_outline, color: C.accent),
                  title: Text('${p['name']}'),
                  subtitle: Text(
                      'online since ${when(p['since']).substring(11)} · ping ${p['ping'] ?? '–'} · played ${playtime(p['playtime_s'])}',
                      style: const TextStyle(color: C.muted)),
                  onTap: () => openProfile(context, widget.api, widget.me, '${p['key']}').then((_) => load()),
                  trailing: canMod
                      ? Wrap(spacing: 4, children: [
                          TextButton(
                            onPressed: () async {
                              final r = await askText(context, 'Kick ${p['name']}?', 'Reason (shown to them)',
                                  button: 'Kick');
                              if (r != null) {
                                await act(
                                    () => widget.api.playerAction('kick', key: '${p['key']}', server: sid, reason: r));
                              }
                            },
                            child: const Text('Kick'),
                          ),
                          TextButton(
                            style: TextButton.styleFrom(foregroundColor: C.bad),
                            onPressed: () async {
                              final b = await askBan(context, '${p['name']}');
                              if (b != null) {
                                await act(() =>
                                    widget.api.playerAction('ban', key: '${p['key']}', minutes: b.$1, reason: b.$2));
                              }
                            },
                            child: const Text('Ban'),
                          ),
                        ])
                      : null,
                ),
            ]),
          ),
      ]),
    );
  }
}

Future<void> openProfile(BuildContext context, Api api, Me me, String key) =>
    Navigator.push(context, MaterialPageRoute(builder: (_) => ProfileScreen(api: api, me: me, playerKey: key)));

/// Everyone HostSpawn has seen on any server, with search.
class PlayersScreen extends StatefulWidget {
  const PlayersScreen({super.key, required this.api, required this.me});
  final Api api;
  final Me me;
  @override
  State<PlayersScreen> createState() => _PlayersScreenState();
}

class _PlayersScreenState extends State<PlayersScreen> {
  final q = TextEditingController();
  List<dynamic>? rows;
  String? error;

  @override
  void initState() {
    super.initState();
    search();
  }

  Future<void> search() async {
    try {
      final r = await widget.api.searchPlayers(q.text.trim());
      if (!mounted) return;
      setState(() {
        rows = r;
        error = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          backgroundColor: C.bg2,
          title: const Text('Players'),
          actions: [
            TextButton.icon(
              onPressed: () => Navigator.push(
                  context, MaterialPageRoute(builder: (_) => BansScreen(api: widget.api, me: widget.me))),
              icon: const Icon(Icons.gavel_outlined),
              label: const Text('Bans'),
            ),
            const SizedBox(width: 8),
          ],
        ),
        body: ListView(padding: const EdgeInsets.all(20), children: [
          TextField(
            controller: q,
            decoration:
                const InputDecoration(prefixIcon: Icon(Icons.search), labelText: 'Search by name (old names too)'),
            onSubmitted: (_) => search(),
            onChanged: (_) => search(),
          ),
          const SizedBox(height: 14),
          if (error != null) Text(error!, style: const TextStyle(color: C.bad)),
          if (rows != null && rows!.isEmpty)
            const _Empty('No players yet. Everyone who joins an instance HostSpawn created is listed here.'),
          if (rows != null && rows!.isNotEmpty)
            Card(
              child: Column(children: [
                for (final p in rows!)
                  ListTile(
                    leading: Icon(Icons.circle, size: 12, color: p['online'] == true ? C.ok : C.dim),
                    title: Text('${p['name']}'),
                    subtitle: Text(
                        'last seen ${when(p['last_seen'])} · played ${playtime(p['playtime_s'])} · '
                        '${p['sessions']} session${p['sessions'] == 1 ? '' : 's'}',
                        style: const TextStyle(color: C.muted)),
                    trailing: Wrap(spacing: 6, children: [
                      if (p['watched'] == true) const _Tag('Watched', C.warn),
                      if (p['banned'] == true) const _Tag('Banned', C.bad),
                    ]),
                    onTap: () => openProfile(context, widget.api, widget.me, '${p['key']}').then((_) => search()),
                  ),
              ]),
            ),
        ]),
      );
}

/// One player: names, playtime, sessions on every server, notes, bans, watch.
class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key, required this.api, required this.me, required this.playerKey});
  final Api api;
  final Me me;
  final String playerKey;
  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  Map<String, dynamic>? p;
  String? error;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final d = await widget.api.player(widget.playerKey);
      if (!mounted) return;
      setState(() {
        p = d;
        error = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  Future<void> act(Future<String> Function() f) async {
    try {
      final m = await f();
      if (mounted) toast(context, m);
    } on ApiError catch (e) {
      if (mounted) toast(context, e.message, bad: true);
    }
    await load();
  }

  @override
  Widget build(BuildContext context) {
    final d = p;
    final canMod = widget.me.canRun;
    final key = widget.playerKey;
    return Scaffold(
      appBar: AppBar(backgroundColor: C.bg2, title: Text(d == null ? 'Player' : '${d['name']}')),
      body: d == null
          ? Center(
              child: error == null
                  ? CircularProgressIndicator(color: C.accent)
                  : Text(error!, style: const TextStyle(color: C.bad)))
          : ListView(padding: const EdgeInsets.all(20), children: [
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(18),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      Expanded(
                          child:
                              Text('${d['name']}', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700))),
                      if (d['watch'] != null) const _Tag('Watched', C.warn),
                      if (d['ban'] != null)
                        const Padding(padding: EdgeInsets.only(left: 6), child: _Tag('Banned', C.bad)),
                    ]),
                    const SizedBox(height: 4),
                    SelectableText(
                        key.startsWith('steam:') ? 'Steam ${key.substring(6)}' : 'BattlEye ${key.substring(3)}',
                        style: const TextStyle(color: C.muted, fontSize: 12)),
                    const SizedBox(height: 12),
                    Wrap(spacing: 28, runSpacing: 10, children: [
                      _Field('Played', playtime(d['playtime_s'])),
                      _Field('Sessions', '${d['sessions']}'),
                      _Field('First seen', when(d['first_seen'])),
                      _Field('Last seen', when(d['last_seen'])),
                      _Field(
                          'Online now',
                          ((d['online'] as List?) ?? []).isEmpty
                              ? 'no'
                              : ((d['online'] as List).map((o) => o['server']).join(', '))),
                    ]),
                    if (d['ban'] != null) ...[
                      const SizedBox(height: 12),
                      Text(
                          'Banned ${d['ban']['until'] == null ? 'permanently' : 'until ${when(d['ban']['until'])}'} by ${d['ban']['by']}: ${d['ban']['reason']}',
                          style: const TextStyle(color: C.bad)),
                    ],
                  ]),
                ),
              ),
              if (canMod) ...[
                const SizedBox(height: 12),
                Wrap(spacing: 10, runSpacing: 10, children: [
                  if (d['ban'] == null)
                    FilledButton.icon(
                      style: FilledButton.styleFrom(backgroundColor: C.bad),
                      onPressed: () async {
                        final b = await askBan(context, '${d['name']}');
                        if (b != null) {
                          await act(() => widget.api.playerAction('ban', key: key, minutes: b.$1, reason: b.$2));
                        }
                      },
                      icon: const Icon(Icons.gavel),
                      label: const Text('Ban on every instance'),
                    )
                  else
                    OutlinedButton.icon(
                      onPressed: () => act(() => widget.api.playerAction('unban', key: key)),
                      icon: const Icon(Icons.lock_open),
                      label: const Text('Unban'),
                    ),
                  OutlinedButton.icon(
                    onPressed: () async {
                      if (d['watch'] != null) return act(() => widget.api.playerAction('unwatch', key: key));
                      final r = await askText(context, 'Watch ${d['name']}', 'Why? (you\'re told when they join)',
                          button: 'Watch');
                      if (r != null) await act(() => widget.api.playerAction('watch', key: key, reason: r));
                    },
                    icon: Icon(d['watch'] != null ? Icons.visibility_off_outlined : Icons.visibility_outlined),
                    label: Text(d['watch'] != null ? 'Stop watching' : 'Watch'),
                  ),
                  OutlinedButton.icon(
                    onPressed: () async {
                      final t = await askText(context, 'Note about ${d['name']}', 'Note (staff only)');
                      if (t != null) await act(() => widget.api.playerAction('note', key: key, text: t));
                    },
                    icon: const Icon(Icons.sticky_note_2_outlined),
                    label: const Text('Add note'),
                  ),
                ]),
              ],
              _List('Notes', [
                for (final n in (d['notes'] as List? ?? []))
                  ListTile(
                      dense: true,
                      title: Text('${n['text']}'),
                      subtitle: Text('${n['by']} · ${when(n['at'])}', style: const TextStyle(color: C.muted))),
              ]),
              _List('Ban history', [
                for (final b in (d['bans'] as List? ?? []))
                  ListTile(
                      dense: true,
                      title: Text('${b['reason']}'),
                      subtitle: Text(
                          '${b['by']} · ${when(b['at'])} · ${b['until'] == null ? 'permanent' : 'until ${when(b['until'])}'}'
                          '${b['lifted_at'] != null ? ' · lifted by ${b['lifted_by']}' : ''}',
                          style: const TextStyle(color: C.muted))),
              ]),
              _List('Names used', [
                for (final n in (d['names'] as List? ?? []))
                  ListTile(
                      dense: true,
                      title: Text('${n['name']}'),
                      trailing: Text(when(n['last_seen']), style: const TextStyle(color: C.muted))),
              ]),
              _List('Recent sessions', [
                for (final s in (d['history'] as List? ?? []))
                  ListTile(
                      dense: true,
                      title: Text('${s['server']}'),
                      subtitle: Text(
                          '${when(s['start'])} → ${when(s['end']).substring(11)} · ${playtime((s['end'] as num) - (s['start'] as num))}',
                          style: const TextStyle(color: C.muted))),
              ]),
            ]),
    );
  }
}

/// The one ban list for every server.
class BansScreen extends StatefulWidget {
  const BansScreen({super.key, required this.api, required this.me});
  final Api api;
  final Me me;
  @override
  State<BansScreen> createState() => _BansScreenState();
}

class _BansScreenState extends State<BansScreen> {
  List<dynamic>? rows;
  bool all = false;
  String? error;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final r = await widget.api.bans(all: all);
      if (!mounted) return;
      setState(() {
        rows = r;
        error = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(backgroundColor: C.bg2, title: const Text('Bans (every instance)')),
        body: ListView(padding: const EdgeInsets.all(20), children: [
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: all,
            onChanged: (v) {
              setState(() => all = v);
              load();
            },
            title: const Text('Show ended and lifted bans too'),
          ),
          if (error != null) Text(error!, style: const TextStyle(color: C.bad)),
          if (rows != null && rows!.isEmpty) const _Empty('No bans.'),
          if (rows != null && rows!.isNotEmpty)
            Card(
              child: Column(children: [
                for (final b in rows!)
                  ListTile(
                    leading: Icon(Icons.gavel, color: b['lifted_at'] == null ? C.bad : C.dim),
                    title:
                        Text('${b['name'] == '' || b['name'] == null ? 'Unknown player' : b['name']}: ${b['reason']}'),
                    subtitle: Text(
                        '${b['by']} · ${when(b['at'])} · ${b['until'] == null ? 'permanent' : 'until ${when(b['until'])}'}'
                        '${b['lifted_at'] != null ? ' · lifted by ${b['lifted_by']}' : ''}',
                        style: const TextStyle(color: C.muted)),
                    onTap: () => openProfile(context, widget.api, widget.me, '${b['key']}').then((_) => load()),
                    trailing: widget.me.canRun && b['lifted_at'] == null
                        ? TextButton(
                            onPressed: () async {
                              try {
                                final m = await widget.api.playerAction('unban', key: '${b['key']}');
                                if (context.mounted) toast(context, m);
                              } on ApiError catch (e) {
                                if (context.mounted) toast(context, e.message, bad: true);
                              }
                              load();
                            },
                            child: const Text('Unban'))
                        : null,
                  ),
              ]),
            ),
        ]),
      );
}

class _BanDialog extends StatefulWidget {
  const _BanDialog({required this.name});
  final String name;
  @override
  State<_BanDialog> createState() => _BanDialogState();
}

class _BanDialogState extends State<_BanDialog> {
  static const lengths = {'1 hour': 60, '1 day': 1440, '7 days': 10080, '30 days': 43200, 'Permanent': 0};
  final reason = TextEditingController();
  int minutes = 1440;
  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: C.bg2,
        title: Text('Ban ${widget.name}?'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('The ban holds on every instance. They\'re kicked straight away if they\'re on.',
              style: TextStyle(color: C.muted)),
          const SizedBox(height: 12),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final e in lengths.entries)
              ChoiceChip(
                  label: Text(e.key),
                  selected: minutes == e.value,
                  onSelected: (_) => setState(() => minutes = e.value)),
          ]),
          const SizedBox(height: 12),
          TextField(
              controller: reason,
              autofocus: true,
              maxLength: 120,
              decoration: const InputDecoration(labelText: 'Reason (shown to them)'),
              onChanged: (_) => setState(() {})),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: C.bad),
            onPressed: reason.text.trim().isEmpty ? null : () => Navigator.pop(context, (minutes, reason.text.trim())),
            child: const Text('Ban'),
          ),
        ],
      );
}

class _TextDialog extends StatefulWidget {
  const _TextDialog({required this.title, required this.label, required this.button});
  final String title, label, button;
  @override
  State<_TextDialog> createState() => _TextDialogState();
}

class _TextDialogState extends State<_TextDialog> {
  final c = TextEditingController();
  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: C.bg2,
        title: Text(widget.title),
        content: TextField(
            controller: c,
            autofocus: true,
            decoration: InputDecoration(labelText: widget.label),
            onChanged: (_) => setState(() {})),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
              onPressed: c.text.trim().isEmpty ? null : () => Navigator.pop(context, c.text.trim()),
              child: Text(widget.button)),
        ],
      );
}

class _List extends StatelessWidget {
  const _List(this.title, this.items);
  final String title;
  final List<Widget> items;
  @override
  Widget build(BuildContext context) => items.isEmpty
      ? const SizedBox.shrink()
      : Padding(
          padding: const EdgeInsets.only(top: 18),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Card(child: Column(children: items)),
          ]),
        );
}

class _Field extends StatelessWidget {
  const _Field(this.label, this.value);
  final String label, value;
  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: const TextStyle(color: C.muted, fontSize: 12)),
        const SizedBox(height: 2),
        Text(value, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
      ]);
}

class _Tag extends StatelessWidget {
  const _Tag(this.text, this.color);
  final String text;
  final Color color;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(color: color.withValues(alpha: 0.18), borderRadius: BorderRadius.circular(6)),
        child: Text(text, style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600)),
      );
}

class _Empty extends StatelessWidget {
  const _Empty(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
      padding: const EdgeInsets.all(24),
      child: Center(child: Text(text, style: const TextStyle(color: C.muted), textAlign: TextAlign.center)));
}
