"""summary busy-retry selftest. run: cd backend && python -m scripts.test_eval_summary_retry"""
from app.services import eval_pipeline as ep

ep._SUMMARY_RETRY_SLEEP = 0.0


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


def test_retry_then_ok():
    calls = {"n": 0}

    def fake(db, task, batch_id, provider=None):
        calls["n"] += 1
        return {"ok": True} if calls["n"] >= 3 else {"error": "busy"}

    res = _run(fake)
    assert res.get("ok") is True, res
    assert calls["n"] == 3, calls
    print("OK retry then ok")


def test_skipped_no_retry():
    calls = {"n": 0}

    def fake(db, task, batch_id, provider=None):
        calls["n"] += 1
        return {"skipped": True}

    res = _run(fake)
    assert res.get("skipped") is True and calls["n"] == 1, calls
    print("OK skipped no retry")


def test_persistent_busy_gives_up():
    calls = {"n": 0}

    def fake(db, task, batch_id, provider=None):
        calls["n"] += 1
        return {"error": "busy"}

    res = _run(fake)
    assert "error" in res and calls["n"] == ep._SUMMARY_RETRY, calls
    print("OK persistent busy gives up")


def main():
    test_retry_then_ok()
    test_skipped_no_retry()
    test_persistent_busy_gives_up()
    print("OK test_eval_summary_retry")


if __name__ == "__main__":
    main()
