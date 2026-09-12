"""被测产品(引擎)注册表:多产品横评的合法引擎集中定义,校验以此为准。

与 eval_run.target_engine / eval_task.target_engines 的取值对齐。加新产品在此加一项 +
CLI 侧加执行器 + 一台声明该引擎的执行机即可,判定/统计/对比零改动(见 spec §12)。
"""

EVAL_ENGINES: dict[str, dict] = {
    "namiwork":  {"label": "纳米Work",  "needs_device": True,  "device_kind": "desktop"},
    "workbuddy": {"label": "WorkBuddy", "needs_device": False, "device_kind": "desktop"},
}

DEFAULT_ENGINE = "namiwork"


def validate_dialog_options(engine: str, options: dict) -> None:
    if engine == "workbuddy" and any(options.get(k) for k in ("chatMode", "thinkingDepth")):
        raise ValueError("WorkBuddy 暂不支持指定对话模式或思考深度，请清空这两项后执行")


def is_valid_engine(engine: str) -> bool:
    return engine in EVAL_ENGINES


def runner_supported_engines(declaration: str | None) -> tuple[str, ...]:
    """runner 默认具备纳米Work能力；workbuddy 声明只增添能力，不替换默认能力。

    保留现有 EVAL_ENGINE/engine 参数，已部署执行器无需修改配置或另启进程。
    非法声明不授予任何执行能力。
    """
    declaration = declaration or DEFAULT_ENGINE
    if not is_valid_engine(declaration):
        return ()
    return tuple(dict.fromkeys((DEFAULT_ENGINE, declaration)))


def normalize_engines(engines: list[str] | None) -> list[str]:
    """去重、剔非法、保序;空/全非法 → [DEFAULT_ENGINE](向后兼容)。"""
    if not engines:
        return [DEFAULT_ENGINE]
    out = list(dict.fromkeys(e.strip() for e in engines if e and e.strip() and is_valid_engine(e.strip())))
    return out or [DEFAULT_ENGINE]
