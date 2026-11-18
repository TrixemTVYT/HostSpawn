// Talks to the HostSpawn agent's API (/api/v1). Signs in with a device token sent as
// "Authorization: Bearer ...". Every POST carries "X-Manager: 1", as the agent requires.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

class ApiError implements Exception {
  ApiError(this.status, this.message);
  final int status;
  final String message;
  bool get signedOut => status == 401;
  @override
  String toString() => message;
}

class Me {
  Me(this.user, this.role, this.device);
  final String user, role;
  final String? device;
  factory Me.fromJson(Map<String, dynamic> j) => Me(j['user'] as String, j['role'] as String, j['device'] as String?);
  bool get isAdmin => role == 'admin';
  bool get canRun => role == 'admin' || role == 'moderator';
}

class AgentInfo {
  AgentInfo(this.name, this.api, this.version, this.setupNeeded);
  final String name, version;
  final int api;
  final bool setupNeeded;
}

class Api {
  Api(this.base, {this.token, HttpClient? client})
      : _http = client ?? (HttpClient()..connectionTimeout = const Duration(seconds: 5));

  /// e.g. http://127.0.0.1:8100
  final Uri base;
  String? token;
  final HttpClient _http;

  static const supportedApi = 1;

  /// Fires after every change that worked (any successful POST), so the dashboard can refresh at once.
  final _changed = StreamController<void>.broadcast();
  Stream<void> get changed => _changed.stream;

  Future<dynamic> _send(String method, String path, [Map<String, dynamic>? body]) async {
    final req = await _http.openUrl(method, base.resolve('/api/v1/$path'));
    req.headers.set('Accept', 'application/json');
    if (token != null) req.headers.set('Authorization', 'Bearer $token');
    if (method == 'POST') {
      req.headers.set('X-Manager', '1');
      req.headers.contentType = ContentType.json;
      final bytes = utf8.encode(jsonEncode(body ?? {}));
      req.contentLength = bytes.length; // the agent needs Content-Length (it doesn't read chunked bodies)
      req.add(bytes);
    }
    final res = await req.close().timeout(const Duration(seconds: 20));
    final text = await res.transform(utf8.decoder).join();
    dynamic data;
    try {
      data = text.isEmpty ? null : jsonDecode(text);
    } on FormatException {
      throw ApiError(res.statusCode, 'The agent sent something unexpected (HTTP ${res.statusCode}).');
    }
    if (res.statusCode >= 400) {
      final msg = data is Map && data['error'] is String ? data['error'] as String : 'HTTP ${res.statusCode}';
      throw ApiError(res.statusCode, msg);
    }
    if (method == 'POST') _changed.add(null);
    return data;
  }

  Future<dynamic> get(String path) => _send('GET', path);
  Future<dynamic> post(String path, [Map<String, dynamic>? body]) => _send('POST', path, body);

  /// Is a HostSpawn agent answering here, and does it speak our API version?
  Future<AgentInfo> info() async {
    final j = await get('info') as Map<String, dynamic>;
    if (j['name'] != 'HostSpawn') throw ApiError(0, "That address isn't a HostSpawn agent.");
    final info = AgentInfo(j['name'] as String, j['api'] as int, '${j['version']}', j['setup_needed'] == true);
    if (info.api != supportedApi) {
      throw ApiError(
          0, 'This agent uses API v${info.api}; this app needs v$supportedApi. Update both to the same release.');
    }
    return info;
  }

  Future<Me> _signedIn(dynamic j) async {
    token = (j as Map<String, dynamic>)['token'] as String;
    return Me.fromJson(j);
  }

  Future<Me> setup(String user, String password, String device) async =>
      _signedIn(await post('setup', {'username': user, 'password': password, 'device': device}));

  Future<Me> login(String user, String password, String device) async =>
      _signedIn(await post('login', {'username': user, 'password': password, 'device': device}));

  Future<Me> pair(String code, String device) async => _signedIn(await post('pair', {'code': code, 'device': device}));

  Future<Me> me() async => Me.fromJson(await get('me') as Map<String, dynamic>);

  Future<void> logout() async {
    try {
      await post('logout');
    } finally {
      token = null;
    }
  }

  Future<Map<String, dynamic>> overview() async => await get('overview') as Map<String, dynamic>;
  Future<Map<String, dynamic>> hostCheck() async => await get('hostcheck') as Map<String, dynamic>;
  Future<Map<String, dynamic>> server(String id) async =>
      await get('server?id=${Uri.encodeQueryComponent(id)}') as Map<String, dynamic>;
  Future<Map<String, dynamic>> logs(String id) async =>
      await get('logs?server=${Uri.encodeQueryComponent(id)}') as Map<String, dynamic>;

