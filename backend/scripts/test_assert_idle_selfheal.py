"""assert_idle 遇到心跳超时的陈旧 exec running 时自愈（claim 即时收口）回归。

线上问题：runner 强关终端＝进程猝死、来不及回写，后端那条 ExecRun 永久 running。
下次 claim 走 assert_idle 被 409「该设备正在执行用例」挡住，要干等 2 小时全局 reaper。
修复：assert_idle 在判定前，先把该设备名下“心跳超时”的 running 就地收口为 failed，
使设备下次拉活即自愈；心跳新鲜的 running 仍应挡（防真正的并发重跑）。
"""
import unittest
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import ExecRun, RunnerDevice, User
from app.services import selector_device


def _fresh_db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


class AssertIdleSelfHealTests(unittest.TestCase):
    def setUp(self):
        self.Session = _fresh_db()
        self.db = self.Session()
        u = User(username="u", name="U", password_hash="x")
        self.db.add(u); self.db.flush()
        self.dev = RunnerDevice(owner_id=u.id, runner_id="nanliru-win", name="win", token="tok-x")
        self.db.add(self.dev); self.db.commit()

    def _run(self, status, heartbeat_min_ago=None, started_min_ago=None):
        r = ExecRun(project_id=1, runner="nanliru-win", runner_device_id=self.dev.id,
                    payload='{"title":"A"}', status=status, batch_id="b1")
        self.db.add(r); self.db.flush()
        now = datetime.utcnow()
        if heartbeat_min_ago is not None:
            r.heartbeat_at = now - timedelta(minutes=heartbeat_min_ago)
        if started_min_ago is not None:
            r.started_at = now - timedelta(minutes=started_min_ago)
        r.updated_at = now - timedelta(minutes=(started_min_ago or heartbeat_min_ago or 0))
        self.db.commit()
        return r

    def test_stale_running_is_reaped_and_idle_passes(self):
        """心跳超时的陈旧 running：assert_idle 应就地收口它并放行（不抛 409）。"""
        r = self._run("running", heartbeat_min_ago=30)
        selector_device.assert_idle(self.db, self.dev.id)   # 不应抛
        self.db.refresh(r)
        self.assertEqual(r.status, "failed")
        self.assertIsNotNone(r.reason)

    def test_fresh_running_still_blocks(self):
        """心跳新鲜的 running：仍应挡住（真正在跑，防并发重跑）。"""
        self._run("running", heartbeat_min_ago=1)
        with self.assertRaises(HTTPException) as ctx:
            selector_device.assert_idle(self.db, self.dev.id)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_running_without_heartbeat_uses_started_at(self):
        """从未发过心跳（heartbeat_at 空）：用 started_at 判定，超时同样收口放行。"""
        r = self._run("running", heartbeat_min_ago=None, started_min_ago=30)
        selector_device.assert_idle(self.db, self.dev.id)
        self.db.refresh(r)
        self.assertEqual(r.status, "failed")


if __name__ == "__main__":
    unittest.main()
