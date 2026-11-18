// The server panel's Settings and Backups pages, and SubPages (the side menu of the Settings tab, the Files / Database
// switch of the Files tab).
import 'dart:convert';

import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'open_folder.dart';

const _wipeModes = {
  'monthly': 'Monthly (first Thursday)',
  'biweekly': 'Every 2 weeks',
  'weekly': 'Weekly',
  'off': 'Off'
};
const _bpPolicies = {
  'forced': 'Only on the monthly forced wipe',
  'every': 'Every wipe',
  'never': 'Never',
};

/// Name, player limit, restart times, automatic backups and (Rust) wipes. Saves only what changed.
class SettingsTab extends StatefulWidget {
  const SettingsTab({super.key, required this.api, required this.me, required this.s, required this.saved});
  final Api api;
  final Me me;
  final Map<String, dynamic> s;
  final VoidCallback saved;
  @override
  State<SettingsTab> createState() => _SettingsTabState();
}

class _SettingsTabState extends State<SettingsTab> {
  late final name = TextEditingController(text: '${widget.s['name'] ?? ''}');
  late final players = TextEditingController(text: '${_set['max_players'] ?? ''}');
  late final keep = TextEditingController(text: '${_set['keep_backups'] ?? 10}');
  late List<String> restarts = ((_set['restart_times'] as List?) ?? []).map((e) => '$e').toList()..sort();
  late bool backup = _set['backup'] != false;
  bool get hasBattleye => _set.containsKey('battleye'); // Arma 3 only
  late bool battleye = _set['battleye'] != false;
  late String wipeMode = '${_wipes?['mode'] ?? 'monthly'}';
  late String wipeTime = '${_wipes?['time'] ?? '20:00'}';
  late String wipeBp = '${_wipes?['bp'] ?? 'forced'}';
  late bool newSeed = _wipes?['new_seed'] != false;
  late int warn = (_set['restart_warn_s'] as num?)?.toInt() ?? 300;
  late List<Map<String, dynamic>> messages =
      ((_set['messages'] as List?) ?? []).map((m) => Map<String, dynamic>.from(m as Map)).toList();
  // Rust: what the server browser shows (written to server.cfg at the next start)
  late final desc = TextEditingController(text: '${_set['description'] ?? ''}');
  late final url = TextEditingController(text: '${_set['url'] ?? ''}');
  late final header = TextEditingController(text: '${_set['header_image'] ?? ''}');
  late String region = '${_set['region'] ?? ''}';
  late bool pve = _set['pve'] == true;
  late bool globalChat = _set['global_chat'] != false;
  bool get isRust => widget.s['game'] == 'Rust' && _wipes != null;
  bool saving = false;
  String? error;

  Map get _set => (widget.s['settings'] as Map?) ?? {};
  String get zone => '${widget.s['timezone'] ?? 'server'}'; // the time zone chosen in account menu → Time zone
  Map? get _wipes => widget.s['wipes'] as Map?;

  Map<String, dynamic> _changes() {
    final out = <String, dynamic>{};
    if (name.text.trim() != '${widget.s['name']}') out['name'] = name.text.trim();
    if (players.text.trim() != '${_set['max_players'] ?? ''}') {
      out['max_players'] = int.tryParse(players.text.trim()) ?? -1;
    }
    final before = ((_set['restart_times'] as List?) ?? []).map((e) => '$e').toList()..sort();
    if (before.join(',') != restarts.join(',')) out['restart_times'] = restarts;
    if (backup != (_set['backup'] != false)) out['backup'] = backup;
    if (hasBattleye && battleye != (_set['battleye'] != false)) out['battleye'] = battleye;
    if (keep.text.trim() != '${_set['keep_backups'] ?? 10}') out['keep_backups'] = int.tryParse(keep.text.trim()) ?? -1;
    if (widget.s['can_say'] == true) {
      if (warn != ((_set['restart_warn_s'] as num?)?.toInt() ?? 300)) out['restart_warn_s'] = warn;
      if (jsonEncode(messages) != jsonEncode((_set['messages'] as List?) ?? [])) out['messages'] = messages;
    }
    if (isRust) {
      for (final (key, c) in [('description', desc), ('url', url), ('header_image', header)]) {
        if (c.text.trim() != '${_set[key] ?? ''}') out[key] = c.text.trim();
      }
      if (region != '${_set['region'] ?? ''}') out['region'] = region;
      if (pve != (_set['pve'] == true)) out['pve'] = pve;
      if (globalChat != (_set['global_chat'] != false)) out['global_chat'] = globalChat;
    }
    if (_wipes != null) {
      if (wipeMode != '${_wipes!['mode']}') out['wipe_mode'] = wipeMode;
      if (wipeTime != '${_wipes!['time']}') out['wipe_time'] = wipeTime;
      if (wipeBp != '${_wipes!['bp']}') out['wipe_bp'] = wipeBp;
      if (newSeed != (_wipes!['new_seed'] != false)) out['wipe_new_seed'] = newSeed;
    }
    return out;
  }

