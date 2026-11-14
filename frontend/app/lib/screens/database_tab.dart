// The Database tab (admins only): an instance's own databases. Browse tables, search, sort, run read-only SQL, and
// (with Changes on) edit a cell, delete a row or run a changing query: you type CHANGE and a backup is made first.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../theme.dart';
import 'players.dart' show toast;

/// Ready-made questions for each kind of database (Altis Life's tables are players, vehicles, gangs, houses).
const _starters = {
  'mariadb': {
    'Richest players': 'SELECT name, pid, cash, bankacc FROM players ORDER BY bankacc + cash DESC LIMIT 25',
    'Vehicles per player':
        'SELECT p.name, COUNT(v.id) AS vehicles FROM players p JOIN vehicles v ON v.pid = p.pid GROUP BY p.pid ORDER BY vehicles DESC',
    'Gangs': 'SELECT name, owner, members, bank FROM gangs WHERE active = 1 ORDER BY bank DESC',
    'Seen this week':
        'SELECT name, pid, last_seen FROM players WHERE last_seen > NOW() - INTERVAL 7 DAY ORDER BY last_seen DESC',
  },
  'sqlite': {
    'All tables': "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name",
  },
};

class DatabaseTab extends StatefulWidget {
  const DatabaseTab({super.key, required this.api, required this.s});
  final Api api;
  final Map<String, dynamic> s;
  @override
  State<DatabaseTab> createState() => _DatabaseTabState();
}

class _DatabaseTabState extends State<DatabaseTab> {
  List<Map<String, dynamic>>? sources;
  List<Map<String, dynamic>> tables = [];
  String? error, source, table, order;
  bool desc = false, changes = false, busy = false, sqlOpen = false;
  int page = 0;
  Map<String, dynamic>? grid; // {columns, key, rows, total} or a query result {columns, rows, truncated}
  bool showingQuery = false;
  final search = TextEditingController(), sql = TextEditingController();

  String get sid => '${widget.s['id']}';
  Map<String, dynamic>? get src => sources?.firstWhere((x) => x['id'] == source, orElse: () => {});

  @override
  void initState() {
    super.initState();
    loadSources();
  }

  @override
  void dispose() {
    search.dispose();
    sql.dispose();
    super.dispose();
  }

