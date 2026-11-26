import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import instagram_login_apk as core
import worker
from instagrapi.exceptions import TwoFactorRequired
from instagrapi.mixins.auth import LoginMixin


TOTP_INFO = {'two_factor_info': {'two_factor_identifier': 'synthetic-identifier', 'totp_two_factor_on': True, 'username': 'teste'}}


class FakeInstagram:
    _caa_result_action_markers = LoginMixin._caa_result_action_markers

    def __init__(self, *args, **kwargs):
        self.username = 'teste'
        self.authorization_data = {}
        self.phone_id = 'synthetic-phone'
        self.uuid = 'synthetic-guid'
        self.android_device_id = 'synthetic-device'
        self.token = 'synthetic-csrf'
        self.private_requests_count = 0
        self.private = SimpleNamespace(cookies=Mock(), headers={})
        self.last_response = SimpleNamespace(headers={}, status_code=200)
        self.last_json = {}
        self.outcome = {'logged_in': False, 'result': TOTP_INFO, 'reason': 'CAA login did not return a session'}
        self.sent = []
        self.legacy_calls = 0
        self.caa_calls = 0
        self.accept_code = True

    @property
    def user_id(self):
        return self.authorization_data.get('ds_user_id')

    @property
    def authorization(self):
        return 'synthetic-authorization' if self.sessionid else ''

    @property
    def sessionid(self):
        return self.authorization_data.get('sessionid', '')

    def bloks_caa_login(self, *args, **kwargs):
        self.caa_calls += 1
        return self.outcome

    def login_legacy(self, *args, **kwargs):
        self.legacy_calls += 1
        self.last_json = TOTP_INFO
        raise TwoFactorRequired('Verification required')

    def _send_private_request(self, endpoint, *args, **kwargs):
        self.sent.append((endpoint, kwargs['data'].copy()))
        self.last_response.headers = {'ig-set-authorization': 'synthetic-header'}
        self.last_json = {'status': 'ok', **({'logged_in_user': {'pk': 123}} if self.accept_code else {})}
        return self.last_json

    def parse_authorization(self, value):
        return {'ds_user_id': '123', 'sessionid': 'synthetic-session'}


class FakeBloksInstagram(FakeInstagram):
    _find_login_response_value = LoginMixin._find_login_response_value
    _extract_two_step_verification_context = LoginMixin._extract_two_step_verification_context
    _login_response_bool = LoginMixin._login_response_bool
    _normalize_backup_code = LoginMixin._normalize_backup_code
    _looks_like_backup_code = LoginMixin._looks_like_backup_code
    _infer_bloks_two_factor_challenge = LoginMixin._infer_bloks_two_factor_challenge
    _login_with_bloks_two_factor = LoginMixin._login_with_bloks_two_factor
    _exception_context = LoginMixin._exception_context

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcome = {'logged_in': False, 'two_step_verification_context': 'synthetic-context', 'totp_two_factor_on': True}
        self.bloks_two_step_verification_entrypoint = Mock(return_value={})
        self.bloks_two_step_verification_method_picker = Mock(return_value={})
        self.bloks_two_step_verification_select_method = Mock(return_value={})
        self.bloks_two_step_verification_enter_backup_code = Mock(return_value={})
        self.bloks_two_step_verification_enter_totp_code = Mock(return_value={})
        self.bloks_two_step_verification_verify_code = Mock(side_effect=lambda *args, **kwargs: {'accepted': self.accept_code})

    def login(self, username, password):
        self.username = username
        self.bloks_caa_login()
        self.last_json = dict(self.outcome)
        raise TwoFactorRequired('Verification required')

    def bloks_apply_login_response(self, result):
        if not result.get('accepted'):
            return False
        self.authorization_data = {'ds_user_id': '123', 'sessionid': 'synthetic-session'}
        return True