  Future<void> save() async {
    final ch = _changes();
    if (ch.isEmpty) return;
    setState(() {
      saving = true;
      error = null;
    });
    try {
      final msg = await widget.api.saveSettings('${widget.s['id']}', ch);
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
      widget.saved();
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  Future<String?> pickTime(String start) async {
    final p = start.split(':');
    final t = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(hour: int.tryParse(p[0]) ?? 0, minute: int.tryParse(p.length > 1 ? p[1] : '') ?? 0),
      builder: (c, child) => MediaQuery(data: MediaQuery.of(c).copyWith(alwaysUse24HourFormat: true), child: child!),
    );
    return t == null ? null : '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    if (widget.s['managed'] != true) {
      return const Center(
          child: Padding(
        padding: EdgeInsets.all(24),
        child: Text('HostSpawn didn\'t create this instance, so its settings are changed in its own files.',
            style: TextStyle(color: C.muted)),
      ));
    }
    if (!widget.me.isAdmin) {
      return const Center(child: Text('Only admins can change settings.', style: TextStyle(color: C.muted)));
    }
    final changed = _changes().isNotEmpty;
    return ListView(padding: const EdgeInsets.all(20), children: [
      _Section('Instance', [
        TextField(
            controller: name,
            decoration: const InputDecoration(labelText: 'Name players see'),
            onChanged: (_) => setState(() {})),
        const SizedBox(height: 12),
        TextField(
            controller: players,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Player limit (1–500)'),
            onChanged: (_) => setState(() {})),
        if (hasBattleye)
          SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('BattlEye anti-cheat'),
              subtitle: Text(
                  battleye
                      ? 'On: every player must run BattlEye. Turn off if it keeps kicking players '
                          '(for example "Game restart required"); turning it off lets cheaters in more easily.'
                      : 'Off: players join without BattlEye. Turn it back on when the kicks are sorted.',
                  style: const TextStyle(color: C.muted, fontSize: 12)),
              value: battleye,
              onChanged: (v) => setState(() => battleye = v)),
        const SizedBox(height: 6),
        Text('Name, player limit${hasBattleye ? ' and BattlEye' : ''} apply at the next restart.',
            style: const TextStyle(color: C.muted, fontSize: 12)),
      ]),
      if (isRust)
        _Section('Server browser (Rust)', [
          TextField(
              controller: desc,
              maxLines: 3,
              maxLength: 400,
              decoration: const InputDecoration(
                  labelText: 'Description', hintText: 'Welcome! Be respectful, have fun. Active admins.'),
              onChanged: (_) => setState(() {})),
          TextField(
              controller: url,
              decoration: const InputDecoration(labelText: 'Website or Discord (https://…)'),
              onChanged: (_) => setState(() {})),
          const SizedBox(height: 12),
          TextField(
              controller: header,
              decoration: const InputDecoration(labelText: 'Header image (https://…, 512 × 256 recommended)'),
              onChanged: (_) => setState(() {})),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            initialValue: region,
            decoration: const InputDecoration(labelText: 'Region tag'),
            items: const [
              DropdownMenuItem(value: '', child: Text('None')),
              DropdownMenuItem(value: 'EU', child: Text('Europe')),
              DropdownMenuItem(value: 'NA', child: Text('North America')),
              DropdownMenuItem(value: 'SA', child: Text('South America')),
              DropdownMenuItem(value: 'WA', child: Text('West Asia')),
              DropdownMenuItem(value: 'EA', child: Text('East Asia')),
              DropdownMenuItem(value: 'OC', child: Text('Oceania')),
              DropdownMenuItem(value: 'AF', child: Text('Africa')),
            ],
            onChanged: (v) => setState(() => region = v ?? ''),
          ),
          SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('PvE (players can\'t hurt each other)'),
              value: pve,
              onChanged: (v) => setState(() => pve = v)),
          SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Global chat'),
              value: globalChat,
              onChanged: (v) => setState(() => globalChat = v)),
          const Text(
              'Tags are set for you from the wipe schedule, vanilla or modded, PvE and region. These apply at the next '
              'restart.',
              style: TextStyle(color: C.muted, fontSize: 12)),
        ]),
      _Section('Daily restarts ($zone time)', [
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final t in restarts)
            InputChip(label: Text(t), onDeleted: () => setState(() => restarts = [...restarts]..remove(t))),
          if (restarts.length < 6)
            ActionChip(
              avatar: const Icon(Icons.add, size: 18),
              label: const Text('Add a time'),
              onPressed: () async {
                final t = await pickTime('06:00');
                if (t != null && !restarts.contains(t)) setState(() => restarts = [...restarts, t]..sort());
              },
            ),
        ]),
        if (restarts.isEmpty)
          const Padding(
            padding: EdgeInsets.only(top: 6),
            child: Text('No scheduled restarts.', style: TextStyle(color: C.warn, fontSize: 12)),
          ),
      ]),
      if (widget.s['can_say'] == true)
        _Section('In-game messages', [
          DropdownButtonFormField<int>(
            initialValue: _warnChoices.containsKey(warn) ? warn : 300,
            decoration: const InputDecoration(labelText: 'Countdown before scheduled restarts'),
            items: [for (final e in _warnChoices.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
            onChanged: (v) => setState(() => warn = v ?? warn),
          ),
          const SizedBox(height: 4),
          const Text('Players see "Server restart in 5 minutes." and so on; the restart still happens on time.',
              style: TextStyle(color: C.muted, fontSize: 12)),
          const SizedBox(height: 14),
          const Text('Scheduled messages', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          for (final (i, m) in messages.indexed)
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.campaign_outlined, color: C.accent),
              title: Text('${m['text']}'),
              subtitle: Text(
                  m['times'] != null ? 'at ${(m['times'] as List).join(', ')}' : 'every ${m['every_min']} min',
                  style: const TextStyle(color: C.muted)),
              trailing: IconButton(
                tooltip: 'Remove',
                icon: const Icon(Icons.close),
                onPressed: () => setState(() => messages = [...messages]..removeAt(i)),
              ),
            ),
          if (messages.length < 10)
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: () async {
                  final m =
                      await showDialog<Map<String, dynamic>>(context: context, builder: (_) => const _MessageDialog());
                  if (m != null) setState(() => messages = [...messages, m]);
                },
                icon: const Icon(Icons.add),
                label: const Text('Add a message'),
              ),
            ),
          const Text('You can use {server}, {players}, {max}, {next_restart} and {time} in a message.',
              style: TextStyle(color: C.muted, fontSize: 12)),
        ]),
      _Section('Backups', [
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          value: backup,
          onChanged: (v) => setState(() => backup = v),
          title: const Text('Automatic backups'),
          subtitle: const Text('Before each restart and every night', style: TextStyle(color: C.muted)),
        ),
        TextField(
            controller: keep,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Backups to keep (oldest are removed)'),
            onChanged: (_) => setState(() {})),
      ]),
      if (_wipes != null)
        _Section('Wipes', [
          DropdownButtonFormField<String>(
            initialValue: _wipeModes.containsKey(wipeMode) ? wipeMode : 'monthly',
            decoration: const InputDecoration(labelText: 'Map wipe'),
            items: [for (final e in _wipeModes.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
            onChanged: (v) => setState(() => wipeMode = v ?? wipeMode),
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            initialValue: _bpPolicies.containsKey(wipeBp) ? wipeBp : 'forced',
            decoration: const InputDecoration(labelText: 'Blueprints'),
            items: [for (final e in _bpPolicies.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
            onChanged: (v) => setState(() => wipeBp = v ?? wipeBp),
          ),
          const SizedBox(height: 4),
          ListTile(
            contentPadding: EdgeInsets.zero,
            title: Text('Wipe time ($zone)'),
            trailing: TextButton(
                onPressed: () async {
                  final t = await pickTime(wipeTime);
                  if (t != null) setState(() => wipeTime = t);
                },
                child: Text(wipeTime)),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: newSeed,
            onChanged: (v) => setState(() => newSeed = v),
            title: const Text('New map seed each wipe'),
          ),
          for (final w in ((_wipes!['next'] as List?) ?? []).take(3))
            Text('Next: ${'${w['when']}'.replaceFirst('T', ' ').substring(0, 16)} · ${w['what']}',
                style: const TextStyle(color: C.muted, fontSize: 12)),
        ]),
      if (error != null)
        Padding(padding: const EdgeInsets.only(bottom: 10), child: Text(error!, style: const TextStyle(color: C.bad))),
      Align(
        alignment: Alignment.centerLeft,
        child: FilledButton.icon(
          onPressed: changed && !saving ? save : null,
          icon: const Icon(Icons.save_outlined),
          label: Text(saving ? 'Saving…' : 'Save changes'),
        ),
      ),
    ]);
  }
}

