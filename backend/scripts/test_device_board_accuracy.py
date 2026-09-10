"""Device attribution, completion dates, liveness and reservation regression tests."""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import devices, exec_queue
from app.core.deps import RunnerCtx, get_current_user, require_runner_ctx
from app.db.session import Base, get_db
from app.models import EvalRun, ExecRun, Project, RunnerDevice, User


class DeviceBoardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.now = datetime(2026, 9, 10, 12)
        self.user = User(id=1, username="test", name="Test", password_hash="x", is_platform_admin=True)
        self.device = RunnerDevice(id=1, owner_id=1, name="A", runner_id="a", token="a")
        self.other = RunnerDevice(id=2, owner_id=1, name="B", runner_id="b", token="b")
        self.db.add_all([self.user, Project(id=1, name="P", code="p"), self.device, self.other])
        self.db.commit()
        app = FastAPI()
        app.include_router(devices.router)
        app.include_router(exec_queue.router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[require_runner_ctx] = lambda: RunnerCtx(self.device)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def run_row(self, model=ExecRun, **kwargs):
        values = dict(project_id=1, runner="a", payload=json.dumps({"title": "snapshot"}),
                      status="pending", created_at=self.now, updated_at=self.now)
        values.update(kwargs)
        row = model(**values)
        self.db.add(row)
        self.db.commit()
        return row

    def board(self):
        with patch("app.services.scheduler._db_now", return_value=self.now):
            response = self.client.get("/api/devices/overview")
        self.assertEqual(response.status_code, 200, response.text)
        return {d["id"]: d for d in response.json()["data"]["devices"]}

    def test_today_uses_completion_not_enqueue_or_later_edits(self):
        yesterday = self.now - timedelta(days=1)
        self.run_row(status="passed", created_at=yesterday, finished_at=self.now)
        self.run_row(status="failed", finished_at=yesterday, updated_at=self.now)
        self.run_row(EvalRun, status="judging", created_at=yesterday, finished_at=self.now)
        self.run_row(EvalRun, status="judged", finished_at=yesterday, updated_at=self.now)
        self.assertEqual(self.board()[1]["today"], {"passed": 2, "failed": 0, "blocked": 0})

    def test_stale_runs_do_not_make_device_online_or_busy(self):
        self.run_row(status="running", updated_at=self.now - timedelta(hours=29))
        self.run_row(EvalRun, status="running", updated_at=self.now - timedelta(hours=29))
        device = self.board()[1]
        self.assertFalse(device["online"])
        self.assertEqual(device["run_counts"]["running"], 0)
        self.assertEqual(device["stale_runs"], 2)
        self.assertEqual(device["active_runs"], [])
        self.assertEqual(device["active_kinds"], [])

    def test_elapsed_starts_at_claim_and_heartbeat_supports_long_runs(self):
        start = self.now - timedelta(seconds=40)
        self.run_row(status="running", created_at=self.now - timedelta(hours=29),
                     started_at=start, heartbeat_at=self.now)
        self.run_row(status="running", started_at=self.now - timedelta(hours=4), heartbeat_at=self.now)
        device = self.board()[1]
        self.assertEqual(device["run_counts"]["running"], 2)
        self.assertIn(40000, [r["elapsed_ms"] for r in device["active_runs"]])
        self.assertTrue(all(r["title"] == "snapshot" for r in device["active_runs"]))

    def test_actual_claimant_not_original_name_and_current_queue_owner(self):
        self.run_row(status="passed", runner="a", runner_device_id=2, finished_at=self.now)
        row = self.run_row(status="pending", runner="a")
        row.runner = "b"
        self.db.commit()
        board = self.board()
        self.assertEqual(board[1]["today"]["passed"], 0)
        self.assertEqual(board[1]["run_counts"]["pending"], 0)
        self.assertEqual(board[2]["today"]["passed"], 1)
        self.assertEqual(board[2]["run_counts"]["pending"], 1)

    def test_duplicate_names_do_not_duplicate_legacy_counts(self):
        self.db.add(User(id=2, username="other", name="Other", password_hash="x"))
        self.other.owner_id = 2
        self.other.runner_id = "a"
        self.db.commit()
        self.run_row(status="passed", runner_device_id=1, finished_at=self.now)
        self.run_row(status="passed", finished_at=self.now)
        board = self.board()
        self.assertTrue(board[1]["identity_conflict"])
        self.assertTrue(board[2]["identity_conflict"])
        self.assertEqual(board[1]["today"]["passed"], 1)
        self.assertEqual(board[2]["today"]["passed"], 0)

    def test_reserved_turns_are_waiting_not_parallel_running(self):
        for turn in (2, 0, 1):
            self.run_row(EvalRun, status="running", batch_id="batch", claim_token="claim",
                         started_at=self.now, heartbeat_at=self.now,
                         payload=json.dumps({"title": f"turn-{turn}", "conversation_group": "g", "turn_index": turn}))
        device = self.board()[1]
        self.assertEqual(device["run_counts"]["running"], 1)
        self.assertEqual(device["run_counts"]["pending"], 2)
        self.assertEqual([r["title"] for r in device["active_runs"]], ["turn-0"])

    def test_claim_records_device_and_completion_is_stable(self):
        row = self.run_row()
        params = {"runner": "a"}
        url = f"/api/exec-queue/{row.id}"
        self.assertEqual(self.client.post(url + "/claim", params=params).status_code, 200)
        self.db.refresh(row)
        self.assertEqual(row.runner_device_id, 1)
        self.assertIsNotNone(row.started_at)
        self.assertEqual(self.client.post(url + "/claim", params=params).status_code, 409)
        self.assertEqual(self.client.post(url + "/heartbeat", params=params).status_code, 200)
        self.assertEqual(self.client.patch(url, params=params, json={"verdict": "pass"}).status_code, 200)
        self.db.refresh(row)
        self.assertIsNotNone(row.finished_at)
        self.assertEqual(self.client.patch(url, params=params, json={"verdict": "fail"}).status_code, 409)

    def test_registration_rejects_trimmed_duplicate(self):
        response = self.client.post("/api/devices", json={"runner_id": " a ", "name": "duplicate"})
        self.assertEqual(response.status_code, 400)

    def test_tracking_migration_is_safe_and_idempotent(self):
        from app.db import migrate
        old = create_engine("sqlite://")
        try:
            with old.begin() as conn:
                conn.execute(text("CREATE TABLE exec_run (id INTEGER PRIMARY KEY)"))
                conn.execute(text("CREATE TABLE eval_run (id INTEGER PRIMARY KEY)"))
                conn.execute(text("INSERT INTO exec_run (id) VALUES (1)"))
            with patch.object(migrate, "engine", old):
                migrate.ensure_run_tracking_columns()
                migrate.ensure_run_tracking_columns()
            with old.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT auto_reassign, finished_at FROM exec_run")).one(), (0, None))
            self.assertIn("runner_device_id", {c["name"] for c in inspect(old).get_columns("eval_run")})
        finally:
            old.dispose()


if __name__ == "__main__":
    unittest.main()
