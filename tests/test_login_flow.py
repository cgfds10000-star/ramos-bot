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
        self.bloks_two_step_verification_verify_code = Mock(side_effect=lambda *args, **kwargs: {'accepted': self.accept_code})

    def login(self, username, password):
        self.username = username
        self.bloks_caa_login()
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
        self.client.two_factor_method = 'auto'
        self.console = patch.object(core, 'CONSOLE', Mock())
        self.console.start()
        self.addCleanup(self.console.stop)

    def bridge(self):
        return worker.ask_code('two_factor', 'Informe o código 2FA solicitado pelo Instagram.')

    def test_received_context_opens_web_prompt_for_totp_sms_and_backup(self):
        for code, sms, method in [('003754', False, 'totp'), ('003754', True, 'sms'), ('00123456', False, 'backup_codes')]:
            with self.subTest(method=method):
                self.client.authenticator_state = 'nao_solicitado'
                self.client.authorization_data = {}
                self.client.caa_last_outcome = {**self.client.outcome, 'sms_two_factor_on': sms, 'totp_two_factor_on': not sms}
                self.client.bloks_two_step_verification_verify_code.reset_mock()
                output = io.StringIO()
                error = TwoFactorRequired('Verification required')
                with patch.object(sys, 'stdin', io.StringIO(json.dumps({'code': code}) + '\n')), patch.object(worker, 'OUTPUT', output), patch.object(core, 'read_two_factor_code', side_effect=self.bridge) as prompt, patch.object(core.getpass, 'getpass', side_effect=AssertionError('Terminal prompt used')):
                    self.assertTrue(core.continue_two_factor(self.client, error))
                prompt.assert_called_once()
                event = json.loads(output.getvalue())
                self.assertEqual((event['type'], event['kind']), ('input_required', 'two_factor'))
                self.client.bloks_two_step_verification_select_method.assert_called_with('synthetic-context', selected_method=method)
                self.client.bloks_two_step_verification_verify_code.assert_called_once_with('synthetic-context', code, challenge=method)
                self.assertNotIn(code, output.getvalue())
                self.assertEqual(self.client.authenticator_state, 'autenticado')
                with patch.object(core, 'read_two_factor_code') as repeated_prompt:
                    with self.assertRaises(core.FlowError):
                        core.continue_two_factor(self.client, error)
                repeated_prompt.assert_not_called()

    def test_no_context_never_prompts_or_verifies(self):
        self.client.caa_last_outcome = {}
        with patch.object(core, 'read_two_factor_code') as prompt:
            with self.assertRaises(core.FlowError):
                core.continue_two_factor(self.client, TwoFactorRequired('Verification required'))
        prompt.assert_not_called()
        self.client.bloks_two_step_verification_verify_code.assert_not_called()

    def test_auto_mode_keeps_native_authenticator_context(self):
        self.client.outcome = {'logged_in': False, 'result': TOTP_INFO}
        with patch.object(core, 'read_authenticator_code', return_value='003754') as prompt:
            self.assertTrue(self.client.bloks_caa_login()['logged_in'])
        prompt.assert_called_once()
        self.assertEqual(self.client.sent[0][1]['verification_code'], '003754')
        self.client.bloks_two_step_verification_verify_code.assert_not_called()

    def test_profile_code_entry_continues_to_the_existing_web_code_handler(self):
        response = {core.PROFILE_CODE: 'synthetic-profile-code-context'}
        with patch.object(FakeBloksInstagram, 'bloks_ap_two_step_verification_entrypoint', return_value=response, create=True), patch.object(self.client, 'bloks_extract_context_data', side_effect=lambda result, app: result.get(app, ''), create=True), patch.object(core, 'read_two_factor_code') as prompt:
            self.assertEqual(self.client.bloks_ap_two_step_verification_entrypoint('synthetic-entry-context'), response)
        prompt.assert_not_called()

    def test_failed_verification_is_not_resent(self):
        self.client.caa_last_outcome = self.client.outcome
        self.client.accept_code = False
        error = TwoFactorRequired('Verification required')
        with patch.object(core, 'read_two_factor_code', return_value='003754') as prompt:
            with self.assertRaises(TwoFactorRequired):
                core.continue_two_factor(self.client, error)
            with self.assertRaises(core.FlowError):
                core.continue_two_factor(self.client, error)
        prompt.assert_called_once()
        self.client.bloks_two_step_verification_verify_code.assert_called_once()
        self.assertFalse(self.client.user_id)

    def test_worker_login_requests_web_input_and_validates_the_same_session(self):
        output = io.StringIO()
        saved_core = {name: getattr(core, name) for name in ['CONSOLE', 'read_json', 'save_private_json', 'read_authenticator_code', 'read_two_factor_code', 'read_verification_code']}
        with tempfile.TemporaryDirectory() as directory, patch.multiple(core, **saved_core), patch.object(core, 'configure_client', return_value=self.client), patch.object(core, 'load_client_class'), patch.object(core, 'save_client'), patch.object(core, 'safe_diagnostic', return_value={}), patch.object(core, 'validate_action_session') as validate, patch.object(worker, 'write_json'), patch.object(worker, 'apply_instagram_proxy'), patch.object(core.getpass, 'getpass', side_effect=AssertionError('Terminal prompt used')), patch.object(sys, 'stdin', io.StringIO('{"code":"003754"}\n')), patch.object(worker, 'OUTPUT', output):
            worker.run({'directory': directory, 'username': 'teste', 'password': 'synthetic-password', 'mode': 'login'})
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([event['kind'] for event in events if event['type'] == 'input_required'], ['two_factor'])
        self.assertEqual(events[-1], {'type': 'done', 'ok': True, 'authenticated': True})
        self.assertEqual(self.client.caa_calls, 1)
        self.assertEqual(self.client.legacy_calls, 0)
        self.client.bloks_two_step_verification_verify_code.assert_called_once_with('synthetic-context', '003754', challenge='totp')
        validate.assert_called_once_with(self.client, 'teste')
        self.assertNotIn('003754', output.getvalue())
        self.assertNotIn('synthetic-password', output.getvalue())


