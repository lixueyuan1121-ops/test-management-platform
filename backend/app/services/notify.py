"""飞书群机器人通知通道：把平台已算好的口径主动推给人。

与 services/feishu.py 的分工：那边是「读」（用 app 凭据取需求文档、写测评结果表），
本模块是「发」（只需群自定义机器人 webhook URL，无需 app 凭据）。两者互不依赖。

设计要点（都是踩过的坑/ 硬约束）：
- **绝不影响主流程**：所有对外发送都在 try/except 里吞掉异常并只记日志。通知失败
  不能让 runner 回写、任务创建这些业务动作失败——推送是旁路，不是链路的一环。
- **异步发送**：send 走 daemon 线程，避免 15s 网络超时把 runner 的 PATCH 回写卡住。
- **未配置即静默关闭**：没配 FEISHU_WEBHOOK_URL 时所有函数直接 return，
  本地开发/未接入飞书的部署零副作用、零报错。
- **批次汇总而非逐条告警**：一批回归 30 条失败 5 条，只在批次跑完时发 1 张卡，
  不发 5 条消息（消息轰炸会让人直接屏蔽机器人，等于没有告警）。
- 卡片用 interactive 消息（富文本+按钮），文案里的动态值都过 _esc 去掉 markdown 语法字符。
"""
import base64
import hashlib
import hmac
import logging
import threading
import time

import requests

from app.core.config import settings

logger = logging.getLogger("test_platform")

_TIMEOUT = 10

# 卡片头部配色（飞书内置模板色）
COLOR_RED = "red"
COLOR_ORANGE = "orange"
COLOR_GREEN = "green"
COLOR_BLUE = "blue"


def is_enabled() -> bool:
    """通道总开关：配了 webhook URL 才算开启。"""
    return bool(settings.FEISHU_WEBHOOK_URL)


def _esc(v) -> str:
    """动态值转纯文本：去掉会破坏卡片 markdown 结构的字符，并限长防超大文本。

    飞书 lark_md 会解析 * _ ~ [] 等；用例标题/失败原因里带这些字符会让卡片排版错乱，
    统一替换成安全字符。原因文本可能是整段堆栈，截断到 200 字。
    """
    s = "" if v is None else str(v)
    s = s.replace("\n", " ").replace("\r", " ")
    for ch in ("*", "_", "~", "[", "]", "`"):
        s = s.replace(ch, " ")
    s = s.strip()
    return s[:200]


def _sign(secret: str, ts: str) -> str:
    """飞书自定义机器人签名：以 "{ts}\\n{secret}" 为 key 对空串做 HMAC-SHA256 再 base64。

    注意这个算法的反直觉之处——待签名内容是空字节串，密钥才是拼接串（官方定义如此）。
    """
    key = f"{ts}\n{secret}".encode("utf-8")
    digest = hmac.new(key, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _post(body: dict) -> None:
    """同步发一条消息到 webhook。失败只记日志（调用方已在线程里）。"""
    url = settings.FEISHU_WEBHOOK_URL
    if not url:
        return
    payload = dict(body)
    if settings.FEISHU_WEBHOOK_SECRET:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _sign(settings.FEISHU_WEBHOOK_SECRET, ts)
    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        data = resp.json() if resp.content else {}
    except requests.RequestException as e:
        logger.warning("飞书通知发送失败（网络）：%s", e)
        return
    except ValueError:
        logger.warning("飞书通知返回非 JSON（HTTP %s）", resp.status_code)
        return
    # 群机器人成功返回 {"code":0,...} 或 {"StatusCode":0,...}（两种历史格式都见过）
    code = data.get("code", data.get("StatusCode", 0))
    if code not in (0, None):
        logger.warning("飞书通知被拒绝：code=%s msg=%s", code, data.get("msg") or data.get("StatusMessage"))


def _send_async(body: dict) -> None:
    """后台线程发送，不阻塞调用方（runner 回写/接口响应）。"""
    if not is_enabled():
        return
    t = threading.Thread(target=_post, args=(body,), daemon=True)
    t.start()


def _link(path: str) -> str | None:
    """拼平台页面绝对链接。未配 PLATFORM_BASE_URL 则返回 None（卡片不带按钮）。"""
    base = (settings.PLATFORM_BASE_URL or "").rstrip("/")
    if not base:
        return None
    return f"{base}/{path.lstrip('/')}"


def send_card(title: str, lines: list[str], color: str = COLOR_BLUE,
              link_path: str | None = None, link_text: str = "打开平台查看") -> None:
    """发一张卡片：标题 + 若干正文行 + 可选跳转按钮。

    lines 里的元素已由调用方用 _esc 处理过动态值；本函数只负责组装结构。
    """
    if not is_enabled():
        return
    elements: list[dict] = [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": "\n".join(lines)},
    }]
    url = _link(link_path) if link_path else None
    if url:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": link_text},
                "url": url,
                "type": "primary",
            }],
        })
    _send_async({
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        },
    })


