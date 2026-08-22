# 对话测评飞书导出 + multica 推送 实现计划(收官)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平台把 eval_run 结果导出到飞书表(分享链接/耗时/算力豆/正文+判定),异常会话(is_abnormal)推送 multica(可插拔适配器,契约占位)。

**Architecture:** feishu.py 加写回(移植 CLI feishu-sheet.js 的列转换/分区间/token重试,与现有只读并存)。新建 services/multica.py(http/cli/off 适配器)+ api/eval_export.py(POST /feishu、/multica)。导出到用户指定飞书表(非回填原表,eval无飞书锚点)。无 schema 变更。

**Tech Stack:** FastAPI+SQLAlchemy2.0;requests(飞书/multica http);subprocess(multica cli);前端 Vue3+ElementPlus。

**Spec:** `docs/superpowers/specs/2026-08-22-eval-feishu-multica-design.md`

## Global Constraints

- **飞书走"导出到指定表"非回填原表**(eval_query 平台生成、无飞书来源锚点)。
- **不改 eval 数据模型**(pushed_multica/multica_ref 子项0 已建)。不改 exec_queue/gen_testcases/eval 生成·下发·判定。
- **飞书写回移植 CLI 样板**:列字母↔序号(多字母26进制)、非连续列分区间写(不碰中间列)、token 失效码{99991663,99991661,99991668}刷新重试一次、answer 截断45000。
- **multica 契约占位**:适配器接口 push_abnormal_run(run)→ref;config MULTICA_MODE(off/http/cli)默认 off;off 时不推。真契约用户后填,只改组装/解析一处。
- **share_link 推 multica 前校验 http(s) scheme**(补子项3 XSS 写入侧遗留)。
- **鉴权**:导出端点用户 JWT + assert_project_role(admin/member)。
- **本仓库无测试框架**:验证脱机(mock _api_put/mock http·cli/构造 eval_run)。后端 backend/ 下,SQLite。
- 飞书写回复用现有 feishu.py 的 _get_token/_friendly_err;PUT 仿 _api_get。

## 文件结构

- Modify `backend/app/services/feishu.py` — 加 _col_to_num/_num_to_col/_api_put/parse_sheet_url/write_sheet_rows。
- Create `backend/app/services/multica.py` — push_abnormal_run(http/cli/off)。
- Modify `backend/app/core/config.py` + `.env.example` — MULTICA_* 配置。
- Create `backend/app/schemas/eval_export.py` — EvalExportFeishuIn/EvalPushMulticaIn。
- Create `backend/app/api/eval_export.py` — POST /feishu、/multica、GET /multica-pending。
- Modify `backend/app/api/router.py` — 注册。
- Modify `frontend/src/api/index.js` + `frontend/src/views/EvalResults.vue` — 两按钮。

---

### Task 1: 飞书写回(feishu.py 加移植函数)

**Files:**
- Modify: `backend/app/services/feishu.py`(加写回,现有只读函数之后)
- Verify(临时,删): `backend/_verify_feishu_write.py`

**Interfaces:**
- Consumes: 现有 `_get_token()`/`_base()`/`_friendly_err()`/`_fetch_wiki`(wiki get_node 逻辑参考)/`is_configured()`。
- Produces: `_col_to_num(col)->int`/`_num_to_col(n)->str`/`parse_sheet_url(url)->tuple[str,str]`(spreadsheet_token,sheet_id)/`write_sheet_rows(sheet_url, rows, col_map, start_row=2)->int`。

