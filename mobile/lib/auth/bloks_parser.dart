import 'dart:convert';

// Parse literal structures only. Never evaluate server operators.
class Expr {
  final String operator;
  final List<Object?> args;
  Expr(this.operator, this.args);
}

class Atom {
  final String value;
  Atom(this.value);
}

class LiteralParser {
  final String text;
  int offset;
  int budget = 10000;
  LiteralParser(this.text, [this.offset = 0]);
  void skip() {
    while (offset < text.length && RegExp(r'[\s,]').hasMatch(text[offset])) {
      offset++;
    }
  }

  Object? read([int depth = 0]) {
    if (depth > 80 || --budget < 0)
      throw const FormatException('structure limit');
    skip();
    if (offset >= text.length) throw const FormatException('incomplete');
    if (text[offset] == '"') {
      final start = offset++;
      while (offset < text.length) {
        if (text[offset] == '\\') {
          offset += 2;
          continue;
        }
        if (text[offset++] == '"')
          return jsonDecode(text.substring(start, offset));
      }
      throw const FormatException('string');
    }
    if (text[offset] == '(') {
      offset++;
      final op = read(depth + 1);
      if (op is! Atom) throw const FormatException('operator');
      final args = <Object?>[];
      while (true) {
        skip();
        if (offset >= text.length) throw const FormatException('unclosed');
        if (text[offset] == ')') {
          offset++;
          return Expr(op.value, args);
        }
        args.add(read(depth + 1));
      }
    }
    final m = RegExp(r'[^\s,()"]+').matchAsPrefix(text, offset);
    if (m == null) throw const FormatException('atom');
    offset = m.end;
    return Atom(m.group(0)!);
  }
}

Iterable<Object?> responseValues(Object? root) sync* {
  final pending = <(Object?, int)>[(root, 0)];
  var count = 0;
  while (pending.isNotEmpty && count++ < 60000) {
    final (v, layer) = pending.removeLast();
    yield v;
    if (v is Map) {
      pending.addAll(v.values.map((e) => (e, layer)));
    } else if (v is List) {
      pending.addAll(v.map((e) => (e, layer)));
    } else if (v is String && layer < 3 && v.length <= 524288) {
      try {
        final decoded = jsonDecode(v);
        if (decoded != v) pending.add((decoded, layer + 1));
      } on FormatException {
        /* ordinary strings */
      }
    }
  }
}

String staticMapContext(Object? expr, String key) {
  if (expr is! Expr || expr.args.length != 2) return '';
  final keys = expr.args[0], vals = expr.args[1];
  if (keys is! Expr ||
      vals is! Expr ||
      keys.operator != vals.operator ||
      keys.args.length != vals.args.length ||
      keys.args.any((k) => k is! String) ||
      keys.args.toSet().length != keys.args.length)
    return '';
  final map = Map<String, Object?>.fromIterables(
    keys.args.cast<String>(),
    vals.args,
  );
  if (map.containsKey(key)) return map[key] is String ? map[key] as String : '';
  var server = map['server_params'];
  if (server is Expr) return staticMapContext(server, key);
  if (server is String) {
    try {
      server = jsonDecode(server);
    } on FormatException {
      return '';
    }
  }
  return server is Map && server[key] is String ? server[key] as String : '';
}

// instagrapi first looks for the f4i parameter map after the exact app id.
// An argument list/GetArg may appear before that map. Reading only the first
// parenthesized expression loses the context even when the Python reads it.
String _contextAfterApp(String section, String key) {
  final followingApp = RegExp(r'(?<![_a-zA-Z0-9])com\.bloks\.[_a-zA-Z0-9.]+')
      .firstMatch(section);
  final legacyStart = section.indexOf('(f4i');
  if (legacyStart >= 0 &&
      (followingApp == null || legacyStart < followingApp.start)) {
    try {
      // Check ownership before the map starts, as Python does. An app name
      // inside an already-started map is a value, not a new map boundary.
      final value = staticMapContext(
        LiteralParser(section, legacyStart).read(),
        key,
      );
      if (value.isNotEmpty) return value;
    } on FormatException {
      // Continue with the wrapper's bounded alias-map fallback.
    }
  }
  final end = followingApp?.start ?? section.length;
  final bounded = section.substring(0, end > 131072 ? 131072 : end);
  final first = bounded.indexOf('(');
  if (first < 0) return '';
  try {
    return staticMapContext(LiteralParser(bounded, first).read(), key);
  } on FormatException {
    return '';
  }
}

