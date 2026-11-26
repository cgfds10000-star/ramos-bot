// Run from project root: dart run tool/test_login.dart
// Uses scripted server replies, never sends credentials to a real service.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../lib/auth/bloks_parser.dart';
import '../lib/auth/login_client.dart';

void check(bool result, String message) {
  if (!result) throw StateError(message);
}

class FakeCrypto implements LoginCrypto {
  @override
  Future<Map<String, String>> identity() async => {
    'uuid': 'uuid',
    'phone': 'phone',
    'android': 'android-test',
    'registration': 'reg',
    'usdidHeader': 'signed',
  };
  @override
  Future<String> encrypt(
    String password,
    String publicKey,
    String keyId,
  ) async => '#PWD_INSTAGRAM:4:0:encrypted';
}

Map<String, dynamic> embeddedSession() => {
  'layout': {
    'bloks_payload': {
      'action':
          '(action ${jsonEncode(jsonEncode({
            'login_response': jsonEncode({
              'status': 'ok',
              'logged_in_user': {'pk': '123'},
            }),
            'headers': jsonEncode({
              'IG-Set-Authorization': 'Bearer IGT:2:${base64Encode(utf8.encode(jsonEncode({'ds_user_id': '123', 'sessionid': 'test-session'})))}',
            }),
            'cookies': '',
          }))})',
    },
  },
};
WireReply ok(Map<String, dynamic> data) => WireReply(200, data);

class ScriptedServer {
  final List<WireReply Function(String, Map<String, String>)> replies;
  final paths = <String>[];
  final bodies = <Map<String, String>>[];
  ScriptedServer(this.replies);
  Future<WireReply> send(
    String host,
    String path,
    Map<String, String> body,
    Map<String, String> headers,
  ) async {
    paths.add(path);
    bodies.add(body);
    if (replies.isEmpty) throw StateError('unexpected request');
    return replies.removeAt(0)(path, body);
  }
}

