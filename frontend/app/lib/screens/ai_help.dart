// Copy for AI: copies a guide to this instance (game, where things are, rules, Server Doctor findings and the newest
// log lines) to paste into the owner's own AI chat. HostSpawn removes passwords and players' details first.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../theme.dart';
import 'players.dart' show toast;

class CopyForAiButton extends StatefulWidget {
  const CopyForAiButton({super.key, required this.api, required this.server});
  final Api api;
  final String server;
  @override
  State<CopyForAiButton> createState() => _CopyForAiButtonState();
}

class _CopyForAiButtonState extends State<CopyForAiButton> {
  bool busy = false;

  Future<void> copy() async {
    setState(() => busy = true);
    try {
      final text = await widget.api.aiHelp(widget.server);
      await Clipboard.setData(ClipboardData(text: text));
      if (mounted) {
        toast(
            context,
            'Copied. Paste it into your AI chat (Claude, ChatGPT, Copilot...) and type your question at the end. '
            'Passwords and players\' details were left out.');
      }
    } catch (e) {
      if (mounted) toast(context, '$e', bad: true);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Tooltip(
        message: 'Copy a guide to this instance for your own AI assistant',
        child: TextButton.icon(
          onPressed: busy ? null : copy,
          icon: busy
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
              : Icon(Icons.auto_awesome_outlined, size: 18, color: C.accent),
          label: const Text('Copy for AI'),
          style: TextButton.styleFrom(foregroundColor: C.text),
        ),
      );
}