- [ ] **Step 1: 列转换 + PUT 封装 + URL 解析** — `backend/app/services/feishu.py` 末尾加(移植 CLI feishu-sheet.js:30-48/300-320/67-89):
```python
# ---- sheets 写回(导出 eval 结果到飞书表;移植自 ai-eval-cli feishu-sheet.js)----
def _col_to_num(col: str) -> int:
    """列字母→序号(多字母26进制):A=1,Z=26,AA=27。"""
    n = 0
    for ch in str(col).upper():
        c = ord(ch)
        if c < 65 or c > 90:
            continue
        n = n * 26 + (c - 64)
    return n or 1


def _num_to_col(n: int) -> str:
    """序号→列字母(_col_to_num 逆)。"""
    s = ""
    while n > 0:
        r = (n - 1) % 26
        s = chr(65 + r) + s
        n = (n - 1) // 26
    return s


_SHEET_TOKEN_ERR = {99991663, 99991661, 99991668}  # token 失效码(刷新重试)


def _api_put(path: str, body: dict) -> dict:
    """PUT 封装(仿 _api_get);token 失效刷新重试一次。code!=0 抛 ValueError。"""
    def _do():
        resp = requests.put(
            f"{_base()}{path}",
            headers={"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json"},
            json=body, timeout=15,
        )
        return resp.json()
    try:
        data = _do()
    except requests.RequestException as e:
        raise ValueError(f"飞书网络错误：{e}")
    except ValueError:
        raise ValueError("飞书接口返回非 JSON")
    if data.get("code") in _SHEET_TOKEN_ERR:
        _token_cache["token"] = ""  # 清缓存强制刷新
        try:
            data = _do()
        except requests.RequestException as e:
            raise ValueError(f"飞书网络错误：{e}")
    if data.get("code") != 0:
        raise ValueError(_friendly_err(data))
    return data.get("data", {}) or {}


def parse_sheet_url(url: str) -> tuple[str, str]:
    """从飞书 sheets/wiki 链接解析 (spreadsheet_token, sheet_id)。wiki 经 get_node 换 obj_token。"""
    import re as _re
    m_sheet = _re.search(r"[?&]sheet=([A-Za-z0-9]+)", url or "")
    sheet_id = m_sheet.group(1) if m_sheet else "0"
    m = _re.search(r"/sheets/([A-Za-z0-9]+)", url or "")
    if m:
        return m.group(1), sheet_id
    m = _re.search(r"/wiki/([A-Za-z0-9]+)", url or "")
    if m:
        node = _api_get("/open-apis/wiki/v2/spaces/get_node", {"token": m.group(1)}).get("node", {})
        obj = node.get("obj_token")
        if not obj:
            raise ValueError("wiki 节点未关联电子表格")
        return obj, sheet_id
    raise ValueError("无法识别飞书表格链接(支持 /sheets/ 或 /wiki/)")
```

- [ ] **Step 2: write_sheet_rows(分区间写)** — 紧接其后:
```python
_MAX_CELL = 45000  # 飞书单元格约 5 万字符上限


def _column_groups(col_map: dict) -> list:
    """把 {字段:列字母} 按列号排序,相邻列合并成组(非连续列分区间,不碰中间列)。
    返回 [{'start':n,'end':n,'fields':[...]}]。"""
    entries = sorted(({"field": f, "num": _col_to_num(c)} for f, c in col_map.items()), key=lambda e: e["num"])
    groups = []
    for e in entries:
        if groups and e["num"] == groups[-1]["end"] + 1:
            groups[-1]["end"] = e["num"]; groups[-1]["fields"].append(e["field"])
        else:
            groups.append({"start": e["num"], "end": e["num"], "fields": [e["field"]]})
    return groups


def write_sheet_rows(sheet_url: str, rows: list[dict], col_map: dict, start_row: int = 2) -> int:
    """把 rows(每个是 {字段:值} dict)按 col_map(字段→列字母)从 start_row 起逐行写飞书表。
    非连续列分区间写(不碰中间列);answer 等长文本截断。返回写入行数。"""
    if not is_configured():
        raise ValueError("未配置飞书应用凭据(FEISHU_APP_ID/FEISHU_APP_SECRET)")
    token, sheet_id = parse_sheet_url(sheet_url)
    groups = _column_groups(col_map)
    for i, row in enumerate(rows):
        real_row = start_row + i
        for g in groups:
            vals = []
            for f in g["fields"]:
                v = row.get(f, "")
                v = "" if v is None else str(v)
                if len(v) > _MAX_CELL:
                    v = v[:_MAX_CELL]
                vals.append(v)
            rng = f"{sheet_id}!{_num_to_col(g['start'])}{real_row}:{_num_to_col(g['end'])}{real_row}"
            _api_put(f"/open-apis/sheets/v2/spreadsheets/{token}/values",
                     {"valueRange": {"range": rng, "values": [vals]}})
    return len(rows)
```

