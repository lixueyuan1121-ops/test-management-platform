"""DeepSeek 429 限流退避重试(deepseek_runner._post_with_retry)自测。
运行: cd backend && python -m scripts.test_deepseek_retry

背景:多分片并发 HTTP 直调易撞网关分钟级 token/请求配额(429/code 1005),原实现遇非 200
直接失败、5 片全灭。本测锁定「429 自动退避重试」不回归。

覆盖:
- 200 直接成功,不重试、不 sleep
- 429 若干次后转 200 → 成功,sleep 按指数退避
- 持续 429 → 重试用尽返回 err(含「限流」提示)
- Retry-After 响应头被尊重(优先于指数退避)
- 不可重试错误(如 400)→ 直接返回 err,不重试
"""
from app.services.generators import deepseek_runner as dr


class _FakeResp:
    def __init__(self, status, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}
        self.closed = False

    def close(self):
        self.closed = True


def _patch_post(responses):
    """monkeypatch requests.post 依次返回 responses 里的假响应(记录调用次数)。"""
    seq = iter(responses)
    calls = {"n": 0}

    def fake_post(*a, **kw):
        calls["n"] += 1
        return next(seq)
    dr.requests.post = fake_post
    return calls


def _rec_sleep():
    waits = []
    return waits, (lambda s: waits.append(s))


def test_success_no_retry():
    _patch_post([_FakeResp(200, "ok")])
    waits, sleep = _rec_sleep()
    resp, err = dr._post_with_retry(stream=False, json_body={}, timeout=900, sleep=sleep)
    assert err is None and resp.status_code == 200, "200 应直接成功"
    assert waits == [], "200 不该 sleep 重试"
    print("✓ 200 直接成功,不重试")


def test_429_then_success():
    _patch_post([_FakeResp(429, '{"code":1005}'), _FakeResp(429, ""), _FakeResp(200, "ok")])
    waits, sleep = _rec_sleep()
    resp, err = dr._post_with_retry(stream=False, json_body={}, timeout=900, sleep=sleep)
    assert err is None and resp.status_code == 200, "429→429→200 应最终成功"
    assert waits == [5.0, 10.0], f"应退避两次 5/10s,实际 {waits}"
    print(f"✓ 429 两次后成功,退避序列 {waits}")


def test_429_exhausted():
    _patch_post([_FakeResp(429, '{"code":1005,"message":"频繁"}')] * 10)
    waits, sleep = _rec_sleep()
    resp, err = dr._post_with_retry(stream=False, json_body={}, timeout=900, sleep=sleep)
    assert resp is None and err is not None, "持续 429 应返回 err"
    assert "限流" in err and "429" in err, f"err 应含限流提示,实际:{err}"
    assert len(waits) == dr._MAX_RETRIES, f"应退避 {dr._MAX_RETRIES} 次,实际 {len(waits)}"
    print(f"✓ 持续 429 重试用尽,退避 {len(waits)} 次后失败:{err[:40]}…")


def test_retry_after_header():
    _patch_post([_FakeResp(429, "", {"Retry-After": "3"}), _FakeResp(200, "ok")])
    waits, sleep = _rec_sleep()
    resp, err = dr._post_with_retry(stream=False, json_body={}, timeout=900, sleep=sleep)
    assert err is None, "有 Retry-After 且随后 200 应成功"
    assert waits == [3.0], f"应尊重 Retry-After=3s,实际 {waits}"
    print("✓ Retry-After 响应头被尊重(优先指数退避)")


def test_non_retryable():
    _patch_post([_FakeResp(400, '{"error":"bad request"}')])
    waits, sleep = _rec_sleep()
    resp, err = dr._post_with_retry(stream=False, json_body={}, timeout=900, sleep=sleep)
    assert resp is None and err is not None, "400 应返回 err"
    assert "400" in err and "限流" not in err, f"400 非限流,不该带限流提示:{err}"
    assert waits == [], "400 不该重试"
    print("✓ 400 不可重试错误,直接失败不重试")


def main():
    test_success_no_retry()
    test_429_then_success()
    test_429_exhausted()
    test_retry_after_header()
    test_non_retryable()
    print("\n✅ DeepSeek 退避重试全部通过")


if __name__ == "__main__":
    main()
