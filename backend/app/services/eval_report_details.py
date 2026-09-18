"""Deterministic query-first report details; never asks the model to pair runs."""
import html
import json
import re
from collections import OrderedDict

from app.services.eval_engines import DEFAULT_ENGINE, EVAL_ENGINES
from app.services.eval_snapshot import payload_of


STATUS_LABEL = {"pending": "待执行", "running": "执行中", "done": "待判定",
                "judging": "判定中", "judged": "已判定", "failed": "执行失败",
                "cancelled": "已取消", "missing": "记录缺失"}
VERDICT_LABEL = {"pass": "通过", "fail": "不通过", "error": "判定出错"}
REVIEW_LABEL = {"confirmed": "人工确认", "false_positive": "人工标记：误报",
                "false_negative": "人工标记：漏报"}


def esc(value):
    return html.escape(str(value if value is not None else ""))


def fmt_duration(ms):
    if not ms:
        return "—"
    seconds = round(ms / 1000)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m{seconds}s" if seconds else f"{minutes}m"


def group_query_runs(rows):
    """Pair the same frozen query, not titles; preserve every configuration/trial/turn."""
    groups = OrderedDict()
    for row in rows:
        p = row["payload"]
        qid = p.get("eval_query_id") or row.get("eval_query_id")
        prompt = p.get("prompt") or ""
        # Without a query id or prompt there is not enough evidence to pair legacy rows.
        identity = str(qid) if qid else ("prompt" if prompt else f"run:{row['run_id']}")
        context = p.get("source_conversation_group")
        if context is None:
            context = p.get("conversation_group")
        key = json.dumps([row.get("batch_id"), identity, prompt, p.get("attachments") or [],
                          context or "", p.get("turn_index") or 0, p.get("expected")],
                         ensure_ascii=False, sort_keys=True)
        groups.setdefault(key, []).append(row)
    return list(groups.values())


def _link(url, label):
    if not re.match(r"^https?://", str(url or ""), re.I):
        return ""
    return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{label}</a>'


def _row_html(row):
    p = row["payload"]
    engine = row.get("target_engine") or DEFAULT_ENGINE
    product = EVAL_ENGINES.get(engine, {}).get("label", engine)
    opts = p.get("dialog_options") or {}
    config = p.get("configuration_label") or " · ".join(str(x) for x in (
        opts.get("model"), opts.get("chatMode"), f"思考深度：{opts['thinkingDepth']}" if opts.get("thinkingDepth") else None) if x) or "沿用客户端配置"
    badges = []
    if p.get("compare_group"):
        badges.append(f"{p['compare_group']} 组")
    if p.get("configuration_index"):
        badges.append(f"配置 {p['configuration_index']}")
    if p.get("trial_count", 1) > 1 or p.get("trial_index", 1) > 1:
        badges.append(f"第 {p.get('trial_index', 1)} 次执行")
    sub = f'<small>{esc(" · ".join(badges))}</small>' if badges else ""
    status = row.get("status")
    verdict = row.get("verdict")
    label = (STATUS_LABEL[status] if status in ("failed", "missing") else
             VERDICT_LABEL.get(verdict, verdict or STATUS_LABEL.get(status, status or "—")))
    tone = "bad" if status == "failed" or verdict == "fail" else "good" if verdict == "pass" else "neutral"
    review = REVIEW_LABEL.get(row.get("review_mark"))
    state = f'<span class="qd-result qd-{tone}">{esc(label)}</span>'
    if verdict and status != "judged":
        state += f'<small>{esc(STATUS_LABEL.get(status, status or "—"))}</small>'
    if review:
        state += f'<small>{esc(review)}</small>'
    cost = row.get("bean_cost")
    unit = {"namiwork": "算力豆", "workbuddy": "积分", "qwork": "积分"}.get(engine, "")
    cost_text = "—" if cost is None or str(cost).strip() == "" else str(cost)
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", cost_text.strip()) and unit:
        cost_text += f" {unit}"
    reason = row.get("reason") if status in ("failed", "missing") else row.get("verdict_reason")
    reason_html = f'<details><summary>查看原因</summary><div class="qd-reason">{esc(reason)}</div></details>' if reason else "—"
    links = "<br>".join(filter(None, [_link(row.get("share_link"), "对话"), _link(row.get("artifact_share_link"), "产物")])) or "—"
    return (f'<tr data-run-id="{esc(row["run_id"])}"><td><strong>{esc(product)}</strong><small>RUN-{esc(row["run_id"])}</small></td>'
            f'<td class="qd-config">{esc(config)}{sub}</td><td>{state}</td>'
            f'<td class="qd-number"><strong>{esc(row.get("score") if row.get("score") is not None else "—")}</strong></td>'
            f'<td class="qd-number">{esc(fmt_duration(row.get("duration_ms")))}</td><td>{esc(cost_text)}</td>'
            f'<td>{links}</td><td>{reason_html}</td></tr>')


