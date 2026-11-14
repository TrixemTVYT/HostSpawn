// Remembers which agent to talk to and its sign-in key. The key goes in the system keychain only.
import 'dart:io';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

abstract class KeyStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

class SystemKeyStore implements KeyStore {
  const SystemKeyStore();
  static const _s = FlutterSecureStorage();

  /// False when this computer has no keychain service (some minimal Linux desktops): then the app still works,
  /// but asks you to sign in each time it opens instead of keeping the key in a plain file.
  static bool available = true;

  @override
  Future<String?> read(String key) async {
    try {
      return await _s.read(key: key);
    } catch (_) {
      available = false;
      return null;
    }
  }

  @override
  Future<void> write(String key, String value) async {
    try {
      await _s.write(key: key, value: value);
    } catch (_) {
      available = false;
    }
  }

  @override
  Future<void> delete(String key) async {
    try {
      await _s.delete(key: key);
    } catch (_) {
      available = false;
    }
  }
}

/// For tests.
class MemoryKeyStore implements KeyStore {
  final Map<String, String> data = {};
  @override
  Future<String?> read(String key) async => data[key];
  @override
  Future<void> write(String key, String value) async => data[key] = value;
  @override
  Future<void> delete(String key) async => data.remove(key);
}

class Saved {
  Saved(this.store);
  final KeyStore store;
  static const _agent = 'hostspawn.agent', _token = 'hostspawn.token', _user = 'hostspawn.last_user';
  static const _theme = 'hostspawn.theme', _server = 'hostspawn.server';
  static const defaultAgent = 'http://127.0.0.1:8100';

  /// HOSTSPAWN_AGENT (an address) overrides the saved one, for testing against a sandbox agent.
  Future<String> agent() async => Platform.environment['HOSTSPAWN_AGENT'] ?? await store.read(_agent) ?? defaultAgent;
  Future<void> setAgent(String url) => store.write(_agent, url);
  Future<String?> token() => store.read(_token);
  Future<void> setToken(String? t) => t == null ? store.delete(_token) : store.write(_token, t);
  Future<String?> lastUser() => store.read(_user);
  Future<void> setLastUser(String u) => store.write(_user, u);
  Future<String?> theme() => store.read(_theme);
  Future<void> setTheme(String id) => store.write(_theme, id);
  Future<String?> server() => store.read(_server); // user@host:sshport:port for Connect to my server
  Future<void> setServer(String s) => store.write(_server, s);
}
