// script-flow-v1: flat, bounded control flow. No eval, arbitrary code or hidden AI fallback.
export const FLOW_ACTIONS = new Set(['flow_start', 'set_var', 'probe', 'get_attribute']);
const own = (v, k) => Object.hasOwn(v, k);
const obj = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const scalar = v => v === null || ['string','boolean'].includes(typeof v) || typeof v === 'number' && Number.isFinite(v);
const name = v => typeof v === 'string' && /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(v) && !['__proto__','constructor','prototype'].includes(v);
const need = (v, m) => { if (!v) throw Error(m); };
export const isRef = v => obj(v) && own(v, '$var');
export const usesFlow = script => script.some(s => obj(s) && (FLOW_ACTIONS.has(s.action) || own(s, 'when') || own(s, 'phase') || JSON.stringify(s).includes('"$var"') || s.target?.attribute));

// Only literal data positions accept references. Never interpolate selector/JS syntax.
function walk(value, defined, path = []) {
  if (isRef(value)) {
    need(Object.keys(value).length === 1 && name(value.$var) && defined.has(value.$var), `变量未定义或引用无效：${value.$var}`);
    const key = path.at(-1);
    need(['text','expected','checked','values','has_text','value'].includes(key) || path.includes('values'), `此字段不支持变量：${path.join('.')}`);
  } else if (Array.isArray(value)) value.forEach((v, i) => walk(v, defined, [...path, String(i)]));
  else if (obj(value)) for (const [k, v] of Object.entries(value)) {
    need(!['__proto__','constructor','prototype'].includes(k), '禁止的对象字段');
    walk(v, defined, [...path, k]);
  }
}

export function validateFlow(script) {
  if (!usesFlow(script)) return false;
  need(script[0]?.action === 'flow_start' && script[0].args?.version === 1 && !script[0].when && !script[0].phase, '条件脚本必须以 flow_start(version=1) 开始');
  const defined = new Set(); let cleanup = false;
  for (const [i, s] of script.entries()) {
    need(obj(s) && typeof s.action === 'string', '步骤需要字符串 action');
    const a = s.args || {};
    need(obj(a) && (s.target == null || obj(s.target)), 'args/target 必须是对象');
    need(!own(s,'phase') || ['setup','test','cleanup'].includes(s.phase), 'phase 只能是 setup/test/cleanup');
    if (s.phase === 'cleanup') cleanup = true;
    else need(!cleanup, 'cleanup 必须位于脚本末尾');
    if (own(s,'when')) {
      const w = s.when;
      need(obj(w) && name(w.var) && defined.has(w.var), 'when 必须引用之前声明的变量');
      need(Object.keys(w).length === 2 && (own(w,'equals') && scalar(w.equals) || w.is_set === true), 'when 使用 {var, equals} 或 {var, is_set:true}');
    }
    walk(s.target, defined, ['target']); walk(a, defined, ['args']);
    if (s.target && Object.keys(s.target).length) validateFlowTarget(s.target);
    if (['probe','get_attribute'].includes(s.action)) validateFlowTarget(s.target);
    if (s.action === 'flow_start') need(i === 0 && Object.keys(a).length === 1, 'flow_start 仅允许 version=1');
    if (s.action === 'set_var') {
      need(name(a.name) && (own(a,'value') !== own(a,'generate')), 'set_var 需要 name 和 value 或 generate');
      need(own(a,'value') ? scalar(a.value) || isRef(a.value) : a.generate === 'unique' && (a.prefix === undefined || typeof a.prefix === 'string' && a.prefix.length <= 80), 'set_var 仅支持标量或 unique');
      defined.add(a.name);
    }
    if (s.action === 'probe') {
      need(obj(a.ready) && (a.ready.selector || a.ready.key), 'probe 必须提供已加载完成的 args.ready 定位');
      validateFlowTarget(a.ready);
      need(Number.isInteger(a.timeout_ms) && a.timeout_ms >= 0 && a.timeout_ms <= 30000, 'probe timeout_ms 需要0–30000');
      need(Number.isInteger(a.settle_ms) && a.settle_ms >= 0 && a.settle_ms <= a.timeout_ms, 'probe settle_ms 需要0–timeout_ms');
    }
    if (s.action === 'get_attribute') need(typeof a.attribute_name === 'string' && /^(data-[\w-]+|aria-[\w-]+|id|title|href|src|value)$/.test(a.attribute_name), 'get_attribute 属性不在允许范围');
    if (own(a, 'expected_from')) need(s.action === 'assert_text' && defined.has(a.expected_from) && !own(a,'expected'), 'expected_from 必须引用之前捕获的变量');
    if (own(a, 'save_as')) {
      need(['get_text','get_attribute','probe'].includes(s.action) && name(a.save_as), 'save_as 仅支持文本、属性和探测步骤');
      defined.add(a.save_as);
    } else need(!['get_attribute','probe'].includes(s.action), `${s.action} 缺少 save_as`);
    if (s.target?.attribute) validateAttribute(s.target.attribute);
    if (cleanup) need(!['flow_start','probe','set_var','mock_route','fault_route','watch_network'].includes(s.action), 'cleanup 仅恢复本条用例状态，不创建新的分支或拦截');
  }
  need(script.some(s => s.phase !== 'cleanup' && s.action.startsWith('assert_')), '缺少业务断言');
  return true;
}
export function validateAttribute(a) {
  need(obj(a) && typeof a.name === 'string' && /^(data-[\w-]+|aria-[\w-]+|id|title|href|src|value)$/.test(a.name) && (typeof a.value === 'string' || isRef(a.value)), 'attribute 需要受支持的 name 和字符串 value');
}
function validateFlowTarget(t, depth = 0) {
  need(obj(t) && depth <= 3 && ['key','selector'].some(k=>typeof t[k] === 'string' && t[k].trim()), '目标需要真实 key/selector，within 最多三层');
  for (const k of ['key','selector','frame']) if (own(t,k)) need(typeof t[k] === 'string' && t[k].trim(), `${k} 必须为非空字符串`);
  if (own(t,'has_text')) need(typeof t.has_text === 'string' || isRef(t.has_text), 'has_text 类型错误');
  if (own(t,'has_text_exact')) need(typeof t.has_text_exact === 'boolean' && (isRef(t.has_text) || typeof t.has_text === 'string' && t.has_text.trim()), 'has_text_exact 需要非空文本');
  if (own(t,'nth')) need(Number.isInteger(t.nth) && t.nth >= 0, 'nth 必须是非负整数');
  if (own(t,'visible')) need(typeof t.visible === 'boolean', 'visible 必须是布尔值');
  if (own(t,'attribute')) validateAttribute(t.attribute);
  if (t.within != null) validateFlowTarget(t.within, depth + 1);
}