const _warnChoices = {
  0: 'Off',
  60: '1 minute',
  120: '2 minutes',
  300: '5 minutes',
  600: '10 minutes',
  900: '15 minutes'
};

/// A scheduled message: its text, and either "every N minutes" or set times.
class _MessageDialog extends StatefulWidget {
  const _MessageDialog();
  @override
  State<_MessageDialog> createState() => _MessageDialogState();
}

class _MessageDialogState extends State<_MessageDialog> {
  final text = TextEditingController();
  bool atTimes = false;
  int every = 30;
  List<String> times = [];

  @override
  Widget build(BuildContext context) {
    final ok = text.text.trim().isNotEmpty && (!atTimes || times.isNotEmpty);
    return AlertDialog(
      backgroundColor: C.bg2,
      title: const Text('Add a message'),
      content: SizedBox(
        width: 420,
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          TextField(
            controller: text,
            autofocus: true,
            maxLength: 200,
            decoration: const InputDecoration(labelText: 'Message', hintText: 'Welcome to {server}! Rules: be nice.'),
            onChanged: (_) => setState(() {}),
          ),
          SegmentedButton<bool>(
            segments: const [
              ButtonSegment(value: false, label: Text('Repeat')),
              ButtonSegment(value: true, label: Text('At set times')),
            ],
            selected: {atTimes},
            onSelectionChanged: (v) => setState(() => atTimes = v.first),
          ),
          const SizedBox(height: 12),
          if (!atTimes)
            DropdownButtonFormField<int>(
              initialValue: every,
              decoration: const InputDecoration(labelText: 'Every'),
              items: [
                for (final m in const [10, 15, 30, 60, 120, 240])
                  DropdownMenuItem(
                      value: m, child: Text(m < 60 ? '$m minutes' : '${m ~/ 60} hour${m >= 120 ? 's' : ''}')),
              ],
              onChanged: (v) => setState(() => every = v ?? every),
            )
          else
            Wrap(spacing: 8, runSpacing: 8, children: [
              for (final tm in times) InputChip(label: Text(tm), onDeleted: () => setState(() => times.remove(tm))),
              if (times.length < 6)
                ActionChip(
                  avatar: const Icon(Icons.add, size: 18),
                  label: const Text('Add a time'),
                  onPressed: () async {
                    final t = await showTimePicker(
                      context: context,
                      initialTime: const TimeOfDay(hour: 18, minute: 0),
                      builder: (c, child) =>
                          MediaQuery(data: MediaQuery.of(c).copyWith(alwaysUse24HourFormat: true), child: child!),
                    );
                    if (t == null) return;
                    final s = '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
                    if (!times.contains(s)) setState(() => times = [...times, s]..sort());
                  },
                ),
            ]),
        ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(
          onPressed: ok
              ? () => Navigator.pop(context, {
                    'text': text.text.trim(),
                    if (atTimes) 'times': times else 'every_min': every,
                  })
              : null,
          child: const Text('Add'),
        ),
      ],
    );
  }
}

