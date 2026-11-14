// New server wizard: game → server type → settings → review and terms → start the setup.
import 'dart:math';

import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'pc_check.dart';
import 'setup_job.dart';

class NewServerScreen extends StatefulWidget {
  const NewServerScreen({super.key, required this.api, required this.me, this.community});
  final Api api;
  final Me me;
  final String? community;
  @override
  State<NewServerScreen> createState() => _NewServerScreenState();
}

class _NewServerScreenState extends State<NewServerScreen> {
  Map<String, dynamic>? cat;
  Map<String, String> pc = {}; // game id -> PC check verdict (ok, tight, no)
  String? termsVersion, termsText, error;
  int step = 0; // 0 game, 1 type, 2 settings, 3 review
  String? game, mode;
  final name = TextEditingController(), steamUser = TextEditingController();
  final Map<String, num> numbers = {};
  bool oxide = false, recommendedRestarts = true, rotation = false, accepted = false, starting = false;
  String? map, release, wipeMode = 'monthly', wipeBp = 'forced';
  List<String>? releases;
  // one ID per wizard: pressing Create twice (or a slow network retry) can't make two servers
  final requestId = List.generate(16, (_) => Random.secure().nextInt(16).toRadixString(16)).join();

  @override
  void initState() {
    super.initState();
    () async {
      try {
        final c = await widget.api.catalog();
        final t = await widget.api.terms();
        if (mounted) {
          setState(() {
            cat = c;
            termsVersion = t['version'] as String?;
            termsText = t['text'] as String?;
          });
        }
        final h = await widget.api.hostCheck();
        if (mounted) {
          setState(() =>
              pc = {for (final g in ((h['games'] as List?) ?? const []).cast<Map>()) '${g['id']}': '${g['verdict']}'});
        }
      } catch (e) {
        if (mounted) setState(() => error = '$e');
      }
    }();
  }

  Map<String, dynamic> get g => cat![game] as Map<String, dynamic>;
  Map<String, dynamic> get m => (g['modes'] as Map)[mode] as Map<String, dynamic>;

  void pickGame(String id) {
    setState(() {
      game = id;
      mode = null;
      numbers.clear();
      for (final e in ((g['options'] as Map?) ?? {}).entries) {
        final spec = e.value as Map;
        if (spec['type'] != 'bool') numbers[e.key as String] = spec['default'] as num;
      }
      oxide = false;
      step = 1;
    });
  }

  Future<void> pickMode(String id) async {
    setState(() {
      mode = id;
      final maps = m['maps'] as Map?;
      map = maps == null || maps.isEmpty ? null : maps.keys.first as String;
      release = null;
      releases = null;
      rotation = false;
      final gm =
          '${m['name']}'.startsWith('${g['name']}') ? '${m['name']}' : '${g['name']} ${m['name']}'; // not "Rust Rust"
      name.text = '${widget.community ?? 'My'} $gm';
      step = 2;
    });
    if (m['has_releases'] == true) {
      try {
        final r = await widget.api.releases(game!, mode!);
        if (mounted) {
          setState(() {
            releases = r.map((x) => (x as Map)['tag'] as String).toList();
            release = releases!.isEmpty ? null : releases!.first;
          });
        }
      } catch (e) {
        if (mounted) setState(() => releases = []); // offline: the agent picks its default release
      }
    }
  }

  Map<String, dynamic> options() => {
        'name': name.text.trim(),
        ...numbers,
        if (game == 'rust') 'oxide': oxide,
        if (map != null) 'map': map,
        if (release != null) 'release': release,
        if (g['login'] == 'steam') 'steam_user': steamUser.text.trim(),
        'recommended_restarts': recommendedRestarts,
        if (!recommendedRestarts) 'restart_times': <String>[],
        if (game == 'rust') 'wipe_mode': wipeMode,
        if (game == 'rust') 'wipe_bp': wipeBp,
        if (game == 'reforger') 'rotation': rotation,
      };

