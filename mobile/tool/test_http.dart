// Real local HTTP regression for the reported non-JSON 429 and request framing.
import 'dart:convert';
import 'dart:io';

import '../lib/auth/login_client.dart';
import 'test_login.dart' show FakeCrypto, check;

Future<void> main() async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final paths = <String>[];
  var homes = 0, registrations = 0, logins = 0;
  final subscription = server.listen((request) async {
    paths.add(request.uri.path);
    check(request.headers.contentLength > 0, 'form Content-Length missing');
    check(
      request.headers.value(HttpHeaders.transferEncodingHeader) == null,
      'chunked form differs from Python requests',
    );
    final raw = await utf8.decoder.bind(request).join();
    final body = Uri.splitQueryString(raw);
    check(
      request.headers.contentType?.mimeType ==
          'application/x-www-form-urlencoded',
      'wrong content type',
    );
    Object data = {'status': 'ok'};
    final path = request.uri.path;
    if (path == '/graphql_www') {
      registrations++;
      data = {
        'data': {
          'usdid_registration': {'success': true},
        },
      };
    } else if (path.contains('process_client_data')) {
      homes++;
      if (homes > 1)
        check(
          request.cookies.any(
            (cookie) => cookie.name == 'mid' && cookie.value == 'server-mid',
          ),
          'cookies lost on reset',
        );
      request.response.cookies.add(Cookie('mid', 'server-mid')..path = '/');
      data = {
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
      };
    } else if (path.contains('attestation')) {
      data = {'challenge_nonce': 'nonce'};
    } else if (path.contains('launcher')) {
      request.response.headers.set('ig-set-password-encryption-pub-key', 'key');
      request.response.headers.set('ig-set-password-encryption-key-id', '1');
    } else if (path.contains('send_login_request')) {
      logins++;
      final params = jsonDecode(body['params']!);
      check(
        params['client_input_params']['machine_id'] == 'server-mid',
        'MID not continued from cookie',
      );
      request.response.statusCode = 429;
      request.response.headers.set('Retry-After', '120');
      request.response.headers.contentType = ContentType.html;
      request.response.write('<html>Too many requests</html>');
      await request.response.close();
      return;
    }
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(data));
    await request.response.close();
  });
  final transport = IoTransport(
    endpoint: (host, path) => Uri.parse('http://127.0.0.1:${server.port}$path'),
  );
  try {
    final templates = jsonDecode(
      File('assets/login_payloads.json').readAsStringSync(),
    ) as Map<String, dynamic>;
    final client = LoginClient(
      crypto: FakeCrypto(),
      send: transport.send,
      templates: templates,
      pause: (_) async {},
    );
    await client.login('test', 'password');
    check(
      client.stage == LoginStage.failed &&
          client.message.contains('HTTP 429') &&
          client.message.contains('envio do login CAA'),
      '429 lost: ${client.message}',
    );
    check(
      client.trace.last.contains('HTTP 429') &&
          client.trace.last.contains('sem JSON'),
      '429 step not recorded',
    );
    check(client.retrySeconds > 0, 'Retry-After ignored');
    final calls = paths.length;
    client.reset();
    await client.login('test', 'password');
    check(paths.length == calls, 'request sent during Retry-After');
    client.retryAt = DateTime.now().subtract(const Duration(seconds: 1));
    await client.login('test', 'password');
    check(
      registrations == 1 && homes == 2 && logins == 2,
      'device re-registered or credential request retried automatically',
    );
    check(!client.trace.join().contains('password'), 'password logged');
    stdout.writeln(
      'Local HTTP passed: HTML 429, Retry-After, exact step, Content-Length, MID/cookies and registration reuse.',
    );
  } finally {
    transport.close();
    await subscription.cancel();
    await server.close(force: true);
  }
}
