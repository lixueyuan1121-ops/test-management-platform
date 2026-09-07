"""被测产品(引擎)注册表:多产品横评的合法引擎集中定义,校验以此为准。

与 eval_run.target_engine / eval_task.target_engines 的取值对齐。加新产品在此加一项 +
CLI 侧加执行器 + 一台声明该引擎的执行机即可,判定/统计/对比零改动(见 spec §12)。
"""

EVAL_ENGINES: dict[str, dict] = {
    "namiwork":  {"label": "纳米Work",  "needs_device": True,  "device_kind": "desktop"},
    "workbuddy": {"label": "WorkBuddy", "needs_device": False, "device_kind": "desktop"},
}

DEFAULT_ENGINE = "namiwork"


def is_valid_engine(engine: str) -> bool:
    return engine in EVAL_ENGINES


def normalize_engines(engines: list[str] | None) -> list[str]:
    """去重、剔非法、保序;空/全非法 → [DEFAULT_ENGINE](向后兼容)。"""
    if not engines:
        return [DEFAULT_ENGINE]
    out = list(dict.fromkeys(e.strip() for e in engines if e and e.strip() and is_valid_engine(e.strip())))
    return out or [DEFAULT_ENGINE]
