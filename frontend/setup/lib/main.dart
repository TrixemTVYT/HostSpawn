// HostSpawn Setup: the installer window. Welcome → Terms → Options → Check → Install → Done.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'installer.dart';
import 'look.dart';

void main() => runApp(SetupApp(installer: Installer(releaseDir())));

class SetupApp extends StatelessWidget {
  const SetupApp({super.key, required this.installer});
  final Installer installer;
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'HostSpawn Setup',
    debugShowCheckedModeBanner: false,
    theme: look(),
    home: Setup(installer: installer),
  );
}

enum Page { welcome, terms, options, check, install, done }

const _titles = {
  Page.welcome: 'Welcome',
  Page.terms: 'Terms',
  Page.options: 'Options',
  Page.check: 'Check',
  Page.install: 'Install',
  Page.done: 'Done',
};

class Setup extends StatefulWidget {
  const Setup({super.key, required this.installer});
  final Installer installer;
  @override
  State<Setup> createState() => _SetupState();
}

class _SetupState extends State<Setup> {
  Page page = Page.welcome;
  bool accepted = false, running = false;
  final opts = Options();
  late final folder = TextEditingController(text: opts.folder);
  final log = <Line>[];
  int step = 0, code = -1;
  String openCmd = '';

  Installer get inst => widget.installer;
  List<Line> get problems => log.where((l) => l.kind == Kind.fail).toList();
  List<Line> get warnings => log.where((l) => l.kind == Kind.warn).toList();

  void _onLine(Line l) {
    if (!mounted) return;
    setState(() {
      if (l.kind == Kind.step) step = l.step;
      if (l.kind == Kind.text && l.text.startsWith('OPEN: ')) {
        openCmd = l.text.substring(6);
        return;
      }
      if (l.text.isNotEmpty) log.add(l);
    });
  }

  Future<void> runCheck() async {
    setState(() {
      page = Page.check;
      running = true;
      log.clear();
      code = -1;
    });
    final c = await inst.check(opts, _onLine);
    if (mounted) {
      setState(() {
        running = false;
        code = c;
      });
    }
  }

