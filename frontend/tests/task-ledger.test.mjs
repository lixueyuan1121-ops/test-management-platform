import assert from 'node:assert/strict'
import { filterLedger, TASK_STATUS, hasRisk, taskDateQuery } from '../src/utils/taskLedger.js'
const defaults = { keyword:'', status:[], owner:[], priority:[], version:[], risk:'', impact:'', riskOnly:false }
const rows = [
  {id:1,title:'文档划词',status:'testing',priority:'p1',assigned_to:2,assigned_to_ids:[2,4],assigned_to_name:'平台姓名',target_version:'3.27.0',risk_level:'red',affects_release:true},
  {id:2,title:'视频画布',status:'online',priority:'p2',assigned_to:3,target_version:'3.26.0',risk_level:'green',affects_release:false},
  {id:3,title:'历史任务',status:'pending',assigned_to:2,risk_level:null,affects_release:null},
]
assert.deepEqual(filterLedger(rows,{...defaults,owner:[2],riskOnly:true,version:['3.27.0']}).map(t=>t.id),[1])
assert.deepEqual(filterLedger(rows,{...defaults,risk:'__unset',impact:'__unset'}).map(t=>t.id),[3])
assert.deepEqual(filterLedger(rows,{...defaults,impact:'no'}).map(t=>t.id),[2])
assert.deepEqual(filterLedger(rows,{...defaults,keyword:'平台姓名'}).map(t=>t.id),[1])
assert.equal(filterLedger(rows,{...defaults,priority:['p0']}).length,0)
assert.deepEqual(filterLedger(rows,{...defaults,owner:[4]}).map(t=>t.id),[1])
assert.equal(TASK_STATUS.ready_online.label,'测试完成')
assert.equal(Object.keys(TASK_STATUS).length,11)
assert.equal(hasRisk({status:'at_risk'}),true)
// Within each multi-select match any option; across filters require every condition.
assert.deepEqual(filterLedger(rows,defaults).map(t=>t.id),[1,2,3])
assert.deepEqual(filterLedger(rows,{...defaults,status:['testing','online']}).map(t=>t.id),[1,2])
assert.deepEqual(filterLedger(rows,{...defaults,owner:[4,3]}).map(t=>t.id),[1,2])
assert.deepEqual(filterLedger(rows,{...defaults,priority:['p1','p2']}).map(t=>t.id),[1,2])
assert.deepEqual(filterLedger(rows,{...defaults,version:['3.27.0','3.26.0']}).map(t=>t.id),[1,2])
assert.deepEqual(filterLedger(rows,{...defaults,version:['3.26.0','__unset']}).map(t=>t.id),[2,3])
assert.deepEqual(filterLedger(rows,{...defaults,status:['testing','online'],owner:[4,3],priority:['p1','p3'],version:['3.27.0','__unset']}).map(t=>t.id),[1])
assert.deepEqual(filterLedger(rows,{...defaults,owner:[2,4]}).map(t=>t.id),[1,3])
assert.equal(filterLedger([{id:4,assigned_to:null,assigned_to_ids:[]}],{...defaults,owner:[2,3]}).length,0)
console.log('PASS task ledger multi-select OR/AND filters, legacy null values and Feishu statuses')

// The default today-to-today picker must use the carryover-aware daily API.
assert.deepEqual(taskDateQuery(['2026-10-18', '2026-10-18']), {date:'2026-10-18'})
assert.deepEqual(taskDateQuery(['2026-10-11', '2026-10-11']), {date:'2026-10-11'})
assert.deepEqual(taskDateQuery(['2026-10-09', '2026-10-18']), {start_date:'2026-10-09',end_date:'2026-10-18'})
for (const range of [null, undefined, [], ['2026-10-18'], ['', '']]) {
  assert.deepEqual(taskDateQuery(range), {})
}
console.log('PASS daily carryover query, explicit assignment range and cleared date selection')
