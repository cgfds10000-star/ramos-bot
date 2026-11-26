import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'bloks_parser.dart';
import 'http_transport.dart';
import 'totp.dart';
export 'http_transport.dart' show WireReply, IoTransport;

const bloksVersion =
    '799db3e387d7a21a19c72918b8698b6cd28a1516ade363792c100ed45a0f9f0b';
const apEntry = 'com.bloks.www.ap.two_step_verification.entrypoint_async';
const apScreen = 'com.bloks.www.ap.two_step_verification.code_entry';
const apVerify = 'com.bloks.www.ap.two_step_verification.code_entry_async';
const totpApps = [
  'com.bloks.www.two_step_verification.entrypoint',
  'com.bloks.www.two_step_verification.method_picker',
  'com.bloks.www.two_step_verification.method_picker.navigation.async',
  'com.bloks.www.two_factor_login.enter_totp_code',
  'com.bloks.www.two_step_verification.verify_code.async',
];

enum LoginStage { credentials, preparing, code, approval, success, failed }

class LoginFailure implements Exception {
  final String message;
  LoginFailure(this.message);
  @override
  String toString() => message;
}

typedef SendRequest = Future<WireReply> Function(
  String host,
  String path,
  Map<String, String> body,
  Map<String, String> headers,
);

abstract class LoginCrypto {
  Future<Map<String, String>> identity();
  Future<String> encrypt(String password, String publicKey, String keyId);
}

abstract class DeviceRegistrationStore {
  Future<void> markDeviceRegistered();
}

class LoginClient {
  final LoginCrypto crypto;
  final SendRequest send;
  final Map<String, dynamic> templates;
  final void Function()? onChange;
  final bool authenticatorOnly;
  final Future<void> Function(Duration) pause;
  final Future<void> Function()? onCheckpoint;
  final void Function()? clearCookies;
  final DateTime Function() totpNow;
  LoginClient({
    required this.crypto,
    required this.send,
    required this.templates,
    this.onChange,
    this.authenticatorOnly = false,
    this.onCheckpoint,
    this.clearCookies,
    Future<void> Function(Duration)? pause,
    DateTime Function()? totpNow,
  }) : pause = pause ?? Future<void>.delayed,
       totpNow = totpNow ?? DateTime.now;
  LoginStage stage = LoginStage.credentials;
  String message = 'Entre para testar o login Android e o codigo 2FA.';
  final List<String> trace = [];
  final List<Map<String, dynamic>> requestTrace = [];
  String failureKind = '';
  Map<String, dynamic> _continuationShape = {};
  Map<String, dynamic> diagnostic() => {
    'revisao': 'flutter-caa-http2-5',
    'transporte': 'dart-http2',
    'protocolo_exigido': 'h2',
    'tls_minima': '1.3',
    'estado': stage.name,
    'etapa_atual': lastStep,
    'erro': failureKind,
    'sessao_recebida': userId.isNotEmpty && _session.isNotEmpty,
    'metodo_2fa': authenticatorOnly ? 'autenticador' : 'auto',
    'parser_contexto': bloksParserCapabilities(),
    if (_continuationShape.isNotEmpty) 'retorno_bloks': _continuationShape,
    'etapas': requestTrace
        .map((row) => Map<String, dynamic>.from(row))
        .toList(),
  };
  bool _busy = false;
  bool get busy => _busy;
  String username = '', userId = '';
  Map<String, String> _identity = {};
  String _mid = '', _auth = '', _session = '', _claim = '0', _rur = '';
  String _context = '', _apContext = '', _nativeId = '', _nativeUsername = '';
  String _aac = '', _nonce = '', _waterfall = '', _method = 'totp';
  String _encrypted = '';
  bool _passwordNonAscii = false;
  String _csrf = '';
  String _pigeon = '';
  String _owner = '';
  bool get hasSavedIdentity => _owner.isNotEmpty;
  Map<String, dynamic> snapshot() => {
    'owner': _owner,
    'mid': _mid,
    'authorization': _auth,
    'session': _session,
    'claim': _claim,
    'rur': _rur,
    'userId': userId,
    'csrf': _csrf,
    'retryAt': retryAt?.toUtc().toIso8601String(),
  };
  void restore(Map<String, dynamic> data) {
    String str(String key) => data[key] is String ? data[key] as String : '';
    _owner = str('owner');
    username = _owner;
    _mid = str('mid');
    _auth = str('authorization');
    _session = str('session');
    _claim = str('claim').isEmpty ? '0' : str('claim');
    _rur = str('rur');
    userId = str('userId');
    _csrf = str('csrf');
    retryAt = DateTime.tryParse(str('retryAt'));
  }

  Future<void> _checkpoint() async {
    await onCheckpoint?.call();
  }

