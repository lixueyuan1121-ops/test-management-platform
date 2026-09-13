"""Small reproducible evaluation harness. Validation is offline; --run invokes the configured model."""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from app.schemas.requirement_analysis import RequirementDraft
from app.core.config import settings
from app.services import generators, mission_quality as q
from app.services.requirement_analysis import collect, encode, parse_object, analysis_prompt, prepare_draft

DATASET = Path(__file__).resolve().parents[1] / 'evals' / 'ai-native' / 'seed.json'


def contract(sample):
    c=sample['contract']
    return RequirementDraft.model_validate({'summary':sample['origin']['text'],'scope':'仅评测给定约束，不代表实际产品已确认',
        'rules':[{'id':'R1','title':sample['id'],'condition':c['given'],'action':c['when'],'expected':c['then'],'status':'confirmed',
                  'criteria':[{'id':'R1-C1','text':c['then']}]}]}).model_dump()


def load(path):
    data=json.loads(Path(path).read_text()); seen=set()
    for s in data['samples']:
        if s['id'] in seen: raise ValueError('重复样本编号')
        seen.add(s['id'])
        if s['stage'] not in ('analysis','case_review','evidence'): raise ValueError('未知评测阶段')
        if s['label_status'] not in ('draft','synthetic','reviewed'): raise ValueError('标签状态不合法')
        if s['label_status']=='reviewed' and not s.get('reviewed_by'): raise ValueError('人工标准样本缺少确认人')
        if s['stage'] != 'analysis': contract(s)
        if s['stage']=='case_review':
            if type(s['expected']['blocked']) is not bool: raise ValueError('缺少用例审查预期')
            if not all(k in s['case'] for k in ('precondition','steps','expected')): raise ValueError('用例资料不完整')
        elif s['stage']=='evidence' and s['expected']['verdict'] not in ('supported','contradicted','insufficient'): raise ValueError('证据预期不合法')
    return data


class Meter:
    def __init__(self, engine): self.engine=engine; self.calls=[]
    def stream_generate(self,*args,**kwargs):
        prompt=kwargs['prompt_builder'](); row={'prompt_hash':hashlib.sha256(prompt.encode()).hexdigest(),'prompt':prompt,'raw_output':'','raw_output_truncated':False,'cost_usd':None,'output_tokens':None}; self.calls.append(row)
        for event in self.engine.stream_generate(*args,**kwargs):
            # Keep malformed responses reviewable without relaxing production parsing.
            if event.get('type') == 'delta':
                value = event.get('text') or ''
                remaining = 200000 - len(row['raw_output'])
                row['raw_output'] += value[:remaining]
                row['raw_output_truncated'] |= len(value) > remaining
            elif event.get('type') == 'result' and event.get('text'):
                # collect() uses the final result when supplied, replacing streamed text.
                row['raw_output'] = event['text'][:200000]
                row['raw_output_truncated'] = len(event['text']) > 200000
            if event.get('type')=='result':
                for k in ('cost_usd','output_tokens'): row[k]=event.get(k)
            yield event


def evaluate(sample, engine):
    if sample['stage']=='analysis':
        prompt=analysis_prompt(sample['source'],[],{'warnings':['评测仅包含已保存摘录，不是整篇需求']})
        draft=prepare_draft(parse_object(collect(engine,prompt,timeout=180)),sample['source'],[])
        cids={c.id for r in draft.rules for c in r.criteria}; covered={cid for s in draft.scenarios for cid in s.criterion_ids}
        prediction={'scene_coverage':bool(cids) and cids <= covered, 'human_decisions_unset':all(r.status=='pending' for r in draft.rules) and not any(s.reviewed for s in draft.scenarios) and not any(q.answer for q in draft.questions)}
        return {'prediction':prediction,'matches_label':prediction==sample['expected'],'result':draft.model_dump(),'human_rubric':sample['rubric'],'needs_semantic_review':True}
    payload=contract(sample)
    if sample['stage']=='case_review':
        case={'id':1,'title':sample['id'],'kind':'manual','hash':'evaluation','criterion_ids':['R1-C1'],**sample['case']}
        result=q.review_plan(engine,payload,[case]); prediction={'blocked':result['status']=='blocked'}
    else:
        steps=json.loads(encode(sample['steps']))
        for step in steps:
            if 'check' in step: step['check_pass']=q.check_result(step['check'])
        data={'criteria':[{'id':'R1-C1','text':sample['contract']['then'],'rule':payload['rules'][0]}],'steps':steps}
        result=q.unobservable_result(data,[]) or q.validate_assessment(parse_object(collect(engine,q.assess_prompt(data),timeout=180)),data,[])
        prediction={'verdict':result['criteria'][0]['verdict']}
    return {'prediction':prediction,'matches_label':prediction==sample['expected'],'result':result}