  Future<T?> _guard<T>(Future<T> Function() f) async {
    setState(() => busy = true);
    try {
      final r = await f();
      if (mounted) setState(() => error = null);
      return r;
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
    return null;
  }

  Future<void> loadSources() async {
    final s = await _guard(() => widget.api.dbSources(sid));
    if (s == null || !mounted) return;
    setState(() => sources = s.cast<Map<String, dynamic>>());
    if (sources!.isNotEmpty) await pickSource('${sources!.first['id']}');
  }

  Future<void> pickSource(String id) async {
    setState(() {
      source = id;
      table = null;
      grid = null;
      tables = [];
    });
    final t = await _guard(() => widget.api.dbTables(sid, id));
    if (t == null || !mounted) return;
    setState(() => tables = t.cast<Map<String, dynamic>>());
    if (tables.isNotEmpty) await pickTable('${tables.first['name']}');
  }

  Future<void> pickTable(String name) async {
    setState(() {
      table = name;
      page = 0;
      order = null;
      desc = false;
      search.clear();
    });
    await loadRows();
  }

  Future<void> loadRows() async {
    if (source == null || table == null) return;
    final r = await _guard(
        () => widget.api.dbRows(sid, source!, table!, page: page, search: search.text, order: order ?? '', desc: desc));
    if (r != null && mounted) {
      setState(() {
        grid = r;
        showingQuery = false;
      });
    }
  }

  void _goPage(int p) {
    setState(() => page = p);
    loadRows();
  }

  Future<String?> _confirm(String what) => showDialog<String>(context: context, builder: (_) => _ConfirmChange(what));

  Future<void> runSql() async {
    if (source == null || sql.text.trim().isEmpty) return;
    if (changes) {
      if (await _confirm('Run this query on ${src?['name']}') == null) return;
      final r = await _guard(() => widget.api.dbChange(sid, source!, sql: sql.text));
      if (r != null && mounted) {
        toast(context, '${r['changed']} row(s) changed. Backup: ${r['backup']}');
        await loadRows();
      }
      return;
    }
    final r = await _guard(() => widget.api.dbQuery(sid, source!, sql.text));
    if (r != null && mounted) {
      setState(() {
        grid = r;
        showingQuery = true;
      });
    }
  }

  Map<String, dynamic> _keyOf(List row) {
    final cols = ((grid!['columns'] as List?) ?? []).cast<String>();
    return {for (final k in ((grid!['key'] as List?) ?? []).cast<String>()) k: row[cols.indexOf(k)]};
  }

  bool get editable => changes && !showingQuery && ((grid?['key'] as List?) ?? []).isNotEmpty;

  Future<void> editCell(List row, String col, dynamic old) async {
    if (!editable) return;
    final v = await showDialog<(bool, String)>(context: context, builder: (_) => _EditCell(col: col, old: old));
    if (v == null) return;
    if (await _confirm('Change $col in $table') == null) return;
    dynamic value = v.$1 ? null : v.$2;
    if (old is num && value is String) value = num.tryParse(value) ?? value;
    final r = await _guard(() => widget.api.dbChange(sid, source!, edit: {
          'op': 'update',
          'table': table,
          'key': _keyOf(row),
          'set': {col: value}
        }));
    if (r != null && mounted) {
      toast(context, 'Saved. Backup: ${r['backup']}');
      await loadRows();
    }
  }

  Future<void> deleteRow(List row) async {
    if (await _confirm('Delete this row from $table') == null) return;
    final r = await _guard(
        () => widget.api.dbChange(sid, source!, edit: {'op': 'delete', 'table': table, 'key': _keyOf(row)}));
    if (r != null && mounted) {
      toast(context, 'Row deleted. Backup: ${r['backup']}');
      await loadRows();
    }
  }

  void copyCsv() {
    String esc(dynamic v) {
      final s = v == null ? '' : '$v';
      return s.contains(RegExp('[",\n]')) ? '"${s.replaceAll('"', '""')}"' : s;
    }

    final cols = (grid!['columns'] as List).map(esc).join(',');
    final rows = (grid!['rows'] as List).map((r) => (r as List).map(esc).join(','));
    Clipboard.setData(ClipboardData(text: [cols, ...rows].join('\n')));
    toast(context, 'Copied ${(grid!['rows'] as List).length} rows as CSV');
  }

  @override
  Widget build(BuildContext context) {
    if (sources == null) {
      return Center(
          child: error != null
              ? Padding(padding: const EdgeInsets.all(24), child: Text(error!, style: const TextStyle(color: C.muted)))
              : CircularProgressIndicator(color: C.accent));
    }
    if (sources!.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Text(
              'This instance has no database yet.\n\nAltis Life\'s MariaDB database and SQLite files that plugins '
              'save (for example Rust plugins in oxide/data) show up here.',
              textAlign: TextAlign.center,
              style: TextStyle(color: C.muted, height: 1.5)),
        ),
      );
    }
    return Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      SizedBox(width: 250, child: _side()),
      const VerticalDivider(width: 1, color: C.line),
      Expanded(child: _main()),
    ]);
  }

  Widget _side() => ListView(padding: const EdgeInsets.all(12), children: [
        DropdownButtonFormField<String>(
          initialValue: source,
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Database', isDense: true),
          items: [
            for (final s in sources!)
              DropdownMenuItem(value: '${s['id']}', child: Text('${s['label']}', overflow: TextOverflow.ellipsis)),
          ],
          onChanged: (v) => v == null ? null : pickSource(v),
        ),
        if (src?['path'] != null) ...[
          const SizedBox(height: 6),
          SelectableText('${src!['path']}',
              style: const TextStyle(color: C.dim, fontSize: 11.5, fontFamily: 'monospace')),
        ],
        if (src?['note'] != null) ...[
          const SizedBox(height: 8),
          Text('${src!['note']}', style: const TextStyle(color: C.muted, fontSize: 12)),
        ],
        const SizedBox(height: 12),
        const Text('TABLES', style: TextStyle(color: C.dim, fontSize: 11, letterSpacing: 1.2)),
        for (final t in tables)
          ListTile(
            dense: true,
            selected: t['name'] == table && !showingQuery,
            selectedTileColor: C.accent.withValues(alpha: .12),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
            leading: Icon(Icons.table_chart_outlined, size: 18, color: C.accent),
            title: Text('${t['name']}', overflow: TextOverflow.ellipsis),
            trailing: Text(t['rows'] == null ? '' : '${t['approx'] == true ? '~' : ''}${t['rows']}',
                style: const TextStyle(color: C.muted, fontSize: 12)),
            onTap: () => pickTable('${t['name']}'),
          ),
      ]);

  Widget _main() {
    final total = (grid?['total'] as num?)?.toInt();
    final pages = total == null ? 1 : (total / 50).ceil().clamp(1, 1 << 30);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 12, 6),
        child: Wrap(spacing: 10, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Text(showingQuery ? 'Query result' : (table ?? ''),
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
          if (!showingQuery)
            SizedBox(
              width: 220,
              child: TextField(
                controller: search,
                decoration: const InputDecoration(
                    isDense: true, hintText: 'Search this table', prefixIcon: Icon(Icons.search, size: 18)),
                onSubmitted: (_) {
                  page = 0;
                  loadRows();
                },
              ),
            ),
          if (!showingQuery && total != null) ...[
            IconButton(
                tooltip: 'Previous page',
                onPressed: page > 0 ? () => _goPage(page - 1) : null,
                icon: const Icon(Icons.chevron_left)),
            Text('Page ${page + 1} of $pages · $total rows', style: const TextStyle(color: C.muted)),
            IconButton(
                tooltip: 'Next page',
                onPressed: page + 1 < pages ? () => _goPage(page + 1) : null,
                icon: const Icon(Icons.chevron_right)),
          ],
          if (showingQuery)
            TextButton.icon(
                onPressed: loadRows, icon: const Icon(Icons.arrow_back, size: 18), label: const Text('Back to table')),
          if (grid != null)
            TextButton.icon(
                onPressed: copyCsv, icon: const Icon(Icons.content_copy, size: 18), label: const Text('Copy CSV')),
          TextButton.icon(
              onPressed: () => setState(() => sqlOpen = !sqlOpen),
              icon: const Icon(Icons.code, size: 18),
              label: Text(sqlOpen ? 'Hide SQL' : 'SQL')),
          FilterChip(
            avatar: Icon(changes ? Icons.lock_open : Icons.lock_outline, size: 16, color: changes ? C.warn : C.ok),
            label: Text(changes ? 'Changes on' : 'Read-only'),
            selected: changes,
            showCheckmark: false,
            tooltip: changes
                ? 'Double-click a cell to edit it. Every change asks you to type CHANGE and makes a backup first.'
                : 'Looking only. Turn on to edit cells, delete rows or run changing SQL.',
            onSelected: (v) => setState(() => changes = v),
          ),
          if (busy) const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
        ]),
      ),
      if (error != null)
        Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 6),
            child: Text(error!, style: const TextStyle(color: C.bad))),
      if (sqlOpen) _sqlBox(),
      Expanded(child: grid == null ? const SizedBox() : _grid()),
    ]);
  }

  Widget _sqlBox() => Padding(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Wrap(spacing: 6, runSpacing: 6, children: [
            for (final e in (_starters[src?['kind']] ?? const <String, String>{}).entries)
              ActionChip(label: Text(e.key), onPressed: () => setState(() => sql.text = e.value)),
          ]),
          const SizedBox(height: 6),
          TextField(
            controller: sql,
            minLines: 3,
            maxLines: 8,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
            decoration: InputDecoration(
              hintText: changes ? 'Any one SQL statement (a backup is made first)' : 'SELECT … (read-only)',
              filled: true,
              fillColor: Colors.black54,
            ),
          ),
          const SizedBox(height: 6),
          Row(children: [
            Text(changes ? 'Changes are on: this can change data.' : 'Read-only: queries can\'t change anything.',
                style: TextStyle(color: changes ? C.warn : C.muted, fontSize: 12.5)),
            const Spacer(),
            FilledButton.icon(
              onPressed: busy ? null : runSql,
              style: changes ? FilledButton.styleFrom(backgroundColor: C.warn, foregroundColor: Colors.black) : null,
              icon: const Icon(Icons.play_arrow_rounded),
              label: Text(changes ? 'Run change…' : 'Run'),
            ),
          ]),
        ]),
      );

  Widget _grid() {
    final cols = ((grid!['columns'] as List?) ?? []).cast<String>();
    final rows = ((grid!['rows'] as List?) ?? []).cast<List>();
    if (cols.isEmpty) return const Center(child: Text('Done. Nothing to show.', style: TextStyle(color: C.muted)));
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (grid!['truncated'] == true)
        const Padding(
            padding: EdgeInsets.fromLTRB(16, 0, 16, 6),
            child: Text('Showing the first 500 rows.', style: TextStyle(color: C.warn, fontSize: 12.5))),
      Expanded(
        child: Scrollbar(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(8, 0, 8, 16),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: DataTable(
                headingRowHeight: 38,
                dataRowMinHeight: 32,
                dataRowMaxHeight: 44,
                columnSpacing: 22,
                headingTextStyle: const TextStyle(fontWeight: FontWeight.w700, color: C.text),
                sortColumnIndex: !showingQuery && order != null && cols.contains(order) ? cols.indexOf(order!) : null,
                sortAscending: !desc,
                columns: [
                  for (final c in cols)
                    DataColumn(
                      label: Row(mainAxisSize: MainAxisSize.min, children: [
                        if (((grid!['key'] as List?) ?? []).contains(c)) ...[
                          Icon(Icons.key, size: 13, color: C.warn),
                          const SizedBox(width: 4),
                        ],
                        Text(c),
                      ]),
                      onSort: showingQuery
                          ? null
                          : (_, __) {
                              setState(() {
                                desc = order == c ? !desc : false;
                                order = c;
                                page = 0;
                              });
                              loadRows();
                            },
                    ),
                  if (editable) const DataColumn(label: Text('')),
                ],
                rows: [
                  for (final r in rows)
                    DataRow(cells: [
                      for (var i = 0; i < cols.length; i++)
                        DataCell(
                          ConstrainedBox(
                            constraints: const BoxConstraints(maxWidth: 320),
                            child: Text(r[i] == null ? 'NULL' : '${r[i]}',
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                    color: r[i] == null ? C.dim : C.text,
                                    fontStyle: r[i] == null ? FontStyle.italic : null,
                                    fontFamily: 'monospace',
                                    fontSize: 12.5)),
                          ),
                          onDoubleTap: editable ? () => editCell(r, cols[i], r[i]) : null,
                        ),
                      if (editable)
                        DataCell(IconButton(
                            tooltip: 'Delete row…',
                            icon: const Icon(Icons.delete_outline, size: 18, color: C.bad),
                            onPressed: () => deleteRow(r))),
                    ]),
                ],
              ),
            ),
          ),
        ),
      ),
    ]);
  }
}

