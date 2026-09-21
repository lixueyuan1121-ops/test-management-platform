"""Run: cd backend; python -m scripts.test_verified_import (isolated SQLite)."""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api.verified_import import router
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import Project, RunnerDevice, TestCase, ExecRun, Requirement
from app.schemas.verified_import import VerifiedImport

def main():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine)
    with factory() as db:
        db.add(Project(id=1,name="test",code="test"))
        db.add(RunnerDevice(id=1,owner_id=1,runner_id="own",name="own",token="test"))
        db.add(RunnerDevice(id=2,owner_id=2,runner_id="other",name="other",token="other"))
        db.commit()
    app=FastAPI();app.include_router(router)
    def database():
        with factory() as db: yield db
    app.dependency_overrides[get_db]=database
    admin=SimpleNamespace(id=1,is_platform_admin=True)
    app.dependency_overrides[get_current_user]=lambda:admin
    client=TestClient(app)
    script=[{"action":"connect","target":{},"args":{},"desc":"connect"},
            {"action":"assert_text","target":{"selector":"#result","frame":"shell"},"args":{"expected":"done","contains":True},"desc":"assert result"}]
    body={"project_id":1,"runner_device_id":1,"external_id":"test-import-001","requirement":"test requirement",
          "cases":[{"title":"main flow","steps":"connect and check","expected":"done","script":script,
                    "report":[{"action":"connect","ok":True},{"action":"assert_text","ok":True,"check":{"pass":True,"actual":"done","expected":"done"}}],
                    "verdict":"pass","executor":"test executor","environment":"test only","scope":"test only",
                    "finished_at":datetime.now(timezone.utc).isoformat(),"duration_ms":100}]}
    r=client.post("/api/verified-imports",json=body)
    assert r.status_code==200,r.text
    receipt=r.json()["data"];assert not receipt["reused"]
    r2=client.post("/api/verified-imports",json=body)
    assert r2.json()["data"]["reused"] and r2.json()["data"]["records"]==receipt["records"]
    changed=copy.deepcopy(body);changed["cases"][0]["title"]="changed"
    assert client.post("/api/verified-imports",json=changed).status_code==409
    for change in ("failed","incomplete","wrong_action","invalid_script","wrong_device"):
        candidate=copy.deepcopy(body);candidate["external_id"]="test-"+change
        c=candidate["cases"][0]
        if change=="failed":c["report"][1]["ok"]=False
        elif change=="incomplete":c["report"].pop()
        elif change=="wrong_action":c["report"][1]["action"]="click"
        elif change=="invalid_script":c["script"][1]["target"]={"key":"nonexistent"}
        else:candidate["runner_device_id"]=2
        res=client.post("/api/verified-imports",json=candidate)
        assert res.status_code in (400,422),(change,res.text)
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=3,is_platform_admin=False)
    assert client.post("/api/verified-imports",json=body).status_code==403
    app.dependency_overrides.clear()
    assert client.post("/api/verified-imports",json=body).status_code in (401,403)
    with factory() as db:
        assert db.query(TestCase).count()==1 and db.query(ExecRun).count()==1 and db.query(Requirement).count()==1
        run=db.query(ExecRun).one()
        assert "外部实测导入" in run.reason and run.runner=="own"
        assert db.query(TestCase).one().review_status=="pending"
    print("PASS: import/readback, idempotence, conflict, failed/incomplete/mismatched evidence, invalid selectors, device ownership, project permission, authentication, atomic rejection")
if __name__=="__main__":main()
