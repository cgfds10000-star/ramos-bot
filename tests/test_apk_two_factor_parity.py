"""Regressoes do fluxo do APK, usando os payloads reais da dependencia.

Somente o limite HTTP e a resposta inicial CAA sao substituidos. Nenhuma
credencial real ou requisicao ao Instagram e utilizada.
"""
import base64
from contextlib import ExitStack
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from requests import Response
from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import instagram_login_apk as core
import worker


NATIVE = {'two_factor_info': {'two_factor_identifier': 'synthetic-id',
          'totp_two_factor_on': True, 'username': 'teste'}}


def authorization():
    return 'Bearer IGT:2:' + base64.b64encode(json.dumps({
        'ds_user_id': '123', 'sessionid': 'synthetic-session',
    }).encode()).decode()


def embedded_session():
    raw = {'login_response': json.dumps({'status': 'ok', 'logged_in_user': {'pk': '123'}}),
           'headers': json.dumps({'IG-Set-Authorization': authorization()}), 'cookies': ''}
    return {'layout': {'bloks_payload': {'action': '(action ' + json.dumps(json.dumps(raw)) + ')'}}}


class ApkTwoFactorParity(unittest.TestCase):
    def setUp(self):
        self.client = core.load_client_class()(override_app_version=True,
            session_retry_total=0, public_request_retries_count=0)
        self.client.username = 'teste'
        self.client.request_timeout = 0
        self.sent = []

    def run_flow(self, initial, respond, *, code='003754', error=False, ap=False):
        output = io.StringIO()
        def send(endpoint, *args, **kwargs):
            self.sent.append((endpoint, kwargs))
            result = respond(endpoint, kwargs)
            response = Response()
            response.status_code = 200
            response._content = json.dumps(result).encode()
            response.headers['ig-set-authorization'] = authorization()
            self.client.last_response = response
            self.client.last_json = result
            return result
        def caa(*args, **kwargs):
            self.client.last_json = initial
            if error:
                raise TwoFactorRequired('Verification required', **initial)
            if ap:
                resolved = self.client.bloks_caa_resolve_two_step_verification(initial)
                return {'logged_in': resolved['logged_in'], 'result': initial, 'two_step': resolved}
            return {'logged_in': False, 'result': initial}
        with ExitStack() as stack:
            stack.enter_context(patch.object(Client, 'bloks_caa_login', side_effect=caa))
            stack.enter_context(patch.object(self.client, '_send_private_request', side_effect=send))
            stack.enter_context(patch.object(self.client, 'login_flow', return_value=True))
            stack.enter_context(patch.object(self.client.private, 'send', side_effect=AssertionError('Unexpected live network')))
            stack.enter_context(patch.object(sys, 'stdin', io.StringIO(json.dumps({'code': code}) + '\n')))
            stack.enter_context(patch.object(worker, 'OUTPUT', output))
            stack.enter_context(worker.web_input(core))
            try:
                accepted = self.client.login('teste', 'synthetic-password')
            except TwoFactorRequired as exc:
                accepted = core.continue_two_factor(self.client, exc)
        return accepted, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_native_identifier_works_in_auto_for_caa_and_http_error(self):
        for error in (False, True):
            with self.subTest(error=error):
                self.setUp()
                accepted, events = self.run_flow(NATIVE,
                    lambda *_: {'status': 'ok', 'logged_in_user': {'pk': 123}}, error=error)
                self.assertTrue(accepted)
                self.assertEqual(str(self.client.user_id), '123')
                self.assertEqual([e['kind'] for e in events if e['type'] == 'input_required'], ['authenticator'])
                self.assertEqual(len(self.sent), 1)
                endpoint, kwargs = self.sent[0]
                self.assertEqual(endpoint, 'accounts/two_factor_login/')
                self.assertEqual(kwargs['data']['verification_code'], '003754')
                self.assertEqual(kwargs['data']['verification_method'], '3')
                with self.assertRaises(core.FlowError):
                    core.continue_two_factor(self.client, TwoFactorRequired('Verification required'))
                self.assertEqual(len(self.sent), 1)

    def test_rotated_context_is_used_for_every_step_and_verify(self):
        expected = [core.TOTP_ENTRY, core.TOTP_PICKER, core.TOTP_SELECT, core.TOTP_VERIFY]
        def respond(endpoint, kwargs):
            index = len(self.sent) - 1
            self.assertIn(expected[index], endpoint)
            params = json.loads(kwargs['data']['params'])
            self.assertEqual(params['server_params']['two_step_verification_context'], f'context-{index}')
            if index == 3:
                self.assertEqual(params['client_input_params']['code'], '003754')
                return embedded_session()
            return {'two_step_verification_context': f'context-{index + 1}'}
        accepted, _ = self.run_flow({'two_step_verification_context': 'context-0'}, respond)
        self.assertTrue(accepted)
        self.assertEqual(len(self.sent), 4)

    def test_backup_screen_remains_allowed_and_uses_updated_context(self):
        expected = [core.TOTP_ENTRY, core.TOTP_PICKER, core.TOTP_SELECT, core.BACKUP_SCREEN, core.TOTP_VERIFY]
        def respond(endpoint, kwargs):
            index = len(self.sent) - 1
            self.assertIn(expected[index], endpoint)
            params = json.loads(kwargs['data']['params'])
            self.assertEqual(params['server_params']['two_step_verification_context'], f'context-{index}')
            if index == 4:
                self.assertEqual(params['client_input_params']['code'], '00123456')
                self.assertEqual(params['server_params']['challenge'], 'backup_codes')
                return embedded_session()
            return {'two_step_verification_context': f'context-{index + 1}'}
        accepted, _ = self.run_flow({'two_step_verification_context': 'context-0'}, respond, code='00123456')
        self.assertTrue(accepted)
        self.assertEqual(len(self.sent), 5)

    def test_session_returned_by_preparation_stops_before_code_submission(self):
        accepted, _ = self.run_flow({'two_step_verification_context': 'initial'}, lambda *_: embedded_session())
        self.assertTrue(accepted)
        self.assertEqual(len(self.sent), 1)

    def test_ap_entry_can_continue_to_bloks_or_native_without_restarting_login(self):
        for native in (False, True):
            with self.subTest(native=native):
                self.setUp()
                initial = {'app_id': core.PROFILE_ENTRY, 'params': {'context_data': 'ap-context'}}
                def respond(endpoint, kwargs):
                    if core.PROFILE_ENTRY in endpoint:
                        return NATIVE if native else {'two_step_verification_context': 'totp-context'}
                    if endpoint == 'accounts/two_factor_login/':
                        return {'status': 'ok', 'logged_in_user': {'pk': 123}}
                    if core.TOTP_VERIFY in endpoint:
                        return embedded_session()
                    return {}
                accepted, events = self.run_flow(initial, respond, ap=True)
                self.assertTrue(accepted)
                self.assertEqual(len(self.sent), 2 if native else 5)
                self.assertEqual(len([e for e in events if e['type'] == 'input_required']), 1)

    def test_timeout_consumes_attempt_without_requesting_another_code(self):
        def respond(*_):
            raise TimeoutError('synthetic transport timeout')
        with self.assertRaises(TimeoutError):
            self.run_flow({'two_step_verification_context': 'initial'}, respond)
        with patch.object(core, 'input', create=True) as prompt, self.assertRaises(core.FlowError):
            core.continue_two_factor(self.client, TwoFactorRequired('Verification required'))
        prompt.assert_not_called()
        self.assertEqual(len(self.sent), 1)

    def test_context_parser_reads_legacy_map_after_argument_list(self):
        action = '"' + core.TOTP_ENTRY + '" (args (GetArg 0)) (f4i (dkc "two_step_verification_context") (dkc "synthetic-context"))'
        self.assertEqual(self.client._extract_two_step_verification_context({'action': action}), 'synthetic-context')

    def test_context_parser_reads_alias_map_and_nested_json(self):
        action = '"' + core.TOTP_VERIFY + '" (map (list "server_params") (list (map (list "two_step_verification_context") (list "synthetic-context"))))'
        data = {'payload': json.dumps({'action': action})}
        self.assertEqual(self.client._extract_two_step_verification_context(data), 'synthetic-context')

    def test_context_from_previous_caa_response_does_not_conflict_with_current(self):
        self.client.last_json = {'two_step_verification_context': 'fresh'}
        self.client.caa_last_outcome = {'two_step_verification_context': 'old'}
        with patch.object(core, 'input', create=True, return_value='003754'), patch.object(self.client, '_login_with_bloks_two_factor', return_value=True) as complete:
            self.assertTrue(core.continue_two_factor(self.client, TwoFactorRequired('Verification required')))
        self.assertEqual(complete.call_args.args[1]['two_step_verification_context'], 'fresh')


if __name__ == '__main__':
    unittest.main()