String extractContext(Object? data, String app, [String key = 'context_data']) {
  final candidates = <String>{};
  for (final v in responseValues(data)) {
    if (v is Map && v['app_id'] == app) {
      Object? p = v['params'] ?? v['server_params'];
      if (p is String) {
        try {
          p = jsonDecode(p);
        } on FormatException {
          p = null;
        }
      }
      if (p is Map) {
        p = p['server_params'] ?? p;
      }
      if (p is Map && p[key] is String) candidates.add(p[key] as String);
    }
    if (v is! String) continue;
    var start = 0;
    for (var n = 0; n < 24; n++) {
      final index = v.indexOf(jsonEncode(app), start);
      if (index < 0) break;
      start = index + jsonEncode(app).length;
      final section = v.substring(start);
      final context = _contextAfterApp(section, key);
      if (context.isNotEmpty) candidates.add(context);
    }
  }
  candidates.remove('');
  if (candidates.length > 1)
    throw const FormatException(
      'O servidor devolveu contextos diferentes. Reinicie o login.',
    );
  return candidates.isEmpty ? '' : candidates.single;
}

// Diagnostics contain schema names and value types only, never scalar values.
const _diagnosticFields = {
  'status',
  'layout',
  'bloks_payload',
  'action',
  'data',
  'payload',
  'app_id',
  'params',
  'server_params',
  'client_input_params',
  'headers',
  'cookies',
  'login_response',
  'logged_in_user',
  'two_factor_info',
  'two_factor_required',
  'two_factor_identifier',
  'totp_two_factor_on',
  'sms_two_factor_on',
  'context_data',
  'two_step_verification_context',
  'challenge',
  'challenge_context',
  'checkpoint_url',
  'error_type',
  'message',
  'errors',
  'next_screen',
  'screen',
  'screen_id',
};

String _diagnosticOperator(String operator) =>
    const {
      'f4i',
      'dkc',
      'GetArg',
      'Get',
      'map',
      'list',
      'call',
    }.contains(operator)
    ? operator
    : 'outro';

Object _diagnosticShape(Object? value, [int depth = 0, List<int>? budget]) {
  budget ??= [300];
  if (depth >= 6 || --budget[0] < 0) return {'tipo': 'estrutura_limitada'};
  String name(Object? key) =>
      key is String && _diagnosticFields.contains(key) ? key : 'outro';
  if (value is Expr) {
    if (value.args.length == 2) {
      final keys = value.args[0], values = value.args[1];
      if (keys is Expr &&
          values is Expr &&
          keys.operator == values.operator &&
          keys.args.length == values.args.length &&
          keys.args.every((key) => key is String)) {
        return {
          'tipo': 'mapa_literal',
          'operador': _diagnosticOperator(value.operator),
          'campos': [
            for (var i = 0; i < keys.args.length && i < 24; i++)
              {
                'nome': name(keys.args[i]),
                'estrutura': _diagnosticShape(
                  values.args[i],
                  depth + 1,
                  budget,
                ),
              },
          ],
        };
      }
    }
    return {
      'tipo': 'expressao',
      'operador': _diagnosticOperator(value.operator),
      'argumentos': value.args
          .take(12)
          .map((v) => _diagnosticShape(v, depth + 1, budget))
          .toList(),
    };
  }
  if (value is Map)
    return {
      'tipo': 'objeto',
      'campos': [
        for (final row in value.entries.take(12))
          {
            'nome': name(row.key),
            'estrutura': _diagnosticShape(row.value, depth + 1, budget),
          },
      ],
    };
  if (value is List)
    return {
      'tipo': 'lista',
      'itens': value
          .take(6)
          .map((v) => _diagnosticShape(v, depth + 1, budget))
          .toList(),
    };
  return {
    'tipo': value is String
        ? 'texto'
        : value is Atom
        ? 'atomo'
        : value is bool
        ? 'booleano'
        : value == null
        ? 'nulo'
        : 'numero',
  };
}

