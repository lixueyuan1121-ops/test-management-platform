"""推推(TuiTui)群机器人通知通道：把平台已算好的口径主动推给人。

历史：原走飞书群自定义机器人 webhook；因飞书自定义机器人不可用，通知已统一改走推推。
FEISHU_WEBHOOK_* 配置保留仅为 .env 向后兼容，代码不再引用。
"""
import logging
import threading

import requests

from app.core.config import settings

logger = logging.getLogger("test_platform")

_TIMEOUT = 10

COLOR_RED = "red"
COLOR_ORANGE = "orange"
COLOR_GREEN = "green"
COLOR_BLUE = "blue"
_COLOR_EMOJI = {"red": "\U0001f534 ", "orange": "\U0001f7e0 ", "green": "\u2705 ", "blue": ""}


def is_enabled() -> bool:
    """通道总开关：推推 appid+secret+群id 都配齐才算开启。"""
    return bool((settings.TUITUI_BOT_APPID or "").strip()
                and (settings.TUITUI_BOT_SECRET or "").strip()
                and (settings.TUITUI_BOT_GROUP or "").strip())


def _esc(v) -> str:
    s = "" if v is None else str(v)
    s = s.replace("\n", " ").replace("\r", " ")
    for ch in ("*", "_", "~", "[", "]", "`"):
        s = s.replace(ch, " ")
    return s.strip()[:200]


def _link(path: str) -> str | None:
    base = (settings.PLATFORM_BASE_URL or "").rstrip("/")
    return f"{base}{path}" if base else None


def _tuitui_send(content: str, group: str | None = None) -> None:
    """推推机器人发群消息：POST /message/custom/send，appid+secret URL 鉴权。异步、静默降级。"""
    appid = (settings.TUITUI_BOT_APPID or "").strip()
    secret = (settings.TUITUI_BOT_SECRET or "").strip()
    grp = (group or settings.TUITUI_BOT_GROUP or "").strip()
    if not (appid and secret and grp):
        return
    base = (settings.TUITUI_BASE_URL or "https://alarm.im.qihoo.net").rstrip("/")
    url = f"{base}/message/custom/send?appid={appid}&secret={secret}"
    body = {"togroups": [grp], "msgtype": "text", "text": {"content": content[:50000]}}

    def _do():
        try:
            resp = requests.post(url, json=body, timeout=_TIMEOUT)
            if resp.status_code != 200:
                logger.warning("推推通知发送失败：HTTP %s", resp.status_code)
                return
            data = resp.json() if resp.content else {}
            if str(data.get("errcode", "0")) != "0":
                logger.warning("推推通知被拒绝：errcode=%s errmsg=%s",
                               data.get("errcode"), data.get("errmsg"))
        except requests.RequestException as e:
            logger.warning("推推通知网络异常：%s", e)
        except ValueError:
            logger.warning("推推通知返回非 JSON")

    threading.Thread(target=_do, daemon=True).start()


def send_card(title: str, lines: list[str], color: str = COLOR_BLUE,
              link_path: str | None = None, link_text: str = "打开平台查看") -> None:
    """发一条推推群消息（原飞书卡片统一改推推纯文本）。签名保留兼容既有调用方。"""
    if not is_enabled():
        return
    out = [_COLOR_EMOJI.get(color, "") + title]
    out += [ln.replace("**", "") for ln in lines]
    url = _link(link_path) if link_path else None
    if url:
        out.append(f"{link_text}：{url}")
    _tuitui_send("\n".join(out))


# ---------------- 场景一：自动回归批次失败告警 ----------------
def notify_batch_result(batch_id: str, project_name: str, total: int, passed: int,
                        failed: int, blocked: int, trigger: str,
                        failed_titles: list[str] | None = None,
                        auto_issues: int = 0, flaky: int = 0) -> None:
    if not settings.NOTIFY_EXEC_FAIL or not is_enabled():
        return
    if failed <= 0 and blocked <= 0:
        return
    color = COLOR_RED if failed > 0 else COLOR_ORANGE
    kind = "真功能失败" if failed > 0 else "环境/选择器阻塞"
    lines = [
        f"项目：{_esc(project_name)}",
        f"触发方式：{'定时自动回归' if trigger == 'auto' else '手动执行'}",
        f"结果：共 {total} 条，通过 {passed}，失败 {failed}，阻塞 {blocked}"
        + (f"，抖动 {flaky}（重试后通过）" if flaky else ""),
        f"性质：{kind}",
    ]
    for t in (failed_titles or [])[:5]:
        lines.append(f"\u2022 {_esc(t)}")
    if failed_titles and len(failed_titles) > 5:
        lines.append(f"\u2022 \u2026另有 {len(failed_titles) - 5} 条")
    if auto_issues > 0:
        lines.append(f"已自动生成 {auto_issues} 条缺陷草稿（遗留问题页复核，误报请纠偏后关闭）")
    send_card(title=f"回归失败告警（批次 {_esc(batch_id)[:12]}）", lines=lines, color=color,
              link_path=f"/exec-results?batch_id={batch_id}", link_text="查看执行结果")


# ---------------- 场景二：任务指派通知 ----------------
def notify_task_assigned(task_title: str, project_name: str, assignee_name: str,
                         assigner_name: str, assigned_date, priority: str) -> None:
    if not settings.NOTIFY_TASK_ASSIGN or not is_enabled():
        return
    send_card(title="新测试任务指派",
              lines=[
                  f"任务：{_esc(task_title)}",
                  f"项目：{_esc(project_name)}",
                  f"负责人：{_esc(assignee_name)}（由 {_esc(assigner_name)} 指派）",
                  f"测试日期：{_esc(assigned_date)}\u3000优先级：{_esc(priority)}",
              ],
              color=COLOR_BLUE, link_path="/tasks", link_text="查看任务")


# ---------------- 场景三：日报缺交提醒 ----------------
def notify_reports_missing(project_name: str, report_date, missing_names: list[str],
                           submitted: int, expected: int) -> None:
    if not settings.NOTIFY_REPORT_MISSING or not is_enabled():
        return
    if not missing_names:
        return
    lines = [
        f"项目：{_esc(project_name)}\u3000日期：{_esc(report_date)}",
        f"提交进度：{submitted}/{expected}",
        f"未提交（{len(missing_names)} 人）：" + "、".join(_esc(n) for n in missing_names[:20]),
    ]
    send_card(title="日报缺交提醒", lines=lines, color=COLOR_ORANGE,
              link_path="/my-reports", link_text="去提交日报")


# ---------------- 场景四：设备离线告警 ----------------
def notify_devices_offline(offline: list[str], pending_count: int) -> None:
    if not is_enabled() or not offline:
        return
    send_card(title="执行机离线告警",
              lines=[
                  f"离线设备（{len(offline)} 台）：" + "、".join(_esc(d) for d in offline[:10]),
                  f"受影响的排队任务：{pending_count} 条仍在 pending，无执行机可认领",
              ],
              color=COLOR_ORANGE, link_path="/device-board", link_text="查看设备看板")


# ---------------- 场景五：测评任务一条龙分步通知 ----------------
def notify_eval_pipeline(task_name: str, project_id: int, title: str,
                         lines: list[str], color: str = COLOR_BLUE) -> None:
    if not settings.NOTIFY_EVAL_PIPELINE:
        return
    parts = [_esc(title), f"任务：{_esc(task_name)}"] + [_esc(l) for l in lines]
    _tuitui_send("\n".join(parts))
