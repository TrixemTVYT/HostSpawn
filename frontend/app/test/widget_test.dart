// Screen tests: no agent needed (nothing here reaches the network).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/sign_in.dart';
import 'package:hostspawn/store.dart';
import 'package:hostspawn/theme.dart';

Widget _app(Widget child) => MaterialApp(theme: hostspawnTheme(), home: child);

SignInScreen _signIn({required bool setup, Future<void> Function(Me)? done}) => SignInScreen(
      api: Api(Uri.parse('http://127.0.0.1:9')),
      setupNeeded: setup,
      saved: Saved(MemoryKeyStore()),
      device: 'test',
      done: done ?? (_) async {},
    );

void main() {
  testWidgets('first run asks for the owner account and checks both passwords match', (t) async {
    await t.pumpWidget(_app(_signIn(setup: true)));
    await t.pumpAndSettle();
    expect(find.text('Welcome to HostSpawn'), findsOneWidget);
    expect(find.text('Create account'), findsOneWidget);
    expect(find.text('Use a pairing code instead'), findsNothing); // no pairing before an owner exists
    final fields = find.byType(TextField);
    await t.enterText(fields.at(0), 'owner');
    await t.enterText(fields.at(1), 'correct horse battery');
    await t.enterText(fields.at(2), 'something else entirely');
    await t.tap(find.text('Create account'));
    await t.pump();
    expect(find.text("The two passwords don't match."), findsOneWidget);
  });

  testWidgets('sign in can switch to a pairing code and back', (t) async {
    await t.pumpWidget(_app(_signIn(setup: false)));
    await t.pumpAndSettle();
    expect(find.text('Sign in'), findsWidgets);
    await t.tap(find.text('Use a pairing code instead'));
    await t.pumpAndSettle();
    expect(find.text('Pair this device'), findsOneWidget);
    await t.enterText(find.byType(TextField), '12ab34567');
    expect(find.text('1234'), findsNothing);
    expect(find.text('123456'), findsOneWidget); // digits only, at most 6
    await t.tap(find.text('Sign in with a password instead'));
    await t.pumpAndSettle();
    expect(find.text('Username'), findsOneWidget);
  });

  testWidgets('password is hidden until shown', (t) async {
    await t.pumpWidget(_app(_signIn(setup: false)));
    await t.pumpAndSettle();
    TextField pw() => t.widget<TextField>(find.byType(TextField).at(1));
    expect(pw().obscureText, isTrue);
    await t.tap(find.byTooltip('Show'));
    await t.pump();
    expect(pw().obscureText, isFalse);
  });
}
