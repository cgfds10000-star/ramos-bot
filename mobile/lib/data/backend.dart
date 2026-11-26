import 'dart:async';
import 'dart:convert';
import 'dart:io';
import '../auth/platform_crypto.dart';

class SecureVault {
  final AndroidLoginStorage storage;
  Map<String, dynamic> data = {};
  Future<void> _pending = Future.value();
  SecureVault({AndroidLoginStorage? storage})
    : storage = storage ?? AndroidLoginStorage();
  Future<void> load() async {
    data = await storage.read();
  }

  dynamic operator [](String key) => data[key];
  Future<void> put(String key, dynamic value) {
    data[key] = value;
    _pending = _pending
        .catchError((Object _) {})
        .then((_) => storage.write(data));
    return _pending;
  }

  Map<String, dynamic>? login(String id) {
    final rows = data['logins'];
    final row = rows is Map ? rows[id] : null;
    return row is Map ? Map<String, dynamic>.from(row) : null;
  }

  Future<void> saveLogin(String id, Map<String, dynamic> row) {
    final rows = Map<String, dynamic>.from(
      data['logins'] is Map ? data['logins'] : {},
    );
    rows[id] = row;
    final owner = object(row['client'])['owner']?.toString();
    if (owner != null && owner.isNotEmpty)
      rows['account:${owner.toLowerCase()}'] = row;
    return put('logins', rows);
  }
}

class BackendFailure implements Exception {
  final String message;
  final bool locked;
  BackendFailure(this.message, {this.locked = false});
  @override
  String toString() => message;
}

class BackendApi {
  final SecureVault vault;
  final HttpClient _http = HttpClient()
    ..connectionTimeout = const Duration(seconds: 15);
  String base = 'https://ramos-bot-production.up.railway.app';
  String cookie = '', csrf = '';
  BackendApi(this.vault) {
    final row = vault['backend'];
    if (row is Map) {
      base = row['base']?.toString() ?? base;
      cookie = row['cookie']?.toString() ?? '';
    }
  }
  Future<void> setBase(String value) async {
    final uri = Uri.tryParse(value.trim());
    if (uri == null ||
        uri.scheme != 'https' ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        (uri.path.isNotEmpty && uri.path != '/')) {
      throw BackendFailure(
        'Informe o endereço HTTPS do servidor, sem caminho adicional.',
      );
    }
    final next = uri.origin;
    if (next != base) {
      base = next;
      cookie = '';
      csrf = '';
    }
    await _save();
  }

  Future<void> _save() =>
      vault.put('backend', {'base': base, 'cookie': cookie});
  Future<Map<String, dynamic>> get(String path) => request('GET', path);
  Future<Map<String, dynamic>> post(
    String path, [
    Map<String, dynamic> body = const {},
  ]) => request('POST', path, body);
  Future<Map<String, dynamic>> delete(String path) =>
      request('DELETE', path, {});
  Future<Map<String, dynamic>> request(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    try {
      final request = await _http
          .openUrl(method, Uri.parse('$base/api$path'))
          .timeout(const Duration(seconds: 15));
      request.followRedirects = false;
      request.headers.contentType = ContentType.json;
      if (cookie.isNotEmpty)
        request.headers.set(HttpHeaders.cookieHeader, cookie);
      if (csrf.isNotEmpty) request.headers.set('X-Ramos-CSRF', csrf);
      if (body != null) {
        final bytes = utf8.encode(jsonEncode(body));
        request.contentLength = bytes.length;
        request.add(bytes);
      }
      final response = await request.close().timeout(
        const Duration(seconds: 45),
      );
      for (final item in response.cookies) {
        if (item.name == 'ramos_access') {
          cookie = '${item.name}=${item.value}';
          await _save();
        }
      }
      final text = await utf8.decoder
          .bind(response)
          .join()
          .timeout(const Duration(seconds: 45));
      dynamic decoded;
      try {
        decoded = jsonDecode(text);
      } on FormatException {
        throw BackendFailure(
          'O servidor retornou uma resposta inválida. Confira o endereço.',
        );
      }
      if (decoded is! Map)
        throw BackendFailure('Resposta do servidor inválida.');
      final data = Map<String, dynamic>.from(decoded);
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw BackendFailure(
          data['error']?.toString() ?? 'Não foi possível concluir a operação.',
          locked: response.statusCode == 401,
        );
      }
      if (data['csrf'] is String) csrf = data['csrf'];
      return data;
    } on SocketException {
      throw BackendFailure(
        'Sem conexão com o servidor. As tarefas já iniciadas continuam no servidor.',
      );
    } on TimeoutException {
      throw BackendFailure(
        'O servidor demorou para responder. Atualize o andamento antes de repetir a operação.',
      );
    } on TlsException {
      throw BackendFailure(
        'Não foi possível validar a conexão segura com o servidor.',
      );
    } on HttpException {
      throw BackendFailure(
        'A conexão com o servidor foi interrompida. Atualize o andamento antes de repetir a operação.',
      );
    }
  }

  void close() => _http.close(force: true);
}

List<Map<String, dynamic>> rows(dynamic value) => value is List
    ? value.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList()
    : [];
Map<String, dynamic> object(dynamic value) =>
    value is Map ? Map<String, dynamic>.from(value) : {};
bool activeRun(dynamic run) =>
    ['running', 'waiting', 'recovering'].contains(object(run)['status']);
