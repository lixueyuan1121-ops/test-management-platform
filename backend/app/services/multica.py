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