- [ ] **Step 3: 验证脚本(mock PUT)** — `backend/_verify_feishu_write.py`:
```python
"""Task1 验证:列转换往返/分区间/write_sheet_rows 组装。backend/下运行(mock _api_put/_get_token)。"""
from app.services import feishu
# 列转换往返
for col in ["A","Z","AA","AZ","BA"]:
    assert feishu._num_to_col(feishu._col_to_num(col)) == col, col
assert feishu._col_to_num("A")==1 and feishu._col_to_num("AA")==27
# 分区间:C/D/E/F 一组 + H 单独
g = feishu._column_groups({"a":"C","b":"D","c":"E","d":"F","e":"H"})
assert len(g)==2, g
assert g[0]["start"]==3 and g[0]["end"]==6 and g[1]["start"]==8
# write_sheet_rows 组装(mock 捕获 PUT)
puts = []
feishu._api_put = lambda path, body: (puts.append((path, body)) or {})
feishu.is_configured = lambda: True
feishu.parse_sheet_url = lambda u: ("TOK", "sht1")
n = feishu.write_sheet_rows("url", [{"a":"link1","e":"ans1"},{"a":"link2","e":"ans2"}], {"a":"C","e":"H"}, start_row=2)
assert n==2
# 两行×两组(C,H不连续→分区间)=4 次 PUT
assert len(puts)==4, len(puts)
# 第一行 C 列 range
assert "sht1!C2:C2" in puts[0][1]["valueRange"]["range"] and puts[0][1]["valueRange"]["values"]==[["link1"]]
assert "sht1!H2:H2" in puts[1][1]["valueRange"]["range"] and puts[1][1]["valueRange"]["values"]==[["ans1"]]
print("OK: 列转换往返/分区间(C-F+H)/write_sheet_rows 组装 正常")
```

- [ ] **Step 4: 跑** — `python _verify_feishu_write.py`(backend/下),末行 OK。依赖缺失先 pip install。CreateFile Error:5 忽略。

- [ ] **Step 5: 删脚本** — `rm backend/_verify_feishu_write.py`

- [ ] **Step 6: 提交**
```bash
git add backend/app/services/feishu.py
git commit -m "feat(eval): feishu.py 加 sheets 写回(导出到飞书表)

移植 CLI feishu-sheet.js:列字母↔序号、非连续列分区间写(不碰中间列)、
token失效刷新重试、answer截断。parse_sheet_url(sheets/wiki)+write_sheet_rows。与只读并存。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: multica 适配器 + config

**Files:**
- Create: `backend/app/services/multica.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Verify(临时,删): `backend/_verify_multica.py`

**Interfaces:**
- Produces: `push_abnormal_run(run) -> str | None`(推送异常 run,返回 multica ref;off/未配返回 None;失败抛异常)。`_safe_link(u)->str|None`。

- [ ] **Step 1: config 加 MULTICA_*** — `backend/app/core/config.py` 的 FEISHU_* 附近加:
```python
    # multica(异常会话详细分析平台)对接。契约待细化,默认 off 不推。
    MULTICA_MODE: str = "off"          # off / http / cli
    MULTICA_URL: str = ""              # http 模式:创建分析任务的 endpoint
    MULTICA_TOKEN: str = ""            # http 模式:Bearer token(如需)
    MULTICA_CLI_TEMPLATE: str = ""     # cli 模式:命令模板,如 'multica push --link {share_link} --run {run_id}'
```
`.env.example` 加(注释说明契约待填):
```
# multica 推送(异常会话→详细分析)。契约待定,默认 off。
# http 模式:MULTICA_MODE=http + MULTICA_URL + MULTICA_TOKEN
# cli 模式:MULTICA_MODE=cli + MULTICA_CLI_TEMPLATE(占位 {share_link}/{run_id}/{session_id})
MULTICA_MODE=off
MULTICA_URL=
MULTICA_TOKEN=
MULTICA_CLI_TEMPLATE=
```