  bool get acceptsBackupCode =>
      !_apCode && _nativeId.isEmpty && !authenticatorOnly;
  bool get acceptsAuthenticatorSecret => !_apCode && _method == 'totp';
  String get codeHint => acceptsAuthenticatorSecret
      ? 'Codigo 2FA ou chave com espacos'
      : acceptsBackupCode
      ? 'Codigo 2FA (6 numeros) ou backup (8)'
      : 'Codigo de 6 digitos';
  DateTime? retryAt;
  String lastStep = '';
  bool _registered = false;
  int get retrySeconds {
    final remaining = retryAt?.difference(DateTime.now()).inMilliseconds ?? 0;
    return remaining <= 0 ? 0 : (remaining / 1000).ceil();
  }

  bool _apCode = false;
  int _approvalChecks = 0;
  void _notify() => onChange?.call();
  void _set(LoginStage value, String text) {
    stage = value;
    message = text;
    _notify();
  }

  void _log(String text) {
    trace.add(text);
    _notify();
  }

  String _uuid() {
    final r = Random.secure();
    final b = List<int>.generate(16, (_) => r.nextInt(256));
    b[6] = (b[6] & 15) | 64;
    b[8] = (b[8] & 63) | 128;
    final h = b.map((v) => v.toRadixString(16).padLeft(2, '0')).join();
    return '${h.substring(0, 8)}-${h.substring(8, 12)}-${h.substring(12, 16)}-${h.substring(16, 20)}-${h.substring(20)}';
  }

  Future<void> _guard(Future<void> Function() action) async {
    if (_busy) return;
    if (retrySeconds > 0) {
      message =
          'O Instagram pediu uma pausa. Aguarde $retrySeconds segundos antes de tentar novamente.';
      _notify();
      return;
    }
    _busy = true;
    _notify();
    try {
      await action();
    } on TransportFailure catch (e) {
      failureKind = 'transporte_http2';
      _set(LoginStage.failed, e.message);
    } on TlsException {
      failureKind = 'tls';
      _set(
        LoginStage.failed,
        'A conexao TLS 1.3 nao foi concluida. Nenhuma etapa foi repetida. Copie o diagnostico para conferir a etapa.',
      );
    } on LoginFailure catch (e) {
      if (failureKind.isEmpty) failureKind = 'fluxo_login';
      _set(LoginStage.failed, e.message);
    } on FormatException catch (_) {
      failureKind = 'formato';
      _set(
        LoginStage.failed,
        'O contexto retornado pelo servidor nao pode ser usado com seguranca. Reinicie o login.',
      );
    } on TimeoutException {
      failureKind = 'timeout';
      _set(
        LoginStage.failed,
        'Tempo esgotado. A etapa nao foi repetida. Reinicie o login.',
      );
    } on SocketException {
      failureKind = 'conexao';
      _set(LoginStage.failed, 'Falha de conexao. A etapa nao foi repetida.');
    } catch (_) {
      failureKind = 'falha_interna';
      _set(
        LoginStage.failed,
        'Nao foi possivel concluir esta tentativa. Reinicie o login.',
      );
    } finally {
      _encrypted = '';
      _busy = false;
      _notify();
    }
  }