/// Backups: the newest 30 files, and Back up now.
class BackupsTab extends StatelessWidget {
  const BackupsTab({super.key, required this.api, required this.s, required this.canRun, required this.backupNow});
  final Api api;
  final Map<String, dynamic> s;
  final bool canRun;
  final VoidCallback backupNow;

  String _size(num b) => b > 1 << 30
      ? '${(b / (1 << 30)).toStringAsFixed(1)} GB'
      : b > 1 << 20
          ? '${(b / (1 << 20)).toStringAsFixed(1)} MB'
          : '${(b / 1024).ceil()} KB';

  @override
  Widget build(BuildContext context) {
    final files = (s['backups'] as List?) ?? [];
    final can = canRun && s['controllable'] == true && ((s['actions'] as List?) ?? []).contains('backup');
    return ListView(padding: const EdgeInsets.all(20), children: [
      Row(children: [
        Expanded(
          child: Text(
              files.isEmpty ? 'No backups yet' : '${files.length} backup${files.length == 1 ? '' : 's'} (newest first)',
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
        ),
        if (s['folder'] != null)
          OpenFolderButton(api: api, dir: '${s['folder']}/backups', label: 'Open backups folder'),
        if (can)
          FilledButton.icon(
              onPressed: backupNow, icon: const Icon(Icons.backup_outlined), label: const Text('Back up now')),
      ]),
      const SizedBox(height: 12),
      if (files.isNotEmpty)
        Card(
          child: Column(children: [
            for (final f in files)
              ListTile(
                dense: true,
                leading: Icon(Icons.inventory_2_outlined, color: C.accent),
                title: Text('${f['name']}'),
                subtitle: Text(
                    '${DateTime.fromMillisecondsSinceEpoch(((f['time'] as num) * 1000).round()).toString().substring(0, 16)} · ${_size(f['size'] as num)}',
                    style: const TextStyle(color: C.muted)),
              ),
          ]),
        ),
    ]);
  }
}

class _Section extends StatelessWidget {
  const _Section(this.title, this.children);
  final String title;
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
              const SizedBox(height: 12),
              ...children,
            ]),
          ),
        ),
      );
}