# ---------------- 场景一：自动回归批次失败告警 ----------------
def notify_batch_result(batch_id: str, project_name: str, total: int, passed: int,
                        failed: int, blocked: int, trigger: str,
                        failed_titles: list[str] | None = None,
                        auto_issues: int = 0, flaky: int = 0) -> None:
    """一个执行批次跑完后的结果卡（只在有失败/阻塞时发）。

    trigger=auto 是定时无人值守回归——这类批次没人盯着，最需要推送；manual 批次
    用户就在页面上看着，不必打扰（由调用方决定是否调本函数）。
    auto_issues>0 时附「已自动生成 N 条缺陷草稿」；flaky>0 时附抖动条数(重试后通过)。
    """
    if not settings.NOTIFY_EXEC_FAIL or not is_enabled():
        return
    if failed <= 0 and blocked <= 0:
        return
    color = COLOR_RED if failed > 0 else COLOR_ORANGE
    kind = "真功能失败" if failed > 0 else "环境/选择器阻塞"
    lines = [
        f"**项目**：{_esc(project_name)}",
        f"**触发方式**：{'定时自动回归' if trigger == 'auto' else '手动执行'}",
        f"**结果**：共 {total} 条，通过 {passed}，失败 {failed}，阻塞 {blocked}"
        + (f"，抖动 {flaky}（重试后通过）" if flaky else ""),
        f"**性质**：{kind}",
    ]
    for t in (failed_titles or [])[:5]:
        lines.append(f"• {_esc(t)}")
    if failed_titles and len(failed_titles) > 5:
        lines.append(f"• …另有 {len(failed_titles) - 5} 条")
    if auto_issues > 0:
        lines.append(f"**已自动生成 {auto_issues} 条缺陷草稿**（遗留问题页复核，误报请纠偏后关闭）")
    send_card(
        title=f"回归失败告警（批次 {_esc(batch_id)[:12]}）",
        lines=lines, color=color,
        link_path=f"/exec-results?batch_id={batch_id}",
        link_text="查看执行结果",
    )


# ---------------- 场景二：任务指派通知 ----------------
def notify_task_assigned(task_title: str, project_name: str, assignee_name: str,
                         assigner_name: str, assigned_date, priority: str) -> None:
    """任务被指派给他人时通知（自己派给自己不必通知，由调用方判断）。"""
    if not settings.NOTIFY_TASK_ASSIGN or not is_enabled():
        return
    send_card(
        title="新测试任务指派",
        lines=[
            f"**任务**：{_esc(task_title)}",
            f"**项目**：{_esc(project_name)}",
            f"**负责人**：{_esc(assignee_name)}（由 {_esc(assigner_name)} 指派）",
            f"**测试日期**：{_esc(assigned_date)}　**优先级**：{_esc(priority)}",
        ],
        color=COLOR_BLUE, link_path="/tasks", link_text="查看任务",
    )


# ---------------- 场景三：日报缺交提醒 ----------------
def notify_reports_missing(project_name: str, report_date, missing_names: list[str],
                           submitted: int, expected: int) -> None:
    """日报缺交名单提醒（定时 job 调用；无人缺交则不发）。"""
    if not settings.NOTIFY_REPORT_MISSING or not is_enabled():
        return
    if not missing_names:
        return
    lines = [
        f"**项目**：{_esc(project_name)}　**日期**：{_esc(report_date)}",
        f"**提交进度**：{submitted}/{expected}",
        f"**未提交（{len(missing_names)} 人）**：" + "、".join(_esc(n) for n in missing_names[:20]),
    ]
    send_card(title="日报缺交提醒", lines=lines, color=COLOR_ORANGE,
              link_path="/my-reports", link_text="去提交日报")


# ---------------- 场景四：设备离线告警 ----------------
def notify_devices_offline(offline: list[str], pending_count: int) -> None:
    """执行机离线且仍有排队任务时告警（定时 job 调用）。"""
    if not is_enabled() or not offline:
        return
    send_card(
        title="执行机离线告警",
        lines=[
            f"**离线设备（{len(offline)} 台）**：" + "、".join(_esc(d) for d in offline[:10]),
            f"**受影响的排队任务**：{pending_count} 条仍在 pending，无执行机可认领",
        ],
        color=COLOR_ORANGE, link_path="/device-board", link_text="查看设备看板",
    )


# ---------------- 场景五：测评任务一条龙分步通知 ----------------
def notify_eval_pipeline(task_name: str, project_id: int, title: str,
                         lines: list[str], color: str = COLOR_BLUE) -> None:
    """测评任务一条龙(auto pipeline)分步通知:每完成一步发一张卡(共 4 步)。

    受 NOTIFY_EVAL_PIPELINE 开关 + 通道总开关(FEISHU_WEBHOOK_URL)约束,未配置静默跳过。
    lines 由编排器组装(可含 lark_md 加粗);project_id 预留(暂用统一列表页跳转)。
    """
    if not settings.NOTIFY_EVAL_PIPELINE or not is_enabled():
        return
    body = [f"**任务**:{_esc(task_name)}"] + [_esc(l) for l in lines]
    send_card(title=title, lines=body, color=color,
              link_path="/eval-tasks", link_text="查看测评任务")
