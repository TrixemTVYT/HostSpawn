// The dashboard: machine stats, every server as a card (live, refreshed every 5 s), recent events.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../theme.dart';
import 'bug_report.dart';
import 'devices.dart';
import 'new_server.dart';
import 'pc_check.dart';
import 'players.dart';
import 'setup_job.dart';
import 'time_zone.dart';
import 'server.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.api, required this.me, required this.version, required this.signOut});
  final Api api;
  final Me me;
  final String version;
  final Future<void> Function() signOut;
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  Map<String, dynamic>? data;
  String? error;
  Timer? timer;
  StreamSubscription<void>? changes;

  @override
  void initState() {
    super.initState();
    load();
    timer = Timer.periodic(const Duration(seconds: 5), (_) => load());
    changes = widget.api.changed.listen((_) => load()); // a server deleted or created anywhere in the app
  }

  @override
  void dispose() {
    timer?.cancel();
    changes?.cancel();
    super.dispose();
  }

  int _asked = 0; // a slow older refresh must not overwrite a newer one (it would bring a deleted server back)

  Future<void> load() async {
    final n = ++_asked;
    try {
      final d = await widget.api.overview();
      if (n != _asked) return;
      if (mounted) {
        setState(() {
          data = d;
          error = null;
        });
      }
    } on ApiError catch (e) {
      if (e.signedOut) {
        timer?.cancel();
        await widget.signOut();
        return;
      }
      if (mounted && n == _asked) setState(() => error = e.message);
    } catch (e) {
      if (mounted && n == _asked) setState(() => error = "Lost contact with the agent. Retrying… ($e)");
    }
  }

  @override
  Widget build(BuildContext context) {
    final servers = (data?['servers'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final running = servers.where((s) => (s['status'] as Map?)?['state'] == 'running').length;
    final limits = (data?['limits'] as Map?) ?? const {};
    final atLimit =
        limits['max_instances'] is num && (limits['instances'] as num? ?? 0) >= (limits['max_instances'] as num);
    final players = servers.fold<int>(0, (n, s) => n + (((s['status'] as Map?)?['players'] as num?)?.toInt() ?? 0));
    final host = (data?['host'] as Map?) ?? {};
    final memUsed =
        host['mem_total_mb'] != null ? (1 - (host['mem_avail_mb'] as num) / (host['mem_total_mb'] as num)) : null;
    final events = (data?['events'] as List?)?.cast<Map<String, dynamic>>().reversed.take(8).toList() ?? [];
    // finished setups drop off once their instance is in the list below
    final jobs = ((data?['jobs'] as List?)?.cast<Map<String, dynamic>>() ?? [])
        .where((j) => !(j['status'] == 'done' && servers.any((s) => s['id'] == j['server_id'])))
        .toList();
    void newServer() => Navigator.push(
            context,
            MaterialPageRoute(
                builder: (_) =>
                    NewServerScreen(api: widget.api, me: widget.me, community: data?['community'] as String?)))
        .then((_) => load());
    return Scaffold(
      floatingActionButton: widget.me.isAdmin
          ? FloatingActionButton.extended(
              heroTag: null, // each page's button animates on its own (two shared the default tag)
              onPressed: atLimit
                  ? () => toast(
                      context,
                      "You've reached the limit of ${limits['max_instances']} instance${limits['max_instances'] == 1 ? '' : 's'}. "
                      'Delete one to make a new one.',
                      bad: true)
                  : newServer,
              backgroundColor: atLimit ? C.dim : C.accent,
              foregroundColor: C.bg,
              icon: const Icon(Icons.add),
              label: Text(limits['max_instances'] is num
                  ? 'New instance (${limits['instances']}/${limits['max_instances']})'
                  : 'New instance'),
            )
          : null,
      appBar: AppBar(
        backgroundColor: C.bg2,
        titleSpacing: 18,
        title: Row(children: [
          const Logo(size: 26),
          const SizedBox(width: 10),
          const Text('HostSpawn', style: TextStyle(fontWeight: FontWeight.w700)),
          const SizedBox(width: 12),
          if (data?['community'] != null)
            Text('${data!['community']}', style: const TextStyle(color: C.muted, fontSize: 14)),
        ]),
        actions: [
          if (widget.me.canRun)
            IconButton(
              tooltip: 'Message every instance',
              icon: const Icon(Icons.campaign_outlined),
              onPressed: () async {
                final t = await askText(context, 'Message every instance', 'Message (shown in game)', button: 'Send');
                if (t == null) return;
                try {
                  final m = await widget.api.playerAction('say', text: t);
                  if (context.mounted) toast(context, m);
                } on ApiError catch (e) {
                  if (context.mounted) toast(context, e.message, bad: true);
                }
              },
            ),
          TextButton.icon(
            onPressed: () => Navigator.push(
                context, MaterialPageRoute(builder: (_) => PlayersScreen(api: widget.api, me: widget.me))),
            icon: const Icon(Icons.groups_outlined),
            label: const Text('Players'),
          ),
          PopupMenuButton<String>(
            tooltip: widget.me.user,
            icon: const Icon(Icons.account_circle_outlined),
            onSelected: (v) => switch (v) {
              'out' => widget.signOut(),
              'theme' => pickTheme(context),
              'discord' => showDiscord(context),
              'timezone' => showTimeZone(context, widget.api, widget.me.isAdmin),
              'bug' => showBugReport(
                  context, widget.version, (data?['servers'] as List?)?.cast<Map<String, dynamic>>() ?? const []),
              'pccheck' => Navigator.push(context, MaterialPageRoute(builder: (_) => PcCheckScreen(api: widget.api))),
              'devices' => Navigator.push(
                  context, MaterialPageRoute(builder: (_) => DevicesScreen(api: widget.api, me: widget.me))),
              _ => null,
            },
            itemBuilder: (_) => [
              PopupMenuItem(
                  enabled: false,
                  child: Text('${widget.me.user} · ${widget.me.role}\nHostSpawn ${widget.version} by TrixemTV')),
              const PopupMenuItem(
                  value: 'devices',
                  child: ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(Icons.devices_other),
                      title: Text('Devices'))),
              const PopupMenuItem(
                  value: 'pccheck',
                  child: ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(Icons.health_and_safety_outlined),
                      title: Text('PC check'))),
              const PopupMenuItem(
                  value: 'theme',
                  child: ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(Icons.palette_outlined),
                      title: Text('Theme'))),
              const PopupMenuItem(
                  value: 'discord',
                  child: ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(Icons.forum_outlined),
                      title: Text('Help and updates on Discord'))),
              const PopupMenuItem(
                  value: 'timezone',
                  child: ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(Icons.schedule_outlined),
                      title: Text('Time zone'))),
              const PopupMenuItem(
                  value: 'bug',
                  child: ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(Icons.bug_report_outlined),
                      title: Text('Report a bug'))),
              const PopupMenuItem(value: 'out', child: Text('Sign out')),
            ],
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: Backdrop(
        child: data == null && error == null
            ? Center(child: CircularProgressIndicator(color: C.accent))
            : RefreshIndicator(
                onRefresh: load,
                child: ListView(padding: const EdgeInsets.all(20), children: [
                  if (error != null)
                    Padding(
                        padding: const EdgeInsets.only(bottom: 12),
                        child: Text(error!, style: const TextStyle(color: C.warn))),
                  if (data?['sandbox'] == true || servers.any((s) => s['practice'] == true))
                    Card(
                      color: const Color(0x2238BDF8),
                      child: const ListTile(
                        leading: Icon(Icons.science_outlined, color: C.info),
                        title: Text('Practice sandbox: nothing here is a real game server'),
                        subtitle: Text(
                            'Instances made here are small pretend games for trying HostSpawn out. Nothing is downloaded '
                            'from Steam and players can\'t join them. To run a real server, install HostSpawn on the '
                            'machine that will host it (packaging/installer) and create the instance there.',
                            style: TextStyle(color: C.muted)),
                      ),
                    ),
                  Entrance(
                    child: Padding(
                      padding: const EdgeInsets.only(top: 6, bottom: 22),
                      child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
                        Expanded(
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text(greeting(), style: const TextStyle(color: C.muted, fontSize: 14)),
                            const SizedBox(height: 4),
                            GradientText('Welcome back, ${widget.me.user}',
                                style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w800, letterSpacing: -.6)),
                            const SizedBox(height: 6),
                            Text(
                                servers.isEmpty
                                    ? 'No instances yet. Create one and HostSpawn does the rest.'
                                    : running == servers.length
                                        ? 'All ${servers.length} instance${servers.length == 1 ? ' is' : 's are'} running smoothly.'
                                        : '$running of ${servers.length} instances running.',
                                style: const TextStyle(color: C.muted, fontSize: 15)),
                          ]),
                        ),
                      ]),
                    ),
                  ),
                  Wrap(spacing: 14, runSpacing: 14, children: [
                    _Kpi(
                        Icons.dns_outlined,
                        'Instances',
                        limits['max_instances'] is num
                            ? '${limits['instances'] ?? 0} / ${limits['max_instances']}'
                            : '${servers.length}',
                        C.accent,
                        0,
                        sub: 'used of your limit'),
                    _Kpi(Icons.play_circle_outline, 'Running', '$running', C.ok, 40, sub: 'right now'),
                    _Kpi(Icons.groups_2_outlined, 'Players online', '$players', C.info, 80,
                        sub: 'across all instances'),
                    _Kpi(Icons.memory, 'Memory used', memUsed == null ? '–' : '${(memUsed * 100).round()}%',
                        memUsed != null && memUsed > .85 ? C.bad : C.accent2, 120,
                        sub: 'of this machine'),
                    _Kpi(Icons.save_outlined, 'Disk free',
                        host['disk_free_gb'] == null ? '–' : '${host['disk_free_gb']} GB', C.warn, 160,
                        sub: 'games and backups'),
                  ]),
                  if (jobs.isNotEmpty) ...[
                    const SizedBox(height: 26),
                    const Text('Being set up', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 12),
                    for (final j in jobs)
                      Card(
                        child: ListTile(
                          leading: Icon(
                              j['status'] == 'failed'
                                  ? Icons.error
                                  : j['status'] == 'waiting'
                                      ? Icons.pan_tool_alt_outlined
                                      : j['status'] == 'done'
                                          ? Icons.check_circle
                                          : Icons.downloading,
                              color: j['status'] == 'failed'
                                  ? C.bad
                                  : j['status'] == 'waiting'
                                      ? C.warn
                                      : j['status'] == 'done'
                                          ? C.ok
                                          : C.accent),
                          title: Text('${j['name']}'),
                          subtitle: Text(
                              '${j['game_name']} · ${j['mode_name']} · ${j['status'] == 'waiting' ? 'waiting for you' : j['status']}',
                              style: const TextStyle(color: C.muted)),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () => Navigator.push(
                                  context,
                                  MaterialPageRoute(
                                      builder: (_) =>
                                          SetupJobScreen(api: widget.api, me: widget.me, jobId: j['id'] as String)))
                              .then((_) => load()),
                        ),
                      ),
                  ],
                  const SizedBox(height: 26),
                  const Text('Instances', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 12),
                  if (servers.isEmpty)
                    Container(
                      padding: const EdgeInsets.symmetric(vertical: 40, horizontal: 24),
                      decoration: glass(glow: C.accent),
                      child: Column(children: [
                        Icon(Icons.rocket_launch_outlined, size: 46, color: C.accent),
                        const SizedBox(height: 14),
                        const Text('No instances yet.', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
                        const SizedBox(height: 6),
                        const Text('Pick a game and HostSpawn downloads, sets up and starts it for you.',
                            textAlign: TextAlign.center, style: TextStyle(color: C.muted)),
                        const SizedBox(height: 18),
                        if (widget.me.isAdmin)
                          FilledButton.icon(
                              onPressed: newServer,
                              icon: const Icon(Icons.add),
                              label: const Text('Create your first instance')),
                      ]),
                    ),
                  LayoutBuilder(builder: (context, box) {
                    final cols = (box.maxWidth / 340).floor().clamp(1, 4);
                    final w = (box.maxWidth - (cols - 1) * 14) / cols;
                    return Wrap(spacing: 14, runSpacing: 14, children: [
                      for (final (i, s) in servers.indexed)
                        SizedBox(
                            width: w,
                            child: Entrance(
                                delayMs: 40 * i,
                                child: _ServerCard(s,
                                    onOpen: () => Navigator.push(
                                        context,
                                        MaterialPageRoute(
                                            builder: (_) => ServerScreen(
                                                api: widget.api,
                                                me: widget.me,
                                                id: s['id'] as String))).then((_) => load())))),
                    ]);
                  }),
                  const SizedBox(height: 26),
                  const Text('Recent events', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 10),
                  Container(
                    decoration: glass(),
                    child: Column(children: [
                      for (final e in events)
                        ListTile(
                          dense: true,
                          leading: Icon(Icons.circle,
                              size: 10,
                              color: e['level'] == 'warn'
                                  ? C.warn
                                  : e['level'] == 'alert'
                                      ? C.bad
                                      : C.info),
                          title: Text('${e['message']}'),
                          subtitle:
                              Text('${e['server'] ?? ''} · ${when(e['t'])}', style: const TextStyle(color: C.dim)),
                        ),
                      if (events.isEmpty) const ListTile(title: Text('Nothing yet', style: TextStyle(color: C.muted))),
                    ]),
                  ),
                  const SizedBox(height: 80), // room for the New instance button
                ]),
              ),
      ),
    );
  }
}

