"""签名校验：输出解析 + 上传→认领→下载→回传→查询 全链路 + 鉴权/收口。
运行: cd backend && python -m scripts.test_signcheck  (需 httpx)
"""
import base64
import hashlib
import json
from io import BytesIO
from zipfile import ZipFile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import signcheck as api
from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.main import app
from app.models import SignCheck, SignCheckAgent, User
from app.services import signcheck as svc

eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(eng)
S = sessionmaker(bind=eng)
seed = S()
seed.add(User(id=1, username="admin", password_hash="x", name="管理员", is_platform_admin=True))
seed.add(User(id=2, username="bob", password_hash="x", name="Bob"))
seed.commit()
seed.close()


def _get_db():
    db = S()
    try:
        yield db
    finally:
        db.close()


current = SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_current_user] = lambda: current
app.dependency_overrides[api.upload_user_id] = lambda: current.id
app.dependency_overrides[get_db] = _get_db
api.SessionLocal = S                                   # 上传走的是模块内直连会话
svc.STORAGE_DIR = Path(tempfile.mkdtemp(prefix="signcheck-test-"))
client = TestClient(app)

AGENT = {"Authorization": "Bearer agent-secret"}
OCTET = {"Content-Type": "application/octet-stream"}

GOOD = (
    "<<*********************FilePath:C:\\Temp\\signcheck-1\\setup.exe\r\n"
    "EmbeddedSign(微软数字签名)->\t签名状态：签名有效\t\r\n"
    "Old360Sign(360旧签名)->\t\t签名状态：没有签名\t\r\n"
    "New360Sign(360超级签名)->\t签名状态：签名有效\t\r\n"
    "S3360Sign(s3签名)->\t\t签名状态：签名有效\t\r\n"
    "*********************FilePath:C:\\Temp\\signcheck-1\\setup.exe>>\r\n"
)


def _states(text):
    return [s["state"] for s in svc.parse_output(text)]


def test_parse():
    sigs = svc.parse_output(GOOD)
    assert [s["kind"] for s in sigs] == list(svc.KINDS)
    assert _states(GOOD) == ["valid", "unsigned", "valid", "valid"]
    assert svc.summarize(sigs, 0, GOOD) == ("passed", None)

    # 通过的条件：微软签名 + S3 都有效；旧签名/超级签名不作要求
    only_two = GOOD.replace("New360Sign(360超级签名)->\t签名状态：签名有效", "New360Sign(360超级签名)->\t签名状态：没有签名")
    assert _states(only_two) == ["valid", "unsigned", "unsigned", "valid"]
    assert svc.summarize(svc.parse_output(only_two), 0, only_two)[0] == "passed"
    # 两项必需签名里任一「无签名」→ 验签不通过
    no_s3 = GOOD.replace("S3360Sign(s3签名)->\t\t签名状态：签名有效", "S3360Sign(s3签名)->\t\t签名状态：没有签名")
    assert svc.summarize(svc.parse_output(no_s3), 0, no_s3)[0] == "rejected"
    no_ms = GOOD.replace("EmbeddedSign(微软数字签名)->\t签名状态：签名有效", "EmbeddedSign(微软数字签名)->\t签名状态：没有签名")
    assert svc.summarize(svc.parse_output(no_ms), 0, no_ms)[0] == "rejected"
    assert svc.summarize(svc.parse_output(GOOD.replace("签名有效", "没有签名")), 0, "")[0] == "rejected"
    bad = GOOD.replace("New360Sign(360超级签名)->\t签名状态：签名有效", "New360Sign(360超级签名)->\t签名状态：签名无效")
    assert svc.summarize(svc.parse_output(bad), 0, bad)[0] == "invalid"
    exp = GOOD.replace("S3360Sign(s3签名)->\t\t签名状态：签名有效", "S3360Sign(s3签名)->\t\t签名状态：签名过期")
    assert svc.summarize(svc.parse_output(exp), 0, exp)[0] == "expired"

    # 某一项校验出错：保留失败说明，整体记 partial
    err = GOOD.replace("EmbeddedSign(微软数字签名)->\t签名状态：签名有效", "EmbeddedSign(微软数字签名)->\tVerifyTrust调用失败!")
    sigs = svc.parse_output(err)
    assert sigs[0]["state"] == "error" and "VerifyTrust" in sigs[0]["detail"], sigs[0]
    assert svc.summarize(sigs, 0, err)[0] == "partial"

    uns = GOOD.replace("Old360Sign(360旧签名)->\t\t签名状态：没有签名", "Old360Sign(360旧签名)->\t\t签名状态：不支持的签名状态")
    assert _states(uns)[1] == "unsupported"
    assert svc.summarize(svc.parse_output(uns), 0, uns)[0] == "passed"       # 旧签名状态不影响结论
    uns3 = GOOD.replace("S3360Sign(s3签名)->\t\t签名状态：签名有效", "S3360Sign(s3签名)->\t\t签名状态：不支持的签名状态")
    assert svc.summarize(svc.parse_output(uns3), 0, uns3)[0] == "partial"    # 必需项没拿到明确状态

    # 输出缺行 → missing → partial
    assert _states("\r\n".join(GOOD.split("\r\n")[:3])) == ["valid", "unsigned", "missing", "missing"]

    # 完全没有可识别状态：按退出码/输出给排障提示
    assert svc.summarize(svc.parse_output(""), 0xC0000135, "") == ("error", svc.MSG_RUNTIME)
    assert svc.summarize(svc.parse_output(""), -1073741515, "") == ("error", svc.MSG_RUNTIME)   # 同一退出码的有符号形式
    garbled = "EmbeddedSign(????)->\t????\r\n"
    assert svc.summarize(svc.parse_output(garbled), 0, garbled) == ("error", svc.MSG_LOCALE)

    # GBK 字节解码（校验机按 ANSI 代码页输出）
    assert svc.decode_output(GOOD.encode("gbk")) == GOOD
    assert svc.decode_output(b"") == ""


