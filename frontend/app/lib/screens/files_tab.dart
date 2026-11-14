// The server panel's Files tab (admins only): the instance's config and script files in a code editor with colours,
// line numbers, find and replace and go to line; checked before saving, versions to go back to, starter templates,
// and Arma 3 mission folders packed into .pbo files. See docs/FILE_EDITOR.md.
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:re_editor/re_editor.dart';
import 'package:re_highlight/languages/bash.dart';
import 'package:re_highlight/languages/cpp.dart';
import 'package:re_highlight/languages/csharp.dart';
import 'package:re_highlight/languages/ini.dart';
import 'package:re_highlight/languages/javascript.dart';
import 'package:re_highlight/languages/json.dart';
import 'package:re_highlight/languages/lua.dart';
import 'package:re_highlight/languages/python.dart';
import 'package:re_highlight/languages/xml.dart';
import 'package:re_highlight/languages/yaml.dart';
import 'package:re_highlight/styles/atom-one-dark.dart';

import '../api.dart';
import '../theme.dart';
import 'open_folder.dart';
import 'players.dart' show toast;

class FilesTab extends StatefulWidget {
  const FilesTab({super.key, required this.api, required this.s});
  final Api api;
  final Map<String, dynamic> s;
  @override
  State<FilesTab> createState() => _FilesTabState();
}

class _FilesTabState extends State<FilesTab> {
  List<Map<String, dynamic>> files = [], missions = [];
  Map<String, dynamic>? folder; // the folder being browsed: {dir, up, start, entries}
  String? listError, path, saveError, liveProblem;
  Map<String, dynamic>? open; // the file as last loaded or saved (text, sha, read_only, versions)
  final search = TextEditingController();
  final code = CodeLineEditingController();
  late final find = CodeFindController(code);
  bool busy = false, wrap = false;
  double fontSize = 14;
  Timer? _check;

  String get sid => '${widget.s['id']}';
  bool get dirty => open != null && code.text != open!['text'];
  bool get readOnly => open?['read_only'] == true;

  @override
  void initState() {
    super.initState();
    code.addListener(_changed);
    loadList();
    browse(null);
  }

