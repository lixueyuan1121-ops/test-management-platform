// Offline contract checks shared by Windows and Python clients. Never executes a case.
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { validateNetworkScript, NETWORK_ACTIONS } from './network-contract.mjs';
import { validateEvidence } from './validate-evidence.mjs';
import { validateCodexObservation } from './codex-observation.mjs';
import { FLOW_ACTIONS, validateFlow, isRef, validateAttribute } from './script-flow.mjs';

const object = v => !!v && typeof v === 'object' && !Array.isArray(v);
const text = v => typeof v === 'string' && !!v.trim();
const has = (v, k) => Object.hasOwn(v, k);
const integer = (v, min = 0) => Number.isSafeInteger(v) && v >= min;
const targeted = new Set(['click','hover','fill','type','set_checked','select_option','wait_for','get_text','get_attribute','probe','assert_text','assert_visible','assert_absent','assert_list_from_response']);
const actions = new Set([...targeted, ...NETWORK_ACTIONS, ...FLOW_ACTIONS, 'connect','press','wait_response','screenshot','mock_route','unmock_route']);
const ensure = (yes, message) => { if (!yes) throw new Error(message); };

function targetCheck(t, label, depth = 0) {
  ensure(object(t) && (text(t.key) || text(t.selector)), `${label} 缺 target.key/selector`);
  ensure(depth <= 3, `${label} within 最多三层`);
  for (const k of ['key','selector','frame']) if (has(t,k)) ensure(text(t[k]), `${label} ${k} 必须为非空字符串`);
  if (has(t,'has_text')) ensure(typeof t.has_text === 'string' || isRef(t.has_text), `${label} has_text 必须为字符串`);
  if (has(t,'has_text_exact')) ensure(typeof t.has_text_exact === 'boolean' && (text(t.has_text) || isRef(t.has_text)), `${label} has_text_exact 需要布尔值及非空 has_text`);
  if (has(t,'attribute')) validateAttribute(t.attribute);
  if (has(t,'nth')) ensure(integer(t.nth), `${label} nth 必须是非负整数`);
  if (t.within != null) targetCheck(t.within, label, depth + 1);
}

export function validateScript(script, title = '') {
  ensure(Array.isArray(script) && script.length > 0, `${title} script 必须为非空数组`);
  const flow = validateFlow(script);
  const captured = new Set();
  script.forEach((s, i) => {
    const label = `${title} 第${i+1}步「${s?.action || '?'}」`;
    ensure(object(s) && actions.has(s.action), `${label} 非平台 GUI/E2E action`);
    const a = s.args ?? {};
    ensure(object(a), `${label} args 必须为对象`);
    ensure(s.target == null || object(s.target), `${label} target 必须为对象`);
    if (targeted.has(s.action) || (s.action === 'press' && s.target && Object.keys(s.target).length)) targetCheck(s.target,label);
    if (s.action === 'probe') targetCheck(a.ready,label+' ready');
    if (has(a,'timeout_ms')) ensure(integer(a.timeout_ms), `${label} timeout_ms 必须是非负整数`);
    for (const k of ['contains','negate','if_visible']) if (has(a,k)) ensure(typeof a[k] === 'boolean', `${label} ${k} 必须为布尔值`);
    if (['fill','type'].includes(s.action)) ensure(typeof a.text === 'string' || isRef(a.text), `${label} 缺 args.text（空字符串可用于清空）`);
    if (s.action === 'press') ensure(text(a.key_name), `${label} 缺 args.key_name；不能用 args.key`);
    if (s.action === 'set_checked') ensure(typeof a.checked === 'boolean' || isRef(a.checked), `${label} 缺布尔值 args.checked`);
    if (s.action === 'select_option') ensure(typeof a.values === 'string' || isRef(a.values) || (Array.isArray(a.values) && a.values.every(v=>typeof v==='string' || isRef(v))), `${label} 缺 args.values`);
    if (s.action === 'click' && has(a,'click_count')) ensure(integer(a.click_count,1), `${label} click_count 必须为正整数`);
    if (['mock_route','unmock_route'].includes(s.action)) ensure(text(a.url), `${label} 缺 args.url`);
    if (!flow && has(a,'save_as')) {
      ensure(s.action === 'get_text' && typeof a.save_as === 'string' && /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(a.save_as) && !captured.has(a.save_as), `${label} save_as 无效或重复`);
      captured.add(a.save_as);
    }
    if (!flow && has(a,'expected_from')) ensure(s.action === 'assert_text' && captured.has(a.expected_from) && !has(a,'expected'), `${label} expected_from 必须引用之前 get_text 保存的值，且不能同时提供 expected`);
    else if (s.action === 'assert_text' && !has(a,'expected_from')) ensure(typeof a.expected === 'string' || isRef(a.expected), `${label} 缺字符串 args.expected 或有效 expected_from`);
  });
  validateNetworkScript(script);
  ensure(script.some(s=>s.action.startsWith('assert_')), `${title} 缺少业务断言`);
}

