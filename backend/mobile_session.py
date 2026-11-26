"""Authenticated mobile-session handoff. Never accepts a password or 2FA key."""
import base64
import json
from pathlib import Path
import re


class MobileSessionError(ValueError):
    pass


def normalize_mobile_session(body):
    allowed = {'schema', 'username', 'user_id', 'sessionid', 'authorization', 'mid', 'claim', 'rur', 'csrf', 'device'}
    if not isinstance(body, dict) or set(body) - allowed or type(body.get('schema')) is not int or body.get('schema') != 1:
        raise MobileSessionError('Formato de sessão do aplicativo inválido.')
    for key in allowed - {'schema', 'device'}:
        if not isinstance(body.get(key, ''), str) or len(body.get(key, '')) > (16000 if key == 'authorization' else 2048):
            raise MobileSessionError('Dados da sessão inválidos.')
        if any(ord(char) < 32 or ord(char) == 127 for char in body.get(key, '')):
            raise MobileSessionError('Dados da sessão inválidos.')
    handle = body.get('username', '').strip().lstrip('@')
    uid = body.get('user_id', '')
    sessionid = body.get('sessionid', '')
    if not re.fullmatch(r'[A-Za-z0-9_.]{1,30}', handle) or not re.fullmatch(r'[1-9][0-9]{0,29}', uid) or not sessionid:
        raise MobileSessionError('O aplicativo ainda não confirmou uma sessão autenticada.')
    device = body.get('device')
    if not isinstance(device, dict) or set(device) != {'uuid', 'phone', 'android'}:
        raise MobileSessionError('Identidade do dispositivo inválida.')
    if any(not isinstance(device[k], str) for k in device):
        raise MobileSessionError('Identidade do dispositivo inválida.')
    if any(not re.fullmatch(r'[0-9a-fA-F-]{36}', device[k]) for k in ('uuid', 'phone')) or not re.fullmatch(r'android-[0-9a-fA-F]{16}', device['android']):
        raise MobileSessionError('Identidade do dispositivo inválida.')
    authorization = body.get('authorization', '')
    if authorization:
        try:
            if not authorization.startswith('Bearer IGT:2:'):
                raise ValueError()
            encoded = authorization[len('Bearer IGT:2:'):]
            data = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4), validate=True))
            if not isinstance(data, dict) or str(data.get('ds_user_id')) != uid or data.get('sessionid') != sessionid:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise MobileSessionError('A autorização não corresponde à conta autenticada.') from None
    return {**{key: body.get(key, '') for key in allowed - {'schema', 'device'}},
            'username': handle, 'device': dict(device), 'schema': 1}


def validate_mobile_session(data, proxy=''):
    import instagram_login_apk as core
    from network import apply_instagram_proxy
    from dart_http2 import configure_login_transport
    client = core.configure_client(core.load_client_class(), Path('/nonexistent/mobile-session'),
                                   Path('/nonexistent/mobile-legacy'), 30)
    try:
        uuids = client.get_settings()['uuids']
        uuids.update(uuid=data['device']['uuid'], phone_id=data['device']['phone'], android_device_id=data['device']['android'])
        client.set_uuids(uuids)
        # The phone's Android Keystore private key remains on the phone.
        # Authenticated server actions reuse the session, never the password flow.
        client.set_usdid_settings({})
        client.authorization_data = {'ds_user_id': data['user_id'], 'sessionid': data['sessionid']}
        client.mid = data['mid']
        client.set_ig_www_claim(data['claim'])
        client.set_ig_u_rur(data['rur'])
        for name, value in {'sessionid': data['sessionid'], 'ds_user_id': data['user_id'], 'mid': data['mid'], 'csrftoken': data['csrf']}.items():
            if value:
                client.private.cookies.set(name, value, domain='.instagram.com', path='/')
        client.private.headers['Authorization'] = client.authorization
        client.username = data['username']
        client.password = None
        apply_instagram_proxy(client, proxy)
        configure_login_transport(client)
        core.validate_action_session(client, data['username'])
        if str(client.user_id) != data['user_id'] or client.username.casefold() != data['username'].casefold():
            raise MobileSessionError('O servidor confirmou outra conta. A sessão não foi importada.')
        settings = client.get_settings()
        settings['_local_login_username'] = client.username
        settings['_mobile_handoff'] = 1
        return settings
    finally:
        client.private.close()
        client.public.close()
