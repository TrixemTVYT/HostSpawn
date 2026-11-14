// First-run setup (create the owner), sign in, or pair this device with a 6-digit code.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../store.dart';
import '../theme.dart';

enum _Mode { setup, signIn, pair }

class SignInScreen extends StatefulWidget {
  const SignInScreen(
      {super.key,
      required this.api,
      required this.setupNeeded,
      required this.saved,
      required this.device,
      required this.done});
  final Api api;
  final bool setupNeeded;
  final Saved saved;
  final String device;
  final Future<void> Function(Me) done;
  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
  late _Mode mode = widget.setupNeeded ? _Mode.setup : _Mode.signIn;
  final user = TextEditingController(),
      pw = TextEditingController(),
      pw2 = TextEditingController(),
      code = TextEditingController();
  String? error;
  bool busy = false, show = false;

  @override
  void initState() {
    super.initState();
    widget.saved.lastUser().then((u) {
      if (u != null && user.text.isEmpty) user.text = u;
    });
  }

  @override
  void dispose() {
    for (final c in [user, pw, pw2, code]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> submit() async {
    if (mode == _Mode.setup && pw.text != pw2.text) {
      setState(() => error = "The two passwords don't match.");
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final me = switch (mode) {
        _Mode.setup => await widget.api.setup(user.text.trim(), pw.text, widget.device),
        _Mode.signIn => await widget.api.login(user.text.trim(), pw.text, widget.device),
        _Mode.pair => await widget.api.pair(code.text.trim(), widget.device),
      };
      pw.clear();
      pw2.clear();
      await widget.done(me);
    } on ApiError catch (e) {
      setState(() => error = e.message);
    } catch (e) {
      setState(() => error = "Couldn't reach the agent: $e");
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = switch (mode) {
      _Mode.setup => 'Welcome to HostSpawn',
      _Mode.signIn => 'Sign in',
      _Mode.pair => 'Pair this device',
    };
    final hint = switch (mode) {
      _Mode.setup => "Create the owner account. It can do everything, so use a strong password (10+ characters). "
          "It's stored only as a scrambled hash on this machine.",
      _Mode.signIn => 'Sign in to manage your instances.',
      _Mode.pair => 'On a device that is already signed in, open Settings → Devices → Pair a device '
          '(or run ./manager.sh pair <username> on the server). Type the 6-digit code here. It works once, for 5 minutes.',
    };
    return Scaffold(
      body: Backdrop(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: Entrance(
                child: Container(
                  decoration: glass(glow: C.accent2),
                  child: Padding(
                    padding: const EdgeInsets.all(30),
                    child: AutofillGroup(
                      child: Column(
                          mainAxisSize: MainAxisSize.min,
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            const Center(child: Logo(size: 56)),
                            const SizedBox(height: 18),
                            Center(
                              child: GradientText(title,
                                  style:
                                      const TextStyle(fontSize: 26, fontWeight: FontWeight.w800, letterSpacing: -.4)),
                            ),
                            const SizedBox(height: 8),
                            Text(hint,
                                textAlign: TextAlign.center, style: const TextStyle(color: C.muted, height: 1.4)),
                            const SizedBox(height: 22),
                            if (mode == _Mode.pair)
                              TextField(
                                controller: code,
                                autofocus: true,
                                keyboardType: TextInputType.number,
                                maxLength: 6,
                                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                                textAlign: TextAlign.center,
                                style: const TextStyle(fontSize: 28, letterSpacing: 10, fontWeight: FontWeight.w600),
                                decoration: const InputDecoration(labelText: 'Pairing code', counterText: ''),
                                onSubmitted: (_) => submit(),
                              )
                            else ...[
                              TextField(
                                controller: user,
                                autofocus: true,
                                autofillHints: const [AutofillHints.username],
                                decoration: const InputDecoration(labelText: 'Username'),
                              ),
                              const SizedBox(height: 12),
                              TextField(
                                controller: pw,
                                obscureText: !show,
                                autofillHints: [
                                  mode == _Mode.setup ? AutofillHints.newPassword : AutofillHints.password
                                ],
                                decoration: InputDecoration(
                                  labelText: 'Password',
                                  suffixIcon: IconButton(
                                    tooltip: show ? 'Hide' : 'Show',
                                    icon: Icon(show ? Icons.visibility_off : Icons.visibility),
                                    onPressed: () => setState(() => show = !show),
                                  ),
                                ),
                                onSubmitted: (_) => mode == _Mode.signIn ? submit() : null,
                              ),
                              if (mode == _Mode.setup) ...[
                                const SizedBox(height: 12),
                                TextField(
                                  controller: pw2,
                                  obscureText: !show,
                                  decoration: const InputDecoration(labelText: 'Password again'),
                                  onSubmitted: (_) => submit(),
                                ),
                              ],
                            ],
                            if (error != null) ...[
                              const SizedBox(height: 14),
                              Text(error!, style: const TextStyle(color: C.bad)),
                            ],
                            const SizedBox(height: 20),
                            FilledButton(
                              onPressed: busy ? null : submit,
                              child: busy
                                  ? const SizedBox(
                                      width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                                  : Text(mode == _Mode.setup
                                      ? 'Create account'
                                      : mode == _Mode.pair
                                          ? 'Pair'
                                          : 'Sign in'),
                            ),
                            if (mode != _Mode.setup) ...[
                              const SizedBox(height: 8),
                              TextButton(
                                onPressed: () => setState(() {
                                  mode = mode == _Mode.pair ? _Mode.signIn : _Mode.pair;
                                  error = null;
                                }),
                                child: Text(mode == _Mode.pair
                                    ? 'Sign in with a password instead'
                                    : 'Use a pairing code instead'),
                              ),
                            ],
                          ]),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
