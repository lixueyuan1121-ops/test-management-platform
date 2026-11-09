"""HTTP contracts for two-device dispatch, real-device binding and CI evidence."""
import json
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.api import exec_queue, hooks, test_plan
from app.core.deps import RunnerCtx, require_runner_ctx
from app.models import ExecRun, TestCase, TestPlan, TestPlanRun
from scripts import test_device_board_accuracy as fixtures


class MobileTests(unittest.TestCase):
    setUp = fixtures.DeviceBoardTests.setUp
    tearDown = fixtures.DeviceBoardTests.tearDown

    def prepare(self):
        self.device.platform = "android"
        self.other.platform = "ios"
        self.db.commit()
        self.client.app.include_router(test_plan.router)
        self.client.app.include_router(hooks.router)
        self.client.app.dependency_overrides[hooks.require_hook_auth] = lambda: None
        self.targets = {"android": "a", "ios": "b"}
        self.handsets = [self.device, self.other]
        for device in self.handsets:
            self.presence(device)

    def token(self, device):
        self.client.app.dependency_overrides[require_runner_ctx] = lambda: RunnerCtx(device)

    def presence(self, device, status=200, **extra):
        self.token(device)
        info = {"platform": device.platform, "device_id": device.runner_id * 64,
                "app_id": "com.example.dialog", "build": "100", "model": "phone", "os_version": "17",
                "physical": True, "online": True, **extra}
        res = self.client.post("/api/devices/mobile-presence", json=info)
        self.assertEqual(res.status_code, status, res.text)

    def plan(self):
        post = self.client.post("/api/test-plans", json={"project_id": 1, "name": "双端对话",
            "mobile_targets": self.targets, "mobile_builds": {"ios": "100"}, "seed_mobile_smoke": True})
        self.assertEqual(post.status_code, 200, post.text)
        plan = post.json()["data"]
        self.assertEqual(plan["case_count"], 4)
        return plan["id"]

    def test_manual_schedule_and_hook_share_snapshots_and_scenarios(self):
        self.prepare()
        pid = self.plan()
        self.assertEqual(self.client.post(f"/api/test-plans/{pid}/mobile-smoke").json()["data"]["case_count"], 4)
        self.assertEqual(self.db.query(TestCase).count(), 4)
        manual = self.client.post(f"/api/test-plans/{pid}/run")
        self.assertEqual(manual.status_code, 200, manual.text)
        self.assertEqual(manual.json()["data"]["run_count"], 8)
        detail = self.client.get("/api/test-plans/runs/" + str(manual.json()["data"]["plan_run_id"])).json()["data"]
        self.assertEqual({i["mobile_scenario"] for i in detail["items"]}, {"send", "followup", "stop", "history"})
        self.assertEqual({i["mobile_target"]["device_id"] for i in detail["items"]}, {"a"*64, "b"*64})
        self.db.get(TestPlan, pid).schedule_enabled = True
        self.db.commit()
        from app.services.scheduler import run_plan_job
        from sqlalchemy.orm import sessionmaker
        with patch("app.db.session.SessionLocal", sessionmaker(bind=self.engine)):
            run_plan_job(pid)
        ci = self.client.post("/api/hooks/run-plan", json={"plan_id": pid, "mobile_builds": {"android": "100"}})
        self.assertEqual(ci.status_code, 200, ci.text)
        self.assertEqual(self.db.query(ExecRun).filter_by(native_mobile=True).count(), 24)
        self.assertEqual({p.trigger for p in self.db.query(TestPlanRun)}, {"auto", "ci", "manual"})
        batch = ci.json()["data"]["batch_id"]
        gate = "/api/hooks/gate?batch_id=" + batch
        self.assertEqual(self.client.get(gate).json()["data"]["gate"], "pending")
        rows = self.db.query(ExecRun).filter_by(batch_id=batch).all()
        for row in rows:
            row.status = "passed"
        rows[-1].status = "blocked"
        self.db.commit()
        outcome = self.client.get(gate).json()["data"]
        self.assertEqual(outcome["gate"], "fail")
        self.assertEqual(outcome["mobile_case_stats"], {"total":4, "passed":3, "failed":0, "blocked":1, "pending":0})
        self.assertEqual(outcome["pass_rate"], 75.0)
        self.assertEqual(self.client.get(gate + "&min_pass_rate=0").json()["data"]["gate"], "fail")

    def test_atomic_validation_and_ownership(self):
        self.prepare()
        pid = self.plan()
        self.presence(self.device, 422, physical=False)
        self.presence(self.device, 409, device_id="b"*64)
        self.presence(self.other, online=False)
        path = f"/api/test-plans/{pid}/run"
        self.assertEqual(self.client.post(path).status_code, 409)
        self.presence(self.other)
        self.assertEqual(self.client.post(path, json={"mobile_builds": {"ios":"different"}}).status_code, 409)
        self.assertEqual(self.client.post(path, json={"runner":"mac-01"}).status_code, 422)
        self.other.owner_id = 99
        self.db.commit()
        self.assertEqual(self.client.post(path).status_code, 400)
        self.other.owner_id = 1
        self.db.query(TestCase).first().expected = "需要核对改动"
        self.db.commit()
        self.assertEqual(self.client.post(path).status_code, 422)
        self.assertEqual(self.db.query(ExecRun).count(), 0)
        self.assertEqual(self.db.query(TestPlanRun).count(), 0)

    def test_stale_queue_and_filtered_history(self):
        self.prepare()
        pid = self.plan()
        dispatch = self.client.post(f"/api/test-plans/{pid}/run").json()["data"]
        second = self.client.post(f"/api/test-plans/{pid}/run").json()["data"]
        history = self.client.get("/api/exec-queue/history", params={"project_id":1, "batch_id":second["batch_id"]})
        self.assertEqual(len(history.json()["data"]), 8)
        self.assertEqual({r["batch_id"] for r in history.json()["data"]}, {second["batch_id"]})
        summary = self.client.get("/api/exec-queue/history", params={"project_id": 1, "batch_id": second["batch_id"], "page": 1}).json()["data"]
        self.assertEqual({row["mobile_target"]["platform"] for row in summary["items"]}, {"ios", "android"})
        self.assertTrue(all("payload" not in row for row in summary["items"]))
        self.presence(self.device, build="101")
        from app.services.scheduler import reap_stale_exec_runs
        from sqlalchemy.orm import sessionmaker
        self.assertEqual(reap_stale_exec_runs(sessionmaker(bind=self.engine)), 8)
        self.db.expire_all()
        android_id, ios_id = dispatch["run_ids"][:2]
        self.assertEqual(self.db.get(ExecRun, android_id).status, "blocked")
        self.assertEqual(self.db.get(ExecRun, ios_id).status, "pending")
        corrected = self.client.patch(f"/api/exec-queue/{android_id}/verdict", json={"verdict":"pass"})
        self.assertEqual(corrected.status_code, 422)

    def test_android_signed_api_jobs_keep_desktop_execution(self):
        self.prepare()
        self.plan()
        api = self.db.query(TestCase).first()
        api.exec_kind, api.platform, api.mobile_scenario = "api", "android", None
        self.db.commit()
        # A desktop host can execute HTTP calls with Android signing parameters.
        response = self.client.post("/api/exec-queue/enqueue-cases", json={"project_id":1,"runner":"pc",
                                    "test_case_ids":[api.id]})
        self.assertEqual(response.status_code, 200, response.text)
        api_run = self.db.get(ExecRun, response.json()["data"]["run_ids"][0])
        self.assertFalse(api_run.native_mobile)
        self.assertEqual(api_run.kind, "api")
        # A previously registered Android API worker also still uses the ordinary claim route.
        self.token(self.device)
        response = self.client.post("/api/exec-queue/enqueue-cases", json={"project_id":1,"runner":"a",
                                    "test_case_ids":[api.id]})
        self.assertEqual(response.status_code, 200, response.text)
        rid = response.json()["data"]["run_ids"][0]
        self.assertEqual(self.client.post(f"/api/exec-queue/{rid}/claim?runner=a").status_code, 200)
        self.assertEqual(self.client.get("/api/exec-queue?runner=a&consumer=mobile").json()["data"], [])

    def test_only_mobile_consumer_can_claim_and_passing_needs_local_evidence(self):
        self.prepare()
        pid = self.plan()
        self.client.post(f"/api/test-plans/{pid}/run")
        self.token(self.device)
        rq = "/api/exec-queue"
        self.assertEqual(self.client.get(rq, params={"runner":"a"}).json()["data"], [])
        pending = self.client.get(rq, params={"runner":"a", "consumer":"mobile"}).json()["data"]
        self.assertEqual(len(pending), 4)
        rid = pending[0]["run_id"]
        endpoint = f"{rq}/{rid}"
        claim = endpoint + "/claim"
        native = {"runner": "a", "consumer": "mobile"}
        self.assertEqual(self.client.post(claim, params={"runner":"a"}).status_code, 409)
        self.token(None)
        self.assertEqual(self.client.post(claim, params=native).status_code, 403)
        self.token(self.other)
        self.assertEqual(self.client.post(claim, params=native).status_code, 403)
        self.presence(self.device, build="101")
        self.assertEqual(self.client.post(claim, params=native).status_code, 409)
        self.presence(self.device)
        self.assertEqual(self.client.patch(endpoint, params=native, json={"verdict":"pass"}).status_code, 409)
        self.assertEqual(self.client.post(claim, params=native).status_code, 200)
        self.assertEqual(self.client.patch(endpoint, params=native, json={"verdict":"pass"}).status_code, 422)
        with tempfile.TemporaryDirectory() as root, patch.object(exec_queue, "_UPLOADS_DIR", root), patch.object(
                exec_queue, "_EXEC_SHOT_ROOT", str(Path(root)/"execs")):
            shot = self.client.post(endpoint+"/screenshot", params={**native, "idx":1},
                files={"file":("step.png", exec_queue._PNG_MAGIC + b"sample", "image/png")})
            self.assertEqual(shot.status_code, 200, shot.text)
            steps = [{"action":"assert", "ok":True, "check":{"actual":"complete","expected":"complete"},
                      "shot":shot.json()["data"]["screenshot_url"]}]
            for invalid in [{"actual":"empty","expected":"complete"}, {"actual":None}, {"mode":"unrecognized",
                            "actual":"x","expected":"x"}, {"mode":[],"actual":1,"expected":1}]:
                wrong = [*steps, {"action":"assert", "ok":True, "check":invalid}]
                rejected = self.client.patch(endpoint, params=native, json={"verdict":"pass","report":wrong})
                self.assertEqual(rejected.status_code, 422)
            res = self.client.patch(endpoint, params=native, json={"verdict":"pass", "report":steps})
            self.assertEqual(res.status_code, 200, res.text)
        retry = self.client.post(endpoint + "/retry")
        self.assertEqual(retry.status_code, 200, retry.text)
        retried = self.db.get(ExecRun, retry.json()["data"]["run_id"])
        self.assertEqual(json.loads(retried.payload), pending[0]["payload"])
        self.assertEqual((retried.batch_id, retried.retry_of, retried.attempt), (pending[0]["batch_id"], rid, 2))
        self.assertEqual(self.client.post(endpoint + "/retry").json()["data"]["run_id"], retried.id)
        self.device.mobile_seen_at = datetime.utcnow() - timedelta(minutes=4)
        self.db.commit()
        self.assertEqual(self.client.post(endpoint + "/retry").status_code, 409)

    def test_script_report_requires_the_dispatched_steps_hash_and_assertion_screens(self):
        self.prepare()
        plan_id = self.plan()
        case = self.db.query(TestCase).first()
        from app.services.mobile_dispatch import bind_scenario
        script = [{"action":"new_chat"}, {"action":"compose","text":"验收\n{{marker}}😊"},
                  {"action":"assert_draft","text":"验收\n{{marker}}😊"}, {"action":"send"},
                  {"action":"assert_user_message","turn":1}, {"action":"wait_reply"},
                  {"action":"assert_reply_contains","text":"收到"}, {"action":"open_history"},
                  {"action":"assert_history_restored"}]
        case.platform, case.script = "mobile", json.dumps(script, ensure_ascii=False)
        bind_scenario(case, "script")
        self.db.commit()
        self.client.post(f"/api/test-plans/{plan_id}/run").raise_for_status()
        self.token(self.device)
        run = self.db.query(ExecRun).filter_by(test_case_id=case.id, runner="a").one()
        endpoint = f"/api/exec-queue/{run.id}"
        native = {"runner":"a","consumer":"mobile"}
        self.client.post(endpoint + "/claim", params=native).raise_for_status()
        message = "验收\nQA-test😊"
        answer = "已收到。"
        checks = {3: ("equals", message, message), 5: ("equals", message, message), 6: ("neq", answer, ""),
                  7: ("contains", answer, "收到"), 9: ("equals", message+"\n"+answer, message+"\n"+answer)}
        report = [{"no":i, "action":s["action"], "ok":True} for i, s in enumerate(script, 1)]
        metadata = {"no":10, "ok":True, "action":"device_info", "scenario":"script", "marker":"QA-test",
                    "mobile_script_length":len(script), "mobile_script_sha256":hashlib.sha256(
                        json.dumps(script, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()}
        with tempfile.TemporaryDirectory() as root, patch.object(exec_queue, "_UPLOADS_DIR", root), patch.object(
                exec_queue, "_EXEC_SHOT_ROOT", str(Path(root)/"execs")):
            for i, (mode, actual, expected) in checks.items():
                shot = self.client.post(endpoint + "/screenshot", params={**native, "idx":i},
                    files={"file":("step.png", exec_queue._PNG_MAGIC + b"sample", "image/png")})
                report[i-1].update(check={"mode":mode, "actual":actual, "expected":expected},
                                   shot=shot.json()["data"]["screenshot_url"])
            def submit(rows):
                return self.client.patch(endpoint, params=native, json={"verdict":"pass","report":rows})

            for invalid in ([*report[1:], metadata], [*report, {**metadata, "mobile_script_sha256":"f"*64}],
                            [*report, {**metadata, "mobile_script_length":100}], [*report],
                            [report[1], report[0], *report[2:], metadata],
                            [*report[:4], {**report[4], "check":None}, *report[5:], metadata],
                            [*report[:4], {**report[4], "shot":report[2]["shot"]}, *report[5:], metadata],
                            [*report[:2], {**report[2], "check":{"actual":"other", "expected":"other"}},
                             *report[3:], metadata],
                            [*report[:5], {**report[5], "check":{"mode":"neq","actual":None,"expected":""}},
                             *report[6:], metadata],
                            [*report[:6], {**report[6], "check":{"mode":"contains","actual":"别的收到","expected":"收到"}},
                             *report[7:], metadata]):
                self.assertEqual(submit(invalid).status_code, 422)
            response = submit([*report, metadata])
            self.assertEqual(response.status_code, 200, response.text)

    def test_case_editor_validates_native_steps_and_clears_approval_when_changed(self):
        self.prepare()
        self.plan()
        from app.api import ai
        self.client.app.include_router(ai.router)
        case = self.db.query(TestCase).first()
        path = f"/api/ai/testcases/{case.id}"
        steps = [{"action":"new_chat"}, {"action":"assert_send_unavailable"}]
        self.assertEqual(self.client.patch(path, json={"script":[{"action":"click","key":"window"}]}).status_code, 400)
        self.assertEqual(self.client.patch(path, json={"script":steps}).status_code, 200)
        self.db.refresh(case)
        self.assertEqual(json.loads(case.script), steps)
        self.assertEqual(case.mobile_scenario, "script")
        self.assertIsNone(case.mobile_case_hash)
        self.assertEqual(self.client.post(path + "/gen-script").status_code, 422)


if __name__ == "__main__":
    unittest.main()
