"""默认纳米Work、额外开启 WorkBuddy：实际轮询→双引擎下发→认领，内存库隔离。"""
import unittest
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.eval_queue import router
from app.api.eval_task import dispatch_task_runs
from app.core.deps import RunnerCtx, require_runner_ctx
from app.db.session import Base, get_db
from app.models import EvalQuery, EvalRun, EvalTask, Project, RunnerDevice, RunnerEvalHeartbeat, User
from app.services.dispatcher import online_eval_runners
from app.services.runner_presence import touch_eval_engine


class SameDeviceEnginesTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        with self.sessions() as db:
            db.add_all([User(id=1, username="test", name="Test", password_hash="x"),
                        Project(id=1, name="P", code="P"),
                        RunnerDevice(id=1, owner_id=1, runner_id="dual", name="双引擎电脑", token="test-only"),
                        EvalQuery(id=1, project_id=1, title="第一题", prompt="one"),
                        EvalQuery(id=2, project_id=1, title="第二题", prompt="two"),
                        EvalTask(id=1, project_id=1, name="双引擎回归", query_ids="[1,2]")])
            db.commit()
        self.shared = False
        app = FastAPI()
        app.include_router(router)

        def database():
            with self.sessions() as db:
                yield db

        def identity(db=Depends(get_db)):
            return RunnerCtx(None if self.shared else db.get(RunnerDevice, 1))

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[require_runner_ctx] = identity
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def poll(self, engine=None):
        params = {"runner": "dual", "dynamic": True, "limit": 20}
        if engine is not None:
            params["engine"] = engine
        response = self.client.get("/api/eval-queue", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def online(self, engine):
        with self.sessions() as db:
            return online_eval_runners(db, engine=engine)

    def dispatch(self):
        with self.sessions() as db:
            ids, _ = dispatch_task_runs(db, db.get(EvalTask, 1), "auto",
                                       ["namiwork", "workbuddy"], None, {}, None, 1)
            db.commit()
            return ids

    def claim(self, run_id, engine):
        return self.client.post(f"/api/eval-queue/{run_id}/claim", params={
            "runner": "dual", "engine": engine, "whole_group": True})

    def test_one_workbuddy_runner_dispatches_and_claims_both_products(self):
        self.poll("workbuddy")
        for engine in ["namiwork", "workbuddy"]:
            self.assertEqual(self.online(engine), ["dual"])
        self.assertEqual(len(self.dispatch()), 4)
        nami, wb = self.poll("namiwork"), self.poll("workbuddy")
        self.assertEqual(len(nami), 2)
        self.assertEqual(len(wb), 4)
        legacy_poll = self.client.get("/api/eval-queue", params={"runner": "dual", "engine": "namiwork"})
        self.assertTrue(all(r["target_engine"] == "namiwork" for r in legacy_poll.json()["data"]))
        self.assertTrue(all(r["target_engine"] == "namiwork" for r in nami))
        self.assertEqual({r["target_engine"] for r in wb}, {"namiwork", "workbuddy"})
        wb_run = next(r for r in wb if r["target_engine"] == "workbuddy")
        self.assertEqual(self.claim(wb_run["run_id"], "namiwork").status_code, 409)
        self.assertEqual(self.client.post(f'/api/eval-queue/{wb_run["run_id"]}/claim',
                                         params={"runner": "dual"}).status_code, 409)
        # 同一个启用 WorkBuddy 的客户端能认领两种产品，任务目标引擎仍独立。
        for run in wb:
            self.assertEqual(self.claim(run["run_id"], "workbuddy").status_code, 200)

    def test_workbuddy_capability_expires_when_only_default_runner_remains(self):
        self.poll("workbuddy")
        with self.sessions() as db:
            db.get(RunnerEvalHeartbeat, (1, "workbuddy")).last_seen_at = datetime.utcnow() - timedelta(minutes=4)
            db.commit()
        self.poll("namiwork")
        self.assertEqual(self.online("namiwork"), ["dual"])
        self.assertEqual(self.online("workbuddy"), [])
        with self.assertRaisesRegex(ValueError, "WorkBuddy.*无在线执行机"):
            self.dispatch()
        with self.sessions() as db:
            self.assertEqual(db.query(EvalRun).count(), 0)

    def test_shared_token_keeps_both_engine_heartbeats(self):
        self.shared = True
        self.poll("workbuddy")
        self.poll("namiwork")
        self.assertEqual(self.online("namiwork"), ["dual"])
        self.assertEqual(self.online("workbuddy"), ["dual"])

    def test_running_nami_heartbeat_preserves_both_capabilities_for_legacy_and_new_clients(self):
        self.poll("workbuddy")
        self.dispatch()
        rid = next(r["run_id"] for r in self.poll("workbuddy") if r["target_engine"] == "namiwork")
        token = self.claim(rid, "workbuddy").json()["data"]["claim_token"]
        for declaration in [None, "workbuddy"]:
            with self.subTest(engine=declaration):
                old = datetime.utcnow() - timedelta(minutes=4)
                with self.sessions() as db:
                    db.query(RunnerEvalHeartbeat).update({"last_seen_at": old})
                    db.get(EvalRun, rid).heartbeat_at = old
                    # 新客户端应使用显式声明，即使另一默认 runner 覆盖了旧字段。
                    if declaration:
                        db.get(RunnerDevice, 1).eval_engine = "namiwork"
                    db.commit()
                for engine in ["namiwork", "workbuddy"]:
                    self.assertEqual(self.online(engine), [])
                params = {"runner": "dual", "claim_token": token}
                if declaration:
                    params["engine"] = declaration
                invalid = self.client.post(f"/api/eval-queue/{rid}/heartbeat", params={**params, "claim_token": "invalid"})
                self.assertEqual(invalid.status_code, 409)
                invalid_engine = self.client.post(f"/api/eval-queue/{rid}/heartbeat", params={**params, "engine": "unknown"})
                self.assertEqual(invalid_engine.status_code, 409)
                for engine in ["namiwork", "workbuddy"]:
                    self.assertEqual(self.online(engine), [])
                valid = self.client.post(f"/api/eval-queue/{rid}/heartbeat", params=params)
                self.assertEqual(valid.status_code, 200, valid.text)
                for engine in ["namiwork", "workbuddy"]:
                    self.assertEqual(self.online(engine), ["dual"])

    def test_older_workbuddy_heartbeat_also_implies_nami(self):
        self.poll("workbuddy")
        with self.sessions() as db:
            db.delete(db.get(RunnerEvalHeartbeat, (1, "namiwork")))
            db.commit()
        self.assertEqual(self.online("namiwork"), ["dual"])
        self.assertEqual(self.online("workbuddy"), ["dual"])

    def test_legacy_and_invalid_engine(self):
        self.poll()
        self.assertEqual(self.online("namiwork"), ["dual"])
        self.assertEqual(self.online("workbuddy"), [])
        with self.assertRaisesRegex(ValueError, "WorkBuddy.*无在线执行机"):
            self.dispatch()
        self.poll("workbuddy")
        self.poll("unknown")
        with self.sessions() as db:
            self.assertEqual(db.query(RunnerEvalHeartbeat).count(), 2)
            self.assertEqual(db.get(RunnerDevice, 1).eval_engine, "workbuddy")

    def test_independent_sessions_do_not_overwrite_other_engine(self):
        with self.sessions() as first, self.sessions() as second:
            a, b = first.get(RunnerDevice, 1), second.get(RunnerDevice, 1)
            touch_eval_engine(first, a, "namiwork", datetime.utcnow())
            first.commit()
            touch_eval_engine(second, b, "workbuddy", datetime.utcnow())
            second.commit()
        with self.sessions() as db:
            self.assertEqual({r.engine for r in db.query(RunnerEvalHeartbeat)}, {"namiwork", "workbuddy"})

    def test_existing_database_gets_new_table_idempotently(self):
        RunnerEvalHeartbeat.__table__.drop(self.engine)
        Base.metadata.create_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.poll("namiwork")
        self.poll("workbuddy")
        self.assertEqual(self.online("namiwork"), ["dual"])
        self.assertEqual(self.online("workbuddy"), ["dual"])


if __name__ == "__main__":
    unittest.main()
