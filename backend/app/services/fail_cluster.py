"""失败聚类去噪：规则粗聚（本文件纯函数部分）+ AI 命名（Task3）。

规则粗聚 = 按失败指纹分组，同指纹一簇。指纹靠归一化失败特征，
免 token、确定性、可单测。AI 只负责给簇起根因标题/判严重度（Task3）。
"""
import hashlib
import json
import re

_HEX = re.compile(r"0x[0-9a-fA-F]+")
_NUM = re.compile(r"\d+")
_WS = re.compile(r"\s+")
_URLQ = re.compile(r"\?[^\s]*")


def normalize_reason(reason: str | None) -> str:
    """去掉易变量（行号/时间/数字 id/十六进制/url query），保留失败骨架文本。"""
    if not reason:
        return ""
    t = str(reason)
    t = _URLQ.sub("", t)
    t = _HEX.sub("#", t)
    t = _NUM.sub("#", t)          # 所有数字→占位符（毫秒/行号/id 归一）
    t = _WS.sub(" ", t).strip()
    return t[:300]


def _first_fail_step(report) -> str:
    """从逐步报告取首个失败步的 action+选择器 key（UI 失败最稳的指纹源）。"""
    if not isinstance(report, list):
        return ""
    for s in report:
        if isinstance(s, dict) and not s.get("ok"):
            key = ""
            ck = s.get("check")
            if isinstance(ck, dict):
                key = str(ck.get("key") or "")
            return f"{s.get('action', '')}:{key}".strip(":")
    return ""


def build_fingerprint(triage_kind, reason, fail_kind, report) -> str:
    """指纹 = triage_kind + (首失败步 或 归一reason 或 fail_kind) 的短哈希。"""
    feat = _first_fail_step(report) or normalize_reason(reason) or (fail_kind or "unknown")
    raw = f"{triage_kind or 'none'}|{feat}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{triage_kind or 'none'}-{h}"


def rule_cluster(runs: list[dict]) -> list[dict]:
    """按指纹粗聚。runs 每项须含 id/triage_kind/reason/fail_kind/report/requirement_id。

    返回每簇 {fingerprint, triage_kind, run_ids, requirement_ids, member_count, sample}，
    按 member_count 降序。sample 取簇内第一条（供 AI 命名）。
    """
    buckets: dict[str, dict] = {}
    for r in runs:
        fp = build_fingerprint(r.get("triage_kind"), r.get("reason"),
                               r.get("fail_kind"), r.get("report"))
        b = buckets.get(fp)
        if b is None:
            b = buckets[fp] = {
                "fingerprint": fp,
                "triage_kind": r.get("triage_kind"),
                "run_ids": [],
                "requirement_ids": [],
                "sample": r,
            }
        b["run_ids"].append(r["id"])
        rid = r.get("requirement_id")
        if rid is not None and rid not in b["requirement_ids"]:
            b["requirement_ids"].append(rid)
    out = []
    for b in buckets.values():
        b["member_count"] = len(b["run_ids"])
        out.append(b)
    out.sort(key=lambda c: -c["member_count"])
    return out