  String? settingsProblem() {
    if (name.text.trim().isEmpty) return 'Give the instance a name.';
    if (g['login'] == 'steam' && !RegExp(r'^[A-Za-z0-9_.@-]{2,64}$').hasMatch(steamUser.text.trim())) {
      return "Enter the Steam username of the account that downloads the server (the password isn't asked here).";
    }
    return null;
  }

  Future<void> create() async {
    setState(() {
      starting = true;
      error = null;
    });
    try {
      final job =
          await widget.api.deploy(game!, mode!, options(), requestId: requestId, acceptedTerms: termsVersion ?? '');
      if (!mounted) return;
      Navigator.pushReplacement(
          context,
          MaterialPageRoute(
              builder: (_) => SetupJobScreen(api: widget.api, me: widget.me, jobId: job['id'] as String)));
    } on ApiError catch (e) {
      setState(() {
        starting = false;
        error = e.message;
      });
    } catch (e) {
      setState(() {
        starting = false;
        error = "Couldn't reach the agent: $e";
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final titles = ['Choose a game', 'Choose a type', 'Settings', 'Review and create'];
    return Scaffold(
      appBar: AppBar(
        backgroundColor: C.bg2,
        title: Text(cat == null ? 'New instance' : titles[step]),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          tooltip: 'Back',
          onPressed: () => step == 0 || starting ? Navigator.pop(context) : setState(() => step--),
        ),
      ),
      body: cat == null
          ? Center(
              child: error == null
                  ? CircularProgressIndicator(color: C.accent)
                  : Text(error!, style: const TextStyle(color: C.bad)))
          : Column(children: [
              _Steps(current: step, labels: const ['Game', 'Type', 'Settings', 'Review']),
              Expanded(
                child: AnimatedSwitcher(
                  duration: const Duration(milliseconds: 260),
                  transitionBuilder: (c, a) => FadeTransition(
                      opacity: a,
                      child: SlideTransition(
                          position: Tween(begin: const Offset(.04, 0), end: Offset.zero).animate(a), child: c)),
                  child: KeyedSubtree(key: ValueKey(step), child: [games, modes, settings, review][step]()),
                ),
              ),
            ]),
    );
  }

  // ---- step 1: game ----
  Widget games() => ListView(padding: const EdgeInsets.all(20), children: [
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            icon: const Icon(Icons.health_and_safety_outlined),
            label: const Text('Can this machine run it? Open the PC check'),
            onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PcCheckScreen(api: widget.api))),
          ),
        ),
        const SizedBox(height: 8),
        GridView.extent(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          maxCrossAxisExtent: 320,
          mainAxisSpacing: 14,
          crossAxisSpacing: 14,
          childAspectRatio: 1.5,
          children: [
            for (final e in cat!.entries)
              _Choice(
                title: '${(e.value as Map)['name']}',
                text: (e.value as Map)['status'] == 'ready'
                    ? '${(e.value as Map)['about'] ?? ''}'
                    : _soon((e.value as Map)['reason']),
                badge: (e.value as Map)['status'] == 'ready'
                    ? '${(e.value as Map)['size_gb']} GB · ${(e.value as Map)['ram_gb']} GB RAM'
                    : 'Soon',
                verdict: (e.value as Map)['status'] == 'ready' ? pc[e.key] : null,
                enabled: (e.value as Map)['status'] == 'ready',
                onTap: () => pickGame(e.key),
              ),
          ],
        ),
      ]);

  // ---- step 2: server type ----
  Widget modes() => ListView(padding: const EdgeInsets.all(20), children: [
        for (final e in (g['modes'] as Map).entries)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: _Choice(
              title: '${(e.value as Map)['name']}',
              text: (e.value as Map)['status'] == 'ready'
                  ? '${(e.value as Map)['about'] ?? ''}'
                  : _soon((e.value as Map)['reason']),
              badge: (e.value as Map)['status'] == 'ready' ? null : 'Soon',
              enabled: (e.value as Map)['status'] == 'ready',
              onTap: () => pickMode(e.key as String),
            ),
          ),
      ]);

  // ---- step 3: settings ----
  Widget settings() {
    final opts = ((g['options'] as Map?) ?? {}).cast<String, dynamic>();
    final restarts = ((g['restarts'] as Map?)?[mode] as Map?) ?? {};
    final times = ((restarts['times'] as List?) ?? []).cast<String>();
    return ListView(padding: const EdgeInsets.all(20), children: [
      _Section('Instance', [
        TextField(
            controller: name,
            maxLength: 80,
            decoration: const InputDecoration(labelText: 'Server name (shown in the server browser)')),
        for (final e in opts.entries)
          if ((e.value as Map)['type'] != 'bool')
            _NumberSlider(
                spec: e.value as Map, value: numbers[e.key]!, onChanged: (v) => setState(() => numbers[e.key] = v)),
        if (map != null)
          DropdownButtonFormField<String>(
            initialValue: map,
            decoration: const InputDecoration(labelText: 'Map'),
            items: [for (final k in (m['maps'] as Map).keys) DropdownMenuItem(value: '$k', child: Text('$k'))],
            onChanged: (v) => setState(() => map = v),
          ),
        if (m['has_releases'] == true)
          releases == null
              ? const Padding(
                  padding: EdgeInsets.all(8), child: Text('Checking releases…', style: TextStyle(color: C.muted)))
              : releases!.isEmpty
                  ? const Text("Couldn't list releases (offline?). The recommended one will be used.",
                      style: TextStyle(color: C.muted))
                  : DropdownButtonFormField<String>(
                      initialValue: release,
                      decoration: const InputDecoration(labelText: 'Release'),
                      items: [for (final r in releases!) DropdownMenuItem(value: r, child: Text(r))],
                      onChanged: (v) => setState(() => release = v),
                    ),
      ]),
      for (final e in opts.entries)
        if ((e.value as Map)['type'] == 'bool' && e.key == 'oxide')
          _Section('Mods', [
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: oxide,
              activeThumbColor: C.accent,
              title: Text('${(e.value as Map)['label']}'),
              subtitle: Text('${(e.value as Map)['help'] ?? ''}', style: const TextStyle(color: C.muted)),
              onChanged: (v) => setState(() => oxide = v),
            ),
          ]),
      if (g['login'] == 'steam')
        _Section('Steam account', [
          const Text(
              "This game's server download needs a Steam account (it doesn't have to own the game; a separate account "
              "just for servers is best). Only the username goes here. You'll log in once during setup; the password is "
              'never saved.',
              style: TextStyle(color: C.muted, height: 1.4)),
          const SizedBox(height: 10),
          TextField(controller: steamUser, decoration: const InputDecoration(labelText: 'Steam username')),
        ]),
      _Section('Restarts', [
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          value: recommendedRestarts,
          activeThumbColor: C.accent,
          title: Text(times.isEmpty ? 'Recommended restarts' : 'Recommended restarts: ${times.join(', ')} (UK time)'),
          subtitle: Text('${restarts['why'] ?? ''}', style: const TextStyle(color: C.muted)),
          onChanged: (v) => setState(() => recommendedRestarts = v),
        ),
        if (!recommendedRestarts)
          const Text('No scheduled restarts. You can set your own times in the instance panel later.',
              style: TextStyle(color: C.muted)),
      ]),
      if (game == 'rust')
        _Section('Wipes', [
          DropdownButtonFormField<String>(
            initialValue: wipeMode,
            decoration: const InputDecoration(labelText: 'Map wipe'),
            items: const [
              DropdownMenuItem(value: 'monthly', child: Text('Monthly (first Thursday, with the Rust update)')),
              DropdownMenuItem(value: 'biweekly', child: Text('Every two weeks')),
              DropdownMenuItem(value: 'weekly', child: Text('Weekly')),
              DropdownMenuItem(value: 'off', child: Text('Off (wipe by hand)')),
            ],
            onChanged: (v) => setState(() => wipeMode = v),
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            initialValue: wipeBp,
            decoration: const InputDecoration(labelText: 'Blueprints'),
            items: const [
              DropdownMenuItem(value: 'forced', child: Text('Wipe on the monthly forced wipe')),
              DropdownMenuItem(value: 'every', child: Text('Wipe with every map wipe')),
              DropdownMenuItem(value: 'never', child: Text('Never wipe')),
            ],
            onChanged: (v) => setState(() => wipeBp = v),
          ),
        ]),
      if (game == 'reforger' && ((m['maps'] as Map?)?.length ?? 0) >= 2)
        _Section('Map rotation', [
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: rotation,
            activeThumbColor: C.accent,
            title: const Text('Change map at each scheduled restart'),
            subtitle: const Text(
                'Starts on the map above, then goes through the others. Change the order later in the instance panel.',
                style: TextStyle(color: C.muted)),
            onChanged: (v) => setState(() => rotation = v),
          ),
        ]),
      const SizedBox(height: 8),
      Align(
        alignment: Alignment.centerRight,
        child: FilledButton(
          onPressed: () {
            final p = settingsProblem();
            if (p != null) {
              ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(p), backgroundColor: C.bad));
              return;
            }
            setState(() => step = 3);
          },
          child: const Text('Next: review'),
        ),
      ),
    ]);
  }

  // ---- step 4: review + terms ----
  Widget review() {
    final terms = (((g['terms'] as Map?)?[mode] as List?) ?? []).cast<Map>();
    final o = options();
    return ListView(padding: const EdgeInsets.all(20), children: [
      _Section('You are creating', [
        _Row('Name', '${o['name']}'),
        _Row('Game', '${g['name']} · ${m['name']}${oxide ? ' + Oxide' : ''}'),
        for (final e in numbers.entries) _Row('${((g['options'] as Map)[e.key] as Map)['label']}', '${e.value}'),
        if (map != null) _Row('Map', map!),
        if (release != null) _Row('Release', release!),
        if (o['steam_user'] != null) _Row('Steam account', '${o['steam_user']}'),
        _Row('Restarts', recommendedRestarts ? 'Recommended' : 'None for now'),
        if (game == 'rust') _Row('Wipes', '$wipeMode, blueprints: $wipeBp'),
        if (game == 'reforger') _Row('Map rotation', rotation ? 'On' : 'Off'),
        _Row('Needs', 'About ${g['size_gb']} GB of disk and ${g['ram_gb']} GB of memory'),
      ]),
      _Section('Terms', [
        const Text('By creating this instance you agree to:', style: TextStyle(color: C.muted)),
        const SizedBox(height: 8),
        for (final t in terms)
          ListTile(
            contentPadding: EdgeInsets.zero,
            dense: true,
            leading: Icon(Icons.description_outlined, color: C.accent2),
            title: Text('${t['name']}'),
            subtitle: Text('${t['detail']}${t['url'] != null ? '\n${t['url']}' : ''}',
                style: const TextStyle(color: C.muted)),
          ),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton(
            onPressed: () => showDialog(
              context: context,
              builder: (_) => AlertDialog(
                backgroundColor: C.bg2,
                title: const Text('HostSpawn terms (draft)'),
                content: SizedBox(width: 640, child: SingleChildScrollView(child: SelectableText(termsText ?? ''))),
                actions: [FilledButton(onPressed: () => Navigator.pop(context), child: const Text('Close'))],
              ),
            ),
            child: const Text('Read the HostSpawn terms'),
          ),
        ),
        CheckboxListTile(
          contentPadding: EdgeInsets.zero,
          value: accepted,
          activeColor: C.accent,
          controlAffinity: ListTileControlAffinity.leading,
          title: const Text('I have read and accept these terms'),
          onChanged: (v) => setState(() => accepted = v ?? false),
        ),
      ]),
      if (error != null)
        Padding(padding: const EdgeInsets.only(bottom: 12), child: Text(error!, style: const TextStyle(color: C.bad))),
      Align(
        alignment: Alignment.centerRight,
        child: FilledButton.icon(
          onPressed: accepted && !starting ? create : null,
          icon: starting
              ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.rocket_launch_outlined),
          label: const Text('Create instance'),
        ),
      ),
    ]);
  }
}

