// Offline oracle: fixtures were captured by running the supplied Python wrapper
// and instagrapi==3.2.19 against synthetic responses. No real account is used.
import 'dart:convert';
import 'dart:io';

import '../lib/auth/login_client.dart';
import 'test_login.dart' show FakeCrypto, check, embeddedSession, ok;

Object? canonical(Object? value, [String key = '']) {
  if (['waterfall_id', 'INTERNAL__latency_qpl_instance_id'].contains(key))
    return '<runtime>';
  if (['password_text_input_id', 'username_text_input_id'].contains(key))
    return (value as String).split(':').last;
  if (value is String &&
      ['params', 'bk_client_context', 'signed_body'].contains(key)) {
    final text = value.startsWith('SIGNATURE.') ? value.substring(10) : value;
    return canonical(jsonDecode(text));
  }
  if (value is Map) {
    final keys = value.keys.map((k) => k.toString()).toList()..sort();
    return {for (final k in keys) k: canonical(value[k], k)};
  }
  if (value is List) return value.map((v) => canonical(v)).toList();
  return value;
}

void equal(Object? actual, Object? expected, String label) {
  final a = jsonEncode(canonical(actual)), e = jsonEncode(canonical(expected));
  check(a == e, '$label\nactual: $a\npython: $e');
}

Future<void> main() async {
  final templates = jsonDecode(
    File('assets/login_payloads.json').readAsStringSync(),
  ) as Map<String, dynamic>;
  for (final branch in ['auto', 'sms', 'backup', 'autenticador']) {
    final oracle = jsonDecode(
      File('tool/fixtures/python_$branch.json').readAsStringSync(),
    ) as List;
    var index = 0;
    Future<WireReply> send(
      String host,
      String path,
      Map<String, String> body,
      Map<String, String> headers,
    ) async {
      check(index < oracle.length, 'extra request: $path');
      final expected = oracle[index++] as Map;
      if (path == '/graphql_www') {
        check(expected['kind'] == 'graphql', 'registration sequence differs');
        final vars = jsonDecode(body['variables']!) as Map;
        (vars['input']['usdid_token'] as Map)['sensitive_string_value'] =
            '<device-registration>';
        equal(vars, expected['variables'], 'registration variables');
        return ok({
          'data': {
            'usdid_registration': {'success': true},
          },
        });
      }
      check(
        host == expected['host'],
        'host differs: $host / ${expected['host']}',
      );
      check(
        path == '/api/v1/${expected['route']}',
        'route differs: $path / ${expected['route']}',
      );
      final data = body.containsKey('signed_body')
          ? jsonDecode(body['signed_body']!.substring(10))
          : body;
      equal(data, expected['data'], '$branch $path payload differs');
      final expectedHeaders = expected['headers'] as Map;
      for (final key in expectedHeaders.keys) {
        if ([
          'Host',
          'X-Pigeon-Session-Id',
          'X-Pigeon-Rawclienttime',
        ].contains(key))
          continue;
        equal(
          headers[key],
          expectedHeaders[key],
          '$branch header $key differs',
        );
      }
      check(
        headers['X-Pigeon-Session-Id']!.startsWith('UFS-'),
        'Pigeon identity absent',
      );
      if (path.contains('process_client_data'))
        return ok({
          'layout': {
            'bloks_payload': {
              'data': [
                {
                  'data': {
                    'key': 'CAA_ACCOUNT_ACCESS_CONTEXT:aac',
                    'initial': 'server-aac',
                  },
                },
              ],
            },
          },
        });
      if (path.contains('attestation'))
        return ok({'challenge_nonce': 'nonce', 'key_nonce': 'key-nonce'});
      if (path.contains('launcher'))
        return WireReply(
          200,
          {'status': 'ok'},
          {
            'ig-set-password-encryption-pub-key': 'key',
            'ig-set-password-encryption-key-id': '1',
          },
        );
      if (path.contains('send_login_request'))
        return ok({
          'two_step_verification_context': 'ctx',
          'two_factor_info': {
            'sms_two_factor_on': branch == 'sms',
            'totp_two_factor_on': branch != 'sms',
          },
        });
      if (path.contains('verify_code')) return ok(embeddedSession());
      return ok({'status': 'ok'});
    }

    final client = LoginClient(
      crypto: FakeCrypto(),
      send: send,
      templates: templates,
      authenticatorOnly: branch == 'autenticador',
      pause: (_) async {},
    );
    await client.login('test', 'sensitive-password');
    check(
      client.stage == LoginStage.code,
      '$branch did not reach 2FA: ${client.message}',
    );
    if (branch != 'autenticador')
      check(index == 6, 'auto called 2FA endpoints before the manual code');
    await client.submitCode(branch == 'backup' ? '12345678' : '123456');
    check(
      client.stage == LoginStage.success,
      '$branch failed: ${client.message}',
    );
    check(
      index == oracle.length,
      '$branch missed Python calls: $index/${oracle.length}',
    );
    stdout.writeln(
      '$branch: all ${oracle.length} Python calls match (route, host, payload, static headers).',
    );
  }
}
