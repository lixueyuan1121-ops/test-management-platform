"""用例关联前置 + 入队展开顺序回归。

需求:用例库可给主用例关联「前置用例」;执行主用例时,前置按 sort_order 先执行、再执行主用例。
后端入队侧(enqueue_case_runs)展开:对每个下发用例,先插其前置的 ExecRun、再插自己;
因执行顺序 = ExecRun.id 升序(入队顺序),前置自然先跑。展开需去重、防环。
"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import Project, AiTask, TestCase, ExecRun, TestCaseLink


def _fresh():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng)
    s = S()
    s.add(Project(id=2, name="P", code="test", status="active"))
    s.add(AiTask(id=1, project_id=2, user_id=1, input_type="text", status="done"))
    for cid, title in [(10, "前置A"), (11, "前置B"), (12, "主用例C"), (13, "无关D")]:
        s.add(TestCase(id=cid, ai_task_id=1, project_id=2, title=title, exec_kind="gui",
                       review_status="adopted",
                       script='[{"action":"connect","target":{},"args":{},"desc":"连"}]'))
    s.commit()
    return s


def _link(s, case_id, prereq_id, order=0):
    s.add(TestCaseLink(case_id=case_id, prereq_case_id=prereq_id, sort_order=order))
    s.commit()


def _enqueue(s, ids):
    from app.api.exec_queue import enqueue_case_runs
    user = SimpleNamespace(id=1, is_platform_admin=True)
    body = SimpleNamespace(project_id=2, runner="bendi-win", test_case_ids=ids,
                           release_id=None, auto_prepare=False)
    return enqueue_case_runs(s, user, body, commit=True)


def _order(s):
    """返回入队产生的 ExecRun 按执行序(id 升序)的 test_case_id 列表。"""
    rows = s.query(ExecRun).order_by(ExecRun.id).all()
    return [r.test_case_id for r in rows]


def test_prereq_runs_before_main():
    """主用例 C 挂前置 A(order0)、B(order1);下发 [C] → 执行序应为 [A, B, C]。"""
    s = _fresh()
    _link(s, 12, 10, 0)
    _link(s, 12, 11, 1)
    _enqueue(s, [12])
    assert _order(s) == [10, 11, 12], _order(s)
    print("OK prereq_runs_before_main")


def test_dedup_when_prereq_also_selected():
    """前置 A 同时被用户勾选:不应重复入队,最终 [A, C] 各一次。"""
    s = _fresh()
    _link(s, 12, 10, 0)
    order = (_enqueue(s, [10, 12]), _order(s))[1]
    assert order.count(10) == 1, order
    assert order.count(12) == 1, order
    assert order.index(10) < order.index(12), order   # A 仍在 C 前
    print("OK dedup_when_prereq_also_selected")


def test_cycle_does_not_hang():
    """环:A 前置 C、C 前置 A;下发 [C] 不应死循环,每条最多一次。"""
    s = _fresh()
    _link(s, 12, 10, 0)   # C 的前置是 A
    _link(s, 10, 12, 0)   # A 的前置是 C(成环)
    _enqueue(s, [12])
    order = _order(s)
    assert order.count(10) == 1 and order.count(12) == 1, order
    print("OK cycle_does_not_hang")


def test_no_link_keeps_original_order():
    """无任何关联:下发 [12, 13] 保持原顺序,不引入额外 run。"""
    s = _fresh()
    _enqueue(s, [12, 13])
    assert _order(s) == [12, 13], _order(s)
    print("OK no_link_keeps_original_order")


def main():
    test_prereq_runs_before_main()
    test_dedup_when_prereq_also_selected()
    test_cycle_does_not_hang()
    test_no_link_keeps_original_order()
    print("ALL OK")


if __name__ == "__main__":
    main()