List<WireReply Function(String, Map<String, String>)> preflight(
  Map<String, dynamic> login,
) => [
  (p, b) => ok({
    'data': {
      'usdid_registration': {'success': true},
    },
  }),
  (p, b) => ok({
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
  }),
  (p, b) => ok({'challenge_nonce': 'nonce'}),
  (p, b) => ok({'status': 'ok'}),
  (p, b) => WireReply(
    200,
    {'status': 'ok'},
    {
      'ig-set-password-encryption-pub-key': 'key',
      'ig-set-password-encryption-key-id': '1',
    },
  ),
  (p, b) {
    final params = jsonDecode(b['params']!);
    check(
      params['client_input_params']['password'] ==
          '#PWD_INSTAGRAM:4:0:encrypted',
      'plaintext password sent',
    );
    check(params['client_input_params']['aac'] == 'server-aac', 'aac lost');
    return ok(login);
  },
];
Future<void> main() async {
  final templates = jsonDecode(
    File('assets/login_payloads.json').readAsStringSync(),
  ) as Map<String, dynamic>;
  var passed = 0;
  // Literal aliases, nested maps, ambiguous values and dynamic expressions.
  final app = totpApps.first;
  final literal =
      '(call ${jsonEncode(app)} (map (list "server_params") (list (map (list "two_step_verification_context") (list "ctx")))))';
  check(
    extractContext({'action': literal}, app, 'two_step_verification_context') ==
        'ctx',
    'literal extraction failed',
  );
  check(
    extractContext(
      {
        'action':
            '(call ${jsonEncode(app)} (map (list "two_step_verification_context") (list (GetArg "ctx"))))',
      },
      app,
      'two_step_verification_context',
    ).isEmpty,
    'dynamic expression accepted',
  );
  bool ambiguous = false;
  try {
    extractContext(
      [
        {
          'app_id': app,
          'params': {'two_step_verification_context': 'a'},
        },
        {
          'app_id': app,
          'params': {'two_step_verification_context': 'b'},
        },
      ],
      app,
      'two_step_verification_context',
    );
  } on FormatException {
    ambiguous = true;
  }
  check(ambiguous, 'ambiguous context accepted');
  passed++;
  // Full login -> updated 2FA context -> one manual code -> embedded session.
  final replies = preflight({'two_step_verification_context': 'ctx0'});
  for (var n = 1; n <= 4; n++) {
    final i = n;
    replies.add((p, b) {
      final params = jsonDecode(b['params']!);
      check(
        params['server_params']['two_step_verification_context'] ==
            'ctx${i - 1}',
        'stale 2FA context',
      );
      return ok({'two_step_verification_context': 'ctx$i'});
    });
  }
  replies.add((p, b) {
    final params = jsonDecode(b['params']!);
    check(params['client_input_params']['code'] == '123456', 'wrong code');
    check(
      params['server_params']['two_step_verification_context'] == 'ctx4',
      'latest context missing',
    );
    return ok(embeddedSession());
  });
  final server = ScriptedServer(replies);
  final c = LoginClient(
    authenticatorOnly: true,
    crypto: FakeCrypto(),
    send: server.send,
    templates: templates,
  );
  await c.login('test', 'sensitive-password');
  check(c.stage == LoginStage.code, 'not waiting for 2FA: ${c.message}');
  final count = server.paths.length;
  await c.submitCode('123');
  check(
    c.stage == LoginStage.code && server.paths.length == count,
    'invalid code was sent',
  );
  await Future.wait([c.submitCode('123456'), c.submitCode('123456')]);
  check(
    c.stage == LoginStage.success && c.userId == '123',
    'session not accepted: ${c.message}',
  );
  check(
    server.paths.where((p) => p.contains('send_login_request')).length == 1,
    'password resent',
  );
  check(
    server.paths.where((p) => p.contains('verify_code')).length == 1,
    'code submitted twice',
  );
  check(
    !c.trace.join().contains('123456') &&
        !c.trace.join().contains('sensitive-password'),
    'secrets logged',
  );
  passed++;
  // Native TOTP method 3, no new credential request.
  final native = ScriptedServer([
    ...preflight({
      'two_factor_info': {
        'totp_two_factor_on': true,
        'two_factor_identifier': 'id',
      },
    }),
    (p, b) {
      final payload = jsonDecode(
        b['signed_body']!.substring('SIGNATURE.'.length),
      );
      check(
        payload['verification_method'] == '3' &&
            payload['verification_code'] == '654321',
        'wrong native payload',
      );
      return WireReply(
        200,
        {
          'status': 'ok',
          'logged_in_user': {'pk': '123'},
        },
        {
          'ig-set-authorization':
              'Bearer IGT:2:${base64Encode(utf8.encode(jsonEncode({'ds_user_id': '123', 'sessionid': 'session'})))}',
        },
      );
    },
  ]);
  final n = LoginClient(
    crypto: FakeCrypto(),
    send: native.send,
    templates: templates,
  );
  await n.login('test', 'pass');
  await n.submitCode('654321');
  check(n.stage == LoginStage.success, 'native 2FA failed: ${n.message}');
  passed++;
  // A 200 success-shaped UI without a valid session must fail.
  final falseSuccess = ScriptedServer(
    preflight({
      'status': 'ok',
      'logged_in_user': {'pk': '123'},
    }),
  );
  final f = LoginClient(
    crypto: FakeCrypto(),
    send: falseSuccess.send,
    templates: templates,
  );
  await f.login('test', 'pass');
  check(f.stage == LoginStage.failed, 'false positive login');
  passed++;
  // Timeout at submit: never retry code or credentials.
  final timeoutReplies = preflight({
    'two_factor_info': {
      'totp_two_factor_on': true,
      'two_factor_identifier': 'id',
    },
  });
  timeoutReplies.add((p, b) => throw TimeoutException('test'));
  final timeout = ScriptedServer(timeoutReplies);
  final t = LoginClient(
    crypto: FakeCrypto(),
    send: timeout.send,
    templates: templates,
  );
  await t.login('test', 'pass');
  await t.submitCode('123456');
  await t.submitCode('123456');
  check(
    t.stage == LoginStage.failed && timeout.paths.length == 7,
    'timeout retried',
  );
  passed++;
  // Manual approval -> profile screen -> numeric code, preserving AP context.
  Map<String, dynamic> ap(String name, String ctx) => {
    'app_id': name,
    'params': {
      'server_params': {'context_data': ctx},
    },
  };
  final apReplies = preflight(ap(apEntry, 'ap1'));
  apReplies.add(
    (p, b) => ok({'action': 'com.bloks.www.ap.two_step_verification.approve'}),
  );
  apReplies.add((p, b) => ok(ap(apScreen, 'ap2')));
  apReplies.add((p, b) => ok(ap(apVerify, 'ap3')));
  apReplies.add((p, b) {
    check(
      jsonDecode(b['params']!)['server_params']['context_data'] == 'ap3',
      'AP submit context lost',
    );
    return ok(embeddedSession());
  });
  final apServer = ScriptedServer(apReplies);
  final a = LoginClient(
    crypto: FakeCrypto(),
    send: apServer.send,
    templates: templates,
  );
  await a.login('test', 'pass');
  check(a.stage == LoginStage.approval, 'approval not detected');
  await a.checkApproval();
  check(a.stage == LoginStage.code, 'AP code not shown');
  await a.submitCode('123456');
  check(a.stage == LoginStage.success, 'AP login failed');
  passed++;
  stdout.writeln(
    '$passed test groups passed (simulated responses; no live login).',
  );
}
