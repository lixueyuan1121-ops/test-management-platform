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


# 候选优先级排序权重（越小越先试）：testid 最稳 > xpath（class+文本等精确定位，人工/自动纠正用）
# > css/label/placeholder（一般稳定，含缺省 by）> text/role（脆弱，降链尾）。
# 同权重内保持相对顺序（稳定排序）。xpath 介于 testid 与 css 之间：CSS 类名多命中时用 XPath 精确区分。
_BY_RANK: dict[str, int] = {"testid": 0, "xpath": 1, "text": 3, "role": 3}


def _rank(cand: dict) -> int:
    return _BY_RANK.get(cand.get("by") or "css", 2)


def order_candidates(cands: list[dict]) -> list[dict]:
    """按 by 优先级稳定排序（testid > xpath > css/label/placeholder > text/role），返回新列表。

    历史行为（脆弱降尾、稳定保持相对序）是本规则的子集——升级为显式分档以给 xpath 固定档位。
    """
    return sorted(cands, key=_rank)


VALID_BYS: set[str] = {"testid", "xpath", "role", "label", "text", "placeholder", "css"}


def is_valid_candidate(cand: dict) -> bool:
    """候选结构是否有效（可被 runner 定位）：含合法 by + 非空 value。

    与 is_fragile 正交：is_fragile 谈"稳不稳"，本函数谈"结构完不完整"。
    坏例 {}、{"by":"css"}(缺 value)、{"value":"x"}(缺 by)、非法 by 均无效。
    镜像：frontend/src/utils/selector-ranking.js、gui-core.mjs::validCands（三处口径契约）。
    """
    return isinstance(cand, dict) and cand.get("by") in VALID_BYS and bool(cand.get("value"))


def valid_candidates(cands: list[dict]) -> list[dict]:
    """过滤出有效候选（保序）；非 list → []。"""
    return [c for c in cands if is_valid_candidate(c)] if isinstance(cands, list) else []