def render_query_details(rows):
    if not rows:
        return ""
    groups = group_query_runs(rows)
    products = list(dict.fromkeys(r.get("target_engine") or DEFAULT_ENGINE for r in rows))
    parts = [f'<style>{DETAIL_STYLE}</style><section class="query-details" data-layout="query-v1">'
             '<h2>逐条执行明细</h2>'
             f'<p class="qd-note">{len(groups)} 个 query · {len(rows)} 条执行 · {len(products)} 个产品。相同 query 的产品结果相邻展示。</p>'
             '<p class="qd-note">耗时为执行总耗时；消耗按各产品单位展示，不跨产品换算。配置为下发时的设置；未采集到的数据记为 —。</p>']
    for index, group in enumerate(groups, 1):
        first = group[0]
        p = first["payload"]
        title = p.get("title") or first.get("title") or f"Query {p.get('eval_query_id') or first.get('eval_query_id') or index}"
        dim = p.get("dimension", first.get("dimension")) or "未标注"
        meta = [str(dim), f"{len(group)} 条执行"]
        if p.get("source_conversation_group") or p.get("conversation_group"):
            meta.append(f"会话第 {(p.get('turn_index') or 0) + 1} 轮")
        qid = p.get("eval_query_id") or first.get("eval_query_id")
        if qid:
            meta.append(f"Query #{qid}")
        prompt = p.get("prompt")
        prompt_html = f'<details class="qd-prompt"><summary>查看完整 query</summary><div>{esc(prompt)}</div></details>' if prompt else ""
        attachments = p.get("attachments") or []
        if attachments:
            names = [a.get("name") or a.get("filename") or f"附件 {i}" if isinstance(a, dict) else str(a) for i, a in enumerate(attachments, 1)]
            prompt_html += f'<div class="qd-note">附件：{esc("、".join(names))}</div>'
        ordered = sorted(group, key=lambda r: (products.index(r.get("target_engine") or DEFAULT_ENGINE),
                         str(r["payload"].get("compare_group") or ""),
                         r["payload"].get("configuration_index") or 0, r["payload"].get("trial_index") or 1, r["run_id"]))
        parts.append(f'<article class="qd-query"><header><h3><span class="qd-index">{index:02d}</span>{esc(title)}</h3>'
                     f'<div class="qd-note">{esc(" · ".join(meta))}</div>{prompt_html}</header>'
                     '<div class="qd-scroll" tabindex="0" role="region" aria-label="各产品执行结果对比"><table class="qd-table">'
                     '<thead><tr><th>产品 / 执行</th><th>模型 / 配置</th><th>结果</th><th>评分 / 5</th><th>执行耗时</th><th>消耗</th><th>链接</th><th>判定 / 异常原因</th></tr></thead>'
                     '<tbody>' + "".join(_row_html(r) for r in ordered) + '</tbody></table></div></article>')
    return "".join(parts) + "</section>"


