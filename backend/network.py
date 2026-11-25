"""Conexão de saída das integrações; configuração explícita, sem fallback direto."""
from __future__ import annotations

import io
import ipaddress
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit

import requests
from urllib3.exceptions import HTTPError as TransportError


def normalize_proxy(value):
    value = str(value or '').strip()
    if not value or len(value) > 1000 or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError('Informe um proxy válido.')
    if '://' not in value:
        parts = value.split(':', 3)
        if len(parts) == 4:
            host, port, user, password = parts
            if not user or not password:
                raise ValueError('Informe usuário e senha do proxy.')
            value = f'http://{quote(user, safe="")}:{quote(password, safe="")}@{host}:{port}'
        elif len(parts) == 2:
            value = 'http://' + value
        else:
            raise ValueError('Use host:porta:usuário:senha ou uma URL de proxy.')
    parsed = urlsplit(value)
    try:
        port = parsed.port
        host = parsed.hostname
        valid = parsed.scheme in {'http', 'https', 'socks5', 'socks5h'} and host and port and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment
    except ValueError:
        valid = False
    if not valid or not re.fullmatch(r'[A-Za-z0-9.:-]+', host):
        raise ValueError('Use host:porta:usuário:senha ou http://usuário:senha@host:porta.')
    credentials = ''
    if parsed.username is not None or parsed.password is not None:
        if not parsed.username or not parsed.password:
            raise ValueError('Informe usuário e senha do proxy.')
        credentials = quote(unquote(parsed.username), safe='') + ':' + quote(unquote(parsed.password), safe='') + '@'
    host = '[' + host + ']' if ':' in host else host
    scheme = 'socks5h' if parsed.scheme == 'socks5' else parsed.scheme
    return f'{scheme}://{credentials}{host}:{port}'


def proxy_fields_url(fields, current=''):
    if not isinstance(fields, dict):
        raise ValueError('Configuração de proxy inválida.')
    saved = urlsplit(current)
    host = fields.get('host', saved.hostname or '')
    port = fields.get('port', str(saved.port or ''))
    scheme = fields.get('scheme', saved.scheme or 'http')
    user = fields.get('username', unquote(saved.username or ''))
    password = fields.get('password', unquote(saved.password or ''))
    if not all(isinstance(value, str) for value in (host, port, scheme, user, password)):
        raise ValueError('Campos de proxy inválidos.')
    if type(fields.get('clear_auth', False)) is not bool:
        raise ValueError('Configuração de autenticação inválida.')
    if fields.get('clear_auth'):
        user = password = ''
    if bool(user) != bool(password):
        raise ValueError('Informe usuário e senha do proxy.')
    credentials = f'{quote(user, safe="")}:{quote(password, safe="")}@' if user else ''
    host = host.strip()
    if ':' in host and not host.startswith('['):
        host = '[' + host + ']'
    return normalize_proxy(f'{scheme}://{credentials}{host}:{port.strip()}')


def apply_instagram_proxy(client, proxy):
    # Aplicado depois de restaurar a sessão: uma configuração antiga não prevalece.
    client.set_proxy(normalize_proxy(proxy) if proxy else None)
    for name in ('private', 'public', 'graphql'):
        session = getattr(client, name, None)
        if session is not None:
            session.trust_env = False


class OutboundOpener:
    def __init__(self, proxy=''):
        self.session = requests.Session()
        self.session.trust_env = False
        self.proxy = normalize_proxy(proxy) if proxy else ''
        self.session.proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else {}

    def open(self, request, timeout=8):
        # Requests não repete falhas por padrão. Redirecionamentos também não são seguidos.
        try:
            with self.session.request(request.get_method(), request.full_url, data=request.data,
                                      headers=dict(request.header_items()), timeout=timeout,
                                      allow_redirects=False, stream=True) as response:
                body = response.raw.read(262145, decode_content=True)
                if not 200 <= response.status_code < 300:
                    raise HTTPError(request.full_url, response.status_code, 'Resposta HTTP', response.headers, io.BytesIO(body))
                return io.BytesIO(body)
        except HTTPError:
            raise
        except (requests.RequestException, TransportError, OSError):
            raise URLError('Falha na conexão de saída configurada.') from None

    def close(self):
        self.session.close()


def check_proxy_ip(proxy):
    from urllib.request import Request
    opener = OutboundOpener(normalize_proxy(proxy))
    try:
        with opener.open(Request('https://api.ipify.org?format=json', headers={'Accept': 'application/json'}), timeout=8) as response:
            raw = response.read(4097)
        if len(raw) > 4096:
            raise ValueError('Resposta de IP inválida.')
        result = json.loads(raw)
        return str(ipaddress.ip_address(result['ip']))
    finally:
        opener.close()
