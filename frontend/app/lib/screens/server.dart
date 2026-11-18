// One server's panel. A top bar on every tab (status, Start/Restart, Stop, More), then six tabs: Overview (Server
// Doctor and details), Players, Console, Files (and Database), Settings (with Backups, Installation and Delete) and
// Tools (Spyglass, Copy for AI, Open folder, PC check). Risky actions ask you to type the action.
import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'database_tab.dart';
import 'doctor.dart';
import 'files_tab.dart';
import 'install_tab.dart';
import 'pc_check.dart';
import 'open_folder.dart';
import 'players.dart';
import 'server_tabs.dart';
import 'tools_tab.dart';

const _moderatorActions = {'start', 'stop', 'restart', 'update', 'backup', 'send'}; // the agent checks too

/// The instance's tabs. Files is for admins only. Code opens a tab by name, never by its number.
enum InstanceTab { overview, players, console, files, settings, tools }

/// The pages inside the Settings tab (Delete is for admins only).
enum SettingsPart { game, backups, installation, delete }

class ServerScreen extends StatefulWidget {
  const ServerScreen({super.key, required this.api, required this.me, required this.id});
  final Api api;
  final Me me;
  final String id;
  @override
  State<ServerScreen> createState() => _ServerScreenState();
}

class _ServerScreenState extends State<ServerScreen> with SingleTickerProviderStateMixin {
  Map<String, dynamic>? s;
  Map<String, dynamic>? logs;
  String? error, logName;
  Timer? timer, logTimer;
  bool live = true; // the console follows new lines while its tab is open
  TabController? tabs;
  SettingsPart settingsPart = SettingsPart.game;
  bool showDatabase = false; // the Files tab's Files / Database switch

  late final pages = [
    for (final t in InstanceTab.values)
      if (t != InstanceTab.files || widget.me.isAdmin) t
  ];
  late final settingsParts = [
    for (final p in SettingsPart.values)
      if (p != SettingsPart.delete || widget.me.isAdmin) p
  ];
  bool get onLogs => tabs != null && pages[tabs!.index] == InstanceTab.console;
  void goTo(InstanceTab t) => tabs?.animateTo(pages.indexOf(t));

  @override
  void initState() {
    super.initState();
    tabs = TabController(length: pages.length, vsync: this)..addListener(_tabChanged);
    load();
    timer = Timer.periodic(const Duration(seconds: 5), (_) => load());
  }

  @override
  void dispose() {
    timer?.cancel();
    logTimer?.cancel();
    tabs?.removeListener(_tabChanged);
    tabs?.dispose();
    super.dispose();
  }

  void _tabChanged() {
    if (onLogs) {
      _follow();
    } else {
      logTimer?.cancel();
      logTimer = null;
    }
  }

  /// Load the logs now, then every 2 seconds while the console tab is open and Live is on.
  void _follow() {
    if (logTimer != null) return;
    loadLogs();
    logTimer = Timer.periodic(const Duration(seconds: 2), (_) {
      if (live && onLogs) loadLogs();
    });
  }

  /// Show the console and follow it, e.g. while the server boots after Start or Restart.
  void watchConsole() {
    setState(() {
      live = true;
      logName = (logs?.containsKey('console') ?? true) ? 'console' : logName;
    });
    goTo(InstanceTab.console);
    _follow();
  }

