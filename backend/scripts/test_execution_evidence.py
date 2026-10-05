"""Isolated import -> queue -> report -> version invalidation integration."""
import copy
import json
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api import exec_queue, verified_import
from app.core.deps import get_current_user, require_runner_ctx
from app.db.session import Base, get_db
from app.models import Project, RunnerDevice, ExecRun, TestCase
from app.services.execution_evidence import fingerprint, execution_contract, normalize_script, case_readiness
from app.services.selectors import resolved_registry

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = [{'action':'click','target':{'selector':'#start','frame':'shell'}}, {'action':'assert_text','target':{'selector':'#result','frame':'shell'},'args':{'expected':'完成'}}]
REPORT = [{'action':'click','ok':True}, {'action':'assert_text','ok':True,'check':{'actual':'完成','expected':'完成'}}]

def evidence(payload, report=REPORT):
    return dict(version='qalab-execution-v1', script_sha256=fingerprint(normalize_script(payload['script'])),
                contract_sha256=fingerprint(execution_contract(payload)), report_sha256=fingerprint(report), runtime_sha256='a'*64,
                node_version='v22', platform='test', mode='script', strict_replay=True, reset=True, precondition=False)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(Project(id=1,name='P',code='P'))
        self.device = RunnerDevice(id=1, owner_id=1, runner_id='fixture', name='fixture', token='fixture', last_exec_at=datetime.now(timezone.utc).replace(tzinfo=None), last_seen_at=datetime.now(timezone.utc).replace(tzinfo=None))
        self.db.add(self.device); self.db.commit()
        self.app=FastAPI(); self.app.include_router(verified_import.router); self.app.include_router(exec_queue.router)
        def db(): yield self.db
        self.app.dependency_overrides[get_db]=db
        self.app.dependency_overrides[get_current_user]=lambda: SimpleNamespace(id=1,is_platform_admin=True)
        self.app.dependency_overrides[require_runner_ctx]=lambda: SimpleNamespace(device=self.device,runner='fixture')
        self.client=TestClient(self.app)
        self.registry=resolved_registry(self.db,1)
        self.case=dict(title='main flow',steps='click and check',expected='完成',exec_kind='gui',script=copy.deepcopy(SCRIPT),report=copy.deepcopy(REPORT),verdict='pass',executor='fixture',environment='fixture',scope='fixture',finished_at=datetime.now(timezone.utc).isoformat(),duration_ms=10,selector_registry=self.registry)
        self.case['execution_evidence']=evidence(self.case)
        self.packet=dict(project_id=1,runner_device_id=1,external_id='fixture-001',requirement='fixture',cases=[self.case])
    def tearDown(self): self.db.close(); self.engine.dispose()
    def import_case(self):
        r=self.client.post('/api/verified-imports',json=self.packet)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['data']['records'][0]
    def test_changed_script_report_and_precondition_rejected_before_import(self):
        for field in ('script','report','precondition','selector_registry'):
            p=copy.deepcopy(self.packet); c=p['cases'][0]
            if field=='script': c['script'][0]['target']['selector']='#changed'
            elif field=='report': c['report'][1]['check']['actual']='changed'
            elif field=='precondition': c[field]='open another page'
            else: c[field]['vmIframe']='#changed'
            r=self.client.post('/api/verified-imports',json=p)
            self.assertEqual(r.status_code,422,(field,r.text))
        self.assertEqual(self.db.query(ExecRun).count(),0)
    def test_two_queue_passes_certify_and_script_change_invalidates(self):
        record=self.import_case(); tc=self.db.get(TestCase,record['case_id'])
        self.assertEqual(case_readiness(self.db,tc)['state'],'pending','external evidence is not queue certification')
        r=self.client.post('/api/exec-queue/enqueue-cases',json=dict(project_id=1,runner='fixture',test_case_ids=[tc.id],verification_runs=2))
        self.assertEqual(r.status_code,200,r.text)
        ids=r.json()['data']['run_ids']; self.assertEqual(len(ids),2)
        for n,rid in enumerate(ids):
            row=self.db.get(ExecRun,rid); payload=json.loads(row.payload)
            row.status='running'; self.db.commit()
            res=self.client.patch(f'/api/exec-queue/{rid}?runner=fixture',json=dict(verdict='pass',report=REPORT,execution_evidence=evidence(payload)))
            self.assertEqual(res.status_code,200,res.text)
            self.assertEqual(case_readiness(self.db,tc)['consecutive_passes'],n+1)
        detail=self.client.get(f'/api/exec-queue/{ids[-1]}').json()['data']
        self.assertTrue(detail['matches_current_version']); self.assertEqual(detail['replay_readiness']['state'],'verified')
        script=json.loads(tc.script); script[0]['target']['selector']='#new';tc.script=json.dumps(script);self.db.commit()
        detail=self.client.get(f'/api/exec-queue/{ids[-1]}').json()['data']
        self.assertFalse(detail['matches_current_version']); self.assertEqual(detail['replay_readiness']['state'],'pending')
    def test_manual_correction_and_runtime_change_do_not_certify(self):
        record=self.import_case(); tc=self.db.get(TestCase,record['case_id'])
        payload=exec_queue._payload_of(tc,self.db); payload['strict_replay']=True
        for version in ('a','b'):
            ev=evidence(payload);ev['runtime_sha256']=version*64
            self.db.add(ExecRun(project_id=1,test_case_id=tc.id,kind='gui',runner='fixture',status='passed',verdict='pass',payload=json.dumps({**payload,'execution_evidence':ev})))
        self.db.commit()
        self.assertEqual(case_readiness(self.db,tc)['consecutive_passes'],1)
        latest=self.db.query(ExecRun).order_by(ExecRun.id.desc()).first();latest.reason='[人工纠偏] pass';self.db.commit()
        self.assertEqual(case_readiness(self.db,tc)['consecutive_passes'],0)
    def test_queue_report_mismatch_rejected_and_legacy_report_does_not_certify(self):
        record=self.import_case();tc=self.db.get(TestCase,record['case_id']);payload=exec_queue._payload_of(tc,self.db)
        self.db.add(ExecRun(id=100,project_id=1,test_case_id=tc.id,kind='gui',runner='fixture',runner_device_id=1,status='running',payload=json.dumps(payload)));self.db.commit()
        ev=evidence(payload);ev['script_sha256']='b'*64
        r=self.client.patch('/api/exec-queue/100?runner=fixture',json=dict(verdict='pass',report=REPORT,execution_evidence=ev))
        self.assertEqual(r.status_code,422)
        self.assertEqual(self.db.get(ExecRun,100).status,'running')
        r=self.client.patch('/api/exec-queue/100?runner=fixture',json=dict(verdict='pass',report=REPORT))
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(case_readiness(self.db,tc)['state'],'pending')
    def test_js_python_fingerprints_and_skill_agree(self):
        vectors=[None,True,0,-0.0,1.0,1e-7,1e20,{'𐀀':'视觉🙂','z':[SCRIPT,REPORT],'\ue000':False}]
        js="import {fingerprint} from './tools/qalab-runner/execution-evidence.mjs'; import {readFileSync} from 'node:fs'; console.log(JSON.stringify(JSON.parse(readFileSync(0,'utf8')).map(fingerprint)))"
        out=subprocess.run(['node','--input-type=module','-e',js],input=json.dumps(vectors),text=True,capture_output=True,cwd=ROOT,check=True)
        self.assertEqual(json.loads(out.stdout),[fingerprint(v) for v in vectors])
        valid=subprocess.run(['node',str(ROOT/'tools/skills/qalab-requirement-test/scripts/validate-evidence.mjs')],input=json.dumps(self.packet,ensure_ascii=False),text=True,capture_output=True)
        self.assertEqual(valid.returncode,0,valid.stderr)

if __name__=='__main__': unittest.main()
