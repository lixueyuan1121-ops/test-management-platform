"""极库云(geelib)缺陷上报通道：把平台的遗留问题/回归失败推成极库云工作项「缺陷」。

与 services/notify.py 的分工：notify 是「发通知给人看」（飞书/推推），本模块是「把缺陷
写进极库云缺陷系统」，产出可追踪的工作项 ID 回填到 RemainingIssue.external_ref。

鉴权与端点（与 sso-geelib-project-skill 同源，但零 Node 依赖，后端纯 Python 复刻）：
- token：subprocess 调 `qihoo-sso-cli -app geelib -tool sso-geelib-project-skill`，取 stdout
  JSON 的 app_token；进程内缓存 4 分钟（仿 skill 的 auth.ts，留 1 分钟余量）。
- 建缺陷：POST {GEELIB_API_URL}/openapi/Matter/add，头 `X-Agent-Auth: Bearer <token>`，
  体 {sub_id, type_id:"缺陷", title, mkd_content}；响应 errno===2000 为成功，data 里带工作项 id。

设计要点（对齐 notify.py 的硬约束）：
- **未配置即静默**：未开 GEELIB_ENABLED / 缺 sub_id 映射 / 缺 CLI 时 report_defect 返回
  ok=False+reason，绝不抛异常影响主流程（自动闭环里更要吞掉）。
- **写操作要幂等去重在调用方**：本模块只管「发一条」，不重复判断（RemainingIssue.external_ref
  非空即已上报，去重在调用侧）。
- **最小字段稳妥**：只传 sub_id/type_id/title/mkd_content。优先级等自定义字段(cf_*)因各项目
  字段规范不同、盲传会 400，故降级为把严重度写进正文，保证最小可用不因字段规范失败。
"""
import html as _html
import json
import logging
import shutil
import subprocess
import time

import requests

from app.core.config import settings

logger = logging.getLogger("test_platform")

_TIMEOUT = 30
_TOKEN_TTL = 4 * 60          # 秒，仿 skill auth.ts（4 分钟，留 1 分钟余量）
_token_cache: dict = {"token": None, "exp": 0.0}


class GeelibError(Exception):
    """上报失败的显式异常（供手动端点转成 4xx/5xx 给前端；自动闭环侧应捕获吞掉）。"""


def is_enabled() -> bool:
    """通道总开关：开了 GEELIB_ENABLED 才算启用（sub_id 映射在 resolve_sub_id 再校验）。"""
    return bool(settings.GEELIB_ENABLED)


def _sso_bin() -> str | None:
    """qihoo-sso-cli 可执行路径：优先配置，其次 PATH 查找。找不到返回 None。"""
    return settings.GEELIB_SSO_BIN or shutil.which("qihoo-sso-cli")


