"""summary busy-retry selftest. run: cd backend && .venv/bin/python -m scripts.test_eval_summary_retry

收敛后语义:**仅**并发繁忙(_is_busy_error)才退避重试;超时/报错/无输出立即收口不重试
(每次重试都要再吃一个 15min 硬超时,叠加会把前端「生成中」拖到 ~60min——线上实测的坑)。
"""
from app.services import eval_pipeline as ep

ep._SUMMARY_RETRY_SLEEP = 0.0

# 引擎繁忙的真实消息(claude_runner: "AI 生成繁忙（已达并发上限），请稍后重试")
BUSY_MSG = "AI 生成繁忙（已达并发上限），请稍后重试"
TIMEOUT_MSG = "生成超时（>900s）"


class _FakeDB:
    def get(self, model, pk):
        return object()


def _run(fake):
    import app.api.eval_task as et
    orig = et.generate_task_summary_headless
    et.generate_task_summary_headless = fake
    try:
        return ep._summary_with_retry(_FakeDB(), 1, "b1")
    finally:
        et.generate_task_summary_headless = orig


def test_busy_retry_then_ok():
    calls = {"n": 0}

    def fake(db, task, batch_id, provider=None):
        calls["n"] += 1
        return {"ok": True} if calls["n"] >= 3 else {"error": BUSY_MSG}

    res = _run(fake)
    assert res.get("ok") is True, res
    assert calls["n"] == 3, calls
    print("OK 繁忙重试直至成功")


def test_skipped_no_retry():
    calls = {"n": 0}

    def fake(db, task, batch_id, provider=None):
        calls["n"] += 1
        return {"skipped": True}

    res = _run(fake)
    assert res.get("skipped") is True and calls["n"] == 1, calls
    print("OK skipped 不重试")


def test_persistent_busy_gives_up():
    calls = {"n": 0}

    def fake(db, task, batch_id, provider=None):
        calls["n"] += 1
        return {"error": BUSY_MSG}

    res = _run(fake)
    assert "error" in res and calls["n"] == ep._SUMMARY_RETRY, calls
    print("OK 持续繁忙用满重试次数后放弃")


def test_timeout_no_retry():
    """超时不是繁忙:重试只会再吃一个 15min 超时,必须立即收口(calls==1)。"""
    calls = {"n": 0}

    def fake(db, task, batch_id, provider=None):
        calls["n"] += 1
        return {"error": TIMEOUT_MSG}

    res = _run(fake)
    assert "error" in res and calls["n"] == 1, calls
    print("OK 超时不重试(立即收口)")


def test_generic_error_no_retry():
    calls = {"n": 0}

    def fake(db, task, batch_id, provider=None):
        calls["n"] += 1
        return {"error": "引擎没有产出有效 HTML 评价"}

    res = _run(fake)
    assert "error" in res and calls["n"] == 1, calls
    print("OK 一般错误不重试")


def main():
    test_busy_retry_then_ok()
    test_skipped_no_retry()
    test_persistent_busy_gives_up()
    test_timeout_no_retry()
    test_generic_error_no_retry()
    print("OK test_eval_summary_retry")


if __name__ == "__main__":
    main()
