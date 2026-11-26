"""Isolated server storage addressed by a signed, persistent browser cookie."""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import re
import secrets
import threading

from storage import read_json, write_json

WORKSPACE_ID = re.compile(r'[0-9a-f]{32}')
COOKIE_TOKEN = re.compile(r'v2\.([0-9a-f]{32})\.([0-9a-f]{64})')


class BrowserWorkspaces:
    def __init__(self, directory, service_factory, legacy_validator=lambda _: False):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.storage = self.directory / 'browsers'
        self.storage.mkdir(exist_ok=True)
        self.index_file = self.directory / 'browser-workspaces.json'
        self.factory = service_factory
        self.legacy_validator = legacy_validator
        self.lock = threading.RLock()
        self.services = {}
        self.closed = False
        try:
            self.index = read_json(self.index_file)
        except FileNotFoundError:
            self.index = {'version': 1, 'signing_secret': secrets.token_hex(32)}
            write_json(self.index_file, self.index)
        if (not isinstance(self.index, dict) or self.index.get('version') != 1
                or not re.fullmatch(r'[0-9a-f]{64}', self.index.get('signing_secret', ''))):
            raise RuntimeError('Não foi possível abrir o índice dos navegadores. Preserve os dados do volume.')
        owner = self.index.get('legacy_owner')
        if owner is not None and (not isinstance(owner, dict)
                or not WORKSPACE_ID.fullmatch(owner.get('id', ''))
                or not re.fullmatch(r'[0-9a-f]{64}', owner.get('fingerprint', ''))):
            raise RuntimeError('O vínculo do navegador antigo não é válido. Preserve os dados do volume.')
        self.secret = bytes.fromhex(self.index['signing_secret'])

    def signature(self, value):
        return hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()

    def cookie(self, workspace_id):
        payload = 'v2.' + workspace_id
        return payload + '.' + self.signature(payload)

    def identify(self, token):
        match = COOKIE_TOKEN.fullmatch(token or '')
        if match and hmac.compare_digest(match[2], self.signature('v2.' + match[1])):
            return match[1]
        return None

    def service_directory(self, workspace_id):
        owner = self.index.get('legacy_owner', {})
        return self.directory if owner.get('id') == workspace_id else self.storage / workspace_id

    def activate(self, directory):
        directory = Path(directory)
        service = self.services.get(directory)
        if service is None:
            service = self.factory(directory)
            self.services[directory] = service
            service.resume_runs()
        return service

    def resolve(self, token, create=False):
        """Only a signed cookie selects existing data; IDs in requests never do."""
        with self.lock:
            if self.closed:
                raise RuntimeError('O servidor está encerrando.')
            workspace_id = self.identify(token)
            if workspace_id is None and self.legacy_validator(token or ''):
                # A signed old access cookie may recover the old shared volume once.
                # Every other old browser gets a different, empty namespace.
                fingerprint = hashlib.sha256(token.encode()).hexdigest()
                workspace_id = self.signature('legacy-browser:' + token)[:32]
                if not self.index.get('legacy_owner') and (self.directory / 'state.json').is_file():
                    self.index['legacy_owner'] = {'id': workspace_id, 'fingerprint': fingerprint}
                    write_json(self.index_file, self.index)
            if workspace_id is None:
                if not create:
                    return None
                workspace_id = secrets.token_hex(16)
            return self.activate(self.service_directory(workspace_id)), self.cookie(workspace_id)

    def resume_saved(self):
        """Resume saved work without needing a browser to reopen the page."""
        with self.lock:
            directories = [self.directory] + [item for item in self.storage.iterdir()
                if item.is_dir() and WORKSPACE_ID.fullmatch(item.name)]
            for directory in directories:
                try:
                    plans = read_json(directory / 'executions.json')
                except FileNotFoundError:
                    continue
                if isinstance(plans, dict) and any(isinstance(plan, dict) and plan.get('desired') for plan in plans.values()):
                    self.activate(directory)

    def close(self):
        with self.lock:
            self.closed = True
            services = list(self.services.values())
        for service in services:
            service.close()
