import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../lib/http2_transport.dart';
import '../lib/wire_reply.dart';

// One local process per worker, talking only over stdin/stdout. No listener,
// credential files, request logging, redirects, downgrade or automatic retry.
Future<void> main() async {
  Http2Transport? transport;
  String? caFile;
  int? timeoutMs;
  Uri? currentUri;
  try {
    await for (final line
        in stdin.transform(utf8.decoder).transform(const LineSplitter())) {
      try {
        if (line.length > 8 * 1024 * 1024) throw const FormatException();
        final request = jsonDecode(line) as Map<String, dynamic>;
        final uri = Uri.parse(request['url'] as String);
        if (uri.scheme != 'https' ||
            uri.userInfo.isNotEmpty ||
            uri.hasFragment) {
          throw const FormatException();
        }
        currentUri = uri;
        final nextCa = request['ca_file'] as String?;
        final nextTimeout = request['timeout_ms'] as int;
        if (nextTimeout < 1 || nextTimeout > 120000)
          throw const FormatException();
        if (transport == null || caFile != nextCa || timeoutMs != nextTimeout) {
          transport?.close();
          final context = SecurityContext(withTrustedRoots: true);
          if (nextCa != null) context.setTrustedCertificates(nextCa);
          transport = Http2Transport(
            externalCookies: true,
            securityContext: context,
            endpoint: (_, _) => currentUri!,
            connectTimeout: Duration(milliseconds: nextTimeout),
            requestTimeout: Duration(milliseconds: nextTimeout),
          );
          caFile = nextCa;
          timeoutMs = nextTimeout;
        }
        // The endpoint callback must use the current URL, including its port.
        // Production callers use both Instagram hosts in a single connection pool.
        final body = Map<String, String>.from(request['body'] as Map);
        final headers = Map<String, String>.from(request['headers'] as Map);
        final expectedMethod = body.isEmpty ? 'GET' : 'POST';
        if (request['method'] != expectedMethod) throw const FormatException();
        final reply = await transport.send(
          uri.authority,
          '${uri.path}${uri.hasQuery ? '?${uri.query}' : ''}',
          body,
          headers,
        );
        stdout.writeln(
          jsonEncode({
            'ok': true,
            'status': reply.status,
            'headers': reply.headers,
            'set_cookies': reply.setCookies,
            'body': base64Encode(reply.rawBody),
            'transport': reply.transportInfo,
          }),
        );
      } catch (error) {
        final kind = switch (error) {
          TlsException() => 'tls',
          TimeoutException() => 'timeout',
          SocketException() => 'connection',
          TransportFailure() => 'http2',
          FormatException() => 'format',
          _ => 'transport',
        };
        // Never serialize exception strings: they can contain URLs or bodies.
        stdout.writeln(jsonEncode({'ok': false, 'error': kind}));
      }
      await stdout.flush();
    }
  } finally {
    transport?.close();
  }
}
