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
    """后台线程:独立 Session 跑完整编排,末尾把门闩落 done/failed。异常全捕获(线程不能抛)。"""
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        run_pipeline(db, task_id, project_id, task_name, batch_id)
    except Exception:
        logger.exception("测评一条龙编排异常 task=%s", task_id)
        try:
            t = db.get(EvalTask, task_id)
            if t:
                t.pipeline_status = "failed"; db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def _result_summary(db: Session, task_id: int, batch_id: str) -> dict:
    """收尾摘要指标:通过/失败/异常/均分 + A/B 胜率(若有对比组)。"""
    import json

    rows = (db.query(EvalRun)
            .filter(EvalRun.eval_task_id == task_id, EvalRun.batch_id == batch_id).all())
    # 只统计可评的(排除 cancelled)
    rows = [r for r in rows if getattr(r.status, "value", r.status) != "cancelled"]
    total = len(rows)
    passed = sum(1 for r in rows if r.verdict == "pass")
    failed = sum(1 for r in rows if r.verdict == "fail")
    errored = sum(1 for r in rows if r.verdict == "error")   # 判定失败/无法定论,待重判
    abnormal = sum(1 for r in rows if r.is_abnormal)
    scores = [r.score for r in rows if r.score is not None]
    avg_score = round(sum(scores) / len(scores), 2) if scores else None
    # A/B 胜率:按 compare_group 统计各组 pass 率(payload 里的 compare_group)
    ab = {"A": [0, 0], "B": [0, 0]}   # group -> [pass, total]
    has_ab = False
    for r in rows:
        try:
            p = json.loads(r.payload) if r.payload else {}
        except (ValueError, TypeError):
            p = {}
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
            "errored": errored, "avg_score": avg_score, "ab_line": ab_line}


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


def run_pipeline(db: Session, task_id: int, project_id: int, task_name: str, batch_id: str) -> None:
    """一条龙四步编排(同步执行,供后台线程调用)。各步失败不中断后续,分步发飞书。"""
    from app.api.eval_judge import _run_batch_judge
    from app.api.eval_task import generate_task_summary_headless
    from app.core.config import settings
    from app.services import notify
    from datetime import datetime

    COLOR_BLUE, COLOR_GREEN = "blue", "green"

    # 步骤 1:对话已完成(钩子触发即代表所有 run 已达终态)
    settled = (db.query(EvalRun)
               .filter(EvalRun.eval_task_id == task_id, EvalRun.batch_id == batch_id,
                       EvalRun.status.in_([EvalRunStatus.done, EvalRunStatus.judged,
                                           EvalRunStatus.failed])).count())
    notify.notify_eval_pipeline(task_name, project_id, "✅ 已完成对话",
                                [f"本批 {settled} 条对话执行完毕,开始批量判定…"], COLOR_BLUE)

    # 步骤 2:批量判定(复用 eval_judge 内部逻辑)
    try:
        judged = _run_batch_judge(db, project_id, batch_id, provider=settings.EVAL_PIPELINE_PROVIDER or None)
        notify.notify_eval_pipeline(task_name, project_id, "✅ 已完成批量判定",
                                    [f"已判定 {judged} 条,开始生成综合评价…"], COLOR_BLUE)
    except Exception as e:  # noqa: BLE001
        logger.exception("一条龙批量判定失败 task=%s", task_id)
        notify.notify_eval_pipeline(task_name, project_id, "⚠️ 批量判定出错",
                                    [f"原因:{e}", "跳过判定,继续综合评价…"], "orange")

    # 步骤 3:综合评价(无头)
    task = db.get(EvalTask, task_id)
    summary_res = generate_task_summary_headless(db, task, batch_id,
                                                 provider=settings.EVAL_PIPELINE_PROVIDER or None)
    if summary_res.get("ok"):
        notify.notify_eval_pipeline(task_name, project_id, "✅ 已完成综合评价",
                                    ["综合评价已生成,可在平台查看 HTML 报告。"], COLOR_BLUE)
    else:
        why = summary_res.get("reason") or summary_res.get("error") or "未知原因"
        notify.notify_eval_pipeline(task_name, project_id, "⚠️ 综合评价未生成",
                                    [f"原因:{why}"], "orange")

    # 步骤 4:结果摘要
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
    if drafts:
        lines.append(f"已自动生成 {drafts} 条缺陷草稿,请复核后上报极库云。")
    lines.append("详情与综合评价见平台测评任务页。")
    notify.notify_eval_pipeline(task_name, project_id, "🎉 测评任务执行完毕", lines, COLOR_GREEN)

    # 落门闩 done
    t2 = db.get(EvalTask, task_id)
    if t2:
        t2.pipeline_status = "done"
        t2.pipeline_at = datetime.now()
        db.commit()
    logger.info("测评一条龙完成 task=%s batch=%s", task_id, batch_id)