def detail_row(run, query=None):
    fields = ("eval_query_id", "batch_id", "target_engine", "verdict", "score", "duration_ms", "bean_cost",
              "reason", "verdict_reason", "review_mark", "share_link", "artifact_share_link")
    return {**{key: getattr(run, key, None) for key in fields}, "run_id": run.id,
            "status": getattr(run.status, "value", run.status), "payload": payload_of(run),
            "title": query.title if query else None, "dimension": query.dimension if query else None}


# Also embedded in frozen detail_html, so later layout changes do not alter older snapshots.
DETAIL_STYLE = """
.query-details { margin-top:28px; min-width:0; }
.query-details .qd-note { font-size:12px; color:#728196; overflow-wrap:anywhere; margin:6px 0; }
.query-details .qd-query { border:1px solid #dce5ed; border-radius:10px; margin:18px 0; overflow:hidden; }
.query-details .qd-query header { padding:16px 18px; background:#f6f9fc; border:0; }
.query-details h3 { display:flex; gap:10px; align-items:baseline; margin:0 0 6px; font-size:15px; color:#20334a; overflow-wrap:anywhere; }
.query-details .qd-index { color:#178875; font-size:13px; font-variant-numeric:tabular-nums; flex-shrink:0; }
.query-details .qd-scroll { overflow-x:auto; }
.query-details .qd-table { width:100%; min-width:960px; table-layout:fixed; border-collapse:collapse; margin:0; font-size:13px; }
.query-details .qd-table th, .query-details .qd-table td { text-align:left; padding:12px 10px; border:0; border-bottom:1px solid #e8edf3; vertical-align:top; overflow-wrap:anywhere; }
.query-details .qd-table th { background:#fff; color:#728196; font-size:12px; font-weight:500; }
.query-details .qd-table th:nth-child(1) { width:12%; }
.query-details .qd-table th:nth-child(2) { width:24%; }
.query-details .qd-table th:nth-child(3) { width:12%; }
.query-details .qd-table th:nth-child(4) { width:7%; }
.query-details .qd-table th:nth-child(5) { width:9%; }
.query-details .qd-table th:nth-child(6) { width:9%; }
.query-details .qd-table th:nth-child(7) { width:6%; }
.query-details .qd-table th:nth-child(8) { width:21%; }
.query-details .qd-table tbody tr:nth-child(even) { background:#fbfcfe; }
.query-details .qd-table tr:last-child td { border-bottom:0; }
.query-details small { display:block; margin-top:4px; font-size:11px; color:#728196; }
.query-details .qd-number { font-variant-numeric:tabular-nums; white-space:nowrap; }
.query-details .qd-result { display:inline-block; border-radius:4px; padding:1px 7px; font-size:12px; }
.query-details .qd-good { color:#087952; background:#e7f6ee; }
.query-details .qd-bad { color:#bc3543; background:#fcecee; }
.query-details .qd-neutral { color:#75632e; background:#f7f2e6; }
.query-details summary { cursor:pointer; color:#247567; font-size:12px; }
.query-details .qd-reason, .query-details .qd-prompt div { white-space:pre-wrap; overflow-wrap:anywhere; margin-top:8px; }
.query-details .qd-prompt { margin-top:8px; }
.query-details a { color:#247567; text-decoration:none; }
.query-details a:hover { text-decoration:underline; }
@media(max-width:640px) {
.query-details .qd-scroll::before { content:"左右滑动查看完整对比"; display:block; position:sticky; left:0; padding:8px 10px; font-size:11px; color:#728196; background:#fff; }
.query-details .qd-table th:first-child, .query-details .qd-table td:first-child { position:sticky; left:0; background:#fff; box-shadow:1px 0 #e8edf3; }
.query-details .qd-table tbody tr:nth-child(even) td:first-child { background:#fbfcfe; }
}
@media print { .query-details .qd-scroll { overflow:visible; } .query-details .qd-table { min-width:0; } }
"""