  Future<WireReply> _request(
    String route,
    Map<String, String> body, {
    String host = 'i.instagram.com',
    Map<String, String> extra = const {},
    bool graphql = false,
    bool allow2fa = false,
    bool signed = false,
    bool allowLoginRequired = false,
  }) async {
    final headers = <String, String>{
      'User-Agent': 'Instagram 449.0.0.52.84 Android (34/14; 480dpi; 1344x2992; Google/google; Pixel 8 Pro; husky; husky; pt_BR; 1079242191)',
      'X-IG-App-ID': '567067343352427',
      'X-Pigeon-Session-Id': _pigeon,
      'X-Pigeon-Rawclienttime': (DateTime.now().millisecondsSinceEpoch / 1000)
          .toStringAsFixed(3),
      'X-IG-Bandwidth-Speed-KBPS': '-1.000',
      'X-IG-Bandwidth-TotalBytes-B': '0',
      'X-IG-Bandwidth-TotalTime-MS': '0',
      'Priority': 'u=3',
      'X-Zero-Balance': 'INIT',
      'X-Zero-Eh': '',
      'X-Zero-State': 'unknown',
      'Zero-HTTP-Network-Interface': 'wifi',
      'Connection': 'keep-alive',
      'X-FB-Client-IP': 'True',
      'X-FB-Server-Cluster': 'True',
      'X-IG-Nav-Chain': '9MV:self_profile:2,ProfileMediaTabFragment:self_profile:3,9Xf:self_following:4',
      'X-Bloks-Version-Id': bloksVersion,
      'X-IG-App-Locale': 'pt_BR',
      'X-IG-Device-Locale': 'pt_BR',
      'X-IG-Mapped-Locale': 'pt_BR',
      'Accept-Language': 'pt-BR, en-US',
      'Accept-Encoding': 'gzip, deflate',
      'X-IG-App-Startup-Country': 'BR',
      'X-IG-Device-ID': _identity['uuid'] ?? '',
      'X-IG-Family-Device-ID': _identity['phone'] ?? '',
      'X-IG-Android-ID': _identity['android'] ?? '',
      'X-IG-Timezone-Offset': '-10800',
      'X-IG-Connection-Type': 'WIFI',
      'X-IG-Capabilities': '3brTv10=',
      'X-Bloks-Is-Layout-RTL': 'false',
      'X-Bloks-Is-Panorama-Enabled': 'true',
      'X-FB-HTTP-Engine': 'Tigon/MNS/TCP',
      'X-Tigon-Is-Retry': 'False',
      'X-IG-WWW-Claim': _claim,
      'X-MID': _mid,
      'IG-INTENDED-USER-ID': userId.isEmpty ? '0' : userId,
      'X-Meta-Usdid': _identity['usdidHeader'] ?? '',
      if (_auth.isNotEmpty) 'Authorization': _auth,
      if (_rur.isNotEmpty) 'IG-U-RUR': _rur,
      ...extra,
    };
    final payload = signed
        ? {'signed_body': 'SIGNATURE.${jsonEncode(body)}'}
        : body;
    if (graphql) await pause(const Duration(seconds: 1));
    lastStep = _stepName(route, graphql);
    final response = await send(
      host,
      graphql ? '/graphql_www' : '/api/v1/$route',
      payload,
      headers,
    );
    _mid =
        response.headers['ig-set-x-mid'] ?? response.headers['x-mid'] ?? _mid;
    _claim = response.headers['x-ig-set-www-claim'] ?? _claim;
    _rur = response.headers['ig-set-ig-u-rur'] ?? _rur;
    final cookieHeader = response.headers['set-cookie'] ?? '';
    for (final name in ['mid', 'csrftoken']) {
      final m = RegExp('(?:^|[;, ]+)' + name + r'=([^; ,]+)')
          .firstMatch(cookieHeader);
      if (m != null) {
        if (name == 'mid') {
          _mid = m.group(1)!;
        } else {
          _csrf = m.group(1)!;
        }
      }
    }
    final authorization = response.headers['ig-set-authorization'];
    if (authorization != null && authorization.isNotEmpty)
      _auth = authorization;
    lastStep = _stepName(route, graphql);
    final format = response.isJsonObject ? 'JSON' : 'resposta sem JSON';
    requestTrace.add({
      'etapa': lastStep,
      'http': response.status,
      'json': response.isJsonObject,
      ...response.transportInfo,
    });
    final protocol = response.transportInfo['http_version'];
    _log(
      'HTTP ${response.status} · $lastStep · $format${protocol == null ? '' : ' · HTTP/$protocol (ALPN ${response.transportInfo['alpn']})'}',
    );
    await _checkpoint();
    if (allowLoginRequired && _loginRequired(response)) return response;
    if (response.status == 429) {
      failureKind = 'http_429';
      final raw = response.headers['retry-after'];
      if (raw != null) {
        final seconds = int.tryParse(raw.trim());
        if (seconds != null && seconds >= 0) {
          retryAt = DateTime.now().add(Duration(seconds: seconds));
        } else {
          try {
            retryAt = HttpDate.parse(raw);
          } on HttpException {
            /* no retry time */
          }
        }
      }
      await _checkpoint();
      throw LoginFailure(
        'O Instagram retornou HTTP 429 em $lastStep. '
        'A tentativa foi interrompida antes do 2FA. '
        '${retrySeconds > 0 ? "Aguarde $retrySeconds segundos (prazo do servidor)." : "Aguarde antes de tentar novamente; o servidor nao informou um prazo."}',
      );
    }
    if (response.status >= 300 &&
        !(allow2fa && response.data['two_factor_required'] == true)) {
      failureKind = 'http_${response.status}';
      final kind = response.data['error_type'];
      final safe =
          kind is String && RegExp(r'^[A-Za-z0-9_.-]{1,80}$').hasMatch(kind)
          ? ' · $kind'
          : '';
      throw LoginFailure(
        'O Instagram recusou esta etapa (HTTP ${response.status}$safe). Nenhuma etapa foi repetida.',
      );
    }
    if (!response.isJsonObject) {
      failureKind = 'resposta_sem_json';
      throw LoginFailure(
        'O servidor retornou HTTP ${response.status} sem JSON em $lastStep. A etapa nao foi repetida.',
      );
    }
    if (response.data['status'] == 'fail' &&
        !(allow2fa && response.data['two_factor_required'] == true)) {
      failureKind = 'resposta_recusada';
      final error = response.data['error_type'];
      final symbol =
          error is String && RegExp(r'^[A-Za-z0-9_.-]{1,80}$').hasMatch(error)
          ? ' ($error)'
          : '';
      throw LoginFailure(
        'O Instagram recusou $lastStep$symbol. Nenhuma etapa foi repetida.',
      );
    }
    return response;
  }

  bool _loginRequired(WireReply reply) =>
      reply.status == 401 ||
      reply.data['error_type'] == 'login_required' ||
      reply.data['message'] == 'login_required';