  /// Opens a folder in the browser (null: the game server's own folder).
  Future<void> browse(String? dir) async {
    try {
      final r = await widget.api.filesDir(sid, dir ?? folder?['start'] as String?);
      if (mounted) setState(() => folder = r);
    } on ApiError catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  @override
  void dispose() {
    _check?.cancel();
    code.removeListener(_changed);
    find.dispose();
    code.dispose();
    search.dispose();
    super.dispose();
  }

  void _changed() {
    _check?.cancel();
    _check = Timer(const Duration(milliseconds: 500), () {
      if (!mounted || path == null) return;
      setState(() => liveProblem = problemIn(path!, code.text)); // mistakes marked while typing
    });
    // the editor can change its text while it is laying out: refresh "not saved" after this frame
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) setState(() {});
    });
  }

  Future<void> loadList() async {
    try {
      final r = await widget.api.files(sid);
      if (!mounted) return;
      setState(() {
        files = ((r['files'] as List?) ?? []).cast<Map<String, dynamic>>();
        missions = ((r['missions'] as List?) ?? []).cast<Map<String, dynamic>>();
        listError = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => listError = e.message);
    }
  }

  Future<bool> _okToLeave() async {
    if (!dirty) return true;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: C.bg2,
        title: const Text('Leave without saving?'),
        content: Text('Your changes to ${path ?? 'this file'} aren\'t saved.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep editing')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Leave without saving')),
        ],
      ),
    );
    return ok == true;
  }

  Future<void> openFile(String p) async {
    if (p == path || !await _okToLeave()) return;
    setState(() => busy = true);
    try {
      final f = await widget.api.file(sid, p);
      if (!mounted) return;
      setState(() {
        path = p;
        open = f;
        saveError = null;
        liveProblem = problemIn(p, '${f['text']}');
      });
      code.text = '${f['text']}';
    } on ApiError catch (e) {
      if (mounted) toast(context, e.message, bad: true);
    }
    if (mounted) setState(() => busy = false);
  }

  Future<void> save() async {
    if (path == null || !dirty) return;
    setState(() {
      busy = true;
      saveError = null;
    });
    try {
      final f = await widget.api.saveFile(sid, path!, code.text, '${open!['sha'] ?? ''}');
      if (!mounted) return;
      setState(() => open = f);
      toast(context, 'Saved. It applies at the next restart (Overview → Restart).');
      loadList();
    } on ApiError catch (e) {
      if (mounted) setState(() => saveError = e.message);
    }
    if (mounted) setState(() => busy = false);
  }

  void goToLine([int? line]) async {
    line ??= int.tryParse(await askLine() ?? '');
    if (line == null || line < 1) return;
    code.selectLine((line - 1).clamp(0, code.lineCount - 1));
    code.makeCursorCenterIfInvisible();
  }

  Future<String?> askLine() {
    final c = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (d) => AlertDialog(
        backgroundColor: C.bg2,
        title: const Text('Go to line'),
        content: TextField(
            controller: c,
            autofocus: true,
            keyboardType: TextInputType.number,
            onSubmitted: (v) => Navigator.pop(d, v)),
        actions: [FilledButton(onPressed: () => Navigator.pop(d, c.text), child: const Text('Go'))],
      ),
    );
  }

  Future<void> showVersions() async {
    final versions = ((open?['versions'] as List?) ?? []).cast<Map>();
    final pick = await showDialog<Map>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: C.bg2,
        title: const Text('Versions'),
        content: SizedBox(
          width: 460,
          child: versions.isEmpty
              ? const Text('No earlier versions yet. Each save keeps the one before it (the last 20).',
                  style: TextStyle(color: C.muted))
              : ListView(shrinkWrap: true, children: [
                  for (final v in versions)
                    ListTile(
                      leading: const Icon(Icons.history),
                      title: Text(_when(v['at'])),
                      subtitle: Text('${v['by'] ?? ''} · ${v['size']} bytes', style: const TextStyle(color: C.muted)),
                      onTap: () => Navigator.pop(c, v),
                    ),
                ]),
        ),
        actions: [TextButton(onPressed: () => Navigator.pop(c), child: const Text('Close'))],
      ),
    );
    if (pick == null || !mounted) return;
    final text = await widget.api.fileVersion(sid, path!, '${pick['id']}');
    if (!mounted) return;
    final restore = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: C.bg2,
        title: Text('Version from ${_when(pick['at'])}'),
        content: SizedBox(
          width: 640,
          height: 380,
          child: SingleChildScrollView(
              child: SelectableText(text, style: const TextStyle(fontFamily: 'monospace', fontSize: 13))),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Close')),
          if (!readOnly)
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Restore this version')),
        ],
      ),
    );
    if (restore != true || !mounted) return;
    try {
      final f = await widget.api.restoreFile(sid, path!, '${pick['id']}');
      if (!mounted) return;
      setState(() => open = f);
      code.text = '${f['text']}';
      toast(context, 'Restored. The version before it is kept too.');
    } on ApiError catch (e) {
      if (mounted) toast(context, e.message, bad: true);
    }
  }

  Future<void> newFile() async {
    if (!await _okToLeave()) return;
    final arma = widget.s['game'] == 'Arma 3';
    final choices = arma ? armaTemplates : rustTemplates;
    final where = TextEditingController(text: arma ? 'server/mpmissions/MyMission.Stratis/' : 'server/oxide/plugins/');
    String? chosen;
    if (!mounted) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, set) => AlertDialog(
          backgroundColor: C.bg2,
          title: const Text('New file from a template'),
          content: SizedBox(
            width: 500,
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
              for (final name in choices.keys)
                ListTile(
                  dense: true,
                  leading: Icon(chosen == name ? Icons.radio_button_checked : Icons.radio_button_unchecked,
                      color: chosen == name ? C.accent : C.muted),
                  title: Text(name),
                  onTap: () => set(() {
                    chosen = name;
                    final dir =
                        where.text.contains('/') ? where.text.substring(0, where.text.lastIndexOf('/') + 1) : '';
                    where.text = '$dir${name.split(' ').first}';
                  }),
                ),
              const SizedBox(height: 8),
              TextField(
                  controller: where, decoration: const InputDecoration(labelText: 'Save as (inside the instance)')),
            ]),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
            FilledButton(onPressed: chosen == null ? null : () => Navigator.pop(c, true), child: const Text('Create')),
          ],
        ),
      ),
    );
    if (ok != true || chosen == null) return;
    try {
      final f = await widget.api.saveFile(sid, where.text.trim(), choices[chosen]!, '');
      if (!mounted) return;
      setState(() {
        path = '${f['path']}';
        open = f;
        saveError = null;
      });
      code.text = '${f['text']}';
      loadList();
    } on ApiError catch (e) {
      if (mounted) toast(context, e.message, bad: true);
    }
  }

  Future<void> pack(String folder) async {
    try {
      final m = await widget.api.packMission(sid, folder);
      if (mounted) toast(context, m);
      loadList();
    } on ApiError catch (e) {
      if (mounted) toast(context, e.message, bad: true);
    }
  }

  // ---- view ----
  @override
  Widget build(BuildContext context) {
    if (listError != null) {
      return Padding(
          padding: const EdgeInsets.all(24), child: Text(listError!, style: const TextStyle(color: C.muted)));
    }
    return PopScope(
      canPop: !dirty,
      onPopInvokedWithResult: (popped, _) async {
        if (!popped && await _okToLeave() && context.mounted) Navigator.of(context).pop();
      },
      child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        SizedBox(width: 300, child: _list()),
        const VerticalDivider(width: 1),
        Expanded(child: path == null ? _empty() : _editor()),
      ]),
    );
  }

  Widget _empty() => const Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(Icons.description_outlined, size: 44, color: C.dim),
          SizedBox(height: 10),
          Text('Pick a file on the left to open it.', style: TextStyle(color: C.muted)),
        ]),
      );

  Widget _list() {
    final q = search.text.toLowerCase();
    final shown = files.where((f) => '${f['path']}'.toLowerCase().contains(q)).toList();
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 6),
        child: Row(children: [
          Expanded(
            child: TextField(
                controller: search,
                decoration:
                    const InputDecoration(isDense: true, prefixIcon: Icon(Icons.search), hintText: 'Search files'),
                onChanged: (_) => setState(() {})),
          ),
          IconButton(
              tooltip: 'New file from a template', icon: const Icon(Icons.note_add_outlined), onPressed: newFile),
        ]),
      ),
      Expanded(
        child: ListView(children: [
          if (missions.isNotEmpty) ...[
            const Padding(
                padding: EdgeInsets.fromLTRB(14, 8, 14, 4),
                child: Text('Missions (mpmissions)', style: TextStyle(color: C.muted, fontWeight: FontWeight.w600))),
            for (final m in missions)
              ListTile(
                dense: true,
                title: Text('${m['folder']}', overflow: TextOverflow.ellipsis),
                subtitle: Text(m['packed'] == true ? 'packed ${_when(m['packed_at'])}' : 'not packed',
                    style: const TextStyle(color: C.dim, fontSize: 12)),
                trailing: TextButton(onPressed: () => pack('${m['folder']}'), child: const Text('Pack to PBO')),
              ),
            const Divider(),
          ],
          if (q.isEmpty && folder != null)
            ..._browser()
          else
            for (final f in shown)
              ListTile(
                dense: true,
                selected: f['path'] == path,
                selectedTileColor: C.accent.withValues(alpha: .10),
                leading: Icon(f['read_only'] == true ? Icons.lock_outline : _icon('${f['path']}'),
                    size: 18, color: f['read_only'] == true ? C.dim : C.accent),
                title: Text('${f['path']}'.split('/').last, overflow: TextOverflow.ellipsis),
                subtitle: Text(_folder('${f['path']}'),
                    overflow: TextOverflow.ellipsis, style: const TextStyle(color: C.dim, fontSize: 11.5)),
                onTap: () => openFile('${f['path']}'),
              ),
          if (q.isNotEmpty && shown.isEmpty)
            const Padding(
                padding: EdgeInsets.all(16), child: Text('No files found.', style: TextStyle(color: C.muted))),
        ]),
      ),
    ]);
  }

  /// The folder view: where you are, a way up, then folders and files (game data is listed but can't be opened).
  List<Widget> _browser() {
    final f = folder!;
    final here = '${f['dir']}';
    final entries = ((f['entries'] as List?) ?? []).cast<Map<String, dynamic>>();
    return [
      Padding(
        padding: const EdgeInsets.fromLTRB(14, 4, 6, 2),
        child: Row(children: [
          Icon(Icons.folder_open, size: 18, color: C.accent),
          const SizedBox(width: 8),
          Expanded(
              child: Text(here.isEmpty ? 'Instance folder' : here,
                  overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600))),
          if ('${f['start']}' != here)
            IconButton(
                tooltip: 'Back to the server folder',
                icon: const Icon(Icons.home_outlined, size: 18),
                onPressed: () => browse('${f['start']}')),
        ]),
      ),
      if (f['up'] != null)
        ListTile(
            dense: true,
            leading: const Icon(Icons.arrow_upward, size: 18, color: C.muted),
            title: const Text('.. (up one folder)'),
            onTap: () => browse('${f['up']}')),
      for (final e in entries)
        ListTile(
          dense: true,
          selected: e['path'] == path,
          selectedTileColor: C.accent.withValues(alpha: .10),
          leading: Icon(
              e['dir'] == true
                  ? Icons.folder_outlined
                  : e['read_only'] == true
                      ? Icons.lock_outline
                      : e['editable'] == true
                          ? _icon('${e['name']}')
                          : Icons.insert_drive_file_outlined,
              size: 18,
              color: e['dir'] == true || e['editable'] == true ? C.accent : C.dim),
          title: Text('${e['name']}',
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: e['dir'] == true || e['editable'] == true ? null : C.muted)),
          subtitle: e['dir'] == true
              ? null
              : Text(_size((e['size'] as num?) ?? 0), style: const TextStyle(color: C.dim, fontSize: 11.5)),
          onTap: e['dir'] == true
              ? () => browse('${e['path']}')
              : e['editable'] == true
                  ? () => openFile('${e['path']}')
                  : () => ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                      content: Text('${e['name']} isn\'t a text file (or is over 1 MB), so it can\'t be opened here. '
                          'Use Show in folder to open it on the computer.'))),
        ),
      if (entries.isEmpty)
        const Padding(
            padding: EdgeInsets.all(16), child: Text('This folder is empty.', style: TextStyle(color: C.muted))),
    ];
  }

  Widget _editor() {
    final versions = ((open?['versions'] as List?) ?? []).length;
    final err = saveError ?? liveProblem;
    final errLine = int.tryParse(RegExp(r'line (\d+)').firstMatch(err ?? '')?.group(1) ?? '');
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 10, 6),
        child: Row(children: [
          Expanded(
            child: Text(path!,
                overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
          ),
          if (widget.s['folder'] != null)
            IconButton(
                tooltip: isLocal(widget.api) ? 'Show in folder' : 'Copy folder path',
                icon: const Icon(Icons.folder_open_outlined, size: 20),
                onPressed: () => openFolder(context, widget.api, folderOf('${widget.s['folder']}', path!))),
          if (dirty) ...[
            Icon(Icons.circle, size: 9, color: C.warn),
            const SizedBox(width: 6),
            const Text('not saved', style: TextStyle(color: C.warn, fontSize: 12.5)),
          ],
          IconButton(
              tooltip: 'Find and replace (Ctrl+F)', icon: const Icon(Icons.search), onPressed: () => find.findMode()),
          IconButton(tooltip: 'Go to line (Ctrl+G)', icon: const Icon(Icons.format_list_numbered), onPressed: goToLine),
          IconButton(
              tooltip: wrap ? 'Word wrap: on' : 'Word wrap: off',
              icon: Icon(Icons.wrap_text, color: wrap ? C.accent : null),
              onPressed: () => setState(() => wrap = !wrap)),
          IconButton(
              tooltip: 'Smaller text',
              icon: const Icon(Icons.text_decrease),
              onPressed: () => setState(() => fontSize = (fontSize - 1).clamp(10, 24))),
          IconButton(
              tooltip: 'Bigger text',
              icon: const Icon(Icons.text_increase),
              onPressed: () => setState(() => fontSize = (fontSize + 1).clamp(10, 24))),
        ]),
      ),
      if (readOnly)
        _banner(C.info, Icons.lock_outline,
            'HostSpawn writes this file at every start, so it\'s read-only here. Change these settings in the Settings tab.'),
      if (err != null)
        _banner(C.bad, Icons.error_outline, err,
            action: errLine == null
                ? null
                : TextButton(onPressed: () => goToLine(errLine), child: const Text('Go to line'))),
      Expanded(
        child: CallbackShortcuts(
          bindings: {
            const SingleActivator(LogicalKeyboardKey.keyS, control: true): save,
            const SingleActivator(LogicalKeyboardKey.keyG, control: true): goToLine,
          },
          child: CodeEditor(
            controller: code,
            findController: find,
            readOnly: readOnly,
            wordWrap: wrap,
            style: CodeEditorStyle(
              fontSize: fontSize,
              fontFamily: 'monospace',
              backgroundColor: const Color(0xFF0B0F16),
              textColor: C.text,
              selectionColor: C.accent.withValues(alpha: .25),
              cursorColor: C.accent,
              cursorLineColor: const Color(0x0DFFFFFF),
              codeTheme: CodeHighlightTheme(
                  languages: {'lang': CodeHighlightThemeMode(mode: _language(path!))}, theme: atomOneDarkTheme),
            ),
            indicatorBuilder: (context, editing, chunks, notifier) => Row(children: [
              DefaultCodeLineNumber(
                  controller: editing,
                  notifier: notifier,
                  textStyle: TextStyle(color: C.dim, fontSize: fontSize - 1),
                  focusedTextStyle: TextStyle(color: C.accent, fontSize: fontSize - 1)),
              DefaultCodeChunkIndicator(width: 18, controller: chunks, notifier: notifier),
            ]),
            findBuilder: (context, controller, ro) => _FindBar(controller: controller, readOnly: ro),
          ),
        ),
      ),
      Container(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 10),
        decoration: const BoxDecoration(border: Border(top: BorderSide(color: C.line))),
        child: Row(children: [
          FilledButton.icon(
              onPressed: readOnly || !dirty || busy ? null : save,
              icon: const Icon(Icons.save_outlined, size: 18),
              label: const Text('Save')),
          const SizedBox(width: 8),
          OutlinedButton(
              onPressed: dirty ? () => setState(() => code.text = '${open!['text']}') : null,
              child: const Text('Undo changes')),
          const SizedBox(width: 8),
          OutlinedButton.icon(
              onPressed: showVersions, icon: const Icon(Icons.history, size: 18), label: Text('Versions ($versions)')),
          const SizedBox(width: 12),
          const Expanded(
            child: Text('Checked before saving · the old version is kept',
                textAlign: TextAlign.right,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: C.dim, fontSize: 12)),
          ),
        ]),
      ),
    ]);
  }

  Widget _banner(Color color, IconData icon, String text, {Widget? action}) => Container(
        margin: const EdgeInsets.fromLTRB(12, 0, 12, 8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
            color: color.withValues(alpha: .12),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: color.withValues(alpha: .4))),
        child: Row(children: [
          Icon(icon, size: 18, color: color),
          const SizedBox(width: 10),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 13))),
          if (action != null) action,
        ]),
      );
}

