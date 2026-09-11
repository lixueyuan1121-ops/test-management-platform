"""自愈回写选择器服务自测:未注册→新建、已注册→补候选、幂等去重。
运行: cd backend && python -m scripts.test_heal_selectors
"""
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.session import Base
from app.models import SelectorKey
from app.services.heal_selectors import apply_heal_items

def _cands(s, key):
    r = s.query(SelectorKey).filter_by(project_id=2, sub_product="", key=key).first()
    return json.loads(r.candidates) if r else None

def main():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=False)()   # 与生产 SessionLocal 一致(autoflush=False),否则 C1 被自动 flush 掩盖
    s.add(SelectorKey(project_id=2, sub_product="", key="oldKey", page="任务",
                      candidates=json.dumps([{"by": "css", "value": ".old"}], ensure_ascii=False)))
    s.commit()

    r1 = apply_heal_items(s, 2, [{"key": "newKey", "selector": ".new-btn", "page": "任务搜索"}])
    assert r1["created"] == 1, r1
    row = s.query(SelectorKey).filter_by(project_id=2, key="newKey").first()
    assert row and json.loads(row.candidates) == [{"by": "css", "value": ".new-btn"}], row.candidates
    assert row.page == "任务搜索" and "[自愈]" in (row.desc or ""), (row.page, row.desc)

    r2 = apply_heal_items(s, 2, [{"key": "oldKey", "selector": ".fresh"}])
    assert r2["patched"] == 1, r2
    c = _cands(s, "oldKey")
    assert {"by": "css", "value": ".fresh"} in c and {"by": "css", "value": ".old"} in c, c

    r3 = apply_heal_items(s, 2, [{"key": "oldKey", "selector": ".fresh"}])
    assert r3["skipped"] == 1 and r3["patched"] == 0, r3

    r4 = apply_heal_items(s, 2, [{"key": "", "selector": ".x"}, {"key": "k", "selector": ""}])
    assert r4["created"] == 0 and r4["patched"] == 0, r4

    # ⑤ C1 回归:同一未注册 key 在同批出现 2 次(2 步都自愈到 selector)→ 不撞唯一约束,新建1次+补候选/幂等
    r5 = apply_heal_items(s, 2, [
        {"key": "dupKey", "selector": ".a", "page": "X"},
        {"key": "dupKey", "selector": ".b", "page": "X"},
    ])
    assert r5["created"] == 1, r5   # 只新建1次
    row5 = s.query(SelectorKey).filter_by(project_id=2, key="dupKey").first()
    assert row5 is not None
    vals = {c["value"] for c in json.loads(row5.candidates)}
    assert vals == {".a", ".b"}, vals   # 两个 selector 都进候选(第2次走补候选)

    # ⑥ C1 同 key 同 selector 批内重复 → 新建1次,第2条幂等skip
    r6 = apply_heal_items(s, 2, [
        {"key": "dupKey2", "selector": ".same"},
        {"key": "dupKey2", "selector": ".same"},
    ])
    assert r6["created"] == 1 and r6["skipped"] == 1, r6

    # 四段式 desc:item 带 desc → 写入 selector_key.desc(而非死串)
    r7 = apply_heal_items(s, 2, [{"key": "descKey", "selector": ".d", "page": "任务搜索",
                                  "desc": "[任务]-[任务搜索]-[清空搜索输入框]-[弹窗标题·回到最近任务默认态]"}])
    assert r7["created"] == 1, r7
    row7 = s.query(SelectorKey).filter_by(project_id=2, key="descKey").first()
    assert row7.desc == "[任务]-[任务搜索]-[清空搜索输入框]-[弹窗标题·回到最近任务默认态]", row7.desc
    # 不带 desc → 回退旧串(兼容)
    r8 = apply_heal_items(s, 2, [{"key": "noDescKey", "selector": ".n"}])
    row8 = s.query(SelectorKey).filter_by(project_id=2, key="noDescKey").first()
    assert "[自愈]" in (row8.desc or ""), row8.desc
    # 已注册 key 补候选时:若原 desc 是旧自愈串,可更新为新四段式;人工 desc 不覆盖
    # (先建一个人工 desc 的 key,再回写带 desc,断言人工 desc 不被覆盖)
    from datetime import datetime
    import json as _json
    s.add(SelectorKey(project_id=2, sub_product="", key="humanKey", page="X", desc="人工写的说明",
                      candidates=_json.dumps([{"by":"css","value":".old-h"}], ensure_ascii=False))); s.commit()
    apply_heal_items(s, 2, [{"key": "humanKey", "selector": ".new-h", "desc": "[A]-[B]-[C]-[D]"}])
    rowh = s.query(SelectorKey).filter_by(project_id=2, key="humanKey").first()
    assert rowh.desc == "人工写的说明", f"人工 desc 不应被自愈覆盖,实际 {rowh.desc}"

    print("OK test_heal_selectors")

if __name__ == "__main__":
    main()
