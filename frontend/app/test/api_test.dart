// The API client against a small fake agent (no real HostSpawn, no network beyond 127.0.0.1).
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:hostspawn/api.dart';
import 'package:hostspawn/store.dart';

void main() {
  late HttpServer fake;
  late Api api;
  final seen = <HttpRequest>[];
  final bodies = <String, Map>{};

  setUp(() async {
    seen.clear();
    fake = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    fake.listen((req) async {
      seen.add(req);
      if (req.method == 'POST' && req.headers.contentLength < 0) {
        // like the real agent: a body without Content-Length is refused
        req.response.statusCode = 411;
        req.response.write('{"error": "chunked"}');
        await req.response.close();
        return;
      }
      final body = req.method == 'POST' ? jsonDecode(await utf8.decoder.bind(req).join()) as Map : {};
      bodies[req.uri.path] = body;
      final auth = req.headers.value('authorization');
      void reply(int code, Object data) {
        req.response.statusCode = code;
        req.response.headers.contentType = ContentType.json;
        req.response.write(jsonEncode(data));
        req.response.close();
      }

      switch (req.uri.path) {
        case '/api/v1/info':
          return reply(200, {'name': 'HostSpawn', 'api': 1, 'version': 'test', 'setup_needed': false});
        case '/api/v1/login':
          return body['password'] == 'right password'
              ? reply(200, {'user': 'owner', 'role': 'admin', 'device': body['device'], 'token': 'tok123'})
              : reply(400, {'error': 'Wrong username or password.'});
        case '/api/v1/overview':
          return auth == 'Bearer tok123' ? reply(200, {'servers': []}) : reply(401, {'error': 'Please sign in'});
        case '/api/v1/action':
          return req.headers.value('x-manager') == '1'
              ? reply(202, {'message': 'start accepted'})
              : reply(403, {'error': 'missing X-Manager header'});
        default:
          return reply(404, {'error': 'not found'});
      }
    });
    api = Api(Uri.parse('http://127.0.0.1:${fake.port}'));
  });

  tearDown(() async {
    api.close();
    await fake.close(force: true);
  });

  test('checks it is a HostSpawn agent', () async {
    final info = await api.info();
    expect(info.version, 'test');
    expect(info.api, 1);
  });

  test('wrong password: clear message, no token', () async {
    await expectLater(api.login('owner', 'nope', 'pc'),
        throwsA(isA<ApiError>().having((e) => e.message, 'message', 'Wrong username or password.')));
    expect(api.token, isNull);
  });

  test('sign in sends the device name and then uses the token', () async {
    final me = await api.login('owner', 'right password', 'Test PC');
    expect(me.isAdmin, isTrue);
    expect(bodies['/api/v1/login']!['device'], 'Test PC');
    expect(api.token, 'tok123');
    expect((await api.overview())['servers'], isEmpty);
  });

  test('signed out shows as signedOut', () async {
    api.token = 'old';
    await expectLater(api.overview(), throwsA(isA<ApiError>().having((e) => e.signedOut, 'signedOut', isTrue)));
  });

  test('every POST carries X-Manager', () async {
    api.token = 'tok123';
    expect(await api.action('srv', 'start'), 'start accepted');
  });

  test('saved settings: default agent, token kept and cleared', () async {
    final saved = Saved(MemoryKeyStore());
    expect(await saved.agent(), 'http://127.0.0.1:8100');
    await saved.setToken('abc');
    expect(await saved.token(), 'abc');
    await saved.setToken(null);
    expect(await saved.token(), isNull);
  });
}
