"""分片并行生成测试点 自测(假引擎,不调真模型)。
运行: cd backend && python -m scripts.test_shard_parallel

锁住四件事:
1. **真并行**:K 片同时在跑(用 Barrier 验证——串行执行会在 barrier 上超时)。
2. 合并去重:各片结果汇总,同名 title 只落一条(分片边界靠 prompt 约束,兜底仍要去重)。
3. 部分失败不整批失败:一片抛异常/无产出,其余片照常落地,失败信息可回传。
4. 全失败 → cases 空 + errors 非空(调用方据此落 AiTask=failed)。
"""
import threading

from app.services.generators.sharded import generate_sharded


class FakeEngine:
    """假引擎:每片按 shard id 产 1-2 条用例;可注入 barrier(验并发)与失败片。"""

    def __init__(self, barrier=None, fail_ids=(), dup_title=None):
        self.barrier = barrier
        self.fail_ids = set(fail_ids)
        self.dup_title = dup_title
        self.calls = []          # 记录每片实际拿到的 shard id
        self.threads = set()
        self._lock = threading.Lock()

    def build_testcase_prompt(self, requirement, project_id=None, pages=None, shard=None):
        return f"PROMPT::{shard['id'] if shard else 'full'}::{requirement}"

    def stream_generate(self, requirement, project_id=None, timeout=None, pages=None,
                        prompt_builder=None, system_prompt=None):
        prompt = prompt_builder() if prompt_builder else "full"
        sid = prompt.split("::")[1]
        with self._lock:
            self.calls.append(sid)
            self.threads.add(threading.current_thread().name)
        if self.barrier is not None:
            self.barrier.wait()      # 串行跑会在此超时 → 证明并行
        if sid in self.fail_ids:
            raise RuntimeError(f"片 {sid} 引擎炸了")
        yield {"type": "delta", "text": f"RAW::{sid}"}
        yield {"type": "result", "text": f"RAW::{sid}", "output_tokens": 10,
               "cost_usd": 0.5, "duration_ms": 100}

    def parse_testcases(self, raw, project_id=None):
        sid = raw.split("::")[1]
        title = self.dup_title or f"{sid} 用例"
        return [{"category": "功能", "title": title, "steps": "", "expected": "",
                 "priority": "P1", "kind": "manual", "kind_reason": "", "script": None, "page": None}]


_SHARDS = [{"id": "a", "name": "A", "kinds": "gui/e2e", "focus": "f", "exclude": "e"},
           {"id": "b", "name": "B", "kinds": "gui/e2e", "focus": "f", "exclude": "e"},
           {"id": "c", "name": "C", "kinds": "api", "focus": "f", "exclude": "e"}]


def test_runs_shards_in_parallel():
    """K 片必须同时在跑:Barrier(K) 在串行实现下必然超时。"""
    barrier = threading.Barrier(3, timeout=5)
    eng = FakeEngine(barrier=barrier)
    res = generate_sharded(eng, "需求", project_id=None, shards=_SHARDS)
    assert sorted(eng.calls) == ["a", "b", "c"], f"每片各跑一次:{eng.calls}"
    assert len(eng.threads) == 3, f"应在 3 个线程并行,实际:{eng.threads}"
    assert len(res["cases"]) == 3, res["cases"]
    assert not res["errors"], res["errors"]


def test_merges_and_dedups():
    """各片产出同名 title(边界没切干净)时兜底去重,只落一条。"""
    eng = FakeEngine(dup_title="同一条用例")
    res = generate_sharded(eng, "需求", project_id=None, shards=_SHARDS)
    assert len(res["cases"]) == 1, f"同名 title 应去重:{res['cases']}"
    assert res["dropped_dup"] == 2, f"应记录去重条数:{res}"


def test_partial_failure_keeps_rest():
    """一片炸了不拖垮整批:其余片照常产出,失败信息回传。"""
    eng = FakeEngine(fail_ids=["b"])
    res = generate_sharded(eng, "需求", project_id=None, shards=_SHARDS)
    titles = sorted(c["title"] for c in res["cases"])
    assert titles == ["a 用例", "c 用例"], titles
    assert len(res["errors"]) == 1 and "b" in res["errors"][0], res["errors"]


def test_all_failed():
    """全片失败 → cases 空 + errors 齐(调用方据此落 AiTask=failed)。"""
    eng = FakeEngine(fail_ids=["a", "b", "c"])
    res = generate_sharded(eng, "需求", project_id=None, shards=_SHARDS)
    assert res["cases"] == []
    assert len(res["errors"]) == 3, res["errors"]


def test_meta_aggregated():
    """成本/token 求和,耗时取墙钟最大值(并行,不能求和)。"""
    eng = FakeEngine()
    res = generate_sharded(eng, "需求", project_id=None, shards=_SHARDS)
    assert res["meta"]["output_tokens"] == 30, res["meta"]
    assert abs(res["meta"]["cost_usd"] - 1.5) < 1e-9, res["meta"]
    assert res["meta"]["duration_ms"] == 100, "并行耗时应取最大值而非求和"


def main():
    test_runs_shards_in_parallel()
    test_merges_and_dedups()
    test_partial_failure_keeps_rest()
    test_all_failed()
    test_meta_aggregated()
    print("OK test_shard_parallel")


if __name__ == "__main__":
    main()
