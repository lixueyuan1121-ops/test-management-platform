"""选择器候选「稳定/脆弱」口径（后端侧）。

脆弱 by = text / role：`getByText` 默认子串匹配（'登录' 命中 '立即登录'），role 只能靠
name 匹配（copy 依赖）——二者随文案变化易失效、且多条同时命中会触发 Playwright strict 违例。
其余（testid/css/label/placeholder）视为稳定。

order_candidates 把脆弱候选降到链尾、稳定候选保持相对顺序（存库顺序已反映探测期分梯，
运行期不做完整重排，只降级脆弱）。

镜像：frontend/src/utils/selector-ranking.js（改一处必改另一处）；
参照 tools/qalab-runner/gui-mcp/gui-core.mjs::genCandidates 的分梯（text/role 为其最低档）。
"""
from __future__ import annotations

FRAGILE_BYS: set[str] = {"text", "role"}


def is_fragile(cand: dict) -> bool:
    """候选是否脆弱（by=text/role）。缺 by 按 css（稳定）处理。"""
    return (cand.get("by") or "css") in FRAGILE_BYS


def order_candidates(cands: list[dict]) -> list[dict]:
    """稳定候选在前、脆弱候选在后，各自保持相对顺序（稳定排序，返回新列表）。"""
    stable = [c for c in cands if not is_fragile(c)]
    fragile = [c for c in cands if is_fragile(c)]
    return stable + fragile