- [ ] **Step 2: multica.py** — `backend/app/services/multica.py`:
```python
"""multica(异常会话详细分析平台)推送适配器。

可插拔:MULTICA_MODE=off/http/cli。契约占位——用户填 MULTICA_URL/CLI_TEMPLATE 即用。
push_abnormal_run(run):组装 {share_link,session_id,verdict_reason,run_id,...} 发 multica,返回任务 ref。
share_link 推前校验 http(s)(补子项3 XSS 写入侧:外发也校验)。
"""
import logging
import re
import shlex
import subprocess

import requests

from app.core.config import settings

logger = logging.getLogger("test_platform")


def _safe_link(u):
    """只放行 http(s) 链接,否则 None(防把 javascript:/file: 等外发)。"""
    return u if isinstance(u, str) and re.match(r"^https?://", u, re.I) else None


def _payload(run) -> dict:
    return {
        "run_id": run.id,
        "project_id": run.project_id,
        "share_link": _safe_link(run.share_link),
        "artifact_share_link": _safe_link(run.artifact_share_link),
        "session_id": run.session_id,
        "verdict": run.verdict,
        "verdict_reason": run.verdict_reason,
    }


def push_abnormal_run(run) -> str | None:
    """推一条异常 run 到 multica。off/未配→None;http/cli 按 config;失败抛异常(端点捕获)。"""
    mode = (settings.MULTICA_MODE or "off").lower()
    if mode == "off":
        return None
    payload = _payload(run)
    if mode == "http":
        if not settings.MULTICA_URL:
            raise ValueError("MULTICA_MODE=http 但未配 MULTICA_URL")
        headers = {"Content-Type": "application/json"}
        if settings.MULTICA_TOKEN:
            headers["Authorization"] = f"Bearer {settings.MULTICA_TOKEN}"
        resp = requests.post(settings.MULTICA_URL, json=payload, headers=headers, timeout=15)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        # 契约占位:尽力从返回取任务 id/链接作 ref;拿不到用 http 状态
        ref = (data.get("task_id") or data.get("id") or data.get("url")
               or data.get("data", {}).get("id") if isinstance(data.get("data"), dict) else None)
        return str(ref) if ref else f"http:{resp.status_code}"
    if mode == "cli":
        tmpl = settings.MULTICA_CLI_TEMPLATE
        if not tmpl:
            raise ValueError("MULTICA_MODE=cli 但未配 MULTICA_CLI_TEMPLATE")
        # 占位替换(share_link 可能 None → 空串)
        cmd_str = tmpl.format(
            share_link=payload["share_link"] or "", run_id=run.id,
            session_id=run.session_id or "", project_id=run.project_id)
        proc = subprocess.run(shlex.split(cmd_str), capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise ValueError(f"multica CLI 失败(exit {proc.returncode}): {proc.stderr[:200]}")
        return (proc.stdout or "").strip()[:512] or "cli:ok"
    raise ValueError(f"未知 MULTICA_MODE: {mode}")
```

- [ ] **Step 3: 验证脚本** — `backend/_verify_multica.py`:
```python
"""Task2 验证:multica 适配器 off/http/cli + share_link 校验。backend/下运行。"""
from app.services import multica
from app.core.config import settings

class _Run:
    id=1; project_id=2; share_link="http://x/s"; artifact_share_link="javascript:evil"
    session_id="s1"; verdict="fail"; verdict_reason="工具维不达标"

# off → None
settings.MULTICA_MODE="off"
assert multica.push_abnormal_run(_Run()) is None
# _safe_link:http 放行、非 http 挡
assert multica._safe_link("http://x")=="http://x" and multica._safe_link("javascript:e") is None
# payload:artifact_share_link 是 javascript: → 被挡成 None
p = multica._payload(_Run())
assert p["share_link"]=="http://x/s" and p["artifact_share_link"] is None
# http 模式 mock
settings.MULTICA_MODE="http"; settings.MULTICA_URL="http://multica.local/api"
class _Resp:
    status_code=200
    def json(self): return {"task_id": "T123"}
multica.requests.post = lambda *a, **k: _Resp()
assert multica.push_abnormal_run(_Run())=="T123"
# cli 模式 mock
settings.MULTICA_MODE="cli"; settings.MULTICA_CLI_TEMPLATE="echo {run_id}"
class _Proc: returncode=0; stdout="REF-1\n"; stderr=""
multica.subprocess.run = lambda *a, **k: _Proc()
assert multica.push_abnormal_run(_Run())=="REF-1"
settings.MULTICA_MODE="off"  # 复位
print("OK: multica off/http/cli + share_link 校验(javascript 挡) 正常")
```

