"""AI job **各 handler** 写库断连自愈自测(972105f4 只修了 testcase_gen,本测覆盖其余长跑 handler)。
运行: cd backend && python -m scripts.test_ai_handler_resilience

背景:每个长跑 handler(对话 query 生成 / 归因 / 判定 / 失败聚类)都曾「借着 worker 传入的 db
连接跑几十秒 LLM,生成完再用同一条(已被中间层空闲掐断的)连接写库」→ 2013 Lost connection。
pool_pre_ping/pool_recycle 管不到「一直借着、从不归还的连接」;根治=生成期释放连接 + 写库走
_persist_with_retry(全新 session,首次断连即重连重放)。本测锁定各 handler 都已接入该韧性件。

注入手法:patch 各 handler 模块的 sessionmaker,令其产出「首次 commit 抛 OperationalError」的
faulty session。修复前 handler 直接用传入 db.commit()、根本不碰 sessionmaker → faulty 未触发 →
断言「重试发生」失败(干净 RED);修复后走 _persist_with_retry(sessionmaker(bind=...)) → 重试成功。
"""
import json
import types
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from app.core.enums import AiInputType, AiTaskStatus
from app.db.session import Base
from app.models import AiTask, EvalQuery  # noqa: F401  触发 app.models.__init__ 注册全表

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)


# ── 故障注入脚手架 ────────────────────────────────────────────────────────────────
class _FaultyCommit:
    """包装真实 session:前 fail_times 次 commit 抛 OperationalError(模拟写库瞬间 2013),其后正常。

    after_commit=True:先真提交再抛(模拟服务端已落库、客户端才断连的「半成功」)——用于测重放幂等
    (整段重放若不先清半成品,会重复插入)。commit 是本类真方法;其余(get/add/query/rollback/close…)
    经 __getattr__ 透传真实 session。
    """
    def __init__(self, real, counter, fail_times, after_commit):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_counter", counter)
        object.__setattr__(self, "_fail_times", fail_times)
        object.__setattr__(self, "_after_commit", after_commit)

    def commit(self):
        self._counter["n"] += 1
        if self._after_commit:
            self._real.commit()
        if self._counter["n"] <= self._fail_times:
            raise OperationalError("COMMIT", {}, Exception("2013 Lost connection"))
        if not self._after_commit:
            self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _faulty_factory(counter, fail_times=1, after_commit=False):
    """返回一个 sessionmaker 替身:被 handler 以 sessionmaker(bind=...) 调用后,产出的 factory 造 faulty session。"""
    def factory(*_a, **_k):
        return _FaultyCommit(_Session(), counter, fail_times, after_commit)
    return factory


class _FakeEngine:
    """固定产出一段 result 文本;解析由各测试 patch parse_* 决定,故 text 内容无所谓。"""
    def is_available(self):
        return True

    def stream_generate(self, *_a, **_k):
        yield {"type": "result", "text": "[]", "duration_ms": 120, "cost_usd": 0.5, "output_tokens": 42}


def _truncate(*models):
    s = _Session()
    for m in models:
        s.query(m).delete()
    s.commit()
    s.close()


def _job(**inp):
    return types.SimpleNamespace(input=json.dumps(inp), project_id=inp.get("project_id"))


_EVAL_Q = [{"title": "t", "prompt": "p", "dimension": "tool_use", "expected": "e",
            "attachments": None, "conversation_group": "g1", "turn_index": 0}]


# ── eval_query_gen ───────────────────────────────────────────────────────────────
def _seed_ai_task(kind="eval_query_gen") -> int:
    s = _Session()
    at = AiTask(project_id=1, user_id=1, kind=kind,
                input_type=AiInputType.url, status=AiTaskStatus.running)
    s.add(at); s.commit()
    at_id = at.id
    s.close()
    return at_id


def test_eval_query_gen_retries_on_write_disconnect():
    """对话 query 生成:写库首次断连 → 重连重试成功,EvalQuery 落库、AiTask=done(不丢结果/不僵死)。"""
    import app.api.ai_eval as m
    _truncate(EvalQuery, AiTask)
    at_id = _seed_ai_task()
    counter = {"n": 0}
    with patch.object(m.generators, "get_provider", return_value=_FakeEngine()), \
         patch.object(m.generators, "normalize_provider", side_effect=lambda x: x or "claude"), \
         patch.object(m.claude_runner, "parse_eval_queries", return_value=_EVAL_Q), \
         patch.object(m, "sessionmaker", return_value=_faulty_factory(counter), create=True), \
         patch("app.services.ai_jobs.time.sleep"):
        db = _Session()
        m.run_eval_query_gen_job(db, _job(ai_task_id=at_id, project_id=1,
                                          requirement="r", dimensions=["tool_use"], provider="claude"))
        db.close()
    s = _Session()
    at = s.get(AiTask, at_id)
    n_q = s.query(EvalQuery).filter_by(ai_task_id=at_id).count()
    s.close()
    assert counter["n"] >= 2, f"写库应经故障 factory 重试(证明接入 _persist_with_retry+新 session),commit 次数={counter['n']}"
    assert at.status == AiTaskStatus.done, f"重试后 AiTask 应 done,实际 {at.status}"
    assert n_q == 1, f"应落 1 条 EvalQuery,实际 {n_q}"
    print("✓ eval_query_gen:写库断连重连重试成功,结果不丢、AiTask 落 done")


