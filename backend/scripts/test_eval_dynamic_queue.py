"""Run with: python -m scripts.test_eval_dynamic_queue (isolated SQLite)."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.api import eval_queue as queue
from app.core.deps import RunnerCtx, require_runner_ctx
from app.core.enums import EvalRunStatus
from app.db.session import Base, get_db
from app.models import EvalRun, Project


class DynamicQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.temp.name}/queue.db",
                                    connect_args={"check_same_thread": False, "timeout": 10})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add(Project(id=1, name="P", code="P", status="active"))
            db.commit()
        app = FastAPI()
        app.include_router(queue.router)

        def database():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[require_runner_ctx] = lambda: RunnerCtx(device=None)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def seed(self, count=2, batch="b1", owner="r1", group="g", **kwargs):
        with self.sessions() as db:
            rows = [EvalRun(project_id=1, batch_id=batch, runner=owner,
                            target_engine="namiwork", status=EvalRunStatus.pending,
                            eligible_runners=json.dumps(["r1", "r2", "r3", "r4", "r5"]),
                            payload=json.dumps({"conversation_group": group, "turn_index": i}),
                            **kwargs) for i in range(count)]
            db.add_all(rows)
            db.commit()
            return [r.id for r in rows]

    def pending(self, runner="r2", engine="namiwork", dynamic=True):
        response = self.client.get("/api/eval-queue", params={
            "runner": runner, "engine": engine, "limit": 1, "dynamic": dynamic})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def claim(self, rid, runner="r2", whole=True):
        return self.client.post(f"/api/eval-queue/{rid}/claim", params={
            "runner": runner, "engine": "namiwork", "whole_group": whole})

    def test_idle_selected_machine_takes_offline_owners_whole_group(self):
        ids = self.seed()
        self.assertEqual([r["run_id"] for r in self.pending()], ids)
        result = self.claim(ids[0])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["data"]["run_ids"], ids)
        with self.sessions() as db:
            rows = db.query(EvalRun).all()
            self.assertTrue(all(r.runner == "r2" and r.started_at and r.claim_token for r in rows))
        self.assertEqual(self.pending("r3"), [])

    def test_scope_engine_pinned_vm_and_legacy_are_not_stolen(self):
        ids = self.seed(target_device="vm-1")
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.claim(ids[0]).status_code, 409)
        with self.sessions() as db:
            for r in db.query(EvalRun).all():
                r.target_device = None
            db.commit()
        self.assertEqual(self.pending("outsider"), [])
        self.assertEqual([r["run_id"] for r in self.pending(engine="workbuddy")], ids)
        self.assertEqual(self.pending(engine="unknown"), [])
        self.assertEqual(self.pending(dynamic=False), [])
        with self.sessions() as db:
            for r in db.query(EvalRun).all():
                r.target_engine = "workbuddy"
            db.commit()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.claim(ids[0]).status_code, 409)
        self.assertEqual([r["run_id"] for r in self.pending(engine="workbuddy")], ids)
        with self.sessions() as db:
            for r in db.query(EvalRun).all():
                r.eligible_runners = None
                r.target_engine = "namiwork"
            db.commit()
        self.assertEqual(self.pending(), [])
        self.assertEqual(len(self.pending("r1", dynamic=False)), 2)

    def test_fast_runner_drains_five_shards_without_moving_active_work(self):
        shards = {runner: self.seed(count=2, owner=runner, group=None)
                  for runner in ["r1", "r2", "r3", "r4", "r5"]}
        for runner, ids in shards.items():
            if runner != "r2":
                self.assertEqual(self.claim(ids[0], runner).status_code, 200)
        finished = []
        while pending := self.pending("r2"):
            rid = pending[0]["run_id"]
            data = self.claim(rid).json()["data"]
            response = self.client.patch(f"/api/eval-queue/{rid}",
                params={"runner": "r2", "claim_token": data["claim_token"]}, json={"status": "done"})
            self.assertEqual(response.status_code, 200)
            finished.append(rid)
        self.assertEqual(len(finished), 6)
        self.assertEqual(finished[:2], shards["r2"])
        with self.sessions() as db:
            active = db.query(EvalRun).filter(EvalRun.status == EvalRunStatus.running).all()
            self.assertEqual({r.runner for r in active}, {"r1", "r3", "r4", "r5"})
            self.assertEqual(db.query(EvalRun).filter(EvalRun.status == EvalRunStatus.pending).count(), 0)

    def test_schema_upgrade_preserves_rows_and_is_idempotent(self):
        from app.db import migrate
        old = create_engine("sqlite:///:memory:")
        try:
            with old.begin() as conn:
                conn.execute(text("CREATE TABLE eval_run (id INTEGER PRIMARY KEY)"))
                conn.execute(text("INSERT INTO eval_run (id) VALUES (1)"))
            with patch.object(migrate, "engine", old):
                migrate.ensure_eval_run_scheduling_columns()
                migrate.ensure_eval_run_scheduling_columns()
            self.assertEqual({c["name"] for c in inspect(old).get_columns("eval_run")},
                             {"id", "eligible_runners", "started_at", "heartbeat_at", "claim_token"})
            with old.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT id, claim_token FROM eval_run")).one(), (1, None))
        finally:
            old.dispose()

    def test_batch_and_variant_isolation(self):
        first = self.seed(batch="a")
        second = self.seed(batch="b")
        self.assertEqual([r["run_id"] for r in self.pending()], first)
        self.assertEqual(self.claim(first[0]).json()["data"]["run_ids"], first)
        self.assertEqual([r["run_id"] for r in self.pending("r3")], second)
        with self.sessions() as db:
            a = db.get(EvalRun, second[0])
            b = db.get(EvalRun, second[1])
            a.payload = json.dumps({"conversation_group": "g", "compare_group": "A"})
            b.payload = json.dumps({"conversation_group": "g", "compare_group": "B"})
            db.commit()
        self.assertEqual(len(self.pending("r3")), 1)

    def test_started_legacy_group_cannot_move(self):
        ids = self.seed()
        self.assertEqual(self.claim(ids[0], "r1", whole=False).status_code, 200)
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.claim(ids[1]).status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.get(EvalRun, ids[1]).status, EvalRunStatus.pending)

    def test_concurrent_claim_only_one_wins(self):
        ids = self.seed()
        barrier = threading.Barrier(2)
        original = queue._group_rows

        def synchronized(db, row):
            rows = original(db, row)
            barrier.wait(timeout=5)
            return rows

        with patch.object(queue, "_group_rows", synchronized), ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda runner: self.claim(ids[0], runner), ["r2", "r3"]))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        with self.sessions() as db:
            self.assertEqual(len({r.runner for r in db.query(EvalRun).all()}), 1)

    def test_execution_token_and_terminal_protection(self):
        rid = self.seed(count=1)[0]
        token = self.claim(rid).json()["data"]["claim_token"]
        url = f"/api/eval-queue/{rid}"
        self.assertEqual(self.client.patch(url, params={"runner": "r2"}, json={"status": "done"}).status_code, 409)
        params = {"runner": "r2", "claim_token": token}
        self.assertEqual(self.client.post(url + "/heartbeat", params=params).status_code, 200)
        with patch.object(queue, "_TRACE_ROOT", self.temp.name), patch.object(queue, "_UPLOADS_DIR", self.temp.name):
            response = self.client.post(url + "/trace", params={"runner": "r2", "claim_token": "wrong"},
                                        files={"file": ("trace.json", b"{}", "application/json")})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.patch(url, params=params, json={"status": "done"}).status_code, 200)
        self.assertEqual(self.client.patch(url, params=params, json={"status": "failed"}).status_code, 409)
        self.assertEqual(self.client.post(url + "/heartbeat", params=params).status_code, 409)

    def test_group_claim_rolls_back_if_legacy_claims_one_turn(self):
        ids = self.seed()
        original = queue._group_rows

        def race(db, row):
            group = original(db, row)
            self.assertEqual(self.claim(ids[1], "r1", whole=False).status_code, 200)
            return group

        with patch.object(queue, "_group_rows", race):
            self.assertEqual(self.claim(ids[0]).status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.get(EvalRun, ids[0]).status, EvalRunStatus.pending)
            self.assertEqual(db.get(EvalRun, ids[0]).runner, "r1")
            self.assertEqual(db.get(EvalRun, ids[1]).status, EvalRunStatus.running)

    def test_duplicate_runner_process_cannot_double_claim(self):
        rid = self.seed(count=1)[0]
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: self.claim(rid), range(2)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])

    def test_retry_resets_whole_context_and_invalidates_old_token(self):
        ids = self.seed()
        old = self.claim(ids[0]).json()["data"]["claim_token"]
        for rid, state in zip(ids, ["done", "failed"]):
            response = self.client.patch(f"/api/eval-queue/{rid}", params={"runner": "r2", "claim_token": old},
                                         json={"status": state})
            self.assertEqual(response.status_code, 200)
        with self.sessions() as db:
            queue.reset_conversation_for_retry(db, db.get(EvalRun, ids[1]))
            db.commit()
        self.assertEqual(len(self.pending("r3")), 2)
        new = self.claim(ids[0], "r2").json()["data"]["claim_token"]
        self.assertNotEqual(new, old)
        response = self.client.patch(f"/api/eval-queue/{ids[0]}", params={"runner": "r2", "claim_token": old},
                                     json={"status": "done"})
        self.assertEqual(response.status_code, 409)

    def test_queue_age_does_not_kill_newly_started_run(self):
        ids = self.seed(count=3, group=None, created_at=datetime.utcnow() - timedelta(days=2))
        self.claim(ids[0])
        self.claim(ids[1])
        with self.sessions() as db:
            db.get(EvalRun, ids[1]).heartbeat_at = datetime.utcnow() - timedelta(hours=7)
            db.commit()
        from app.services.scheduler import reap_stale_eval_runs
        with patch("app.db.session.SessionLocal", self.sessions), patch("app.services.eval_pipeline.on_batch_maybe_done"):
            reap_stale_eval_runs()
        with self.sessions() as db:
            self.assertEqual(db.get(EvalRun, ids[0]).status, EvalRunStatus.running)
            self.assertEqual(db.get(EvalRun, ids[1]).status, EvalRunStatus.failed)
            self.assertEqual(db.get(EvalRun, ids[2]).status, EvalRunStatus.pending)


if __name__ == "__main__":
    unittest.main()