class WebTwoFactorChecks(unittest.TestCase):
    def setUp(self):
        with patch('instagrapi.Client', FakeBloksInstagram):
            self.client = core.load_client_class()()

    def worker_run(self, *, diagnostic=None, **changes):
        output = io.StringIO()
        payload = {'username': 'teste', 'password': 'synthetic-password', 'mode': 'login', 'two_factor_method': 'auto', **changes}
        if payload.get('two_factor_method') is None:
            payload.pop('two_factor_method', None)
        with tempfile.TemporaryDirectory() as directory, patch.object(core, 'configure_client', return_value=self.client), patch.object(core, 'load_client_class'), patch.object(core, 'save_client'), patch.object(core, 'safe_diagnostic', return_value=diagnostic or {}), patch.object(core, 'validate_action_session') as validate, patch.object(worker, 'write_json'), patch.object(worker, 'apply_instagram_proxy'), patch.object(sys, 'stdin', io.StringIO('{"code":"003754"}\n')), patch.object(worker, 'OUTPUT', output):
            worker.run({'directory': directory, **payload})
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        return events, output.getvalue(), validate

    def test_worker_uses_the_same_automatic_two_factor_flow_as_the_script(self):
        self.assertEqual(self.client.two_factor_method, 'auto')
        events, output, validate = self.worker_run()
        self.assertEqual(self.client.two_factor_method, 'auto')
        self.assertEqual([event['kind'] for event in events if event['type'] == 'input_required'], ['two_factor'])
        self.assertEqual(events[-1], {'type': 'done', 'ok': True, 'authenticated': True})
        self.assertEqual(self.client.caa_calls, 1)
        self.assertEqual(self.client.legacy_calls, 0)
        self.client.bloks_two_step_verification_select_method.assert_called_once_with('synthetic-context', selected_method='totp')
        self.client.bloks_two_step_verification_verify_code.assert_called_once_with('synthetic-context', '003754', challenge='totp')
        validate.assert_not_called()
        self.assertNotIn('003754', output)
        self.assertNotIn('synthetic-password', output)

    def test_missing_worker_selection_defaults_to_the_python_automatic_method(self):
        events, _, _ = self.worker_run(two_factor_method=None)
        self.assertEqual(events[-1]['ok'], True)
        self.assertEqual(self.client.two_factor_method, 'auto')

    def test_code_typed_in_login_is_consumed_once_by_the_script_prompt(self):
        for code, method in [('003754', 'totp'), ('00123456', 'backup_codes')]:
            with self.subTest(method=method):
                self.setUp()
                events, output, _ = self.worker_run(verification_code=code)
                self.assertEqual(events[-1], {'type': 'done', 'ok': True, 'authenticated': True})
                self.assertFalse(any(event['type'] == 'input_required' for event in events))
                self.client.bloks_two_step_verification_verify_code.assert_called_once_with('synthetic-context', code, challenge=method)
                self.assertNotIn(code, output)

    def test_rejected_pretyped_code_is_not_reused_or_reported_as_connected(self):
        self.client.accept_code = False
        events, output, _ = self.worker_run(verification_code='003754')
        self.assertEqual(events[-1], {'type': 'done', 'ok': False, 'authenticated': False})
        self.client.bloks_two_step_verification_verify_code.assert_called_once()
        self.assertFalse(any(event['type'] == 'input_required' for event in events))
        self.assertNotIn('003754', output)

    def test_rejected_code_is_not_resent_and_never_connects_the_session(self):
        self.client.accept_code = False
        events, output, validate = self.worker_run()
        self.assertEqual(len([e for e in events if e['type'] == 'input_required']), 1)
        self.client.bloks_two_step_verification_verify_code.assert_called_once()
        self.assertEqual(events[-1], {'type': 'done', 'ok': False, 'authenticated': False})
        validate.assert_not_called()
        self.assertNotIn('003754', output)

    def test_missing_context_does_not_request_a_code_or_report_success(self):
        self.client.outcome = {'logged_in': False}
        events, _, validate = self.worker_run()
        self.assertFalse(any(e['type'] == 'input_required' for e in events))
        self.client.bloks_two_step_verification_verify_code.assert_not_called()
        self.assertEqual(events[-1]['ok'], False)
        validate.assert_not_called()

    def test_worker_reapplies_cli_retry_policy_after_loading_saved_settings(self):
        self.client.device_settings = {'app_version': 'synthetic', 'version_code': '1'}
        self.client.set_settings = Mock()
        self.client.set_retry_config = Mock()
        def check_login(*_):
            self.client.set_settings.assert_called_once()
            self.client.set_retry_config.assert_called_once_with(session_retry_total=0, public_request_retries_count=0)
            self.assertTrue(self.client.override_app_version)
            self.assertEqual(self.client.read_timeout, 30)
            raise TypeError('synthetic failure')

        self.client.login = Mock(side_effect=check_login)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(core, 'configure_client', return_value=self.client), \
                patch.object(core, 'save_client'), \
                patch.object(worker, 'apply_instagram_proxy'), \
                patch.object(worker, 'OUTPUT', output):
            worker.write_json(Path(directory) / 'session.json', {'_local_login_username': 'teste'})
            worker.run({'directory': directory, 'username': 'teste', 'password': 'synthetic-password', 'mode': 'login'})
            diagnostic = worker.read_json(Path(directory) / 'diagnostic.json')
        self.client.login.assert_called_once()
        self.assertEqual(diagnostic['excecao'], 'TypeError')
        self.assertIn('Etapa: login_instagram', output.getvalue())

    def test_worker_reports_401_without_retrying_login_or_requesting_code(self):
        from instagrapi.exceptions import ClientError

        self.client.login = Mock(side_effect=ClientError('private synthetic detail'))
        events, output, _ = self.worker_run(diagnostic={
            'http': 401, 'etapas': [{'http': 401, 'etapa': 'caa_login'}],
        })
        self.client.login.assert_called_once()
        self.assertFalse(any(event['type'] == 'input_required' for event in events))
        self.assertEqual(events[-1], {'type': 'done', 'ok': False, 'authenticated': False})
        self.assertIn('HTTP 401', output)
        self.assertIn('caa_login', output)
        self.assertIn('aplicativo oficial', output)
        self.assertNotIn('private synthetic detail', output)

    def test_worker_reports_safe_exception_type_and_saves_safe_diagnostic(self):
        self.client.device_settings = {'app_version': 'synthetic', 'version_code': '1'}
        self.client.login = Mock(side_effect=TypeError('private synthetic detail'))
        output = io.StringIO()
        written = []
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(core, 'configure_client', return_value=self.client), \
                patch.object(core, 'save_client'), \
                patch.object(worker, 'apply_instagram_proxy'), \
                patch.object(worker, 'write_json', side_effect=lambda path, value: written.append((path, value))), \
                patch.object(worker, 'OUTPUT', output):
            worker.run({'directory': directory, 'username': 'teste', 'password': 'synthetic-password',
                        'mode': 'login', 'two_factor_method': 'auto'})
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        error_message = next(event['message'] for event in events if event['type'] == 'log' and event['tone'] == 'error')
        diagnostic = next(value for path, value in written if Path(path).name == 'diagnostic.json')
        self.assertIn('TypeError', error_message)
        self.assertIn('Etapa: login_instagram', error_message)
        self.assertEqual(diagnostic['excecao'], 'TypeError')
        self.assertEqual(diagnostic['etapa_worker'], 'login_instagram')
        self.assertNotIn('private synthetic detail', error_message)
        self.assertNotIn('private synthetic detail', json.dumps(diagnostic))
        self.assertNotIn('synthetic-password', output.getvalue())

    def test_generic_script_getpass_is_bridged_for_totp_sms_and_backup_codes(self):
        for code, sms, method in [('003754', False, 'totp'), ('003754', True, 'sms'), ('00123456', False, 'backup_codes')]:
            with self.subTest(method=method):
                self.setUp()
                self.client.last_json = {**self.client.outcome, 'sms_two_factor_on': sms, 'totp_two_factor_on': not sms}
                self.client.bloks_two_step_verification_verify_code.reset_mock()
                output = io.StringIO()
                with patch.object(sys, 'stdin', io.StringIO(json.dumps({'code': code}) + '\n')), patch.object(worker, 'OUTPUT', output), worker.web_input(core):
                    self.assertTrue(core.continue_two_factor(self.client, TwoFactorRequired('Verification required')))
                event = json.loads(output.getvalue())
                self.assertEqual((event['type'], event['kind']), ('input_required', 'two_factor'))
                self.client.bloks_two_step_verification_select_method.assert_called_with('synthetic-context', selected_method=method)
                self.client.bloks_two_step_verification_verify_code.assert_called_once_with('synthetic-context', code, challenge=method)
                self.assertNotIn(code, output.getvalue())

    def test_web_adapter_restores_cli_input_on_success_and_failure(self):
        readers = {name: getattr(core, name) for name in ['CONSOLE', 'read_json', 'save_private_json', 'read_authenticator_code', 'read_verification_code']}
        getpass = core.getpass.getpass
        with self.assertRaises(RuntimeError):
            with worker.web_input(core):
                self.assertIsNot(core.getpass.getpass, getpass)
                raise RuntimeError('synthetic failure')
        self.assertIs(core.getpass.getpass, getpass)
        for name, value in readers.items():
            self.assertIs(getattr(core, name), value)


