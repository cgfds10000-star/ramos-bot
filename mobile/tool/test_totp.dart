import 'dart:convert';
import 'dart:io';

import '../lib/auth/totp.dart';
import '../lib/auth/login_client.dart';
import 'test_login.dart'
    show FakeCrypto, ScriptedServer, preflight, ok, embeddedSession, check;

Future<void> main() async {
  const secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'; // Public RFC test key.
  for (final vector in <(int, String)>[
    (59, '287082'),
    (1111111109, '081804'),
    (1111111111, '050471'),
    (1234567890, '005924'),
    (2000000000, '279037'),
    (20000000000, '353130'),
  ]) {
    check(
      generateTotp(
            secret,
            at: DateTime.fromMillisecondsSinceEpoch(
              vector.$1 * 1000,
              isUtc: true,
            ),
          ) ==
          vector.$2,
      'RFC vector mismatch',
    );
  }
  final grouped = RegExp(r'.{1,4}')
      .allMatches(secret)
      .map((m) => m.group(0))
      .join(' ')
      .toLowerCase();
  check(
    parseVerificationEntry(grouped).secret == secret,
    'grouped key rejected',
  );
  check(
    parseVerificationEntry('0 0 3 7 5 4').code == '003754',
    'leading zeros lost',
  );
  check(
    parseVerificationEntry('0012-3456').code == '00123456',
    'backup rejected',
  );
  for (final invalid in ['', '1234567', 'GEZD0NBVGY3TQOJQ', 'invalid!']) {
    var rejected = false;
    try {
      parseVerificationEntry(invalid);
    } on FormatException {
      rejected = true;
    }
    check(rejected, 'invalid input accepted');
  }
  var rejectsSmsKey = false;
  try {
    parseVerificationEntry(grouped, allowSecret: false);
  } on FormatException {
    rejectsSmsKey = true;
  }
  check(rejectsSmsKey, 'TOTP key used for SMS');
  final templates = jsonDecode(
    File('assets/login_payloads.json').readAsStringSync(),
  ) as Map<String, dynamic>;
  var clock = 59;
  final script = preflight({'two_step_verification_context': 'ctx'});
  for (final step in ['entry', 'picker', 'select']) {
    script.add((path, body) {
      if (step == 'select') clock = 1111111111;
      return ok({'status': 'ok'});
    });
  }
  script.add((path, body) {
    check(
      jsonDecode(body['params']!)['client_input_params']['code'] == '050471',
      'TOTP was generated before preparation completed',
    );
    return ok(embeddedSession());
  });
  final server = ScriptedServer(script);
  final client = LoginClient(
    crypto: FakeCrypto(),
    send: server.send,
    templates: templates,
    pause: (_) async {},
    totpNow: () =>
        DateTime.fromMillisecondsSinceEpoch(clock * 1000, isUtc: true),
  );
  await client.login('test', 'sensitive-password');
  check(client.stage == LoginStage.code, 'login did not ask 2FA');
  await client.submitCode(grouped);
  check(
    client.stage == LoginStage.success,
    'key did not complete login: ${client.message}',
  );
  final diagnostic = jsonEncode(client.diagnostic());
  check(
    !diagnostic.contains(secret) &&
        !diagnostic.contains(grouped) &&
        !diagnostic.contains('050471'),
    'TOTP secret/code exported',
  );
  stdout.writeln(
    'TOTP passed: 6 RFC vectors, grouped keys, numeric/backup codes, invalid input, SMS distinction and fresh generation at submit.',
  );
}
