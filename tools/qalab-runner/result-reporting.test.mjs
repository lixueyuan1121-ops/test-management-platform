import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { publishResult } from './result-reporting.mjs';

const original = { verdict: 'fail', fail_kind: 'business', reason: 'step5 actual mismatch', report: [{no:5,ok:false}] };
const deadlock = () => new Error('PATCH -> HTTP 500 msg=(1213, Deadlock found when trying to get lock)');
for (const scenario of ['recovered','exhausted','network','forbidden']) {
  test(`reporting ${scenario} preserves the original execution result`, async () => {
    const directory = mkdtempSync(join(tmpdir(), 'qalab-report-'));
    try {
      const sends = [], waits = [];
      const work = () => publishResult({runId:42,result:original,directory,wait:async n=>waits.push(n),send:async body=>{
        sends.push(structuredClone(body));
        assert.deepEqual(JSON.parse(readFileSync(join(directory,'run-42.json'))).result, original);
        if(scenario==='network')throw new Error('fetch failed');
        if(scenario==='forbidden')throw new Error('HTTP 403');
        if(scenario==='exhausted'||sends.length<3)throw deadlock();
        return {status:'failed'};
      }});
      if(scenario==='recovered'){
        assert.deepEqual(await work(),{status:'failed'});
        assert(!existsSync(join(directory,'run-42.json')));
      }else{
        await assert.rejects(work(),e=>e.savedReport===join(directory,'run-42.json'));
        assert.deepEqual(JSON.parse(readFileSync(join(directory,'run-42.json'))).result,original);
      }
      const count=['recovered','exhausted'].includes(scenario)?3:1;
      assert.equal(sends.length,count);assert.equal(waits.length,count-1);
      for(const sent of sends)assert.deepEqual(sent,original);
    }finally{rmSync(directory,{recursive:true,force:true});}
  });
}