/// One page inside a tab: its name, icon and contents (built the first time it's opened).
class SubPage {
  const SubPage(this.label, this.icon, this.build, {this.danger = false});
  final String label;
  final IconData icon;
  final WidgetBuilder build;
  final bool danger; // shown in red (Delete)
}

/// Several pages in one tab: a side menu on wide screens ([side]), otherwise a row of chips on top. Pages stay alive
/// once opened, so unsaved settings or an open file aren't lost when you look at another page.
class SubPages extends StatefulWidget {
  const SubPages({super.key, required this.pages, required this.selected, required this.pick, this.side = true});
  final List<SubPage> pages;
  final int selected;
  final void Function(int) pick;
  final bool side;
  @override
  State<SubPages> createState() => _SubPagesState();
}

class _SubPagesState extends State<SubPages> {
  final opened = <int>{};

  @override
  Widget build(BuildContext context) {
    final sel = widget.selected.clamp(0, widget.pages.length - 1);
    opened.add(sel);
    final body = IndexedStack(index: sel, children: [
      for (var i = 0; i < widget.pages.length; i++)
        opened.contains(i) ? widget.pages[i].build(context) : const SizedBox.shrink(),
    ]);
    Color color(int i, SubPage p) => p.danger ? C.bad : (i == sel ? C.accent : C.muted);
    return LayoutBuilder(builder: (context, box) {
      if (widget.side && box.maxWidth >= 760) {
        return Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          SizedBox(
            width: 210,
            child: ListView(padding: const EdgeInsets.fromLTRB(12, 20, 8, 20), children: [
              for (final (i, p) in widget.pages.indexed)
                Padding(
                  padding: const EdgeInsets.only(bottom: 2),
                  child: ListTile(
                    dense: true,
                    selected: i == sel,
                    selectedTileColor: C.accent.withValues(alpha: .1),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                    leading: Icon(p.icon, size: 19, color: color(i, p)),
                    title: Text(p.label,
                        style: TextStyle(
                            color: color(i, p),
                            fontWeight: i == sel ? FontWeight.w600 : FontWeight.w400,
                            fontSize: 14)),
                    onTap: () => widget.pick(i),
                  ),
                ),
            ]),
          ),
          const VerticalDivider(width: 1),
          Expanded(child: body),
        ]);
      }
      return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Row(children: [
            for (final (i, p) in widget.pages.indexed)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: ChoiceChip(
                  avatar: Icon(p.icon, size: 17, color: color(i, p)),
                  label: Text(p.label,
                      style: TextStyle(color: color(i, p), fontWeight: i == sel ? FontWeight.w600 : FontWeight.w400)),
                  selected: i == sel,
                  showCheckmark: false,
                  backgroundColor: Colors.transparent,
                  selectedColor: C.accent.withValues(alpha: .15),
                  side: BorderSide(color: i == sel ? C.accent.withValues(alpha: .6) : C.line2),
                  onSelected: (_) => widget.pick(i),
                ),
              ),
          ]),
        ),
        Expanded(child: body),
      ]);
    });
  }
}
