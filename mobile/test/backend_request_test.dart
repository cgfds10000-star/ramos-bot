import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:ramos_bot/data/backend.dart';

class _MemoryVault extends SecureVault {
  @override
  Future<void> put(String key, dynamic value) async {
    data[key] = value;
  }
}

void main() {
  test(
    'native writes are accepted by a server requiring a sized JSON body',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final received = <List<int>>[];
      final headers = <HttpHeaders>[];
      server.listen((request) async {
        final bytes = await request.fold<List<int>>(
          [],
          (all, part) => all..addAll(part),
        );
        received.add(bytes);
        headers.add(request.headers);
        request.response.headers.contentType = ContentType.json;
        if (request.contentLength <= 0 ||
            request.contentLength != bytes.length) {
          request.response.statusCode = 400;
          request.response.write(jsonEncode({'error': 'Requisição inválida.'}));
        } else {
          request.response.write(jsonEncode({'ok': true}));
        }
        await request.response.close();
      });
      final api = BackendApi(_MemoryVault())
        ..base = 'http://127.0.0.1:${server.port}'
        ..cookie = 'ramos_access=synthetic-session'
        ..csrf = 'synthetic-csrf';
      try {
        final result = await api.post('/sessions/example/import', {
          'username': 'ação',
        });
        expect(result['ok'], true);
        expect(jsonDecode(utf8.decode(received.first)), {'username': 'ação'});
        expect(headers.first.contentLength, received.first.length);
        expect(headers.first.value(HttpHeaders.transferEncodingHeader), isNull);
        expect(
          headers.first.value(HttpHeaders.cookieHeader),
          'ramos_access=synthetic-session',
        );
        expect(headers.first.value('X-Ramos-CSRF'), 'synthetic-csrf');
        await api.delete('/accounts/example');
        expect(utf8.decode(received.last), '{}');
        expect(headers.last.contentLength, 2);
      } finally {
        api.close();
        await server.close(force: true);
      }
    },
  );
}