String greeting() {
  final h = DateTime.now().hour;
  return h < 12
      ? 'Good morning'
      : h < 18
          ? 'Good afternoon'
          : 'Good evening';
}

String when(dynamic t) {
  if (t is! num) return '';
  final d = DateTime.fromMillisecondsSinceEpoch((t * 1000).round());
  String two(int n) => n.toString().padLeft(2, '0');
  final today = DateTime.now();
  final time = '${two(d.hour)}:${two(d.minute)}';
  return d.year == today.year && d.month == today.month && d.day == today.day ? time : '${d.day}/${d.month} $time';
}

class _Kpi extends StatelessWidget {
  const _Kpi(this.icon, this.label, this.value, this.color, this.delay, {this.sub});
  final IconData icon;
  final String label, value;
  final String? sub;
  final Color color;
  final int delay;
  @override
  Widget build(BuildContext context) => Entrance(
        delayMs: delay,
        child: Container(
          width: 214,
          padding: const EdgeInsets.all(18),
          decoration: glass(),
          child: Row(children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                  color: color.withValues(alpha: .14),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: color.withValues(alpha: .35))),
              child: Icon(icon, color: color, size: 22),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(label, style: const TextStyle(color: C.muted, fontSize: 12.5)),
                const SizedBox(height: 2),
                Text(value, style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w800, letterSpacing: -.5)),
                if (sub != null) Text(sub!, style: const TextStyle(color: C.dim, fontSize: 11.5)),
              ]),
            ),
          ]),
        ),
      );
}