  Future<void> runInstall() async {
    setState(() {
      page = Page.install;
      running = true;
      log.clear();
      step = 0;
      code = -1;
    });
    final c = await inst.install(opts, _onLine);
    if (mounted) {
      setState(() {
        running = false;
        code = c;
        page = Page.done;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _Rail(page: page),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(36, 32, 36, 24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(child: _body()),
                  const SizedBox(height: 16),
                  _buttons(),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _body() => switch (page) {
    Page.welcome => _welcome(),
    Page.terms => _terms(),
    Page.options => _options(),
    Page.check => _results(title: 'Checking this computer', subtitle: 'Nothing is changed yet.'),
    Page.install => _results(title: 'Installing HostSpawn', subtitle: 'This takes a minute or two.'),
    Page.done => _done(),
  };

  Widget _buttons() {
    Widget back(Page p) =>
        TextButton(onPressed: running ? null : () => setState(() => page = p), child: const Text('Back'));
    final next = switch (page) {
      Page.welcome => FilledButton(onPressed: () => setState(() => page = Page.terms), child: const Text('Next')),
      Page.terms => FilledButton(
        onPressed: accepted ? () => setState(() => page = Page.options) : null,
        child: const Text('I accept, next'),
      ),
      Page.options => FilledButton(
        onPressed: () {
          opts.folder = folder.text.trim();
          runCheck();
        },
        child: const Text('Check this computer'),
      ),
      Page.check => FilledButton(
        onPressed: running || code != 0 ? null : runInstall,
        child: Text(running ? 'Checking…' : 'Install'),
      ),
      Page.install => const FilledButton(onPressed: null, child: Text('Installing…')),
      Page.done =>
        code == 0
            ? FilledButton.icon(
                onPressed: openCmd.isEmpty
                    ? null
                    : () async {
                        await inst.open(openCmd);
                        SystemNavigator.pop();
                      },
                icon: const Icon(Icons.rocket_launch_outlined),
                label: const Text('Open HostSpawn'),
              )
            : FilledButton(onPressed: () => setState(() => page = Page.options), child: const Text('Try again')),
    };
    return Row(
      children: [
        if (page == Page.terms) back(Page.welcome),
        if (page == Page.options) back(Page.terms),
        if (page == Page.check) back(Page.options),
        const Spacer(),
        if (page != Page.install)
          TextButton(
            onPressed: running ? null : () => SystemNavigator.pop(),
            child: Text(page == Page.done ? 'Close' : 'Cancel'),
          ),
        const SizedBox(width: 10),
        next,
      ],
    );
  }

  Widget _welcome() => ListView(
    children: [
      Text(
        'HostSpawn',
        style: TextStyle(fontSize: 34, fontWeight: FontWeight.w800, color: L.accent),
      ),
      Text('by TrixemTV · Setup${inst.version.isEmpty ? '' : ' · version ${inst.version}'}', style: const TextStyle(color: L.muted)),
      const SizedBox(height: 22),
      const Text(
        'Set up and run your own game servers (Rust, Arma Reforger, Arma 3) on this computer, '
        'with a watchdog, backups, updates and a Server Doctor that explains problems in plain English.',
        style: TextStyle(fontSize: 16),
      ),
      const SizedBox(height: 22),
      const Text('Is it safe?', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
      const SizedBox(height: 8),
      for (final (icon, text) in const [
        (
          Icons.verified_outlined,
          'Every file is checked against the release\'s checksums (and HostSpawn\'s signature, when the release '
              'is signed) before anything is installed.',
        ),
        (
          Icons.person_outline,
          'It installs for your user only, in one folder. It only asks for your password to '
              'add missing system packages, and shows you which ones first.',
        ),
        (Icons.shield_outlined, 'Firewall and security settings are never changed: HostSpawn shows you the commands.'),
        (Icons.code, 'The source is open to read, and you can build it yourself instead (docs/BUILDING.md).'),
        (Icons.delete_outline, 'Uninstalling is one command, and it keeps your servers unless you say otherwise.'),
      ])
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, size: 20, color: L.accent),
              const SizedBox(width: 12),
              Expanded(
                child: Text(text, style: const TextStyle(color: L.muted)),
              ),
            ],
          ),
        ),
    ],
  );

  Widget _terms() => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      const Text('Terms of use', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
      const SizedBox(height: 6),
      const Text('Please read them. Nothing is installed until you accept.', style: TextStyle(color: L.muted)),
      const SizedBox(height: 14),
      Expanded(
        child: Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: L.panel,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: L.line),
          ),
          child: SingleChildScrollView(child: SelectableText(inst.terms, style: const TextStyle(height: 1.45))),
        ),
      ),
      CheckboxListTile(
        contentPadding: EdgeInsets.zero,
        controlAffinity: ListTileControlAffinity.leading,
        value: accepted,
        onChanged: (v) => setState(() => accepted = v ?? false),
        title: const Text('I have read and accept the terms'),
      ),
    ],
  );

  Widget _options() => ListView(
    children: [
      const Text('Options', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
      const SizedBox(height: 18),
      TextField(
        controller: folder,
        decoration: const InputDecoration(labelText: 'Install folder'),
      ),
      const SizedBox(height: 6),
      const Text(
        'Your settings, servers and backups are kept here too. Upgrading keeps them.',
        style: TextStyle(color: L.dim, fontSize: 12),
      ),
      const SizedBox(height: 14),
      SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: const Text('Start HostSpawn automatically'),
        subtitle: const Text('Keeps your servers looked after after a reboot.', style: TextStyle(color: L.muted)),
        value: opts.autostart,
        onChanged: (v) => setState(() => opts.autostart = v),
      ),
      SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: const Text('Add HostSpawn to the desktop and app menu'),
        value: opts.shortcut,
        onChanged: (v) => setState(() => opts.shortcut = v),
      ),
      SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: const Text('Install missing system packages'),
        subtitle: const Text('For example tmux. A password window asks you first.', style: TextStyle(color: L.muted)),
        value: opts.packages,
        onChanged: (v) => setState(() => opts.packages = v),
      ),
    ],
  );

  Widget _results({required String title, required String subtitle}) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Text(title, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
      const SizedBox(height: 6),
      Text(
        running
            ? subtitle
            : code == 0
            ? 'All checks passed${warnings.isEmpty ? '' : ' (see the notes marked !)'}. Ready to install.'
            : 'It can\'t be installed yet: ${problems.isEmpty ? 'see below' : problems.last.text}',
        style: TextStyle(color: running || code == 0 ? L.muted : L.bad),
      ),
      const SizedBox(height: 12),
      if (page == Page.install) ...[
        LinearProgressIndicator(
          value: step == 0 ? null : step / 6,
          minHeight: 6,
          borderRadius: BorderRadius.circular(4),
        ),
        const SizedBox(height: 12),
      ] else if (running)
        const LinearProgressIndicator(minHeight: 4),
      Expanded(child: _LogList(log: log)),
    ],
  );

  Widget _done() {
    final ok = code == 0;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Icon(ok ? Icons.check_circle : Icons.error_outline, size: 54, color: ok ? L.ok : L.bad),
        const SizedBox(height: 12),
        Text(
          ok ? 'HostSpawn is installed' : 'Setup didn\'t finish',
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 8),
        Text(
          ok
              ? 'Open it now. The first time, it asks you to create your owner account.'
              : (problems.isEmpty ? 'See the details below.' : problems.last.text),
          textAlign: TextAlign.center,
          style: TextStyle(color: ok ? L.muted : L.bad),
        ),
        const SizedBox(height: 16),
        Expanded(child: _LogList(log: log)),
        if (!ok)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: () => Clipboard.setData(ClipboardData(text: log.map((l) => l.text).join('\n'))),
              icon: const Icon(Icons.copy, size: 16),
              label: const Text('Copy the details (to ask for help)'),
            ),
          ),
      ],
    );
  }
}

