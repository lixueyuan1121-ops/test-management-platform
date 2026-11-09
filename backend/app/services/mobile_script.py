"""Shared contract for user-visible native chat actions. Every script starts with a fresh chat."""
import hashlib
import json
import re

# These descriptors are also the action reference supplied to the generation and review models.
ACTIONS = {
    "new_chat": "进入新的测试会话，只能作为第一步",
    "compose": "text：向空草稿输入指定文字，保留空格和换行",
    "assert_draft": "text：核对编辑区与指定文字完全相等，允许空串",
    "clear_draft": "清空本次用例输入的草稿",
    "assert_send_unavailable": "核对空或全空白草稿的发送操作不可用",
    "send": "发送本次非空白草稿，开始新一轮",
    "assert_user_message": "turn：从1开始的已发送轮数，核对本轮用户气泡原文",
    "wait_reply": "等待当前轮产生新回复并恢复可输入，记录非空回复证据",
    "assert_reply_contains": "text：核对最近已完成的一轮回复包含给定文字",
    "stop_generation": "在当前轮生成时点击停止并验证退出生成状态",
    "open_history": "离开有完整回复的会话，进入历史列表并重开本次测试会话",
    "assert_history_restored": "核对重开后的会话内容与本次离开前记录一致",
}
LIMITS = {"max_cases": 50, "max_steps": 40, "max_text_chars": 1000, "max_turns": 5}
OBSERVATIONS = {"assert_draft", "assert_send_unavailable", "assert_user_message", "wait_reply",
                "assert_reply_contains", "stop_generation", "assert_history_restored"}


def validate_script(script):
    if not isinstance(script, list) or not 2 <= len(script) <= LIMITS["max_steps"]:
        raise ValueError("真机用例需要 2–40 个步骤")
    draft, turns, generating, completed, history = "", 0, False, False, False
    marked_thread = False
    for index, step in enumerate(script):
        def reject(reason):
            raise ValueError(f"真机步骤 {index + 1}：{reason}")

        action = step.get("action") if isinstance(step, dict) else None
        if not isinstance(action, str) or action not in ACTIONS:
            reject("动作不在对话驱动支持范围内")
        if (action == "new_chat") != (index == 0):
            reject("第一步且仅第一步可开启新会话")
        fields = {"action"}
        if action in {"compose", "assert_draft", "assert_reply_contains"}:
            fields.add("text")
        if action == "assert_user_message":
            fields.add("turn")
        if set(step) != fields:
            reject("步骤缺少参数或含未知参数")
        text = step.get("text")
        if "text" in fields:
            if not isinstance(text, str) or len(text) > LIMITS["max_text_chars"] or any(
                    (ord(c) < 32 and c not in "\r\n\t") or 0xD800 <= ord(c) <= 0xDFFF for c in text):
                reject("文字需要是最多 1000 字符的有效文本")
        if history and action != "assert_history_restored":
            reject("打开历史后需要核对恢复的会话")
        if generating and action not in {"assert_user_message", "wait_reply", "stop_generation"}:
            reject("当前轮正在生成，请先等待回复或停止生成")
        if action == "compose":
            if draft or not text:
                reject("输入需要空编辑区和非空文本")
            draft = text
        elif action == "assert_draft" and text != draft:
            reject("预期草稿与已输入或清空的内容不一致")
        elif action == "clear_draft":
            if not draft:
                reject("没有本次输入的草稿可清空")
            draft = ""
        elif action == "assert_send_unavailable" and draft.strip():
            reject("此断言适用于空或全空白草稿")
        elif action == "send":
            if not draft.strip() or turns >= LIMITS["max_turns"]:
                reject("发送要求非空白草稿，每用例最多五轮")
            if turns == 0:
                marked_thread = "{{marker}}" in draft
            draft, turns, generating, completed = "", turns + 1, True, False
        elif action == "assert_user_message":
            turn = step["turn"]
            if type(turn) is not int or not 1 <= turn <= turns:
                reject("只能核对已发送的正整数轮次")
        elif action in {"wait_reply", "stop_generation"}:
            if not generating:
                reject("需要本轮正在生成的消息")
            generating, completed = False, action == "wait_reply"
        elif action == "assert_reply_contains" and (not completed or not text.strip()):
            reject("需要完整回复和非空匹配文字")
        elif action == "open_history":
            if not marked_thread:
                reject("恢复历史需要首条已发送文本含 {{marker}} 唯一标记")
            if not completed or draft:
                reject("恢复历史需要本轮已有完整回复且没有草稿")
            history = True
        elif action == "assert_history_restored":
            if not history:
                reject("尚未打开当前会话的历史")
            history = False
    if generating or history or script[-1]["action"] not in OBSERVATIONS:
        raise ValueError("真机用例必须以能完成取证的断言结束")
    return script