export function createFlow(script) {
  validateFlow(script);
  const values = new Map();
  function read(k) { need(values.has(k), `本次分支尚未赋值：${k}`); return values.get(k); }
  function resolve(v) {
    if (isRef(v)) return read(v.$var);
    if (Array.isArray(v)) return v.map(resolve);
    if (obj(v)) return Object.fromEntries(Object.entries(v).map(([k,x]) => [k,resolve(x)]));
    return v;
  }
  return {
    values, resolve,
    enabled(s) { return !s.when || (s.when.is_set ? values.has(s.when.var) : read(s.when.var) === s.when.equals); },
    argsFor(a = {}) {
      const r = resolve(a);
      if (own(a,'expected_from')) r.expected = read(a.expected_from);
      delete r.expected_from; delete r.save_as;
      return r;
    },
    record(a, r) { if (a.save_as) { const v = own(r,'text') ? r.text.trim() : r.value; need(scalar(v), '捕获值必须为标量'); values.set(a.save_as,v); } },
    assign(a, generated) { const v = a.generate ? generated : resolve(a.value); need(scalar(v), '变量必须为标量'); values.set(a.name,v); return v; },
  };
}

// Replay observation decisions without executing the UI. Skips are evidence, never passes.
export function validateFlowReport(c, refs) {
  const flow = createFlow(c.script);
  need(c.report.length === c.script.length, '条件脚本报告须包含分支跳过和收尾记录');
  let failed = false, checks = 0;
  for (const [i, s] of c.script.entries()) {
    const r = c.report[i], a = s.args || {}, cleanup = s.phase === 'cleanup';
    need(r.no === i + 1 && r.action === s.action && typeof r.observation === 'string' && r.observation.trim() && refs.has(r.evidence_ref), '步骤须对应脚本并保留真实证据');
    const skip = failed && !cleanup ? 'previous_failure' : !flow.enabled(s) ? 'condition_false' : null;
    if (skip) { need(r.ok === true && r.skipped === true && r.skip_reason === skip && !r.check && !own(r,'value') && !own(r,'text'), '未执行分支须明确标记，不能伪造断言/捕获值'); continue; }
    flow.resolve(s.target);
    if (r.skipped) { need(s.action === 'click' && a.if_visible === true && r.ok === true && !r.check, '不能跳过必要业务步骤'); flow.argsFor(a); continue; }
    const args = flow.argsFor(a);
    if (s.action === 'set_var') {
      if (a.generate) need(typeof r.value === 'string' && r.value.startsWith(a.prefix || 'qa-') && /^[0-9a-f-]{36}$/i.test(r.value.slice((a.prefix || 'qa-').length)), 'unique 须记录本次实际唯一值');
      else need(r.value === flow.resolve(a.value), 'set_var 记录与脚本不一致');
      flow.assign(a, r.value);
    } else if (['get_text','get_attribute','probe'].includes(s.action)) {
      need(s.action === 'get_text' ? typeof r.text === 'string' : s.action === 'probe' ? typeof r.value === 'boolean' : typeof r.value === 'string', '缺少实际捕获值');
      flow.record(a,r);
    }
    if (!s.action.startsWith('assert')) { need(r.ok === true, '执行阻塞不能作为业务失败导入'); continue; }
    if (!cleanup) checks++;
    const k = r.check;
    need(k && own(k,'actual') && own(k,'expected') && typeof k.pass === 'boolean' && r.ok === k.pass, '断言缺 actual/expected/pass');
    let matched;
    if (s.action === 'assert_text') {
      need(typeof k.actual === 'string' && typeof k.expected === 'string' && k.expected === args.expected, '文本断言预期与脚本/捕获不一致');
      matched = args.contains ? k.actual.includes(k.expected) : k.actual === k.expected;
      if (args.negate) matched = !matched;
    } else if (['assert_visible','assert_absent'].includes(s.action)) {
      need(typeof k.actual === 'boolean' && k.expected === (s.action === 'assert_visible'), '可见性预期无效'); matched = k.actual === k.expected;
    } else if (['assert_network_count','assert_fault_hits'].includes(s.action)) {
      need(Number.isInteger(k.actual) && k.actual >= 0 && k.expected === args.expected, '网络计数无效'); matched = k.actual === k.expected;
    } else { matched = JSON.stringify(k.actual) === JSON.stringify(k.expected); }
    need(matched === k.pass, '断言结论与实际值矛盾');
    if (!matched) {
      need(!cleanup && !failed && r.fail_kind === 'business' && typeof r.error === 'string' && r.error.trim(), '收尾失败或执行阻塞不能冒充业务失败');
      failed = true;
    }
  }
  need(checks > 0 && (c.verdict === 'fail') === failed, '没有执行业务断言或用例结论与报告不一致');
}
