# 对话测评判定层 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 读 eval_run 的会话轨迹(trace)+ eval_query.expected,用大模型判三维(思考完整/工具·mcp调用正常/产物符预期),写 verdict_dims/verdict/is_abnormal,status→judged。

**Architecture:** 复用 stream_generate(传 judge prompt_builder + judge system_prompt,累积文本后解析,零改引擎层)。新建 services/eval_judge.py(judge_run:读trace文件+调引擎+解析+落库+降级)+ api/eval_judge.py(POST /{run_id}、/batch、GET /abnormal)。判定是平台侧动作,用户 JWT 鉴权。

**Tech Stack:** FastAPI+SQLAlchemy2.0;claude/deepseek 引擎(复用现有 stream_generate);前端 Vue3+ElementPlus。

**Spec:** `docs/superpowers/specs/2026-08-22-eval-judge-design.md`

## Global Constraints

- **零改引擎抽象层**:判定复用 stream_generate(子项1/2 已加 prompt_builder/system_prompt)。不改 exec_queue/gen_testcases/eval 生成/下发链路。
- **不用原生JSON列**;verdict_dims 存 Text-JSON(eval_run.verdict_dims 子项0 已建 Text 列)。
- **鉴权**:判定端点用 get_current_user + assert_project_role(admin/member);abnormal 列表含 guest。
- **无 schema 变更**:eval_run 判定字段(verdict/verdict_dims/verdict_reason/judged_by/is_abnormal)子项0 已建。
- **本仓库无测试框架**:验证用一次性脚本(mock 引擎 yield 固定判定JSON + 构造样例trace文件)。后端命令 backend/ 下,SQLite。
- **三维**:verdict_dims={thinking_complete,tools_ok,artifact_expected} 各 {pass:bool,note:str}。任一 pass=false→verdict=fail→is_abnormal=true;全pass→verdict=pass;解析error→verdict=error。
- **EvalVerdict 值**:passed="pass"/failed="fail"/error="error"(子项0 已定,成员名规避保留字)。
- **降级**:ws_captured=false 或 trace 文件读不到→仍判但只凭 answer,verdict_dims 相关维 note 标"轨迹未捕获",不崩。
- **str() 防护**:parse 文本字段用 str() 包裹(吸取子项1 教训)。

## 文件结构

- Modify `backend/app/services/claude_runner.py` — build_eval_judge_prompt + EVAL_JUDGE_SYSTEM_PROMPT + parse_eval_verdict + _extract_json_object。
- Modify `backend/app/services/generators/deepseek_runner.py` — import 新增判定函数(复用)。
- Create `backend/app/services/eval_judge.py` — judge_run(读trace+调引擎+解析+落库+降级)。
- Create `backend/app/api/eval_judge.py` — POST /{run_id}、POST /batch、GET /abnormal。
- Modify `backend/app/api/router.py` — 注册。
- Modify `backend/app/api/index.js`(前端) + 新建/改 eval 结果页 — 判定触发 + 三维展示。

---

### Task 1: 判定 prompt + 解析(claude_runner)

纯函数,脱机验。

**Files:**
- Modify: `backend/app/services/claude_runner.py`(判定函数,放 parse_eval_queries 之后)
- Modify: `backend/app/services/generators/deepseek_runner.py`(import)
- Verify(临时,删): `backend/_verify_judge_parse.py`

**Interfaces:**
- Produces: `build_eval_judge_prompt(trace: dict, expected: str, dimensions: list[str]) -> str`;`parse_eval_verdict(raw: str) -> dict`;`EVAL_JUDGE_SYSTEM_PROMPT`;`_extract_json_object(raw) -> dict|None`。

