"""Selector lifecycle metadata must not affect descriptions or runtime locators."""
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.main import app
from app.db.session import Base, get_db
from app.core.deps import get_current_user
from app.models import Project, User, SelectorKey
from app.services.selectors import resolved_registry
from app.db import migrate

class SelectorStatusTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(id=1, username='test', name='Test', password_hash='unused', is_platform_admin=True)
        self.db.add_all([self.user, Project(id=1, name='P', code='p')]); self.db.commit()
        def database():
            yield self.db
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
    def tearDown(self):
        app.dependency_overrides.clear(); self.client.close(); self.db.close(); self.engine.dispose()
    def create(self):
        r = self.client.post('/api/selectors', json={'project_id': 1, 'key': 'save', 'desc': '[技能]-[编辑]-[保存]-[按钮]', 'candidates': [{'by': 'testid', 'value': 'save'}]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['data']
    def test_status_only_update_preserves_description_and_runtime(self):
        row = self.create(); self.assertEqual(row['change_status'], 'new')
        before = resolved_registry(self.db, 1, '')
        r = self.client.patch('/api/selectors/'+str(row['id']), json={'expected_revision': row['revision'], 'change_status': 'retired'})
        self.assertEqual(r.status_code, 200, r.text)
        after = r.json()['data']; self.assertEqual(after['desc'], row['desc'])
        self.assertEqual(after['candidates'], row['candidates'])
        self.assertEqual(after['change_status'], 'retired')
        self.assertEqual(resolved_registry(self.db, 1, ''), before)
        stale = self.client.patch('/api/selectors/'+str(row['id']), json={'expected_revision': row['revision'], 'desc': 'stale'})
        self.assertEqual(stale.status_code, 409)
        history = self.client.get('/api/selectors/'+str(row['id'])+'/history').json()['data']['history']
        self.assertEqual(history[0]['snapshot']['change_status'], 'new')
        restored = self.client.post('/api/selectors/'+str(row['id'])+'/restore', json={'expected_revision': after['revision'], 'history_id': history[0]['id']})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['data']['change_status'], 'new')
    def test_normal_edit_defaults_updated_and_invalid_status_rejected(self):
        row = self.create()
        r = self.client.patch('/api/selectors/'+str(row['id']), json={'expected_revision': row['revision'], 'desc': '新说明'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['data']['change_status'], 'updated')
        self.assertEqual(r.json()['data']['desc'], '[未分类]-[未分类]-[页面操作]-[新说明]')
        r = self.client.patch('/api/selectors/'+str(row['id']), json={'expected_revision': r.json()['data']['revision'], 'change_status': '未覆盖'})
        self.assertEqual(r.status_code, 422)
    def test_import_create_and_overwrite(self):
        from app.api.selectors import _apply_import
        reg = {'save': {'desc': '保存', 'candidates': [{'by': 'testid', 'value': 'save'}]}}
        _apply_import(self.db, self.user, 1, '', reg, '', False)
        row = self.db.query(SelectorKey).one(); self.assertEqual(row.change_status, 'new')
        reg['save']['desc'] = '保存按钮'
        _apply_import(self.db, self.user, 1, '', reg, '', True)
        self.db.refresh(row); self.assertEqual(row.change_status, 'updated'); self.assertEqual(row.desc, '[未分类]-[未分类]-[页面操作]-[保存按钮]')
    def test_legacy_migration_is_idempotent_and_does_not_infer_status(self):
        legacy = create_engine('sqlite://')
        try:
            with legacy.begin() as c:
                c.execute(text('CREATE TABLE selector_key (id INTEGER PRIMARY KEY, desc TEXT)'))
                c.execute(text("INSERT INTO selector_key VALUES (1, 'Original')"))
            with patch.object(migrate, 'engine', legacy):
                migrate.ensure_selector_change_status_column(); migrate.ensure_selector_change_status_column()
            with legacy.connect() as c:
                self.assertEqual(tuple(c.execute(text('SELECT desc, change_status FROM selector_key')).one()), ('Original', ''))
        finally: legacy.dispose()

if __name__ == '__main__': unittest.main()
