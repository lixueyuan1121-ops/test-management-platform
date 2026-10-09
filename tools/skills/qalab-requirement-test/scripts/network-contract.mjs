export const NETWORK_ACTIONS = new Set(['watch_network', 'fault_route', 'release_fault', 'wait_network', 'assert_network_count', 'assert_fault_hits', 'assert_list_from_response']);
const idOk = v => typeof v === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(v);
const pathOk = v => typeof v === 'string' && /^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$/.test(v) && !v.split('.').some(x => ['__proto__', 'prototype', 'constructor'].includes(x));
export function validateNetworkScript(script) {
  const watches = new Map(), faults = new Map();
  for (const step of script) {
    const { action, args: a = {} } = step;
    if (!NETWORK_ACTIONS.has(action)) continue;
    if (!idOk(a.id)) throw new Error(`${action}: args.id 无效`);
    for (const key of ['timeout_ms', 'settle_ms']) if (a[key] !== undefined && (!Number.isInteger(a[key]) || a[key] < 0 || a[key] > 30000)) throw new Error(`${key} 必须为 0–30000 的整数`);
    if (['watch_network', 'fault_route'].includes(action)) {
      if (typeof a.path !== 'string' || !/^\/(?!\/)[^?#*\s]+$/.test(a.path)) throw new Error('args.path 必须是精确接口路径，不含域名、通配符或查询串');
      if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(a.method)) throw new Error('args.method 必须显式指定 HTTP 方法');
      if (a.frame !== 'shell' && !(typeof a.frame === 'string' && a.frame.startsWith('url:') && a.frame.length > 4)) throw new Error('args.frame 必须为 shell 或唯一 url: frame');
      if (a.query !== undefined && (!a.query || Array.isArray(a.query) || typeof a.query !== 'object' || Object.values(a.query).some(v => typeof v !== 'string'))) throw new Error('query 必须为字符串字典');
    }
    if (action === 'watch_network') {
      if (watches.has(a.id)) throw new Error('watch id 重复');
      if ((a.response_path !== undefined || a.item_field !== undefined) && (!pathOk(a.response_path) || !pathOk(a.item_field))) throw new Error('列表捕获需 response_path 和 item_field');
      watches.set(a.id, a);
    } else if (action === 'fault_route') {
      if (faults.has(a.id)) throw new Error('fault id 重复');
      if (!['response', 'network_error', 'hold_response'].includes(a.mode)) throw new Error('fault mode 无效');
      if (a.mode === 'response' && (!Number.isInteger(a.status) || a.status < 200 || a.status > 599 || [204, 205, 304].includes(a.status) || a.body === undefined)) throw new Error('response 需要有效 status 和 JSON body');
      if (a.mode === 'hold_response' && (!Number.isInteger(a.timeout_ms) || a.timeout_ms < 100 || a.timeout_ms > 30000)) throw new Error('hold_response 需要 100–30000ms timeout_ms');
      faults.set(a.id, true);
    } else if (['release_fault', 'assert_fault_hits'].includes(action)) {
      if (!faults.has(a.id)) throw new Error('fault id 尚未注册');
      if (action === 'release_fault') {
        if (!faults.get(a.id)) throw new Error('fault 已释放');
        faults.set(a.id, false);
      }
    } else if (!watches.has(a.id)) throw new Error('watch id 尚未注册');
    if (['assert_fault_hits', 'assert_network_count'].includes(action) && (!Number.isInteger(a.expected) || a.expected < 0)) throw new Error('expected 必须为非负整数');
    if (action === 'wait_network' && (!['requested', 'received', 'completed'].includes(a.phase) || !Number.isInteger(a.count) || a.count < 1)) throw new Error('wait_network 需要 phase 和正整数 count');
    if (action === 'assert_list_from_response' && !watches.get(a.id)?.response_path) throw new Error('watch 未配置列表字段');
    if (action === 'assert_list_from_response' && a.allow_empty !== undefined && typeof a.allow_empty !== 'boolean') throw new Error('allow_empty 必须为布尔值');
  }
}
