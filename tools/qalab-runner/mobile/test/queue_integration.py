"""Distributed Runner → FastAPI → private SQLite integration; no network or handset.

Run: backend/.venv/bin/python tools/qalab-runner/mobile/test/queue_integration.py
"""
import base64
from contextlib import ExitStack
import io
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "backend"))
from app.api import devices, exec_queue, runner_update, test_plan
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import ExecRun, Project, RunnerDevice, User


class DistributedRunnerIntegration(unittest.TestCase):
    def test_two_consumers_report_all_four_contract_blocks(self):
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="qalab-mobile-contract-")))
            engine = create_engine(f"sqlite:///{root}/platform.db", connect_args={"check_same_thread": False})
            stack.callback(engine.dispose)
            Base.metadata.create_all(engine)
            session = sessionmaker(bind=engine, expire_on_commit=False)
            names = {p: "contract-" + p for p in ("android", "ios")}
            tokens = {p: secrets.token_hex(24) for p in names}
            with session.begin() as db:
                user = User(id=1, username="contract", name="Contract test", password_hash="unused", is_platform_admin=True)
                db.add_all([user, Project(id=1, code="contract", name="Transport fixture")])
                for platform, name in names.items():
                    db.add(RunnerDevice(owner_id=1, name=name, runner_id=name, platform=platform, token=tokens[platform]))

            def fixture_db():
                with session() as db:
                    yield db

            app = FastAPI()
            for router in (devices.router, exec_queue.router, test_plan.router, runner_update.router):
                app.include_router(router)
            app.dependency_overrides[get_db] = fixture_db
            app.dependency_overrides[get_current_user] = lambda: user
            stack.enter_context(patch.object(exec_queue, "_UPLOADS_DIR", str(root / "uploads")))
            stack.enter_context(patch.object(exec_queue, "_EXEC_SHOT_ROOT", str(root / "uploads/execs")))
            client = stack.enter_context(TestClient(app))

            bundle = client.get("/api/runner/bundle", headers={"Authorization": "Bearer " + tokens["ios"]})
            bundle.raise_for_status()
            install = root / "runner"
            with ZipFile(io.BytesIO(bundle.content)) as archive:
                for member in archive.namelist():
                    if member.startswith("mobile/") or member in {"desktop-lease.cjs", "result-reporting.mjs"}:
                        archive.extract(member, install)
            self.assertTrue((install / "mobile/runner.mjs").is_file())
            self.assertFalse((install / "mobile/test/queue_integration.py").exists())

            child = subprocess.Popen(["node", str(HERE / "queue_client.mjs"), str(install), str(root / "evidence"),
                                      tokens["android"], tokens["ios"]], text=True,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            def stop():
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
                child.stdin.close()
                child.stdout.close()
            stack.callback(stop)
            for line in child.stdout:
                request = json.loads(line)
                if request.get("dispatch"):
                    plan = client.post("/api/test-plans", json={
                        "project_id": 1, "name": "Transport only", "mobile_targets": names, "seed_mobile_smoke": True,
                    })
                    plan.raise_for_status()
                    response = client.post(f"/api/test-plans/{plan.json()['data']['id']}/run")
                    response.raise_for_status()
                    reply = response.json()["data"]
                else:
                    response = client.request(request["method"], request["path"], headers=request["headers"],
                                              content=base64.b64decode(request["body"]))
                    reply = {"status": response.status_code, "data": response.json()}
                child.stdin.write(json.dumps(reply) + "\n")
                child.stdin.flush()
            self.assertEqual(child.wait(timeout=5), 0)
            with session() as db:
                rows = db.query(ExecRun).all()
                self.assertEqual(len(rows), 8)
                for row in rows:
                    self.assertIsNotNone(row.heartbeat_at)
                    self.assertEqual(row.verdict, "blocked")
                    self.assertIn("Contract fixture: no product was operated", row.reason)
                    self.assertEqual(row.fail_kind, "selector")
                    payload, report = json.loads(row.payload), json.loads(row.report)
                    self.assertEqual(payload["mobile_target"]["device_id"], report[-1]["device"]["device_id"])
                    shot = next(s["shot"] for s in report if s.get("shot"))
                    self.assertTrue((root / shot.lstrip("/")).is_file())


if __name__ == "__main__":
    unittest.main()
