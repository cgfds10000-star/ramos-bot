import io
import json
from pathlib import Path
import sys
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