- [ ] **Step 4: 跑** — `python _verify_multica.py`(backend/下),末行 OK。

- [ ] **Step 5: 删脚本** — `rm backend/_verify_multica.py`

- [ ] **Step 6: 提交**
```bash
git add backend/app/services/multica.py backend/app/core/config.py backend/.env.example
git commit -m "feat(eval): multica 推送适配器(http/cli/off,契约占位)

push_abnormal_run:异常run→multica。MULTICA_MODE=off默认不推;http POST payload取task ref;
cli 命令模板。share_link外发前校验http(s)(补子项3 XSS写入侧)。真契约用户填MULTICA_*。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 导出端点 api/eval_export.py

**Files:**
- Create: `backend/app/schemas/eval_export.py`
- Create: `backend/app/api/eval_export.py`
- Modify: `backend/app/api/router.py`
- Verify(临时,删): `backend/_verify_eval_export.py`

**Interfaces:**
- Consumes: Task1 `feishu.write_sheet_rows`/`feishu.is_configured`;Task2 `multica.push_abnormal_run`;`EvalRun`/`EvalQuery`/`assert_project_role`。
- Produces: `POST /api/eval-export/feishu`、`POST /api/eval-export/multica`、`GET /api/eval-export/multica-pending`。

- [ ] **Step 1: schemas** — `backend/app/schemas/eval_export.py`:
```python
from pydantic import BaseModel, Field


class EvalExportFeishuIn(BaseModel):
    project_id: int
    sheet_url: str = Field(min_length=1)
    batch_id: str | None = None
    abnormal_only: bool = False
    start_row: int = 2


class EvalPushMulticaIn(BaseModel):
    project_id: int
    batch_id: str | None = None
