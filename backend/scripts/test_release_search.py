"""上线内容搜索：完整内容、字面关键词、筛选分页和项目权限。"""
import unittest
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import release
from app.core.deps import get_current_user
from app.core.enums import ProjectRole
from app.db.session import Base, get_db
from app.models import Project, ProjectMember, ReleaseRecord, User


class ReleaseSearchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions() as db:
            db.add_all([Project(id=i, code=f'p{i}', name=f'项目{i}') for i in (1, 2)])
            db.add(User(id=1, username='reader', name='读者', password_hash='unused'))
            db.add(ProjectMember(project_id=1, user_id=1, role=ProjectRole.guest))
            contents = [
                ('v1', '桌面版', '常规说明' * 50 + '\n## 首页性能优化 SearchAPI', None),
                ('v2', '云端版', '首页性能优化：支持搜索', None),
                ('v3', '桌面版', '修复一般问题', '首页性能优化'),
                ('首页性能优化-v4', '桌面版', None, None),
                ('v5', '桌面版', r'完成度100% file_name C:\release', None),
                ('v6', '桌面版', '完成度1000 fileXname', None),
            ]
            db.add_all([ReleaseRecord(id=i, project_id=1, version=v, sub_product=p,
                                     content=c, memo=m, release_date=date(2026, 10, i))
                        for i, (v, p, c, m) in enumerate(contents, 1)])
            db.add(ReleaseRecord(id=7, project_id=2, version='other-project',
                                 content='首页性能优化', release_date=date(2026, 10, 19)))
            db.commit()
        app = FastAPI()
        app.include_router(release.router)
        def session():
            with self.sessions() as db:
                yield db
        def current():
            with self.sessions() as db:
                return db.get(User, 1)
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = current
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def search(self, **params):
        response = self.client.get('/api/releases', params={'project_id': 1, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data']

    def test_only_full_content_is_searched_and_ascii_case_is_ignored(self):
        result = self.search(keyword=' 首页性能优化 ')
        self.assertEqual(result['total'], 2)
        self.assertEqual([r['id'] for r in result['items']], [2, 1])
        self.assertEqual([r['id'] for r in self.search(keyword='searchapi')['items']], [1])

    def test_filters_and_total_apply_before_pagination(self):
        first = self.search(keyword='首页', limit=1, offset=0)
        second = self.search(keyword='首页', limit=1, offset=1)
        self.assertEqual((first['total'], second['total']), (2, 2))
        self.assertEqual([r['id'] for r in first['items'] + second['items']], [2, 1])
        selected = self.search(keyword='首页', sub_product='桌面版', limit=1)
        self.assertEqual(selected['total'], 1)
        self.assertEqual(selected['items'][0]['id'], 1)
        self.assertEqual(self.search(keyword='首页', offset=2)['items'], [])

    def test_literal_wildcards_and_empty_or_missing_keyword(self):
        for term in ('%', '_', '\\'):
            with self.subTest(term=term):
                self.assertEqual([r['id'] for r in self.search(keyword=term)['items']], [5])
        self.assertEqual(self.search(keyword="' OR 1=1 --")['total'], 0)
        self.assertEqual(self.search(keyword='不存在')['total'], 0)
        self.assertEqual(self.search()['total'], 6)
        self.assertEqual(self.search(keyword='   ')['total'], 6)

    def test_project_access_and_keyword_limit_are_enforced(self):
        self.assertEqual(self.client.get('/api/releases', params={'project_id': 2, 'keyword': '首页'}).status_code, 403)
        self.assertEqual(self.client.get('/api/releases', params={'project_id': 1, 'keyword': 'a' * 201}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
