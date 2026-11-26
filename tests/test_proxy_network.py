import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import ApiError, RamosService, ViralizeApi
from network import OutboundOpener, apply_instagram_proxy, normalize_proxy, proxy_fields_url
from instagrapi import Client


class ProxyChecks(unittest.TestCase):
    def test_split_fields_preserve_saved_auth_and_allow_explicit_removal(self):
        saved = 'socks5h://synthetic-user:pass%40word@old.example:1080'
        self.assertEqual(proxy_fields_url({'host': 'new.example', 'port': '1081'}, saved),
                         'socks5h://synthetic-user:pass%40word@new.example:1081')
        self.assertEqual(proxy_fields_url({'password': 'new:pass'}, saved),
                         'socks5h://synthetic-user:new%3Apass@old.example:1080')
        self.assertEqual(proxy_fields_url({'clear_auth': True}, saved), 'socks5h://old.example:1080')
        self.assertEqual(proxy_fields_url({'host': '::1', 'port': '8080', 'scheme': 'http'}), 'http://[::1]:8080')
        for fields in ({'host': 'host', 'port': '99999'}, {'host': 'host/path', 'port': '80'}, {'username': ''}, [], {'password': None}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                proxy_fields_url(fields, saved)

    def test_saving_split_fields_never_returns_auth_and_invalid_edits_do_not_replace_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory)
            try:
                service.mutate('POST', ['proxy'], {'enabled': True, 'fields': {'scheme': 'http', 'host': 'proxy.example', 'port': '8080', 'username': 'synthetic-user', 'password': 'synthetic-secret'}})
                service.mutate('POST', ['proxy'], {'enabled': True, 'fields': {'host': 'new.example', 'port': '9090'}})
                saved = service.data['proxy']
                self.assertEqual(saved, 'http://synthetic-user:synthetic-secret@new.example:9090')
                snapshot = service.snapshot()['proxy']
                self.assertTrue(snapshot['has_auth'])
                self.assertNotIn('synthetic-secret', json.dumps(snapshot))
                self.assertNotIn('synthetic-user', json.dumps(snapshot))
                with self.assertRaises(ApiError):
                    service.mutate('POST', ['proxy'], {'enabled': True, 'fields': {'port': 'invalid'}})
                self.assertEqual(service.data['proxy'], saved)
            finally:
                service.close()

    def test_supplier_format_is_normalized_and_special_characters_are_encoded(self):
        self.assertEqual(normalize_proxy('proxy.example:6969:synthetic-user:pass@word:42'),
                         'http://synthetic-user:pass%40word%3A42@proxy.example:6969')
        self.assertEqual(normalize_proxy('socks5://user:pass@proxy.example:1080'),
                         'socks5h://user:pass@proxy.example:1080')
        self.assertEqual(normalize_proxy('http://user:pass%40word@proxy.example:80/'),
                         'http://user:pass%40word@proxy.example:80')
        for value in ('', 'ftp://host:80', 'host:99999', 'http://host:80/path', 'http://host:80?x=1', 'host:80:user:', 'host:80\nInjected: value'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_proxy(value)

    def test_instagram_uses_current_proxy_on_private_public_and_graphql_sessions(self):
        client = Client()
        client.set_proxy('http://old.example:80')
        proxy = normalize_proxy('proxy.example:6969:user:pass')
        apply_instagram_proxy(client, proxy)
        for name in ('private', 'public', 'graphql'):
            session = getattr(client, name, None)
            if session is not None:
                self.assertEqual(session.proxies, {'http': proxy, 'https': proxy})
                self.assertFalse(session.trust_env)
        apply_instagram_proxy(client, '')
        self.assertEqual(client.private.proxies, {})
        self.assertEqual(client.public.proxies, {})

    def test_proxy_handles_http_and_https_despite_environment_overrides(self):
        proxy = 'http://user:pass@proxy.example:6969'
        with patch.dict(os.environ, {'NO_PROXY': '*', 'HTTPS_PROXY': 'http://wrong.example:80'}):
            opener = OutboundOpener(proxy)
            try:
                for target in ('http://service.example/', 'https://service.example/'):
                    settings = opener.session.merge_environment_settings(target, {}, False, True, None)
                    self.assertEqual(settings['proxies'], {'http': proxy, 'https': proxy})
                self.assertEqual(opener.session.get_adapter('https://service.example/').max_retries.total, 0)
            finally:
                opener.close()

    def test_failure_is_not_retried_directly_and_does_not_expose_credentials(self):
        opener = OutboundOpener('http://user:synthetic-password@proxy.example:80')
        try:
            import requests
            with patch.object(opener.session, 'request', side_effect=requests.exceptions.ProxyError('synthetic-password')) as send:
                with self.assertRaises(URLError) as caught:
                    opener.open(Request('https://service.example/'))
            send.assert_called_once()
            self.assertNotIn('synthetic-password', str(caught.exception))
        finally:
            opener.close()

    def test_http_error_keeps_status_and_body_for_existing_api_handling(self):
        opener = OutboundOpener('http://proxy.example:80')
        response = Mock(status_code=429, headers={'Retry-After': '60'})
        response.raw.read.return_value = b'{"success":false}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        try:
            with patch.object(opener.session, 'request', return_value=response) as send:
                with self.assertRaises(HTTPError) as caught:
                    opener.open(Request('https://service.example/'))
            with caught.exception as error:
                self.assertEqual(error.code, 429)
                self.assertEqual(error.headers['Retry-After'], '60')
                self.assertEqual(error.read(), b'{"success":false}')
            self.assertFalse(send.call_args.kwargs['allow_redirects'])
            send.assert_called_once()
        finally:
            opener.close()

    def test_real_http_request_reaches_authenticated_proxy_instead_of_destination(self):
        observed = []
        class LocalProxy(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                observed.append((self.path, self.headers.get('Proxy-Authorization')))
                body = b'{"ip":"203.0.113.8"}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), LocalProxy)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = OutboundOpener(f'http://synthetic-user:synthetic-password@127.0.0.1:{server.server_port}')
        try:
            with opener.open(Request('http://destination.invalid/resource'), timeout=2) as response:
                self.assertEqual(json.load(response)['ip'], '203.0.113.8')
            self.assertEqual(observed, [('http://destination.invalid/resource', 'Basic ' + base64.b64encode(b'synthetic-user:synthetic-password').decode())])
        finally:
            opener.close()
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_ip_test_is_server_side_and_proxy_credentials_never_enter_snapshot(self):
        checker = Mock(return_value='203.0.113.9')
        factory = Mock()
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory, proxy_checker=checker, viralize_api=factory)
            try:
                service.mutate('POST', ['proxy'], {'enabled': True, 'url': 'proxy.example:6969:user:synthetic-password'})
                result = service.mutate('POST', ['proxy', 'test'], {})
                self.assertTrue(result['ok'])
                checker.assert_called_once_with('http://user:synthetic-password@proxy.example:6969')
                self.assertEqual(service.snapshot()['proxy']['check']['ip'], '203.0.113.9')
                self.assertNotIn('synthetic-password', json.dumps(service.snapshot()))
                factory.assert_not_called()
                service.new_viralize_client('vzs_synthetic')
                factory.assert_called_once_with('vzs_synthetic', proxy='http://user:synthetic-password@proxy.example:6969')
                service.mutate('POST', ['proxy'], {'enabled': False})
                self.assertIsNone(service.snapshot()['proxy']['check'])
            finally: service.close()

    def test_failed_ip_test_reports_no_ip_and_cannot_fake_success(self):
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory, proxy_checker=Mock(side_effect=ValueError('synthetic-password')))
            try:
                service.mutate('POST', ['proxy'], {'enabled': True, 'url': 'host.example:80'})
                result = service.check_proxy()
                self.assertFalse(result['ok'])
                self.assertIsNone(result['check']['ip'])
                self.assertNotIn('synthetic-password', json.dumps(result))
            finally: service.close()

    def test_legacy_pc_proxy_and_environment_do_not_override_server_connection(self):
        from storage import read_json, write_json
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'RAMOS_INITIAL_PROXY': '127.0.0.1:8899'}):
            service = RamosService(directory)
            self.assertFalse(service.snapshot()['proxy']['enabled'])
            service.close()
            path = Path(directory) / 'state.json'
            data = read_json(path)
            data.pop('egress_revision', None)
            data['proxy'] = 'http://127.0.0.1:8899'
            data['proxy_check'] = {'ip': '127.0.0.1'}
            write_json(path, data)
            restored = RamosService(directory)
            try:
                self.assertFalse(restored.snapshot()['proxy']['enabled'])
                self.assertEqual(restored.data['proxy'], '')
                self.assertNotIn('proxy_check', restored.data)
            finally:
                restored.close()

    def test_proxy_cannot_change_in_the_gap_between_viralize_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory)
            try:
                service.viralize_runs['synthetic'] = {'status': 'running', 'stop': False}
                with self.assertRaises(ApiError) as caught:
                    service.mutate('POST', ['proxy'], {'enabled': True, 'url': 'host.example:80'})
                self.assertEqual(caught.exception.status, 409)
            finally: service.close()


if __name__ == '__main__': unittest.main()
