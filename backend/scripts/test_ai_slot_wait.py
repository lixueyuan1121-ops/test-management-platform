"""AI 引擎并发闸:超限「排队等待」而非「立即拒绝」(方案2 P3a)自测。
运行: cd backend && python -m scripts.test_ai_slot_wait

覆盖 _acquire_slot(slots, timeout):
- 有空槽 → 立即拿到(True)。
- 满槽 + timeout=0 → 不等,立即 False(旧「拒绝」行为,可配置回退)。
- 满槽 + timeout>0 + 另一线程稍后释放 → 排队等到并拿到(True)。
- 满槽 + timeout 很短且无人释放 → 超时 False。
"""
import threading
import time

from app.services.claude_runner import _acquire_slot


def test_free_slot_acquires():
    sem = threading.BoundedSemaphore(1)
    assert _acquire_slot(sem, timeout=0) is True
    sem.release()
    print("OK free slot acquires")


def test_full_timeout0_rejects():
    sem = threading.BoundedSemaphore(1)
    sem.acquire()  # 占满
    assert _acquire_slot(sem, timeout=0) is False, "满槽 timeout=0 应立即拒绝"
    sem.release()
    print("OK full+timeout0 rejects")


def test_full_waits_until_released():
    sem = threading.BoundedSemaphore(1)
    sem.acquire()  # 占满

    def _release_soon():
        time.sleep(0.2)
        sem.release()
    threading.Thread(target=_release_soon, daemon=True).start()
    t0 = time.monotonic()
    got = _acquire_slot(sem, timeout=3)
    waited = time.monotonic() - t0
    assert got is True, "应排队等到释放后拿到槽"
    assert waited >= 0.15, f"应确实等待了(实际 {waited:.2f}s)"
    sem.release()
    print("OK full waits until released")


def test_full_times_out():
    sem = threading.BoundedSemaphore(1)
    sem.acquire()  # 占满,无人释放
    got = _acquire_slot(sem, timeout=0.3)
    assert got is False, "无人释放应超时返回 False(而非永久阻塞)"
    sem.release()
    print("OK full times out")


def main():
    test_free_slot_acquires()
    test_full_timeout0_rejects()
    test_full_waits_until_released()
    test_full_times_out()
    print("OK test_ai_slot_wait")


if __name__ == "__main__":
    main()
