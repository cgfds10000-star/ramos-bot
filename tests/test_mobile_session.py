import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import RamosService, ApiError
from mobile_session import normalize_mobile_session, validate_mobile_session, MobileSessionError
from storage import read_json, write_json
import instagram_login_apk as core
import worker


def payload():
    return {'schema': 1, 'username': 'teste', 'user_id': '123', 'sessionid': 'synthetic-session',
            'authorization': 'Bearer IGT:2:' + base64.b64encode(json.dumps({'ds_user_id': '123', 'sessionid': 'synthetic-session'}).encode()).decode(),
            'mid': 'synthetic-mid', 'claim': '0', 'rur': '', 'csrf': 'synthetic-csrf',
            'device': {'uuid': '11111111-1111-4111-8111-111111111111', 'phone': '22222222-2222-4222-8222-222222222222', 'android': 'android-0123456789abcdef'}}


def settings(data, proxy=''):
    return {'authorization_data': {'ds_user_id': data['user_id'], 'sessionid': data['sessionid']},
            '_local_login_username': data['username'], '_mobile_handoff': 1}


class MobileSessionChecks(unittest.TestCase):
    def test_validation_rejects_passwords_mismatched_auth_and_untrusted_device_fields(self):
        original = payload()
        self.assertEqual(normalize_mobile_session(original)['username'], 'teste')
        changes = [{'password': 'secret'}, {'verification_code': '003754'}, {'schema': True},
                   {'user_id': '124'}, {'sessionid': ''}, {'mid': 'bad\nheader'},
                   {'device': {**original['device'], 'private_key': 'secret'}},
                   {'authorization': 'Bearer IGT:2:' + base64.b64encode(b'[]').decode()}]
        for change in changes:
            with self.subTest(change=list(change)), self.assertRaises(MobileSessionError):
                normalize_mobile_session({**original, **change})

    def test_server_only_validates_current_user_without_password_login(self):
        routes = []
        def receive(client, endpoint, **kwargs):
            routes.append(endpoint)
            self.assertEqual(endpoint, 'accounts/current_user/')
            self.assertIsNone(client.password)
            return {'user': {'pk': '123', 'username': 'teste'}, 'status': 'ok'}
        with patch.object(core, 'mobile_request', side_effect=receive):
            result = validate_mobile_session(normalize_mobile_session(payload()))
        self.assertEqual(routes, ['accounts/current_user/'])
        self.assertEqual(result['authorization_data']['ds_user_id'], '123')
        self.assertEqual(result['uuids']['android_device_id'], payload()['device']['android'])
        self.assertNotIn('private_key', result.get('usdid', {}))
        self.assertNotIn('password', result)

    def test_import_preserves_session_id_and_delete_account_clears_both_server_link_and_file(self):
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory, mobile_validator=settings)
            try:
                sid = service.data['sessions'][0]['id']
                result = service.mutate('POST', ['sessions', sid, 'import'], payload())
                self.assertEqual(result['execution_mode'], 'server')
                self.assertEqual(service.session(sid)['id'], sid)
                self.assertTrue(service.session(sid)['authenticated'])
                account = service.data['accounts'][0]
                path = Path(directory) / 'accounts' / account['id'] / 'session.json'
                self.assertTrue(path.is_file())
                self.assertNotIn('synthetic-session', json.dumps(service.snapshot()))
                service.mutate('DELETE', ['accounts', account['id']], {})
                self.assertEqual(service.session(sid)['id'], sid)
                self.assertFalse(service.session(sid)['authenticated'])
                self.assertFalse(path.exists())
            finally: service.close()

    def test_refused_handoff_keeps_previous_session_and_never_logs_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory, mobile_validator=settings)
            try:
                sid = service.data['sessions'][0]['id']
                service.import_mobile_session(sid, payload())
                old = dict(service.session(sid))
                account = service.data['accounts'][0]
                path = Path(directory) / 'accounts' / account['id'] / 'session.json'
                before = path.read_bytes()
                def refused(*args): raise RuntimeError('synthetic-session secret-provider-body')
                service.mobile_validator = refused
                with self.assertRaises(ApiError) as caught:
                    service.import_mobile_session(sid, payload())
                self.assertNotIn('synthetic-session', str(caught.exception))
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(service.session(sid), old)
                self.assertFalse(service.mobile_imports)
            finally: service.close()

    def test_import_lock_prevents_parallel_delete_proxy_edit_and_worker_start(self):
        entered, release = threading.Event(), threading.Event()
        def waiting(data, proxy):
            entered.set(); release.wait(3); return settings(data)
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory, mobile_validator=waiting)
            sid = service.data['sessions'][0]['id']
            errors = []
            def run():
                try: service.import_mobile_session(sid, payload())
                except Exception as error: errors.append(error)
            thread = threading.Thread(target=run); thread.start()
            self.assertTrue(entered.wait(2))
            try:
                for method, route, data in [('DELETE', ['sessions', sid], {}), ('POST', ['proxy'], {'enabled': False}),
                                            ('POST', ['sessions', sid, 'login'], {'username': 'another', 'password': 'synthetic'})]:
                    with self.assertRaises(ApiError): service.mutate(method, route, data)
            finally:
                release.set(); thread.join(4); service.close()
            self.assertFalse(errors)

    def test_saved_mobile_session_is_reused_by_worker_without_a_new_login(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            write_json(Path(directory) / 'session.json', settings(payload()))
            with patch.object(core, 'validate_action_session') as validate, patch.object(worker, 'OUTPUT', output):
                worker.run({'directory': directory, 'mode': 'login', 'username': 'teste', 'password': '', 'proxy': ''})
            validate.assert_called_once()
            events = [json.loads(row) for row in output.getvalue().splitlines()]
            self.assertEqual(events[-1], {'type': 'done', 'ok': True, 'authenticated': True})

    def test_session_is_encrypted_with_existing_server_storage_key(self):
        from cryptography.fernet import Fernet
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'RAMOS_ENCRYPTION_KEY': Fernet.generate_key().decode()}):
            service = RamosService(directory, mobile_validator=settings)
            try:
                service.import_mobile_session(service.data['sessions'][0]['id'], payload())
                path = next((Path(directory) / 'accounts').glob('*/session.json'))
                self.assertNotIn(b'synthetic-session', path.read_bytes())
                self.assertEqual(read_json(path)['_mobile_handoff'], 1)
            finally: service.close()


if __name__ == '__main__': unittest.main()