  /// start/stop/restart/update/backup... Risky actions need [confirm] = the action name (the agent says which).
  Future<String> action(String server, String action, {String arg = '', String? confirm}) async {
    final j =
        await post('action', {'server': server, 'action': action, 'arg': arg, if (confirm != null) 'confirm': confirm});
    return (j as Map)['message'] as String? ?? 'Done';
  }

  /// Changes a HostSpawn server's settings (admins only): name, max_players, restart_times, backup, keep_backups,
  /// wipe_mode/wipe_time/wipe_bp/wipe_new_seed (Rust). Only the keys given are changed.
  // ---- the file editor (admins only) ----
  String _q(String server, [String? path]) =>
      'server=${Uri.encodeQueryComponent(server)}${path == null ? '' : '&path=${Uri.encodeQueryComponent(path)}'}';
  Future<Map<String, dynamic>> files(String server) async => await get('files?${_q(server)}') as Map<String, dynamic>;
  Future<Map<String, dynamic>> filesDir(String server, String? path) async =>
      await get('files/dir?${_q(server)}${path == null ? '' : '&path=${Uri.encodeQueryComponent(path)}'}')
          as Map<String, dynamic>;
  Future<Map<String, dynamic>> file(String server, String path) async =>
      await get('file?${_q(server, path)}') as Map<String, dynamic>;
  Future<String> fileVersion(String server, String path, String id) async =>
      ((await get('file/version?${_q(server, path)}&id=${Uri.encodeQueryComponent(id)}')) as Map)['text'] as String;
  Future<Map<String, dynamic>> saveFile(String server, String path, String text, String sha) async =>
      (await post('file', {'server': server, 'path': path, 'text': text, 'sha': sha})) as Map<String, dynamic>;
  Future<Map<String, dynamic>> restoreFile(String server, String path, String id) async =>
      (await post('file/restore', {'server': server, 'path': path, 'id': id})) as Map<String, dynamic>;
  Future<String> packMission(String server, String folder) async =>
      ((await post('file/pack', {'server': server, 'folder': folder})) as Map)['message'] as String;

  /// Server Doctor: {findings: [{id, level, title, why, fix, where, action?}], checked, ok}.
  Future<Map<String, dynamic>> doctor(String server) async => await get('doctor?${_q(server)}') as Map<String, dynamic>;
  Future<Map<String, dynamic>> spyglass(String server) async =>
      await get('spyglass?${_q(server)}') as Map<String, dynamic>;

  /// Copy for AI: a guide to the instance for the owner's own AI assistant, passwords and players' details removed.
  Future<String> aiHelp(String server) async => ((await get('aihelp?${_q(server)}')) as Map)['text'] as String;

  // ---- the Database tab (admins only) ----
  String _dq(String server, String source) => '${_q(server)}&source=${Uri.encodeQueryComponent(source)}';
  Future<List<dynamic>> dbSources(String server) async =>
      ((await get('db?${_q(server)}')) as Map)['sources'] as List<dynamic>;
  Future<List<dynamic>> dbTables(String server, String source) async =>
      ((await get('db/tables?${_dq(server, source)}')) as Map)['tables'] as List<dynamic>;
  Future<Map<String, dynamic>> dbRows(String server, String source, String table,
          {int page = 0, String search = '', String order = '', bool desc = false}) async =>
      await get('db/rows?${_dq(server, source)}&table=${Uri.encodeQueryComponent(table)}&page=$page'
              '&search=${Uri.encodeQueryComponent(search)}&order=${Uri.encodeQueryComponent(order)}&desc=${desc ? 1 : 0}')
          as Map<String, dynamic>;
  Future<Map<String, dynamic>> dbQuery(String server, String source, String sql) async =>
      (await post('db/query', {'server': server, 'source': source, 'sql': sql})) as Map<String, dynamic>;

  /// A change (backed up first): either [sql] or an [edit] {op: update|delete|insert, table, key, set, values}.
  Future<Map<String, dynamic>> dbChange(String server, String source,
          {String sql = '', Map<String, dynamic>? edit}) async =>
      (await post('db/change', {'server': server, 'source': source, 'confirm': 'CHANGE', 'sql': sql, 'edit': edit}))
          as Map<String, dynamic>;

