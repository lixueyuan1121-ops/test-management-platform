"""Real HTTP claims/cleanup against an isolated database; no production writes."""
from datetime import datetime, timedelta
from unittest.mock import patch
import unittest
from scripts.test_device_board_accuracy import DeviceBoardTests
from app.api import eval_queue
from app.core.deps import RunnerCtx, require_runner_ctx
from app.models import EvalRun, ExecRun
from app.services.run_activity import expired_run_filter


class RunnerIsolationTests(DeviceBoardTests):
    def setUp(self):
        super().setUp()
        self.client.app.include_router(eval_queue.router)
        self.now = datetime.utcnow()

    def claim(self, row):
        kind = 'eval' if isinstance(row, EvalRun) else 'exec'
        return self.client.post(f'/api/{kind}-queue/{row.id}/claim', params={
            'runner': 'a', 'whole_group': True, 'engine': 'namiwork'})

    def test_both_directions_are_exclusive(self):
        for first_model, next_model in [(ExecRun, EvalRun), (EvalRun, ExecRun), (EvalRun, EvalRun)]:
            with self.subTest(first=first_model, second=next_model):
                self.db.query(ExecRun).delete(); self.db.query(EvalRun).delete(); self.db.commit()
                first = self.run_row(first_model)
                second = self.run_row(next_model)
                response = self.claim(first)
                self.assertEqual(response.status_code, 200, response.text)
                response = self.claim(second)
                self.assertEqual(response.status_code, 409, response.text)
                self.db.refresh(second)
                self.assertEqual(second.status, 'pending')
                first.status = 'failed'; self.db.commit()
                self.assertEqual(self.claim(second).status_code, 200)

    def test_eval_shared_token_cannot_bypass_functional_lock(self):
        self.run_row(status='running', runner_device_id=1, heartbeat_at=self.now)
        pending = self.run_row(EvalRun)
        self.client.app.dependency_overrides[require_runner_ctx] = lambda: RunnerCtx(None)
        self.assertEqual(self.claim(pending).status_code, 409)

    def test_stale_eval_recovery_preserves_queue_and_live_legacy_work(self):
        old = self.run_row(EvalRun, status='running', runner_device_id=1,
                           heartbeat_at=self.now - timedelta(minutes=6), claim_token='old')
        pending = self.run_row()
        self.assertEqual(self.claim(pending).status_code, 200)
        self.db.refresh(old)
        self.assertEqual(old.status, 'failed')
        self.assertIsNotNone(old.finished_at)
        pending.status = 'passed'; self.db.commit()
        legacy = self.run_row(EvalRun, status='running', started_at=self.now - timedelta(hours=1))
        self.assertEqual(self.claim(self.run_row()).status_code, 409)
        self.assertEqual(legacy.status, 'running')

    def test_heartbeat_expiry_does_not_end_long_active_run(self):
        self.run_row(EvalRun, status='running', claim_token='live', heartbeat_at=self.now,
                     started_at=self.now - timedelta(hours=8))
        stale = self.run_row(EvalRun, status='running', claim_token='stale',
                             heartbeat_at=self.now - timedelta(minutes=6))
        self.assertEqual([r.id for r in self.db.query(EvalRun).filter(expired_run_filter(EvalRun, self.now))], [stale.id])

    def test_clear_queue_retains_history_running_and_other_devices(self):
        pending = self.run_row()
        ev = self.run_row(EvalRun)
        active = self.run_row(status='running', runner_device_id=1)
        other = self.run_row(runner='b', runner_device_id=2)
        done = self.run_row(status='passed')
        response = self.client.post('/api/devices/1/clear-pending')
        self.assertEqual(response.json()['data'], {'func': 1, 'eval': 1})
        self.db.expire_all()
        self.assertEqual(pending.status, 'blocked')
        self.assertEqual(pending.fail_kind, 'cancelled')
        self.assertEqual(ev.status, 'cancelled')
        self.assertEqual(active.status, 'running')
        self.assertEqual(other.status, 'pending')
        self.assertEqual(done.status, 'passed')
        self.assertEqual(self.client.post('/api/devices/1/clear-pending').json()['data'], {'func': 0, 'eval': 0})

    def test_clear_queue_denies_non_owner(self):
        self.user.is_platform_admin = False
        self.device.owner_id = 42
        self.db.commit()
        row = self.run_row()
        self.assertEqual(self.client.post('/api/devices/1/clear-pending').status_code, 404)
        self.assertEqual(row.status, 'pending')

    def test_dual_consumers_are_both_available(self):
        from app.services.dispatcher import device_conflicts_kind
        self.device.last_exec_at = self.now
        self.device.last_eval_at = self.now
        self.db.commit()
        self.assertFalse(device_conflicts_kind(self.db, 'a', 'exec'))
        self.assertFalse(device_conflicts_kind(self.db, 'a', 'eval'))
        self.assertEqual(self.board()[1]['active_kinds'], ['func', 'eval'])

if __name__ == '__main__': unittest.main()