  Future<void> load() async {
    try {
      final d = await widget.api.server(widget.id);
      if (mounted) {
        setState(() {
          s = d;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> loadLogs() async {
    try {
      final l = await widget.api.logs(widget.id);
      if (mounted) {
        setState(() {
          logs = l;
          logName = logName ?? (l.keys.isEmpty ? null : l.keys.first);
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  void toast(String m, {bool bad = false}) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m), backgroundColor: bad ? C.bad : null));

  Future<void> run(String action) async {
    final confirm = ((s?['confirm'] as List?) ?? []).contains(action);
    String? typed;
    if (confirm) {
      typed =
          await showDialog<String>(context: context, builder: (_) => _Confirm(action: action, server: '${s?['name']}'));
      if (typed == null) return;
    }
    try {
      toast(await widget.api.action(widget.id, action, confirm: typed));
      Future.delayed(const Duration(seconds: 3), load);
      if (const {'start', 'restart', 'update'}.contains(action)) watchConsole(); // watch it boot
    } on ApiError catch (e) {
      toast(e.message, bad: true);
    }
  }

  /// A Server Doctor fix: an installation job (repair, cleanup, update), an action (start, restart) or a page.
  Future<void> fix(Map<String, dynamic> a) async {
    switch (a['kind']) {
      case 'action':
        await run('${a['action']}');
      case 'install':
        final op = '${a['op']}';
        final ok = await showDialog<bool>(
            context: context,
            builder: (_) => AlertDialog(
                  backgroundColor: C.bg2,
                  title: Text('${a['label']}?'),
                  content: Text(
                      op == 'repair'
                          ? 'HostSpawn checks every game file and downloads any that are missing or broken. '
                              'Your settings and saves are kept. The instance restarts if it was running.'
                          : op == 'cleanup'
                              ? 'Removes leftover download and temporary files. Your game, settings, saves and '
                                  'backups are kept.'
                              : 'Downloads the latest version of the game server. The instance restarts if it was running.',
                      style: const TextStyle(color: C.muted)),
                  actions: [
                    TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
                    FilledButton(onPressed: () => Navigator.pop(context, true), child: Text('${a['label']}')),
                  ],
                ));
        if (ok != true) return;
        try {
          await widget.api.installAction(widget.id, op);
          toast('${a['label']} started: you can follow it in Settings › Installation.');
          setState(() => settingsPart = SettingsPart.installation);
          goTo(InstanceTab.settings);
        } on ApiError catch (e) {
          toast(e.message, bad: true);
        }
      case 'open':
        final page = '${a['page']}';
        if (page == 'pccheck') {
          await Navigator.push(context, MaterialPageRoute(builder: (_) => PcCheckScreen(api: widget.api)));
        } else if (!widget.me.isAdmin) {
          toast('Only admins can open an instance\'s ${page == 'files' ? 'files' : 'database'}.', bad: true);
        } else {
          setState(() => showDatabase = page != 'files');
          goTo(InstanceTab.files);
        }
    }
  }

  Future<void> delete() async {
    final name = '${s?['name']}';
    final res = await showDialog<(String, bool)>(context: context, builder: (_) => _ConfirmDelete(server: name));
    if (res == null) return;
    try {
      final msg = await widget.api.deleteServer(widget.id, res.$1, keepBackups: res.$2);
      if (!mounted) return;
      timer?.cancel();
      final messenger = ScaffoldMessenger.of(context);
      Navigator.popUntil(context, (r) => r.isFirst); // the dashboard (not the setup screen that opened this panel)
      messenger.showSnackBar(SnackBar(content: Text('$name: $msg')));
    } on ApiError catch (e) {
      toast(e.message, bad: true);
    }
  }

  /// The top bar's buttons: one filled button for the obvious next step (Start when it's down, Restart when it's
  /// up), Stop, and More for everything else that acts on the server. Spyglass's actions live on the Tools tab.
  List<Widget> _actionBar(Map st) {
    final allowed = ((s?['actions'] as List?) ?? [])
        .cast<String>()
        .where((a) => a != 'send' && a != 'wipe' && a != 'logs' && !a.startsWith('spyglass'))
        .where((a) => widget.me.isAdmin || (widget.me.canRun && _moderatorActions.contains(a)))
        .toList();
    if (s?['controllable'] != true || allowed.isEmpty) return const [];
    final up = const {'running', 'starting'}.contains(st['state']);
    final down = const {'stopped', 'maintenance', 'not_installed'}.contains(st['state']);
    final primary = up && allowed.contains('restart') ? 'restart' : (allowed.contains('start') ? 'start' : null);
    final more = allowed.where((a) => !const {'start', 'stop', 'restart'}.contains(a)).toList();
    if (!up && allowed.contains('restart')) more.insert(0, 'restart'); // e.g. crashed: Restart is still on offer
    String label(String a) => a == 'backup' ? 'Back up now' : a[0].toUpperCase() + a.substring(1);
    return [
      if (primary != null)
        FilledButton.icon(
          onPressed: primary == 'start' && up ? null : () => run(primary),
          icon: Icon(_icon(primary), size: 19),
          label: Text(label(primary)),
          style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14)),
        ),
      if (allowed.contains('stop')) ...[
        const SizedBox(width: 8),
        OutlinedButton.icon(
          onPressed: down ? null : () => run('stop'), // nothing to stop
          icon: const Icon(Icons.stop_rounded, size: 19),
          label: const Text('Stop'),
          style: OutlinedButton.styleFrom(
            foregroundColor: C.bad,
            side: BorderSide(color: down ? C.line2 : C.bad.withValues(alpha: .6)),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          ),
        ),
      ],
      if (more.isNotEmpty) ...[
        const SizedBox(width: 4),
        PopupMenuButton<String>(
          tooltip: 'More actions',
          onSelected: run,
          itemBuilder: (_) => [
            for (final a in more)
              PopupMenuItem(
                value: a,
                child: ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(_icon(a), color: C.accent),
                    title: Text(label(a))),
              ),
          ],
          child: const Padding(
            padding: EdgeInsets.symmetric(horizontal: 10, vertical: 12),
            child: Row(mainAxisSize: MainAxisSize.min, children: [Text('More'), Icon(Icons.arrow_drop_down)]),
          ),
        ),
      ],
    ];
  }

  @override
  Widget build(BuildContext context) {
    final st = (s?['status'] as Map?) ?? {};
    final max = (s?['settings'] as Map?)?['max_players'];
    final controllable = s?['controllable'] == true;
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final status = Row(mainAxisSize: MainAxisSize.min, children: [
      _StatePill('${st['state'] ?? 'unknown'}'),
      if (wide) ...[
        const SizedBox(width: 16),
        _Stat('Players', st['players'] == null ? '–' : '${st['players']}${max != null ? ' / $max' : ''}'),
        _Stat('FPS', st['fps'] == null ? '–' : '${st['fps']}'),
      ],
    ]);
    return Backdrop(
        child: Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        backgroundColor: C.bg2,
        toolbarHeight: 72,
        actions: [if (s != null) ..._actionBar(st), const SizedBox(width: 16)],
        title: Row(children: [
          Flexible(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${s?['label'] ?? s?['name'] ?? widget.id}', overflow: TextOverflow.ellipsis),
              Text(
                  [if (s?['label'] != null) '${s?['name']}', if (s?['mode_name'] != null) '${s?['mode_name']}']
                      .join(' · '),
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: C.muted, fontSize: 13)),
            ]),
          ),
          if (s != null) ...[const SizedBox(width: 18), status],
        ]),
        bottom: TabBar(
          controller: tabs,
          indicatorColor: C.accent,
          isScrollable: true,
          tabAlignment: TabAlignment.start,
          tabs: [
            for (final t in pages)
              Tab(
                  text: switch (t) {
                InstanceTab.overview => 'Overview',
                InstanceTab.players => 'Players',
                InstanceTab.console => 'Console',
                InstanceTab.files => 'Files',
                InstanceTab.settings => 'Settings',
                InstanceTab.tools => 'Tools',
              }),
          ],
        ),
      ),
      body: s == null
          ? Center(
              child: error == null
                  ? CircularProgressIndicator(color: C.accent)
                  : Text(error!, style: const TextStyle(color: C.bad)))
          : TabBarView(controller: tabs, children: [
              for (final t in pages)
                switch (t) {
                  InstanceTab.overview => _overview(controllable),
                  InstanceTab.players => PlayersTab(api: widget.api, me: widget.me, s: s!),
                  InstanceTab.console => _Logs(
                      logs: logs,
                      selected: logName,
                      pick: (n) => setState(() => logName = n),
                      refresh: loadLogs,
                      live: live,
                      setLive: (v) => setState(() => live = v)),
                  InstanceTab.files => SubPages(
                        side: false,
                        selected: showDatabase ? 1 : 0,
                        pick: (i) => setState(() => showDatabase = i == 1),
                        pages: [
                          SubPage('Files', Icons.description_outlined, (_) => FilesTab(api: widget.api, s: s!)),
                          SubPage('Database', Icons.storage_outlined, (_) => DatabaseTab(api: widget.api, s: s!)),
                        ]),
                  InstanceTab.settings => SubPages(
                      selected: settingsParts.indexOf(settingsPart),
                      pick: (i) => setState(() => settingsPart = settingsParts[i]),
                      pages: [for (final p in settingsParts) _settingsPage(p)]),
                  InstanceTab.tools => ToolsTab(api: widget.api, me: widget.me, s: s!, onChanged: load),
                },
            ]),
    ));
  }

  Widget _overview(bool controllable) {
    final st = (s!['status'] as Map?) ?? {};
    final max = (s!['settings'] as Map?)?['max_players'];
    return ListView(padding: const EdgeInsets.all(20), children: [
      if (!controllable)
        const Padding(
          padding: EdgeInsets.only(bottom: 14),
          child: Text('This copy of HostSpawn only watches this instance (development copy).',
              style: TextStyle(color: C.warn)),
        )
      else if (_actionBar(st).isEmpty)
        const Padding(
          padding: EdgeInsets.only(bottom: 14),
          child: Text('Your role can look at instances but not control them.', style: TextStyle(color: C.muted)),
        ),
      Entrance(child: DoctorCard(api: widget.api, s: s!, canFix: widget.me.canRun && controllable, fix: fix)),
      const SizedBox(height: 14),
      Card(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Wrap(spacing: 28, runSpacing: 12, children: [
            _Field('Game', '${s!['mode_name'] ?? s!['game']}'),
            _Field('Players', st['players'] == null ? '–' : '${st['players']}${max != null ? ' / $max' : ''}'),
            _Field('FPS', st['fps'] == null ? '–' : '${st['fps']}'),
            for (final e in ((s!['ports'] as Map?) ?? {}).entries) _Field(e.key.replaceAll('_', ' '), '${e.value}'),
            _Field('Supervised by', '${s!['supervised_by'] ?? '–'}'),
          ]),
        ),
      ),
      if (s!['folder'] != null) ...[
        const SizedBox(height: 14),
        Card(
          child: ListTile(
            leading: Icon(Icons.folder_outlined, color: C.accent),
            title: const Text('Server files'),
            subtitle: SelectableText('${s!['folder']}',
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12.5, color: C.muted)),
            trailing: OpenFolderButton(api: widget.api, dir: '${s!['folder']}'),
          ),
        ),
      ],
    ]);
  }

  SubPage _settingsPage(SettingsPart p) => switch (p) {
        SettingsPart.game => SubPage(
            'Game settings',
            Icons.tune,
            (_) => SettingsTab(
                key: ValueKey('settings-${s!['name']}-${s!['settings']}-${s!['wipes']?['mode']}'),
                api: widget.api,
                me: widget.me,
                s: s!,
                saved: load)),
        SettingsPart.backups => SubPage('Backups', Icons.inventory_2_outlined,
            (_) => BackupsTab(api: widget.api, s: s!, canRun: widget.me.canRun, backupNow: () => run('backup'))),
        SettingsPart.installation => SubPage('Installation', Icons.download_for_offline_outlined,
            (_) => InstallTab(api: widget.api, me: widget.me, s: s!)),
        SettingsPart.delete => SubPage('Delete', Icons.delete_outline, danger: true, (_) => _deletePage()),
      };

  Widget _deletePage() => ListView(padding: const EdgeInsets.all(20), children: [
        Card(
          child: ListTile(
            leading: const Icon(Icons.delete_outline, color: C.bad),
            title: const Text('Delete instance'),
            subtitle: Text(
                s!['deletable'] == true
                    ? 'Stops it and removes its folder and settings. You can keep its backups.'
                    : 'HostSpawn didn\'t create this instance, so it won\'t delete it. '
                        'Remove it from config/servers.toml instead.',
                style: const TextStyle(color: C.muted)),
            trailing: s!['deletable'] == true
                ? OutlinedButton(
                    onPressed: delete,
                    style: OutlinedButton.styleFrom(foregroundColor: C.bad, side: const BorderSide(color: C.bad)),
                    child: const Text('Delete…'),
                  )
                : null,
          ),
        ),
      ]);
}

