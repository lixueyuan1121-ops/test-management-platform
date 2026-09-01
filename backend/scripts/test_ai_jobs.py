"""AI 任务队列(ai_job)+ worker 池 + 归因入队 自测。
运行: cd backend && python -m scripts.test_ai_jobs

覆盖(分任务递增):
- Task1 模型:AiJob 落库/查回,默认 status=pending
- Task2 队列核心:enqueue 建 pending(input 可 json 回)、claim_next 原子抢占(单条只一胜)、
  queue_position 排队位次
- Task3 handler:归因 handler 经 run_job 跑通(mock 引擎)→ 域表 triage 落库;坏输出→failed 不覆盖
- Task4 worker 池:reap 启动收口 running→failed;start_pool 起线程消费一条到 done
- Task5 端点:GET /api/ai-jobs/{id} 状态/位次/鉴权;cancel 仅 pending
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import AiJob

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


# ─── Task1: 模型 ────────────────────────────────────────────────────────────────

def test_model_defaults():
    j = AiJob(kind="triage", input=json.dumps({"run_id": 501}))
    _s.add(j); _s.commit(); _s.refresh(j)
    assert j.id and j.status == "pending", (j.id, j.status)
    got = _s.get(AiJob, j.id)
    assert json.loads(got.input)["run_id"] == 501
    assert got.created_at is not None
    print("OK model defaults")
    _s.delete(got); _s.commit()


def main():
    test_model_defaults()
    print("OK test_ai_jobs")


if __name__ == "__main__":
    main()