def get_app_token(force: bool = False) -> str:
    """取极库云 app_token（进程内缓存 4 分钟）。失败抛 GeelibError。

    调 `qihoo-sso-cli -app <app> -tool <tool>`，stdout 是 {errcode, app_token, errmsg} JSON。
    errcode!=0 或缺 app_token 视为失败。secret 不出现在参数里，日志安全。
    """
    now = time.time()
    if not force and _token_cache["token"] and now < _token_cache["exp"]:
        return _token_cache["token"]
    binp = _sso_bin()
    if not binp:
        raise GeelibError("qihoo-sso-cli 未安装或不在 PATH（配 GEELIB_SSO_BIN 指定绝对路径）")
    try:
        proc = subprocess.run(
            [binp, "-app", settings.GEELIB_SSO_APP, "-tool", settings.GEELIB_SSO_TOOL],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        raise GeelibError("qihoo-sso-cli 取 token 超时")
    except OSError as e:
        raise GeelibError(f"qihoo-sso-cli 调用失败：{e}")
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 and not out:
        raise GeelibError(f"qihoo-sso-cli 退出码 {proc.returncode}：{(proc.stderr or '').strip()[:200]}")
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        raise GeelibError(f"qihoo-sso-cli 输出非 JSON：{out[:200]}")
    if data.get("errcode") not in (0, "0"):
        raise GeelibError(f"qihoo-sso-cli 授权失败(errcode={data.get('errcode')})：{data.get('errmsg') or '未知'}")
    token = data.get("app_token")
    if not token:
        raise GeelibError("qihoo-sso-cli 成功但缺 app_token 字段")
    _token_cache["token"] = token
    _token_cache["exp"] = now + _TOKEN_TTL
    return token


def resolve_sub_id(project_code: str | None, geelib_sub_id: int | None = None) -> int | None:
    """定位平台项目对应的极库云 sub_id：优先 Project.geelib_sub_id，其次 GEELIB_SUB_MAP 里按 code 查。"""
    if geelib_sub_id:
        return int(geelib_sub_id)
    if project_code:
        return settings.geelib_sub_map.get(project_code)
    return None


def _post_matter_add(sub_id: int, title: str, mkd_content: str,
                     executor_mail: str | None = None) -> dict:
    """POST /openapi/Matter/add 建缺陷；errno!=2000 抛 GeelibError。返回 data(含工作项 id)。

    executor_mail：执行人邮箱。极库云「缺陷」类型把「执行人」列为必填，
    不传会返回 errno=3000。同时接口要求 content（富文本）非空，
    mkd_content 虽也支持但不能替代 content，因此两者均传。
    """
    token = get_app_token()
    url = f"{settings.GEELIB_API_URL.rstrip('/')}/openapi/Matter/add"
    # content（富文本）和 mkd_content（markdown）均传，极库云要求 content 非空
    # mkd_content 是多段 markdown：逐行转 HTML 段落（转义防特殊字符破坏富文本），
    # 链接行让其保持可点击。
    def _line_html(line: str) -> str:
        esc = _html.escape(line)
        return f"<p>{esc}</p>" if line.strip() else "<br/>"
    html_content = "".join(_line_html(l) for l in mkd_content.splitlines()) \
        or f"<p>{_html.escape(mkd_content)}</p>"
    body = {
        "sub_id": sub_id,
        "type_id": settings.GEELIB_DEFECT_TYPE,
        "title": title[:255],
        "content": html_content,
        "mkd_content": mkd_content,
        "data": [
            {"cf_name": "状态", "cf_value": "新建"},
            {"cf_name": "优先级", "cf_value": "P2"},
        ],
    }
    if executor_mail:
        body["data"].append({"cf_name": "执行人", "cf_value": executor_mail})
    elif settings.GEELIB_DEFAULT_EXECUTOR:
        body["data"].append({"cf_name": "执行人", "cf_value": settings.GEELIB_DEFAULT_EXECUTOR})
    try:
        resp = requests.post(url, json=body, headers={"X-Agent-Auth": f"Bearer {token}"},
                             timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise GeelibError(f"极库云建缺陷网络异常：{e}")
    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        raise GeelibError(f"极库云返回非 JSON（HTTP {resp.status_code}）")
    if data.get("errno") != 2000:
        raise GeelibError(f"极库云建缺陷失败(errno={data.get('errno')})：{data.get('errmsg') or '未知'}")
    # 成功时 data 直接是工作项 id 的字符串（实测 "1012069"），统一包成 dict
    payload = data.get("data")
    if isinstance(payload, (str, int)):
        return {"id": payload}
    return payload or {}


# 平台严重度 → 中文标签（写进正文，避免盲传 cf 字段触发字段规范 400）
_SEVERITY_LABEL = {"blocker": "阻断", "critical": "严重", "major": "主要", "minor": "次要", "trivial": "轻微"}


def build_defect_body(description: str | None, severity: str | None,
                      platform_url: str | None = None, extra: list[str] | None = None,
                      share_link: str | None = None) -> str:
    """把平台侧信息拼成极库云缺陷正文（markdown）。

    description 直接写入正文，保留其完整内容（含来源链路自动生成的失败原因/证据/会话等各段）。
    share_link 是对话分享链接（eval_run.share_link），单独追加在末尾便于快速跳转复核。
    """
    lines = []
    if severity:
        lines.append(f"**严重度**：{_SEVERITY_LABEL.get(severity, severity)}")
    for e in (extra or []):
        lines.append(e)
    lines.append("")
    # 完整写入描述（通常由自动草稿链路组装，含失败原因、执行机、批次、证据截图等）
    lines.append(description or "(无详细描述)")
    if share_link:
        lines.append("")
        lines.append(f"**对话分享链接**：{share_link}")
    if platform_url:
        lines.append("")
        lines.append(f"**来源**：测试管理平台 {platform_url}")
    return "\n".join(lines)


def report_defect(sub_id: int, title: str, description: str | None = None,
                  severity: str | None = None, platform_url: str | None = None,
                  extra: list[str] | None = None,
                  executor_mail: str | None = None,
                  share_link: str | None = None) -> dict:
    """上报一条缺陷到极库云。返回 {ok, matter_id, ref, reason}。

    executor_mail：执行人邮箱（极库云「缺陷」类型的必填字段）。传 None 时回退到
    GEELIB_DEFAULT_EXECUTOR 环境变量；两者均空则由极库云侧报 3000。
    share_link：对话分享链接（来自 eval_run.share_link），写进缺陷正文便于跳转复核。

    未开通道 → ok=False+reason（不抛）。其余失败抛 GeelibError（调用方决定吞/抛）。
    ref 是回填 RemainingIssue.external_ref 的字符串（形如 "geelib#<id>"）。
    """
    if not is_enabled():
        return {"ok": False, "reason": "极库云上报通道未启用（GEELIB_ENABLED=false）"}
    if not sub_id:
        return {"ok": False, "reason": "未配置该项目的极库云 sub_id（Project.geelib_sub_id 或 GEELIB_SUB_MAP）"}
    content = build_defect_body(description, severity, platform_url, extra, share_link=share_link)
    data = _post_matter_add(sub_id, title, content, executor_mail=executor_mail)
    matter_id = data.get("id") or data.get("matter_id") or data.get("Id")
    ref = f"geelib#{matter_id}" if matter_id else "geelib#?"
    logger.info("极库云缺陷已创建 sub_id=%s matter=%s title=%s", sub_id, matter_id, title[:40])
    return {"ok": True, "matter_id": matter_id, "ref": ref, "reason": None}
