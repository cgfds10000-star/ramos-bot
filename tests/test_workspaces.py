import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import RamosService, make_server, valid_legacy_cookie
from storage import read_json, write_json
from workspaces import BrowserWorkspaces


class BrowserIsolationChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'RAMOS_HOST': '127.0.0.1',
            'RAMOS_ENCRYPTION_KEY': Fernet.generate_key().decode(),
            'RAMOS_ADMIN_PASSWORD': 'synthetic-old-password',
            'RAMOS_API_KEY': 'synthetic-old-api-key',
        })
        self.env.start()
        self.start_server()

    def factory(self, directory):
        return RamosService(directory, dependency_check=lambda: True)

    def start_server(self):
        self.workspaces = BrowserWorkspaces(self.temp.name, self.factory, valid_legacy_cookie)
        self.workspaces.resume_saved()
        self.server = make_server(self.workspaces, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.workspaces.close()

    def tearDown(self):
        self.stop_server()
        self.env.stop(); self.temp.cleanup()

    def request(self, path='/api/state', cookie='', method='GET', body=None, csrf='', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        values = {**(headers or {})}
        if cookie: values['Cookie'] = cookie
        if csrf: values['X-Ramos-CSRF'] = csrf
        if body is not None: values['Content-Type'] = 'application/json'
        try:
            connection.request(method, path, None if body is None else json.dumps(body), values)
            response = connection.getresponse()
            data = response.read()
            parsed = json.loads(data) if 'application/json' in (response.getheader('Content-Type') or '') else data
            return response.status, parsed, response.getheader('Set-Cookie')
        finally:
            connection.close()

    def browser(self, cookie=''):
        status, state, issued = self.request(cookie=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn('locked', state)
        self.assertTrue(issued.startswith('ramos_access=v2.'))
        return issued.split(';', 1)[0], state

    def legacy_cookie(self, nonce='original-browser', seconds=3600):
        payload = str(int(time.time()) + seconds) + '.' + nonce
        key = (os.environ['RAMOS_ADMIN_PASSWORD'] + os.environ['RAMOS_ENCRYPTION_KEY']).encode()
        return 'ramos_access=' + payload + '.' + hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()

    def test_browsers_have_separate_accounts_sessions_macros_proxy_logs_and_csrf(self):
        first, a = self.browser()
        second, b = self.browser()
        self.assertNotEqual(first, second)
        self.assertNotEqual(a['csrf'], b['csrf'])
        self.assertNotEqual(a['sessions'][0]['id'], b['sessions'][0]['id'])
        mutations = [
            ('/api/accounts', {'username': 'private_owner'}),
            ('/api/sessions', {}),
            ('/api/macros', {'name': 'private macro', 'steps': [{'command': 'perfil', 'target': 'private_owner'}]}),
            ('/api/proxy', {'enabled': True, 'fields': {'host': 'proxy.example.invalid', 'port': '8080', 'scheme': 'http', 'username': 'private_user', 'password': 'private_password'}}),
        ]
        for path, body in mutations:
            self.assertEqual(self.request(path, first, 'POST', body, a['csrf'])[0], 200)
        _, a = self.browser(first)
        _, b = self.browser(second)
        self.assertEqual(len(a['sessions']), 2)
        self.assertEqual(a['accounts'][0]['username'], 'private_owner')
        self.assertEqual(a['macros'][0]['name'], 'private macro')
        self.assertTrue(a['proxy']['enabled'])
        self.assertEqual(len(b['sessions']), 1)
        self.assertEqual(b['accounts'], []); self.assertEqual(b['macros'], [])
        self.assertFalse(b['proxy']['enabled'])
        self.assertNotIn('private', json.dumps(b))
        self.assertEqual(self.request('/api/sessions', second, 'POST', {}, a['csrf'])[0], 403)
        self.assertEqual(self.request('/api/sessions/' + a['sessions'][0]['id'], second, 'DELETE', {}, b['csrf'])[0], 404)
        self.assertEqual(self.request('/api/accounts/' + a['accounts'][0]['id'], second, 'DELETE', {}, b['csrf'])[0], 404)
        self.assertEqual(self.request('/api/diagnostic/' + a['sessions'][0]['id'], second)[0], 404)
        self.assertEqual(self.request('/api/state?workspace=' + self.workspaces.identify(first.split('=', 1)[1]), second)[1]['accounts'], [])

    def test_refresh_and_server_restart_retain_only_the_same_browsers_data(self):
        cookie, state = self.browser()
        self.assertEqual(self.request('/api/accounts', cookie, 'POST', {'username': 'persistent_owner'}, state['csrf'])[0], 200)
        self.stop_server(); self.start_server()
        restored, state = self.browser(cookie)
        self.assertEqual(restored, cookie)
        self.assertEqual(state['accounts'][0]['username'], 'persistent_owner')
        anonymous, empty = self.browser()
        self.assertNotEqual(anonymous, cookie)
        self.assertEqual(empty['accounts'], [])
        self.assertTrue(Path(self.temp.name, 'browsers').is_dir())

    def test_tampered_cookie_and_cross_origin_cannot_read_or_change_another_workspace(self):
        cookie, state = self.browser()
        self.assertEqual(self.request('/api/accounts', cookie, 'POST', {'username': 'private_owner'}, state['csrf'])[0], 200)
        tampered = cookie[:-1] + ('0' if cookie[-1] != '0' else '1')
        _, stranger = self.browser(tampered)
        self.assertEqual(stranger['accounts'], [])
        self.assertEqual(self.request('/api/sessions', tampered, 'POST', {}, state['csrf'])[0], 403)
        self.assertEqual(self.request(cookie=cookie, headers={'Origin': 'https://unrelated.invalid'})[0], 403)
        self.assertEqual(self.request('/api/sessions', cookie, 'POST', {}, state['csrf'], {'Origin': 'https://unrelated.invalid'})[0], 403)
        self.assertEqual(self.request('/api/sessions', method='POST', body={}, headers={'Authorization': 'Bearer synthetic-old-api-key'})[0], 403)
        self.assertEqual(self.request('/api/access/login', cookie, 'POST', {'password': 'synthetic-old-password'}, state['csrf'])[0], 404)

    def test_cookie_is_http_only_secure_and_issued_before_the_app_and_native_api_read_state(self):
        static_root = Path(self.temp.name, 'static-fixture')
        (static_root / 'dist/client').mkdir(parents=True)
        (static_root / 'dist/client/index.html').write_text('<main>Ramos Bot</main>')
        with patch('app.ROOT', static_root):
            code, page, cookie = self.request('/', headers={'X-Forwarded-Proto': 'https'})
        self.assertEqual(code, 200)
        for flag in ['HttpOnly', 'SameSite=Strict', 'Secure', 'Max-Age=31536000', 'Path=/']:
            self.assertIn(flag, cookie)
        self.assertNotIn(b'Senha do painel', page)
        browser_cookie, web = self.browser(cookie.split(';', 1)[0])
        native_cookie, native = self.browser(browser_cookie)
        self.assertEqual(native_cookie, browser_cookie)
        self.assertEqual(native['sessions'], web['sessions'])
        self.assertEqual(native['csrf'], web['csrf'])
        self.assertNotIn(self.workspaces.index['signing_secret'], json.dumps(web))
        self.assertNotIn(b'signing_secret', Path(self.temp.name, 'browser-workspaces.json').read_bytes())

    def test_old_volume_is_recovered_once_by_existing_signed_access_not_anonymous_visitors(self):
        legacy = self.factory(self.temp.name)
        legacy.add_account('original_owner'); legacy.save(); legacy.close()
        _, anonymous = self.browser()
        self.assertEqual(anonymous['accounts'], [])
        _, expired = self.browser(self.legacy_cookie(seconds=-10))
        self.assertEqual(expired['accounts'], [])
        old_cookie = self.legacy_cookie()
        recovered_cookie, recovered = self.browser(old_cookie)
        self.assertEqual(recovered['accounts'][0]['username'], 'original_owner')
        _, other_old_browser = self.browser(self.legacy_cookie('other-browser'))
        self.assertEqual(other_old_browser['accounts'], [])
        same_cookie, same = self.browser(old_cookie)
        self.assertEqual(same_cookie, recovered_cookie)
        self.assertEqual(same['accounts'][0]['username'], 'original_owner')
        self.stop_server(); self.start_server()
        _, restored = self.browser(recovered_cookie)
        self.assertEqual(restored['accounts'][0]['username'], 'original_owner')

    def test_saved_executions_resume_without_browser_requests(self):
        folder = Path(self.temp.name, 'resume-probe')
        folder.mkdir()
        directory = folder / 'browsers' / ('a' * 32)
        write_json(directory / 'executions.json', {'session': {'desired': True}})
        called = []
        class Probe:
            def resume_runs(self): called.append('resumed')
            def close(self): called.append('closed')
        manager = BrowserWorkspaces(folder, lambda path: Probe())
        manager.resume_saved(); manager.resume_saved()
        self.assertEqual(called, ['resumed'])
        manager.close(); self.assertEqual(called, ['resumed', 'closed'])


if __name__ == '__main__':
    unittest.main()