/// "Coming soon" plus the reason, without saying it twice when the reason is just "Coming soon".
String _soon(dynamic reason) {
  final r = '${reason ?? ''}'.trim();
  return r.isEmpty || r.toLowerCase().startsWith('coming soon') ? 'Coming soon.' : 'Coming soon. $r';
}

class _Steps extends StatelessWidget {
  const _Steps({required this.current, required this.labels});
  final int current;
  final List<String> labels;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 4),
        child: Row(children: [
          for (var i = 0; i < labels.length; i++) ...[
            AnimatedContainer(
              duration: const Duration(milliseconds: 250),
              width: 26,
              height: 26,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: i < current
                    ? C.accent
                    : i == current
                        ? const Color(0x333EE6C4)
                        : C.panel2,
                border: Border.all(color: i <= current ? C.accent : C.line2),
              ),
              child: i < current
                  ? const Icon(Icons.check, size: 15, color: C.bg)
                  : Text('${i + 1}', style: TextStyle(fontSize: 12, color: i == current ? C.accent : C.muted)),
            ),
            const SizedBox(width: 8),
            Text(labels[i], style: TextStyle(color: i == current ? C.text : C.muted)),
            if (i < labels.length - 1)
              Expanded(
                  child: Container(
                      height: 1,
                      margin: const EdgeInsets.symmetric(horizontal: 10),
                      color: i < current ? C.accent : C.line2)),
          ],
        ]),
      );
}