/// Find and replace bar (Ctrl+F opens it; the editor does the searching).
class _FindBar extends StatelessWidget implements PreferredSizeWidget {
  const _FindBar({required this.controller, required this.readOnly});
  final CodeFindController controller;
  final bool readOnly;
  @override
  Size get preferredSize => Size.fromHeight(controller.value == null ? 0 : (controller.value!.replaceMode ? 92 : 48));
  @override
  Widget build(BuildContext context) {
    final v = controller.value;
    if (v == null) return const SizedBox.shrink();
    final r = v.result;
    final count = r == null ? '' : (r.matches.isEmpty ? 'none' : '${r.index + 1} of ${r.matches.length}');
    return Container(
      color: C.bg2,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      child: Column(children: [
        Row(children: [
          Expanded(
            child: TextField(
                controller: controller.findInputController,
                focusNode: controller.findInputFocusNode,
                decoration: const InputDecoration(isDense: true, hintText: 'Find')),
          ),
          SizedBox(width: 70, child: Text(count, textAlign: TextAlign.center, style: const TextStyle(color: C.muted))),
          IconButton(
              tooltip: 'Match case', icon: const Icon(Icons.text_fields), onPressed: controller.toggleCaseSensitive),
          IconButton(tooltip: 'Previous', icon: const Icon(Icons.arrow_upward), onPressed: controller.previousMatch),
          IconButton(tooltip: 'Next', icon: const Icon(Icons.arrow_downward), onPressed: controller.nextMatch),
          if (!readOnly)
            IconButton(tooltip: 'Replace', icon: const Icon(Icons.find_replace), onPressed: controller.toggleMode),
          IconButton(tooltip: 'Close', icon: const Icon(Icons.close), onPressed: controller.close),
        ]),
        if (v.replaceMode && !readOnly)
          Row(children: [
            Expanded(
              child: TextField(
                  controller: controller.replaceInputController,
                  focusNode: controller.replaceInputFocusNode,
                  decoration: const InputDecoration(isDense: true, hintText: 'Replace with')),
            ),
            TextButton(onPressed: controller.replaceMatch, child: const Text('Replace')),
            TextButton(onPressed: controller.replaceAllMatches, child: const Text('Replace all')),
          ]),
      ]),
    );
  }
}

