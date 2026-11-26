import 'dart:convert';
import 'dart:io';

import '../lib/auth/login_client.dart';
import 'test_login.dart' show FakeCrypto, check;

Future<void> main() async {
  final templates = jsonDecode(
    File('assets/login_payloads.json').readAsStringSync(),
  ) as Map<String, dynamic>;
  final saved = {
    'owner': 'test',
    'authorization': 'Bearer test-session-header',
    'session': 'test-session',
    'userId': '123',
    'mid': 'machine',
    'claim': '0',
    'rur': '',
    'csrf': 'csrf',
  };
  var calls = 0;
  Map<String, dynamic>? written;
  late LoginClient client;
  client = LoginClient(
    crypto: FakeCrypto(),
    templates: templates,
    pause: (_) async {},
    onCheckpoint: () async {
      written =
          jsonDecode(jsonEncode(client.snapshot())) as Map<String, dynamic>;
    },
    send: (host, path, body, headers) async {
      calls++;
      check(
        path == '/api/v1/accounts/current_user/?edit=true' && body.isEmpty,
        'saved session sent credentials',
      );
      check(
        headers['Authorization'] == 'Bearer test-session-header',
        'authorization lost',
      );
      return WireReply(200, {
        'status': 'ok',
        'user': {'pk': '123'},
      });
    },
  );
  client.restore(saved);
  await client.login('test', 'sensitive-password');
  check(
    client.stage == LoginStage.success && calls == 1,
    'saved session validation failed',
  );
  check(
    written != null &&
        !jsonEncode(written).contains('sensitive-password') &&
        !written!.containsKey('code'),
    'credentials persisted',
  );
  var wrongAccountCalls = 0;
  final wrong = LoginClient(
    crypto: FakeCrypto(),
    templates: templates,
    send: (h, p, b, headers) async {
      wrongAccountCalls++;
      return WireReply(200, {});
    },
  );
  wrong.restore(saved);
  await wrong.login('someone_else', 'pass');
  check(
    wrong.stage == LoginStage.failed && wrongAccountCalls == 0,
    'saved owner mismatch ignored',
  );
  final limited = LoginClient(
    crypto: FakeCrypto(),
    templates: templates,
    send: (h, p, b, headers) async {
      throw StateError('cooldown lost');
    },
  );
  limited.restore({
    ...saved,
    'retryAt': DateTime.now()
        .add(const Duration(seconds: 120))
        .toUtc()
        .toIso8601String(),
  });
  await limited.login('test', 'pass');
  check(limited.retrySeconds > 0, 'cooldown not restored');
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final done = server.first.then((request) async {
    check(
      request.method == 'GET' &&
          request.uri.path == '/api/v1/accounts/current_user/' &&
          request.uri.queryParameters['edit'] == 'true',
      'query encoded as path',
    );
    check(
      request.cookies.any((c) => c.name == 'mid' && c.value == 'machine'),
      'saved cookie not restored',
    );
    request.response.headers.contentType = ContentType.json;
    request.response.write('{"status":"ok"}');
    await request.response.close();
  });
  final transport = IoTransport(
    endpoint: (host, path) => Uri.parse('http://127.0.0.1:${server.port}$path'),
  );
  transport.restoreCookies([
    {
      'name': 'mid',
      'value': 'machine',
      'domain': 'i.instagram.com',
      'path': '/',
      'secure': false,
    },
  ]);
  await transport.send(
    'i.instagram.com',
    '/api/v1/accounts/current_user/?edit=true',
    {},
    {},
  );
  await done;
  transport.close();
  await server.close(force: true);
  stdout.writeln(
    'Saved session passed: user validation, account binding, credential exclusion, durable cooldown, cookies and GET query.',
  );
}
