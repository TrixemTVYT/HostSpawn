// Account menu → Time zone: the zone HostSpawn uses for scheduled restarts, backups and wipes (admins change it).
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';

Future<void> showTimeZone(BuildContext context, Api api, bool isAdmin) async {
  Map<String, dynamic> s;
  try {
    s = await api.appSettings();
  } on ApiError catch (e) {
    if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    return;
  }
  if (!context.mounted) return;
  final zones = ((s['timezones'] as List?) ?? []).cast<String>();
  String pick = '${s['timezone'] ?? 'UTC'}';
  await showDialog<void>(
    context: context,
    builder: (c) => StatefulBuilder(
      builder: (c, set) => AlertDialog(
        backgroundColor: C.bg2,
        title: const Text('Time zone'),
        content: SizedBox(
          width: 440,
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text(
                'Scheduled restarts, daily backups and Rust wipes happen at the times you set, in this time zone. '
                'Changing it moves when they happen.',
                style: TextStyle(color: C.muted, height: 1.4)),
            const SizedBox(height: 14),
            if (isAdmin)
              Autocomplete<String>(
                initialValue: TextEditingValue(text: pick),
                optionsBuilder: (v) => zones.where((z) => z.toLowerCase().contains(v.text.toLowerCase())).take(30),
                onSelected: (z) => set(() => pick = z),
                fieldViewBuilder: (ctx, ctl, focus, done) => TextField(
                    controller: ctl,
                    focusNode: focus,
                    decoration: const InputDecoration(labelText: 'Time zone', hintText: 'Type a city, e.g. London'),
                    onChanged: (v) => set(() => pick = v.trim())),
              )
            else
              Text(pick, style: const TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            Text('This computer\'s own time zone: ${s['system_timezone'] ?? '?'}',
                style: const TextStyle(color: C.dim, fontSize: 12)),
          ]),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: Text(isAdmin ? 'Cancel' : 'Close')),
          if (isAdmin)
            FilledButton(
                onPressed: !zones.contains(pick) || pick == s['timezone']
                    ? null
                    : () async {
                        try {
                          await api.saveAppSettings({'timezone': pick});
                          if (c.mounted) Navigator.pop(c);
                          if (context.mounted) {
                            ScaffoldMessenger.of(context)
                                .showSnackBar(SnackBar(content: Text('Time zone set to $pick')));
                          }
                        } on ApiError catch (e) {
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
                          }
                        }
                      },
                child: const Text('Save')),
        ],
      ),
    ),
  );
}