- [ ] **Step 1: 加 _extract_json_object + 判定常量/prompt** — `backend/app/services/claude_runner.py`,parse_eval_queries 之后:
```python
# 判定输出是单个 JSON 对象(非数组);平行 _extract_cases_array 的多重兜底提取。
def _extract_json_object(raw: str):
    """从模型输出提取单个 JSON 对象:① ```json fence 内 {..} ② 全文首个 { 到末个 } ③ salvage 第一个。失败 None。"""
    candidates = []
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw or "", re.S)
    if m:
        candidates.append(m.group(1))
    s, e = (raw or "").find("{"), (raw or "").rfind("}")
    if s != -1 and e > s:
        candidates.append(raw[s:e + 1])
    for blob in candidates:
        try:
            obj = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    salv = _salvage_objects(raw or "")
    return salv[0] if salv else None


EVAL_JUDGE_SYSTEM_PROMPT = (
    "你是 AI 对话质量评审专家。严格依据提供的会话轨迹(思考过程、工具调用、产物、答案)与期望,"
    "客观判定各维度是否达标。只输出要求的 JSON,不寒暄、不解释。"
)

# 判定维度说明(供 build_eval_judge_prompt)
EVAL_JUDGE_DIMS = {
    "thinking_complete": "思考过程是否完整、有条理(有清晰的推理/规划,而非跳步或空洞)",
    "tools_ok": "工具与 MCP 调用是否正常(该调的调了、调用有结果 reached_result、结果被正确使用;无报错中断)",
    "artifact_expected": "最终产物/答案是否符合期望(对照 expected 描述)",
}


def build_eval_judge_prompt(trace: dict, expected: str, dimensions=None) -> str:
    """构造判定 prompt:喂思考/工具调用/产物/答案 + 期望,要求三维 JSON 判定。"""
    t = trace or {}
    _MAX = 4000  # 各长文本截断,判定看要点、避免 prompt 超限
    def _clip(s):
        s = str(s or "")
        return s if len(s) <= _MAX else s[:_MAX] + "…(已截断)"
    thinking = _clip(t.get("thinking"))
    answer = _clip(t.get("answer"))
    ws_captured = t.get("ws_captured", True)
    tools = t.get("tool_calls") or []
    tool_lines = []
    for tc in tools:
        if not isinstance(tc, dict):
            continue
        mcp = " [MCP]" if tc.get("is_mcp") else ""
        reached = "有结果" if tc.get("reached_result") else "未完成/无结果"
        tool_lines.append(
            f"- {str(tc.get('original_tool_name') or tc.get('name') or '')}{mcp}: "
            f"{reached};结果摘要={_clip(tc.get('result_text'))[:500]}"
        )
    tools_block = "\n".join(tool_lines) if tool_lines else "(无工具调用)"
    artifacts = t.get("artifacts") or []
    art_block = "\n".join(f"- {str(a.get('name') if isinstance(a, dict) else a)}" for a in artifacts) or "(无产物)"
    dim_lines = "\n".join(f"- {k}: {v}" for k, v in EVAL_JUDGE_DIMS.items())
    ws_note = "" if ws_captured else "\n注意:本会话轨迹未完整捕获(ws_captured=false),思考/工具信息可能缺失,对应维度请据可得信息判定并在 note 说明。"

    return f"""判定下面这次 AI 对话的质量。按三个维度各给 pass(true/false)与 note(简短理由)。

维度:
{dim_lines}

期望(该对话应达到什么):
{expected or "(未提供明确期望,仅凭合理性判定产物维度)"}

会话轨迹:
【思考过程】
{thinking or "(无)"}

【工具/MCP 调用】
{tools_block}

【产物】
{art_block}

【最终答案】
{answer or "(无)"}
{ws_note}