  static String _stepName(String route, bool graphql) {
    if (graphql) return 'registro do dispositivo';
    if (route.contains('accounts/current_user'))
      return 'validacao da sessao salva';
    if (route.contains('process_client_data')) return 'preparacao CAA / AAC';
    if (route.contains('oauth.token')) return 'preparacao OAuth';
    if (route.contains('send_login_request')) return 'envio do login CAA';
    if (route.contains('launcher')) return 'chave da senha';
    if (route.contains('attestation')) return 'preparacao do dispositivo';
    if (route.contains('verify_code') ||
        route.contains('code_entry_async') ||
        route.contains('two_factor_login/'))
      return 'envio do codigo 2FA';
    if (route.contains('method_picker.navigation'))
      return 'selecao do metodo 2FA';
    if (route.contains('method_picker')) return 'metodos de verificacao 2FA';
    if (route.contains('enter_totp_code') || route.contains('code_entry/'))
      return 'tela do codigo 2FA';
    return 'entrada da verificacao 2FA';
  }

  Object? _replace(Object? value) {
    if (value is Map)
      return value.map((k, v) => MapEntry(k.toString(), _replace(v)));
    if (value is List) return value.map(_replace).toList();
    final vars = <String, String>{
      '@uuid': _identity['uuid'] ?? '',
      '@phone': _identity['phone'] ?? '',
      '@android': _identity['android'] ?? '',
      '@mid': _mid,
      '@username': username,
      '@aac': _aac,
      '@waterfall': _waterfall,
      '@password': _encrypted,
      '@context': _context,
      '@apContext': _apContext,
      '@method': _method,
    };
    if (value is String && vars.containsKey(value)) return vars[value];
    if (value == 1234567890123) return DateTime.now().millisecondsSinceEpoch;
    return value;
  }

  Map<String, dynamic> _caaSignals(Object? data) {
    const markerNames = {
      'CAA_LOGIN_FALLBACK',
      'CAA_LOGIN_FORM',
      'CAA_LOGIN_SUCCESS',
      'CAA_LOGIN_ERROR',
      'CAA_LOGIN_2FA',
    };
    final literalMarkers = <String>{}, textMarkers = <String>{};
    final possibleMessages = <String>{};
    final patterns = <String, RegExp>{
      'senha_incorreta': RegExp(
        r'incorrect password|password (?:you entered |that you entered |that you.ve entered )?is incorrect|senha (?:que voce (?:digitou|inseriu) (?:esta |e )?)?incorreta|senha esta errada',
      ),
      'usuario_nao_encontrado': RegExp(
        r'(?:couldn.t|could not|cannot|can.t) find (?:an account|your account|the username)|username (?:you entered )?doesn.t|nao (?:foi possivel |conseguimos )?encontrar (?:sua |uma |a )?conta|nome de usuario (?:inserido |digitado )?nao',
      ),
      'aguardar': RegExp(
        r'please wait a few minutes|try again (?:in a few minutes|later)|aguarde alguns minutos|espere alguns minutos|tente novamente (?:mais tarde|em alguns minutos)',
      ),
      'senha_ou_usuario_recusado': RegExp(
        r'invalid credentials|invalid username or password|usuario ou senha incorret',
      ),
      'atualizacao': RegExp(
        r'needs_upgrade|version of instagram is out of date|update instagram to (?:continue|log in)|atualize o instagram para|versao do instagram esta desatualizada',
      ),
      'conta_desativada': RegExp(
        r'your account has been (?:disabled|suspended)|sua conta (?:foi|esta) (?:desativada|suspensa)',
      ),
      'confirmar_identidade': RegExp(
        r'confirm (?:that )?you.re (?:a real person|human)|confirm your identity|confirme sua identidade|confirme que voce (?:e uma pessoa|e humano)',
      ),
    };
    var fallback = false, inspected = 0, limited = false;
    String normalize(String value) {
      var text = value.toLowerCase().replaceAll('’', "'");
      for (final pair in const {
        'a': 'áàâãä',
        'e': 'éèêë',
        'i': 'íìîï',
        'o': 'óòôõö',
        'u': 'úùûü',
        'c': 'ç',
      }.entries) {
        text = text.replaceAll(RegExp('[${pair.value}]'), pair.key);
      }
      return text;
    }

    void inspect(String value) {
      if (inspected + value.length > 2097152) {
        limited = true;
        return;
      }
      inspected += value.length;
      for (final name in markerNames) {
        if (value.contains('$name:')) textMarkers.add(name);
      }
      final normalized = normalize(value);
      for (final row in patterns.entries) {
        if (row.value.hasMatch(normalized)) possibleMessages.add(row.key);
      }
    }

    for (final value in responseValues(data).whereType<String>()) {
      // This is exactly the marker condition used by Client.login. The
      // supplied wrapper deliberately stops instead of calling login_legacy.
      if (value.startsWith('CAA_LOGIN_FALLBACK:')) fallback = true;
      for (final name in markerNames) {
        if (value.startsWith('$name:')) literalMarkers.add(name);
      }
      inspect(value);
      // UI messages can be JSON string literals inside an action program.
      // Decode literals only; never execute operators or export their values.
      if (value.length > 524288 || !value.contains('(')) continue;
      for (var i = 0, count = 0; i < value.length && count < 256; i++) {
        if (value[i] != '"') continue;
        count++;
        try {
          final parser = LiteralParser(value, i);
          final literal = parser.read();
          i = parser.offset - 1;
          if (literal is String) inspect(literal);
        } on FormatException {
          /* Not a literal. */
        }
      }
    }
    return {
      'fallback_legacy_solicitado': fallback,
      'marcadores_literais': literalMarkers.toList()..sort(),
      'marcadores_no_texto': textMarkers.toList()..sort(),
      // These are occurrences, not proof of which bundled UI is active.
      'mensagens_possiveis_na_resposta': possibleMessages.toList()..sort(),
      'limite_textual_atingido': limited,
    };
  }

