// Based on the working Flutter login ZIP, revision flutter-caa-http2-5.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http2/transport.dart' as h2;

import 'wire_reply.dart';

class _Connection {
  final SecureSocket socket;
  final h2.ClientTransportConnection http;
  _Connection(this.socket, this.http);
}

// Direct HTTPS with negotiated h2 and TLS >=1.3. A submitted request is never
// replayed, redirected, or downgraded to HTTP/1.1 by this transport.
class Http2Transport extends LoginCookieJar {
  final SecurityContext _context;
  final bool externalCookies;
  final Duration connectTimeout;
  final Duration requestTimeout;
  final Uri Function(String, String)? endpoint;
  final Map<String, _Connection> _connections = {};
  bool _closed = false;
  Http2Transport({
    SecurityContext? securityContext,
    this.externalCookies = false,
    this.endpoint,
    this.connectTimeout = const Duration(seconds: 30),
    this.requestTimeout = const Duration(seconds: 45),
  }) : _context = securityContext ?? SecurityContext(withTrustedRoots: true) {
    _context.minimumTlsProtocolVersion = TlsProtocolVersion.tls1_3;
  }
  void close() {
    _closed = true;
    for (final connection in _connections.values) {
      unawaited(connection.http.terminate().catchError((Object _) {}));
      connection.socket.destroy();
    }
    _connections.clear();
  }

  Future<_Connection> _connection(Uri uri) async {
    if (_closed) throw TransportFailure('A tentativa foi encerrada.');
    final key = '${uri.host}:${uri.port}';
    final cached = _connections[key];
    if (cached != null && cached.http.isOpen) return cached;
    if (cached != null) cached.socket.destroy();
    final socket = await SecureSocket.connect(
      uri.host,
      uri.port,
      context: _context,
      supportedProtocols: const ['h2'],
      timeout: connectTimeout,
    );
    if (_closed || socket.selectedProtocol != 'h2') {
      socket.destroy();
      throw TransportFailure(
        'O servidor nao negociou HTTP/2. Nenhuma credencial foi enviada.',
      );
    }
    final http = h2.ClientTransportConnection.viaSocket(socket);
    try {
      await http.onInitialPeerSettingsReceived.timeout(connectTimeout);
    } catch (_) {
      socket.destroy();
      throw TransportFailure(
        'A conexao HTTP/2 nao foi confirmada. Nenhuma credencial foi enviada.',
      );
    }
    if (_closed) {
      socket.destroy();
      throw TransportFailure('A tentativa foi encerrada.');
    }
    final value = _Connection(socket, http);
    _connections[key] = value;
    return value;
  }

