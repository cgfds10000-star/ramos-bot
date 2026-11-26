import 'dart:async';
import 'dart:convert';
import 'dart:io';

class WireReply {
  final int status;
  final Map<String, dynamic> data;
  final Map<String, String> headers;
  final bool isJsonObject;
  final Map<String, String> transportInfo;
  WireReply(
    this.status,
    this.data, [
    this.headers = const {},
    this.isJsonObject = true,
    this.transportInfo = const {},
  ]);
}

// Preserve HTTP evidence even if Instagram returns an empty or HTML error page.
WireReply decodeReply(
  int status,
  List<int> bytes,
  Map<String, String> headers, [
  Map<String, String> transportInfo = const {},
]) {
  final normalized = headers.map(
    (key, value) => MapEntry(key.toLowerCase(), value),
  );
  final text = utf8.decode(bytes, allowMalformed: true).trimLeft();
  try {
    final data = jsonDecode(
      text.startsWith('for (;;);') ? text.substring(9) : text,
    );
    if (data is Map<String, dynamic>)
      return WireReply(status, data, normalized, true, transportInfo);
  } on FormatException {
    /* HTTP status must be handled before format errors. */
  }
  return WireReply(status, {}, normalized, false, transportInfo);
}

class LoginCookieJar {
  final List<Cookie> _cookies = [];
  void clearCookies() => _cookies.clear();
  List<Map<String, dynamic>> cookieSnapshot() => _cookies
      .map(
        (c) => {
          'name': c.name,
          'value': c.value,
          'domain': c.domain,
          'path': c.path,
          'secure': c.secure,
          'httpOnly': c.httpOnly,
          'expires': c.expires?.toUtc().toIso8601String(),
        },
      )
      .toList();
  void restoreCookies(Object? value) {
    if (value is! List) return;
    for (final row in value.whereType<Map>()) {
      final domain = row['domain'];
      if (domain is! String ||
          !(domain == 'instagram.com' || domain.endsWith('.instagram.com')))
        continue;
      if (row['name'] is! String || row['value'] is! String) continue;
      try {
        final c = Cookie(row['name'] as String, row['value'] as String)
          ..domain = domain
          ..path = (row['path'] as String?) ?? '/'
          ..secure = row['secure'] == true
          ..httpOnly = row['httpOnly'] == true;
        c.expires = DateTime.tryParse(row['expires']?.toString() ?? '');
        if (c.expires == null || c.expires!.isAfter(DateTime.now()))
          _cookies.add(c);
      } on FormatException {
        /* malformed saved cookie */
      }
    }
  }

  void applyLoginCookies(String session, String userId) {
    if (session.isEmpty || userId.isEmpty) return;
    for (final row in {'sessionid': session, 'ds_user_id': userId}.entries) {
      _cookies.removeWhere((c) => c.name == row.key);
      _cookies.add(
        Cookie(row.key, row.value)
          ..domain = '.instagram.com'
          ..path = '/'
          ..secure = true
          ..httpOnly = true,
      );
    }
  }

  List<Cookie> cookiesFor(String host, Uri uri) {
    _cookies.removeWhere(
      (c) =>
          (c.maxAge != null && c.maxAge! <= 0) ||
          (c.expires != null && !c.expires!.isAfter(DateTime.now())),
    );
    return _cookies.where((c) {
      final raw = c.domain ?? host;
      final domain = raw.startsWith('.') ? raw.substring(1) : raw;
      final path = c.path ?? '/';
      final matches =
          uri.path == path ||
          (uri.path.startsWith(path) &&
              (path.endsWith('/') ||
                  uri.path.substring(path.length).startsWith('/')));
      return (host == domain ||
              (raw.startsWith('.') && host.endsWith('.$domain'))) &&
          matches &&
          (!c.secure || uri.scheme == 'https');
    }).toList();
  }

