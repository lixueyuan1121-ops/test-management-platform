"""存量修复脚本分类逻辑自测（纯函数，免 DB）。
运行: cd backend && .venv/bin/python -m scripts.test_fix_broken_selector_candidates
"""
from scripts.fix_broken_selector_candidates import classify_key


def main():
    good = [{"by": "css", "value": "h1"}]
    assert classify_key(good, []) == "ok"                    # DB 候选有效 → 跳过
    assert classify_key([{}], good) == "backfill"            # DB 坏 + 内置有 → 回填
    assert classify_key([], good) == "backfill"              # DB 空 + 内置有 → 回填
    assert classify_key([{}], []) == "manual"                # DB 坏 + 内置无 → 人工
    assert classify_key([{"by": "css"}], [{}]) == "manual"   # 两边都坏 → 人工
    print("OK test_fix_broken_selector_candidates")


if __name__ == "__main__":
    main()