  Future<WireReply> send(
    String host,
    String path,
    Map<String, String> body,
    Map<String, String> headers,
  ) async {
    final route = Uri.parse(path);
    final uri =
        endpoint?.call(host, path) ??
        Uri.https(
          host,
          route.path,
          route.hasQuery ? route.queryParameters : null,
        );
    if (uri.scheme != 'https') throw TransportFailure('O login exige HTTPS.');
    final connection = await _connection(uri);
    final form = utf8.encode(
      body.entries
          .map(
            (entry) =>
                '${Uri.encodeQueryComponent(entry.key)}=${Uri.encodeQueryComponent(entry.value)}',
          )
          .join('&'),
    );
    final outgoing = <h2.Header>[
      h2.Header.ascii(':method', body.isEmpty ? 'GET' : 'POST'),
      h2.Header.ascii(':authority', uri.authority),
      h2.Header.ascii(':scheme', 'https'),
      h2.Header.ascii(
        ':path',
        '${uri.path.isEmpty ? '/' : uri.path}${uri.hasQuery ? '?${uri.query}' : ''}',
      ),
    ];
    const forbidden = {
      'connection',
      'keep-alive',
      'proxy-connection',
      'transfer-encoding',
      'upgrade',
      'host',
      'content-length',
      'content-type',
      'cookie',
    };
    for (final entry in headers.entries) {
      final name = entry.key.toLowerCase();
      if (forbidden.contains(name) || name.startsWith(':')) continue;
      outgoing.add(
        h2.Header(
          ascii.encode(name),
          utf8.encode(entry.value),
          neverIndexed: name == 'authorization',
        ),
      );
    }
    if (body.isNotEmpty) {
      outgoing.add(
        h2.Header.ascii(
          'content-type',
          'application/x-www-form-urlencoded; charset=UTF-8',
        ),
      );
      outgoing.add(h2.Header.ascii('content-length', form.length.toString()));
    }
    // The web backend owns its requests cookie jar, including restored sessions.
    // Forward only the Cookie header already scoped to this URL by requests.
    final cookies = externalCookies
        ? headers.entries
              .where((e) => e.key.toLowerCase() == 'cookie')
              .map((e) => e.value)
              .join('; ')
        : cookiesFor(host, uri).map((c) => '${c.name}=${c.value}').join('; ');
    if (cookies.isNotEmpty)
      outgoing.add(
        h2.Header(
          ascii.encode('cookie'),
          utf8.encode(cookies),
          neverIndexed: true,
        ),
      );
    final stream = connection.http.makeRequest(
      outgoing,
      endStream: body.isEmpty,
    );
    if (body.isNotEmpty) stream.sendData(form, endStream: true);
    Future<WireReply> receive() async {
      var status = 0;
      final fields = <String, String>{};
      final setCookies = <String>[];
      final bytes = <int>[];
      await for (final message in stream.incomingMessages) {
        if (message is h2.HeadersStreamMessage) {
          final block = <String, String>{};
          for (final header in message.headers) {
            final name = ascii.decode(header.name).toLowerCase();
            final value = utf8.decode(header.value, allowMalformed: true);
            if (name == 'set-cookie') setCookies.add(value);
            block[name] = block.containsKey(name)
                ? '${block[name]}, $value'
                : value;
          }
          final current = int.tryParse(block[':status'] ?? '');
          if (current != null && current < 200) continue;
          if (current != null) status = current;
          block.remove(':status');
          fields.addAll(block);
        } else if (message is h2.DataStreamMessage) {
          bytes.addAll(message.bytes);
          if (bytes.length > 4 * 1024 * 1024)
            throw TransportFailure('A resposta excedeu o limite do teste.');
        }
      }
      if (status < 200 || status > 599)
        throw TransportFailure(
          'Resposta HTTP/2 sem status valido. A etapa nao foi repetida.',
        );
      if (!externalCookies) acceptCookies(host, uri, setCookies);
      if (setCookies.isNotEmpty) fields['set-cookie'] = setCookies.join(', ');
      Stream<List<int>> decoded = Stream.value(bytes);
      final encoding = fields['content-encoding']?.toLowerCase();
      if (encoding == 'gzip') decoded = gzip.decoder.bind(decoded);
      if (encoding == 'deflate') decoded = zlib.decoder.bind(decoded);
      final content = <int>[];
      await for (final chunk in decoded) {
        content.addAll(chunk);
        if (content.length > 4 * 1024 * 1024)
          throw TransportFailure('A resposta descompactada excedeu o limite.');
      }
      return decodeReply(status, content, fields, {
        'http_version': '2',
        'alpn': connection.socket.selectedProtocol ?? '',
        'tls_minima': '1.3',
        'content_type': (fields['content-type'] ?? '').split(';').first,
        'server': {'proxygen-bolt', 'cloudflare'}.contains(fields['server'])
            ? fields['server']!
            : 'outro',
      }, setCookies);
    }

    try {
      return await receive().timeout(requestTimeout);
    } on TimeoutException {
      stream.terminate();
      rethrow;
    } catch (_) {
      stream.terminate();
      rethrow;
    }
  }
}
