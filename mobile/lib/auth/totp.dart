import 'dart:typed_data';

import 'package:crypto/crypto.dart';

Uint8List decodeTotpSecret(String input) {
  final text = input.replaceAll(RegExp(r'[\s-]+'), '').toUpperCase();
  final match = RegExp(r'^([A-Z2-7]{16,128})(={0,6})$').firstMatch(text);
  if (match == null) throw const FormatException('Chave Base32 invalida.');
  final body = match.group(1)!;
  const padding = {0: 0, 2: 6, 4: 4, 5: 3, 7: 1};
  final expected = padding[body.length % 8];
  if (expected == null ||
      (match.group(2)!.isNotEmpty && match.group(2)!.length != expected))
    throw const FormatException('Chave Base32 incompleta.');
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  final bytes = <int>[];
  var buffer = 0, bits = 0;
  for (final character in body.split('')) {
    buffer = (buffer << 5) | alphabet.indexOf(character);
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      bytes.add((buffer >> bits) & 255);
      buffer &= (1 << bits) - 1;
    }
  }
  if (bits > 0 && buffer != 0)
    throw const FormatException('Chave Base32 incompleta.');
  return Uint8List.fromList(bytes);
}

String generateTotp(String secret, {DateTime? at}) {
  final key = decodeTotpSecret(secret);
  try {
    final seconds = (at ?? DateTime.now()).millisecondsSinceEpoch ~/ 1000;
    if (seconds < 0) throw const FormatException('Relogio invalido.');
    final counter = ByteData(8)..setUint64(0, seconds ~/ 30, Endian.big);
    final hash = Hmac(sha1, key).convert(counter.buffer.asUint8List()).bytes;
    final offset = hash.last & 15;
    final number =
        ((hash[offset] & 127) << 24) |
        (hash[offset + 1] << 16) |
        (hash[offset + 2] << 8) |
        hash[offset + 3];
    return (number % 1000000).toString().padLeft(6, '0');
  } finally {
    key.fillRange(0, key.length, 0);
  }
}

class VerificationEntry {
  final String? code;
  final String? secret;
  VerificationEntry._(this.code, this.secret);
}

VerificationEntry parseVerificationEntry(
  String input, {
  bool allowSecret = true,
  bool allowBackup = true,
}) {
  final normalized = input.replaceAll(RegExp(r'[\s-]+'), '');
  if (RegExp(r'^[0-9]{6}$').hasMatch(normalized) ||
      (allowBackup && RegExp(r'^[0-9]{8}$').hasMatch(normalized)))
    return VerificationEntry._(normalized, null);
  if (allowSecret) {
    final decoded = decodeTotpSecret(normalized);
    decoded.fillRange(0, decoded.length, 0);
    return VerificationEntry._(null, normalized.toUpperCase());
  }
  throw const FormatException('Informe o codigo numerico solicitado.');
}
