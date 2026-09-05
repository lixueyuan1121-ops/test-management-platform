"""测评任务一条龙(auto pipeline)编排器:批次全部执行完 → 批量判定 → 综合评价 → 分步飞书通知。

触发点(所有能让批次达终态的路径都调 on_batch_maybe_done,幂等):
  - eval_queue.report 回写最后一条 run
  - eval_task.mark_run_failed 手动收口
  - scheduler.reap_stale_eval_runs 超龄收口

并发去重靠 eval_task.pipeline_status 门闩:一次条件 UPDATE(WHERE 可抢占 → running)抢占,
只有抢到的那一次在后台线程里真正跑编排;并发回写/reaper 重复触发都被挡下。
仅对开了 auto_pipeline 的任务生效;未开则直接返回。
"""
import logging
import threading

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.enums import EvalRunStatus
from app.models import EvalRun
from app.models.ai_eval import EvalTask

logger = logging.getLogger("test_platform")

# Retry summary generation when claude concurrency slots are exhausted (busy).
_SUMMARY_RETRY = 4
_SUMMARY_RETRY_SLEEP = 3.0

# 门闩可抢占态:NULL(从未跑)或 idle;running/done/failed 都不再抢(done/failed 是上一轮结果,
# 换批 dispatch 会重置回 NULL 才允许下一轮)。
_CLAIMABLE = (None, "idle")


def _batch_all_settled(db: Session, task_id: int, batch_id: str) -> bool:
    """该批次是否已全部达终态(无 pending/running)。空批次视为未完成(没东西可编排)。"""
    total = (db.query(EvalRun)
             .filter(EvalRun.eval_task_id == task_id, EvalRun.batch_id == batch_id).count())
    if total == 0:
        return False
    unfinished = (db.query(EvalRun)
                  .filter(EvalRun.eval_task_id == task_id, EvalRun.batch_id == batch_id,
                          EvalRun.status.in_([EvalRunStatus.pending, EvalRunStatus.running]))
                  .count())
    return unfinished == 0


def on_batch_maybe_done(db: Session, batch_id: str) -> bool:
    """批次完成钩子:若该批属于某个开了 auto_pipeline 的任务且已全部执行完,抢占门闩并后台跑一条龙。

    返回是否成功抢占并启动了编排(供测试/日志判断)。幂等:已在跑/已完成/未开开关/未完成 → False。
    """
    if not batch_id:
        return False
    task = (db.query(EvalTask).filter(EvalTask.last_batch_id == batch_id).first())
    if not task or not task.auto_pipeline:
        return False
    if task.pipeline_status not in _CLAIMABLE:
        return False   # 已在跑或已完成,不重复
    if not _batch_all_settled(db, task.id, batch_id):
        return False   # 还有 pending/running,等最后一条回写再触发

    # 原子抢占:条件 UPDATE(仅当仍可抢占才置 running),防并发双触发。
    claimable_vals = [v for v in _CLAIMABLE if v is not None]
    cond = EvalTask.pipeline_status.is_(None)
    if claimable_vals:
        cond = cond | EvalTask.pipeline_status.in_(claimable_vals)
    res = db.execute(
        update(EvalTask)
        .where(EvalTask.id == task.id, cond)
        .values(pipeline_status="running")
    )
    db.commit()
    if res.rowcount != 1:
        return False   # 被其他触发抢先

    _spawn_pipeline(task.id, task.project_id, task.name, batch_id)
    logger.info("测评一条龙已启动 task=%s batch=%s", task.id, batch_id)
    return True


def _spawn_pipeline(task_id: int, project_id: int, task_name: str, batch_id: str) -> None:
    """起后台线程跑编排(抽出以便测试 patch 成同步/记录,不实际起线程)。"""
    t = threading.Thread(target=_run_pipeline_thread,
                         args=(task_id, project_id, task_name, batch_id),
                         name=f"eval-pipeline-{task_id}", daemon=True)
    t.start()


