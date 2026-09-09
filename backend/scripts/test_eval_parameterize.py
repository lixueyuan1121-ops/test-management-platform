"""AI 参数化(把具体测评题挖成 {{变量}} 模板)prompt + 解析自测(纯函数,无需 DB/引擎)。
运行: cd backend && .venv/bin/python -m scripts.test_eval_parameterize

覆盖:
- build_eval_parameterize_prompt:喂 title/prompt/expected,prompt 含挖占位符指令、半角 {{}} 要求、
  要求 variables 给建议取值、保持原语义、输出 JSON 对象。
- parse_eval_parameterize:解析出 {title, prompt, expected, variables};容错 fence/裸对象;
  variables 值统一成 list[str];坏输入返回 None。
"""
from app.services import claude_runner as cr


def test_prompt_has_key_instructions():
    p = cr.build_eval_parameterize_prompt(
        title="视频压缩并对比前后体积",
        prompt="帮我把这个 1080p/60fps 的视频压到一半以下",
        expected="应调用视频压缩工具,给出压缩前后对比")
    for kw in ("{{", "占位符", "取值", "JSON"):
        assert kw in p, f"参数化 prompt 应含关键词 {kw}"
    # 原题三段都应喂进去
    assert "1080p/60fps" in p and "视频压缩并对比前后体积" in p, "原题文本应注入 prompt"
    print("OK 参数化 prompt 含挖空指令 + 原题注入")


def test_parse_basic():
    raw = '''```json
{"title":"视频压缩({{分辨率}})","prompt":"帮我把这个 {{分辨率}}/{{帧率}} 的视频压到 {{压缩比}} 以下",
 "expected":"应调用压缩工具输出 {{压缩比}} 的结果","variables":{"分辨率":["1080p","4K"],"帧率":["30fps","60fps"],"压缩比":["一半"]}}
```'''
    r = cr.parse_eval_parameterize(raw)
    assert r is not None, "应解析出对象"
    assert "{{分辨率}}" in r["prompt"] and "{{帧率}}" in r["prompt"], "prompt 含占位符"
    assert r["variables"]["分辨率"] == ["1080p", "4K"], r["variables"]
    assert r["variables"]["压缩比"] == ["一半"], "单取值也成 list"
    print("OK 解析基础模板 + variables")


def test_parse_coerces_scalar_values():
    # 模型偶尔把单取值写成标量而非数组 → 统一成 list[str]
    raw = '{"title":"t","prompt":"{{城市}}天气","expected":"e","variables":{"城市":"北京"}}'
    r = cr.parse_eval_parameterize(raw)
    assert r["variables"]["城市"] == ["北京"], f"标量取值应包成 list,实得 {r['variables']['城市']}"
    print("OK 标量取值容错成 list")


def test_parse_bad_returns_none():
    assert cr.parse_eval_parameterize("这不是 JSON") is None, "非 JSON 返回 None"
    assert cr.parse_eval_parameterize('{"prompt":""}') is None, "无占位符/空 prompt 返回 None"
    print("OK 坏输入返回 None")


def main():
    test_prompt_has_key_instructions()
    test_parse_basic()
    test_parse_coerces_scalar_values()
    test_parse_bad_returns_none()
    print("\n[PASS] AI 参数化 prompt + 解析 全部通过")


if __name__ == "__main__":
    main()
