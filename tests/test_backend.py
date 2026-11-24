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
from email.message import Message
from urllib.error import HTTPError

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(BACKEND))
from app import ApiError, RamosService, command_payload, make_server, ViralizeApi
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

    def viralize_fixture(self, queue, result='seguindo'):
        account = self.service.add_account('minhaconta')
        self.service.session(self.session).update(account_id=account['id'], authenticated=True, status='connected')
        self.service.worker_command = [sys.executable, '-u', '-c', f'''import json,sys
payload = json.loads(sys.stdin.readline())
assert payload["mode"] == "command" and payload["username"] == "minhaconta"
assert payload["steps"] == [{{"command": "seguir", "target": "perfil_teste", "limit": 10}}]
assert "token" not in payload
print(json.dumps({{"type": "result", "data": {{"estado": {result!r}, "id": "28"}}}}), flush=True)
print(json.dumps({{"type": "done", "ok": True, "authenticated": True}}), flush=True)
''']
        calls = []

        class MockViralize:
            def request(self, endpoint, fields, *, write=False):
                calls.append((endpoint, fields, write))
                if endpoint == 'task':
                    return queue
                return {'success': True, 'completion_id': 84}

        self.service.viralize_api = lambda token: MockViralize()
        return calls

    def viralize(self, action):
        return self.service.mutate('POST', ['sessions', self.session, 'viralize', action], {'token': 'vzs_synthetic', 'type': 'follow'})

    def viralize_result(self, status):
        self.wait(lambda: self.service.viralize_runs[self.session]['status'] != 'running')
        run = self.service.snapshot()['viralize'][self.session]
        self.assertEqual(run['status'], status, run['message'])
        return run

    def test_viralize_reuses_the_worker_then_confirms_refill(self):
        task = {'id': 15, 'type': 'follower', 'action_url': 'https://www.instagram.com/perfil_teste/', 'is_refill': True}
        calls = self.viralize_fixture({'state': 'available', 'task': task})
        self.viralize('link')
        self.viralize('start')
        self.viralize_result('done')
        common = {'social': 'instagram', 'username': 'minhaconta'}
        self.assertEqual(calls, [
            ('account/ensure', common, True),
            ('task', {**common, 'type': 'follow'}, False),
            ('task/accept', {**common, 'task_id': 15, 'is_refill': True}, True),
            ('task/confirm-analysis', {**common, 'completion_id': 84, 'is_refill': True}, True),
        ])
        self.assertNotIn('vzs_synthetic', json.dumps(self.service.snapshot()))
        self.assertNotIn('vzs_synthetic', self.service.state_file.read_text())

    def test_viralize_pending_follow_is_not_confirmed_or_repeated(self):
        task = {'id': 15, 'type': 'follow', 'instagram_username': 'perfil_teste', 'is_refill': True}
        calls = self.viralize_fixture({'state': 'active', 'completion': {'id': 84, 'status': 'pending', 'task': task}}, 'solicitacao_pendente')
        self.viralize('start')
        self.assertTrue(self.viralize_result('attention')['can_skip'])
        self.viralize('start')
        self.assertIn('já foi tentada', self.viralize_result('attention')['message'])
        self.viralize('skip')
        self.viralize_result('done')
        self.assertEqual([call[0] for call in calls], ['task', 'task', 'task', 'task/skip'])
        self.assertEqual(calls[-1][1]['task_id'], 15)
        self.assertTrue(calls[-1][1]['is_refill'])

    def test_viralize_incompatible_offer_is_only_skipped_on_request(self):
        queue = {'state': 'available', 'task': {'id': 15, 'type': 'follow', 'action_url': 'https://instagram.com.example.test/perfil/'}}
        calls = self.viralize_fixture(queue)
        self.viralize('start')
        self.assertTrue(self.viralize_result('attention')['can_skip'])
        self.assertEqual([call[0] for call in calls], ['task'])
        self.viralize('skip')
        self.viralize_result('done')
        self.assertEqual([call[0] for call in calls], ['task', 'task', 'task/skip-browse'])

    def test_viralize_empty_queue_is_not_polled(self):
        calls = self.viralize_fixture({'state': 'empty'})
        self.viralize('start')
        self.viralize_result('empty')
        self.assertEqual([call[0] for call in calls], ['task'])

    def test_viralize_token_is_only_in_bearer_header(self):
        service = ViralizeApi('vzs_synthetic')
        with patch.object(service.opener, 'open', return_value=io.BytesIO(b'{"success":true}')) as send:
            service.request('account/ensure', {'social': 'instagram', 'username': 'teste'}, write=True)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, 'https://viralizesociais.com/api/account/ensure')
        self.assertEqual(request.get_header('Authorization'), 'Bearer vzs_synthetic')
        self.assertEqual(request.get_header('User-agent'), 'Ramos-Bot/1.0')
        self.assertEqual(request.get_header('X-client-name'), 'ramos-bot')
        self.assertNotIn(b'vzs_synthetic', request.data)
        self.assertEqual(request.method, 'POST')

    def test_viralize_distinguishes_cloudflare_denial_from_api_denial(self):
        ray = '0123456789abcdef-GRU'
        cases = (
            (403, {}, {'cloudflare_error': True, 'error_code': 1010, 'ray_id': ray}, 'Cloudflare 1010', ray),
            (403, {'CF-Mitigated': 'challenge', 'CF-Ray': ray}, '<html>Challenge</html>', 'Cloudflare', ray),
            (403, {'CF-Ray': ray}, {'success': False}, 'HTTP 403', None),
            (401, {}, {'success': False}, 'token do Viralize não foi aceito', None),
            (403, {}, {'cloudflare_error': True, 'error_code': 'vzs_synthetic', 'ray_id': 'vzs_synthetic'}, 'Cloudflare', None),
        )
        for status, response_headers, payload, expected, shown_ray in cases:
            with self.subTest(status=status, expected=expected, headers=response_headers):
                service = ViralizeApi('vzs_synthetic')
                headers = Message()
                for name, value in response_headers.items():
                    headers[name] = value
                body = payload if isinstance(payload, str) else json.dumps({**payload, 'detail': 'vzs_synthetic'})
                response = io.BytesIO(body.encode())
                error = HTTPError('https://viralizesociais.com/api/account/ensure', status, 'Error', headers, response)
                with patch.object(service.opener, 'open', side_effect=error) as send, self.assertRaises(ApiError) as caught:
                    service.request('account/ensure', {'social': 'instagram', 'username': 'teste'}, write=True)
                send.assert_called_once()
                self.assertTrue(response.closed)
                message = str(caught.exception)
                self.assertIn(expected, message)
                self.assertEqual(ray in message, shown_ray is not None)
                self.assertNotIn('vzs_synthetic', message)

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

    def test_panel_login_uses_cookie_without_browser_popup(self):
        with patch.dict(os.environ, {'RAMOS_ADMIN_PASSWORD':'synthetic-panel-password','RAMOS_API_KEY':''}):
            server=make_server(self.service,0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1])
                conn.request('GET','/api/state'); response=conn.getresponse()
                self.assertEqual(response.status,401)
                self.assertIsNone(response.getheader('WWW-Authenticate'))
                self.assertTrue(json.loads(response.read())['locked'])
                conn.request('POST','/api/access/login',body=json.dumps({'password':'wrong'}),headers={'Content-Type':'application/json'})
                response=conn.getresponse();self.assertEqual(response.status,401);response.read()
                conn.request('POST','/api/access/login',body=json.dumps({'password':'synthetic-panel-password'}),headers={'Content-Type':'application/json'})
                response=conn.getresponse();self.assertEqual(response.status,200)
                cookie=response.getheader('Set-Cookie');self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie);response.read()
                conn.request('GET','/api/state',headers={'Cookie':cookie.split(';',1)[0]})
                response=conn.getresponse();self.assertEqual(response.status,200);response.read()
                tampered=cookie.split(';',1)[0]+'x'
                conn.request('GET','/api/state',headers={'Cookie':tampered})
                response=conn.getresponse();self.assertEqual(response.status,401);response.read();conn.close()
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