  Future<Map<String, dynamic>> _step(String name, {String? code}) async {
    final template = templates[name] as Map;
    final params = _replace(template['params']) as Map;
    if (name == 'login') {
      (params['client_input_params'] as Map)['password_contains_non_ascii'] =
          _passwordNonAscii.toString();
      final prefix = '${_uuid().replaceAll('-', '').substring(0, 4)}ig';
      (params['server_params'] as Map)['password_text_input_id'] = '$prefix:82';
      (params['server_params'] as Map)['username_text_input_id'] = '$prefix:81';
    }
    if (code != null) (params['client_input_params'] as Map)['code'] = code;
    if (name == 'verify') {
      (params['client_input_params'] as Map)['should_trust_device'] =
          authenticatorOnly ? 0 : 1;
    }
    if (!authenticatorOnly &&
        ['entry', 'picker', 'select', 'backup', 'verify'].contains(name))
      await pause(const Duration(seconds: 1));
    final caa = [
      'home',
      'oauth',
      'login',
      'apEntry',
      'apScreen',
      'apVerify',
    ].contains(name);
    final response = await _request(
      template['route'] as String,
      {
        'params': jsonEncode(params),
        '_uuid': _identity['uuid']!,
        'bk_client_context': jsonEncode({
          'bloks_version': bloksVersion,
          'styles_id': 'instagram',
        }),
        'bloks_versioning_id': bloksVersion,
      },
      host: caa ? 'b.i.instagram.com' : 'i.instagram.com',
      allow2fa: name == 'login',
      extra: {
        if ((template['route'] as String).contains('async_action'))
          'X-FB-Friendly-Name': 'IgApi: ${template['route']}',
        if (name == 'login')
          'X-IG-Attest-Params': jsonEncode({
            'attestation': [
              {
                'version': 2,
                'type': 'keystore',
                'errors': [-1013],
                'challenge_nonce': _nonce,
                'signed_nonce': '',
                'key_hash': '',
              },
            ],
          }),
      },
    );
    if (name == 'login' ||
        name.startsWith('ap') ||
        ['entry', 'picker', 'select', 'screen', 'verify'].contains(name)) {
      // Retain only a redacted schema, never the raw login response.
      _continuationShape = {
        'etapa': name,
        'sinais_caa': _caaSignals(response.data),
        ...bloksContinuationDiagnostic(response.data),
      };
    }
    return response.data;
  }