class NativeAuthenticatorChecks(unittest.TestCase):
    def setUp(self):
        with patch('instagrapi.Client', FakeInstagram):
            self.client = core.load_client_class()()
        self.client.two_factor_method = 'autenticador'
        self.client.caa_last_outcome = {'logged_in': False, 'result': TOTP_INFO}

    def test_native_context_requests_code_in_the_page_and_submits_once(self):
        output = io.StringIO()
        with patch.object(sys, 'stdin', io.StringIO('{"code":"003754"}\n')), patch.object(worker, 'OUTPUT', output), worker.web_input(core):
            self.assertTrue(core.continue_two_factor(self.client, TwoFactorRequired('Verification required')))
            with self.assertRaises(core.FlowError):
                core.continue_two_factor(self.client, TwoFactorRequired('Verification required'))
        self.assertEqual(self.client.authenticator_state, 'autenticado')
        self.assertEqual(self.client.legacy_calls, 0)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([e['kind'] for e in events if e['type'] == 'input_required'], ['authenticator'])
        self.assertEqual(len(self.client.sent), 1)
        endpoint, fields = self.client.sent[0]
        self.assertEqual(endpoint, 'accounts/two_factor_login/')
        self.assertEqual(fields['verification_code'], '003754')
        self.assertEqual(fields['verification_method'], '3')
        self.assertNotIn('003754', output.getvalue())
        self.assertNotIn('003754', json.dumps(self.client.login_trace))

    def test_caa_resolver_preserves_the_native_authenticator_route(self):
        with patch.object(core, 'read_authenticator_code', return_value='003754'):
            result = self.client.bloks_caa_resolve_two_step_verification(TOTP_INFO)
        self.assertTrue(result['logged_in'])
        self.assertTrue(self.client.profile_session_completed)
        self.assertEqual(len(self.client.sent), 1)

    def test_script_never_restarts_password_login_through_the_legacy_route(self):
        self.client.caa_last_outcome = {'logged_in': False, 'result': {'action': 'CAA_LOGIN_FALLBACK:fallback_triggered'}}
        with self.assertRaises(core.FlowError):
            self.client.login_legacy()
        self.assertEqual(self.client.legacy_calls, 0)
        self.assertEqual(self.client.caa_calls, 0)

    def test_unconfirmed_code_does_not_create_a_session_or_repeat_verification(self):
        self.client.accept_code = False
        with patch.object(core, 'read_authenticator_code', return_value='003754') as prompt:
            with self.assertRaises(core.FlowError):
                core.continue_two_factor(self.client, TwoFactorRequired('Verification required'))
            with self.assertRaises(core.FlowError):
                core.continue_two_factor(self.client, TwoFactorRequired('Verification required'))
        prompt.assert_called_once()
        self.assertEqual(len(self.client.sent), 1)
        self.assertFalse(self.client.user_id)