/// Every change: type CHANGE. Says a backup is made first.
class _ConfirmChange extends StatefulWidget {
  const _ConfirmChange(this.what);
  final String what;
  @override
  State<_ConfirmChange> createState() => _ConfirmChangeState();
}

class _ConfirmChangeState extends State<_ConfirmChange> {
  final c = TextEditingController();
  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: C.bg2,
        title: Text('${widget.what}?'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('HostSpawn backs up the database first, so this can be undone from the backup.',
              style: TextStyle(color: C.muted)),
          const SizedBox(height: 10),
          const Text('Type CHANGE to confirm.', style: TextStyle(color: C.muted)),
          const SizedBox(height: 8),
          TextField(controller: c, autofocus: true, onChanged: (_) => setState(() {})),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
              onPressed: c.text.trim() == 'CHANGE' ? () => Navigator.pop(context, 'CHANGE') : null,
              child: const Text('Change')),
        ],
      );
}

class _EditCell extends StatefulWidget {
  const _EditCell({required this.col, required this.old});
  final String col;
  final dynamic old;
  @override
  State<_EditCell> createState() => _EditCellState();
}

class _EditCellState extends State<_EditCell> {
  late final c = TextEditingController(text: widget.old == null ? '' : '${widget.old}');
  late bool isNull = widget.old == null;
  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: C.bg2,
        title: Text('Edit ${widget.col}'),
        content: SizedBox(
          width: 460,
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Now: ${widget.old ?? 'NULL'}',
                maxLines: 3, overflow: TextOverflow.ellipsis, style: const TextStyle(color: C.muted)),
            const SizedBox(height: 10),
            TextField(
                controller: c,
                enabled: !isNull,
                autofocus: true,
                minLines: 1,
                maxLines: 6,
                style: const TextStyle(fontFamily: 'monospace')),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: isNull,
              onChanged: (v) => setState(() => isNull = v ?? false),
              title: const Text('Empty (NULL)'),
              controlAffinity: ListTileControlAffinity.leading,
            ),
          ]),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, (isNull, c.text)), child: const Text('Next')),
        ],
      );
}
