from app.services.eval_engines import EVAL_ENGINES, is_valid_engine, normalize_engines, runner_supported_engines

assert is_valid_engine("workbuddy") and is_valid_engine("namiwork")
assert not is_valid_engine("gpt")
assert runner_supported_engines(None) == ("namiwork",)
assert runner_supported_engines("namiwork") == ("namiwork",)
assert runner_supported_engines("workbuddy") == ("namiwork", "workbuddy")
assert runner_supported_engines("gpt") == ()
assert normalize_engines(None) == ["namiwork"]
assert normalize_engines([]) == ["namiwork"]
assert normalize_engines(["workbuddy", "namiwork", "workbuddy"]) == ["workbuddy", "namiwork"]  # 去重保序
assert normalize_engines(["gpt", "workbuddy"]) == ["workbuddy"]                                 # 剔非法
assert normalize_engines(["gpt"]) == ["namiwork"]                                               # 全非法→默认
print("PASS: eval_engines")
