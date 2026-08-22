"""测试点生成引擎(provider)抽象层。

平台支持多引擎生成测试点。每个 provider 是一个模块,实现统一的对外接口:
  - is_available() -> bool
  - stream_generate(requirement, project_id=None, timeout=None, pages=None, prompt_builder=None, system_prompt=None) -> Iterator[dict]
      yield 事件: {"type": "delta"|"result"|"error"|"heartbeat", ...}
      prompt_builder/system_prompt 为空时默认走"生成测试点"的 prompt/system;对话测评 query 生成经此二参复用同一引擎(见 api/ai_eval.py)。
  - generate_script(kind, title, steps, expected, project_id=None, timeout=None) -> tuple[list, str|None]
  - build_testcase_prompt(requirement, project_id=None, pages=None) -> str   # 供各 provider 复用同一 prompt
  - parse_testcases(raw, project_id=None) -> list[dict]          # 供各 provider 复用同一解析/降级

project_id 决定 prompt 注入哪个项目的共享 key 清单、script.target.key 用哪个注册表校验
(经 selectors 服务层读 DB;为空则不注入、不校验)。pages 非空时按页面收窄注入的 key。

现有 claude_runner 已满足该接口,直接注册;deepseek_runner 基于 DeepSeek Harness SDK。
新增 provider 时:实现上述接口 → 在 PROVIDERS 注册 → 前端 /ai/status 自动可见。
"""
from app.services import claude_runner
from app.services.generators import deepseek_runner

# provider id → 实现模块。id 会落库到 ai_task.provider / test_case.provider,勿随意改名。
PROVIDERS = {
    "claude": claude_runner,
    "deepseek": deepseek_runner,
}

DEFAULT_PROVIDER = "claude"


def get_provider(name: str | None):
    """按 id 取 provider 实现;未知/空 → 回落默认(claude),永不抛错。"""
    return PROVIDERS.get(name or "") or PROVIDERS[DEFAULT_PROVIDER]


def normalize_provider(name: str | None) -> str:
    """把任意入参规整成合法的 provider id(用于落库,保证列值可控)。"""
    return name if name in PROVIDERS else DEFAULT_PROVIDER


def available_providers() -> list[dict]:
    """列出所有 provider 及其可用性(前端据此渲染引擎选择器,不可用置灰)。"""
    return [
        {"id": pid, "available": bool(mod.is_available())}
        for pid, mod in PROVIDERS.items()
    ]
