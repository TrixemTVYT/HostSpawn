// Copy for AI: the button puts the engine's guide on the clipboard and says so; an error shows the reason.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/screens/ai_help.dart';

class FakeApi extends Api {
  FakeApi({this.fail = false}) : super(Uri.parse('http://127.0.0.1:9'));
  final bool fail;

  @override
  Future<String> aiHelp(String server) async {
    if (fail) throw ApiError(403, 'Only admins can copy an instance\'s AI help');
    return '# AI help: $server';
  }
}

void main() {
  String? copied;
  setUp(() {
    copied = null;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(SystemChannels.platform,
        (call) async {
      if (call.method == 'Clipboard.setData') copied = (call.arguments as Map)['text'] as String;
      return null;
    });
  });

  Future<void> show(WidgetTester t, Api api) =>
      t.pumpWidget(MaterialApp(home: Scaffold(appBar: AppBar(actions: [CopyForAiButton(api: api, server: 'a3')]))));

  testWidgets('copies the guide', (t) async {
    await show(t, FakeApi());
    await t.tap(find.text('Copy for AI'));
    await t.pump();
    await t.pump();
    expect(copied, '# AI help: a3');
    expect(find.textContaining('Paste it into your AI chat'), findsOneWidget);
  });

  testWidgets('shows why it could not copy', (t) async {
    await show(t, FakeApi(fail: true));
    await t.tap(find.text('Copy for AI'));
    await t.pump();
    await t.pump();
    expect(copied, isNull);
    expect(find.textContaining('Only admins'), findsOneWidget);
  });
}
