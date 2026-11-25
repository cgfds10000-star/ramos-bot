"""Fluxo sintético completo, sem chamadas a Instagram ou Viralize."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import ApiError, PACING_PROFILES, RamosService
from storage import read_json, write_json
from test_persistent_execution import FAKE_WORKER, QueueApi, TOKEN, task


class PacingChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.close()
        self.temp.cleanup()

    def service(self, api, existing=False):
        service = RamosService(self.temp.name, worker_command=[sys.executable, '-u', '-c', FAKE_WORKER],
                               dependency_check=lambda: True, viralize_api=api)
        self.services.append(service)
        service.pacing_seconds = Mock(return_value=.02)
        service.data['settings']['continuous_execution'] = False
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
            if predicate():
                return
            time.sleep(.005)
        self.fail('O fluxo não atingiu o estado esperado.')

    def start(self, service, sid, limit=2, speed='moderate'):
        service.start_viralize_batch({'session_ids': [sid], 'token': TOKEN, 'limit': limit, 'speed': speed})

    def test_normal_flow_waits_before_each_stage_and_between_tasks(self):
        class TimedApi(QueueApi):
            def request(inner, endpoint, fields, **kwargs):
                timestamps.append((endpoint, time.monotonic()))
                return super().request(endpoint, fields, **kwargs)

        timestamps = []
        api = TimedApi([task(1), task(2)])
        service, sid = self.service(api)
        self.start(service, sid, speed='calm')
        self.wait(lambda: service.viralize_runs[sid]['status'] == 'done')
        self.assertEqual([name for name, _ in timestamps],
                         ['task', 'task/accept', 'task/confirm-analysis'] * 2)
        for (_, before), (_, after) in zip(timestamps, timestamps[1:]):
            # Tolerância de 1 ns para a subtração dos floats do relógio.
            self.assertGreaterEqual(after - before + 1e-9, .015)
        self.assertEqual([call.args[1] for call in service.pacing_seconds.call_args_list],
                         ['api', 'execute', 'api', 'task', 'api', 'execute', 'api'])
        self.assertEqual(read_json(service.run_file)[sid]['speed'], 'calm')
        self.assertEqual(service.snapshot()['viralize'][sid]['speed'], 'calm')
        self.assertEqual(service.snapshot()['viralize_pacing']['calm']['task'], (60, 90))

    def test_stop_interrupts_pause_before_accepting_any_task(self):
        api = QueueApi([task(1)])
        service, sid = self.service(api)
        service.pacing_seconds.return_value = 60
        self.start(service, sid)
        self.wait(lambda: service.viralize_runs[sid]['phase'] == 'pace_accept')
        before = time.monotonic()
        service.cancel(sid)
        self.wait(lambda: sid not in service.run_threads)
        self.assertLess(time.monotonic() - before, 1)
        self.assertEqual([name for name, _ in api.calls], ['task'])
        self.assertFalse(read_json(service.run_file)[sid]['desired'])

    def test_next_task_cannot_start_while_interval_is_pending(self):
        api = QueueApi([task(1), task(2)])
        service, sid = self.service(api)
        service.pacing_seconds.side_effect = lambda run, stage: 60 if stage == 'task' else 0
        self.start(service, sid)
        self.wait(lambda: service.viralize_runs[sid]['phase'] == 'pace_next')
        snapshot = service.snapshot()['viralize'][sid]
        self.assertEqual(snapshot['status'], 'waiting')
        self.assertGreater(snapshot['next_check_at'], time.time() + 50)
        self.assertEqual([name for name, _ in api.calls], ['task', 'task/accept', 'task/confirm-analysis'])
        service.cancel(sid)
        self.wait(lambda: sid not in service.run_threads)
        self.assertEqual(len(api.calls), 3)

    def test_restart_honors_saved_deadline_and_keeps_selected_speed(self):
        first, sid = self.service(QueueApi([task(1)]))
        first.pacing_seconds.side_effect = lambda run, stage: 60 if stage == 'task' else 0
        self.start(first, sid, speed='slow')
        self.wait(lambda: first.viralize_runs[sid]['phase'] == 'pace_next')
        first.close()
        plans = read_json(first.run_file)
        # Prazo curto apenas para este teste: a retomada não pode consultar antes dele.
        deadline = time.time() + .3
        plans[sid]['next_check_at'] = deadline
        write_json(first.run_file, plans)
        api = QueueApi([task(2)])
        second, _ = self.service(api, existing=True)
        second.resume_runs()
        self.wait(lambda: second.session(sid)['authenticated'])
        self.assertLess(time.time(), deadline)
        self.assertEqual(api.calls, [])
        self.wait(lambda: second.viralize_runs[sid]['status'] == 'done')
        self.assertGreaterEqual(time.time(), deadline)
        self.assertEqual(second.viralize_runs[sid]['completed_total'], 2)
        self.assertEqual(second.viralize_runs[sid]['speed'], 'slow')
        self.assertEqual(len([name for name, _ in api.calls if name == 'task/confirm-analysis']), 1)

    def test_restart_after_instagram_action_does_not_repeat_or_confirm_it(self):
        first, sid = self.service(QueueApi([task(1)]))
        count = 0

        def delays(run, stage):
            nonlocal count
            count += 1
            return 60 if count == 3 else 0

        first.pacing_seconds.side_effect = delays
        self.start(first, sid)
        self.wait(lambda: first.viralize_runs[sid]['phase'] == 'pace_confirm')
        first.close()
        api = QueueApi([task(1)])
        second, _ = self.service(api, existing=True)
        second.resume_runs()
        self.assertEqual(second.viralize_runs[sid]['status'], 'attention')
        self.assertEqual(api.calls, [])

    def test_invalid_speed_is_rejected_before_start_and_legacy_default_is_moderate(self):
        api = QueueApi([])
        service, sid = self.service(api)
        for speed in ['turbo', None, [], {}, 0]:
            with self.subTest(speed=speed), self.assertRaises(ApiError):
                self.start(service, sid, speed=speed)
        self.assertEqual(service.viralize_runs, {})
        self.assertEqual(api.calls, [])
        service.start_viralize_batch({'session_ids': [sid], 'token': TOKEN})
        self.wait(lambda: service.viralize_runs[sid]['status'] == 'empty')
        self.assertEqual(service.viralize_runs[sid]['speed'], 'moderate')
        for profile in PACING_PROFILES:
            for stage in ('api', 'execute', 'task', 'round'):
                run = {'speed': profile}
                seconds = RamosService.pacing_seconds(service, run, stage)
                minimum, maximum = PACING_PROFILES[profile][stage]
                self.assertTrue(minimum <= seconds <= maximum)


if __name__ == '__main__':
    unittest.main()