class _ServerCard extends StatefulWidget {
  const _ServerCard(this.s, {required this.onOpen});
  final Map<String, dynamic> s;
  final VoidCallback onOpen;
  @override
  State<_ServerCard> createState() => _ServerCardState();
}

class _ServerCardState extends State<_ServerCard> {
  bool hover = false;
  @override
  Widget build(BuildContext context) {
    final s = widget.s;
    final st = (s['status'] as Map?) ?? {};
    final state = st['state'] as String? ?? 'unknown';
    final flags = ((st['flags'] as List?) ?? []).length;
    final gc = gameColor(s['game'] as String?);
    final maxP = (s['settings'] as Map?)?['max_players'] as num?;
    final p = st['players'] as num?;
    return MouseRegion(
      onEnter: (_) => setState(() => hover = true),
      onExit: (_) => setState(() => hover = false),
      cursor: SystemMouseCursors.click,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
        transform: Matrix4.translationValues(0, hover ? -4 : 0, 0),
        decoration: glass(glow: hover ? gc : null),
        child: Material(
          type: MaterialType.transparency,
          child: InkWell(
            borderRadius: BorderRadius.circular(16),
            onTap: widget.onOpen,
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Container(
                    width: 46,
                    height: 46,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(12),
                      gradient: LinearGradient(colors: [gc, gc.withValues(alpha: .55)]),
                      boxShadow: [BoxShadow(color: gc.withValues(alpha: .35), blurRadius: 16, spreadRadius: -4)],
                    ),
                    child: Text(gameTag(s['game'] as String?),
                        style: const TextStyle(color: C.bg, fontWeight: FontWeight.w900, fontSize: 15)),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('${s['label'] ?? s['name']}',
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 2),
                      Text('${s['name']} · ${s['mode_name'] ?? s['game']}',
                          overflow: TextOverflow.ellipsis, style: const TextStyle(color: C.muted, fontSize: 13)),
                    ]),
                  ),
                ]),
                const SizedBox(height: 14),
                Row(children: [
                  StatusPill(state),
                  if (s['practice'] == true) ...[const SizedBox(width: 6), const _Tag('Practice', C.info)],
                  if (s['update_available'] == true) ...[const SizedBox(width: 6), _Tag('Update available', C.accent)],
                  if (flags > 0) ...[const SizedBox(width: 6), _Tag('$flags notice${flags == 1 ? '' : 's'}', C.warn)],
                  if (((s['doctor'] as Map?)?['problems'] as num? ?? 0) > 0) ...[
                    const SizedBox(width: 6),
                    Tooltip(
                        message: 'Server Doctor: ${(s['doctor'] as Map)['top']}',
                        child: const Icon(Icons.medical_services_outlined, size: 18, color: C.bad)),
                  ],
                  const Spacer(),
                  _Stat(Icons.speed, st['fps'] == null ? '–' : '${st['fps']} fps'),
                ]),
                const SizedBox(height: 14),
                Row(children: [
                  _Stat(Icons.people_alt_outlined, p == null ? '–' : '$p${maxP != null ? ' / $maxP' : ''} players'),
                ]),
                const SizedBox(height: 8),
                ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                    value: (p != null && maxP != null && maxP > 0) ? (p / maxP).clamp(0, 1).toDouble() : 0,
                    minHeight: 5,
                    backgroundColor: C.line,
                    valueColor: AlwaysStoppedAnimation(gc),
                  ),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag(this.text, this.color);
  final String text;
  final Color color;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
        decoration: BoxDecoration(color: color.withValues(alpha: .14), borderRadius: BorderRadius.circular(20)),
        child: Text(text, style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600)),
      );
}

