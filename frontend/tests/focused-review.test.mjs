import {test} from 'node:test'
import assert from 'node:assert/strict'
import {reviewRows,filterRules} from '../src/utils/focusedReview.js'
const fixture=()=>({rules:[{id:'R1',condition:'已登录',action:'点击分享',expected:'打开分享面板',criteria:[{id:'C1',text:'显示面板'}],source_type:'explicit',source_material_ids:[],status:'pending',review_note:''}],questions:[],scenarios:[]})
test('明确内容无需逐条确认，筛选显示空待确认列表',()=>{const rows=reviewRows(fixture());assert.equal(rows[0].status,'confirmed');assert.equal(filterRules(rows,'pending').length,0)})
test('冲突回答清楚后自动解除待确认',()=>{const d=fixture();d.questions=[{blocking:true,answer:'',rule_ids:['R1']}];assert.equal(reviewRows(d)[0].status,'pending');d.questions[0].answer='按原文打开面板';assert.equal(reviewRows(d)[0].status,'confirmed')})
test('备注不能代替缺失预期或未回答的问题',()=>{const d=fixture();d.rules[0].expected='';d.rules[0].review_note='按此测试';assert.equal(reviewRows(d)[0].status,'pending')})
test('本期排除不被自动纳入，场景不因未勾选被阻断',()=>{const d=fixture();d.scenarios=[{rule_id:'R1',criterion_ids:['C1'],actor:'用户',given:'已登录',when:'点击分享',then:'显示面板',reviewed:false}];assert.equal(reviewRows(d)[0].status,'confirmed');d.rules[0].status='excluded';assert.equal(filterRules(reviewRows(d),'excluded').length,1)})
