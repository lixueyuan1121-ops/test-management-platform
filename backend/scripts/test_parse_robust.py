"""parse_testcases 稳健解析自测:保护"生成半天却抠不出用例"。
运行: cd backend && python -m scripts.test_parse_robust

覆盖:①fence 含嵌套数组(旧非贪婪正则会截断)②输出被截断 ③中间有坏对象 ④正常 ⑤纯垃圾。
"""
from app.services.claude_runner import parse_testcases, _salvage_objects, _extract_cases_array


def _titles(cases):
    return [c["title"] for c in cases]


def main():
    # ① fenced 数组内含嵌套 script/asserts 数组:旧 `\[.*?\]` 非贪婪会在内层 ] 截断 → 全丢。
    raw1 = ('```json\n[{"title":"A","kind":"manual","steps":"s"},'
            '{"title":"B","kind":"gui","kind_reason":"界面",'
            '"script":[{"action":"connect"},{"action":"assert_visible","target":{"selector":".x"}}]}]\n```')
    t1 = _titles(parse_testcases(raw1, project_id=None))
    assert "A" in t1 and "B" in t1, f"嵌套数组应完整解析,实际 {t1}"

    # ② 输出被截断(末尾半个对象、无结尾 ])→ salvage 出完整的,丢半截。
    raw2 = '一些前言\n[{"title":"完整1","kind":"manual"},{"title":"完整2","kind":"manual"},{"title":"半截对象'
    t2 = _titles(parse_testcases(raw2, project_id=None))
    assert "完整1" in t2 and "完整2" in t2, t2
    assert not any("半截" in t for t in t2), f"半截对象不应出现: {t2}"

    # ③ 中间有个坏对象(整体 json.loads 失败)→ salvage 出好的两条。
    raw3 = '[{"title":"好1","kind":"manual"}, {坏对象无引号}, {"title":"好2","kind":"manual"}]'
    t3 = _titles(parse_testcases(raw3, project_id=None))
    assert "好1" in t3 and "好2" in t3, t3

    # ④ 正常干净数组照常。
    assert _titles(parse_testcases('[{"title":"N","kind":"manual"}]', project_id=None)) == ["N"]

    # ⑤ 纯垃圾/空 → []
    assert parse_testcases("完全没有用例的一段话，只有文字。", project_id=None) == []
    assert parse_testcases("", project_id=None) == []

    # salvage 单元:跳过字符串内的花括号(不误判配平)
    sv = _salvage_objects('{"title":"含}花括号","note":"a{b}c"} 垃圾 {"title":"第二条"}')
    assert [o["title"] for o in sv] == ["含}花括号", "第二条"], sv

    # _extract_cases_array:正常走整体解析(非 salvage 路径也 OK)
    assert len(_extract_cases_array('[{"title":"x"},{"title":"y"}]')) == 2

    print("OK test_parse_robust")


if __name__ == "__main__":
    main()