class LoginContinuationChecks(unittest.TestCase):
    def setUp(self):
        with patch('instagrapi.Client', FakeInstagram):
            self.client = core.load_client_class()()
        self.console = patch.object(core, 'CONSOLE', Mock())
        self.console.start()
        self.addCleanup(self.console.stop)

    def code_bridge(self):
        return worker.ask_code('authenticator', 'Informe os 6 dígitos do aplicativo autenticador.')

    def test_native_caa_context_opens_web_2fa_and_sends_the_code_once(self):
        output = io.StringIO()
        with patch.object(sys, 'stdin', io.StringIO('{"code":"003754"}\n')), patch.object(worker, 'OUTPUT', output), patch.object(core, 'read_authenticator_code', side_effect=self.code_bridge) as prompt:
            result = self.client.bloks_caa_login()
        self.assertTrue(result['logged_in'])
        self.assertEqual(self.client.authenticator_state, 'autenticado')
        self.assertEqual(self.client.legacy_calls, 0)
        prompt.assert_called_once()
        event = json.loads(output.getvalue())
        self.assertEqual((event['type'], event['kind']), ('input_required', 'authenticator'))
        self.assertEqual(len(self.client.sent), 1)
        endpoint, fields = self.client.sent[0]
        self.assertEqual(endpoint, 'accounts/two_factor_login/')
        self.assertEqual(fields['verification_code'], '003754')
        self.assertEqual(fields['verification_method'], '3')
        self.assertNotIn('003754', output.getvalue())
        self.assertNotIn('003754', json.dumps(self.client.login_trace))

    def test_server_requested_continuation_reaches_2fa_without_a_second_login(self):
        self.client.caa_last_outcome = {'logged_in': False, 'result': {'action': 'CAA_LOGIN_FALLBACK:fallback_triggered'}}
        with patch.object(core, 'read_authenticator_code', return_value='003754') as prompt:
            with self.assertRaises(TwoFactorRequired) as caught:
                self.client.login_legacy()
            self.assertTrue(core.continue_two_factor(self.client, caught.exception))
        self.assertEqual(self.client.legacy_calls, 1)
        prompt.assert_called_once()
        with self.assertRaises(core.FlowError):
            self.client.login_legacy()
        with self.assertRaises(core.FlowError):
            self.client.bloks_caa_login()
        self.assertEqual(self.client.legacy_calls, 1)
        self.assertEqual(self.client.caa_calls, 0)

    def test_missing_context_does_not_request_a_code_or_invent_a_session(self):
        self.client.outcome = {'logged_in': False, 'result': {}, 'reason': 'CAA login did not return a session'}
        with patch.object(core, 'read_authenticator_code') as prompt:
            result = self.client.bloks_caa_login()
            with self.assertRaises(core.FlowError):
                self.client.login_legacy()
        prompt.assert_not_called()
        self.assertFalse(result['logged_in'])
        self.assertEqual(self.client.legacy_calls, 0)
        self.assertFalse(self.client.user_id)

    def test_unconfirmed_code_does_not_create_a_session_or_repeat_verification(self):
        self.client.accept_code = False
        with patch.object(core, 'read_authenticator_code', return_value='003754') as prompt:
            with self.assertRaises(core.FlowError):
                self.client.bloks_caa_login()
            with self.assertRaises(core.FlowError):
                self.client.login_legacy()
        prompt.assert_called_once()
        self.assertEqual(len(self.client.sent), 1)
        self.assertFalse(self.client.user_id)


if __name__ == '__main__':
    unittest.main()