class _LogList extends StatelessWidget {
  const _LogList({required this.log});
  final List<Line> log;
  @override
  Widget build(BuildContext context) => Container(
    decoration: BoxDecoration(
      color: L.panel,
      borderRadius: BorderRadius.circular(12),
      border: Border.all(color: L.line),
    ),
    child: ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: log.length,
      itemBuilder: (_, i) {
        final l = log[i];
        final (icon, color) = switch (l.kind) {
          Kind.step => (Icons.chevron_right, L.accent),
          Kind.ok => (Icons.check, L.ok),
          Kind.warn => (Icons.info_outline, L.warn),
          Kind.fail => (Icons.close, L.bad),
          Kind.text => (null, L.dim),
        };
        return Padding(
          padding: EdgeInsets.only(top: l.kind == Kind.step && i > 0 ? 10 : 2, bottom: 2),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(width: 22, child: icon == null ? null : Icon(icon, size: 16, color: color)),
              Expanded(
                child: Text(
                  l.text,
                  style: TextStyle(
                    color: l.kind == Kind.text ? L.dim : null,
                    fontWeight: l.kind == Kind.step ? FontWeight.w700 : null,
                    fontSize: l.kind == Kind.text ? 12.5 : 14,
                  ),
                ),
              ),
            ],
          ),
        );
      },
    ),
  );
}

class _Rail extends StatelessWidget {
  const _Rail({required this.page});
  final Page page;
  @override
  Widget build(BuildContext context) => Container(
    width: 210,
    color: L.bg2,
    padding: const EdgeInsets.fromLTRB(22, 30, 16, 20),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(Icons.dns_outlined, color: L.accent),
            const SizedBox(width: 8),
            const Flexible(
              child: Text(
                'HostSpawn Setup',
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontWeight: FontWeight.w700),
              ),
            ),
          ],
        ),
        const SizedBox(height: 30),
        for (final p in Page.values)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 7),
            child: Row(
              children: [
                Icon(
                  p.index < page.index
                      ? Icons.check_circle
                      : p == page
                      ? Icons.radio_button_checked
                      : Icons.radio_button_unchecked,
                  size: 18,
                  color: p.index <= page.index ? L.accent : L.dim,
                ),
                const SizedBox(width: 10),
                Text(
                  _titles[p]!,
                  style: TextStyle(
                    color: p == page ? L.text : (p.index < page.index ? L.muted : L.dim),
                    fontWeight: p == page ? FontWeight.w700 : null,
                  ),
                ),
              ],
            ),
          ),
      ],
    ),
  );
}