class _Choice extends StatelessWidget {
  const _Choice(
      {required this.title, required this.text, this.badge, this.verdict, required this.enabled, required this.onTap});
  final String title, text;
  final String? badge, verdict; // verdict: the PC check's ok / tight / no for this game
  final bool enabled;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => Opacity(
        opacity: enabled ? 1 : .45,
        child: Card(
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: enabled ? onTap : null,
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(child: Text(title, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600))),
                  if (badge != null)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                      decoration: BoxDecoration(color: C.panel2, borderRadius: BorderRadius.circular(6)),
                      child: Text(badge!, style: const TextStyle(fontSize: 11, color: C.muted)),
                    ),
                ]),
                if (verdict != null) ...[
                  const SizedBox(height: 6),
                  Row(children: [
                    Icon(verdictIcon[verdict], size: 15, color: verdictColor[verdict]),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text('${verdictText[verdict]} on this machine',
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(fontSize: 12.5, color: verdictColor[verdict], fontWeight: FontWeight.w600)),
                    ),
                  ]),
                ],
                const SizedBox(height: 8),
                Text(text,
                    maxLines: 3, overflow: TextOverflow.ellipsis, style: const TextStyle(color: C.muted, height: 1.35)),
              ]),
            ),
          ),
        ),
      );
}

