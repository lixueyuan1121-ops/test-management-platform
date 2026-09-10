"""使用内存数据库和模拟推送验证选中正常/异常结果,不访问 Multica。"""
import json
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.main import app
from app.db.session import Base, get_db
from app.core.deps import get_current_user
from app.models import EvalRun, Project, User
from app.services import multica


def main():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        user = User(id=1, username='test', name='test', password_hash='x', is_platform_admin=True)
        db.add_all([user, Project(id=1, name='P', code='P'), Project(id=2, name='Q', code='Q')])
        db.flush()
        for i in range(1, 6):
            db.add(EvalRun(id=i, project_id=2 if i == 4 else 1, status='done',
                           is_abnormal=i in (2, 5), pushed_multica=i == 3, answer='A',
                           payload=json.dumps({'prompt': f'P{i}', 'turn_index': i - 1})))
        db.commit()
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: user
        sent = []
        def send(run, query=None):
            sent.append(multica._payload(run))
            return f'ref-{run.id}'
        try:
            client = TestClient(app)
            with patch.object(multica, 'push_abnormal_run', side_effect=send):
                url = '/api/eval-export/multica'
                r = client.post(url, json={'project_id': 1, 'run_ids': [1, 2, 2, 3, 4]}).json()['data']
                assert r['pushed'] == 2 and [x['run_id'] for x in sent] == [1, 2]
                assert sent[0]['prompt'] == 'P1' and sent[0]['answer'] == 'A'
                assert client.post(url, json={'project_id': 1, 'run_ids': [1, 2]}).json()['data']['pushed'] == 0
                assert client.post(url, json={'project_id': 1, 'run_ids': []}).status_code == 422
                assert client.post(url, json={'project_id': 1}).json()['data']['pushed'] == 1
                assert not db.get(EvalRun, 4).pushed_multica
        finally:
            app.dependency_overrides.clear()
    print('PASS 正常/异常指定推送、去重、跨项目隔离、空选择拒绝及旧接口兼容')


if __name__ == '__main__':
    main()
