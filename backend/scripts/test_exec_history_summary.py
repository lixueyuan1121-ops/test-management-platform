"""Execution history summaries omit heavy snapshots; details load on demand."""
import json
from types import SimpleNamespace
from sqlalchemy import event
from scripts.test_exec_cancel import db, client, engine, app
from app.models import ExecRun, TestCase, ProjectMember
from app.core.deps import get_current_user

def main():
    db.add(TestCase(id=501, project_id=1, ai_task_id=1, title="summary case", exec_kind="gui"))
    db.flush()
    snapshot=json.dumps({"title":"snapshot title","selector_registry":{"large":"x"*32768}})
    report=json.dumps([{"action":"assert_text","ok":True,"check":{"actual":"x"*4096}}])
    active=ExecRun(project_id=1,test_case_id=501,runner="device-1",status="running",payload=snapshot,batch_id="old-active")
    db.add(active);db.flush();active_id=active.id
    for i in range(110):
        db.add(ExecRun(project_id=1,test_case_id=501,runner="device-1",status="passed",verdict="pass",payload=snapshot,report=report,batch_id="new-done"))
    db.commit();db.expire_all()
    statements=[]
    def capture(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith("SELECT"):statements.append(statement)
    event.listen(engine,"before_cursor_execute",capture)
    try:
        summary=client.get("/api/exec-queue/history?project_id=1&summary=true")
    finally:event.remove(engine,"before_cursor_execute",capture)
    assert summary.status_code==200,summary.text
    rows=summary.json()["data"]
    assert len(rows)==100 and rows[0]["run_id"]==active_id
    assert rows[0]["can_cancel"] and rows[0]["title"]=="summary case"
    assert all("payload" not in r and "report" not in r for r in rows)
    assert rows[-1]["has_report"]
    assert len(statements)==1,statements
    assert "exec_run.payload" not in statements[0]
    assert "exec_run.report AS" not in statements[0]
    legacy=client.get("/api/exec-queue/history?project_id=1")
    assert len(summary.content) < len(legacy.content)/20
    filtered=client.get("/api/exec-queue/history?project_id=1&summary=true&status=active").json()["data"]
    assert [r["run_id"] for r in filtered]==[active_id]
    detail=client.get(f'/api/exec-queue/{rows[-1]["run_id"]}').json()["data"]
    assert detail["payload"]["title"]=="snapshot title" and detail["report"][0]["ok"]
    assert client.get("/api/exec-queue/999999").status_code==404
    db.add(ProjectMember(project_id=1,user_id=2,role="guest"));db.commit()
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=2,is_platform_admin=False)
    guest=client.get("/api/exec-queue/history?project_id=1&summary=true").json()["data"]
    assert all(not r["can_cancel"] for r in guest)
    assert client.post(f"/api/exec-queue/{active_id}/cancel").status_code==403
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=3,is_platform_admin=False)
    assert client.get(f"/api/exec-queue/{active_id}").status_code==403
    print(f"PASS history summary: {len(legacy.content)} -> {len(summary.content)} bytes; one query, lazy details, active visibility, permissions")
if __name__=="__main__":main()
