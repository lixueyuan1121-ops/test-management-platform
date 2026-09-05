"""测评任务「多执行机分片」按 run 数均衡分配自测(无框架,python -m scripts.test_eval_dispatch_balance)。

背景:原 dispatch_task_runs 按「会话组」轮转分配设备(next_idx++),组数均衡但——多轮组=1 个轮转位
却含多条 run(每轮一条)。混了多轮题时:一台拿到多轮大组(多 run)、另一台拿几个单轮 →
组数均衡但 run 数不均(真机:昨天两台一多一少)。改为 LPT(大组优先给当前累计 run 数最少的机),
run 数天然均衡;多轮组仍整组同机(不断上下文)。
"""
from app.api.eval_task import assign_groups_balanced


def _load(groups, mapping, runners):
    load = {r: 0 for r in runners}
    for gk, w in groups:
        load[mapping[gk]] += w
    return load


def test_multiturn_mixed_balances_by_run_count():
    # M 多轮(3 run) + 3 个单轮,2 台。旧轮转 = 4/2;LPT 应 3/3。
    groups = [("M", 3), ("q1", 1), ("q2", 1), ("q3", 1)]
    m = assign_groups_balanced(groups, ["dev0", "dev1"])
    load = _load(groups, m, ["dev0", "dev1"])
    assert load["dev0"] == 3 and load["dev1"] == 3, f"run 数应均衡 3/3,实际 {load}"
    print("✓ 混多轮:按 run 数均衡 3/3(旧轮转会 4/2)")


def test_all_singleton_even():
    groups = [("q1", 1), ("q2", 1), ("q3", 1), ("q4", 1)]
    m = assign_groups_balanced(groups, ["a", "b"])
    load = _load(groups, m, ["a", "b"])
    assert load["a"] == 2 and load["b"] == 2, f"全单轮应 2/2,实际 {load}"
    print("✓ 全单轮:2/2 均衡")


def test_same_group_maps_single_runner():
    # 同一 group_key 只映射到一台(多轮整组同机,上下文不断)
    m = assign_groups_balanced([("M", 5)], ["a", "b"])
    assert m["M"] in ("a", "b")
    print("✓ 同组只落一台设备")


def test_single_runner_gets_all():
    groups = [("q1", 1), ("q2", 1)]
    m = assign_groups_balanced(groups, ["only"])
    assert m == {"q1": "only", "q2": "only"}
    print("✓ 单台执行机:全部归它")


def test_deterministic_reproducible():
    groups = [("M", 3), ("q1", 1), ("q2", 1)]
    m1 = assign_groups_balanced(groups, ["a", "b"])
    m2 = assign_groups_balanced(groups, ["a", "b"])
    assert m1 == m2, "同输入应同输出(可复现)"
    print("✓ 分配确定可复现")


def test_three_runners_weighted():
    # 3 台:[5,3,1,1,1,1] → 理想各 4。LPT:5→A(5),3→B(3),1→C(1),1→C(2),1→C(3),1→B(4)... 验证极差≤2
    groups = [("g5", 5), ("g3", 3), ("a", 1), ("b", 1), ("c", 1), ("d", 1)]
    m = assign_groups_balanced(groups, ["A", "B", "C"])
    load = _load(groups, m, ["A", "B", "C"])
    spread = max(load.values()) - min(load.values())
    assert spread <= 2, f"3 台负载极差应≤2(LPT 近似最优),实际 {load}"
    print(f"✓ 3 台加权:负载 {load},极差 {spread}")


def main():
    test_multiturn_mixed_balances_by_run_count()
    test_all_singleton_even()
    test_same_group_maps_single_runner()
    test_single_runner_gets_all()
    test_deterministic_reproducible()
    test_three_runners_weighted()
    print("\n✅ 分片均衡分配 全部通过")


if __name__ == "__main__":
    main()
