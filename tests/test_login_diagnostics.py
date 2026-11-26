import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from requests import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from login_diagnostics import runtime_diagnostic


class LoginDiagnosticChecks(unittest.TestCase):
    def client(self, body=b'{"message":"Please wait a few minutes before you try again."}'):
        response = Response()
        response.status_code = 401
        response.url = 'https://i.instagram.com/api/v1/launcher/sync/?sensitive=synthetic-secret'
        response._content = body
        response.headers = {'Retry-After': '60', 'ig-set-password-encryption-key-id': 'synthetic-key-id',
                            'ig-set-password-encryption-pub-key': 'synthetic-key', 'Set-Cookie': 'sessionid=synthetic-secret'}
        return SimpleNamespace(last_response=response, private_transport='curl', public_transport='requests',
                               private=SimpleNamespace(proxies={}, trust_env=False))

    def test_identifies_password_key_refusal_without_exporting_secrets(self):
        value = runtime_diagnostic(self.client())
        self.assertEqual(value['ultima_rota'], 'chave_criptografia_senha')
        self.assertEqual(value['mensagem_provedor'], 'aguardar_alguns_minutos')
        self.assertEqual(value['transporte_privado'], 'curl')
        self.assertFalse(value['proxy_configurado'])
        self.assertEqual(value['aguardar_segundos'], 60)
        self.assertTrue(value['resposta_chave_senha']['chave_publica_recebida'])
        for secret in ('synthetic-secret', 'synthetic-key', 'i.instagram.com', 'Please wait'):
            self.assertNotIn(secret, json.dumps(value))

    def test_html_and_unknown_provider_message_are_not_exported(self):
        for body, expected in [(b'<html>synthetic-secret</html>', 'resposta_sem_json'),
                               (b'{"message":"private synthetic-secret"}', 'outra_mensagem')]:
            with self.subTest(expected=expected):
                value = runtime_diagnostic(self.client(body))
                self.assertEqual(value['mensagem_provedor'], expected)
                self.assertNotIn('synthetic-secret', json.dumps(value))

    def test_absent_response_is_supported(self):
        value = runtime_diagnostic(SimpleNamespace())
        self.assertEqual(value['ultima_rota'], 'outra_rota')
        self.assertEqual(value['resposta_chave_senha'], {'identificador_recebido': False, 'chave_publica_recebida': False})
        self.assertEqual(value['transporte_privado'], 'indisponivel')


if __name__ == '__main__':
    unittest.main()