严格输出一个 JSON 对象(不要数组、不要额外文字):
{{
  "thinking_complete": {{"pass": true/false, "note": "..."}},
  "tools_ok": {{"pass": true/false, "note": "..."}},
  "artifact_expected": {{"pass": true/false, "note": "..."}},
  "summary": "总体判定理由(简短)"
}}"""
```

- [ ] **Step 2: 加 parse_eval_verdict** — 紧接其后:
```python
_JUDGE_DIM_KEYS = ("thinking_complete", "tools_ok", "artifact_expected")


def parse_eval_verdict(raw: str) -> dict:
    """解析判定输出为三维 dict。健壮:提取失败/缺维 → 该维 pass=None+note;非 dict → error 标记。"""
    obj = _extract_json_object(raw)
    if not isinstance(obj, dict):
        return {"error": True, "raw_tail": str(raw or "")[-200:],
                **{k: {"pass": None, "note": "判定输出无法解析"} for k in _JUDGE_DIM_KEYS}}
    out = {}
    for k in _JUDGE_DIM_KEYS:
        v = obj.get(k)
        if isinstance(v, dict) and "pass" in v:
            out[k] = {"pass": bool(v.get("pass")), "note": str(v.get("note") or "").strip()}
        else:
            out[k] = {"pass": None, "note": "判定未给出该维度"}
    out["summary"] = str(obj.get("summary") or "").strip()
    return out
```

- [ ] **Step 3: deepseek import** — `backend/app/services/generators/deepseek_runner.py` 的 `from app.services.claude_runner import (...)` 加:
```python
    build_eval_judge_prompt, parse_eval_verdict, EVAL_JUDGE_SYSTEM_PROMPT,
```
(判定也复用 stream_generate,deepseek 侧只需能被 import 到这些符号供 service 用;实际调用走 service 传参,deepseek import 是为对称/未来直接引用。若 deepseek 不直接引用可省——但为一致加上。)

- [ ] **Step 4: 验证脚本** — `backend/_verify_judge_parse.py`:
```python
"""Task1 验证:build_eval_judge_prompt 含轨迹/期望;parse_eval_verdict 解析健壮。backend/下运行。"""
from app.services.claude_runner import build_eval_judge_prompt, parse_eval_verdict, EVAL_JUDGE_SYSTEM_PROMPT

trace = {"thinking": "先分析再规划", "answer": "完成的网页",
         "tool_calls": [{"original_tool_name": "mcp__serper__web_search", "is_mcp": True, "reached_result": True, "result_text": "晴"}],
         "artifacts": [{"name": "snake.html"}], "ws_captured": True}
p = build_eval_judge_prompt(trace, "产出可运行贪吃蛇网页", None)
assert "mcp__serper__web_search" in p and "MCP" in p and "产出可运行贪吃蛇网页" in p and "先分析再规划" in p
assert "测试" not in EVAL_JUDGE_SYSTEM_PROMPT and "评审" in EVAL_JUDGE_SYSTEM_PROMPT

# 正常三维
raw = '''噪声
```json
{"thinking_complete":{"pass":true,"note":"完整"},"tools_ok":{"pass":false,"note":"搜索未用结果"},"artifact_expected":{"pass":true,"note":"符合"},"summary":"工具维不达标"}
```'''
d = parse_eval_verdict(raw)
assert d["thinking_complete"]["pass"] is True and d["tools_ok"]["pass"] is False
assert d["artifact_expected"]["pass"] is True and d["summary"] == "工具维不达标"
# 缺维 → pass=None
d2 = parse_eval_verdict('{"thinking_complete":{"pass":true,"note":"x"}}')
assert d2["tools_ok"]["pass"] is None and d2["artifact_expected"]["pass"] is None
# 非 dict → error
d3 = parse_eval_verdict('不是JSON')
assert d3.get("error") is True and d3["thinking_complete"]["pass"] is None
# 非字符串 note 不崩(str 防护)
d4 = parse_eval_verdict('{"thinking_complete":{"pass":true,"note":123},"tools_ok":{"pass":true,"note":"a"},"artifact_expected":{"pass":true,"note":"b"}}')
assert d4["thinking_complete"]["note"] == "123"
print("OK: build_eval_judge_prompt + parse_eval_verdict 正常")
```

- [ ] **Step 5: 跑** — `python _verify_judge_parse.py`(backend/下),末行 `OK: ...`。依赖缺失先 pip install。CreateFile Error:5 忽略。

- [ ] **Step 6: 删脚本** — `rm backend/_verify_judge_parse.py`

- [ ] **Step 7: 提交**
```bash
git add backend/app/services/claude_runner.py backend/app/services/generators/deepseek_runner.py
git commit -m "feat(eval): build_eval_judge_prompt + parse_eval_verdict + 判定 system prompt

判定 prompt(喂思考/工具·mcp/产物/答案+期望→三维JSON) + 解析(单对象提取、缺维降级、str防护)
+ EVAL_JUDGE_SYSTEM_PROMPT 中性评审 persona。deepseek import 复用。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 判定服务 services/eval_judge.py

读 trace 文件 + 调引擎累积 + 解析 + 落库 + 降级。

**Files:**
- Create: `backend/app/services/eval_judge.py`
- Verify(临时,删): `backend/_verify_judge_run.py`

**Interfaces:**
- Consumes: Task1 build_eval_judge_prompt/parse_eval_verdict/EVAL_JUDGE_SYSTEM_PROMPT;`generators`(normalize_provider/get_provider);`EvalRun`/`EvalQuery`;`EvalRunStatus`/`EvalVerdict`。
- Produces: `judge_run(db, run: EvalRun, provider: str | None = None) -> dict`(落库并返回 {verdict, verdict_dims, is_abnormal, judged_by})。`_load_trace(run) -> dict`。

- [ ] **Step 1: 实现** — `backend/app/services/eval_judge.py`:
```python
"""对话测评判定:读 eval_run 的会话轨迹(trace 文件)+ 期望,调大模型判三维,落库。

