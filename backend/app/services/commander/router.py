"""对话式测试指挥官(Commander) 两跳意图路由。

引擎是**纯文本流**（非原生 function-calling），故意图路由=prompt + JSON 解析
（与 rts.parse_rts / build_rts_prompt 同范式）。两跳：
  1. 意图解析：问题 + 能力清单 → 引擎 → parse_intent → {intent, params, missing, clarify, reply_if_none}
  2. 叙事：能力返回的结构化数据 → 引擎 → 人话 Markdown（仅 read/analyze；draft 跳过第二跳）

安全：intent 必须命中 REGISTRY 白名单；project_id 由服务端注入并**覆盖**模型给的值
（模型不得选项目）；取具体对象的 runner 自带 IDOR 反查。

连接纪律（生产加固）：进引擎调用前不持写事务；read runner 只查不 commit；
第二跳叙事（数十秒）前 rollback 释放只读连接，免其空闲被中间层掐断。commander 自身不写库。
"""
import json
import logging
import re

from sqlalchemy.orm import Session

from app.services import rts
from app.services.commander.registry import get_capability, list_capabilities

logger = logging.getLogger("test_platform")

_INTENT_SYSTEM = (
    "你是测试管理平台的对话指挥官。依据用户问题，从给定能力清单里选一个最匹配的能力并抽取参数。"
    "只输出一个 JSON 对象，不要输出任何其它文字、解释或围栏说明。"
)
_NARRATE_SYSTEM = (
    "你是资深测试负责人。依据用户的原始问题与系统查到的结构化数据，用简洁的中文 Markdown 作答。"
    "只依据给定数据，不要编造数据里没有的数字。"
)

_CLARIFY_DEFAULT = "没太听懂，能换个说法吗？"
_CANT_DO = "我暂时不能做这个"


def build_intent_prompt(question: str, caps: list[dict], context: dict | None = None) -> str:
    """第一跳 prompt：用户问题 + 能力清单(name/desc/params) + 项目上下文 → 只回 JSON。"""
    lines = ["可用能力清单（intent 只能从下列 name 里选，不得臆造别的名字）：", ""]
    for c in caps:
        params = c.get("params") or {}
        pdesc = "、".join(f"{k}（{v}）" for k, v in params.items()) or "无"
        lines.append(f"- name={c['name']} | kind={c.get('kind')} | 说明：{c['desc']} | 参数：{pdesc}")
    lines.append("")
    if context:
        lines.append(f"当前上下文：{json.dumps(context, ensure_ascii=False, default=str)}")
        lines.append("")
    lines.append(f"用户问题：{question}")
    lines += [
        "",
        "只输出 JSON（无关字段可给空串/空数组）：",
        '{"intent":"命中的能力 name，或 null",'
        '"params":{"参数名":值},'
        '"missing":["能力需要但用户没给的必填参数名"],'
        '"clarify":"问题含糊、需要用户澄清时写澄清话术，否则空串",'
        '"reply_if_none":"若这不是能力型请求（闲聊/常识）则直接写答复，否则空串"}',
        "规则：能明确命中某能力就填 intent；含糊则用 clarify；非能力型问题用 reply_if_none；"
        "intent 只能是清单里的 name，绝不能编造。",
    ]
    return "\n".join(lines)


def parse_intent(raw: str) -> dict:
    """剥 ```json 围栏 → json.loads；失败降级为 clarify（同 parse_rts 降级范式）。

    返回 {intent, params, missing, clarify, reply_if_none}；解析失败/非对象时
    返回 {"intent": None, "clarify": "没太听懂，能换个说法吗？"}。
    """
    if not raw or not raw.strip():
        return {"intent": None, "clarify": _CLARIFY_DEFAULT}
    text = re.sub(r"```(?:json)?", "", raw).strip("` \n")
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return {"intent": None, "clarify": _CLARIFY_DEFAULT}
    try:
        obj = json.loads(text[s:e + 1])
    except (json.JSONDecodeError, ValueError):
        return {"intent": None, "clarify": _CLARIFY_DEFAULT}
    if not isinstance(obj, dict):
        return {"intent": None, "clarify": _CLARIFY_DEFAULT}
    intent = obj.get("intent")
    intent = str(intent).strip() if intent else None
    params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
    missing = obj.get("missing")
    missing = [str(m) for m in missing if m] if isinstance(missing, list) else []
    return {
        "intent": intent,
        "params": params,
        "missing": missing,
        "clarify": str(obj.get("clarify") or "").strip(),
        "reply_if_none": str(obj.get("reply_if_none") or "").strip(),
    }


