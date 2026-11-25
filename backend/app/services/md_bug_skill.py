"""Run the supplied Geelib skill's create-case entry point using the reporting user's SSO."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from app.core.config import settings
from app.services.geelib import GeelibError


SCRIPT = Path(__file__).resolve().parents[2] / "skill_adapters/geelib-bug-report/scripts/create-case.mjs"


class SubmissionUncertain(GeelibError):
    """Geelib may have accepted the defect before the response was lost."""


def create_case(sub_id: int, title: str, description: str, severity: str | None,
                executor: str | None, app_token: str) -> str:
    node = shutil.which("node")
    if not node or not SCRIPT.is_file():
        raise GeelibError("极库云提 Bug 技能需要 Node.js 18+ 和 create-case.mjs")
    if not executor or not app_token:
        raise GeelibError("缺少执行人邮箱或个人极库云授权")
    priority = {"blocker": "P0", "critical": "P1", "major": "P2", "minor": "P3", "trivial": "P4"}.get(str(severity), "P2")
    args = {"sub-id": sub_id, "type-id": settings.GEELIB_DEFECT_TYPE, "title": title,
            "content": description or title, "data": json.dumps([
                {"cf_name": "执行人", "cf_value": executor}, {"cf_name": "状态", "cf_value": "新建"},
                {"cf_name": "优先级", "cf_value": priority}], ensure_ascii=False)}
    payload = {"apiUrl": settings.GEELIB_API_URL, "appToken": app_token, "args": args}
    try:
        proc = subprocess.run([node, str(SCRIPT), "--platform-sso"], input=json.dumps(payload), capture_output=True,
                              text=True, encoding="utf-8", timeout=40,
                              env={k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "SystemRoot", "NODE_EXTRA_CA_CERTS")
                                   if k in os.environ})
    except OSError as exc:
        raise GeelibError("无法启动极库云提 Bug 技能") from exc
    except subprocess.TimeoutExpired:
        raise SubmissionUncertain("技能调用超时，请先在极库云核对是否已建单") from None
    try:
        result = json.loads(proc.stdout)
        errno = result["errno"]
    except (ValueError, KeyError, TypeError):
        raise SubmissionUncertain("未收到极库云建单回执，请先核对是否已建单") from None
    if errno != 2000:
        raise GeelibError(f"极库云拒绝建单（{errno}）：{str(result.get('errmsg') or '')[:200]}")
    matter_id = result.get("data")
    if isinstance(matter_id, dict):
        matter_id = matter_id.get("id") or matter_id.get("matter_id") or matter_id.get("Id")
    if not str(matter_id).isascii() or not str(matter_id).isdecimal() or int(matter_id) < 1:
        raise SubmissionUncertain("极库云返回成功但缺少缺陷编号，请核对建单结果")
    return f"geelib#{matter_id}"
