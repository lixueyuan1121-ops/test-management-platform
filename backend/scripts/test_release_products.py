"""Release product persistence, access control and record integration."""
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api import release
from app.core.deps import get_current_user
from app.core.enums import ProjectRole
from app.db.session import Base, get_db
from app.models import Project, ProjectMember, ReleaseProduct, User


class ReleaseProductTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as db:
            db.add_all([Project(id=1, code='pc', name='PC', platform_type='pc'), Project(id=2, code='app', name='APP', platform_type='app')])
            for uid in range(1, 5):
                db.add(User(id=uid, username=f'u{uid}', name=f'User {uid}', password_hash='test', is_platform_admin=uid == 1))
            db.add_all([ProjectMember(project_id=1, user_id=2, role=ProjectRole.admin), ProjectMember(project_id=1, user_id=3, role=ProjectRole.member)])
            db.commit()
        self.uid = 1
        def session():
            with self.factory() as db: yield db
        def current():
            with self.factory() as db: return db.get(User, self.uid)
        app = FastAPI()
        app.include_router(release.router)
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = current
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def add(self, name='新产品', project=1):
        return self.client.post('/api/releases/products', json={'project_id': project, 'name': name})

    def products(self, project=1):
        return self.client.get('/api/releases/products', params={'project_id': project})

    def test_defaults_and_custom_products_persist_per_project(self):
        self.assertEqual(self.products().json()['data'], list(release.SUB_PRODUCTS_BY_TYPE['pc']))
        self.assertEqual(self.products(2).json()['data'], list(release.SUB_PRODUCTS_BY_TYPE['app']))
        result = self.add('  新产品  ')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['data']['name'], '新产品')
        self.assertEqual(self.products().json()['data'][-1], '新产品')
        self.assertNotIn('新产品', self.products(2).json()['data'])
        with self.factory() as db:
            saved = db.query(ReleaseProduct).one()
            self.assertEqual((saved.project_id, saved.name, saved.created_by), (1, '新产品', 1))

    def test_only_platform_admin_can_create_even_when_project_admin(self):
        for uid in (2, 3, 4):
            self.uid = uid
            self.assertEqual(self.add().status_code, 403)
        self.assertEqual(self.products().status_code, 403)
        self.uid = 3
        self.assertEqual(self.products().status_code, 200)
        self.assertEqual(self.products(2).status_code, 403)

    def test_invalid_duplicate_and_missing_project(self):
        for name in ('', '  ', '全部', 'x' * 33, 'bad\nname'):
            self.assertEqual(self.add(name).status_code, 422)
        self.assertEqual(self.add(release.SUB_PRODUCTS_BY_TYPE['pc'][0]).status_code, 409)
        self.assertEqual(self.add().status_code, 200)
        self.assertEqual(self.add(' 新产品 ').status_code, 409)
        self.assertEqual(self.add(project=2).status_code, 200)
        self.assertEqual(self.add(project=999).status_code, 404)
        # Database constraint also rejects a second insert, not only API pre-checks.
        with self.factory() as db:
            db.add(ReleaseProduct(project_id=1, name='新产品'))
            with self.assertRaises(IntegrityError): db.commit()

    def test_custom_product_can_create_edit_and_filter_release(self):
        self.add()
        body = {'project_id': 1, 'version': '1.0', 'release_date': '2026-10-10', 'sub_product': '新产品'}
        response = self.client.post('/api/releases', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        rid = response.json()['data']['id']
        selected = self.client.get('/api/releases', params={'project_id': 1, 'sub_product': '新产品'}).json()['data']
        self.assertEqual(selected['total'], 1)
        self.assertEqual(self.client.patch(f'/api/releases/{rid}', json={'sub_product': '新产品'}).status_code, 200)
        self.assertEqual(self.client.post('/api/releases', json={**body, 'project_id': 2}).status_code, 400)
        self.assertEqual(self.client.patch(f'/api/releases/{rid}', json={'sub_product': '未登记'}).status_code, 400)

    def test_existing_database_gets_new_table_idempotently(self):
        ReleaseProduct.__table__.drop(self.engine)
        Base.metadata.create_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.assertEqual(self.add().status_code, 200)
        with self.factory() as db: self.assertEqual(db.query(Project).count(), 2)


if __name__ == '__main__': unittest.main()