复用生成引擎(claude/deepseek)的 stream_generate,累积文本后 parse。判定是平台侧动作。
trace 存磁盘(uploads/eval_traces/{...}.json,子项2),按 run.trace URL 反解路径读。
"""
import json
import logging
import os

from sqlalchemy.orm import Session

from app.core.enums import EvalRunStatus, EvalVerdict
from app.models import EvalQuery, EvalRun
from app.services import claude_runner, generators

logger = logging.getLogger("test_platform")

_UPLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "uploads")
_UPLOADS_DIR = os.path.abspath(_UPLOADS_DIR)


def _load_trace(run: EvalRun) -> dict:
    """按 run.trace(形如 /uploads/eval_traces/xxx.json)反解磁盘路径读 JSON。
    读不到 → 用 run.answer 兜底的空壳(降级判定)。"""
    url = run.trace or ""
    fallback = {"thinking": "", "tool_calls": [], "artifacts": [],
                "answer": run.answer or "", "ws_captured": False}
    if not url.startswith("/uploads/"):
        return fallback
    rel = url[len("/uploads/"):]
    path = os.path.join(_UPLOADS_DIR, rel)
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else fallback
    except (OSError, json.JSONDecodeError, ValueError):
        logger.warning("判定读 trace 失败:%s", path)
        return fallback


def judge_run(db: Session, run: EvalRun, provider: str | None = None) -> dict:
    """判定一条 eval_run:读 trace+expected → 引擎 → 三维 → 落库。返回判定结果 dict。"""
    expected = ""
    if run.eval_query_id:
        q = db.get(EvalQuery, run.eval_query_id)
        if q:
            expected = q.expected or ""
    trace = _load_trace(run)

    provider_id = generators.normalize_provider(provider)
    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        run.verdict_reason = f"判定引擎「{provider_id}」不可用"
        db.commit()
        return {"error": run.verdict_reason}

    run.status = EvalRunStatus.judging
    db.commit()

    raw = ""
    err = None
    try:
        for evt in engine.stream_generate(
            expected or "判定",
            prompt_builder=lambda: claude_runner.build_eval_judge_prompt(trace, expected, None),
            system_prompt=claude_runner.EVAL_JUDGE_SYSTEM_PROMPT,
        ):
            et = evt.get("type")
            if et == "delta":
                raw += evt["text"]
            elif et == "result":
                if evt.get("text"):
                    raw = evt["text"]
            elif et == "error":
                err = evt.get("msg")
    except Exception as e:  # noqa: BLE001
        logger.exception("判定引擎调用异常")
        err = str(e)

    dims = claude_runner.parse_eval_verdict(raw)
    parse_error = dims.get("error")

    if err or parse_error:
        # 判定失败:不进 judged(保持 done 可重判),记原因
        run.status = EvalRunStatus.done
        run.verdict = EvalVerdict.error.value
        run.verdict_reason = (err or "判定输出无法解析")[:2000]
        run.judged_by = provider_id
        db.commit()
        return {"verdict": "error", "reason": run.verdict_reason}

    # 三维任一 fail → fail;有 None(未判维)按合理性:只要有明确 false 即 fail;全 true 才 pass
    passes = [dims[k]["pass"] for k in ("thinking_complete", "tools_ok", "artifact_expected")]
    if any(p is False for p in passes):
        verdict = EvalVerdict.failed.value  # "fail"
    elif all(p is True for p in passes):
        verdict = EvalVerdict.passed.value  # "pass"
    else:
        # 有 None(未给判定)但无明确 false:标 error 供复核,不误判 pass
        verdict = EvalVerdict.error.value

    run.verdict = verdict
    run.verdict_dims = json.dumps(dims, ensure_ascii=False)
    run.verdict_reason = dims.get("summary") or ""
    run.judged_by = provider_id
    run.is_abnormal = (verdict == EvalVerdict.failed.value)
    run.status = EvalRunStatus.judged
    db.commit()
    return {"verdict": verdict, "verdict_dims": dims,
            "is_abnormal": run.is_abnormal, "judged_by": provider_id}
```

- [ ] **Step 2: 验证脚本(mock 引擎)** — `backend/_verify_judge_run.py`:
```python
"""Task2 验证:judge_run 读trace+mock引擎+落库+降级。backend/下运行。"""
import json, os
from app.db.session import SessionLocal
from app.models import EvalRun, EvalQuery, Project
from app.core.enums import EvalRunStatus, EvalVerdict
from app.services import eval_judge, generators

# mock 引擎:stream_generate yield 固定判定 JSON
class _MockEngine:
    def is_available(self): return True
    def stream_generate(self, *a, **k):
        yield {"type": "delta", "text": '{"thinking_complete":{"pass":true,"note":"ok"},'
               '"tools_ok":{"pass":false,"note":"工具未用结果"},"artifact_expected":{"pass":true,"note":"符合"},"summary":"工具维不达标"}'}
_orig = generators.get_provider
generators.get_provider = lambda name=None: _MockEngine()

db = SessionLocal()
try:
    proj = db.query(Project).first(); pid = proj.id if proj else 1
    q = EvalQuery(project_id=pid, provider="claude", title="t", prompt="p", expected="产出网页")
    db.add(q); db.commit(); db.refresh(q)
    # 造 trace 文件
    os.makedirs(os.path.join(eval_judge._UPLOADS_DIR, "eval_traces"), exist_ok=True)
    tf = os.path.join(eval_judge._UPLOADS_DIR, "eval_traces", "999-test.json")
    json.dump({"thinking": "x", "tool_calls": [], "artifacts": [], "answer": "a", "ws_captured": True},
              open(tf, "w", encoding="utf-8"))
    r = EvalRun(eval_query_id=q.id, project_id=pid, runner="mac-01",
                status=EvalRunStatus.done, trace="/uploads/eval_traces/999-test.json", answer="a")
    db.add(r); db.commit(); db.refresh(r)

    res = eval_judge.judge_run(db, r, provider="claude")
    db.refresh(r)
    assert r.status == EvalRunStatus.judged, r.status
    assert r.verdict == "fail", r.verdict  # tools_ok=false → fail
    assert r.is_abnormal is True
    dims = json.loads(r.verdict_dims)
    assert dims["tools_ok"]["pass"] is False and dims["thinking_complete"]["pass"] is True
    assert r.judged_by == "claude"

    # 降级:trace 文件不存在 → 不崩,用兜底
    r2 = EvalRun(eval_query_id=q.id, project_id=pid, runner="mac-01",
                 status=EvalRunStatus.done, trace="/uploads/eval_traces/nope.json", answer="a2")
    db.add(r2); db.commit(); db.refresh(r2)
    tr = eval_judge._load_trace(r2)
    assert tr["ws_captured"] is False and tr["answer"] == "a2"

    os.remove(tf)
    for x in (r, r2, q): db.delete(x)
    db.commit()
    print("OK: judge_run 落库(fail/is_abnormal/judged)+ 降级读trace 正常")
finally:
    generators.get_provider = _orig
    db.close()
```

- [ ] **Step 3: 跑** — `python _verify_judge_run.py`(backend/下),末行 `OK: ...`。

- [ ] **Step 4: 删脚本** — `rm backend/_verify_judge_run.py`

- [ ] **Step 5: 提交**
```bash
git add backend/app/services/eval_judge.py
git commit -m "feat(eval): eval_judge 判定服务(读trace+调引擎+三维落库+降级)

judge_run:读 trace 文件+expected→stream_generate 累积→parse_eval_verdict→
写 verdict/verdict_dims/is_abnormal,status→judged。任一维fail→fail→is_abnormal。
trace读不到/ws_captured=false 降级只凭answer不崩。判定失败保持done可重判。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 判定端点 api/eval_judge.py

**Files:**
- Create: `backend/app/api/eval_judge.py`
- Modify: `backend/app/api/router.py`
- Verify(临时,删): `backend/_verify_judge_api.py`

**Interfaces:**
- Consumes: Task2 `eval_judge.judge_run`;`EvalRun`/`EvalRunStatus`/`assert_project_role`/`get_current_user`。
- Produces: `POST /api/eval-judge/{run_id}`、`POST /api/eval-judge/batch`、`GET /api/eval-judge/abnormal`。

- [ ] **Step 1: 端点文件** — `backend/app/api/eval_judge.py`:
```python
"""对话测评判定路由:触发判定(单条/批量)、异常会话列表。