Map<String, bool> bloksParserCapabilities() {
  const app = 'com.bloks.www.two_step_verification.entrypoint';
  bool probe(String map) {
    try {
      return extractContext(
            {'action': '(call "$app" $map)'},
            app,
            'two_step_verification_context',
          ) ==
          'probe';
    } on FormatException {
      return false;
    }
  }

  return {
    'f4i_apos_argumentos': probe(
      '(dkc 0) (f4i '
      '(dkc "two_step_verification_context") (dkc "probe"))',
    ),
    'f4i_com_app_no_mapa': probe(
      '(f4i '
      '(dkc "next_screen" "two_step_verification_context") '
      '(dkc "com.bloks.www.other.screen" "probe"))',
    ),
  };
}

Map<String, dynamic> bloksContinuationDiagnostic(Object? data) {
  final texts = <String>[];
  final fields = <String>{};
  var characters = 0;
  for (final value in responseValues(data)) {
    if (value is Map)
      fields.addAll(
        value.keys.whereType<String>().where(_diagnosticFields.contains),
      );
    if (value is String &&
        value.length <= 524288 &&
        texts.length < 128 &&
        characters + value.length <= 1048576) {
      texts.add(value);
      characters += value.length;
    }
  }
  // Only literal, public Bloks app identifiers are exported. Arbitrary field
  // names, identifiers, URLs, error messages and expression values are hidden.
  final appLiteral = RegExp(r'"(com\.bloks\.www\.[a-z_][a-z_.]{0,160})"');
  final appValue = RegExp(r'^com\.bloks\.www\.[a-z_][a-z_.]{0,160}$');
  final apps = <String>{};
  for (final text in texts) {
    if (apps.length < 24 && appValue.hasMatch(text)) apps.add(text);
    for (final match in appLiteral.allMatches(text)) {
      if (apps.length >= 24) break;
      apps.add(match.group(1)!);
    }
  }
  final sortedApps = apps.toList()..sort();
  final maps = <Map<String, dynamic>>[];
  for (final app in sortedApps.take(8)) {
    for (final text in texts) {
      final anchor = jsonEncode(app);
      final found = text.indexOf(anchor);
      if (found < 0) continue;
      final start = found + anchor.length;
      final section = text.substring(
        start,
        text.length - start > 131072 ? start + 131072 : text.length,
      );
      final entry = <String, dynamic>{'app': app};
      for (final probe in {
        'primeira_expressao': section.indexOf('('),
        'mapa_f4i': section.indexOf('(f4i'),
      }.entries) {
        if (probe.value < 0) {
          entry[probe.key] = {'tipo': 'ausente'};
          continue;
        }
        try {
          entry[probe.key] = _diagnosticShape(
            LiteralParser(section, probe.value).read(),
          );
        } on FormatException {
          entry[probe.key] = {'tipo': 'expressao_nao_lida'};
        }
      }
      maps.add(entry);
      break;
    }
  }
  return {
    'campos_reconhecidos': fields.toList()..sort(),
    'apps_referenciados': sortedApps,
    'marcadores': {
      for (final key in [
        'context_data',
        'two_step_verification_context',
        'two_factor_info',
        'login_response',
        'challenge',
        'checkpoint_url',
      ])
        key: fields.contains(key) || texts.any((text) => text.contains(key)),
    },
    'mapas_de_parametros': maps,
    'formato_resposta': _diagnosticShape(data),
  };
}

// JSON strings embedded in Bloks actions can contain login_response.
Iterable<Object?> embeddedValues(Object? data) sync* {
  yield* responseValues(data);
  for (final text in responseValues(data).whereType<String>()) {
    if (!text.contains('login_response') || text.length > 524288) continue;
    for (var i = 0; i < text.length; i++) {
      if (text[i] != '"') continue;
      try {
        final parser = LiteralParser(text, i);
        final v = parser.read();
        i = parser.offset - 1;
        if (v is String && v.contains('login_response'))
          yield* responseValues(v);
      } on FormatException {
        /* not JSON */
      }
    }
  }
}
