"""Multica retest API regressions: isolated database, mocked runners, no external calls."""
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.session import Base, get_db
from app.db import migrate
from app.core.deps import get_current_user
from app.models import EvalRun, EvalQuery, Project, ProjectMember, User
from app.models.ai_eval import EvalTask, EvalExperiment
from app.services.eval_snapshot import payload_of


class RetestTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(id=1, username='local-test', name='Test', password_hash='x', is_platform_admin=True)
        self.db.add_all([self.user, Project(id=1, name='P', code='P'), Project(id=2, name='Q', code='Q')])
        self.db.flush()
        self.db.add(EvalQuery(id=1, project_id=1, title='Edited title', prompt='Edited prompt', expected='Edited expected'))
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.runners = patch('app.services.dispatcher.online_eval_runners', return_value=['r1', 'r2'])
        self.online = self.runners.start()

    def tearDown(self):
        self.runners.stop()
        app.dependency_overrides.clear()
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def run_row(self, id, *, payload=None, **kwargs):
        p = {'eval_query_id': 1, 'title': 'Original title', 'prompt': f'Original prompt {id}', 'expected': 'Original expected',
             'attachments': [{'name': 'original.csv', 'url': '/uploads/original.csv'}], 'turn_index': 0,
             'dialog_options': {'model': 'GLM-5.3', 'chatMode': '边想边做', 'thinkingDepth': '高'}}
        p.update(payload or {})
        values = dict(id=id, project_id=1, eval_query_id=1, batch_id='old-batch', target_engine='namiwork', runner='r1',
                      status='judged', verdict='fail', score=2, answer='Original answer', share_link='https://example.com/old',
                      payload=json.dumps(p), pushed_multica=True, multica_ref=f'M-{id}')
        values.update(kwargs)
        row = EvalRun(**values)
        self.db.add(row)
        self.db.commit()
        return row

    def post(self, ids, expected=200, **kwargs):
        response = self.client.post('/api/eval-export/multica-retest', json={'project_id': 1, 'run_ids': ids, **kwargs})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json().get('data')

    def test_defaults_snapshot_and_results_are_isolated(self):
        source = self.run_row(10)
        before = (source.payload, source.answer, source.verdict, source.score, source.share_link, source.pushed_multica)
        result = self.post([10, 10])
        self.assertEqual(result['source_count'], 1)
        new = self.db.get(EvalRun, result['run_ids'][0])
        p = payload_of(new)
        self.assertEqual(p['prompt'], 'Original prompt 10')
        self.assertEqual(p['expected'], 'Original expected')
        self.assertEqual(p['attachments'], payload_of(source)['attachments'])
        self.assertEqual(p['dialog_options'], payload_of(source)['dialog_options'])
        self.assertIsNone(p['conversation_group'])
        self.assertEqual(new.target_engine, 'namiwork')
        self.assertEqual(new.multica_retest_source_id, 10)
        self.assertIsNone(new.answer)
        self.assertIsNone(new.verdict)
        self.assertIsNone(new.share_link)
        self.assertFalse(new.pushed_multica)
        self.db.refresh(source)
        self.assertEqual(before, (source.payload, source.answer, source.verdict, source.score, source.share_link, source.pushed_multica))
        task = self.db.get(EvalTask, result['task_id'])
        self.assertEqual(task.last_batch_id, result['batch_id'])
        manifest = json.loads(self.db.query(EvalExperiment).one().manifest)
        self.assertEqual(manifest['dataset'][0]['prompt'], p['prompt'])
        self.assertEqual(manifest['planned_runs'][0]['run_id'], new.id)
        item = self.client.get('/api/eval-export/multica-results?project_id=1').json()['data']['items'][0]
        self.assertEqual(item['active_retests'], 1)
        self.post([10], expected=409)
        self.assertEqual(self.db.query(EvalTask).count(), 1)
        new.status = 'judged'; new.verdict = 'pass'; new.score = 5
        self.db.commit()
        history = self.client.get('/api/eval-export/multica-results/10/retests').json()['data']
        self.assertEqual(history['source']['verdict'], 'fail')
        self.assertEqual(history['retests'][0]['verdict'], 'pass')
        items = self.client.get('/api/eval-export/multica-results?project_id=1').json()['data']['items']
        self.assertEqual(items[0]['retest_count'], 1)
        self.assertEqual(items[0]['active_retests'], 0)
        self.assertEqual(items[0]['latest_retest']['run_id'], new.id)
        self.post([10])  # completed retests do not block the next round

    def test_product_model_overrides_and_reset(self):
        self.run_row(10, payload={'configuration_id': 'old', 'configuration_label': 'old-label'})
        r = self.post([10], target_engines=['workbuddy', 'qwork'], model='  DeepSeek-V4  ')
        self.assertEqual(len(r['run_ids']), 2)
        for id in r['run_ids']:
            p = payload_of(self.db.get(EvalRun, id))
            self.assertEqual(p['dialog_options'], {'model': 'DeepSeek-V4'})
            self.assertNotIn('configuration_label', p)
        self.run_row(20)
        r = self.post([20], model=' ')
        self.assertEqual(payload_of(self.db.get(EvalRun, r['run_ids'][0]))['dialog_options'], {'chatMode': '边想边做', 'thinkingDepth': '高'})

    def test_multi_turn_context_fanout_and_dedup(self):
        for id, turn in [(10, 0), (11, 1)]:
            self.run_row(id, payload={'conversation_group': 'g', 'turn_index': turn})
        # Same name from another product/config/trial/batch must never enter this conversation.
        self.run_row(12, payload={'conversation_group': 'g'}, target_engine='qwork')
        self.run_row(13, payload={'conversation_group': 'g', 'configuration_id': 'other'})
        self.run_row(14, payload={'conversation_group': 'g', 'trial_index': 2})
        self.run_row(15, payload={'conversation_group': 'g'}, batch_id='another-batch')
        r = self.post([11], target_engines=['namiwork', 'workbuddy'])
        self.assertEqual(r['context_count'], 1)
        created = [self.db.get(EvalRun, id) for id in r['run_ids']]
        self.assertEqual(len(created), 4)
        self.assertEqual({n.multica_retest_source_id for n in created}, {10, 11})
        self.assertEqual(len({payload_of(n)['conversation_group'] for n in created}), 2)
        for engine in ('namiwork', 'workbuddy'):
            group = [n for n in created if n.target_engine == engine]
            self.assertEqual([payload_of(n)['turn_index'] for n in group], [0, 1])
            self.assertEqual(len({n.runner for n in group}), 1)
        self.post([10], expected=409)  # preceding context is also protected against duplicate execution
        for n in created: n.status = 'done'
        self.db.commit()
        r = self.post([10, 11])
        self.assertEqual(len(r['run_ids']), 2)

    def test_missing_context_and_deleted_library(self):
        self.run_row(10, payload={'conversation_group': 'broken', 'turn_index': 1})
        self.post([10], expected=400)
        self.run_row(20, eval_query_id=None)
        self.post([20])  # frozen input survives deletion from the query library
        self.run_row(30, payload={'prompt': ''}, eval_query_id=None)
        self.post([30], expected=400)
        self.run_row(40, payload={'prompt': ''})
        self.post([40], expected=400)  # never substitute a potentially edited library prompt for a missing snapshot

    def test_validation_failures_are_atomic(self):
        self.run_row(10)
        self.run_row(20, project_id=2)
        self.run_row(30, pushed_multica=False)
        for ids in ([999], [10, 20], [10, 30]): self.post(ids, expected=400)
        self.post([10], expected=400, target_engines=['invalid'])
        self.post([10], expected=422, target_engines=[])
        self.post([], expected=422)
        self.post([10], expected=404, project_id=999)
        self.online.return_value = []
        self.post([10], expected=400)
        self.assertEqual(self.db.query(EvalTask).count(), 0)
        self.assertEqual(self.db.query(EvalRun).count(), 3)

    def test_pinned_device_remains_on_owner(self):
        self.run_row(10, target_device='vm-1', runner='r2')
        result = self.post([10])
        new = self.db.get(EvalRun, result['run_ids'][0])
        self.assertEqual((new.runner, new.target_device, json.loads(new.eligible_runners)), ('r2', 'vm-1', ['r2']))
        self.run_row(20, target_device='vm-offline', runner='offline')
        self.post([20], expected=400)
        result = self.post([20], target_engines=['qwork'])
        self.assertIsNone(self.db.get(EvalRun, result['run_ids'][0]).target_device)

    def test_pagination_legacy_filters_and_permissions(self):
        self.run_row(10, target_engine='qwork', multica_pushed_at=datetime(2026, 9, 18, 1), payload={'title': 'Needle 100%'})
        self.run_row(20, target_engine=None)
        self.run_row(30, project_id=2)
        self.run_row(40, pushed_multica=False)
        # More than the old results view's 500-row cap.
        self.db.add_all([EvalRun(project_id=1, pushed_multica=True, status='done', payload='{}') for _ in range(505)])
        self.db.commit()
        path = '/api/eval-export/multica-results'
        data = self.client.get(path, params={'project_id': 1, 'page_size': 1}).json()['data']
        self.assertEqual(data['total'], 507)
        self.assertEqual(data['items'][0]['run_id'], 10)
        data = self.client.get(path, params={'project_id': 1, 'page': 507, 'page_size': 1}).json()['data']
        self.assertEqual(data['items'][0]['run_id'], 20)
        self.assertIsNone(data['items'][0]['multica_pushed_at'])
        for term in ('100%', '10', 'M-10'):
            data = self.client.get(path, params={'project_id': 1, 'search': term}).json()['data']
            self.assertEqual([x['run_id'] for x in data['items']], [10])
        self.user.is_platform_admin = False
        self.db.add(ProjectMember(project_id=1, user_id=1, role='guest')); self.db.commit()
        self.assertEqual(self.client.get(path, params={'project_id': 1}).status_code, 200)
        self.assertEqual(self.client.get(path, params={'project_id': 2}).status_code, 403)
        self.assertEqual(self.client.get('/api/eval-export/multica-results/30/retests').status_code, 403)
        self.post([10], expected=403)

    def test_success_only_push_timestamp(self):
        self.run_row(10, pushed_multica=False)
        self.run_row(20, pushed_multica=False)
        def push(row, query=None):
            if row.id == 20: raise ValueError('mock failure')
            return 'task-id'
        with patch('app.services.multica.push_abnormal_run', side_effect=push), \
             patch('app.services.multica.check_skill_ready'), \
             patch('app.services.multica.push_time', return_value=datetime(2026, 9, 18, 0, 1)):
            data = self.client.post('/api/eval-export/multica', json={'project_id': 1, 'run_ids': [10, 20]}).json()['data']
        self.assertEqual(data['pushed'], 1)
        self.assertEqual(self.db.get(EvalRun, 10).multica_pushed_at, datetime(2026, 9, 18, 0, 1))
        self.assertFalse(self.db.get(EvalRun, 20).pushed_multica)
        self.assertIsNone(self.db.get(EvalRun, 20).multica_pushed_at)


class MigrationTests(unittest.TestCase):
    def test_old_schema_upgrades_twice_without_changing_feedback(self):
        engine = create_engine('sqlite://')
        with engine.begin() as c:
            c.execute(text('CREATE TABLE eval_run (id INTEGER PRIMARY KEY, pushed_multica BOOLEAN, multica_ref TEXT)'))
            c.execute(text("INSERT INTO eval_run VALUES (1, 1, 'old-ref')"))
        with patch.object(migrate, 'engine', engine):
            migrate.ensure_eval_multica_columns()
            migrate.ensure_eval_multica_columns()
        with engine.connect() as c:
            self.assertEqual(tuple(c.execute(text('SELECT * FROM eval_run')).one()), (1, 1, 'old-ref', None, None))
        self.assertEqual(len(inspect(engine).get_indexes('eval_run')), 1)
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
