import { candidateIdentity, isActiveCandidate } from './selector-ranking.js'

export function distinctive(c) {
  if (!isActiveCandidate(c)) return false
  if (c.by === 'css') return /[#.]|\[data-|\[aria-/.test(c.value) && !/^(?:button|input|div|span)\[type=/.test(c.value)
  return c.by !== 'role' || !!c.name
}

// testid 候选(含 css 形式的 [data-testid=x]):testid 全应用唯一,匹配无视 frame。
function isTestid(c) {
  return c && (c.by === 'testid' || (c.by === 'css' && /\[data-testid[=~|]?=?/.test(c.value || '')))
}

// frame 归一化:vm/shell/content/auto 都是"主内容域"(桌面版同一元素在 vm,云端在 shell,注册常写 vm/auto),
// 视为等价 'main';深层 iframe(url:... / iframe...)是真正隔离域,保持原值。别名表优先。
function normFrame(x, aliases = {}) {
  const raw = aliases[x || 'auto'] || x || 'auto'
  if (raw === 'content') return 'main'
  if (raw === 'vm' || raw === 'shell' || raw === 'auto') return 'main'
  return raw
}

// 两个 frame 值是否属于同一作用域:vm/shell/auto/content 归一后皆为主内容域(桌面版元素在 vm、
// 云端同元素在 shell、注册常写 auto,本是同一个),深层 iframe(url:.../iframe)按具体值区分。
// 「加为 key-更新已有」的 frame 校验用它,避免 auto vs vm 这类等价值被当成"frame 不同"误拦。
export function sameFrameDomain(a, b, aliases = {}) {
  return normFrame(a, aliases) === normFrame(b, aliases)
}

// css value 拆成"稳定类集合"(.a.b → {a,b}),供子集匹配:注册单类 .a ⊆ 探测拼接 .a.b。
function cssClasses(value) {
  const m = String(value || '').match(/\.[A-Za-z0-9_-]+/g)
  return m ? m.map(s => s.slice(1)) : null
}

// 两个候选是否指代同一定位(比对专用):
// - 剥 exact(探测带 exact:true、注册常无);
// - css 纯类选择器走"注册类 ⊆ 探测类"子集匹配(容忍类拼接);其余按指纹相等。
function sameTarget(regC, probeC) {
  const strip = c => { const { exact, ...rest } = c; return rest }
  const r = strip(regC), p = strip(probeC)
  if (r.by === 'css' && p.by === 'css') {
    const rc = cssClasses(r.value), pc = cssClasses(p.value)
    if (rc && pc && rc.length) return rc.every(x => pc.includes(x))   // 注册类全部出现在探测类里
  }
  return candidateIdentity(r) === candidateIdentity(p)
}

export function elementStatus(el, rows, elements, aliases = {}) {
  const elCands = (el.candidates || []).filter(distinctive)
  const elTestids = elCands.filter(isTestid)
  const elFrame = normFrame(el._frameMatch, aliases)
  // 元素的「位置唯一 xpath」(探测端 uniqueXPath,或候选里的 xpath):无 testid 又与兄弟共享 class 的元素
  // (如输入框里语音/发送图标同 .chat-input-button__icon)靠 css 无法区分身份,只有位置 xpath 能稳定辨识。
  const elXPaths = [(el.uniqueXPath || '').trim(), ...elCands.filter(c => c.by === 'xpath').map(c => c.value)].filter(Boolean)

  // 唯一性:仅对"非 testid 候选"施加(testid 全局唯一,天然无需;列表类共享类才需防泛化)。
  // 判定"全页是否只有这一个探测元素带该 css 指纹"——用归一 frame 比较。
  const nonTestid = elCands.filter(c => !isTestid(c))
  const uniqueNon = nonTestid.filter(c => elements.filter(e =>
    normFrame(e._frameMatch, aliases) === elFrame
    && (e.candidates || []).some(x => distinctive(x) && !isTestid(x) && sameTarget(c, x))
  ).length === 1)

  const hits = rows.filter(row => {
    const regCands = (row.candidates || []).filter(distinctive)
    // 1) testid 命中:无视 frame,只要有一个 testid 候选与探测的 testid 指纹相同。
    if (regCands.some(rc => isTestid(rc) && elTestids.some(pc => sameTarget(rc, pc)))) return true
    // 1.5) xpath 精确命中:元素位置唯一 xpath 与注册 key 的 xpath 候选完全相等 → 同一元素(位置唯一,无视 frame)。
    //      这是"无 testid + 共享 class"元素判「已存在」的唯一可靠依据,防其一直显示未添加、被反复重复添加。
    if (elXPaths.length && regCands.some(rc => rc.by === 'xpath' && elXPaths.includes(rc.value))) return true
    // 2) 非 testid 命中:frame 归一后同域,且注册候选匹配到探测的"唯一"非 testid 候选。
    if (normFrame(row.frame, aliases) !== elFrame) return false
    return regCands.some(rc => !isTestid(rc) && uniqueNon.some(pc => sameTarget(rc, pc)))
  })
  const keys = [...new Set(hits.map(r => r.key))]
  // 命中 >=1 个已注册 key 即视为「已存在」(该元素已能被定位,不该再新建)。
  // 关键:不能只在恰好命中 1 个时算 exists——同一 testid 若被登记成多个重复 key(命中 2+),
  // 旧逻辑 keys.length===1 会判成 new,于是一直显示"未添加"、可反复重复添加、越加越多。
  // 命中多个时一并返回,便于上层提示去重(取第一个作代表 key)。
  return keys.length ? { type: 'exists', key: keys[0], keys } : { type: 'new' }
}
const hash = value => { let n = 2166136261; for (const c of value) n = Math.imul(n ^ c.charCodeAt(0), 16777619); return (n >>> 0).toString(36) }
const words = {'复制':'copy','编辑':'edit','项目':'project','任务':'task','首页':'home','新建':'create','保存':'save','取消':'cancel','删除':'delete','搜索':'search','发送':'send','展开':'expand','收起':'collapse'}
export function suggestKey(el, page, reserved) {
  const name = el.accessibleName || el.tooltipText || el.text || ''
  const semantic = Object.entries(words).filter(([word])=>name.includes(word)).map(([,word])=>word).join('_')
  const candidate = (el.candidates || []).find(c=>c.by === 'testid') || (el.candidates || []).find(c=>c.by === 'css') || el.best
  const hint = semantic || String(candidate?.value || name).replace(/[^a-zA-Z0-9]+/g,'_').replace(/^_+|_+$/g,'').slice(0,32) || 'element'
  const signature = JSON.stringify([page,el._frameMatch,candidate,el.tag,name])
  const stem = `${/^[a-zA-Z]/.test(hint) ? hint : 'element_'+hint}_${el.tag || 'element'}_${hash(signature)}`.slice(0,56)
  let key=stem, n=2; while(reserved.has(key)) key=`${stem}_${n++}`
  reserved.add(key); return key
}
