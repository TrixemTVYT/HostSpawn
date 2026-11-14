// An instance's Tools tab: Spyglass (Altis Life), Copy for AI, Open folder and PC check, each on its own card.
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'ai_help.dart';
import 'open_folder.dart';
import 'pc_check.dart';
import 'spyglass.dart';

class ToolsTab extends StatelessWidget {
  const ToolsTab({super.key, required this.api, required this.me, required this.s, this.onChanged});
  final Api api;
  final Me me;
  final Map<String, dynamic> s;

  /// Called after a Spyglass change, so the instance page refreshes.
  final VoidCallback? onChanged;

  @override
  Widget build(BuildContext context) {
    final id = '${s['id']}';
    final spyglass = me.isAdmin && s['controllable'] == true && ((s['actions'] as List?) ?? []).contains('spyglass');
    final folder = s['folder'] as String?;
    final local = isLocal(api);
    return ListView(padding: const EdgeInsets.all(20), children: [
      if (spyglass) ...[
        SpyglassCard(api: api, server: id, onChanged: onChanged),
        const SizedBox(height: 14),
      ],
      Wrap(spacing: 14, runSpacing: 14, children: [
        if (me.isAdmin)
          _Tool(
            icon: Icons.auto_awesome_outlined,
            title: 'Copy for AI',
            status: 'A guide to this instance for your own AI chat',
            text: 'Copies the game, where its files are, rules for changing them, the Server Doctor\'s findings and '
                'the newest log lines. Paste it into Claude, ChatGPT or Copilot and ask your question. Passwords '
                'and players\' details are left out.',
            button: CopyForAiButton(api: api, server: id),
          ),
        if (folder != null)
          _Tool(
            icon: Icons.folder_open_outlined,
            title: local ? 'Open folder' : 'Copy folder path',
            status: 'This instance\'s files on the HostSpawn computer',
            text: local
                ? 'Opens the instance folder in your file manager.'
                : 'This device isn\'t the HostSpawn computer, so the folder can\'t open here: copy its path instead.',
            button: OpenFolderButton(api: api, dir: folder),
          ),
        _Tool(
          icon: Icons.health_and_safety_outlined,
          title: 'PC check',
          status: 'Can this machine run a server?',
          text: 'Compares this machine with what each game\'s server needs. A guide only.',
          button: TextButton.icon(
            onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PcCheckScreen(api: api))),
            icon: Icon(Icons.play_arrow_rounded, size: 18, color: C.accent),
            label: const Text('Run PC check'),
            style: TextButton.styleFrom(foregroundColor: C.text),
          ),
        ),
      ]),
    ]);
  }
}

class _Tool extends StatelessWidget {
  const _Tool(
      {required this.icon, required this.title, required this.status, required this.text, required this.button});
  final IconData icon;
  final String title, status, text;
  final Widget button;
  @override
  Widget build(BuildContext context) => SizedBox(
        width: 380,
        child: Card(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(18, 14, 12, 12),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              CardHeader(icon: icon, title: title, status: status, color: C.accent, statusColor: C.muted),
              const SizedBox(height: 10),
              Text(text, style: const TextStyle(color: C.muted, fontSize: 13)),
              const SizedBox(height: 8),
              Align(alignment: Alignment.centerLeft, child: button),
            ]),
          ),
        ),
      );
}
