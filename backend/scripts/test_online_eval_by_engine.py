"""验证 online_eval_runners 按被测引擎(eval_engine)过滤在线机。构造后 rollback,不落库。"""
from datetime import datetime
from app.db.session import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.runner_device import RunnerDevice
from app.models.user import User
from app.services.dispatcher import online_eval_runners

engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
db = sessionmaker(bind=engine)()
db.add(User(id=1, username="test", name="Test", password_hash="x"))
db.flush()
uid = 1
now = datetime.utcnow()
d1 = RunnerDevice(owner_id=uid, runner_id="test-nami-01", name="nami机", token="tk-nami-test",
                  eval_engine="namiwork", last_eval_at=now, last_seen_at=now)
d2 = RunnerDevice(owner_id=uid, runner_id="test-wb-01", name="wb机", token="tk-wb-test",
                  eval_engine="workbuddy", last_eval_at=now, last_seen_at=now)
db.add_all([d1, d2]); db.flush()
try:
    wb = online_eval_runners(db, engine="workbuddy")
    nami = online_eval_runners(db, engine="namiwork")
    alle = online_eval_runners(db)
    assert "test-wb-01" in wb and "test-nami-01" not in wb, f"workbuddy 过滤错: {wb}"
    assert set(nami) == {"test-nami-01", "test-wb-01"}, f"启用 WorkBuddy 仍应支持纳米Work: {nami}"
    assert "test-wb-01" in alle and "test-nami-01" in alle, f"无参应全返回: {alle}"
    print("PASS: online_eval_runners 按 engine 过滤")
finally:
    db.rollback(); db.close(); engine.dispose()