  void acceptCookies(String host, Uri uri, List<String> values) {
    for (final text in values) {
      try {
        final c = Cookie.fromSetCookieValue(text);
        if (c.domain != null) {
          final d = c.domain!.replaceFirst(RegExp(r'^\.'), '').toLowerCase();
          if (host != d && !host.endsWith('.$d')) continue;
          c.domain = '.$d';
        } else {
          c.domain = host;
        }
        c.path ??= uri.path.substring(0, uri.path.lastIndexOf('/') + 1);
        if (c.path!.isEmpty) c.path = '/';
        if (c.maxAge != null)
          c.expires = DateTime.now().add(Duration(seconds: c.maxAge!));
        _cookies.removeWhere(
          (old) =>
              old.name == c.name &&
              old.domain == c.domain &&
              old.path == c.path,
        );
        if ((c.maxAge == null || c.maxAge! > 0) &&
            (c.expires == null || c.expires!.isAfter(DateTime.now())))
          _cookies.add(c);
      } on FormatException {
        /* Invalid cookie is not part of the login session. */
      }
    }
  }
}

class TransportFailure implements Exception {
  final String message;
  TransportFailure(this.message);
  @override
  String toString() => message;
}

class IoTransport extends LoginCookieJar {
  final HttpClient _client;
  // Endpoint injection is used only by the local HTTP regression tests.
  final Uri Function(String, String)? endpoint;
  IoTransport({HttpClient? client, this.endpoint})
    : _client = client ?? HttpClient() {
    _client.connectionTimeout = const Duration(seconds: 30);
  }
  void close() => _client.close(force: true);
  Future<WireReply> send(
    String host,
    String path,
    Map<String, String> body,
    Map<String, String> headers,
  ) async {
    final query = Uri.parse(path);
    final uri =
        endpoint?.call(host, path) ??
        Uri.https(
          host,
          query.path,
          query.hasQuery ? query.queryParameters : null,
        );
    final req =
        await (body.isEmpty ? _client.getUrl(uri) : _client.postUrl(uri))
            .timeout(const Duration(seconds: 30));
    req.followRedirects = false;
    headers.forEach(
      (key, value) => req.headers.set(key, value, preserveHeaderCase: true),
    );
    if (body.isNotEmpty)
      req.headers.set(
        HttpHeaders.contentTypeHeader,
        'application/x-www-form-urlencoded; charset=UTF-8',
      );
    _cookies.removeWhere(
      (c) =>
          (c.maxAge != null && c.maxAge! <= 0) ||
          (c.expires != null && !c.expires!.isAfter(DateTime.now())),
    );
    req.cookies.addAll(
      _cookies.where((c) {
        final cookieDomain = c.domain ?? host;
        final isDomainCookie = cookieDomain.startsWith('.');
        final domain = isDomainCookie
            ? cookieDomain.substring(1)
            : cookieDomain;
        final cookiePath = c.path ?? '/';
        final pathMatches =
            uri.path == cookiePath ||
            (uri.path.startsWith(cookiePath) &&
                (cookiePath.endsWith('/') ||
                    uri.path.substring(cookiePath.length).startsWith('/')));
        return (host == domain ||
                (isDomainCookie && host.endsWith('.$domain'))) &&
            pathMatches &&
            (!c.secure || uri.scheme == 'https');
      }),
    );
    // requests (Python) sends a form with Content-Length, rather than a chunked body.
    final encoded = utf8.encode(
      body.entries
          .map(
            (e) =>
                '${Uri.encodeQueryComponent(e.key)}=${Uri.encodeQueryComponent(e.value)}',
          )
          .join('&'),
    );
    if (body.isNotEmpty) {
      req.contentLength = encoded.length;
      req.add(encoded);
    }
    final response = await req.close().timeout(const Duration(seconds: 45));
    for (final c in response.cookies) {
      c.domain ??= host;
      c.path ??= '/';
      if (c.maxAge != null && c.maxAge! > 0)
        c.expires = DateTime.now().add(Duration(seconds: c.maxAge!));
      _cookies.removeWhere(
        (old) =>
            old.name == c.name && old.domain == c.domain && old.path == c.path,
      );
      _cookies.add(c);
    }
    final outHeaders = <String, String>{};
    response.headers.forEach(
      (k, v) => outHeaders[k.toLowerCase()] = v.join(', '),
    );
    final bytes = <int>[];
    final Stream<List<int>> decoded =
        response.headers.value(HttpHeaders.contentEncodingHeader) == 'deflate'
        ? zlib.decoder.bind(response)
        : response;
    await for (final chunk in decoded.timeout(const Duration(seconds: 45))) {
      bytes.addAll(chunk);
      if (bytes.length > 4 * 1024 * 1024)
        throw const FormatException('HTTP response too large');
    }
    return decodeReply(response.statusCode, bytes, outHeaders);
  }
}
