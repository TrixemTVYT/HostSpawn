// Report a bug: copies safe details (HostSpawn version, system, instances and their state) and opens the bug form on
// GitHub. Leaves out passwords, addresses, folders and player names: the GitHub page is public.
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme.dart';
import 'open_folder.dart' show openUrl;

const bugFormUrl = 'https://github.com/TrixemTVYT/HostSpawn/issues/new?template=bug_report.yml';

/// The safe details: no names the owner chose, no addresses, paths or players.
String bugDetails(String version, List<Map<String, dynamic>> servers) {
  final os = Platform.isLinux
      ? 'Linux'
      : Platform.isWindows
          ? 'Windows'
          : Platform.operatingSystem;
  final lines = [
    'HostSpawn app: $version',
    'This computer: $os (${Platform.operatingSystemVersion.split(' ').take(3).join(' ')})',
    'Instances: ${servers.isEmpty ? 'none' : ''}',
    for (final s in servers)
      '- ${s['game'] ?? '?'} · ${s['mode_name'] ?? '?'} · ${(s['status'] as Map?)?['state'] ?? '?'}'
          '${((s['status'] as Map?)?['flags'] as List?)?.isNotEmpty == true ? ' · ${((s['status'] as Map)['flags'] as List).length} warning(s)' : ''}',
  ];
  return lines.join('\n');
}

Future<void> showBugReport(BuildContext context, String version, List<Map<String, dynamic>> servers) {
  final details = bugDetails(version, servers);
  return showDialog<void>(
    context: context,
    builder: (c) => AlertDialog(
      backgroundColor: C.bg2,
      title: const Text('Report a bug'),
      content: SizedBox(
        width: 500,
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text(
                'Bugs go on GitHub, where everyone can see them get fixed. It takes a free GitHub account.\n'
                '1. Press Copy details.\n'
                '2. Press Open the bug form, say what happened, and paste the details in "Details from HostSpawn".',
                style: TextStyle(color: C.muted, height: 1.4)),
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(8)),
              child: SelectableText(details,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 12, color: C.muted)),
            ),
            const SizedBox(height: 8),
            const Text(
                'No passwords, addresses, folder names or player names are included. Never paste those: the '
                'page is public. Need help rather than a fix? Use Get help in the HostSpawn Discord.',
                style: TextStyle(color: C.dim, fontSize: 12)),
          ]),
        ),
      ),
      actions: [
        TextButton.icon(
            onPressed: () {
              Clipboard.setData(ClipboardData(text: details));
              ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Details copied')));
            },
            icon: const Icon(Icons.copy, size: 16),
            label: const Text('Copy details')),
        FilledButton.icon(
            onPressed: () async {
              if (!await openUrl(bugFormUrl)) {
                await Clipboard.setData(const ClipboardData(text: bugFormUrl));
                if (context.mounted) {
                  ScaffoldMessenger.of(context)
                      .showSnackBar(const SnackBar(content: Text('Link copied: paste it into your browser')));
                }
              }
            },
            icon: const Icon(Icons.open_in_new, size: 16),
            label: const Text('Open the bug form')),
      ],
    ),
  );
}