  /// An instance's installation: sizes, version, update check, the running job and its history.
  Future<Map<String, dynamic>> install(String server) async =>
      await get('install?server=${Uri.encodeQueryComponent(server)}') as Map<String, dynamic>;

  /// update | repair | reinstall (confirm = the instance's name) | cleanup | check. warnS: countdown for players.
  Future<Map<String, dynamic>> installAction(String server, String action, {String confirm = '', int warnS = 0}) async {
    final j = await post('install', {'server': server, 'action': action, 'confirm': confirm, 'warn_s': warnS});
    return ((j as Map)['job'] as Map?)?.cast<String, dynamic>() ?? {};
  }

  /// HostSpawn's own settings (time zone, community, backups).
  Future<Map<String, dynamic>> appSettings() async => await get('settings') as Map<String, dynamic>;
  Future<void> saveAppSettings(Map<String, dynamic> changes) async => await post('settings', changes);

  Future<String> saveSettings(String server, Map<String, dynamic> changes) async {
    final j = await post('server/settings', {'server': server, ...changes});
    return (j as Map)['message'] as String? ?? 'Saved';
  }

  // ---- players (one database and one ban list for every server) ----
  Future<List<dynamic>> onlinePlayers([String? server]) async =>
      await get(server == null ? 'players' : 'players?server=${Uri.encodeQueryComponent(server)}') as List<dynamic>;
  Future<List<dynamic>> searchPlayers(String q) async =>
      await get('players/search?q=${Uri.encodeQueryComponent(q)}') as List<dynamic>;
  Future<Map<String, dynamic>> player(String key) async =>
      await get('player?key=${Uri.encodeQueryComponent(key)}') as Map<String, dynamic>;
  Future<List<dynamic>> bans({bool all = false}) async => await get(all ? 'bans?all=1' : 'bans') as List<dynamic>;
  Future<List<dynamic>> auditLog() async => await get('audit') as List<dynamic>;

  /// kick (needs [server]), ban ([minutes] 0 = permanent, [reason] required), unban, note ([text]), watch, unwatch,
  /// say ([text]; [server] null = every server). Moderators and admins.
  Future<String> playerAction(String action,
      {String? key, String? server, String reason = '', int? minutes, String? text}) async {
    final j = await post('players', {
      'action': action,
      if (key != null) 'key': key,
      if (server != null) 'server': server,
      if (reason.isNotEmpty) 'reason': reason,
      if (minutes != null) 'minutes': minutes,
      if (text != null) 'text': text,
    });
    return (j as Map)['message'] as String? ?? 'Done';
  }

  /// Deletes a server HostSpawn created (admins only). [confirm] must be the server's name; backups kept by default.
  Future<String> deleteServer(String server, String confirm, {bool keepBackups = true}) async {
    final j = await post('delete', {'server': server, 'confirm': confirm, 'keep_backups': keepBackups});
    return (j as Map)['message'] as String? ?? 'Deleted';
  }

  // ---- creating servers ----
  Future<Map<String, dynamic>> catalog() async => await get('catalog') as Map<String, dynamic>;
  Future<Map<String, dynamic>> terms() async => await get('terms') as Map<String, dynamic>;
  Future<List<dynamic>> releases(String game, String mode) async =>
      await get('catalog/releases?game=${Uri.encodeQueryComponent(game)}&mode=${Uri.encodeQueryComponent(mode)}')
          as List<dynamic>;

  /// Starts a setup. [requestId] makes a double click harmless: the agent returns the same job again.
  Future<Map<String, dynamic>> deploy(String game, String mode, Map<String, dynamic> options,
          {required String requestId, required String acceptedTerms}) async =>
      await post('deploy', {
        'game': game,
        'mode': mode,
        'options': options,
        'request_id': requestId,
        'accepted_terms': acceptedTerms,
      }) as Map<String, dynamic>;

  Future<Map<String, dynamic>> job(String id) async =>
      await get('job?id=${Uri.encodeQueryComponent(id)}') as Map<String, dynamic>;

  /// continue / back / skip / cancel / retry / discard
  Future<dynamic> jobAction(String id, String action) => post('job', {'id': id, 'action': action});

  Future<String> pairCode() async => ((await post('pair/new')) as Map)['code'] as String;
  Future<List<dynamic>> devices() async => await get('devices') as List<dynamic>;
  Future<void> revokeDevice(String id) async => post('devices', {'action': 'revoke', 'id': id});

  void close() {
    _http.close(force: true);
    _changed.close();
  }
}
