import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import os
import threading
import http.client

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(BACKEND))
from app import ApiError, RamosService, command_payload, make_server
from storage import write_json, read_json
import worker

FAKE = '''import json,sys,time
payload=json.loads(sys.stdin.readline())
print(json.dumps({"type":"log","tone":"info","message":"test " + payload.get("password", "")}),flush=True)
print(json.dumps({"type":"input_required","kind":"authenticator","message":"Codigo"}),flush=True)
code=json.loads(sys.stdin.readline()).get("code")
print(json.dumps({"type":"done","ok":code=="123456","authenticated":code=="123456"}),flush=True)
'''


class BackendChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = RamosService(self.temp.name, worker_command=[sys.executable, '-u', '-c', FAKE], dependency_check=lambda: True)
        self.session = self.service.data['sessions'][0]['id']

    def tearDown(self):
        self.service.close()
        time.sleep(.05)
        self.temp.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('A operacao simulada nao terminou.')

    def test_login_requires_verification_and_redacts_secrets(self):
        password = 'synthetic-password-42'
        self.service.start(self.session, {'mode': 'login', 'username': 'teste', 'password': password})
        self.wait(lambda: self.service.session(self.session)['status'] == 'awaiting_input')
        self.assertFalse(self.service.session(self.session)['authenticated'])
        snapshot = self.service.snapshot()
        self.assertNotIn(password, json.dumps(snapshot))
        self.assertNotIn(password, self.service.state_file.read_text())
        challenge = self.service.session(self.session)['challenge']['id']
        self.service.submit_input(self.session, {'challenge_id': challenge, 'code': '123456'})
        self.wait(lambda: self.session not in self.service.jobs)
        self.assertEqual(self.service.session(self.session)['status'], 'connected')
        self.assertTrue(self.service.session(self.session)['authenticated'])
        restored = RamosService(self.temp.name, dependency_check=lambda: True)
        self.assertFalse(restored.session(self.session)['authenticated'])
        self.assertEqual(restored.session(self.session)['status'], 'disconnected')

    def test_cancel_prevents_a_late_success(self):
        self.service.start(self.session, {'mode': 'login', 'username': 'teste', 'password': 'synthetic'})
        self.wait(lambda: self.service.session(self.session)['challenge'] is not None)
        challenge = self.service.session(self.session)['challenge']['id']
        self.service.cancel(self.session, disconnect=True)
        with self.assertRaises(ApiError):
            self.service.submit_input(self.session, {'challenge_id': challenge, 'code': '123456'})
        self.assertEqual(self.service.session(self.session)['status'], 'disconnected')
        self.assertNotIn(self.session, self.service.jobs)

    def test_same_account_cannot_run_in_two_sessions(self):
        self.service.start(self.session, {'mode': 'login', 'username': 'teste', 'password': 'synthetic'})
        other = self.service.mutate('POST', ['sessions'], {})['session_id']
        with self.assertRaises(ApiError) as caught:
            self.service.start(other, {'mode': 'login', 'username': 'TESTE', 'password': 'synthetic'})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(len(self.service.data['accounts']), 1)

    def test_invalid_macro_is_not_partially_saved(self):
        with self.assertRaises(ApiError):
            self.service.mutate('POST', ['macros'], {'name': 'Teste', 'steps': [{'command': 'buscar', 'target': 'x'}, {'command': 'shell', 'target': 'whoami'}]})
        self.assertEqual(self.service.snapshot()['macros'], [])

    def test_proxy_credentials_are_not_returned_to_browser(self):
        self.service.mutate('POST', ['proxy'], {'enabled': True, 'url': 'http://test-user:synthetic-proxy-password@example.com:8080'})
        snapshot = self.service.snapshot()
        self.assertTrue(snapshot['proxy']['enabled'])
        self.assertNotIn('synthetic-proxy-password', json.dumps(snapshot))
        self.assertEqual(snapshot['proxy']['host'], 'example.com')

    def test_worker_requests_code_via_json(self):
        previous_input, previous_output = sys.stdin, worker.OUTPUT
        captured = io.StringIO()
        try:
            sys.stdin = io.StringIO('{"code":"123456"}\n')
            worker.OUTPUT = captured
            self.assertEqual(worker.ask_code('authenticator'), '123456')
            event = json.loads(captured.getvalue())
            self.assertEqual(event['type'], 'input_required')
            self.assertEqual(event['kind'], 'authenticator')
            self.assertNotIn('123456', captured.getvalue())
        finally:
            sys.stdin, worker.OUTPUT = previous_input, previous_output

    def test_authenticator_rejects_non_six_digit_code(self):
        previous_input, previous_output = sys.stdin, worker.OUTPUT
        try:
            sys.stdin = io.StringIO('{"code":"12345678"}\n')
            worker.OUTPUT = io.StringIO()
            with self.assertRaises(ValueError): worker.ask_code('authenticator')
        finally:
            sys.stdin, worker.OUTPUT = previous_input, previous_output

    def test_ids_general_search_and_removed_approval(self):
        self.assertTrue(command_payload({'command':'seguir','target':'12345','by_id':True})['by_id'])
        self.assertTrue(command_payload({'command':'buscar','target':'teste','general':True})['general'])
        with self.assertRaises(ApiError): command_payload({'command':'seguir','target':'teste','by_id':True})
        with self.assertRaises(ApiError): self.service.mutate('POST',['settings'],{'two_factor_method':'auto'})
        self.assertEqual(self.service.snapshot()['settings']['two_factor_method'],'autenticador')

    def test_session_storage_is_encrypted_and_roundtrips(self):
        from cryptography.fernet import Fernet
        target = Path(self.temp.name) / 'encrypted.json'
        with patch.dict(os.environ, {'RAMOS_ENCRYPTION_KEY':Fernet.generate_key().decode()}):
            write_json(target, {'sessionid':'synthetic-session-token'})
            self.assertNotIn(b'synthetic-session-token',target.read_bytes())
            self.assertEqual(read_json(target),{'sessionid':'synthetic-session-token'})

    def test_hosted_api_requires_key_and_csrf(self):
        with patch.dict(os.environ, {'RAMOS_API_KEY':'synthetic-api-secret','RAMOS_ADMIN_PASSWORD':''}):
            server = make_server(self.service,0)
            thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                conn = http.client.HTTPConnection('127.0.0.1',server.server_address[1])
                conn.request('GET','/api/state'); response=conn.getresponse(); self.assertEqual(response.status,401); response.read()
                conn.request('GET','/api/state',headers={'Authorization':'Bearer synthetic-api-secret'}); response=conn.getresponse(); self.assertEqual(response.status,200); response.read()
                conn.request('POST','/api/sessions',body='{}',headers={'Authorization':'Bearer synthetic-api-secret','Content-Type':'application/json'}); response=conn.getresponse(); self.assertEqual(response.status,403); response.read()
                conn.close()
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
