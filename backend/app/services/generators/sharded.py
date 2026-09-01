"""测试点生成的**分片并行编排**(提效核心)。

【为什么要分片】单次调用让模型一口气吐 8-100 条用例,耗时由**输出 token 串行生成**主导:
100 条 × ~800 token ≈ 8 万 output token,实测顶死 AI_TIMEOUT_SECONDS=900s 硬超时。
拆成 K 个**正交维度**的分片并行跑,墙钟≈1/K;且每片 prompt 只带自己需要的规则段
(gui 片不带 api spec、api 片不带 gui DSL/key 清单),单片 input 也从 ~7k 降到 ~3k。

【正交靠什么保证】分片定义里 focus/exclude 成对声明(见 claude_runner.TESTCASE_SHARDS),
prompt 里明确"其余维度由其它分片并行产出,你不要产出"。本模块再按 title 归一化兜底去重。

【失败语义】一片失败不拖垮整批:其余片照常落地,失败片记进 errors 供调用方提示;
全片失败才等价于旧的"整批失败"。

本模块只做编排 + 合并,不碰 DB(落库仍由 api 层 run_testcase_gen_job 完成)。
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from app.core.config import settings

logger = logging.getLogger("test_platform")

# 归一化 title 用:去掉空白与常见标点,避免"登录成功"/"登录 成功"/"登录成功。"被当成三条
_NORM_RE = re.compile(r"[\s　,，.。;；:：!！?？、\-—_/\\()（）\[\]【】\"'“”‘’]+")


def _norm_title(title: str) -> str:
    return _NORM_RE.sub("", str(title or "")).lower()


def _run_one_shard(engine, requirement, project_id, pages, shard, timeout=None) -> dict:
    """跑单个分片:引擎流式生成 → 解析。返回 {shard, cases, raw, meta, error}。

    引擎异常在此就地捕获成 error(不外抛),保证一片炸掉不影响其它片的 future。
    """
    sid = shard["id"]
    raw, meta, err = "", None, None
    try:
        for evt in engine.stream_generate(
            requirement, project_id=project_id, pages=pages, timeout=timeout,
            prompt_builder=lambda: engine.build_testcase_prompt(requirement, project_id, pages, shard),
        ):
            et = evt.get("type")
            if et == "delta":
                raw += evt.get("text") or ""
            elif et == "result":
                meta = evt
                if evt.get("text"):
                    raw = evt["text"]
            elif et == "error":
                err = evt.get("msg")
    except Exception as e:  # noqa: BLE001  单片失败不外溢
        logger.exception("分片生成失败 shard=%s", sid)
        return {"shard": sid, "cases": [], "raw": raw, "meta": meta,
                "error": f"分片「{shard['name']}」({sid})生成失败:{e}"}

    cases = engine.parse_testcases(raw, project_id=project_id) if raw else []
    if not cases:
        detail = err or ("引擎无任何输出" if not raw else f"输出 {len(raw)} 字但未解析出用例数组")
        return {"shard": sid, "cases": [], "raw": raw, "meta": meta,
                "error": f"分片「{shard['name']}」({sid})未产出用例:{detail}"}
    return {"shard": sid, "cases": cases, "raw": raw, "meta": meta, "error": None}


def generate_sharded(engine, requirement: str, *, project_id: int | None = None,
                     pages: list[str] | None = None, shards: list[dict] | None = None,
                     max_workers: int | None = None, timeout: int | None = None) -> dict:
    """并行跑各分片并合并结果。

    返回 {cases, raw, meta:{duration_ms,cost_usd,output_tokens}, errors:[str],
          shard_stats:[{shard,count,error}], dropped_dup:int}。
    - cases:合并去重后的用例(顺序=分片顺序,片内保序)
    - raw:各片原始输出拼接(带分片标题分隔),落 AiTask.output_raw 供排查
    - meta.duration_ms:取各片**最大值**(并行墙钟),不是求和
    - errors:失败/空产出的分片说明;全片失败时 cases 为空
    shards 缺省取 claude_runner.plan_shards(project_id)(无 api 契约时自动剔掉 api 片)。
    """
    from app.services.claude_runner import plan_shards

    shards = shards if shards is not None else plan_shards(project_id)
    if not shards:
        return {"cases": [], "raw": "", "meta": {}, "errors": ["无可用生成分片"],
                "shard_stats": [], "dropped_dup": 0}

    n = max_workers or min(len(shards), max(1, getattr(settings, "AI_SHARD_CONCURRENCY", 5)))
    with ThreadPoolExecutor(max_workers=n, thread_name_prefix="shard-gen") as ex:
        results = list(ex.map(
            lambda sh: _run_one_shard(engine, requirement, project_id, pages, sh, timeout), shards))

    cases: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    raw_parts, errors, stats = [], [], []
    durations, cost, tokens = [], 0.0, 0
    for r in results:
        if r["error"]:
            errors.append(r["error"])
        kept = 0
        for c in r["cases"]:
            k = _norm_title(c.get("title"))
            if not k or k in seen:
                dropped += 1
                continue
            seen.add(k)
            cases.append(c)
            kept += 1
        stats.append({"shard": r["shard"], "count": kept, "error": r["error"]})
        if r["raw"]:
            raw_parts.append(f"===== 分片 {r['shard']} =====\n{r['raw']}")
        m = r["meta"] or {}
        if m.get("duration_ms") is not None:
            durations.append(m["duration_ms"])
        if m.get("cost_usd") is not None:
            cost += m["cost_usd"]
        if m.get("output_tokens") is not None:
            tokens += m["output_tokens"]

    meta = {
        "duration_ms": max(durations) if durations else None,   # 并行:墙钟=最慢那片
        "cost_usd": cost or None,
        "output_tokens": tokens or None,
    }
    return {"cases": cases, "raw": "\n\n".join(raw_parts), "meta": meta,
            "errors": errors, "shard_stats": stats, "dropped_dup": dropped}
