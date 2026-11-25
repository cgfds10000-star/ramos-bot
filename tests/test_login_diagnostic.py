import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app
import instagram_login_apk as core
import worker


class LoginDiagnosticChecks(unittest.TestCase):
    def test_private_summary_does_not_copy_credentials_or_response_bodies(self):
        raw = {
            'excecao': 'KeyError', 'erro': 'needs_upgrade', 'http': 400,
            'password': 'synthetic-password', 'username': 'synthetic-user',
            'authorization': 'synthetic-auth', 'response': {'token': 'synthetic-token'},
            'origem_erro': [{'arquivo': 'auth.py', 'funcao': 'login', 'linha': 123,
                             'source': 'synthetic-password'}],
            'etapas': [{'etapa': 'caa_login', 'http': 200, 'body': 'synthetic-token'}],
        }
        summary = app.safe_login_summary(raw)
        self.assertEqual(summary['excecao'], 'KeyError')
        self.assertEqual(summary['erro'], 'needs_upgrade')
        self.assertEqual(summary['etapas'], [{'etapa': 'caa_login', 'http': 200}])
        self.assertNotIn('synthetic', json.dumps(summary))
        self.assertEqual(app.safe_login_summary({'erro': 'ghp_synthetic'}), {'erro': 'oculto'})

    def test_stored_diagnostic_failure_cannot_prevent_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'accounts' / 'synthetic' / 'diagnostic.json'
            path.parent.mkdir(parents=True)
            path.write_text('invalid')
            with patch.object(app, 'storage_read', side_effect=RuntimeError('invalid encryption')), patch.object(app, 'log_login_diagnostic') as log:
                app.log_saved_login_diagnostics(Path(directory))
            log.assert_not_called()

    def test_failed_login_preserves_exception_and_does_not_publish_its_message(self):
        client = SimpleNamespace(
            user_id=None, authorization='', sessionid='', device_settings={},
            last_json={'error_type': 'unexpected_response'},
            last_response=SimpleNamespace(status_code=400), login_trace=[],
            login=Mock(side_effect=KeyError('synthetic-password')),
        )
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            for name in ('CONSOLE', 'read_json', 'save_private_json', 'read_authenticator_code', 'read_verification_code'):
                stack.enter_context(patch.object(core, name))
            stack.enter_context(patch.object(core, 'configure_client', return_value=client))
            stack.enter_context(patch.object(core, 'load_client_class', return_value=object))
            stack.enter_context(patch.object(core, 'save_client'))
            stack.enter_context(patch.object(worker, 'write_json'))
            stack.enter_context(patch.object(worker, 'OUTPUT', output))
            worker.run({'directory': directory, 'mode': 'login', 'username': 'teste', 'password': 'synthetic-password'})
            saved = core.save_private_json.call_args.args[1]
            self.assertEqual(saved['excecao'], 'KeyError')
            self.assertEqual(saved['http'], 400)
            self.assertTrue(saved['origem_erro'])
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        diagnostic = next(event['data'] for event in events if event['type'] == 'diagnostic')
        self.assertEqual(diagnostic['excecao'], 'KeyError')
        self.assertFalse(events[-1]['ok'])
        self.assertIn('KeyError, HTTP 400', output.getvalue())
        self.assertNotIn('synthetic-password', output.getvalue())

    def test_error_class_is_retained_if_diagnostic_builder_fails(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(core, 'configure_client', side_effect=ValueError('synthetic-password')), patch.object(core, 'load_client_class', return_value=object), patch.object(core, 'CONSOLE'), patch.object(core, 'read_json'), patch.object(core, 'save_private_json'), patch.object(core, 'read_authenticator_code'), patch.object(core, 'read_verification_code'), patch.object(worker, 'OUTPUT', io.StringIO()) as output:
            worker.run({'directory': directory, 'mode': 'login', 'username': 'teste', 'password': 'synthetic-password'})
            events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events[0]['data']['excecao'], 'ValueError')
        self.assertFalse(events[-1]['ok'])
        self.assertNotIn('synthetic-password', output.getvalue())


if __name__ == '__main__':
    unittest.main()
