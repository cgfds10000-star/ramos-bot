"""Real TLS/HTTP2 server and the compiled transport; no Instagram traffic."""
import json
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from dart_http2 import DartHttp2Adapter, configure_login_transport
from login_diagnostics import runtime_diagnostic
from instagrapi import Client
import worker
from storage import read_json


BINARY = os.environ.get('RAMOS_TEST_DART_TRANSPORT')


@unittest.skipUnless(BINARY and shutil.which('node') and shutil.which('openssl'),
    'Compile the Dart transport and set RAMOS_TEST_DART_TRANSPORT to run real HTTP/2 tests')
class DartTransportChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.cert = cls.root / 'cert.pem'
        key = cls.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(key), '-out', str(cls.cert), '-days', '1', '-subj', '/CN=localhost',
            '-addext', 'subjectAltName=DNS:localhost'], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=True)
        cls.server = subprocess.Popen(['node', str(Path(__file__).parent / 'fixtures/h2_provider.mjs'),
            str(key), str(cls.cert)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        cls.ports = json.loads(cls.server.stdout.readline())

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=3)
        cls.server.stdout.close()
        cls.directory.cleanup()

    def setUp(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.adapter = DartHttp2Adapter([BINARY])
        self.session.mount('https://', self.adapter)
        self.session.verify = str(self.cert)
        self.session.max_redirects = 0
        self.base = f'https://localhost:{self.ports["h2"]}'

    def tearDown(self):
        self.session.close()

    def counts(self):
        return self.session.get(self.base + '/counts').json()

    def test_real_h2_tls13_form_gzip_cookie_scope_and_session_reuse(self):
        response = self.session.post(self.base + '/first', data={'password': 'synthetic & senha', 'code': '003754'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['http_version'], '2')
        self.assertEqual(response.json()['tls'], 'TLSv1.3')
        self.assertEqual(response.ramos_transport['alpn'], 'h2')
        self.assertIn('code=003754', response.json()['body'])
        self.assertEqual(response.cookies.get('csrftoken'), 'synthetic-csrf')
        self.assertEqual(response.cookies.get('sessionid'), 'synthetic-session')
        self.assertNotIn('content-encoding', response.headers)
        pid = self.adapter.process.pid
        outside = self.session.get(self.base + '/outside').json()
        private = self.session.get(self.base + '/private/check?query=one').json()
        self.assertIn('csrftoken=synthetic-csrf', outside['cookie'])
        self.assertNotIn('sessionid', outside['cookie'])
        self.assertIn('sessionid=synthetic-session', private['cookie'])
        self.assertEqual(private['path'], '/private/check?query=one')
        self.assertEqual(self.adapter.process.pid, pid)

    def test_401_and_429_preserve_body_status_retry_header_and_protocol(self):
        refused = self.session.post(self.base + '/refused', data={'signed_body': 'synthetic'})
        self.assertEqual(refused.status_code, 401)
        self.assertTrue(refused.json()['message'].startswith('Please wait'))
        limited = self.session.get(self.base + '/limited')
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers['Retry-After'], '60')
        self.assertEqual(limited.text, '<html>limited</html>')
        self.assertEqual(limited.raw.version, 20)
        client = Client()
        try:
            client.private = self.session
            client.last_response = refused
            diagnostic = runtime_diagnostic(client)
            self.assertEqual(diagnostic['transporte_privado'], 'dart-http2')
            self.assertEqual(diagnostic['http_version'], '2')
            self.assertEqual(diagnostic['tls_minima'], '1.3')
            self.assertEqual(diagnostic['mensagem_provedor'], 'aguardar_alguns_minutos')
        finally:
            client.public.close()

    def test_redirect_does_not_repeat_credentials_or_follow_location(self):
        with self.assertRaises(requests.TooManyRedirects):
            self.session.post(self.base + '/redirect', data={'password': 'synthetic-password'})
        self.assertNotIn('/must-not-send', self.counts())

    def test_timeout_closes_transport_without_replay_or_curl_fallback(self):
        observer = requests.Session()
        observer.trust_env = False
        observer.verify = str(self.cert)
        observer.mount('https://', DartHttp2Adapter([BINARY]))
        try:
            before = observer.get(self.base + '/counts').json().get('/slow', 0)
            with self.assertRaises(requests.ConnectionError) as caught:
                self.session.post(self.base + '/slow', data={'password': 'synthetic-password'}, timeout=.3)
            self.assertNotIn('synthetic-password', str(caught.exception))
            self.assertTrue(self.adapter.closed)
            with self.assertRaises(requests.ConnectionError):
                self.session.post(self.base + '/slow', data={'password': 'synthetic-password'})
            after = observer.get(self.base + '/counts').json().get('/slow', 0)
            self.assertEqual(after - before, 1)
        finally:
            observer.close()

    def test_stream_reset_is_not_replayed(self):
        with self.assertRaises(requests.ConnectionError):
            self.session.post(self.base + '/reset', data={'code': '003754'})
        self.assertTrue(self.adapter.closed)

    def test_http1_server_is_rejected_before_sending_password(self):
        with self.assertRaises(requests.ConnectionError) as caught:
            self.session.post(f'https://localhost:{self.ports["h1"]}/', data={'password': 'synthetic-password'})
        self.assertNotIn('synthetic-password', str(caught.exception))

    def test_untrusted_certificate_is_rejected(self):
        self.session.verify = True
        with self.assertRaises(requests.ConnectionError):
            self.session.post(self.base + '/untrusted', data={'password': 'synthetic-password'})

    def test_configured_proxy_is_never_bypassed(self):
        client = Client()
        try:
            original = client.private.get_adapter('https://i.instagram.com/')
            client.set_proxy('http://proxy.example:8080')
            with patch.dict(os.environ, {'RAMOS_DART_TRANSPORT': BINARY}):
                configure_login_transport(client)
            self.assertIs(client.private.get_adapter('https://i.instagram.com/'), original)
            with self.assertRaises(requests.ConnectionError):
                self.session.get(self.base + '/', proxies={'https': 'http://proxy.example:8080'})
            self.assertIsNone(self.adapter.process)
        finally:
            client.private.close()
            client.public.close()

    def test_worker_install_uses_dart_with_existing_python_session_settings(self):
        client = Client()
        try:
            saved = client.get_settings()
            with patch.dict(os.environ, {'RAMOS_DART_TRANSPORT': BINARY}):
                configure_login_transport(client)
            adapter = client.private.get_adapter('https://i.instagram.com/')
            self.assertIsInstance(adapter, DartHttp2Adapter)
            self.assertEqual(client.private.max_redirects, 0)
            self.assertEqual(client.get_settings()['private_transport'], saved['private_transport'])
        finally:
            client.private.close()
            client.public.close()

    def test_complete_real_worker_runs_the_ten_app_steps_over_dart_h2(self):
        base = self.base
        cert = str(self.cert)
        class LocalProvider(DartHttp2Adapter):
            def send(self, request, **kwargs):
                uri = urlsplit(request.url)
                local = request.copy()
                local.url = base + uri.path + ('?' + uri.query if uri.query else '')
                kwargs['verify'] = cert
                response = super().send(local, **kwargs)
                response.url = request.url
                response.request = request
                return response
        def install(client):
            client.private.mount('https://', LocalProvider([BINARY]))
            client.private.max_redirects = 0
            client.request_timeout = 0
        output = io.StringIO()
        before = self.counts()
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'configure_login_transport', install), patch.object(worker, 'OUTPUT', output), patch.object(sys, 'stdin', io.StringIO('{"code":"003754"}\n')):
            worker.run({'directory': directory, 'mode': 'login', 'username': 'teste',
                        'password': 'synthetic-password', 'two_factor_method': 'auto', 'proxy': ''})
            diagnostic = read_json(Path(directory) / 'diagnostic.json')
            settings = read_json(Path(directory) / 'session.json')
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events[-1], {'type': 'done', 'ok': True, 'authenticated': True})
        self.assertEqual(diagnostic['transporte_privado'], 'dart-http2')
        self.assertEqual(diagnostic['http_version'], '2')
        self.assertTrue(diagnostic['sessao_recebida'])
        self.assertTrue(settings['usdid']['registered'])
        after = self.counts()
        calls = {path: count - before.get(path, 0) for path, count in after.items() if path.startswith('/api/') or path == '/graphql_www'}
        self.assertEqual(sum(calls.values()), 10)
        self.assertTrue(all(count == 1 for count in calls.values()))
        for secret in ('synthetic-password', '003754', 'synthetic-session'):
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, json.dumps(diagnostic))


if __name__ == '__main__':
    unittest.main()