/// The instance's state as a coloured pill (running, stopped, crashed...).
class _StatePill extends StatelessWidget {
  const _StatePill(this.state);
  final String state;
  @override
  Widget build(BuildContext context) {
    final c = stateColor(state);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 4),
      decoration: BoxDecoration(color: c.withValues(alpha: .14), borderRadius: BorderRadius.circular(99)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(Icons.circle, size: 9, color: c),
        const SizedBox(width: 7),
        Text(state, style: TextStyle(color: c, fontSize: 13, fontWeight: FontWeight.w600)),
      ]),
    );
  }
}

/// A small "Players 3 / 50" in the top bar.
class _Stat extends StatelessWidget {
  const _Stat(this.label, this.value);
  final String label, value;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(right: 16),
        child: Text.rich(TextSpan(children: [
          TextSpan(text: '$label ', style: const TextStyle(color: C.muted)),
          TextSpan(text: value, style: const TextStyle(fontWeight: FontWeight.w600, color: C.text)),
        ], style: const TextStyle(fontSize: 14))),
      );
}

IconData _icon(String a) => switch (a) {
      'start' => Icons.play_arrow_rounded,
      'stop' => Icons.stop_rounded,
      'restart' => Icons.restart_alt,
      'update' => Icons.system_update_alt,
      'backup' => Icons.backup_outlined,
      'oxide' => Icons.extension_outlined,
      'rotate' => Icons.map_outlined,
      'spyglass' => Icons.policy_outlined,
      _ => Icons.bolt,
    };

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

