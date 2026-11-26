// Real TLS/ALPN and HTTP/2 integration against an independent Node.js server.
// Usage: dart run tool/test_http2.dart /path/to/test-cert.pem /path/to/test-key.pem
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../lib/auth/http2_transport.dart';
import '../lib/auth/http_transport.dart';
import '../lib/auth/login_client.dart';
import 'test_login.dart' show FakeCrypto, check;

Future<void> main(List<String> args) async {
  if (args.length != 2)
    throw ArgumentError('Supply a local test TLS certificate and key.');
  final process = await Process.start('node', [
    'tool/h2_test_server.mjs',
    ...args,
  ]);
  final errorOutput = <String>[];
  final errorSub = process.stderr
      .transform(utf8.decoder)
      .listen(errorOutput.add);
  final line = await process.stdout
      .transform(utf8.decoder)
      .transform(const LineSplitter())
      .first
      .timeout(const Duration(seconds: 10));
  final ports = jsonDecode(line) as Map;
  final context = SecurityContext(withTrustedRoots: true)
    ..setTrustedCertificates(args[0]);
  var mode = 'ok';
  final transport = Http2Transport(
    securityContext: context,
    endpoint: (host, path) {
      final uri = Uri.parse('https://localhost:${ports['h2Port']}$path');
      return uri.replace(
        queryParameters: {...uri.queryParameters, 'mode': mode},
      );
    },
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
    await client.login('test', 'sensitive-password');
    check(
      client.stage == LoginStage.code,
      'HTTP/2 login did not reach 2FA: ${client.message}',
    );
    await Future.wait([
      client.submitCode('123456'),
      client.submitCode('123456'),
    ]);
    check(
      client.stage == LoginStage.success,
      'HTTP/2 2FA failed: ${client.message}',
    );
    check(
      client.requestTrace.every(
        (v) =>
            v['http_version'] == '2' &&
            v['alpn'] == 'h2' &&
            v['tls_minima'] == '1.3',
      ),
      'protocol metadata missing',
    );
    mode = 'rate';
    client.reset();
    await client.login('test', 'sensitive-password');
    check(
      client.stage == LoginStage.failed && client.message.contains('HTTP 429'),
      'non-JSON HTTP/2 429 lost',
    );
    check(
      client.requestTrace.last['json'] == false &&
          client.requestTrace.last['http_version'] == '2',
      'HTTP/2 error evidence missing',
    );
    check(client.retrySeconds > 0, 'Retry-After missing');
    await client.login('test', 'sensitive-password');
    final diagnostic = jsonEncode(client.diagnostic());
    for (final secret in [
      'sensitive-password',
      '123456',
      'synthetic-session',
      'Bearer IGT:2:',
    ])
      check(!diagnostic.contains(secret), 'secret in exported diagnostic');
    final redirected = await transport.send(
      'i.instagram.com',
      '/redirect',
      {},
      {},
    );
    check(redirected.status == 302, 'redirect followed');
    var resetRejected = false;
    try {
      await transport.send('i.instagram.com', '/drop', {
        'dummy': 'synthetic',
      }, {});
    } catch (_) {
      resetRejected = true;
    }
    check(resetRejected, 'stream reset was swallowed');
    final wrongProtocol = Http2Transport(
      securityContext: context,
      endpoint: (h, p) =>
          Uri.parse('https://localhost:${ports['http1Port']}$p'),
    );
    var rejectedHttp1 = false;
    try {
      await wrongProtocol.send('i.instagram.com', '/secret', {
        'password': 'synthetic',
      }, {});
    } on TlsException {
      rejectedHttp1 = true;
    } on TransportFailure {
      rejectedHttp1 = true;
    } finally {
      wrongProtocol.close();
    }
    check(rejectedHttp1, 'HTTP/1.1 was accepted');
    final untrusted = Http2Transport(
      endpoint: (h, p) => Uri.parse('https://localhost:${ports['h2Port']}$p'),
    );
    var rejectedCertificate = false;
    try {
      await untrusted.send('i.instagram.com', '/secret', {
        'password': 'synthetic',
      }, {});
    } on TlsException {
      rejectedCertificate = true;
    } finally {
      untrusted.close();
    }
    check(rejectedCertificate, 'untrusted certificate was accepted');
    final metrics = (await transport.send(
      'i.instagram.com',
      '/metrics',
      {},
      {},
    )).data;
    check(
      metrics['serverError'] == '',
      'wire mismatch: ${metrics['serverError']}',
    );
    check(
      metrics['login'] == 2 &&
          metrics['verify'] == 1 &&
          metrics['drop'] == 1 &&
          metrics['redirect'] == 1,
      'request replay: $metrics',
    );
    check(metrics['registration'] == 1, 'device registered twice');
    check(metrics['http1'] == 0, 'credentials sent over HTTP/1.1');
    stdout.writeln(
      'HTTP/2 + TLS 1.3 passed: real ALPN, login -> 2FA -> session, gzip, cookies, one submit, HTML 429, no redirects/replays, certificate validation and no HTTP/1.1 fallback.',
    );
  } finally {
    transport.close();
    process.kill();
    await process.exitCode;
    await errorSub.cancel();
    if (errorOutput.isNotEmpty)
      stderr.writeln('Local TLS test server emitted diagnostic stderr.');
  }
}
