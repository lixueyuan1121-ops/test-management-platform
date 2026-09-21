export const DISCOVER_SCRIPT = function ({ relax = false } = {}) {
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const isBEM = (cls) => /^[a-z][a-z0-9]*(?:[-_]{1,2}[a-z0-9]+)+$/i.test(cls);
  const isHash = (cls) => /[A-Za-z0-9]{6,}$/.test(cls) && !/[-_]/.test(cls.slice(-8));
  const controlSelector = 'button, a[href], input, textarea, select, [role=button], [role=tab], [role=menuitem], [contenteditable=true], [onclick]';
  const hasControls = el => !!el.querySelector(controlSelector);
  const accessibleName = el => {
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean).map(id => el.getRootNode().getElementById?.(id)?.textContent || '').join(' ').trim();
    return labelled || el.getAttribute('aria-label') || (el.labels?.length ? [...el.labels].map(label => label.textContent).join(' ').trim() : '') || (hasControls(el) ? '' : (el.innerText || '').trim()) || el.getAttribute('title') || '';
  };
  // 提示组件的文案用于展示名称，不冒充 DOM 正文或无障碍名称生成 text/role 定位。
  const tooltipText = el => {
    const parent = el.parentElement;
    const text = parent?.getAttribute('trigger') === 'hover' ? (parent.getAttribute('content') || '').trim() : '';
    return text.length <= 80 ? text : '';
  };
  // 生成**在本文档内唯一**的 XPath:从元素向上逐级构造。遇到带稳定 id 的祖先即锚定停止,
  // 否则用「标签 + 同标签兄弟中的序号」定位。序号保证唯一性(即便类名/文本重复),锚定 id 让路径尽量短稳。
  // 用途:CSS 类多命中、文本也不唯一时(如输入框内的模型选择/语音按钮),给一条一定唯一的兜底 XPath。
  // 注:XPath 不能跨 shadow 边界——元素在 shadowRoot 内则返回空,交由候选/框选兜底。
  const uniqueXPath = (el) => {
    if (!el || el.nodeType !== 1) return "";
    // 在 shadowRoot 内则放弃(XPath 无法从 document 定位到 shadow 内部)。ShadowRoot: nodeType 11 且有 host。
    for (let n = el; n; n = n.parentNode) {
      if (n.nodeType === 11 && n.host) return "";
      if (n === document.documentElement) break;
      if (!n.parentNode && n !== document) return "";   // 已脱离文档
    }
    const stableId = (node) => {
      const id = node.getAttribute && node.getAttribute("id");
      return id && !/^\d/.test(id) && id.length < 50 && document.querySelectorAll(`[id=${JSON.stringify(id)}]`).length === 1 ? id : "";
    };
    const segs = [];
    for (let node = el; node && node.nodeType === 1 && node !== document; node = node.parentElement) {
      const id = stableId(node);
      if (id) { segs.unshift(`*[@id=${JSON.stringify(id)}]`); return "//" + segs.join("/"); }
      const tag = node.tagName.toLowerCase();
      const sibs = node.parentElement ? Array.from(node.parentElement.children).filter((c) => c.tagName === node.tagName) : [node];
      const idx = sibs.length > 1 ? `[${sibs.indexOf(node) + 1}]` : "";
      segs.unshift(tag + idx);
    }
    return "/" + segs.join("/");
  };
  const genCandidates = (el) => {
    const cands = [];
    // 测试专用锚点(最稳,开发提测约定):兼容三种属性名 data-testid/data-test-id/data-test。
    // 用命中的**实际属性名**生成 CSS 属性选择器(by=css 走 locator();不用 by=testid/getByTestId——
    // 那个只认 data-testid,开发用 data-test-id 会定位不到)。score 100=最高优先。
    for (const attr of ["data-testid", "data-test-id", "data-test"]) {
      const v = el.getAttribute(attr);
      if (v) { const s = `[${attr}=${JSON.stringify(v)}]`; cands.push({ sel: s, score: 100, by: attr === "data-testid" ? "testid" : "css", value: attr === "data-testid" ? v : s }); }
    }
    if (el.id && !/^\d/.test(el.id) && el.id.length < 50) cands.push({ sel: `#${CSS.escape(el.id)}`, score: 90, by: "css", value: `#${CSS.escape(el.id)}` });
    const aria = el.getAttribute("aria-label");
    if (aria && aria.length < 60) cands.push({ sel: `[aria-label=${JSON.stringify(aria)}]`, score: 80, by: "label", value: aria, exact: true });
    const name = el.getAttribute("name");
    if (name) cands.push({ sel: `[name=${JSON.stringify(name)}]`, score: 75, by: "css", value: `[name=${JSON.stringify(name)}]` });
    const ph = el.getAttribute("placeholder");
    if (ph) cands.push({ sel: `[placeholder=${JSON.stringify(ph)}]`, score: 70, by: "placeholder", value: ph, exact: true });
    const role = el.getAttribute("role") || ({ BUTTON: 'button', A: 'link', INPUT: ['button','submit','reset'].includes(el.type) ? 'button' : el.type === 'checkbox' ? 'checkbox' : el.type === 'radio' ? 'radio' : 'textbox', TEXTAREA: 'textbox', SELECT: 'combobox' })[el.tagName];
    const nameForRole = accessibleName(el);
    if (role && nameForRole && !hasControls(el)) cands.push({ sel: `role=${role}`, score: 95, by: 'role', value: role, name: nameForRole, exact: true });
    const classes = Array.from(el.classList).filter(c => !/(?:--|^is-|^has-).*(?:visible|active|hover|focus|selected|disabled|loading|checked|expanded)/i.test(c));
    const bem = classes.filter(isBEM);
    const stable = bem.length ? bem : classes.filter((c) => !isHash(c) && c.length > 3);
    if (stable.length) { const sel = stable.map((c) => `.${CSS.escape(c)}`).join(""); cands.push({ sel, score: bem.length ? 60 : 45, by: "css", value: sel }); }
    const txt = hasControls(el) ? "" : (el.innerText || el.textContent || "").trim();
    if (txt && txt.length >= 2 && txt.length <= 20) cands.push({ sel: `text=${txt}`, score: 65, by: "text", value: txt, exact: true });
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute("type");
    if (type) cands.push({ sel: `${tag}[type="${type}"]`, score: 30, by: "css", value: `${tag}[type="${type}"]` });
    return cands.sort((a, b) => b.score - a.score);
  };
  // 递归收集所有元素,穿透 open shadowRoot(与 probe-collect.mjs 的 collectDeep 同款,evaluate 内不能 import 故内联)。
  const collectDeep = (root) => {
    const acc = [];
    for (const el of root.querySelectorAll("*")) {
      acc.push(el);
      if (el.shadowRoot) { for (const s of collectDeep(el.shadowRoot)) acc.push(s); }
    }
    return acc;
  };
  const all = collectDeep(document);
  let elements;
  if (relax) {
    // 框选放宽:框内全量——不套白名单、不做父级去重、不判 cursor;只留可见(有候选在末尾筛)。
    elements = all;
  } else {
    // 全页扫描:白名单选择器 + cursor:pointer 补充,再按父级同文本去重(原逻辑,但采集根已穿 shadow)。
    // 白名单:可交互元素 + 展示文本类(section-title/分组标题/标签/heading——纯展示 div 不可交互,
    // 但常是断言目标,如"最近任务"分组标题。加它们让 probe/自愈能采到、能断言/定位。
    // 父级同文本去重 + isVisible + 有候选 三重兜底,故加宽白名单不会灌垃圾。
    const sel = "a, button, [role=button], [role=tab], [role=menuitem], input, textarea, select, [contenteditable=true], [onclick], [class*=btn], [class*=action], [class*=nav__item], [class*=menu-item], [class*=section-title], [class*=__title], [class*=__label], [class*=__header], [role=heading], h1, h2, h3";
    const set = new Set();
    for (const el of all) {
      try { if (el.matches(sel) || getComputedStyle(el).cursor === "pointer") set.add(el); } catch { /* 忽略 */ }
    }
    elements = [...set].filter((el) => {
      if (el.matches(controlSelector)) return true; // 独立按钮不可被同文本或空文本的父容器去掉。
      const t = (el.innerText || "").trim();
      for (let p = el.parentElement; p; p = p.parentElement) {
        if (t && set.has(p) && !hasControls(p) && (p.innerText || "").trim() === t) return false;
      }
      return true;
    });
  }
  const out = [];
  const epoch = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
  const refs = new Map();
  window.__qalabProbeElements = refs; // 仅本次探测有效；下一次探测/导航后旧引用失效。
  for (const el of elements) {
    if (!isVisible(el)) continue;
    const candidates = genCandidates(el);
    if (!candidates.length) continue;
    const r = el.getBoundingClientRect();
    const element_ref = epoch + ':' + out.length;
    refs.set(element_ref, el);
    out.push({ element_ref, accessibleName: accessibleName(el), tooltipText: tooltipText(el), tag: el.tagName.toLowerCase(), type: el.getAttribute("type") || "", text: (el.innerText || el.value || "").trim().slice(0, 40), rect: { x: r.left, y: r.top, w: r.width, h: r.height }, candidates: candidates.slice(0, 6), best: candidates[0], uniqueXPath: uniqueXPath(el) });
  }
  return out;
};