# Executa o worker e o núcleo reais, substituindo somente as respostas externas.
# O worker web deve usar a continuação genérica automática, igual ao CLI.
WORKER_HARNESS = r"""
import json, time
from contextlib import ExitStack
from unittest.mock import patch
from instagrapi import Client
import worker
import instagram_login_apk as core

def apply_response(client, result):
    if result.get('synthetic_accepted'):
        client.authorization_data = {'ds_user_id': '123', 'sessionid': 'synthetic-session'}
        return True
    return False

def verify(client, context, code, *, challenge):
    assert context == 'synthetic-context'
    assert challenge == 'totp'
    time.sleep(.15)
    return {'synthetic_accepted': code == '003754'}

def validate(client, owner):
    raise AssertionError('Unexpected post-login session validation request')

with ExitStack() as stack:
    def caa(client, *args, **kwargs):
        result = {'logged_in': False, 'two_step_verification_context': 'synthetic-context', 'totp_two_factor_on': True}
        client.last_json = result
        return result
    stack.enter_context(patch.object(Client, 'bloks_caa_login', new=caa))
    for name in ['bloks_two_step_verification_entrypoint', 'bloks_two_step_verification_method_picker', 'bloks_two_step_verification_select_method', 'bloks_two_step_verification_enter_totp_code']:
        stack.enter_context(patch.object(Client, name, return_value={}))
    stack.enter_context(patch.object(Client, 'bloks_two_step_verification_verify_code', verify))
    stack.enter_context(patch.object(Client, 'bloks_apply_login_response', apply_response))
    stack.enter_context(patch.object(Client, 'private_request', side_effect=AssertionError('Unexpected network request')))
    stack.enter_context(patch.object(core, 'validate_action_session', validate))
    worker.main()
"""