  Future<void> login(String user, String password) => _guard(() async {
    if (stage != LoginStage.credentials && stage != LoginStage.failed) return;
    if (user.trim().isEmpty || password.isEmpty)
      throw LoginFailure('Informe usuario e senha.');
    if (_owner.isNotEmpty &&
        _owner.toLowerCase() != user.trim().toLowerCase()) {
      throw LoginFailure(
        'A sessao salva pertence a outro usuario. Toque em Limpar sessao salva para testar outra conta.',
      );
    }
    _owner = user.trim();
    username = user.trim();
    _passwordNonAscii = password.codeUnits.any((unit) => unit > 127);
    if (_csrf.isEmpty) {
      const symbols =
          'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
      final random = Random.secure();
      _csrf = List.generate(
        64,
        (_) => symbols[random.nextInt(symbols.length)],
      ).join();
    }
    trace.clear();
    requestTrace.clear();
    failureKind = '';
    _continuationShape = {};
    _set(LoginStage.preparing, 'Preparando login Android...');
    _identity = await crypto.identity();
    if (_pigeon.isEmpty) _pigeon = 'UFS-${_uuid()}-1';
    _registered = _registered || _identity['registered'] == 'true';
    if (userId.isNotEmpty && _session.isNotEmpty) {
      _set(LoginStage.preparing, 'Validando sessao salva...');
      final response = await _request(
        'accounts/current_user/?edit=true',
        {},
        allowLoginRequired: true,
      );
      if (!_loginRequired(response)) {
        final account = response.data['user'];
        if (account is! Map || account['pk']?.toString() != userId)
          throw LoginFailure(
            'O servidor nao confirmou o usuario da sessao salva.',
          );
        _set(
          LoginStage.success,
          'Login confirmado! Usuario: @$username · ID $userId',
        );
        await _checkpoint();
        return;
      }
      _auth = '';
      _session = '';
      userId = '';
      clearCookies?.call();
      _log('A sessao anterior expirou. Continuando pelo login CAA.');
    }
    await _checkpoint();

    _waterfall = _uuid();
    _context = '';
    _apContext = '';
    _nativeId = '';
    _nativeUsername = '';
    _apCode = false;
    _approvalChecks = 0;
    _method = 'totp';
    _auth = '';
    _session = '';
    userId = '';
    if (!_registered) {
      final reg = await _request(
        '',
        {
          'method': 'post',
          'pretty': 'false',
          'format': 'json',
          'server_timestamps': 'true',
          'locale': 'user',
          'fb_api_req_friendly_name': 'IGUSDIDRegistrationMutation',
          'enable_canonical_naming': 'true',
          'enable_canonical_variable_overrides': 'true',
          'enable_canonical_naming_ambiguous_type_prefixing': 'true',
          'purpose': 'fetch',
          'client_doc_id': '124930351917786857261002920888',
          'variables': jsonEncode({
            'input': {
              'usdid_token': {
                'sensitive_string_value': _identity['registration'],
              },
              'fdid': {'sensitive_string_value': _identity['phone']},
            },
          }),
        },
        host: 'b.i.instagram.com',
        graphql: true,
        extra: {
          'X-FB-Friendly-Name': 'IGUSDIDRegistrationMutation',
          'X-Client-Doc-Id': '124930351917786857261002920888',
          'X-Root-Field-Name': 'usdid_registration',
          'X-Graphql-Client-Library': 'pando',
        },
      );
      if (!responseValues(reg.data['data'])
          .whereType<Map>()
          .any((v) => v['success'] == true))
        throw LoginFailure('O registro do dispositivo nao foi confirmado.');
      _registered = true;
      final store = crypto;
      if (store is DeviceRegistrationStore)
        await (store as DeviceRegistrationStore).markDeviceRegistered();
    } else {
      _log('Registro do dispositivo preservado da tentativa anterior.');
    }
    final home = await _step('home');
    _aac = '';
    for (final v in responseValues(home).whereType<Map>()) {
      if (v['key'] != 'CAA_ACCOUNT_ACCESS_CONTEXT:aac') continue;
      if (v['initial'] is String && (v['initial'] as String).isNotEmpty) {
        _aac = v['initial'] as String;
        break;
      }
      final lispy = v['initial_lispy'];
      if (lispy is String) {
        final start = lispy.indexOf('"');
        if (start >= 0) {
          final parsed = LiteralParser(lispy, start).read();
          if (parsed is String) _aac = parsed;
        }
      }
    }
    final nonce = await _request(
      'attestation/create_android_keystore/',
      {'app_scoped_device_id': _identity['uuid']!, 'key_hash': ''},
      host: 'b.i.instagram.com',
      extra: {
        'X-FB-Friendly-Name': 'IgApi: attestation/create_android_keystore/',
      },
    );
    _nonce = nonce.data['challenge_nonce']?.toString() ?? '';
    if (_aac.isEmpty || _nonce.isEmpty)
      throw LoginFailure(
        'A preparacao CAA nao devolveu o contexto e o nonce necessarios.',
      );
    await _step('oauth');
    final keys = await _request('launcher/sync/', {
      'id': _identity['uuid']!,
      'server_config_retrieval': '1',
    }, signed: true);
    final publicKey = keys.headers['ig-set-password-encryption-pub-key'];
    final keyId = keys.headers['ig-set-password-encryption-key-id'];
    if (publicKey == null || keyId == null)
      throw LoginFailure(
        'O servidor nao devolveu a chave para criptografar a senha.',
      );
    _encrypted = await crypto.encrypt(password, publicKey, keyId);
    final result = await _step('login');
    _encrypted = '';
    await _continue(result);
  });
  String _find2fa(Object? data) {
    final set = <String>{};
    for (final v in responseValues(data).whereType<Map>()) {
      final s = v['two_step_verification_context'];
      if (s is String && s.trim().isNotEmpty) set.add(s.trim());
    }
    for (final app in totpApps) {
      final c = extractContext(data, app, 'two_step_verification_context');
      if (c.isNotEmpty) set.add(c.trim());
    }
    if (set.length > 1)
      throw LoginFailure(
        'Contextos 2FA diferentes. Nenhum codigo foi enviado.',
      );
    return set.isEmpty ? '' : set.single;
  }