/// Risky actions: type the action's name to confirm (the agent checks it again).
class _Confirm extends StatefulWidget {
  const _Confirm({required this.action, required this.server});
  final String action, server;
  @override
  State<_Confirm> createState() => _ConfirmState();
}

class _ConfirmState extends State<_Confirm> {
  final c = TextEditingController();
  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: C.bg2,
        title: Text('${widget.action[0].toUpperCase()}${widget.action.substring(1)} ${widget.server}?'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(
              switch (widget.action) {
                'spyglass' => 'Adds the official Arma 3 parts that Spyglass (Altis Life\'s anti-cheat) wrongly flagged '
                    'to its allowed list. The mission is backed up first. Restart afterwards. Type "spyglass" to confirm.',
                'spyglass_off' => 'Turns Spyglass off with Altis Life\'s own switch, so it stops checking players. '
                    'BattlEye still protects the server, and you can turn Spyglass back on any time. The mission is '
                    'backed up first. Restart afterwards. Type "spyglass_off" to confirm.',
                'spyglass_on' => 'Turns Spyglass back on. The mission is backed up first. Restart afterwards. '
                    'Type "spyglass_on" to confirm.',
                _ => 'Players on this instance will be affected. Type "${widget.action}" to confirm.',
              },
              style: const TextStyle(color: C.muted)),
          const SizedBox(height: 14),
          TextField(controller: c, autofocus: true, onChanged: (_) => setState(() {})),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            onPressed:
                c.text.trim().toLowerCase() == widget.action ? () => Navigator.pop(context, widget.action) : null,
            child: Text(widget.action[0].toUpperCase() + widget.action.substring(1)),
          ),
        ],
      );
}

