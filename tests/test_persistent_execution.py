import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import ApiError, RamosService, make_server
from storage import read_json, write_json, PREFIX
from cryptography.fernet import Fernet

FAKE_WORKER = '''import json,sys
p=json.loads(sys.stdin.readline())
assert not p.get('password')
if p['mode']=='command':
 print(json.dumps({'type':'result','data':{'estado':'seguindo','id':'28'}}),flush=True)
print(json.dumps({'type':'done','ok':True,'authenticated':True}),flush=True)
'''
TOKEN = 'vzs_synthetic_persistence'


class QueueApi:
    def __init__(self, queue):
        self.queue = list(queue)
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, token, **kwargs):
        if token != TOKEN:
            raise ValueError('Token sintético incorreto')
        return self

    def close(self): pass

    def request(self, endpoint, fields, **kwargs):
        with self.lock:
            self.calls.append((endpoint, fields))
            if endpoint == 'task':
                item = self.queue.pop(0) if self.queue else {'state': 'empty'}
                if isinstance(item, Exception): raise item
                return item
            return {'success': True, 'completion_id': 84}


def task(number):
    return {'state': 'available', 'task': {'id': number, 'type': 'follow', 'instagram_username': 'perfil_teste'}}


class PersistentExecutionChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.encryption = patch.dict(os.environ, {'RAMOS_ENCRYPTION_KEY': Fernet.generate_key().decode()})
        self.encryption.start()
        self.services = []

    def tearDown(self):
        for service in self.services: service.close()
        self.encryption.stop()
        self.temp.cleanup()

    def service(self, api, existing=False):
        service = RamosService(self.temp.name, worker_command=[sys.executable, '-u', '-c', FAKE_WORKER], dependency_check=lambda: True, viralize_api=api)
        self.services.append(service)
        service.queue_poll_seconds = .05
        service.pacing_seconds = lambda *_: 0
        sid = service.data['sessions'][0]['id']
        if not existing:
            account = service.add_account('minhaconta')
            service.session(sid).update(account_id=account['id'], authenticated=True, status='connected')
            write_json(service.directory / 'accounts' / account['id'] / 'session.json', {'synthetic': True})
            service.save()
        return service, sid

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.01)
        self.fail('Executor sintético não atingiu o estado esperado.')

    def start(self, service, sid, limit=1):
        service.mutate('POST', ['viralize', 'start'], {'session_ids': [sid], 'token': TOKEN, 'limit': limit})

    def test_empty_queue_waits_and_executes_new_work_after_browser_disconnect(self):
        api = QueueApi([{'state': 'empty'}, task(1)])
        service, sid = self.service(api)
        server = make_server(service, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            connection.request('POST', '/api/viralize/start', json.dumps({'session_ids': [sid], 'token': TOKEN, 'limit': 1}), {'Content-Type': 'application/json', 'X-Ramos-CSRF': service.csrf})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
            # Nenhum browser permanece conectado ou enviando comandos.
            self.wait(lambda: service.viralize_runs[sid].get('completed_total') == 1)
            self.wait(lambda: service.viralize_runs[sid]['status'] == 'waiting')
            self.assertEqual(len([call for call in api.calls if call[0] == 'task/confirm-analysis']), 1)
            snapshot = service.snapshot()
            self.assertEqual(snapshot['viralize'][sid]['completed_total'], 1)
            self.assertNotIn(TOKEN, json.dumps(snapshot))
            service.cancel(sid)
        finally:
            connection.close(); server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_server_runs_multiple_rounds_without_a_second_start_request(self):
        api = QueueApi([task(1), task(2)])
        service, sid = self.service(api)
        self.start(service, sid, limit=1)
        self.wait(lambda: service.viralize_runs[sid].get('completed_total') == 2)
        self.assertGreaterEqual(service.viralize_runs[sid]['round'], 2)
        self.assertEqual(len([call for call in api.calls if call[0] == 'task/accept']), 2)
        service.cancel(sid)

    def test_waiting_execution_survives_restart_and_token_stays_encrypted(self):
        first_api = QueueApi([])
        first, sid = self.service(first_api)
        self.start(first, sid)
        self.wait(lambda: first.viralize_runs[sid]['status'] == 'waiting')
        first.close()
        raw = first.run_file.read_bytes()
        self.assertTrue(raw.startswith(PREFIX))
        self.assertNotIn(TOKEN.encode(), raw)
        self.assertTrue(read_json(first.run_file)[sid]['desired'])
        second_api = QueueApi([task(2)])
        second, _ = self.service(second_api, existing=True)
        self.assertFalse(second.session(sid)['authenticated'])
        second.resume_runs()
        second.resume_runs()  # Não cria um executor duplicado.
        self.wait(lambda: second.viralize_runs[sid].get('completed_total') == 1)
        self.assertTrue(second.session(sid)['authenticated'])
        self.assertEqual(len([call for call in second_api.calls if call[0] == 'task/accept']), 1)
        self.assertNotIn(TOKEN, json.dumps(second.snapshot()))
        second.cancel(sid)

    def test_stop_wakes_waiting_worker_and_prevents_restart(self):
        api = QueueApi([])
        service, sid = self.service(api)
        service.queue_poll_seconds = 60
        self.start(service, sid)
        self.wait(lambda: service.viralize_runs[sid]['status'] == 'waiting')
        service.cancel(sid)
        self.wait(lambda: sid not in service.run_threads)
        plan = read_json(service.run_file)[sid]
        self.assertFalse(plan['desired'])
        self.assertNotIn('token', plan)
        service.close()
        api2 = QueueApi([])
        restored, _ = self.service(api2, existing=True)
        restored.resume_runs()
        self.assertEqual(restored.viralize_runs[sid]['status'], 'stopped')
        self.assertEqual(api2.calls, [])

    def test_restart_during_uncertain_action_pauses_without_repeating(self):
        api = QueueApi([])
        service, sid = self.service(api)
        self.start(service, sid)
        self.wait(lambda: service.viralize_runs[sid]['status'] == 'waiting')
        with service.lock:
            service.viralize_runs[sid]['phase'] = 'executing'
            service.checkpoint_run(sid, service.viralize_runs[sid])
        service.close()
        api2 = QueueApi([task(1)])
        restored, _ = self.service(api2, existing=True)
        with patch('app.subprocess.Popen') as process:
            restored.resume_runs()
            self.assertEqual(restored.viralize_runs[sid]['status'], 'attention')
            process.assert_not_called()
        self.assertEqual(api2.calls, [])
        self.assertFalse(read_json(restored.run_file)[sid]['desired'])

    def test_provider_denial_pauses_without_retry_or_automatic_restart(self):
        api = QueueApi([ApiError('HTTP 429: operação limitada pelo provedor.', 429)])
        service, sid = self.service(api)
        self.start(service, sid)
        self.wait(lambda: service.viralize_runs[sid]['status'] == 'error')
        service.close()
        api2 = QueueApi([])
        restored, _ = self.service(api2, existing=True)
        restored.resume_runs()
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api2.calls, [])
        self.assertFalse(read_json(restored.run_file)[sid]['desired'])


if __name__ == '__main__': unittest.main()