// ---- helpers ----
String _size(num b) => b < 1024
    ? '$b B'
    : b < 1 << 20
        ? '${(b / 1024).toStringAsFixed(0)} KB'
        : b < 1 << 30
            ? '${(b / (1 << 20)).toStringAsFixed(1)} MB'
            : '${(b / (1 << 30)).toStringAsFixed(1)} GB';

String _folder(String p) => p.contains('/') ? p.substring(0, p.lastIndexOf('/')) : '';
String _ext(String p) => p.contains('.') ? p.substring(p.lastIndexOf('.')).toLowerCase() : '';

IconData _icon(String p) => switch (_ext(p)) {
      '.sqf' || '.cs' || '.lua' || '.js' || '.py' || '.sh' => Icons.code,
      '.json' || '.xml' || '.yaml' || '.yml' || '.toml' => Icons.data_object,
      '.txt' || '.md' => Icons.article_outlined,
      _ => Icons.tune,
    };

dynamic _language(String p) => switch (_ext(p)) {
      '.json' => langJson,
      '.cs' => langCsharp,
      '.lua' => langLua,
      '.xml' || '.html' => langXml,
      '.js' => langJavascript,
      '.yaml' || '.yml' => langYaml,
      '.sh' => langBash,
      '.py' => langPython,
      '.sqf' || '.hpp' || '.ext' || '.sqm' => langCpp, // C-like: close enough for colours
      _ => langIni,
    };