/// Delete: type the server's name (the agent checks it again); keeping backups is ticked by default.
class _ConfirmDelete extends StatefulWidget {
  const _ConfirmDelete({required this.server});
  final String server;
  @override
  State<_ConfirmDelete> createState() => _ConfirmDeleteState();
}

class _ConfirmDeleteState extends State<_ConfirmDelete> {
  bool keep = true;
  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: C.bg2,
        icon: const Icon(Icons.delete_forever_outlined, color: C.bad, size: 36),
        title: const Text('Are you sure you want to delete everything?'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(
              'This stops ${widget.server} and deletes its files, world and settings. You can always make a new '
              'instance afterwards.',
              style: const TextStyle(color: C.muted, height: 1.4)),
          const SizedBox(height: 10),
          CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            value: keep,
            onChanged: (v) => setState(() => keep = v ?? true),
            title: const Text('Keep its backups'),
            subtitle: const Text('Saved in servers/_kept_backups, so you could bring it back later.',
                style: TextStyle(color: C.dim, fontSize: 12)),
            controlAffinity: ListTileControlAffinity.leading,
          ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('No, keep it')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: C.bad),
            onPressed: () => Navigator.pop(context, (widget.server, keep)), // the name goes to the engine's check
            child: const Text('Yes, delete everything'),
          ),
        ],
      );
}

class _Logs extends StatelessWidget {
  const _Logs(
      {required this.logs,
      required this.selected,
      required this.pick,
      required this.refresh,
      required this.live,
      required this.setLive});
  final Map<String, dynamic>? logs;
  final String? selected;
  final void Function(String) pick;
  final Future<void> Function() refresh;
  final bool live;
  final void Function(bool) setLive;
  @override
  Widget build(BuildContext context) {
    if (logs == null) return Center(child: CircularProgressIndicator(color: C.accent));
    if (logs!.isEmpty) return const Center(child: Text('No logs yet', style: TextStyle(color: C.muted)));
    final raw = '${logs![selected] ?? ''}';
    final text = raw.trim().isEmpty ? '(This log is empty so far.)' : raw;
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Expanded(
            child: Wrap(spacing: 8, children: [
              for (final n in logs!.keys)
                ChoiceChip(
                    label: Text(n),
                    selected: n == selected,
                    onSelected: (_) => pick(n),
                    selectedColor: const Color(0x333EE6C4)),
            ]),
          ),
          FilterChip(
            avatar: Icon(Icons.circle, size: 10, color: live ? C.ok : C.dim),
            label: Text(live ? 'Live' : 'Paused'),
            selected: live,
            showCheckmark: false,
            tooltip: live ? 'Following new lines every 2 seconds. Tap to pause.' : 'Tap to follow new lines again.',
            onSelected: setLive,
          ),
          IconButton(tooltip: 'Refresh now', onPressed: refresh, icon: const Icon(Icons.refresh)),
        ]),
        const SizedBox(height: 10),
        Expanded(
          child: Container(
            decoration: BoxDecoration(
                color: Colors.black, borderRadius: BorderRadius.circular(10), border: Border.all(color: C.line)),
            padding: const EdgeInsets.all(14),
            child: SingleChildScrollView(
              reverse: true,
              child: SelectableText(text,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 12.5, color: C.text, height: 1.35)),
            ),
          ),
        ),
      ]),
    );
  }
}
