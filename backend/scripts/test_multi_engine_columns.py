"""验证多产品两列(eval_task.target_engines / runner_device.eval_engine)能建 + 幂等。"""
from app.db.migrate import ensure_eval_task_target_engines, ensure_runner_device_eval_engine, _columns

# 幂等：连跑两次不报错
for _ in range(2):
    ensure_eval_task_target_engines()
    ensure_runner_device_eval_engine()

assert "target_engines" in _columns("eval_task"), "eval_task.target_engines 未建"
assert "eval_engine" in _columns("runner_device"), "runner_device.eval_engine 未建"
print("PASS: 两列已建 + 幂等")