function reportCheck(c) {
  ensure(c.verdict === 'pass' && Array.isArray(c.report) && c.report.length === c.script.length, `${c.title} 只导入通过且逐步报告完整的用例`);
  c.script.forEach((s,i)=>{
    const r=c.report[i], label=`${c.title} 第${i+1}步`;
    ensure(object(r) && r.action === s.action && r.ok === true, `${label} 脚本与报告不一致或执行失败`);
    if (!s.action.startsWith('assert')) return;
    const k=r.check, a=s.args || {};
    ensure(object(k) && has(k,'actual') && has(k,'expected') && k.pass !== false, `${label} 断言缺实际值/预期值或报告失败`);
    if (s.action === 'assert_text') {
      ensure(typeof k.actual === 'string' && typeof k.expected === 'string', `${label} 文本断言值必须为字符串`);
      if (has(a,'expected')) ensure(k.expected === a.expected, `${label} 报告预期与脚本不一致`);
      ensure(!has(k,'mode') || k.mode === (a.contains ? 'contains' : 'equals'), `${label} 断言模式与脚本不一致`);
      ensure(!has(k,'negate') || k.negate === !!a.negate, `${label} negate 与脚本不一致`);
      const match = a.contains ? k.actual.includes(k.expected) : k.actual === k.expected;
      // Existing Runner truncates actual to 200 UTF-16 units. Never reject a
      // valid long-text report merely because its suffix is not serialized.
      const truncatedPossible = k.actual.length === 200 && !!c.execution_evidence &&
        (a.contains ? (!a.negate && !match) : (a.negate ? match : k.expected.length > 200 && k.expected.startsWith(k.actual)));
      ensure((a.negate ? !match : match) || truncatedPossible, `${label} 实际值不满足文本断言，不能仅填写 ok=true`);
    } else if (s.action === 'assert_visible' || s.action === 'assert_absent') {
      const expected=s.action === 'assert_visible';
      ensure(k.actual === expected && k.expected === expected, `${label} 可见性断言值不一致`);
    } else if (['assert_network_count','assert_fault_hits'].includes(s.action)) {
      ensure(k.actual === a.expected && k.expected === a.expected, `${label} 网络计数断言不一致`);
    } else if (k.mode === 'equals') ensure(isDeepStrictEqual(k.actual,k.expected), `${label} equals 断言实际值与预期值不一致`);
  });
}

export function validatePayload(p, {draft = false} = {}) {
  ensure(object(p), '导入包必须为对象');
  const codex=p.import_mode==='codex';
  ensure(p.import_mode==null || codex,'未知 import_mode');
  ensure(integer(p.project_id,1),'需要真实的正整数 project_id');
  ensure(codex?p.runner_device_id==null:integer(p.runner_device_id,1),codex?'Codex 实测导入不绑定 Runner 设备':'Runner 实测模式需要真实 runner_device_id');
  ensure(text(p.requirement), '缺少 requirement');
  if (!draft) ensure(typeof p.external_id === 'string' && /^[A-Za-z0-9_-]{8,100}$/.test(p.external_id), 'external_id 必须为8–100位字母数字、下划线或横线');
  ensure(Array.isArray(p.cases) && p.cases.length > 0 && p.cases.length <= 20, '每批需要1–20条用例');
  const titles=new Set();
  for (const c of p.cases) {
    ensure(object(c) && text(c.title) && !titles.has(c.title), '用例标题必须非空且同批唯一'); titles.add(c.title);
    ensure(!c.resolution, '疑似重复由平台集中确认，不允许提交 resolution');
    const kind=c.exec_kind || 'e2e';
    ensure(['gui','e2e','api'].includes(kind), `${c.title} exec_kind 仅支持 gui/e2e/api`);
    if (kind !== 'api') {
      ensure(!text(c.precondition), `${c.title} 请将前置导航写入 setup_script/script；严格验证不使用自然语言前置`);
      validateScript(draft ? [...(c.setup_script || []), ...(c.script || [])] : c.script,c.title);
    } else ensure(Array.isArray(c.script) && c.script.length && c.script.some(s=>s.action?.startsWith('assert')), `${c.title} API脚本需要断言`);
    if (!draft) {
      ensure(!c.setup_script?.length, `${c.title} 请将已实际执行的前置步骤合入完整 script`);
      if(codex)validateCodexObservation(c);
      else { reportCheck(c); validateEvidence(c, {strict:kind !== 'api'}); }
    }
  }
  return p;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { const i=process.argv.indexOf('--payload'); validatePayload(JSON.parse(readFileSync(i<0?0:process.argv[i+1],'utf8').replace(/^\uFEFF/,'')), {draft:process.argv.includes('--draft')}); process.stdout.write('脚本协议校验通过\n'); }
  catch(e) { process.stderr.write(e.message+'\n'); process.exitCode=1; }
}
