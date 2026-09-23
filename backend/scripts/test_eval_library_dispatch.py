"""Library dispatch validates product before creating any runs; options stay product-specific."""
import json
import unittest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.main import app
from app.models import EvalQuery, EvalRun, Project, User


class LibraryDispatchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(id=1, username='test', name='T', password_hash='x', is_platform_admin=True)
        self.db.add_all([self.user, Project(id=1, name='P', code='P'),
            EvalQuery(id=1, project_id=1, title='题', prompt='提问', dialog_options=json.dumps({'chatMode': '边想边做'}))])
        self.db.commit()
        self.overrides = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)
        self.db.close()
        self.engine.dispose()

    def enqueue(self, **kwargs):
        return self.client.post('/api/eval-queue/enqueue', json={'project_id': 1, 'runner': 'r1', 'eval_query_ids': [1], **kwargs})

    def test_each_product_keeps_selected_engine_and_explicit_empty_options(self):
        for product in ['namiwork', 'workbuddy', 'qwork']:
            response = self.enqueue(target_engine=product, dialog_options={})
            self.assertEqual(response.status_code, 200, response.text)
            row = self.db.get(EvalRun, response.json()['data']['run_ids'][0])
            self.assertEqual(row.target_engine, product)
            self.assertEqual(json.loads(row.payload)['dialog_options'], {})

    def test_invalid_product_and_non_nami_device_create_nothing(self):
        for payload in [dict(target_engine='unknown'), dict(target_engine='workbuddy', target_device='vm')]:
            self.assertEqual(self.enqueue(**payload).status_code, 400)
        self.assertEqual(self.db.query(EvalRun).count(), 0)

    def test_unsupported_options_are_rejected_and_legacy_default_is_preserved(self):
        self.assertEqual(self.enqueue(target_engine='workbuddy', dialog_options={'chatMode': '边想边做'}).status_code, 400)
        response = self.enqueue()
        self.assertEqual(response.status_code, 200, response.text)
        row = self.db.get(EvalRun, response.json()['data']['run_ids'][0])
        self.assertEqual(row.target_engine, 'namiwork')
        self.assertEqual(json.loads(row.payload)['dialog_options']['chatMode'], '边想边做')

if __name__ == '__main__':
    unittest.main()
