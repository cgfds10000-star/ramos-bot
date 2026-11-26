"""Regressões do login antes do 2FA, sem acessar contas ou a rede."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from requests import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from Cryptodome.PublicKey import RSA
from instagrapi import Client
from instagrapi.exceptions import ClientError, PleaseWaitFewMinutes
import instagram_login_apk as core
import worker
import key_probe
from app import RamosService


class PasswordPreflightChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.public_key = base64.b64encode(RSA.generate(2048).public_key().export_key(format='DER')).decode()

    def setUp(self):
        self.client = core.load_client_class()()
        self.client.username = 'teste'
        self.client.password = 'synthetic-password'
        self.headers = {'ig-set-password-encryption-key-id': '7',
                        'ig-set-password-encryption-pub-key': self.public_key}

    def prepare(self, **_kwargs):
        self.client.caa_aac = 'synthetic-aac'
        self.client.attestation_challenge_nonce = 'synthetic-nonce'
        self.client._send_private_request('bloks/async_action/com.bloks.www.bloks.caa.login.process_client_data_and_redirect/')
        return True

    def response(self, *_args, **_kwargs):
        self.client.last_response = SimpleNamespace(status_code=200, headers=self.headers.copy())
        self.client.last_json = {}
        return {}

    def test_caa_encrypts_password_with_preflight_key_without_the_failing_launcher_request(self):
        # O cliente real prepara e cifra a senha; apenas a rede é simulada.
        with patch.object(Client, '_send_private_request', side_effect=self.response), \
                patch.object(Client, 'bloks_caa_login_prepare', side_effect=self.prepare), \
                patch.object(Client, 'bloks_async_action', return_value={'synthetic': 'session'}) as send, \
                patch.object(Client, 'bloks_apply_login_response', return_value=True), \
                patch.object(Client, 'password_publickeys', side_effect=ClientError('HTTP 401')) as launcher:
            outcome = self.client.bloks_caa_login()
        self.assertTrue(outcome['logged_in'])
        launcher.assert_not_called()
        self.assertEqual(send.call_count, 1)
        params = send.call_args.args[1]['client_input_params']
        self.assertTrue(params['password'].startswith('#PWD_INSTAGRAM:4:'))
        self.assertNotIn('synthetic-password', params['password'])
        self.assertEqual(self.client.password_key_source, 'caa_headers')
        # A chave pública e o corpo de login não integram o diagnóstico.
        self.assertNotIn(self.public_key, json.dumps(self.client.login_trace))
        self.assertNotIn('synthetic-password', json.dumps(self.client.login_trace))

    def test_key_from_previous_attempt_is_discarded_and_missing_key_keeps_the_original_error(self):
        self.client.caa_password_key = (7, self.public_key)
        self.headers = {}
        with patch.object(Client, '_send_private_request', side_effect=self.response), \
                patch.object(Client, 'bloks_caa_login_prepare', side_effect=self.prepare), \
                patch.object(Client, 'password_publickeys', side_effect=ClientError('HTTP 401')) as launcher, \
                patch.object(Client, 'bloks_async_action') as send:
            with self.assertRaises(ClientError):
                self.client.bloks_caa_login()
        launcher.assert_called_once()
        send.assert_not_called()
        self.assertIsNone(self.client.caa_password_key)

    def test_refused_or_invalid_responses_cannot_supply_password_keys(self):
        cases = [(401, self.headers), (200, {**self.headers, 'ig-set-password-encryption-key-id': '256'}),
                 (200, {'ig-set-password-encryption-key-id': '7'}),
                 (200, {**self.headers, 'ig-set-password-encryption-pub-key': 'invalid'})]
        for status, headers in cases:
            with self.subTest(status=status, headers=list(headers)):
                self.assertIsNone(core.password_key_from_response(SimpleNamespace(status_code=status, headers=headers)))

    def test_script_key_headers_work_even_when_configuration_body_is_401(self):
        requests = []

        def transport(request, **_kwargs):
            requests.append(request)
            response = Response()
            response.status_code = 401
            response._content = b'{"message":"Unauthorized","status":"fail"}'
            response.url = request.url
            response.request = request
            response.headers.update(self.headers)
            response.headers['content-type'] = 'application/json'
            return response

        with patch.object(self.client.private, 'send', side_effect=transport):
            encrypted = self.client.password_encrypt('synthetic-password')
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, 'POST')
        self.assertEqual(requests[0].url, 'https://i.instagram.com/api/v1/launcher/sync/')
        self.assertEqual(requests[0].headers['Host'], 'i.instagram.com')
        self.assertNotIn('Authorization', requests[0].headers)
        self.assertNotIn('synthetic-password', requests[0].body)
        self.assertTrue(encrypted.startswith('#PWD_INSTAGRAM:4:'))
        self.assertEqual(self.client.password_key_source, 'launcher_sync')
        self.assertEqual(self.client.login_trace[-1]['etapa'], 'chave_criptografia_senha')
        self.assertEqual(self.client.login_trace[-1]['dominio'], 'i.instagram.com')
        self.assertTrue(self.client.login_trace[-1]['resposta_chave']['valid_key_headers'])
        self.assertFalse(self.client.user_id)

    def test_missing_headers_stop_before_encrypting_without_trying_another_host(self):
        response = Response()
        response.status_code = 401
        response._content = b'{"message":"Unauthorized"}'
        with patch.object(self.client, '_send_private_request', side_effect=lambda *_a, **_k: setattr(self.client, 'last_response', response)) as send:
            with self.assertRaises(ClientError):
                self.client.password_encrypt('synthetic-password')
        send.assert_called_once()
        self.assertNotIn('domain', send.call_args.kwargs)

    def test_instagram_wait_message_on_401_stops_once_before_password_encryption(self):
        for message in ('Aguarde alguns minutos antes de tentar novamente.',
                        'Please wait a few minutes before you try again.'):
            with self.subTest(message=message):
                response = Response()
                response.status_code = 401
                response.headers['content-type'] = 'application/json'
                response._content = json.dumps({'message': message, 'status': 'fail'}).encode()
                with patch.object(self.client, '_send_private_request', side_effect=lambda *_a, **_k: setattr(self.client, 'last_response', response)) as send:
                    with self.assertRaises(PleaseWaitFewMinutes):
                        self.client.password_encrypt('synthetic-password')
                send.assert_called_once()
                self.assertFalse(self.client.user_id)

    def test_key_probe_never_logs_in_or_persists_a_session(self):
        response = Response()
        response.status_code = 401
        response.headers.update(self.headers)
        response.headers['content-type'] = 'application/json'
        response._content = b'{"message":"synthetic-private-error token=synthetic-token"}'
        self.client.last_response = response
        with patch.object(core, 'configure_client', return_value=self.client), \
                patch.object(self.client, 'password_publickeys', return_value=(7, self.public_key)), \
                patch.object(Client, 'login', side_effect=AssertionError('No login allowed')) as login, \
                patch.object(core, 'save_client') as save:
            report = key_probe.probe()
        self.assertTrue(report['available'])
        self.assertEqual(report['message_category'], 'other')
        login.assert_not_called()
        save.assert_not_called()
        self.assertNotIn(self.public_key, json.dumps(report))
        self.assertNotIn('synthetic-private-error', json.dumps(report))

    def test_startup_probe_publishes_only_approved_anonymous_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory, dependency_check=lambda: True)
            try:
                result = SimpleNamespace(returncode=0, stdout=json.dumps({
                    'available': False, 'http': 401, 'key_id_header': False,
                    'public_key_header': False, 'valid_key_headers': False,
                    'body_kind': 'json', 'message_category': 'useragent_mismatch',
                    'private': 'synthetic-secret', 'error_type': 'synthetic-secret'}))
                with patch('app.subprocess.run', return_value=result) as run:
                    service.check_instagram_key()
                run.assert_called_once()
                self.assertEqual(json.loads(run.call_args.kwargs['input']), {'proxy': ''})
                self.assertEqual(service.instagram_key_check['message_category'], 'useragent_mismatch')
                self.assertNotIn('synthetic-secret', json.dumps(service.instagram_key_check))
            finally:
                service.close()


class SavedLoginProfileChecks(unittest.TestCase):
    def test_web_restores_device_ids_but_uses_the_same_transport_as_the_working_script(self):
        with tempfile.TemporaryDirectory() as directory:
            old_client = Client(private_transport='requests')
            old_settings = old_client.get_settings()
            old_settings['_local_login_username'] = 'teste'
            worker.write_json(Path(directory) / 'session.json', old_settings)
            observed = []

            def login(client, handle, password):
                observed.append((client.private_transport, client.phone_id, handle))
                client.authorization_data = {'ds_user_id': '123', 'sessionid': 'synthetic-session'}
                return True

            output = io.StringIO()
            with patch.object(Client, 'login', new=login), patch.object(worker, 'OUTPUT', output):
                worker.run({'directory': directory, 'username': 'teste', 'password': 'synthetic-password', 'mode': 'login'})
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(events[-1], {'type': 'done', 'ok': True, 'authenticated': True})
            self.assertEqual(observed, [('curl', old_client.phone_id, 'teste')])
            saved = worker.read_json(Path(directory) / 'session.json')
            self.assertEqual(saved['private_transport'], 'curl')
            self.assertEqual(saved['uuids'], old_settings['uuids'])
            self.assertNotIn('synthetic-password', json.dumps(saved))

    def test_old_forced_method_migrates_to_cli_auto_and_later_user_choice_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory, dependency_check=lambda: True)
            service.data['settings']['two_factor_method'] = 'autenticador'
            service.data.pop('login_flow_revision')
            service.save()
            service.close()
            service = RamosService(directory, dependency_check=lambda: True)
            self.assertEqual(service.data['settings']['two_factor_method'], 'auto')
            service.mutate('POST', ['settings'], {'two_factor_method': 'autenticador'})
            service.close()
            service = RamosService(directory, dependency_check=lambda: True)
            try:
                self.assertEqual(service.data['settings']['two_factor_method'], 'autenticador')
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
