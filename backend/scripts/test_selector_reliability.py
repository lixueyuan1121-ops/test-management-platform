"""两轮选择器 review 的故障回归。只使用隔离内存数据库。"""
import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app
from app.db.session import Base, get_db
from app.core.deps import get_current_user, require_runner_ctx
from app.models import Project, User, RunnerDevice, Task, SelectorKey, SelectorScope, RecordSession, TestCase
from app.services.recording import save_recording_as_case
from app.services.selectors import resolved_registry
from app.services.claude_runner import _validate_script


def event(action="click", value="save", frame="shell", id="1"):
    return {"action": action, "event_id": id, "ts": int(id), "frame": frame,
            "tag": "button", "candidates": [{"by": "testid", "value": value}], "assert": {"kind": "visible"}}


class SelectorReliability(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        self.user = User(id=1, username="owner", name="Owner", password_hash="unused", is_platform_admin=True)
        self.db.add_all([self.user, Project(id=1, name="P", code="p"),
                        Task(id=1, project_id=1, assigned_by=1, assigned_to=1, title="T", assigned_date=date.today())])
        self.db.add_all([RunnerDevice(id=i, owner_id=i, runner_id="same-name", name="Device", token=f"test-token-{i}") for i in (1, 2)])
        self.db.commit()
        self.device = self.db.get(RunnerDevice, 1)
        def database():
            try:
                yield self.db
            except Exception:
                self.db.rollback()
                raise
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[require_runner_ctx] = lambda: SimpleNamespace(device=self.device)
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()

    def start(self):
        r = self.client.post("/api/record", json={"project_id": 1, "runner": "same-name", "runner_device_id": 1})
        self.assertEqual(r.status_code, 200, r.text)
        sid = r.json()["data"]["id"]
        r = self.client.get("/api/record/pending?consumer_id=process-1")
        self.assertEqual(r.status_code, 200, r.text)
        return sid

    def upload(self, sid, events, final=False):
        return self.client.post(f"/api/record/{sid}/events", json={"events": events, "consumer_id": "process-1", "final": final})

    def test_isolation_and_exclusive_recording(self):
        sid = self.start()
        self.assertEqual(self.client.post("/api/record", json={"project_id": 1, "runner": "same-name"}).status_code, 409)
        self.device = self.db.get(RunnerDevice, 2)
        self.assertEqual(self.client.get("/api/record/pending?consumer_id=process-2").json()["data"], [])
        self.assertEqual(self.upload(sid, [event()]).status_code, 403)
        self.assertEqual(self.client.get("/api/probe/pending").json()["data"], [])

    def test_shared_token_cannot_bypass_registered_device_isolation(self):
        from app.models import ExecRun
        from app.api.exec_queue import _device_run_filter
        self.db.add_all([ExecRun(project_id=1, runner=name, kind='gui', status='pending', payload='{}', enqueued_by=1)
                         for name in ('same-name', 'unregistered-legacy')])
        self.db.commit()
        rows = self.db.query(ExecRun).filter(_device_run_filter(self.db, SimpleNamespace(device=None))).all()
        self.assertEqual([r.runner for r in rows], ['unregistered-legacy'])

    def test_upload_retry_stop_confirmation_and_save_idempotency(self):
        sid = self.start()
        events = [event(), event("assert", id="2")]
        self.assertEqual(self.upload(sid, events).json()["data"]["appended"], 2)
        self.assertEqual(self.upload(sid, events).json()["data"]["appended"], 0)
        self.assertEqual(self.client.post(f"/api/record/{sid}/stop").json()["data"]["status"], "stopping")
        body = {"title": "Recorded", "task_id": 1}
        url = f"/api/record/{sid}/save-as-case"
        self.assertEqual(self.client.post(url, json=body).status_code, 409)
        self.assertEqual(self.upload(sid, events, final=True).json()["data"]["status"], "stopped")
        first = self.client.post(url, json=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.client.post(url, json=body).json()["data"]["id"], first.json()["data"]["id"])
        self.assertEqual(self.db.query(TestCase).count(), 1)
        self.assertEqual(self.upload(sid, [event(id="3")]).status_code, 409)

    def test_name_collision_and_same_recording_reuse(self):
        self.db.add(SelectorKey(project_id=1, key="save", frame="shell", candidates='[{"by":"css","value":".old-only"}]'))
        self.db.commit()
        tc = save_recording_as_case(self.db, 1, "", [event(), event("assert", id="2")], "T", 1, 1)
        self.db.commit()
        keys = [s["target"].get("key") for s in json.loads(tc.script)[1:]]
        self.assertEqual(keys, ["save2", "save2"])
        self.assertEqual(self.db.query(SelectorKey).filter_by(key="save").one().candidates, '[{"by":"css","value":".old-only"}]')

    def test_frame_and_candidate_identity_survive(self):
        ev = event("assert", frame="vm")
        ev["candidates"] = [{"by": "role", "value": "button", "name": "保存", "exact": True}]
        tc = save_recording_as_case(self.db, 1, "", [ev], "T", 1, 1)
        row = self.db.query(SelectorKey).one()
        self.assertEqual(row.frame, "vm")
        self.assertEqual(json.loads(row.candidates), ev["candidates"])

    def test_failure_rolls_back_selectors_together_with_case(self):
        with patch("app.services.recording._record_ai_task", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                save_recording_as_case(self.db, 1, "", [event("assert")], "T", 1, 1)
        self.db.rollback()
        with self.Session() as check:
            self.assertEqual(check.query(SelectorKey).count(), 0)
            self.assertEqual(check.query(TestCase).count(), 0)

    def test_no_assertion_cannot_create_executable_case(self):
        with self.assertRaisesRegex(ValueError, "断言"):
            save_recording_as_case(self.db, 1, "", [event()], "T", 1, 1)
        self.assertEqual(self.db.query(SelectorKey).count(), 0)

    def test_invalid_event_timestamp_and_incomplete_recording_rejected(self):
        sid = self.start()
        self.assertEqual(self.upload(sid, [{**event(), 'ts': 'tomorrow'}]).status_code, 422)
        for bad in ({'action': 'click', 'candidates': []}, {**event('assert'), 'assert': {'kind': 'text', 'expected': ''}}):
            with self.assertRaises(ValueError):
                save_recording_as_case(self.db, 1, '', [bad, event('assert', id='2')], 'T', 1, 1)
        self.assertEqual(self.db.query(SelectorKey).count(), 0)

    def test_probe_late_result_cannot_resurrect_expired_request(self):
        from app.models import ProbeRequest
        response = self.client.post('/api/probe', json={'project_id':1, 'runner':'same-name'})
        probe_id = response.json()['data']['id']
        url = f'/api/probe/{probe_id}'
        self.assertEqual(self.client.patch(url, json={'result':{}}).status_code, 409)
        self.client.get('/api/probe/pending')
        self.assertEqual(self.client.patch(url, json={'result':{'ok':True}}).status_code, 200)
        self.assertEqual(self.client.patch(url, json={'result':{'ok':True}}).status_code, 200)
        self.db.get(ProbeRequest, probe_id).status = 'failed'
        self.db.commit()
        self.assertEqual(self.client.patch(url, json={'result':{'ok':True}}).status_code, 409)

    def test_shared_key_reused_without_shadowing_and_scoped_delete_only_affects_users(self):
        sub = '纳米Work桌面版'
        shared = SelectorKey(project_id=1, key='save', frame='shell', candidates='[{"by":"testid","value":"save"}]')
        self.db.add(shared); self.db.commit()
        shared_case = save_recording_as_case(self.db, 1, '', [event('assert')], 'Shared', 1, 1)
        scoped_case = save_recording_as_case(self.db, 1, sub, [event('assert')], 'Scoped', 1, 1)
        self.db.commit()
        self.assertEqual(self.db.query(SelectorKey).count(), 1)
        override = SelectorKey(project_id=1, sub_product=sub, key='save', frame='vm', candidates='[{"by":"testid","value":"other"}]')
        self.db.add(override); self.db.commit()
        usage = self.client.get(f'/api/selectors/{shared.id}/usage').json()['data']
        self.assertEqual([c['id'] for c in usage['cases']], [shared_case.id])
        self.assertEqual(self.client.delete(f'/api/selectors/{shared.id}').json()['data']['downgraded'], 1)
        self.db.refresh(scoped_case)
        self.assertEqual(scoped_case.exec_kind, 'e2e')

    def test_registry_read_failure_is_not_an_empty_registry(self):
        from app.services.claude_runner import _registered_keys, _load_selector_keys
        with patch('app.db.session.SessionLocal', self.Session), patch('app.services.selectors.usable_key_set', side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError, '注册表读取失败'):
                _registered_keys(1)
        with patch('app.db.session.SessionLocal', self.Session), patch('app.services.selectors.shared_key_dicts', side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError, '注册表读取失败'):
                _load_selector_keys(1)

    def test_empty_registry_does_not_validate_unknown_key(self):
        _, error = _validate_script([{"action": "assert_visible", "target": {"key": "unknown"}}], set())
        self.assertIsNotNone(error)

    def test_concurrent_edit_conflict_and_history_restore(self):
        created = self.client.post('/api/selectors', json={"project_id": 1, "key": "save", "candidates": [{"by": "css", "value": "#old"}]}).json()['data']
        url = f'/api/selectors/{created["id"]}'
        body = {"expected_revision": created['revision'], "candidates": [{"by": "css", "value": "#new"}]}
        edited = self.client.patch(url, json=body)
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(self.client.patch(url, json={**body, 'desc': 'stale edit'}).status_code, 409)
        history = self.client.get(url + '/history').json()['data']
        self.assertEqual(len(history['history']), 1)
        restored = self.client.post(url + '/restore', json={"expected_revision": history['current']['revision'], "history_id": history['history'][0]['id']})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['data']['candidates'], created['candidates'])

    def test_subproduct_generated_validation_backfill_and_execution_snapshot(self):
        from app.services.claude_runner import revalidate_for_backfill, _load_selector_keys
        from app.api.exec_queue import _payload_of
        sub = '纳米Work桌面版'
        ev = event('assert', frame='vm')
        tc = save_recording_as_case(self.db, 1, sub, [ev], 'T', 1, 1)
        self.db.commit()
        self.assertEqual(tc.sub_product, sub)
        script = json.loads(tc.script)
        self.assertIsNone(revalidate_for_backfill(script, 1, sub, self.db)[1])
        self.assertIsNotNone(revalidate_for_backfill(script, 1, '', self.db)[1])
        with patch('app.db.session.SessionLocal', self.Session):
            self.assertEqual(_load_selector_keys(1, sub_product=sub)[0]['key'], 'save')
        payload = _payload_of(tc, self.db)
        self.assertEqual(payload['sub_product'], sub)
        self.assertIn('save', payload['selector_registry']['registry'])
        before = payload['selector_registry']['version']
        self.db.query(SelectorKey).one().candidates = '[{"by":"css","value":"#changed"}]'
        self.db.commit()
        self.assertEqual(payload['selector_registry']['version'], before)
        self.assertNotEqual(_payload_of(tc, self.db)['selector_registry']['version'], before)

    def test_reuse_registered_key_changes_references_without_duplicating_registry(self):
        from app.services.selector_history import revision
        tc = save_recording_as_case(self.db, 1, '', [event('assert')], 'T', 1, 1)
        tc.exec_kind = 'manual'
        tc.kind_reason = '[选择器待补] 补齐选择器 key:missingSave 后即可执行 e2e'
        tc.script = json.dumps([{"action": "fill", "target": {"key": "missingSave"}, "args": {"text": "missingSave"}},
                                {"action": "assert_visible", "target": {"key": "missingSave"}}])
        self.db.commit()
        row = self.db.query(SelectorKey).one()
        response = self.client.post('/api/ai/testcases/remap-selector', json={
            "project_id": 1, "case_ids": [tc.id], "from_key": "missingSave", "to_key": "save", "expected_revision": revision(row)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['restored'], 1)
        self.db.refresh(tc)
        script = json.loads(tc.script)
        self.assertEqual(script[0]['target']['key'], 'save')
        self.assertEqual(script[0]['args']['text'], 'missingSave')
        self.assertEqual(self.db.query(SelectorKey).count(), 1)

    def test_migration_preserves_old_rows_and_is_repeatable(self):
        from sqlalchemy import text, inspect
        from app.db import migrate
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as conn:
            for table in ('record_session', 'probe_request', 'test_case'):
                conn.execute(text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)'))
                conn.execute(text(f'INSERT INTO {table} (id) VALUES (1)'))
        with patch.object(migrate, 'engine', engine):
            migrate.ensure_selector_reliability_columns()
            migrate.ensure_selector_reliability_columns()
        self.assertIn('consumer_id', {c['name'] for c in inspect(engine).get_columns('record_session')})
        self.assertIn('ix_record_session_runner_device_id', {i['name'] for i in inspect(engine).get_indexes('record_session')})
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT id, sub_product FROM test_case')).one(), (1, ''))
        engine.dispose()

    def test_learned_stays_pending_until_approved_and_role_names_do_not_collapse(self):
        self.db.add(SelectorKey(project_id=1, key="save", candidates='[{"by":"css","value":"#old"}]'))
        self.db.commit()
        for name in ("保存", "取消"):
            r = self.client.post("/api/selectors/learned", json={"project_id": 1, "items": [{"key": "save", "candidates": [{"by": "role", "value": "button", "name": name, "exact": True}]}]})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["data"]["appended"], 0)
        reg = resolved_registry(self.db, 1)["registry"]
        self.assertEqual(len(reg["save"]["candidates"]), 1)
        from app.models import SelectorLearned
        self.assertEqual(self.db.query(SelectorLearned).count(), 2)
        lid = self.db.query(SelectorLearned).first().id
        r = self.client.patch(f"/api/selectors/learned/{lid}", json={"action": "approve"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(resolved_registry(self.db, 1)["registry"]["save"]["candidates"]), 2)

    def test_scope_and_delete_change_snapshot_version(self):
        self.db.add(SelectorKey(project_id=1, key="save", candidates='[{"by":"css","value":"#save"}]'))
        self.db.commit()
        first = resolved_registry(self.db, 1)["version"]
        self.db.add(SelectorScope(project_id=1, vm_iframe="#vm"))
        self.db.commit()
        second = resolved_registry(self.db, 1)["version"]
        self.assertNotEqual(first, second)
        self.db.query(SelectorKey).delete()
        self.db.commit()
        self.assertNotEqual(second, resolved_registry(self.db, 1)["version"])


if __name__ == "__main__":
    unittest.main()