String _when(dynamic t) {
  if (t is! num) return '–';
  final d = DateTime.fromMillisecondsSinceEpoch((t * 1000).round());
  String two(int n) => n.toString().padLeft(2, '0');
  return '${d.day}/${d.month}/${d.year} ${two(d.hour)}:${two(d.minute)}';
}

/// Definite mistakes, checked as you type (the agent checks again on save): JSON, and SQF brackets and quotes.
String? problemIn(String path, String text) {
  final ext = _ext(path);
  if (ext == '.json' && text.trim().isNotEmpty) {
    try {
      jsonDecode(text);
    } on FormatException catch (e) {
      final line =
          e.offset == null ? null : '\n'.allMatches(text.substring(0, e.offset!.clamp(0, text.length))).length + 1;
      return 'Not valid JSON: ${e.message}${line == null ? '' : ' (line $line)'}';
    }
  }
  if (ext == '.sqf') return sqfProblem(text);
  return null;
}

String? sqfProblem(String text) {
  const pairs = {')': '(', ']': '[', '}': '{'};
  final stack = <(String, int)>[];
  var i = 0, line = 1;
  while (i < text.length) {
    final c = text[i];
    if (c == '\n') {
      line++;
    } else if (c == '"' || c == "'") {
      final start = line;
      i++;
      while (true) {
        if (i >= text.length) return 'Text in quotes started on line $start is never closed';
        if (text[i] == c) {
          if (i + 1 < text.length && text[i + 1] == c) {
            i += 2;
            continue;
          }
          break;
        }
        if (text[i] == '\n') {
          line++;
        }
        i++;
      }
    } else if (text.startsWith('//', i)) {
      while (i < text.length && text[i] != '\n') {
        i++;
      }
      continue;
    } else if (text.startsWith('/*', i)) {
      final end = text.indexOf('*/', i + 2);
      if (end < 0) return 'A comment started on line $line with /* is never closed with */';
      line += '\n'.allMatches(text.substring(i, end)).length;
      i = end + 2;
      continue;
    } else if ('([{'.contains(c)) {
      stack.add((c, line));
    } else if (')]}'.contains(c)) {
      if (stack.isEmpty) return 'An extra $c on line $line';
      if (stack.last.$1 != pairs[c]) {
        return '$c on line $line doesn\'t match the ${stack.last.$1} opened on line ${stack.last.$2}';
      }
      stack.removeLast();
    }
    i++;
  }
  if (stack.isNotEmpty) return 'The ${stack.last.$1} opened on line ${stack.last.$2} is never closed';
  return null;
}