  Future<bool> _accept(Object? data) async {
    for (final v in embeddedValues(data).whereType<Map>()) {
      var headers = v['headers'];
      if (headers is String) {
        try {
          headers = jsonDecode(headers);
        } on FormatException {
          headers = null;
        }
      }
      if (headers is Map) {
        final h = headers.map(
          (k, v) => MapEntry(k.toString().toLowerCase(), v.toString()),
        );
        _auth = h['ig-set-authorization'] ?? _auth;
        _rur = h['ig-set-ig-u-rur'] ?? _rur;
        _claim = h['x-ig-set-www-claim'] ?? _claim;
      }
      final cookies = v['cookies'];
      if (cookies is Map)
        _session = cookies['sessionid']?.toString() ?? _session;
      if (cookies is String) {
        final m = RegExp(r'(?:^|[\s;,])sessionid=([^;\s,]+)')
            .firstMatch(cookies);
        if (m != null) _session = m.group(1)!;
      }
      Object? login = v['login_response'] ?? v;
      if (login is String) {
        try {
          login = jsonDecode(login);
        } on FormatException {
          login = null;
        }
      }
      if (login is Map && login['logged_in_user'] is Map) {
        final user = login['logged_in_user'] as Map;
        userId = user['pk']?.toString() ?? '';
      }
    }
    String authUser = '';
    if (_auth.startsWith('Bearer IGT:2:')) {
      try {
        final decoded = jsonDecode(
          utf8.decode(base64.decode(base64.normalize(_auth.substring(13)))),
        );
        if (decoded is Map) {
          authUser = decoded['ds_user_id']?.toString() ?? '';
          _session = decoded['sessionid']?.toString() ?? _session;
        }
      } on FormatException {
        /* invalid auth */
      }
    }
    if (userId.isEmpty) userId = authUser;
    if (!RegExp(r'^[1-9][0-9]{0,29}$').hasMatch(userId) ||
        _session.isEmpty ||
        (authUser.isNotEmpty && authUser != userId))
      return false;
    await _checkpoint();
    // A returned UI or HTTP 200 alone never counts as login success.
    _set(
      LoginStage.success,
      'Login confirmado! Usuario: @$username · ID $userId',
    );
    return true;
  }

  Future<void> _continue(Map<String, dynamic> data) async {
    if (await _accept(data)) return;
    // The Python default (2fa=auto) follows AP before the generic 2FA fallback.
    final profileContext = extractContext(data, apEntry);
    if (!authenticatorOnly && profileContext.isNotEmpty) {
      _apContext = profileContext;
      await _profileEntry();
      return;
    }
    _context = _find2fa(data);
    if (_context.isNotEmpty) {
      _method =
          _responseBool(data, 'sms_two_factor_on') &&
              !_responseBool(data, 'totp_two_factor_on')
          ? 'sms'
          : 'totp';
      if (authenticatorOnly) {
        _method = 'totp';
        for (final step in ['entry', 'picker', 'select', 'screen']) {
          final next = await _step(step);
          if (await _accept(next)) return;
          final c = _find2fa(next);
          if (c.isNotEmpty) _context = c;
        }
      }
      _set(
        LoginStage.code,
        authenticatorOnly
            ? 'Digite o codigo de 6 digitos do seu autenticador.'
            : 'Digite o codigo numerico 2FA solicitado pelo Instagram.',
      );
      return;
    }
    final native = <String, Map>{};
    for (final v in responseValues(data).whereType<Map>()) {
      final info = v['two_factor_info'];
      if (info is Map &&
          info['two_factor_identifier'] is String &&
          [true, 1, '1', 'true'].contains(
            info['totp_two_factor_on'] ?? info['is_totp_two_factor_enabled'],
          ))
        native[info['two_factor_identifier'] as String] = info;
    }
    if (native.length > 1)
      throw LoginFailure(
        'Identificadores 2FA diferentes. Nenhum codigo foi enviado.',
      );
    if (native.isNotEmpty) {
      _nativeId = native.keys.single;
      _nativeUsername =
          native.values.single['username']?.toString() ?? username;
      _set(
        LoginStage.code,
        'Digite o codigo de 6 digitos do seu autenticador.',
      );
      return;
    }
    final c = extractContext(data, apEntry);
    if (c.isNotEmpty) {
      _apContext = c;
      await _profileEntry();
      return;
    }
    final signals = _caaSignals(data);
    if (signals['fallback_legacy_solicitado'] == true) {
      failureKind = 'caa_fallback_legacy_interrompido';
      throw LoginFailure(
        'O retorno CAA solicitou a rota antiga, sem fornecer sessao ou contexto 2FA. '
        'A tentativa foi interrompida. Copie o diagnostico para conferir os sinais retornados.',
      );
    }
    if ((signals['marcadores_literais'] as List).contains('CAA_LOGIN_FORM')) {
      failureKind = 'caa_retorno_formulario';
      throw LoginFailure(
        'O CAA devolveu o formulario de login sem fornecer sessao ou contexto 2FA. '
        'Copie o diagnostico para conferir os sinais retornados.',
      );
    }
    failureKind = 'caa_sem_continuacao_reconhecida';
    throw LoginFailure(
      'O login CAA respondeu, mas a continuacao nao foi reconhecida. '
      'Copie o diagnostico desta versao para conferir a estrutura retornada.',
    );
  }