def test_eval_query_gen_replay_no_duplicate():
    """半成功重放幂等:首次 commit 服务端已落库才断连,重放前须按 ai_task_id 清半成品,不得重复插入。"""
    import app.api.ai_eval as m
    _truncate(EvalQuery, AiTask)
    at_id = _seed_ai_task()
    counter = {"n": 0}
    with patch.object(m.generators, "get_provider", return_value=_FakeEngine()), \
         patch.object(m.generators, "normalize_provider", side_effect=lambda x: x or "claude"), \
         patch.object(m.claude_runner, "parse_eval_queries", return_value=_EVAL_Q), \
         patch.object(m, "sessionmaker",
                      return_value=_faulty_factory(counter, fail_times=1, after_commit=True), create=True), \
         patch("app.services.ai_jobs.time.sleep"):
        db = _Session()
        m.run_eval_query_gen_job(db, _job(ai_task_id=at_id, project_id=1,
                                          requirement="r", dimensions=["tool_use"], provider="claude"))
        db.close()
    s = _Session()
    n_q = s.query(EvalQuery).filter_by(ai_task_id=at_id).count()
    s.close()
    assert n_q == 1, f"半成功重放不得重复插入 EvalQuery,应为 1,实际 {n_q}"
    print("✓ eval_query_gen:半成功重放幂等,不重复插入 EvalQuery")


# ── triage(归因)───────────────────────────────────────────────────────────────
def _seed_exec_run(**over) -> int:
    from app.core.enums import ExecStatus
    from app.models import ExecRun
    s = _Session()
    r = ExecRun(project_id=1, payload=json.dumps({"title": "登录失败"}),
                status=ExecStatus.failed, reason="元素未找到", fail_kind="assert", **over)
    s.add(r); s.commit()
    rid = r.id
    s.close()
    return rid


def test_triage_retries_on_write_disconnect():
    """失败归因:长跑后写库首次断连 → 重连重试成功,run.triage 落库(覆盖写,天然幂等)。"""
    import app.services.exec_triage as m
    from app.models import ExecRun
    _truncate(ExecRun)
    rid = _seed_exec_run()
    counter = {"n": 0}
    with patch.object(m.generators, "get_provider", return_value=_FakeEngine()), \
         patch.object(m.generators, "normalize_provider", side_effect=lambda x: x or "claude"), \
         patch.object(m, "parse_triage", return_value={"kind": "env", "summary": "环境问题"}), \
         patch.object(m, "sessionmaker", return_value=_faulty_factory(counter), create=True), \
         patch("app.services.ai_jobs.time.sleep"):
        db = _Session()
        m.run_triage_job(db, _job(run_id=rid, provider="claude"))
        db.close()
    s = _Session()
    r = s.get(ExecRun, rid)
    s.close()
    assert counter["n"] >= 2, f"写库应经故障 factory 重试(证明接入 _persist_with_retry+新 session),commit 次数={counter['n']}"
    assert r.triage_kind == "env" and r.triage, f"归因应落库,实际 kind={r.triage_kind} triage={r.triage!r}"
    print("✓ triage:写库断连重连重试成功,归因落库")


# ── fail_cluster(失败聚类命名)──────────────────────────────────────────────────
_CLUSTERS = [{"fingerprint": "env-abc", "triage_kind": "env", "run_ids": [1, 2],
              "requirement_ids": [10], "member_count": 2, "sample": {"reason": "x"}}]
_NAMING = {"root_cause_title": "环境不稳", "summary": "s", "severity": "major", "confidence": 0.8}


def test_fail_cluster_retries_and_replay_no_duplicate():
    """失败聚类:长跑(逐簇 LLM 命名)后写库首次断连 → 重连重试;重放靠 batch_key 先删,不重复插入 FailCluster。"""
    import app.services.fail_cluster as m
    from app.models import FailCluster
    _truncate(FailCluster)
    counter = {"n": 0}
    with patch.object(m.generators, "get_provider", return_value=_FakeEngine()), \
         patch.object(m, "_pick_provider", return_value="claude"), \
         patch.object(m, "collect_failed_runs", return_value=[{"id": 1}, {"id": 2}]), \
         patch.object(m, "rule_cluster", return_value=[dict(c) for c in _CLUSTERS]), \
         patch.object(m, "_name_one", return_value=dict(_NAMING)), \
         patch.object(m, "sessionmaker",
                      return_value=_faulty_factory(counter, fail_times=1, after_commit=True), create=True), \
         patch("app.services.ai_jobs.time.sleep"):
        db = _Session()
        job = _job(release_id=5, batch_key="b1", project_id=1)
        job.id = 99
        m.run_fail_cluster_job(db, job)
        db.close()
    s = _Session()
    n = s.query(FailCluster).filter_by(batch_key="b1").count()
    s.close()
    assert counter["n"] >= 2, f"写库应经故障 factory 重试(证明接入 _persist_with_retry+新 session),commit 次数={counter['n']}"
    assert n == 1, f"半成功重放靠 batch_key 先删,不得重复插入,应为 1,实际 {n}"
    print("✓ fail_cluster:写库断连重连重试 + batch_key 重放幂等,不重复插入")


def main():
    test_eval_query_gen_retries_on_write_disconnect()
    test_eval_query_gen_replay_no_duplicate()
    test_triage_retries_on_write_disconnect()
    test_fail_cluster_retries_and_replay_no_duplicate()
    print("\n✅ AI handler 写库断连自愈 全部通过")


if __name__ == "__main__":
    main()