// ---- starter templates ----
const armaTemplates = {
  'init.sqf (runs on every machine at mission start)':
      '''// init.sqf: runs on the server and every player's game when the mission starts.
// Keep it light; put server-only setup in initServer.sqf.

if (hasInterface) then {
    // player-side setup: welcome message once the player has spawned
    waitUntil { !isNull player };
    hint format ["Welcome, %1!", name player];
};
''',
  'initServer.sqf (runs on the server only)': '''// initServer.sqf: runs once on the server when the mission starts.

// Time and weather
setDate [2035, 6, 24, 12, 0];
0 setOvercast 0.3;
forceWeatherChange;

// Remove dead bodies and wrecks after a while (seconds)
corpseRemovalMinTime = 300;
wreckRemovalMinTime = 600;
''',
  'description.ext (mission settings)': '''// description.ext: mission settings read by Arma 3.
author = "Your community";
onLoadName = "My mission";
onLoadMission = "Welcome to the server";

// Players respawn at their respawn marker after 10 seconds
respawn = 3;            // 3 = BASE (respawn markers)
respawnDelay = 10;
respawnOnStart = -1;    // don't respawn at the start

// Keep the server tidy
disabledAI = 1;         // no AI in empty player slots
enableDebugConsole = 1; // admins (logged in) only

class Header
{
    gameType = Coop;    // Coop, CTI, DM, TDM, RPG, Sandbox ...
    minPlayers = 1;
    maxPlayers = 40;
};
''',
  'onPlayerRespawn.sqf (runs when a player respawns)':
      '''// onPlayerRespawn.sqf: runs on the player's machine each time they respawn.
params ["_newUnit", "_oldUnit", "_respawn", "_respawnDelay"];

// give back a basic loadout
removeAllWeapons _newUnit;
_newUnit addWeapon "arifle_MX_F";
_newUnit addMagazines ["30Rnd_65x39_caseless_mag", 4];
''',
};

const rustTemplates = {
  'MyPlugin.cs (an empty Oxide plugin)': '''using Oxide.Core;

namespace Oxide.Plugins
{
    [Info("MyPlugin", "Your name", "1.0.0")]
    [Description("What this plugin does, in one line.")]
    public class MyPlugin : RustPlugin
    {
        // Runs when the plugin loads
        private void Init()
        {
            Puts("MyPlugin loaded");
        }

        // Runs when a player joins
        private void OnPlayerConnected(BasePlayer player)
        {
            SendReply(player, "Welcome to the server, " + player.displayName + "!");
        }

        // A chat command: /hello
        [ChatCommand("hello")]
        private void HelloCommand(BasePlayer player, string command, string[] args)
        {
            SendReply(player, "Hello!");
        }
    }
}
''',
  'MyPlugin.json (a plugin config example)': '''{
  "Welcome message": "Welcome to the server!",
  "Show welcome message": true,
  "Cooldown (seconds)": 600
}
''',
};
