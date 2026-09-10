"""multica(异常会话详细分析平台)推送适配器。

可插拔:MULTICA_MODE=off/http/cli。契约占位——用户填 MULTICA_URL/CLI_TEMPLATE 即用。
push_abnormal_run(run):组装 {share_link,session_id,verdict_reason,run_id,...} 发 multica,返回任务 ref。
share_link 推前校验 http(s)(补子项3 XSS 写入侧:外发也校验)。
"""
import logging
import json
import re
import shlex
import subprocess
import shutil
from urllib.parse import quote

import requests

from app.core.config import settings

logger = logging.getLogger("test_platform")
MULTICA_EVAL_PROJECT = "fe648247-d5b5-43bb-876e-e31afa63d2a6"
MULTICA_EVAL_ASSIGNEE = "79bfeb61-df55-40fe-acd4-36408563eeae"


def _safe_link(u):
    """只放行 http(s) 链接,否则 None(防把 javascript:/file: 等外发)。"""
    return u if isinstance(u, str) and re.match(r"^https?://", u, re.I) else None


def _payload(run, query=None) -> dict:
    try:
        snapshot = json.loads(getattr(run, "payload", None) or "{}")
    except (ValueError, TypeError):
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    return {
        "run_id": run.id,
        "project_id": run.project_id,
        "share_link": _safe_link(run.share_link),
        "artifact_share_link": _safe_link(run.artifact_share_link),
        "session_id": run.session_id,
        "verdict": run.verdict,
        "verdict_reason": run.verdict_reason,
        "prompt": snapshot.get("prompt") or getattr(query, "prompt", None),
        "expected": snapshot.get("expected") if "expected" in snapshot else getattr(query, "expected", None),
        "turn_index": snapshot.get("turn_index"),
        "conversation_group": snapshot.get("conversation_group"),
        "answer": getattr(run, "answer", None),
        "target_engine": getattr(run, "target_engine", None),
    }


def _skill_command(args, description=None):
    binary = shutil.which(settings.MULTICA_CLI_BIN)
    if not binary:
        raise ValueError("未找到 multica CLI,请在后端服务账号下安装或配置 MULTICA_CLI_BIN")
    try:
        result = subprocess.run(
            [binary, *args], input=description, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Multica CLI 超时;若为创建请求,请先在目标项目核对是否已创建再重试") from exc
    if result.returncode:
        raise ValueError(f"Multica CLI 失败: {(result.stderr or result.stdout or '')[:400]}")
    return result.stdout or ""


def check_skill_ready():
    """multica-add-task 的批量前置检查,沿用后端服务账号的登录态。"""
    _skill_command(["auth", "status"])
    _skill_command(["workspace", "list"])


def _skill_description(payload):
    def text_block(value, missing):
        text = str(value or "")
        if not text.strip():
            return missing
        # 提问可能自带代码围栏；外层用更长围栏，避免原文破坏描述分区。
        fence = "`" * max(3, 1 + max((len(s) for s in re.findall(r"`+", text)), default=0))
        return f"{fence}text\n{text}\n{fence}"

    link = _safe_link(payload.get("share_link"))
    encoded_link = quote(link, safe="/:?#[]@!$&'()*+,;=%") if link else None
    share = f"[查看完整对话](<{encoded_link}>)" if encoded_link else "暂未回填分享链接"
    return "\n\n".join([
        "## 对话分享", share,
        "## 提问原文（Prompt）", text_block(payload.get("prompt"), "暂无提问原文"),
        "## 预期结果（Expected）", text_block(payload.get("expected"), "未设置预期结果"),
    ])


def _create_skill_task(payload):
    title = "【测评反馈】" + (payload.get("verdict_reason") or "")
    description = _skill_description(payload)
    raw = _skill_command([
        "issue", "create", "--title", title, "--project", MULTICA_EVAL_PROJECT,
        "--assignee-id", MULTICA_EVAL_ASSIGNEE,
        "--description-stdin", "--output", "json",
    ], description)
    try:
        issue = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Multica 未返回有效任务 JSON,请核对目标项目后再重试") from exc
    if not isinstance(issue, dict) or not isinstance(issue.get("id"), str) or not issue["id"].strip():
        raise ValueError("Multica 未返回任务 id,未标记推送成功;请核对目标项目")
    return issue["id"]


def push_abnormal_run(run, query=None) -> str | None:
    """推一条异常 run 到 multica。off/未配→None;http/cli 按 config;失败抛异常(端点捕获)。"""
    mode = (settings.MULTICA_MODE or "off").lower()
    if mode == "off":
        return None
    payload = _payload(run, query)
    if mode == "skill":
        return _create_skill_task(payload)
    if mode == "http":
        if not settings.MULTICA_URL:
            raise ValueError("MULTICA_MODE=http 但未配 MULTICA_URL")
        headers = {"Content-Type": "application/json"}
        if settings.MULTICA_TOKEN:
            headers["Authorization"] = f"Bearer {settings.MULTICA_TOKEN}"
        resp = requests.post(settings.MULTICA_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()  # 非 2xx 抛异常→端点 except 捕获 rollback 不标 pushed(可重试),避免 fail-open 静默吞异常
        try:
            data = resp.json()
        except ValueError:
            data = {}
        # 契约占位:尽力从返回取任务 id/链接作 ref;拿不到用 http 状态
        ref = (data.get("task_id") or data.get("id") or data.get("url")
               or (data.get("data", {}).get("id") if isinstance(data.get("data"), dict) else None))
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
