"""Flow contracts and real import/claim compatibility; isolated SQLite fixtures only."""
import copy
import unittest
from app.services.script_flow import validate_flow
from app.services.script_targets import validate_targets
from app.services.claude_runner import _validate_script
from app.services.script_keys import referenced_keys
from app.services.execution_evidence import fingerprint, normalize_script
from app.schemas.verified_import import CodexImport
from scripts.test_codex_import import CodexTests, codex_source, seal

def source(found=True, failed=False):
    body = codex_source(False, 'flow-fixture-0001')
    c = body['cases'][0]
    c['script'] = [
        {'action':'flow_start','args':{'version':1}},
        {'action':'probe','target':{'selector':'.answer','nth':0},'args':{'ready':{'selector':'#loaded'},'timeout_ms':100,'settle_ms':20,'save_as':'found'}},
        {'action':'click','target':{'selector':'#create'},'when':{'var':'found','equals':False}},
        {'action':'get_attribute','target':{'selector':'.answer','nth':0},'args':{'attribute_name':'data-id','save_as':'id'}},
        {'action':'assert_visible','target':{'selector':'.liked','within':{'selector':'.answer','attribute':{'name':'data-id','value':{'$var':'id'}}}}},
        {'action':'click','target':{'selector':'#restore'},'phase':'cleanup'},
    ]
    c['report']=[{'no':i+1,'action':s['action'],'ok':True,'observation':'isolated fixture observation','evidence_ref':'log-1'} for i,s in enumerate(c['script'])]
    c['report'][1]['value']=found
    if found: c['report'][2].update(skipped=True,skip_reason='condition_false')
    c['report'][3]['value']='message-1'
    c['report'][4]['check']={'actual':not failed,'expected':True,'pass':not failed}
    if failed: c['report'][4].update(ok=False,fail_kind='business',error='赞未生效')
    c['verdict']='fail' if failed else 'pass'
    return seal(body)

class FlowContractTests(unittest.TestCase):
    def test_branches_and_failure_with_cleanup(self):
        for found in (True,False):
            for failed in (True,False):
                body=source(found,failed)
                CodexImport.model_validate(body)
                script=body['cases'][0]['script']
                norm,err=_validate_script(script,set())
                self.assertIsNone(err)
                self.assertEqual(norm[2]['when'],script[2]['when'])
                self.assertEqual(norm[-1]['phase'],'cleanup')
    def test_forged_skips_and_cleanup_failure_rejected(self):
        for mutate in (
            lambda c:c['report'][2].pop('skipped'),
            lambda c:c['report'][3].update(value=None),
            lambda c:c['report'][-1].update(ok=False),
            lambda c:c['report'][4].update(skipped=True,skip_reason='condition_false'),
        ):
            body=source();mutate(body['cases'][0]);seal(body)
            with self.assertRaises(ValueError):CodexImport.model_validate(body)
    def test_required_ready_and_refs(self):
        s=source()['cases'][0]['script'];s[1]['args']['ready']={'key':'loaded'}
        self.assertIn('loaded',referenced_keys(s))
        self.assertIn('未注册',_validate_script(s,set())[1])
        self.assertIsNone(_validate_script(s,{'loaded'})[1])
        for change in (
            lambda x:x.pop(0),
            lambda x:x[1]['args'].pop('ready'),
            lambda x:x[4]['target']['within']['attribute'].update(value={'$var':'missing'}),
            lambda x:x[4]['target'].update(selector={'$var':'id'}),
        ):
            bad=copy.deepcopy(s);change(bad)
            with self.assertRaises(ValueError):validate_targets(bad)
    def test_fingerprint_includes_conditions(self):
        s=source()['cases'][0]['script'];old=fingerprint(normalize_script(s));s[2]['when']['equals']=True
        self.assertNotEqual(old,fingerprint(normalize_script(s)))

    def test_skipped_capture_cannot_locate_later_assertion(self):
        body=source();c=body['cases'][0]
        c['script'][3]['when']={'var':'found','equals':False}
        c['report'][3].pop('value')
        c['report'][3].update(skipped=True,skip_reason='condition_false')
        seal(body)
        with self.assertRaisesRegex(ValueError,'尚未赋值'):CodexImport.model_validate(body)

class FlowImportTests(CodexTests):
    def test_only_capable_assigned_runner_can_claim_flow(self):
        from app.models import ExecRun,RunnerDevice
        from app.core.deps import RunnerCtx,require_runner_ctx
        receipt=self.imported(source())
        queued=self.client.post('/api/exec-queue/enqueue-cases',json={'project_id':1,'runner':'mac','test_case_ids':[receipt['case_id']],'auto_prepare':False},headers={'x-user':'2'})
        self.assertEqual(queued.status_code,200,queued.text)
        with self.factory() as db:
            run_id=db.query(ExecRun).filter(ExecRun.id!=receipt['run_id']).one().id
            device=db.get(RunnerDevice,2)
            db.expunge(device)
        self.client.app.dependency_overrides[require_runner_ctx]=lambda:RunnerCtx(device)
        old=self.client.post(f'/api/exec-queue/{run_id}/claim?runner=mac')
        self.assertEqual(old.status_code,409,old.text)
        self.assertIn('script-flow-v1',old.text)
        with self.factory() as db:self.assertEqual(db.get(ExecRun,run_id).status,'pending')
        new=self.client.post(f'/api/exec-queue/{run_id}/claim?runner=mac&script_flow=1')
        self.assertEqual(new.status_code,200,new.text)

    def test_flow_import_preserves_script_and_full_report(self):
        from app.models import ExecRun,TestCase
        import json
        body=source(failed=True);receipt=self.imported(body)
        with self.factory() as db:
            run=db.get(ExecRun,receipt['run_id'])
            self.assertEqual(json.loads(run.report),body['cases'][0]['report'])
            self.assertEqual(json.loads(run.payload)['script'][2]['when'],{'var':'found','equals':False})

if __name__ == '__main__':
    import sys, json
    if '--dump' in sys.argv:
        print(json.dumps(source(),ensure_ascii=False))
    else: unittest.main()
