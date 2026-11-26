"""Inicia o login local e abre o painel; nenhuma senha faz parte do pacote."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler
import webbrowser

SITE = 'https://ramos-bot-production.up.railway.app'
BASE = 'http://127.0.0.1:8765'


def load_config(root):
    from cryptography.fernet import Fernet
    path = root / '.ramos-pc.json'
    if path.is_file():
        data = json.loads(path.read_text(encoding='utf-8'))
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', data.get('api_key', '')):
            raise ValueError('A configuração local está inválida. Preserve a pasta e confira o arquivo de configuração.')
        Fernet(data['encryption_key'].encode())
        return data
    data = {'api_key': secrets.token_urlsafe(32), 'encryption_key': Fernet.generate_key().decode()}
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(data, stream)
    return data


def local_environment(root, config):
    return {**os.environ, 'RAMOS_HOST': '127.0.0.1', 'PORT': '8765',
            'RAMOS_DATA_DIR': str(root / 'data-pc'), 'RAMOS_API_KEY': config['api_key'],
            'RAMOS_ENCRYPTION_KEY': config['encryption_key'], 'RAMOS_ADMIN_PASSWORD': '',
            'RAMOS_TRUSTED_ORIGINS': SITE, 'RAMOS_PC_CONNECTOR': '1',
            'RAMOS_REQUIRE_PC_LOGIN': '0', 'RAMOS_INITIAL_PROXY': '',
            'RAMOS_KEY_PROBE_ON_START': '0', 'PYTHONIOENCODING': 'utf-8'}


def is_ready(config):
    opener = build_opener(ProxyHandler({}))
    request = Request(BASE + '/api/state', headers={'Authorization': 'Bearer ' + config['api_key']})
    try:
        with opener.open(request, timeout=1) as response:
            return json.load(response).get('connection', {}).get('pc_login') is True
    except (OSError, ValueError):
        return False


def main():
    root = Path(__file__).resolve().parent
    config = load_config(root)
    child = None
    try:
        if not is_ready(config):
            child = subprocess.Popen([sys.executable, str(root / 'backend' / 'app.py')],
                                     cwd=root, env=local_environment(root, config))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and child.poll() is None:
                if is_ready(config):
                    break
                time.sleep(.2)
            else:
                raise RuntimeError('Não foi possível iniciar o conector. Confira se a porta 8765 está em uso.')
        print('Conector pronto. Faça o login e o 2FA na aba que será aberta.', flush=True)
        print('Depois de enviar a sessão ao site, este módulo pode ser fechado.', flush=True)
        webbrowser.open(SITE + '/#' + urlencode({'ramos_pc': config['api_key']}))
        if child is not None:
            child.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Não imprimir configuração, credenciais ou argumentos de conexão.
        print('Não foi possível abrir o conector. Confira o Python e se a porta 8765 está livre.', file=sys.stderr)
        sys.exit(1)