```

- [ ] **Step 2: 端点** — `backend/app/api/eval_export.py`:
```python
"""对话测评结果导出:飞书表 + multica 推送(异常会话)。

飞书:导出到用户指定表(eval 平台生成、无飞书来源锚点,故导出非回填原表)。
multica:推 is_abnormal 且未 pushed 的 run,回写 pushed_multica/multica_ref 防重推。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import EvalQuery, EvalRun, User
from app.schemas.common import ok
from app.schemas.eval_export import EvalExportFeishuIn, EvalPushMulticaIn
from app.services import feishu, multica

router = APIRouter(prefix="/api/eval-export", tags=["eval-export"])
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)

# 导出飞书的默认列映射(字段→列)。沿用 CLI 五列 + 平台判定列。
_FEISHU_COL_MAP = {
    "share_link": "C", "artifact_share_link": "D", "reported_duration": "E",
    "bean_cost": "F", "answer": "H", "verdict": "J", "verdict_reason": "K", "is_abnormal": "L",
}


def _query_runs(db, project_id, batch_id=None, abnormal_only=False):
    q = db.query(EvalRun).filter(EvalRun.project_id == project_id)
    if batch_id:
        q = q.filter(EvalRun.batch_id == batch_id)
    if abnormal_only:
        q = q.filter(EvalRun.is_abnormal == True)  # noqa: E712
    return q.order_by(EvalRun.id).all()


@router.post("/feishu")
def export_feishu(body: EvalExportFeishuIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    if not feishu.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="未配置飞书应用凭据(FEISHU_APP_ID/FEISHU_APP_SECRET)")
    runs = _query_runs(db, body.project_id, body.batch_id, body.abnormal_only)
    rows = []
    for r in runs:
        title = ""
        if r.eval_query_id:
            q = db.get(EvalQuery, r.eval_query_id)
            title = q.title if q else ""
        rows.append({
            "share_link": r.share_link or "", "artifact_share_link": r.artifact_share_link or "",
            "reported_duration": r.reported_duration or "", "bean_cost": r.bean_cost or "",
            "answer": r.answer or "", "verdict": r.verdict or "",
            "verdict_reason": r.verdict_reason or "", "is_abnormal": "是" if r.is_abnormal else "否",
        })
    try:
        n = feishu.write_sheet_rows(body.sheet_url, rows, _FEISHU_COL_MAP, body.start_row)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    return ok({"exported": n, "sheet_url": body.sheet_url})


@router.post("/multica")
def push_multica(body: EvalPushMulticaIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    q = db.query(EvalRun).filter(EvalRun.project_id == body.project_id,
                                 EvalRun.is_abnormal == True,  # noqa: E712
                                 EvalRun.pushed_multica == False)  # noqa: E712
    if body.batch_id:
        q = q.filter(EvalRun.batch_id == body.batch_id)
    runs = q.order_by(EvalRun.id).all()
    pushed, results = 0, []
    for r in runs:
        try:
            ref = multica.push_abnormal_run(r)
            if ref is None:
                results.append({"run_id": r.id, "skipped": "multica 未配置(MULTICA_MODE=off)"})
                continue
            r.pushed_multica = True
            r.multica_ref = str(ref)[:512]
            db.commit()
            pushed += 1
            results.append({"run_id": r.id, "ref": ref})
        except Exception as e:  # noqa: BLE001 单条失败不断批
            db.rollback()
            results.append({"run_id": r.id, "error": str(e)})
    return ok({"pushed": pushed, "candidates": len(runs), "results": results})


@router.get("/multica-pending")
def multica_pending(project_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    n = db.query(EvalRun).filter(EvalRun.project_id == project_id,
                                 EvalRun.is_abnormal == True,  # noqa: E712
                                 EvalRun.pushed_multica == False).count()  # noqa: E712
    return ok({"pending": n})
```

- [ ] **Step 3: 注册** — `backend/app/api/router.py`:import 加 `eval_export`;`include_router(eval_judge.router)` 后加 `api_router.include_router(eval_export.router)`。

- [ ] **Step 4: 验证脚本** — `backend/_verify_eval_export.py`:
```python
"""Task3 验证:导出行映射 + multica 防重推 + 端点注册。backend/下运行。"""
from app.main import app
ps = [r.path for r in app.routes]
for p in ['/api/eval-export/feishu','/api/eval-export/multica','/api/eval-export/multica-pending']:
    assert p in ps, f'缺{p}'
# 列映射覆盖判定列
from app.api.eval_export import _FEISHU_COL_MAP
assert _FEISHU_COL_MAP["share_link"]=="C" and _FEISHU_COL_MAP["verdict"]=="J" and _FEISHU_COL_MAP["is_abnormal"]=="L"
# 防重推:_query 逻辑(pushed_multica==False)——构造验
from app.db.session import SessionLocal
from app.models import EvalRun, Project
from app.core.enums import EvalRunStatus
db = SessionLocal()
try:
    proj = db.query(Project).first(); pid = proj.id if proj else 1
    r1 = EvalRun(project_id=pid, runner="m", status=EvalRunStatus.judged, is_abnormal=True, pushed_multica=False)
    r2 = EvalRun(project_id=pid, runner="m", status=EvalRunStatus.judged, is_abnormal=True, pushed_multica=True)
    db.add_all([r1, r2]); db.commit()
    pend = db.query(EvalRun).filter(EvalRun.project_id==pid, EvalRun.is_abnormal==True, EvalRun.pushed_multica==False).count()
    assert pend >= 1  # r1 待推, r2 已推不算
    ids = [r1.id, r2.id]
    for x in (r1, r2): db.delete(x)
    db.commit()
    print("OK: 端点注册 + 列映射(含判定列) + 防重推查询 正常")
finally:
    db.close()
```

- [ ] **Step 5: 跑** — `python _verify_eval_export.py`(backend/下),末行 OK。

- [ ] **Step 6: 删脚本** — `rm backend/_verify_eval_export.py`

- [ ] **Step 7: 提交**
```bash
git add backend/app/schemas/eval_export.py backend/app/api/eval_export.py backend/app/api/router.py
git commit -m "feat(eval): /api/eval-export 飞书导出 + multica 推送端点

POST /feishu 导出eval_run到指定表(五列+判定列);POST /multica 推异常会话(防重推pushed_multica);
GET /multica-pending 待推数。用户JWT+assert_project_role,单条失败不断批。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 前端导出/推送按钮

**Files:**
- Modify: `frontend/src/api/index.js`
- Modify: `frontend/src/views/EvalResults.vue`

**Interfaces:**
- Consumes: Task3 端点。
- Produces: EvalResults 页"导出飞书""推送异常到 multica"按钮。

- [ ] **Step 1: api** — `frontend/src/api/index.js` 加:
```javascript
// 对话测评导出/推送
export const exportEvalFeishu = (payload) => http.post('/eval-export/feishu', payload)
export const pushEvalMultica = (payload) => http.post('/eval-export/multica', payload)
export const evalMulticaPending = (projectId) => http.get('/eval-export/multica-pending', { params: { project_id: projectId } })
```

- [ ] **Step 2: EvalResults.vue 加按钮** — 读现有 `frontend/src/views/EvalResults.vue`(子项3建)的工具栏 + script setup。加:
  - 工具栏两按钮:"导出到飞书"(点开 el-dialog 填 sheet_url + el-checkbox 仅异常 → 调 exportEvalFeishu({project_id, sheet_url, abnormal_only}))、"推送异常到 multica"(调 pushEvalMultica({project_id}),ElMessage 提示 pushed/candidates)。
  - onMounted/项目切换时调 evalMulticaPending 显示待推数(可选 badge)。
  - handler 完整代码(参照现有页 ElMessage/loading/错误交拦截器风格):
```javascript
const exportDialogVisible = ref(false)
const exportSheetUrl = ref('')
const exportAbnormalOnly = ref(false)
const exporting = ref(false)
async function doExportFeishu() {
  if (!exportSheetUrl.value) { ElMessage.warning('请填飞书表链接'); return }
  exporting.value = true
  try {
    const res = await exportEvalFeishu({ project_id: pid.value, sheet_url: exportSheetUrl.value, abnormal_only: exportAbnormalOnly.value })
    ElMessage.success(`已导出 ${res.exported} 行到飞书表`)
    exportDialogVisible.value = false
  } catch (e) { /* 拦截器已提示 */ } finally { exporting.value = false }
}
const pushingMultica = ref(false)
async function doPushMultica() {
  pushingMultica.value = true
  try {
    const res = await pushEvalMultica({ project_id: pid.value })
    ElMessage.success(`推送 ${res.pushed}/${res.candidates} 条异常到 multica`)
    await load()
  } catch (e) { /* 拦截器 */ } finally { pushingMultica.value = false }
}
```
  (pid.value/load() 沿用 EvalResults 现有;import 三个 api 函数 + ElMessage/ref。)

- [ ] **Step 3: 构建验证** — `cd frontend && npm run build 2>&1 | tail -3`,须 `✓ built`。

- [ ] **Step 4: 提交(仅源码,dist 收尾重建)**
```bash
cd /d/code/test-management-platform
git add frontend/src/api/index.js frontend/src/views/EvalResults.vue
git commit -m "feat(eval): 前端导出飞书 + 推送 multica 按钮

EvalResults 工具栏:导出到飞书(填sheet_url+仅异常)、推送异常到multica(pushed/candidates提示)。
api exportEvalFeishu/pushEvalMultica/evalMulticaPending。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:** §4 feishu写回→Task1;§6 multica适配器+config→Task2;§5 导出端点→Task3;§7 前端→Task4;§8 无schema变更→确认无迁移任务(正确);§3决策(导出非回填/占位/防重推/scheme校验)→Task1-3落实。

**2. Placeholder 扫描:** Task1-3 后端完整代码。Task4 前端参照现有 EvalResults + 给了完整 handler。multica 契约占位是**有意设计**(§6/Task2 明确"待用户填"),非计划占位漏洞——适配器接口/config 都是完整可跑代码,off 默认安全。

**3. 类型一致性:** write_sheet_rows(sheet_url,rows,col_map,start_row)(Task1)→ 导出端点调用一致;push_abnormal_run(run)→ref(Task2)→ multica端点消费(ref None=skip,防重推)一致;_FEISHU_COL_MAP 字段↔row dict 键(Task3)一致;端点入参 EvalExportFeishuIn/EvalPushMulticaIn(Task3)↔前端 payload(Task4)一致。

**注:** 真飞书导出/真multica推送需真配置环境(飞书应用+目标表/multica契约),各Task脱机验(mock _api_put/mock http·cli/构造eval_run)覆盖列转换·分区间·payload组装·防重推·端点。真验证待环境,spec §9记。

---

## Execution Handoff

计划已存 `docs/superpowers/plans/2026-08-22-eval-feishu-multica.md`。