判定是平台侧动作(读 trace + 调引擎),用户 JWT 鉴权(区别于 runner)。
判定逻辑在 services/eval_judge.judge_run。异常会话(is_abnormal)供子项4 推 multica。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import EvalRun, User
from app.schemas.common import ok
from app.services import eval_judge

router = APIRouter(prefix="/api/eval-judge", tags=["eval-judge"])
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


class JudgeIn(BaseModel):
    provider: str | None = None


class JudgeBatchIn(BaseModel):
    project_id: int
    run_ids: list[int] | None = None   # 指定;为空则判该项目所有 done 的 run
    provider: str | None = None


def _run_out(r: EvalRun) -> dict:
    import json
    return {
        "run_id": r.id, "eval_query_id": r.eval_query_id, "project_id": r.project_id,
        "status": getattr(r.status, "value", r.status), "verdict": r.verdict,
        "verdict_dims": json.loads(r.verdict_dims) if r.verdict_dims else None,
        "verdict_reason": r.verdict_reason, "judged_by": r.judged_by,
        "is_abnormal": bool(r.is_abnormal), "share_link": r.share_link, "answer": r.answer,
    }


@router.post("/{run_id}")
def judge_one(run_id: int, body: JudgeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    eval_judge.judge_run(db, r, provider=body.provider)
    db.refresh(r)
    return ok(_run_out(r))


@router.post("/batch")
def judge_batch(body: JudgeBatchIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    q = db.query(EvalRun).filter(EvalRun.project_id == body.project_id)
    if body.run_ids:
        q = q.filter(EvalRun.id.in_(body.run_ids))
    else:
        from app.core.enums import EvalRunStatus
        q = q.filter(EvalRun.status == EvalRunStatus.done)
    rows = q.all()
    results = []
    for r in rows:
        try:
            res = eval_judge.judge_run(db, r, provider=body.provider)
            results.append({"run_id": r.id, **res})
        except Exception as e:  # noqa: BLE001 单条失败不断批
            results.append({"run_id": r.id, "error": str(e)})
    return ok({"judged": len(results), "results": results})


@router.get("/abnormal")
def list_abnormal(project_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (db.query(EvalRun)
            .filter(EvalRun.project_id == project_id, EvalRun.is_abnormal == True)  # noqa: E712
            .order_by(EvalRun.id.desc()).all())
    return ok([_run_out(r) for r in rows])
```

- [ ] **Step 2: 注册** — `backend/app/api/router.py`:import 加 `eval_judge`;`include_router(eval_queue.router)` 后加 `api_router.include_router(eval_judge.router)`。

- [ ] **Step 3: 验证脚本** — `backend/_verify_judge_api.py`:
```python
"""Task3 验证:端点注册 + _run_out。backend/下运行。"""
from app.main import app
from app.api.eval_judge import _run_out
ps = [r.path for r in app.routes]
for p in ['/api/eval-judge/{run_id}', '/api/eval-judge/batch', '/api/eval-judge/abnormal']:
    assert p in ps, f'缺 {p}: {[x for x in ps if "eval-judge" in x]}'
class _R:
    id=1; eval_query_id=2; project_id=3; status=type("S",(),{"value":"judged"})()
    verdict="fail"; verdict_dims='{"tools_ok":{"pass":false}}'; verdict_reason="x"
    judged_by="claude"; is_abnormal=True; share_link="http://x"; answer="a"
o=_run_out(_R())
assert o["verdict"]=="fail" and o["is_abnormal"] is True and o["verdict_dims"]["tools_ok"]["pass"] is False
print("OK: eval-judge 端点注册 + _run_out 正常")
```

- [ ] **Step 4: 跑** — `python _verify_judge_api.py`(backend/下),末行 `OK: ...`。

- [ ] **Step 5: 删脚本** — `rm backend/_verify_judge_api.py`

- [ ] **Step 6: 提交**
```bash
git add backend/app/api/eval_judge.py backend/app/api/router.py
git commit -m "feat(eval): /api/eval-judge 判定端点(单条/批量/异常列表)

POST /{run_id} 判一条、POST /batch 批量判 done、GET /abnormal 异常会话列表(供子项4)。
用户 JWT+assert_project_role。judge_run 落库,单条失败不断批。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 前端判定触发 + 三维展示

**Files:**
- Modify: `frontend/src/api/index.js`
- Create: `frontend/src/views/EvalResults.vue`(eval 执行+判定结果页)
- Modify: `frontend/src/router/index.js` + `frontend/src/layouts/MainLayout.vue`(路由+导航)

**Interfaces:**
- Consumes: Task3 端点。
- Produces: 可看 eval_run 列表、触发判定、看三维结果的页面。

- [ ] **Step 1: api 封装** — `frontend/src/api/index.js` 加:
```javascript
// 对话测评判定
export const judgeEvalRun = (runId, provider) => http.post(`/eval-judge/${runId}`, { provider })
export const judgeEvalBatch = (payload) => http.post('/eval-judge/batch', payload)
export const listAbnormalEvalRuns = (projectId) => http.get('/eval-judge/abnormal', { params: { project_id: projectId } })
// eval 执行历史(子项2 已有端点 /eval-queue/history;若 api 未封装则加)
export const listEvalRuns = (projectId) => http.get('/eval-queue/history', { params: { project_id: projectId } })
```
(先 grep 是否已有 eval-queue/history 封装,有则复用不重复。)

- [ ] **Step 2: 新建 EvalResults.vue** — `frontend/src/views/EvalResults.vue`:参照现有列表页(如 ExecResults.vue 若有,或 AdoptedCases.vue)的 script setup + el-table 风格。含:
  - 项目选择(复用现有 projectId 口径/lastProject)。
  - el-table 列出 eval_run:run_id / eval_query 标题或 prompt 摘要 / status / verdict(pass绿/fail红/error灰 tag)/ is_abnormal(异常标记)/ share_link(链接)。
  - 每行"判定"按钮(调 judgeEvalRun,loading,完成刷新)。工具栏"批量判定 done"按钮(judgeEvalBatch)。
  - verdict_dims 三维展开(点行展开或弹窗):thinking_complete/tools_ok/artifact_expected 各 pass(✓/✗)+note;summary。
  - 数据源:listEvalRuns(projectId)。
  完整实现参照现有列表页风格(@别名/ElementPlus/ElMessage/http 已解包 data)。

- [ ] **Step 3: 路由+导航** — router/index.js 加 EvalResults 路由(懒加载);MainLayout.vue「执行」子菜单(exec,与"执行结果"平行)加"对话测评结果"入口(图标复用已导入的,如 Finished/DataAnalysis)。

- [ ] **Step 4: 构建验证** — `cd frontend && npm run build 2>&1 | tail -3`,须 `✓ built`。

- [ ] **Step 5: 提交(仅源码,dist 收尾统一重建)**
```bash
cd /d/code/test-management-platform
git add frontend/src/api/index.js frontend/src/views/EvalResults.vue frontend/src/router/index.js frontend/src/layouts/MainLayout.vue
git commit -m "feat(eval): 前端对话测评判定页(触发判定+三维展示)

EvalResults.vue:eval_run 列表+判定按钮(单条/批量)+verdict三维(思考/工具/产物 pass+note)展示。
api judgeEvalRun/judgeEvalBatch/listAbnormalEvalRuns/listEvalRuns。路由+导航。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:** §4 判定prompt/解析→Task1;§5 judge_run服务→Task2;§6 端点→Task3;§7 前端→Task4;§3决策(复用stream_generate/is_abnormal规则/降级/status流转)→Task1-2 落实;§8 无schema变更→确认无迁移任务(正确)。

**2. Placeholder 扫描:** Task1-3 后端完整代码。Task4 前端参照现有列表页(有意复用结构)+ 明确差异点(三维展示/判定按钮),非占位。

**3. 类型一致性:** parse_eval_verdict 产 {thinking_complete,tools_ok,artifact_expected 各{pass,note},summary}(Task1)→ judge_run 消费 passes=[dims[k]["pass"]](Task2)一致;verdict 值 pass/fail/error(EvalVerdict)贯穿;verdict_dims 存 json.dumps(Task2)→ _run_out json.loads(Task3)→ 前端展示(Task4)一致;is_abnormal=(verdict==fail)(Task2)→ abnormal 列表(Task3)一致。

**注:** 判定真实质量需真引擎(本机claude被hook污染无法验),各Task脱机验(mock引擎+构造trace)覆盖链路/解析/落库正确性;真判定质量待干净环境,spec §9 已记。

---

## Execution Handoff

计划已存 `docs/superpowers/plans/2026-08-22-eval-judge.md`。
