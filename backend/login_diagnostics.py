"""Metadados limitados de execucao; nenhum corpo, cookie ou credencial."""
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlsplit

_ROUTES = {
    '/api/v1/launcher/sync/': 'chave_criptografia_senha',
    '/api/v1/qe/sync/': 'configuracao_experimentos',
    '/api/v1/attestation/create_android_keystore/': 'nonce_dispositivo',
}


def runtime_diagnostic(client):
    response = getattr(client, 'last_response', None)
    headers = getattr(response, 'headers', {}) or {}
    url = getattr(response, 'url', '')
    path = urlsplit(url).path if isinstance(url, str) else ''
    private = getattr(client, 'private', None)
    transport = getattr(client, 'private_transport', None)
    public_transport = getattr(client, 'public_transport', None)
    protocol = getattr(response, 'ramos_transport', {})
    protocol = protocol if isinstance(protocol, dict) else {}
    try:
        if getattr(private.get_adapter('https://i.instagram.com/'), 'transport_name', None) == 'dart-http2':
            transport = 'dart-http2'
    except AttributeError:
        pass
    packages = {}
    for name in ('requests', 'curl_cffi', 'urllib3', 'cryptography'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    message_kind = 'indisponivel'
    try:
        data = response.json() if response is not None else {}
        message = data.get('message') if isinstance(data, dict) else None
        if isinstance(message, str):
            if message.casefold().startswith(('aguarde alguns minutos', 'please wait a few minutes')):
                message_kind = 'aguardar_alguns_minutos'
            elif message in ('login_required', 'checkpoint_required', 'challenge_required'):
                message_kind = message
            else:
                message_kind = 'outra_mensagem'
    except (ValueError, AttributeError):
        message_kind = 'resposta_sem_json'
    retry = headers.get('Retry-After')
    retry_seconds = int(retry) if isinstance(retry, str) and retry.isascii() and retry.isdigit() and len(retry) <= 8 else None
    return {
        'diagnostico_servidor': 2,
        'transporte_privado': transport if transport in ('curl', 'requests', 'utls', 'dart-http2') else 'indisponivel',
        'http_version': protocol.get('http_version') if protocol.get('http_version') in ('2', '1.1') else None,
        'alpn': protocol.get('alpn') if protocol.get('alpn') in ('h2', 'http/1.1') else None,
        'tls_minima': '1.3' if protocol.get('tls_minima') == '1.3' else None,
        'transporte_publico': public_transport if public_transport in ('curl', 'requests') else 'indisponivel',
        'proxy_configurado': bool(getattr(private, 'proxies', {})),
        'proxy_ambiente_habilitado': bool(getattr(private, 'trust_env', False)),
        'dependencias_transporte': packages,
        'ultima_rota': _ROUTES.get(path, 'outra_rota'),
        'resposta_chave_senha': {
            'identificador_recebido': bool(headers.get('ig-set-password-encryption-key-id')),
            'chave_publica_recebida': bool(headers.get('ig-set-password-encryption-pub-key')),
        },
        'mensagem_provedor': message_kind,
        'aguardar_segundos': retry_seconds,
    }
