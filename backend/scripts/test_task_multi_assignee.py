import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.session import Base, get_db
from app.core.deps import get_current_user
from app.core.enums import ProjectRole
from app.models import User, Project, ProjectMember, Task, TaskAssignee
from app.api import tasks, stats, reports

class MultiAssigneeTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(bind=self.engine,expire_on_commit=False)
        with self.factory() as db:
            db.add(Project(id=1,name='test',code='test'))
            for uid in range(1,5):
                db.add(User(id=uid,username=f'u{uid}',name=f'User {uid}',password_hash='test',is_platform_admin=False))
                db.add(ProjectMember(project_id=1,user_id=uid,role=ProjectRole.admin if uid==1 else ProjectRole.member))
            db.commit()
        self.uid=1
        def session():
            with self.factory() as db: yield db
        def current():
            with self.factory() as db:return db.get(User,self.uid)
        app=FastAPI()
        for router in (tasks.router,stats.router,reports.router):app.include_router(router)
        app.dependency_overrides[get_db]=session;app.dependency_overrides[get_current_user]=current
        self.client=TestClient(app)
        self.notify=patch('app.services.notify.notify_task_assigned');self.notify.start()
    def tearDown(self):
        self.notify.stop();self.client.close();self.engine.dispose()
    def create(self,**extra):
        r=self.client.post('/api/tasks',json={'project_id':1,'title':'Shared task','assigned_date':'2026-09-27','assigned_to_ids':[2,3],**extra})
        self.assertEqual(r.status_code,200,r.text);return r.json()['data']
    def rows(self):
        return self.client.get('/api/tasks',params={'project_id':1,'mine':True}).json()['data']
    def test_create_edit_reload_remove_and_legacy_single(self):
        row=self.create(assigned_to_ids=[2,3,2]);tid=row['id']
        self.assertEqual(row['assigned_to_ids'],[2,3]);self.assertEqual(row['assigned_to_name'],'User 2、User 3')
        for uid in (2,3):self.uid=uid;self.assertEqual([r['id'] for r in self.rows()],[tid])
        self.uid=4;self.assertEqual(self.rows(),[]);self.uid=1
        r=self.client.patch(f'/api/tasks/{tid}',json={'assigned_to_ids':[3,4]}).json()['data']
        self.assertEqual(r['assigned_to_ids'],[3,4]);self.uid=2;self.assertEqual(self.rows(),[])
        self.uid=1
        unchanged=self.client.patch(f'/api/tasks/{tid}',json={'assigned_to':3,'title':'Legacy metadata edit'}).json()['data']
        self.assertEqual(unchanged['assigned_to_ids'],[3,4])
        r=self.client.patch(f'/api/tasks/{tid}',json={'assigned_to':2}).json()['data']
        self.assertEqual(r['assigned_to_ids'],[2]);self.assertEqual(r['assigned_to'],2)
        with self.factory() as db:self.assertEqual(db.query(TaskAssignee).count(),0)
        legacy=self.create(assigned_to_ids=None,assigned_to=3)
        self.assertEqual(legacy['assigned_to_ids'],[3])
    def test_old_database_create_all_is_idempotent_and_keeps_legacy_assignee(self):
        row=self.create(assigned_to_ids=None,assigned_to=2)
        TaskAssignee.__table__.drop(self.engine)
        Base.metadata.create_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.uid=2
        self.assertEqual(self.rows()[0]['assigned_to_ids'],[2])
        self.assertEqual(self.rows()[0]['id'],row['id'])

    def test_invalid_assignment_is_atomic_and_empty_rejected(self):
        tid=self.create()['id']
        for payload,code in [({'assigned_to_ids':[]},422),({'assigned_to_ids':[2,999]},404),({'assigned_to_ids':[2,3],'assigned_to':4},422)]:
            r=self.client.patch(f'/api/tasks/{tid}',json={'title':'must not persist',**payload});self.assertEqual(r.status_code,code,r.text)
        with self.factory() as db:
            t=db.get(Task,tid);self.assertEqual(t.title,'Shared task');self.assertEqual(t.assigned_to_ids,[2,3])
    def test_copy_and_delete_preserve_then_remove_links(self):
        tid=self.create()['id'];r=self.client.post('/api/tasks/copy',params={'project_id':1,'target_date':'2026-09-28'})
        self.assertEqual(r.status_code,200,r.text)
        with self.factory() as db:
            rows=db.query(Task).all();self.assertEqual(len(rows),2);self.assertTrue(all(t.assigned_to_ids==[2,3] for t in rows))
        self.assertEqual(self.client.delete(f'/api/tasks/{tid}').status_code,200)
        with self.factory() as db:self.assertEqual(db.query(TaskAssignee).filter_by(task_id=tid).count(),0)
    def test_secondary_assignee_report_permission_and_workload(self):
        tid=self.create()['id'];self.uid=3
        r=self.client.post('/api/daily-reports',json={'task_id':tid,'report_date':'2026-09-27','summary':'shared progress'})
        self.assertEqual(r.status_code,200,r.text)
        daily=self.client.get('/api/stats/daily',params={'project_id':1,'date':'2026-09-27'}).json()['data']
        self.assertEqual(daily['should_submit'],2);self.assertEqual(daily['submitted'],2);self.assertEqual(daily['not_submitted'],[])
        self.uid=4;r=self.client.post('/api/daily-reports',json={'task_id':tid,'report_date':'2026-09-27'})
        self.assertEqual(r.status_code,403)
        self.uid=1;r=self.client.get('/api/stats/workload',params={'project_id':1,'from':'2026-09-27','to':'2026-09-28'})
        self.assertEqual(r.status_code,200,r.text);data=r.json()['data']
        self.assertEqual(data['total_tasks'],1);self.assertEqual({m['user_id']:m['task_cnt'] for m in data['members']},{2:1,3:1})

if __name__=='__main__':unittest.main()