class _Stat extends StatelessWidget {
  const _Stat(this.icon, this.text);
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) => Row(children: [
        Icon(icon, size: 16, color: C.muted),
        const SizedBox(width: 6),
        Text(text, style: const TextStyle(color: C.text, fontSize: 13)),
      ]);
}

const discordInvite = 'https://discord.gg/AaBwz8szM';

/// Where to get help: the HostSpawn Discord (link to copy; opening a browser comes with the Windows/macOS builds).
Future<void> showDiscord(BuildContext context) => showDialog<void>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: C.bg2,
        title: const Text('HostSpawn on Discord'),
        content: const SizedBox(
          width: 440,
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(
                'Updates, ideas and help from the HostSpawn team and community. For help, press Get help in #help '
                'to open a private ticket.',
                style: TextStyle(color: C.muted, height: 1.4)),
            SizedBox(height: 14),
            SelectableText(discordInvite, style: TextStyle(fontWeight: FontWeight.w600)),
          ]),
        ),
        actions: [
          TextButton(
              onPressed: () {
                Clipboard.setData(const ClipboardData(text: discordInvite));
                Navigator.pop(c);
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Link copied')));
              },
              child: const Text('Copy link')),
          FilledButton(onPressed: () => Navigator.pop(c), child: const Text('Close')),
        ],
      ),
    );