class WorkerProtocolChecks(unittest.TestCase):
    def test_grouped_secret_reaches_the_same_script_and_is_generated_at_submit(self):
        from app import RamosService, ApiError
        import time
        public_secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        formatted = ' '.join(public_secret[i:i+4] for i in range(0, len(public_secret), 4))
        harness = WORKER_HARNESS.replace("code == '003754'", "code == '050471'")
        harness = harness.replace('with ExitStack() as stack:', 'with ExitStack() as stack:\n    stack.enter_context(patch.object(core.time, "time", return_value=1111111111))')
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory, worker_command=[sys.executable, '-u', '-c', harness], dependency_check=lambda: True)
            try:
                sid = service.data['sessions'][0]['id']
                service.start(sid, {'mode': 'login', 'username': 'teste', 'password': 'synthetic-password'})
                deadline = time.monotonic() + 8
                while not service.session(sid)['challenge'] and time.monotonic() < deadline:
                    time.sleep(.01)
                challenge = service.session(sid)['challenge']
                self.assertIsNotNone(challenge)
                body = {'challenge_id': challenge['id'], 'secret': formatted}
                service.submit_input(sid, body)
                with self.assertRaises(ApiError):
                    service.submit_input(sid, body)
                while sid in service.jobs and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertNotIn(sid, service.jobs)
                self.assertTrue(service.session(sid)['authenticated'])
                for secret in (formatted, public_secret, '050471', 'synthetic-password'):
                    self.assertNotIn(secret, json.dumps(service.snapshot()))
                    for path in Path(directory).rglob('*.json'):
                        self.assertNotIn(secret, path.read_text())
            finally:
                service.close()

    def test_typed_code_reaches_real_worker_without_terminal_input_or_secret_in_logs(self):
        from app import RamosService, ApiError
        import time
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory, worker_command=[sys.executable, '-u', '-c', WORKER_HARNESS], dependency_check=lambda: True)
            try:
                sid = service.data['sessions'][0]['id']
                service.start(sid, {'mode': 'login', 'username': 'teste', 'password': 'synthetic-password', 'verification_code': '003754'})
                deadline = time.monotonic() + 8
                while sid in service.jobs and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertNotIn(sid, service.jobs)
                self.assertTrue(service.session(sid)['authenticated'])
                self.assertIsNone(service.session(sid)['challenge'])
                public = json.dumps(service.snapshot())
                self.assertNotIn('003754', public)
                self.assertNotIn('synthetic-password', public)
                for path in Path(directory).rglob('*.json'):
                    self.assertNotIn('003754', path.read_text())
                for invalid in ('1234567', 'secret', 123456):
                    with self.assertRaises(ApiError):
                        service.start(sid, {'mode': 'login', 'username': 'teste', 'password': 'synthetic-password', 'verification_code': invalid})
                self.assertNotIn(sid, service.jobs)
            finally:
                service.close()

    def test_real_worker_waits_for_user_input_and_never_repeats_password_or_code(self):
        from app import RamosService, ApiError
        import time
        with tempfile.TemporaryDirectory() as directory:
            service = RamosService(directory, worker_command=[sys.executable, '-u', '-c', WORKER_HARNESS], dependency_check=lambda: True)
            try:
                sid = service.data['sessions'][0]['id']
                service.start(sid, {'mode': 'login', 'username': 'teste', 'password': 'synthetic-password'})
                deadline = time.monotonic() + 8
                while not service.session(sid)['challenge'] and time.monotonic() < deadline:
                    time.sleep(.01)
                challenge = service.session(sid)['challenge']
                self.assertIsNotNone(challenge)
                self.assertEqual(challenge['kind'], 'two_factor')
                self.assertEqual(service.session(sid)['status'], 'awaiting_input')
                self.assertFalse(service.session(sid)['authenticated'])
                time.sleep(.1)
                self.assertEqual(service.session(sid)['challenge']['id'], challenge['id'])
                body = {'challenge_id': challenge['id'], 'code': '003754'}
                service.submit_input(sid, body)
                with self.assertRaises(ApiError):
                    service.submit_input(sid, body)
                while sid in service.jobs and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertNotIn(sid, service.jobs)
                self.assertTrue(service.session(sid)['authenticated'])
                public = json.dumps(service.snapshot())
                self.assertNotIn('003754', public)
                self.assertNotIn('synthetic-password', public)
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