def _upload(content=b"MZ\x90\x00fake-pe", name="setup 安装包.exe", **kw):
    return client.post("/api/signcheck", params={"filename": name}, content=content, headers=OCTET, **kw)


def test_disabled():
    settings.SIGNCHECK_AGENT_TOKEN = ""
    assert client.get("/api/signcheck/status").json()["data"]["enabled"] is False
    assert _upload().status_code == 503
    assert client.get("/api/signcheck/agent/next", headers=AGENT).status_code == 401   # 空 token 不放行


def test_flow():
    settings.SIGNCHECK_AGENT_TOKEN = "agent-secret"
    settings.CI_HOOK_TOKEN = "ci-secret"
    content = b"MZ" + bytes(range(256)) * 400
    r = _upload(content)
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    row = r.json()["data"]
    cid = row["id"]
    assert row["status"] == "pending" and row["filename"] == "setup 安装包.exe"
    assert row["file_size"] == len(content) and row["sha256"] == hashlib.sha256(content).hexdigest()
    assert row["created_by_name"] == "管理员"
    assert svc.file_path(cid).read_bytes() == content
    assert not list(svc.STORAGE_DIR.glob("*.part")), "临时分片文件应已改名"

    st = client.get("/api/signcheck/status").json()["data"]
    assert st["enabled"] and not st["agent_online"] and st["queued"] == 1

    # agent 鉴权
    assert client.get("/api/signcheck/agent/next").status_code in (401, 403)
    assert client.get("/api/signcheck/agent/next", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(f"/api/signcheck/agent/{cid}/file", headers=AGENT).status_code == 404   # 未认领不可下载

    job = client.get("/api/signcheck/agent/next", params={"agent": "win-a"}, headers=AGENT).json()["data"]
    assert job == {"id": cid, "filename": "setup 安装包.exe", "file_size": len(content),
                   "sha256": hashlib.sha256(content).hexdigest()}, job
    assert client.get("/api/signcheck/agent/next", params={"agent": "win-b"}, headers=AGENT).json()["data"] is None
    st = client.get("/api/signcheck/status").json()["data"]
    assert st["agent_online"] and st["agents"] == ["win-a", "win-b"]

    got = client.get(f"/api/signcheck/agent/{cid}/file", headers=AGENT)
    assert got.status_code == 200 and got.content == content

    d = client.get(f"/api/signcheck/{cid}").json()["data"]
    assert d["status"] == "running" and d["agent"] == "win-a" and d["agent_online"] is True

    body = {"agent": "win-a", "exit_code": 0, "duration_ms": 812,
            "stdout_b64": base64.b64encode(GOOD.encode("gbk")).decode()}
    r = client.post(f"/api/signcheck/agent/{cid}/result", json=body, headers=AGENT)
    assert r.json()["data"] == {"id": cid, "status": "completed", "verdict": "passed"}, r.text
    assert client.post(f"/api/signcheck/agent/{cid}/result", json=body, headers=AGENT).status_code == 409
    assert not svc.file_path(cid).exists(), "出结果后应删除上传文件"

    d = client.get(f"/api/signcheck/{cid}").json()["data"]
    assert d["status"] == "completed" and d["verdict"] == "passed" and d["duration_ms"] == 812
    assert [s["state"] for s in d["signatures"]] == ["valid", "unsigned", "valid", "valid"]
    assert "签名有效" in d["stdout"] and d["exit_code"] == 0 and d["finished_at"].endswith("Z")

    lst = client.get("/api/signcheck", params={"keyword": "安装包"}).json()["data"]
    assert lst["total"] == 1 and "stdout" not in lst["items"][0]
    assert client.get("/api/signcheck", params={"keyword": "不存在的文件"}).json()["data"]["total"] == 0
    assert client.get("/api/signcheck", params={"verdict": "invalid"}).json()["data"]["total"] == 0
    return cid


def test_agent_error_and_locale():
    cid = _upload(b"abc", "a.dll").json()["data"]["id"]
    client.get("/api/signcheck/agent/next", headers=AGENT)
    r = client.post(f"/api/signcheck/agent/{cid}/result", headers=AGENT,
                    json={"error": "原校验工具在 120 秒内未完成，请重试。"})
    assert r.json()["data"]["status"] == "failed"
    d = client.get(f"/api/signcheck/{cid}").json()["data"]
    assert d["verdict"] == "error" and "120 秒" in d["error"] and len(d["signatures"]) == 4

    cid = _upload(b"abc", "b.dll").json()["data"]["id"]
    client.get("/api/signcheck/agent/next", headers=AGENT)
    client.post(f"/api/signcheck/agent/{cid}/result", headers=AGENT,
                json={"exit_code": 0, "stdout_b64": base64.b64encode(b"EmbeddedSign(\xff\xfe)->\t??\r\n").decode()})
    d = client.get(f"/api/signcheck/{cid}").json()["data"]
    assert d["status"] == "failed" and d["error"] == svc.MSG_LOCALE, d


def test_validation():
    assert _upload(b"").status_code == 422                          # 空文件
    assert client.post("/api/signcheck", content=b"x", headers=OCTET).status_code == 422   # 无文件名
    # 路径成分被剥掉，只留文件名本体
    r = client.post("/api/signcheck", params={"filename": "..\\..\\evil/..\\x.exe"}, content=b"x", headers=OCTET)
    assert r.json()["data"]["filename"] == "x.exe", r.text
    # 文件名也可走请求头（URL 编码）
    r = client.post("/api/signcheck", content=b"x", headers={**OCTET, "X-File-Name": "%E5%AE%89%E8%A3%85.exe"})
    assert r.json()["data"]["filename"] == "安装.exe", r.text

    old = settings.SIGNCHECK_MAX_MB
    settings.SIGNCHECK_MAX_MB = 1
    try:
        assert _upload(b"x" * (1024 * 1024 + 1), "big.exe").status_code == 413
        assert not list(svc.STORAGE_DIR.glob("*.part")), "超限的半截文件不应留盘"
    finally:
        settings.SIGNCHECK_MAX_MB = old

    oldq = settings.SIGNCHECK_MAX_QUEUE
    settings.SIGNCHECK_MAX_QUEUE = 1
    try:
        assert _upload(b"x", "q.exe").status_code == 429            # 上面两条 pending 已占满
    finally:
        settings.SIGNCHECK_MAX_QUEUE = oldq


def test_reap_and_delete(done_id):
    db = S()
    pend = db.query(SignCheck).filter_by(status="pending").order_by(SignCheck.id).all()
    assert len(pend) == 2
    pend[0].created_at = datetime.utcnow() - timedelta(minutes=settings.SIGNCHECK_PENDING_TTL_MINUTES + 1)
    pend[1].status, pend[1].started_at = "running", datetime.utcnow() - timedelta(minutes=11)
    ids = [p.id for p in pend]
    db.commit()
    db.close()
    a = client.get(f"/api/signcheck/{ids[0]}").json()["data"]
    b = client.get(f"/api/signcheck/{ids[1]}").json()["data"]
    assert a["status"] == "failed" and "未在线" in a["error"], a
    assert b["status"] == "failed" and "超时未回传" in b["error"], b
    assert not svc.file_path(ids[0]).exists() and not svc.file_path(ids[1]).exists()

    # 删除：非本人非管理员 403；本人/管理员可删
    global current
    current.id, current.is_platform_admin = 2, False
    assert client.delete(f"/api/signcheck/{done_id}").status_code == 403
    mine = _upload(b"x", "mine.exe").json()["data"]
    assert mine["created_by"] == 2 and mine["created_by_name"] == "Bob"
    assert client.get("/api/signcheck", params={"mine": True}).json()["data"]["total"] == 1
    assert client.delete(f"/api/signcheck/{mine['id']}").json()["code"] == 0
    assert not svc.file_path(mine["id"]).exists()
    current.id, current.is_platform_admin = 1, True
    assert client.delete(f"/api/signcheck/{done_id}").json()["code"] == 0
    assert client.get(f"/api/signcheck/{done_id}").status_code == 404


def test_resume_after_agent_restart():
    """校验到一半 agent 被重启：它再来轮询时应拿回同一条，而不是让记录卡在「校验中」。"""
    cid = _upload(b"MZresume", "resume.exe").json()["data"]["id"]
    job = client.get("/api/signcheck/agent/next", params={"agent": "win-r"}, headers=AGENT).json()["data"]
    assert job["id"] == cid
    # 别的校验机拿不到它
    assert client.get("/api/signcheck/agent/next", params={"agent": "win-other"}, headers=AGENT).json()["data"] is None
    again = client.get("/api/signcheck/agent/next", params={"agent": "win-r"}, headers=AGENT).json()["data"]
    assert again == job, again
    # 干活期间不轮询：心跳过期也仍算在线
    db = S()
    db.query(SignCheckAgent).update({SignCheckAgent.last_seen_at: datetime.utcnow() - timedelta(minutes=5)})
    db.commit()
    db.close()
    st = client.get("/api/signcheck/status").json()["data"]
    assert st["agent_online"] and "win-r" in st["agents"], st
    assert client.get(f"/api/signcheck/agent/{cid}/file", headers=AGENT).content == b"MZresume"
    r = client.post(f"/api/signcheck/agent/{cid}/result", headers=AGENT,
                    json={"agent": "win-r", "exit_code": 0, "stdout_b64": base64.b64encode(GOOD.encode("gbk")).decode()})
    assert r.json()["data"]["verdict"] == "passed"
    assert client.get("/api/signcheck/agent/next", params={"agent": "win-r"}, headers=AGENT).json()["data"] is None
    # 心跳全部过期且没有在跑的校验 → 离线；再轮询一次 → 在线
    db = S()
    db.query(SignCheckAgent).update({SignCheckAgent.last_seen_at: datetime.utcnow() - timedelta(minutes=5)})
    db.commit()
    db.close()
    assert client.get("/api/signcheck/status").json()["data"]["agent_online"] is False
    client.get("/api/signcheck/agent/next", params={"agent": "win-r"}, headers=AGENT)
    assert client.get("/api/signcheck/status").json()["data"]["agents"] == ["win-r"]

    # 文件已不在的孤儿：判失败而不是反复重派
    cid = _upload(b"MZgone", "gone.exe").json()["data"]["id"]
    client.get("/api/signcheck/agent/next", params={"agent": "win-r"}, headers=AGENT)
    svc.remove_file(cid)
    assert client.get("/api/signcheck/agent/next", params={"agent": "win-r"}, headers=AGENT).json()["data"] is None
    d = client.get(f"/api/signcheck/{cid}").json()["data"]
    assert d["status"] == "failed" and "中断" in d["error"], d


def test_resync_verdicts():
    """历史记录按新口径重算：旧口径下「只有微软签名有效」记为 passed，应改成 rejected。"""
    import json
    db = S()
    sigs = svc.parse_output(GOOD.replace("S3360Sign(s3签名)->\t\t签名状态：签名有效", "S3360Sign(s3签名)->\t\t签名状态：没有签名"))
    old = SignCheck(filename="old.exe", file_size=1, status="completed", verdict="passed",
                    signatures_json=json.dumps(sigs, ensure_ascii=False), exit_code=0)
    db.add(old)
    db.commit()
    assert svc.resync_verdicts(db) == 1
    db.refresh(old)
    assert old.verdict == "rejected"
    assert svc.resync_verdicts(db) == 0, "应幂等"
    db.delete(old)
    db.commit()
    db.close()


def test_hook():
    url = "/api/hooks/signcheck"
    assert client.post(url, params={"filename": "ci.exe"}, content=b"x", headers=OCTET).status_code == 401
    r = client.post(url, params={"filename": "ci.exe"}, content=b"x", headers={**OCTET, "X-CI-Token": "ci-secret"})
    row = r.json()["data"]
    assert row["status"] == "pending" and row["source"] == "api" and row["created_by"] is None, r.text
    assert client.get(f"{url}/{row['id']}").status_code == 401
    job = client.get("/api/signcheck/agent/next", headers=AGENT).json()["data"]
    assert job["id"] == row["id"]
    client.post(f"/api/signcheck/agent/{row['id']}/result", headers=AGENT,
                json={"exit_code": 0, "stdout_b64": base64.b64encode(GOOD.replace("签名有效", "签名无效").encode("gbk")).decode()})
    got = client.get(f"{url}/{row['id']}", headers={"X-CI-Token": "ci-secret"}).json()["data"]
    assert got["status"] == "completed" and got["verdict"] == "invalid" and "stdout" in got
    # wait>0 且无校验机处理：到点返回当前状态而不是挂死
    r = client.post(url, params={"filename": "w.exe", "wait": 1}, content=b"x", headers={**OCTET, "X-CI-Token": "ci-secret"})
    assert r.json()["data"]["status"] == "pending"
    settings.CI_HOOK_TOKEN = ""
    assert client.post(url, params={"filename": "ci.exe"}, content=b"x", headers={**OCTET, "X-CI-Token": ""}).status_code == 403


def legacy_native_job(params, content):
    # A record created by the release that dispatched macOS jobs to agents.
    with S() as db:
        r = SignCheck(platform="macos", created_by=current.id, filename=params["filename"],
                      team_id=params.get("team_id"), file_size=len(content), status="pending")
        db.add(r)
        db.flush()
        svc.file_path(r.id).write_bytes(content)
        db.commit()
        return api._to_out(db, r)


def test_mac_notary():
    settings.SIGNCHECK_AGENT_TOKEN = "agent-secret"
    settings.SIGNCHECK_MAC_AGENT_TOKEN = "mac-test"
    mac_auth = {"Authorization": "Bearer mac-test"}
    params = {"platform": "macos", "filename": "应用 1.zip", "team_id": "AB12345678"}
    assert client.post("/api/signcheck", params={**params, "filename": "app.exe"}, content=b"x").status_code == 422
    assert client.post("/api/signcheck", params={**params, "team_id": "invalid"}, content=b"x").status_code == 422
    row = legacy_native_job(params, b"zip")
    cid = row["id"]
    assert row["team_id"] == params["team_id"] and row["platform"] == "macos"
    assert client.get("/api/signcheck/agent/next", headers=AGENT).json()["data"] is None
    assert client.get("/api/signcheck/status?platform=macos").json()["data"]["agent_online"] is False
    assert client.get("/api/signcheck/agent/next?platform=macos", headers=AGENT).status_code == 401
    job = client.get("/api/signcheck/agent/next?agent=shared&platform=macos", headers=mac_auth).json()["data"]
    assert job["id"] == cid and job["platform"] == "macos" and job["team_id"] == params["team_id"]
    assert client.get("/api/signcheck/agent/next?agent=shared", headers=AGENT).json()["data"] is None
    assert client.post(f"/api/signcheck/agent/{cid}/heartbeat?agent=shared&platform=macos", headers=mac_auth).status_code == 200
    assert client.get("/api/signcheck/status?platform=macos").json()["data"]["agents"] == ["shared"]
    assert client.get("/api/signcheck?platform=macos").json()["data"]["total"] == 1
    assert client.get(f"/api/signcheck/agent/{cid}/file", headers=AGENT).status_code == 404
    assert client.get(f"/api/signcheck/agent/{cid}/file?agent=shared&platform=macos", headers=mac_auth).content == b"zip"
    url = f"/api/signcheck/agent/{cid}/result?platform=macos"
    report = {"target": "/private/tmp/test/" + row["filename"], "teamId": row["team_id"], "checked": 1,
              "failed": 0, "warnings": [], "results": [{"path": "app/cli", "executable": True,
              "ok": True, "problems": [], "notes": ["get-task-allow"], "authority": None, "team": row["team_id"]}]}
    from app.services.mac_notary import summarize
    def verdict(data, code):
        return summarize(data, code, row["filename"], row["team_id"])[0]
    assert verdict(report, 0) == "passed"
    assert verdict({**report, "warnings": ["未覆盖"]}, 1) == "partial"
    assert verdict({**report, "checked": 0, "results": []}, 1) == "partial"
    assert verdict({**report, "target": "other.zip"}, 0) == "error"
    assert verdict({**report, "failed": 1}, 1) == "error"
    assert verdict(report, 1) == "error" and verdict(None, 2) == "error"
    report["results"][0].update(ok=False, problems=["临时签名"])
    report.update(warnings=["跳过内层"], failed=1)
    body = {"agent": "shared", "exit_code": 1, "report": report,
            "stdout_b64": base64.b64encode("执行日志".encode()).decode()}
    assert client.post(url, headers=mac_auth, json={**body, "agent": "other"}).status_code == 409
    assert client.post(url, headers=mac_auth, json=body).json()["data"]["verdict"] == "invalid"
    detail = client.get(f"/api/signcheck/{cid}").json()["data"]
    assert detail["report"] == report and detail["stdout"] == "执行日志"
    assert not svc.file_path(cid).exists()


def test_personal_mac_worker():
    settings.SIGNCHECK_MAC_AGENT_TOKEN = ""
    settings.SIGNCHECK_AGENT_TOKEN = "agent-secret"
    downloaded = client.get("/api/signcheck/mac-agent.zip")
    assert downloaded.status_code == 200 and "no-store" in downloaded.headers["cache-control"]
    with ZipFile(BytesIO(downloaded.content)) as bundle:
        assert bundle.testzip() is None
        connection = json.loads(bundle.read("qalab-mac-notary/connection.json"))
        assert connection["url"] == (settings.PLATFORM_BASE_URL or "https://qalab.claw.qihoo.net").rstrip("/")
        assert bundle.getinfo("qalab-mac-notary/启动预检.command").external_attr >> 16 & 0o100
    personal = {"Authorization": "Bearer " + connection["token"]}
    params = {"platform": "macos", "filename": "my.zip"}
    # An earlier package from someone else must remain available to its owner.
    current.id = 2
    other = legacy_native_job(params, b"other")["id"]
    current.id = 1
    own = legacy_native_job(params, b"own")["id"]
    poll = "/api/signcheck/agent/next?platform=macos&agent=personal"
    assert client.get(poll, headers=personal).json()["data"]["id"] == own
    assert "personal" in client.get("/api/signcheck/status?platform=macos").json()["data"]["agents"]
    current.id = 2
    assert "personal" not in client.get("/api/signcheck/status?platform=macos").json()["data"]["agents"]
    current.id = 1
    assert "personal" not in client.get("/api/signcheck/status").json()["data"]["agents"]
    assert client.get("/api/signcheck/agent/next", headers=personal).status_code == 401
    prefix = f"/api/signcheck/agent/{other}"
    assert client.get(prefix + "/file?platform=macos&agent=personal", headers=personal).status_code == 404
    assert client.post(prefix + "/heartbeat?platform=macos&agent=personal", headers=personal).status_code == 409
    assert client.post(prefix + "/result?platform=macos", json={}, headers=personal).status_code == 404
    assert client.get("/api/signcheck/status?platform=macos").json()["data"]["enabled"]
    client.delete(f"/api/signcheck/{own}")
    client.delete(f"/api/signcheck/{other}")


def test_server_static():
    import struct
    from app.services.mac_static import jobs
    settings.SIGNCHECK_MAC_AGENT_TOKEN = ""
    settings.SIGNCHECK_AGENT_TOKEN = ""
    settings.CI_HOOK_TOKEN = "ci-secret"
    params = {"platform": "macos", "filename": "嵌套.zip"}
    def zipped(name, data):
        buf = BytesIO()
        with ZipFile(buf, "w") as z:
            z.writestr(name, data)
        return buf.getvalue()
    unsigned = struct.pack("<8I", 0xfeedfacf, 0x100000c, 0, 2, 0, 0, 0, 0)
    pkg = zipped("inner.whl", zipped("bin/helper", unsigned))
    row = client.post("/api/signcheck", params=params, content=pkg).json()["data"]
    assert row["check_mode"] == "server-static" and row["status"] == "pending"
    assert jobs.process_next(S)
    detail = client.get(f"/api/signcheck/{row['id']}").json()["data"]
    assert detail["verdict"] == "invalid" and detail["report"]["checked"] == 1
    assert detail["report"]["results"][0]["path"].endswith("inner.whl/bin/helper")
    assert not svc.file_path(row["id"]).exists()
    # Interrupted server jobs are reclaimed even without a browser polling them.
    hook = client.post("/api/hooks/signcheck", params=params, content=zipped("a", b"text"),
                       headers={"X-CI-Token": "ci-secret"}).json()["data"]
    with S() as db:
        db.query(SignCheck).filter_by(id=hook["id"]).update(dict(status="running",
            agent="interrupted", started_at=datetime.utcnow() - timedelta(hours=1)))
        db.commit()
    assert jobs.process_next(S) and not jobs.process_next(S)
    final = client.get(f"/api/hooks/signcheck/{hook['id']}", headers={"X-CI-Token": "ci-secret"}).json()["data"]
    assert final["verdict"] == "partial" and final["report"]["checked"] == 0
    assert not svc.file_path(hook["id"]).exists()
    modes = {r["check_mode"] for r in client.get("/api/signcheck", params={"platform": "macos"}).json()["data"]["items"]}
    assert modes == {"server-static", "mac-native"}


def main():
    test_parse()
    test_disabled()
    done_id = test_flow()
    test_agent_error_and_locale()
    test_validation()
    test_reap_and_delete(done_id)
    test_resume_after_agent_restart()
    test_resync_verdicts()
    test_mac_notary()
    test_personal_mac_worker()
    test_hook()
    test_server_static()
    print("OK test_signcheck")


if __name__ == "__main__":
    main()
