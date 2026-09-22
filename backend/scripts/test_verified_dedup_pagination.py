"""Isolated API regression, including concurrent imports using independent DB connections."""
import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.api.verified_import import router as imports
from app.api.exec_queue import router as queue
from app.api.ai import router as cases
from app.core.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.db.session import Base, get_db
from app.models import Project, RunnerDevice, TestCase, ExecRun, Requirement, AiTask


def source(external="import-0001", device=1):
    return {"project_id": 1, "runner_device_id": device, "external_id": external, "requirement": "Logo returns home",
            "cases": [{"title": "任务侧栏点击纳米Work Logo返回首页", "steps": "打开任务侧栏，点击顶部Logo并检查首页", "expected": "首页主体可见",
                       "exec_kind": "gui", "verdict": "pass", "executor": "actual executor", "environment": "test fixture Windows",
                       "scope": "logo navigation", "finished_at": datetime.now(timezone.utc).isoformat(), "duration_ms": 10,
                       "script": [{"action": "click", "target": {"selector": "#logo", "frame": "shell"}, "args": {}, "desc": "click"},
                                  {"action": "assert_visible", "target": {"selector": "#home", "frame": "shell"}, "args": {}, "desc": "home"}],
                       "report": [{"action": "click", "ok": True}, {"action": "assert_visible", "ok": True, "check": {"actual": True, "expected": True}}]}]}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + str(Path(self.tmp.name)/"test.db"), connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False)
        with self.factory() as db:
            db.add_all([Project(id=1, name="A", code="a"), Project(id=2, name="B", code="b"),
                        RunnerDevice(id=1, owner_id=1, runner_id="windows", name="windows", token="fixture1"),
                        RunnerDevice(id=2, owner_id=2, runner_id="mac", name="mac", token="fixture2")]); db.commit()
        app = FastAPI(); register_exception_handlers(app)
        app.include_router(imports); app.include_router(queue); app.include_router(cases)
        def database():
            with self.factory() as db: yield db
        def user(x_user: int = Header(1)): return SimpleNamespace(id=x_user, is_platform_admin=x_user in (1,2))
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = user
        self.client = TestClient(app)
    def tearDown(self):
        self.client.close(); self.engine.dispose(); self.tmp.cleanup()
    def post(self, body, user=1, preview=False):
        return self.client.post("/api/verified-imports" + ("/preview" if preview else ""), json=body, headers={"x-user": str(user)})
    def count(self, model):
        with self.factory() as db: return db.query(model).count()
    def baseline(self):
        response = self.post(source()); self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["records"][0]["case_id"]
    def ambiguous(self):
        body = source("import-similar", 2)
        body["cases"][0]["script"][0]["target"]["selector"] = "aside #logo"
        body["cases"][0]["expected"] = "产品首页主体、欢迎区可见"
        return body
    def choice(self, body, action, cid=None):
        preview = self.post(body, 2, True)
        self.assertEqual(preview.status_code, 200, preview.text)
        plan = preview.json()["data"]["cases"][0]
        body["cases"][0]["resolution"] = {"action": action, "case_id": cid, "token": plan["confirmation_token"],
                                                 "reason": "用户已比较步骤和验收点，确认此选择"}
        return body
    def test_cross_user_exact_and_receipt_retry(self):
        cid = self.baseline()
        body = source("other-member", 2)
        body["cases"][0]["title"] = "同一场景的新标题"
        body["cases"][0]["environment"] = "macOS"
        body["cases"][0]["script"][0]["desc"] = "different description"
        response = self.post(body, 2); self.assertEqual(response.status_code,200,response.text)
        data=response.json()["data"]; self.assertEqual(data["reused_cases"],1)
        self.assertEqual(data["records"][0]["case_id"],cid)
        self.assertEqual(self.count(TestCase),1);self.assertEqual(self.count(Requirement),1)
        self.assertEqual(self.post(body,2).json()["data"]["records"], data["records"])
        self.assertEqual(self.count(ExecRun),2)
        with self.factory() as db:
            self.assertEqual(db.get(TestCase,cid).title, source()["cases"][0]["title"])
            run=db.get(ExecRun,data["records"][0]["run_id"])
            self.assertEqual(json.loads(run.payload)["title"],"同一场景的新标题")
            self.assertEqual(run.runner,"mac")
    def test_ambiguous_atomic_confirmation_and_actual_snapshot(self):
        cid=self.baseline();body=self.ambiguous()
        result=self.post(body,2);self.assertEqual(result.status_code,409)
        self.assertEqual(result.json()["data"]["cases"][0]["candidates"][0]["case_id"],cid)
        self.assertEqual(self.count(ExecRun),1)
        self.assertEqual(self.count(AiTask),1)
        self.choice(body,"reuse",cid)
        data=self.post(body,2).json()["data"]
        with self.factory() as db:
            run=db.get(ExecRun,data["records"][0]["run_id"])
            self.assertEqual(json.loads(run.payload)["script"],body["cases"][0]["script"])
            self.assertEqual(json.loads(run.report),body["cases"][0]["report"])
            self.assertEqual(json.loads(db.get(TestCase,cid).script)[0]["target"]["selector"],"#logo")
    def test_confirm_distinct_and_stale_confirmation(self):
        cid=self.baseline();body=self.choice(self.ambiguous(),"create")
        with self.factory() as db:
            db.get(TestCase,cid).expected="新验收标准";db.commit()
        self.assertEqual(self.post(body,2).status_code,409)
        self.choice(body,"create")
        self.assertEqual(self.post(body,2).status_code,200)
        self.assertEqual(self.count(TestCase),2)
    def test_exact_cannot_force_create_and_invalid_target(self):
        cid=self.baseline(); body=source("exact-create",2)
        self.choice(body,"create"); self.assertEqual(self.post(body,2).status_code,409)
        body=self.choice(self.ambiguous(),"reuse",cid+999)
        self.assertEqual(self.post(body,2).status_code,409)
        self.assertEqual(self.count(TestCase),1)
    def test_batch_rejection_writes_nothing(self):
        self.baseline();body=self.ambiguous()
        extra=copy.deepcopy(body["cases"][0]);extra.update(title="无关的新场景",expected="支付成功",steps="付款")
        extra["script"][0]["target"]["selector"]="#pay"
        extra["script"][1]["target"]["selector"]="#paid"
        body["cases"].insert(0,extra)
        self.assertEqual(self.post(body,2).status_code,409)
        self.assertEqual(self.count(TestCase),1);self.assertEqual(self.count(Requirement),1)
    def test_scope_permissions_and_report_validation(self):
        self.baseline()
        other=source("other-project");other["project_id"]=2
        self.assertEqual(self.post(other).status_code,200)
        sub=source("other-product");sub["sub_product"]="new"
        self.assertEqual(self.post(sub).status_code,200)
        self.assertEqual(self.count(TestCase),3)
        self.assertEqual(self.post(source("bad-permission"),3).status_code,403)
        self.assertIn(self.post(source("wrong-device",2)).status_code,(400,422))
        invalid=source("invalid-report");invalid["cases"][0]["report"][1]["ok"]=False
        self.assertEqual(self.post(invalid).status_code,422)
        dup=source("same-batch");dup["cases"].append(copy.deepcopy(dup["cases"][0]));dup["cases"][1]["title"]="different title"
        self.assertEqual(self.post(dup).status_code,422)
    def test_concurrent_users_and_idempotent_retries(self):
        barrier=threading.Barrier(6)
        def run(i):
            body=source("parallel-"+str(i),1+i%2);barrier.wait()
            return self.post(body,1+i%2)
        with ThreadPoolExecutor(max_workers=6) as pool: results=list(pool.map(run,range(6)))
        self.assertTrue(all(r.status_code==200 for r in results),[r.text for r in results])
        self.assertEqual(self.count(TestCase),1);self.assertEqual(self.count(ExecRun),6)
        body=source("shared-retry")
        with ThreadPoolExecutor(max_workers=6) as pool: results=list(pool.map(lambda _:self.post(body),range(6)))
        self.assertTrue(all(r.status_code==200 for r in results),[r.text for r in results])
        self.assertEqual(self.count(ExecRun),7)
    def test_history_pages_filters_light_payload_and_cross_page_retry(self):
        cid=self.baseline()
        with self.factory() as db:
            for n in range(44):
                db.add(ExecRun(project_id=1,test_case_id=cid,runner="mac" if n%2 else "windows",status="passed",verdict="pass",kind="gui",
                               payload=json.dumps({"title":"x","large":"x"*20000}),report="[]",batch_id="shared",retry_of=1 if n==43 else None))
            db.commit()
        def get(**kwargs):return self.client.get('/api/exec-queue/history',params={"project_id":1,**kwargs})
        pages=[get(page=i).json()["data"] for i in (1,2,3)]
        self.assertEqual([len(p["items"]) for p in pages],[20,20,5])
        self.assertTrue(all(p["total"]==45 for p in pages))
        ids=[r["run_id"] for p in pages for r in p["items"]];self.assertEqual(len(set(ids)),45)
        self.assertTrue(next(r for r in pages[2]["items"] if r["run_id"]==1)["superseded"])
        self.assertNotIn("payload",pages[0]["items"][0]);self.assertNotIn("report",pages[0]["items"][0])
        self.assertEqual(get(page=1,runner="mac").json()["data"]["total"],22)
        self.assertEqual(get(page=1,batch_id="shared").json()["data"]["total"],44)
        self.assertEqual(get(page=1,verdict="fail").json()["data"]["total"],0)
        self.assertEqual(get(page=4).json()["data"]["items"],[])
        self.assertEqual(get(page=0).status_code,422);self.assertEqual(get(page=1,page_size=21).status_code,422)
        self.assertIsInstance(get(summary=True).json()["data"],list)
        # Same existing case endpoint serves both libraries, including adopted filter and offsets.
        with self.factory() as db:
            first=db.get(TestCase,cid)
            for n in range(44):
                db.add(TestCase(ai_task_id=first.ai_task_id,project_id=1,title=f"case-{n}",review_status="adopted"))
            db.commit()
        for adopted,total in ((False,45),(True,44)):
            query={"project_id":1,"limit":20,"offset":20}
            if adopted:query["review_status"]="adopted"
            data=self.client.get('/api/ai/cases',params=query).json()["data"]
            self.assertEqual(data["total"],total);self.assertEqual(len(data["items"]),20)


if __name__ == "__main__": unittest.main()
