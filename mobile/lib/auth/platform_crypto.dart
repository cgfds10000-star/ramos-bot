import 'dart:convert';

import 'package:flutter/services.dart';

import 'login_client.dart';

class AndroidLoginCrypto implements LoginCrypto, DeviceRegistrationStore {
  static const _channel = MethodChannel('ramos/login_crypto');
  @override
  Future<Map<String, String>> identity() async {
    final result = await _channel.invokeMapMethod<String, String>('identity');
    if (result == null)
      throw LoginFailure('Nao foi possivel preparar o dispositivo Android.');
    return result;
  }

  @override
  Future<void> markDeviceRegistered() =>
      _channel.invokeMethod<void>('markRegistered');

  @override
  Future<String> encrypt(
    String password,
    String publicKey,
    String keyId,
  ) async {
    final result = await _channel.invokeMethod<String>('encryptPassword', {
      'password': password,
      'publicKey': publicKey,
      'keyId': keyId,
    });
    if (result == null)
      throw LoginFailure('Nao foi possivel criptografar a senha.');
    return result;
  }
}

class AndroidLoginStorage {
  static const _channel = MethodChannel('ramos/login_crypto');
  Future<Map<String, dynamic>> read() async {
    final text = await _channel.invokeMethod<String>('readState');
    if (text == null || text.isEmpty) return {};
    final data = jsonDecode(text);
    return data is Map<String, dynamic> ? data : {};
  }

  Future<void> write(Map<String, dynamic> data) =>
      _channel.invokeMethod<void>('writeState', jsonEncode(data));
  Future<void> clear() => _channel.invokeMethod<void>('clearState');
}
