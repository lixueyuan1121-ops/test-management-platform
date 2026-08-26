"""TDD: platform 字段测试

覆盖：
1. selector_key 创建/查询携带 platform 字段（web / android / ios）
2. test_case 创建时 platform 默认 web，可写 android/ios
3. runner_device 注册时携带 platform，概览接口带出
4. 老数据兜底：未带 platform 时默认 web
"""
import sys
sys.path.insert(0, '.')

import json
import secrets
from app.db.session import Base, engine, SessionLocal
from app.db.migrate import ensure_platform_columns
from app.models import (
    User, Project, RunnerDevice, SelectorKey, TestCase, AiTask
)
from app.core.security import hash_password


def setup():
    """建表 + 补列（幂等），返回干净 db session。"""
    ensure_platform_columns()
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def teardown(db, objs):
    for o in reversed(objs):
        try:
            db.delete(db.merge(o))
        except Exception:
            pass
    db.commit()
    db.close()


def get_or_create_user(db):
    """取第一个用户；没有则建一个测试用的，测完不删（种子数据）。"""
    user = db.query(User).first()
    if not user:
        user = User(
            username='__test_user__', name='测试用户',
            password_hash=hash_password('test1234'), is_platform_admin=True,
        )
        db.add(user); db.commit(); db.refresh(user)
    return user


# ------------------------------------------------------------------
# Case 1: selector_key platform 默认 web
# ------------------------------------------------------------------
def test_selector_platform_default():
    db = setup()
    objs = []
    try:
        sk = SelectorKey(
            project_id=999999, sub_product='', key='__test_sk_default__',
            frame='auto', page='home', desc='test', candidates='[]',
        )
        db.add(sk); db.commit(); db.refresh(sk)
        objs.append(sk)
        assert sk.platform == 'web', f"expected 'web', got {sk.platform!r}"
        print("PASS  selector_key default platform=web")
    finally:
        teardown(db, objs)


# ------------------------------------------------------------------
# Case 2: selector_key 写入 android / ios
# ------------------------------------------------------------------
def test_selector_platform_android_ios():
    db = setup()
    objs = []
    try:
        for plat in ('android', 'ios'):
            sk = SelectorKey(
                project_id=999999, sub_product='', key=f'__test_sk_{plat}__',
                platform=plat, frame='auto', page='', desc='', candidates='[]',
            )
            db.add(sk); db.commit(); db.refresh(sk)
            objs.append(sk)
            assert sk.platform == plat, f"expected {plat!r}, got {sk.platform!r}"
            print(f"PASS  selector_key platform={plat}")
    finally:
        teardown(db, objs)


# ------------------------------------------------------------------
# Case 3: test_case platform 默认 web
# ------------------------------------------------------------------
def test_testcase_platform_default():
    db = setup()
    objs = []
    try:
        user = get_or_create_user(db)
        proj = Project(name='__test_proj_plat__', code='__TP__', description='')
        db.add(proj); db.commit(); db.refresh(proj)
        objs.append(proj)

        at = AiTask(project_id=proj.id, user_id=user.id, kind='testcase_gen',
                    status='done', provider='claude')
        db.add(at); db.commit(); db.refresh(at)
        objs.insert(0, at)

        tc = TestCase(
            ai_task_id=at.id, project_id=proj.id, title='__platform_test_case__',
            exec_kind='gui',
        )
        db.add(tc); db.commit(); db.refresh(tc)
        objs.insert(0, tc)

        assert tc.platform == 'web', f"expected 'web', got {tc.platform!r}"
        print("PASS  test_case default platform=web")
    finally:
        teardown(db, objs)


# ------------------------------------------------------------------
# Case 4: test_case 写入 android
# ------------------------------------------------------------------
def test_testcase_platform_android():
    db = setup()
    objs = []
    try:
        user = get_or_create_user(db)
        proj = Project(name='__test_proj_plat2__', code='__TP2__', description='')
        db.add(proj); db.commit(); db.refresh(proj)
        objs.append(proj)

        at = AiTask(project_id=proj.id, user_id=user.id, kind='testcase_gen',
                    status='done', provider='claude')
        db.add(at); db.commit(); db.refresh(at)
        objs.insert(0, at)

        tc = TestCase(
            ai_task_id=at.id, project_id=proj.id, title='__android_tc__',
            exec_kind='gui', platform='android',
        )
        db.add(tc); db.commit(); db.refresh(tc)
        objs.insert(0, tc)

        assert tc.platform == 'android', f"expected 'android', got {tc.platform!r}"
        print("PASS  test_case platform=android")
    finally:
        teardown(db, objs)


# ------------------------------------------------------------------
# Case 5: runner_device platform 默认 web
# ------------------------------------------------------------------
def test_runner_device_platform_default():
    db = setup()
    objs = []
    try:
        user = get_or_create_user(db)
        rd = RunnerDevice(
            owner_id=user.id, runner_id='__test_rd_web__',
            name='test-web-runner', token=secrets.token_hex(32),
        )
        db.add(rd); db.commit(); db.refresh(rd)
        objs.append(rd)
        assert rd.platform == 'web', f"expected 'web', got {rd.platform!r}"
        print("PASS  runner_device default platform=web")
    finally:
        teardown(db, objs)


# ------------------------------------------------------------------
# Case 6: runner_device 写入 ios
# ------------------------------------------------------------------
def test_runner_device_platform_ios():
    db = setup()
    objs = []
    try:
        user = get_or_create_user(db)
        rd = RunnerDevice(
            owner_id=user.id, runner_id='__test_rd_ios__',
            name='test-ios-runner', platform='ios', token=secrets.token_hex(32),
        )
        db.add(rd); db.commit(); db.refresh(rd)
        objs.append(rd)
        assert rd.platform == 'ios', f"expected 'ios', got {rd.platform!r}"
        print("PASS  runner_device platform=ios")
    finally:
        teardown(db, objs)


# ------------------------------------------------------------------
# Case 7: ensure_platform_columns 幂等（再跑一次不报错）
# ------------------------------------------------------------------
def test_migrate_idempotent():
    ensure_platform_columns()
    ensure_platform_columns()
    print("PASS  ensure_platform_columns idempotent")


if __name__ == '__main__':
    tests = [
        test_selector_platform_default,
        test_selector_platform_android_ios,
        test_testcase_platform_default,
        test_testcase_platform_android,
        test_runner_device_platform_default,
        test_runner_device_platform_ios,
        test_migrate_idempotent,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL  {t.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{'='*40}")
    print(f"Total {passed+failed}  Passed {passed}  Failed {failed}")
    sys.exit(0 if failed == 0 else 1)