def stored_script(tc):
    if tc.platform != "mobile":
        raise ValueError("参数化脚本仅适用于双端真机用例")
    try:
        parsed = json.loads(tc.script or "null")
    except (TypeError, ValueError) as exc:
        raise ValueError("真机用例的脚本不是有效 JSON") from exc
    return validate_script(parsed)


def verify_report(script, rows, uploaded_steps):
    """Replay the expected observations against this run's full ordered report and screenshots."""
    validate_script(script)
    digest = hashlib.sha256(json.dumps(script, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if len(rows) != len(script) + 1 or any(not isinstance(r, dict) or r.get("ok") is not True for r in rows):
        raise ValueError("真机报告未完整执行已派发的脚本")
    info = rows[-1]
    marker = info.get("marker")
    if (info.get("action") != "device_info" or info.get("scenario") != "script"
            or info.get("mobile_script_sha256") != digest or info.get("mobile_script_length") != len(script)
            or not isinstance(marker, str) or not re.fullmatch(r"QA-[A-Za-z0-9_-]{1,60}", marker)):
        raise ValueError("真机脚本快照、会话标记或报告摘要不匹配")
    prompts, draft, last_answer = [], "", ""
    for number, (step, row) in enumerate(zip(script, rows), 1):
        action = step["action"]
        if row.get("no") != number or row.get("action") != action:
            raise ValueError(f"真机步骤 {number} 与派发的动作顺序不一致")
        text = step.get("text", "").replace("{{marker}}", marker)
        if action == "compose":
            draft = text
        elif action == "clear_draft":
            draft = ""
        elif action == "send":
            prompts.append(draft)
            draft, last_answer = "", ""
        if action not in OBSERVATIONS:
            continue
        check = row.get("check")
        if number not in uploaded_steps or not isinstance(check, dict) or check.get("negate"):
            raise ValueError(f"真机步骤 {number} 缺少对应截图或断言")
        actual, mode = check.get("actual"), "equals"
        if action == "assert_draft":
            expected = text
        elif action == "assert_user_message":
            expected = prompts[step["turn"] - 1]
        elif action == "assert_send_unavailable":
            expected = False
        elif action == "stop_generation":
            expected = True
        elif action == "assert_history_restored":
            expected = prompts[0] + "\n" + last_answer
        elif action == "wait_reply":
            mode, expected = "neq", ""
            if not isinstance(actual, str) or not actual.strip():
                raise ValueError(f"真机步骤 {number} 缺少该轮实际回复")
            last_answer = actual
        elif action == "assert_reply_contains":
            mode, expected = "contains", text
            if actual != last_answer or text not in last_answer:
                raise ValueError(f"真机步骤 {number} 的证据与该轮回复不一致")
        if (check.get("mode", "equals") != mode or type(check.get("expected")) is not type(expected)
                or check["expected"] != expected or (mode == "equals" and
                    (type(actual) is not type(expected) or actual != expected))):
            raise ValueError(f"真机步骤 {number} 的实际值、预期或断言方式与已审查步骤不符")