def _run_pipeline_thread(task_id: int, project_id: int, task_name: str, batch_id: str) -> None:
    """后台线程:跑完整编排,末尾用【全新 session】把门闩/综合评价的残留 running 收口。

    编排内各步各自开短命 session(见 run_pipeline),不再全程持有一条长连接——根治
    「长跑后连接失效→写终态失败→前端一直生成中」。异常全捕获(线程不能抛)。
    """
    from app.db.session import SessionLocal
    try:
        run_pipeline(SessionLocal, task_id, project_id, task_name, batch_id)
    except Exception:
        logger.exception("测评一条龙编排异常 task=%s", task_id)
    finally:
        # 兜底收口:无论编排如何结束,都用一条【全新 session】把可能残留的 running 落 failed。
        # 用新连接(而非编排里那条可能已随长跑失效的连接)是关键——pool_pre_ping 取连接时探活,
        # 确保这步一定写得进库。正常结束时门闩已是 done、综合评价已是 done/failed,此步无改动(幂等)。
        _reconcile_stuck_status(SessionLocal, task_id)


def _reconcile_stuck_status(session_factory, task_id: int) -> None:
    """用全新 session 把某任务残留的 pipeline_status/summary_status='running' 收口为 failed。幂等、吞异常。"""
    try:
        db = session_factory()
    except Exception:  # noqa: BLE001
        logger.exception("一条龙兜底收口开 session 失败 task=%s", task_id)
        return
    try:
        t = db.get(EvalTask, task_id)
        if t is None:
            return
        changed = False
        if t.pipeline_status == "running":
            t.pipeline_status = "failed"; changed = True
        if t.summary_status == "running":
            t.summary_status = "failed"; changed = True
        if changed:
            db.commit()
            logger.info("一条龙兜底收口残留 running task=%s", task_id)
    except Exception:  # noqa: BLE001
        logger.exception("一条龙兜底收口失败 task=%s", task_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def reap_stale_running_on_startup(db: Session) -> dict:
    """进程启动时收口僵尸 running。

    后端重启(部署/崩溃/关窗口)后,内存里正在跑的综合评价/一条龙编排线程都已随进程消失,
    但库里可能残留 summary_status='running' 或 pipeline_status='running'——这些是被重启
    打断、再没有线程去落终态的僵尸态:不收口则前端永久"生成中"、编排门闩永远不能重跑。
    uvicorn 单进程启动时不存在正在跑的这类线程,故此刻所有 running 都可安全收口为 failed
    (综合评价可手动/换批重生成;pipeline 换批 dispatch 会重置门闩)。幂等,无 running 不写库。
    """
    r1 = db.execute(update(EvalTask).where(EvalTask.summary_status == "running")
                    .values(summary_status="failed"))
    r2 = db.execute(update(EvalTask).where(EvalTask.pipeline_status == "running")
                    .values(pipeline_status="failed"))
    n_sum, n_pipe = r1.rowcount or 0, r2.rowcount or 0
    if n_sum or n_pipe:
        db.commit()
        logger.info("启动收口僵尸 running:综合评价 %d 条、一条龙 %d 条", n_sum, n_pipe)
    else:
        db.rollback()
    return {"summary": n_sum, "pipeline": n_pipe}


def _fmt_dur(ms) -> str:
    """毫秒→紧凑耗时,与前端 fmtDur 同口径:秒<60→Ns;否则 MmSs(整分省秒);**不进位小时**(故 269m41s)。"""
    if not ms:
        return "—"
    s = round(ms / 1000)
    if s < 60:
        return f"{s}s"
    m, r = divmod(s, 60)
    return f"{m}m{r}s" if r else f"{m}m"


def _result_summary(db: Session, task_id: int, batch_id: str) -> dict:
    """收尾摘要指标:通过/失败/异常/均分 + A/B 胜率(若有对比组)+ 逐条明细 items(标题/结果/评分/耗时)。"""
    import json

    rows = (db.query(EvalRun)
            .filter(EvalRun.eval_task_id == task_id, EvalRun.batch_id == batch_id)
            .order_by(EvalRun.id).all())
    # 只统计可评的(排除 cancelled)
    rows = [r for r in rows if getattr(r.status, "value", r.status) != "cancelled"]
    total = len(rows)
    passed = sum(1 for r in rows if r.verdict == "pass")
    failed = sum(1 for r in rows if r.verdict == "fail")
    errored = sum(1 for r in rows if r.verdict == "error")   # 判定失败/无法定论,待重判
    abnormal = sum(1 for r in rows if r.is_abnormal)
    scores = [r.score for r in rows if r.score is not None]
    avg_score = round(sum(scores) / len(scores), 2) if scores else None
    # A/B 胜率:按 compare_group 统计各组 pass 率(payload 里的 compare_group);同时收集逐条明细
    ab = {"A": [0, 0], "B": [0, 0]}   # group -> [pass, total]
    has_ab = False
    items = []
    for r in rows:
        try:
            p = json.loads(r.payload) if r.payload else {}
        except (ValueError, TypeError):
            p = {}
        items.append({
            "title": (p.get("title") or p.get("prompt") or f"query#{r.eval_query_id}"),
            "verdict": r.verdict, "score": r.score, "duration_ms": r.duration_ms,
        })
        g = p.get("compare_group")
        if g in ab:
            has_ab = True
            ab[g][1] += 1
            if r.verdict == "pass":
                ab[g][0] += 1
    ab_line = None
    if has_ab:
        def _rate(x):
            return f"{round(100 * x[0] / x[1])}%" if x[1] else "—"
        ab_line = f"A 组通过率 {_rate(ab['A'])}（{ab['A'][0]}/{ab['A'][1]}）｜B 组通过率 {_rate(ab['B'])}（{ab['B'][0]}/{ab['B'][1]}）"
    return {"total": total, "passed": passed, "failed": failed, "abnormal": abnormal,
            "errored": errored, "avg_score": avg_score, "ab_line": ab_line, "items": items}


# 测评失败严重度：执行异常(abnormal)比单纯判定不过(fail)更严重。
_EVAL_SEVERITY = {"abnormal": "major", "fail": "minor"}


def auto_issue_for_eval_failures(db: Session, task_id: int, project_id: int, batch_id: str) -> list:
    """eval 批次里 verdict=fail 或 is_abnormal 的 run 逐条生成 RemainingIssue 草稿。返回新建列表。

    对齐 exec 侧 _auto_issue_for_failures：AUTO_ISSUE_ON_FAIL 开关控制；按 eval_query 去重
    （同题已有 open 的自动草稿则跳过，避免夜夜失败重复开单）；回指 eval_run_id/eval_task_id。
    abnormal(执行异常)→major，纯判定 fail→minor。
    """
    from app.core.config import settings
    if not settings.AUTO_ISSUE_ON_FAIL:
        return []
    from app.core.enums import IssueSeverity, IssueStatus
    from app.models import EvalQuery, RemainingIssue

    runs = (db.query(EvalRun)
            .filter(EvalRun.eval_task_id == task_id, EvalRun.batch_id == batch_id).all())
    created = []
    seen_queries = set()   # 本批已开单的 eval_query_id（同批多设备分片同题只开一条）
    for r in runs:
        abnormal = bool(r.is_abnormal)
        if not (r.verdict == "fail" or abnormal):
            continue
        if r.eval_query_id:
            if r.eval_query_id in seen_queries:
                continue
            dup = (db.query(RemainingIssue.id)
                   .join(EvalRun, EvalRun.id == RemainingIssue.eval_run_id)
                   .filter(RemainingIssue.status == IssueStatus.open,
                           EvalRun.eval_query_id == r.eval_query_id)
                   .first())
            if dup:
                continue
            seen_queries.add(r.eval_query_id)
        q = db.get(EvalQuery, r.eval_query_id) if r.eval_query_id else None
        q_title = (q.title if q else None) or f"run#{r.id}"
        sev = _EVAL_SEVERITY["abnormal" if abnormal else "fail"]
        desc_lines = [
            f"AI 测评失败草稿（测评任务 #{task_id}，批次 {batch_id}，执行机 {r.runner}）",
            f"判定：{'执行异常' if abnormal else '判定不通过'}"
            + (f"，评分 {r.score}" if r.score is not None else ""),
            f"理由：{(r.verdict_reason or r.reason or '无')[:500]}",
        ]
        if r.share_link:
            desc_lines.append(f"会话：{r.share_link}")
        desc_lines.append("请复核：确认为真 bug 则补负责人并上报极库云；误报请在测评结果页人工纠偏后关闭本条。")
        issue = RemainingIssue(
            report_id=None,
            task_id=None,
            eval_run_id=r.id,
            project_id=project_id,
            title=f"[自动] 测评失败：{q_title}"[:255],
            description="\n".join(desc_lines),
            severity=IssueSeverity(sev),
            status=IssueStatus.open,
        )
        db.add(issue)
        created.append(issue)
    if created:
        db.commit()
    return created


def _is_busy_error(err) -> bool:
    """错误是否为「并发槽繁忙」——唯一值得退避重试的临时错误(引擎繁忙消息含「繁忙/并发上限」)。"""
    return bool(err) and ("繁忙" in err or "并发上限" in err)


def _summary_with_retry(session_factory, task_id, batch_id):
    """综合评价生成;**仅**并发繁忙才退避重试。每次用一条短命 session(见 headless 卡死根治)。

    超时/引擎报错/无有效输出等不重试:每次重试都会重新跑一个 AI_TIMEOUT_SECONDS(默认 15min)
    硬超时,4 次叠加能把前端「生成中」拖到最长 ~60min(线上实测卡 >15min 即此叠加),且毫无收益。
    provider 固定为 None(= 平台默认引擎 claude),与手动生成综合评价完全一致。
    """
    from app.api.eval_task import generate_task_summary_headless
    import time as _t
    res = {}
    for _i in range(_SUMMARY_RETRY):
        db = session_factory()
        try:
            task = db.get(EvalTask, task_id)
            res = generate_task_summary_headless(db, task, batch_id, provider=None,
                                                 session_factory=session_factory)
        finally:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
        if res.get("ok") or res.get("skipped") or "error" not in res:
            break
        if not _is_busy_error(res.get("error")):
            break   # 非繁忙(超时/报错/无输出):重试徒增一个 15min 超时,立即收口
        if _i < _SUMMARY_RETRY - 1:
            _t.sleep(_SUMMARY_RETRY_SLEEP)
    return res


def _summary_share_url(session_factory, task_id) -> str | None:
    """综合评价在线短链:优先 nami 公网短链(部署整页 HTML),失败/未配则回落自托管 /r/<code>。

    - nami:把 render_report_page 的完整 HTML 部署到 n.cn → zhaomi.cn 公网短链,任何人可点开
      (推推群里直接可看)。依赖服务器上的 nami cookie;缺失/过期/网关错都回落,不阻断一条龙。
    - 自托管回落:PLATFORM_BASE_URL + /r/<code>(需平台可达)。两者都不可用 → None(通知不带链接)。
    """
    from app.core.config import settings
    from app.api.eval_report import share_path, render_report_page

    # 取任务(拿短链码 + 渲染整页 HTML)
    db = session_factory()
    try:
        t = db.get(EvalTask, task_id)
        if t is None:
            return None
        code = t.summary_share_code
        page_html = render_report_page(t) if (t.summary_status == "done" and t.summary_html) else None
    finally:
        db.close()

    # 优先 nami 公网短链
    if settings.NAMI_DEPLOY_ENABLED and page_html:
        try:
            from app.services import nami_deploy
            if nami_deploy.is_configured():
                url = nami_deploy.deploy_html(page_html)
                logger.info("综合评价 nami 短链部署成功 task=%s url=%s", task_id, url)
                return url
        except Exception as e:  # noqa: BLE001 nami 失败绝不阻断一条龙,回落自托管
            logger.warning("综合评价 nami 短链部署失败 task=%s,回落自托管 /r:%s", task_id, e)

    # 回落自托管 /r/<code>
    base = (settings.PLATFORM_BASE_URL or "").rstrip("/")
    if base and code:
        return f"{base}{share_path(code)}"
    return None


def run_pipeline(session_factory, task_id: int, project_id: int, task_name: str, batch_id: str) -> None:
    """一条龙四步编排(同步执行,供后台线程调用)。

    ⚠️ 每步各开一条【短命 session】(用完即关),不跨步持有长连接:综合评价那步会跑最长 15min
    的 LLM 流,若全程持一条连接,长跑后它可能已被 MySQL/中间层断掉,写终态即失败→前端一直
    「生成中」。短命 session + pool_pre_ping 保证每步都拿到活连接。各步失败不中断后续,分步发推推。
    判定/综合评价均用平台默认引擎(provider=None,即 claude),与手动判定/手动综合评价完全一致。
    """
    from app.api.eval_judge import _run_batch_judge
    from app.services import notify
    from datetime import datetime

    COLOR_BLUE, COLOR_GREEN = "blue", "green"

    # 步骤 1:对话已完成(钩子触发即代表所有 run 已达终态)
    db = session_factory()
    try:
        settled = (db.query(EvalRun)
                   .filter(EvalRun.eval_task_id == task_id, EvalRun.batch_id == batch_id,
                           EvalRun.status.in_([EvalRunStatus.done, EvalRunStatus.judged,
                                               EvalRunStatus.failed])).count())
    finally:
        db.close()
    notify.notify_eval_pipeline(task_name, project_id, "✅ 已完成对话",
                                [f"本批 {settled} 条对话执行完毕,开始批量判定…"], COLOR_BLUE)

    # 步骤 2:批量判定(复用 eval_judge 内部逻辑;provider=None 与手动批量判定同引擎)
    db = session_factory()
    try:
        judged = _run_batch_judge(db, project_id, batch_id, provider=None)
        notify.notify_eval_pipeline(task_name, project_id, "✅ 已完成批量判定",
                                    [f"已判定 {judged} 条,开始生成综合评价…"], COLOR_BLUE)
    except Exception as e:  # noqa: BLE001
        logger.exception("一条龙批量判定失败 task=%s", task_id)
        notify.notify_eval_pipeline(task_name, project_id, "⚠️ 批量判定出错",
                                    [f"原因:{e}", "跳过判定,继续综合评价…"], "orange")
    finally:
        db.close()

    # 步骤 3:综合评价(无头,短命 session,繁忙退避重试);成功则生成在线短链
    summary_res = _summary_with_retry(session_factory, task_id, batch_id)
    share_url = _summary_share_url(session_factory, task_id) if summary_res.get("ok") else None
    if summary_res.get("ok"):
        lines = ["综合评价已生成,可在平台查看 HTML 报告。"]
        if share_url:
            lines.append(f"在线报告:{share_url}")
        notify.notify_eval_pipeline(task_name, project_id, "✅ 已完成综合评价", lines, COLOR_BLUE)
    else:
        why = summary_res.get("reason") or summary_res.get("error") or "未知原因"
        notify.notify_eval_pipeline(task_name, project_id, "⚠️ 综合评价未生成",
                                    [f"原因:{why}"], "orange")

    # 步骤 4:结果摘要
    db = session_factory()
    try:
        s = _result_summary(db, task_id, batch_id)
        # 失败/异常自动建缺陷草稿(供人复核后一键上报极库云)。失败不阻断收尾。
        drafts = 0
        try:
            drafts = len(auto_issue_for_eval_failures(db, task_id, project_id, batch_id))
        except Exception:  # noqa: BLE001
            logger.exception("一条龙自动建缺陷草稿失败 task=%s", task_id)
        lines = [
            f"**结果**:共 {s['total']} 条,通过 {s['passed']},失败 {s['failed']},异常 {s['abnormal']} 条"
            + (f",平均分 {s['avg_score']}" if s['avg_score'] is not None else ""),
        ]
        if s["ab_line"]:
            lines.append(f"**A/B 对比**:{s['ab_line']}")
        if s.get("errored"):
            lines.append(f"⚠️ {s['errored']} 条判定失败/无法定论，需到测评结果页重判。")
        # 逐条结果明细(标题/结果/评分/耗时);耗时口径同前端详情(duration_ms)。条数多时截断,余量提示见平台。
        _ICON = {"pass": "✅", "fail": "❌", "error": "⚠️"}
        detail = []
        for it in s.get("items", []):
            icon = _ICON.get(it["verdict"], "⏳")
            sc = f" {it['score']}分" if it["score"] is not None else ""
            title = (it["title"] or "").strip().replace("\n", " ")[:24]
            detail.append(f"{icon} {title}{sc} · ⏱{_fmt_dur(it['duration_ms'])}")
        if detail:
            _MAX = 50
            lines.append(f"**逐条明细**（{len(detail)} 条）：")
            lines.extend(detail[:_MAX])
            if len(detail) > _MAX:
                lines.append(f"…余 {len(detail) - _MAX} 条见平台测评任务页")
        if drafts:
            lines.append(f"已自动生成 {drafts} 条缺陷草稿,请复核后上报极库云。")
        if share_url:
            lines.append(f"在线报告:{share_url}")
        lines.append("详情与综合评价见平台测评任务页。")
        notify.notify_eval_pipeline(task_name, project_id, "🎉 测评任务执行完毕", lines, COLOR_GREEN)

        # 落门闩 done
        t2 = db.get(EvalTask, task_id)
        if t2:
            t2.pipeline_status = "done"
            t2.pipeline_at = datetime.now()
            db.commit()
    finally:
        db.close()
    logger.info("测评一条龙完成 task=%s batch=%s", task_id, batch_id)