def summarize(rows):
    def group(status):
        selected=[r for r in rows if r['label_status']==status]
        return {'samples':len(selected),'matches':sum(r.get('matches_label',False) for r in selected),'errors':sum(bool(r.get('error')) for r in selected)}
    return {'human_reviewed':group('reviewed'),'synthetic':group('synthetic'),'provisional':group('draft'),
            'note':'草案标签结果只供校准，不代表产品预期正确率；合成样本只验证指定能力，不能证明线上效果。费用缺失不估算。'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,default=DATASET); p.add_argument('--run',action='store_true'); p.add_argument('--provider',choices=list(generators.PROVIDERS),default='claude')
    p.add_argument('--output',type=Path); p.add_argument('--limit',type=int,default=17); p.add_argument('--sample',action='append',default=[])
    p.add_argument('--include-draft',action='store_true',help='同时向模型发送待校准的真实需求摘录，结果不计入正式准确率')
    p.add_argument('--compare',type=Path,help='历史评测报告，逐项比较标签匹配与错误变化')
    args=p.parse_args(); dataset=load(args.dataset)
    samples=[s for s in dataset['samples'] if (not args.sample or s['id'] in args.sample) and (not args.run or args.include_draft or s['label_status'] != 'draft')][:max(1,min(args.limit,50))]
    if not samples: p.error('未找到指定样本')
    if not args.run:
        print(encode({'valid':True,'samples':len(dataset['samples']),'selected':len(samples),'model_called':False,'label_counts':{k:sum(s['label_status']==k for s in dataset['samples']) for k in ('reviewed','synthetic','draft')}})); return
    if not args.output: p.error('--run 需要 --output 保存可复核报告')
    engine=generators.get_provider(args.provider)
    if not engine.is_available(): p.error('所选模型不可用')
    rows=[]; prior=json.loads(args.compare.read_text()) if args.compare else {}
    old={r['id']:r for r in prior.get('rows',[])}
    model = settings.AI_MODEL if args.provider == 'claude' else settings.DEEPSEEK_MODEL
    report={'version':q.VERSION,'provider':args.provider,'configured_model':model or None,
            'model_note':'配置为空表示使用 CLI 默认模型，未从响应获取实际模型版本；严格比较请固定模型配置。',
            'dataset_sha256':hashlib.sha256(args.dataset.read_bytes()).hexdigest(),'created_at':datetime.now(timezone.utc).isoformat(),'rows':rows}
    for s in samples:
        meter=Meter(engine); start=time.monotonic(); row={'id':s['id'],'stage':s['stage'],'label_status':s['label_status'],'expected':s['expected'],'sample_sha256':hashlib.sha256(encode(s).encode()).hexdigest()}
        try: row.update(evaluate(s,meter))
        except Exception as exc: row['error']=str(exc); row['matches_label']=False
        row.update(duration_seconds=round(time.monotonic()-start,2),calls=meter.calls)
        if s['id'] in old and old[s['id']].get('sample_sha256') == row['sample_sha256']: row['previous_matches_label']=old[s['id']].get('matches_label')
        rows.append(row); report['summary']=summarize(rows)
        args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(encode({k:row[k] for k in ('id','matches_label','duration_seconds')}),flush=True)
    print(encode(report['summary']))
    # Draft mismatches need human calibration; only reviewed/synthetic regressions fail the command.
    if any(not r.get('matches_label') and r['label_status']!='draft' for r in rows): raise SystemExit(1)

if __name__=='__main__': main()
