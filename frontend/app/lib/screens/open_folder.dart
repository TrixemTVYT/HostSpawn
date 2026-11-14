// Open an instance's folder (or a file's folder) in the computer's file manager. Only when the app is talking to
// HostSpawn on this same computer; a paired device elsewhere gets Copy path instead, because the folder isn't there.
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../theme.dart';
import 'players.dart' show toast;

/// HostSpawn's engine runs on Linux, so its folders are on this computer only when this is Linux and the address is
/// this computer. (A Windows app reaching a server through an SSH tunnel also uses 127.0.0.1, but the folders are
/// on the server.)
bool isLocal(Api api) => Platform.isLinux && const {'127.0.0.1', 'localhost', '::1'}.contains(api.base.host);

/// Opens a web address in the computer's browser. Returns false if nothing could open it.
Future<bool> openUrl(String url) async {
  try {
    final r = Platform.isWindows
        ? await Process.run('cmd', ['/c', 'start', '', url]) // "" is start's window title
        : await Process.run(Platform.isMacOS ? 'open' : 'xdg-open', [url]);
    return r.exitCode == 0;
  } on ProcessException {
    return false;
  }
}

/// The folder that holds [rel] (a path inside the instance), or the instance folder itself when [rel] is empty.
String folderOf(String root, [String rel = '']) {
  if (rel.isEmpty) return root;
  final i = rel.lastIndexOf('/');
  return i < 0 ? root : '$root/${rel.substring(0, i)}';
}

Future<void> openFolder(BuildContext context, Api api, String dir) async {
  if (!isLocal(api)) {
    await Clipboard.setData(ClipboardData(text: dir));
    if (context.mounted) toast(context, 'Path copied: this folder is on the HostSpawn computer, not this one.');
    return;
  }
  final cmd = Platform.isMacOS
      ? 'open'
      : Platform.isWindows
          ? 'explorer'
          : 'xdg-open';
  try {
    final r = await Process.run(cmd, [dir]);
    if (r.exitCode != 0 && !Platform.isWindows && context.mounted) {
      await Clipboard.setData(ClipboardData(text: dir));
      if (context.mounted) toast(context, 'No file manager answered, so the path is copied instead: $dir');
    }
  } on ProcessException {
    await Clipboard.setData(ClipboardData(text: dir));
    if (context.mounted) toast(context, 'No file manager found, so the path is copied instead: $dir');
  }
}

/// A small "Open folder" button (or "Copy path" from another device). Hidden when [dir] is null.
class OpenFolderButton extends StatelessWidget {
  const OpenFolderButton({super.key, required this.api, required this.dir, this.label = 'Open folder'});
  final Api api;
  final String? dir;
  final String label;
  @override
  Widget build(BuildContext context) {
    if (dir == null) return const SizedBox.shrink();
    final local = isLocal(api);
    return TextButton.icon(
      onPressed: () => openFolder(context, api, dir!),
      icon: Icon(local ? Icons.folder_open_outlined : Icons.copy, size: 18, color: C.accent),
      label: Text(local ? label : 'Copy path'),
      style: TextButton.styleFrom(foregroundColor: C.text),
    );
  }
}