  bool _responseBool(Object? data, String key) {
    for (final v in responseValues(data).whereType<Map>()) {
      if (v.containsKey(key))
        return [true, 1, '1', 'true', 'yes'].contains(
          v[key] is String ? (v[key] as String).toLowerCase() : v[key],
        );
    }
    return false;
  }

  Future<void> _profileEntry() async {
    final next = await _step('apEntry');
    if (await _accept(next)) return;
    if (_find2fa(next).isNotEmpty) {
      await _continue(next);
      return;
    }
    final c = extractContext(next, apScreen);
    if (c.isNotEmpty) {
      _apContext = c;
      final screen = await _step('apScreen');
      if (await _accept(screen)) return;
      _apContext = extractContext(screen, apVerify);
      if (_apContext.isEmpty)
        throw LoginFailure('Nao foi recebido contexto para enviar o codigo.');
      _apCode = true;
      _set(
        LoginStage.code,
        'Digite o codigo numerico solicitado pelo Instagram.',
      );
      return;
    }
    if (responseValues(next)
        .whereType<String>()
        .any((v) => v.contains('ap.two_step_verification.approve'))) {
      final c = extractContext(next, apEntry);
      if (c.isNotEmpty) _apContext = c;
      _set(
        LoginStage.approval,
        'O Instagram solicitou aprovacao em outro aparelho. Confirme no Instagram e toque em Verificar aprovacao.',
      );
      return;
    }
    throw LoginFailure(
      'A verificacao nao devolveu uma tela de codigo reconhecida.',
    );
  }

  Future<void> checkApproval() => _guard(() async {
    if (stage != LoginStage.approval) return;
    if (++_approvalChecks > 3)
      throw LoginFailure(
        'A aprovacao ainda nao foi confirmada. Reinicie o login.',
      );
    await _profileEntry();
  });
  Future<void> submitCode(String input) => _guard(() async {
    if (stage != LoginStage.code) return;
    VerificationEntry entry;
    try {
      entry = parseVerificationEntry(
        input,
        allowSecret: acceptsAuthenticatorSecret,
        allowBackup: acceptsBackupCode,
      );
    } on FormatException {
      message = codeHint;
      _notify();
      return;
    }
    var code = entry.code ?? '';
    _set(LoginStage.preparing, 'Verificando codigo 2FA...');
    Map<String, dynamic> result;
    if (_nativeId.isNotEmpty) {
      if (entry.secret != null)
        code = generateTotp(entry.secret!, at: totpNow());
      final res = await _request('accounts/two_factor_login/', {
        'verification_code': code,
        'verification_method': '3',
        'two_factor_identifier': _nativeId,
        'username': _nativeUsername,
        'phone_id': _identity['phone']!,
        'guid': _identity['uuid']!,
        'device_id': _identity['android']!,
        'trust_this_device': '0',
        '_csrftoken': _csrf,
      }, signed: true);
      result = res.data;
    } else {
      if (_apCode ? _apContext.isEmpty : _context.isEmpty)
        throw LoginFailure('Contexto 2FA ausente. Reinicie o login.');
      if (!_apCode && !authenticatorOnly) {
        // Same order as _login_with_bloks_two_factor: ask code, entry, picker,
        // select, optional backup screen, then one verify request.
        if (code.length == 8) _method = 'backup_codes';
        for (final step in [
          'entry',
          'picker',
          'select',
          if (_method == 'backup_codes') 'backup',
        ]) {
          final next = await _step(step);
          if (await _accept(next)) return;
          final current = _find2fa(next);
          if (current.isNotEmpty) _context = current;
        }
      }
      if (entry.secret != null)
        code = generateTotp(entry.secret!, at: totpNow());
      result = await _step(_apCode ? 'apVerify' : 'verify', code: code);
    }
    if (!await _accept(result))
      throw LoginFailure(
        'O codigo nao confirmou uma sessao. Confira o codigo e reinicie o login. O envio nao foi repetido.',
      );
  });
  Future<Map<String, dynamic>> serverSession({bool allowStored = false}) async {
    if ((!allowStored && stage != LoginStage.success) || userId.isEmpty || _session.isEmpty) {
      throw LoginFailure('Conclua o login no celular antes de conectar ao servidor.');
    }
    final identity = _identity.isEmpty ? await crypto.identity() : _identity;
    return {
      'schema': 1,
      'username': username,
      'user_id': userId,
      'sessionid': _session,
      'authorization': _auth,
      'mid': _mid,
      'claim': _claim,
      'rur': _rur,
      'csrf': _csrf,
      'device': {'uuid': identity['uuid'], 'phone': identity['phone'], 'android': identity['android']},
    };
  }

  void reset() {
    if (_busy) return;
    _context = '';
    _apContext = '';
    _nativeId = '';
    _auth = '';
    _session = '';
    _encrypted = '';
    _aac = '';
    _nonce = '';
    userId = '';
    username = '';
    trace.clear();
    requestTrace.clear();
    failureKind = '';
    _continuationShape = {};
    _set(
      LoginStage.credentials,
      'Entre para testar o login Android e o codigo 2FA.',
    );
  }
}