class _Section extends StatelessWidget {
  const _Section(this.title, this.children);
  final String title;
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
              const SizedBox(height: 12),
              for (final c in children) Padding(padding: const EdgeInsets.only(bottom: 6), child: c),
            ]),
          ),
        ),
      );
}

class _NumberSlider extends StatelessWidget {
  const _NumberSlider({required this.spec, required this.value, required this.onChanged});
  final Map spec;
  final num value;
  final ValueChanged<num> onChanged;
  @override
  Widget build(BuildContext context) {
    final min = (spec['min'] as num).toDouble(), max = (spec['max'] as num).toDouble();
    final stepSize = (spec['step'] as num?)?.toDouble() ?? 1;
    return Row(children: [
      SizedBox(width: 150, child: Text('${spec['label']}', style: const TextStyle(color: C.muted))),
      Expanded(
        child: Slider(
          value: value.toDouble().clamp(min, max),
          min: min,
          max: max,
          divisions: ((max - min) / stepSize).round(),
          activeColor: C.accent,
          label: '${value.round()}',
          onChanged: (v) => onChanged((v / stepSize).round() * stepSize),
        ),
      ),
      SizedBox(
          width: 56,
          child: Text('${value.round()}',
              textAlign: TextAlign.right, style: const TextStyle(fontWeight: FontWeight.w600))),
    ]);
  }
}

class _Row extends StatelessWidget {
  const _Row(this.label, this.value);
  final String label, value;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 150, child: Text(label, style: const TextStyle(color: C.muted))),
          Expanded(child: Text(value)),
        ]),
      );
}