def build_narrate_prompt(question: str, intent: str, data: dict) -> str:
    """第二跳 prompt：原问题 + 能力返回的结构化数据(JSON) → 人话 Markdown。"""
    return "\n".join([
        f"用户问题：{question}",
        f"已执行能力：{intent}",
        "查询结果（JSON）：",
        json.dumps(data, ensure_ascii=False, default=str),
        "",
        "请用简洁中文 Markdown 回答用户问题，聚焦关键数字与结论，不要罗列原始 JSON，不要编造数据。",
    ])


def _run_engine(engine, title: str, prompt: str, system_prompt: str) -> tuple[str, str | None]:
    """跑一次引擎流，收集正文/错误（与 rts.run_rts_job 的收集范式一致）。"""
    raw, err = "", None
    try:
        for evt in engine.stream_generate(title, prompt_builder=lambda: prompt, system_prompt=system_prompt):
            t = evt.get("type")
            if t == "delta":
                raw += evt.get("text") or ""
            elif t == "result" and evt.get("text"):
                raw = evt["text"]
            elif t == "error":
                err = evt.get("msg")
    except Exception as e:  # noqa: BLE001
        logger.exception("Commander 引擎异常")
        err = str(e)
    return raw, err


def ask(db: Session, user, project_id: int, question: str,
        provider: str | None = None, context: dict | None = None) -> dict:
    """两跳编排：意图解析 →（draft 直接回草稿 | read/analyze 叙事）。

    返回信封（供 Task3 API 再套统一 {code,msg,data}）：
      {"type":"answer",  ...}   闲聊回复 / 叙事结果（read/analyze）
      {"type":"clarify", "answer": ...}   需澄清 / 缺参 / 不支持
      {"type":"draft",   "intent", "draft": {...}}   写动作草稿（跳过第二跳）
    """
    pid, engine = rts._pick_provider(provider)
    if not engine.is_available():
        return {"type": "answer", "answer": f"叙事引擎「{pid}」暂不可用，请稍后再试。"}

    # ── 第一跳：意图解析（无写事务）──
    # 进第一跳前先释放连接：get_current_user(db.get User) + assert_project_role(ProjectMember SELECT)
    # 已在共享 session 上开了一个空闲只读事务；若挂着它跑数秒 stream_generate，MySQL 5.6 会以
    # 2013 Lost connection 掐断（同第二跳叙事前的隐患）。此处无先前状态需保留，cap.runner 后续会重查。
    db.rollback()
    intent_prompt = build_intent_prompt(question, list_capabilities(), context)
    raw, err = _run_engine(engine, "指挥官意图解析", intent_prompt, _INTENT_SYSTEM)
    if err:
        return {"type": "clarify", "answer": f"我没能理解你的请求（{err[:200]}），要不要换个说法？"}
    parsed = parse_intent(raw)

    if parsed.get("reply_if_none"):
        return {"type": "answer", "answer": parsed["reply_if_none"]}

    intent = parsed.get("intent")
    clarify = parsed.get("clarify") or ""
    cap = get_capability(intent) if intent else None
    # 含糊 或 intent 未命中白名单 → 澄清（此处即挡住模型臆造/越权的能力名）。
    if clarify or cap is None:
        return {"type": "clarify", "answer": clarify or _CANT_DO}

    if parsed.get("missing"):
        return {"type": "clarify", "answer": "还需要补充：" + "、".join(parsed["missing"])}

    # 服务端注入 project_id：放在展开之后 → 覆盖模型给的任何 project_id（模型不得选项目）。
    params = {**(parsed.get("params") or {}), "project_id": project_id}
    data = cap.runner(db, user, params)   # runner 自带 IDOR 反查；read 只查、draft 连查带校验不写库

    # draft：拿到草稿即返回，**跳过第二跳**（不叙事、不执行）。
    if cap.kind == "draft":
        return {"type": "draft", "intent": intent, "draft": data}

    # ── 第二跳：叙事（read/analyze）──
    # 叙事前释放只读连接：read runner 只查未写，rollback 安全且不影响已物化的 data(纯 dict)，
    # 避免数十秒叙事期间空持连接被中间层掐断。
    db.rollback()
    narrate_prompt = build_narrate_prompt(question, intent, data)
    md, nerr = _run_engine(engine, "指挥官叙事", narrate_prompt, _NARRATE_SYSTEM)
    if nerr or not md.strip():
        # 叙事失败不致命：仍回结构化数据，让上层/前端可展示。
        return {"type": "answer", "intent": intent,
                "answer": (nerr or "已取到数据，但叙事生成为空。"),
                "data": data, "provider": pid}
    return {"type": "answer", "intent": intent, "answer": md.strip(),
            "data": data, "provider": pid}
