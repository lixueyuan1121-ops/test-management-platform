// 录制捕获:注入被测页的事件监听脚本 + runner 侧的原始事件→步骤 纯映射(便于单测)。
// 设计:捕获脚本把每次 click/change 的目标元素候选(内联 genCandidates,与 DISCOVER_SCRIPT 同款)
// 页面缓冲和 exposeBinding 双通道保留事件，Runner 持久化后上传，服务端确认才清理。
// Alt+点击 = 标断言。stop 时置 window.__qalabRecOn=false,捕获脚本空转(addInitScript 撤不掉,用开关关)。

// ---- 注入进页面的捕获脚本(必须自包含:addInitScript 会序列化函数源,不能引用模块作用域) ----
export function CAPTURE_INIT({ sessionId = "local" } = {}) {
  try {
    // 重复注入同一会话不清空未确认事件。
    if (window.__qalabRecSession !== sessionId) {
      window.__qalabRecReset?.();
      window.__qalabRec = [];
      window.__qalabRecSession = sessionId;
      window.__qalabRecDocument = (globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2));
      window.__qalabRecSeq = 0;
    }
    if (window.__qalabRecHooked) { window.__qalabRecOn = true; return; }
    window.__qalabRecHooked = true;
    window.__qalabRecOn = true;
    window.__qalabRec = window.__qalabRec || [];
    var isBEM = function (c) { return /^[a-z][a-z0-9]*(?:[-_]{1,2}[a-z0-9]+)+$/i.test(c); };
    var isHash = function (c) { return /[A-Za-z0-9]{6,}$/.test(c) && !/[-_]/.test(c.slice(-8)); };
    var gen = function (el) {
      var cands = [];
      var tid = "";
      for (var attr of ["data-testid", "data-test-id", "data-test"]) {
        var value = el.getAttribute(attr);
        if (!value) continue;
        tid = value;
        cands.push(attr === "data-testid" ? { by: "testid", value: value }
          : { by: "css", value: "[" + attr + "=" + JSON.stringify(value) + "]" });
      }
      if (el.id && !/^\d/.test(el.id) && el.id.length < 50) cands.push({ by: "css", value: "#" + CSS.escape(el.id) });
      var role = el.getAttribute("role") || ({ BUTTON: "button", A: "link", INPUT: ["button", "submit", "reset"].includes(el.type) ? "button" : el.type === "checkbox" ? "checkbox" : el.type === "radio" ? "radio" : "textbox", TEXTAREA: "textbox", SELECT: "combobox" })[el.tagName];
      var labelled = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean).map(function (id) { return document.getElementById(id)?.textContent || ""; }).join(" ").trim();
      var accessibleName = labelled || el.getAttribute("aria-label") || (el.labels?.length ? Array.from(el.labels).map(function(l) { return l.textContent; }).join(" ").trim() : "") || ((el.innerText || "").trim());
      if (role && accessibleName) cands.push({ by: "role", value: role, name: accessibleName, exact: true });
      var aria = el.getAttribute("aria-label");
      if (aria && aria.length < 60) cands.push({ by: "label", value: aria, exact: true });
      var name = el.getAttribute("name");
      if (name) cands.push({ by: "css", value: '[name=' + JSON.stringify(name) + ']' });
      var ph = el.getAttribute("placeholder");
      if (ph) cands.push({ by: "placeholder", value: ph, exact: true });
      var classes = Array.prototype.slice.call(el.classList || []);
      var bem = classes.filter(isBEM);
      var stable = bem.length ? bem : classes.filter(function (c) { return !isHash(c) && c.length > 3; });
      var stableSel = stable.length ? stable.map(function (c) { return "." + CSS.escape(c); }).join("") : "";
      if (stableSel) cands.push({ by: "css", value: stableSel });
      var txt = (el.innerText || el.textContent || "").trim().slice(0, 30);
      // 无 testid 时:补一条 **xpath**(tag + class contains + 精确文本)——比裸 css 类(常多命中)和
      // 脆弱 text(子串多命中)更能精确定位。修"技能 tab 无 testid、退到 text=技能 跑脚本定位不到"。
      // xpath 1.0 无转义:文本含单引号则用双引号包;都含则跳过文本条件(仅 class 兜底)。
      if (!tid && txt && txt.length >= 1 && txt.length <= 20) {
        var tag = el.tagName ? el.tagName.toLowerCase() : "*";
        var conds = [];
        for (var i = 0; i < stable.length; i++) conds.push("contains(@class,'" + stable[i] + "')");
        var tcond = "";
        if (txt.indexOf("'") < 0) tcond = "normalize-space(.)='" + txt + "'";
        else if (txt.indexOf('"') < 0) tcond = 'normalize-space(.)="' + txt + '"';
        if (tcond) conds.push(tcond);
        if (conds.length) cands.push({ by: "xpath", value: "//" + tag + "[" + conds.join("][") + "]" });
      }
      if (txt && txt.length >= 2 && txt.length <= 20) cands.push({ by: "text", value: txt, exact: true });
      return cands;
    };
    // 从事件目标向上找"有意义"的可定位元素(内层 svg/span/文本 div → 最近的可点击容器)。
    // 两轮:①先找语义强的(button/a/[role]/带 testid);找不到再②找常见可点击容器 class
    // (nav__item / tab / menu-item / list row 等)——修"点『技能』导航,pick 停在内层文本 div、
    // 拿到多命中的 .sidebar-nav__text,回放点错/点不上"。真正可点击的是外层 li.sidebar-nav__item。
    var pick = function (el) {
      var strong = "a,button,[role=button],[role=tab],[role=menuitem],input,textarea,select,[contenteditable=true],[data-testid],[data-test-id],[data-test]";
      for (var n = el, i = 0; n && i < 6; n = n.parentElement, i++) {
        try { if (n.matches && n.matches(strong)) return n; } catch (e) { /* ignore */ }
      }
      // 无 testid/role 的自定义导航:向上找"可点击项"容器(li 或 class 含 item/row/tab/option/card 等),
      // 跳过纯展示叶子(__text/__label/__icon/__title)——否则会停在多命中的 .sidebar-nav__text。
      var containerRe = /(^|[-_])(item|row|cell|option|card|entry|menuitem|listitem)([-_]|$)|__item\b|__tab\b|[-_]tab([-_]|$)|\bmenu-item\b/i;
      var leafRe = /__(text|label|icon|title|name)\b|[-_](text|label|icon)([-_]|$)/i;
      for (var m = el, j = 0; m && j < 6; m = m.parentElement, j++) {
        try {
          var cls = (typeof m.className === "string" ? m.className : "") || "";
          if (leafRe.test(cls)) continue;   // 展示叶子:继续向上
          if ((m.tagName === "LI" || containerRe.test(cls))
              && (m.innerText || "").trim().length <= 24) return m;
        } catch (e) { /* ignore */ }
      }
      return el;
    };
    var pendingBindings = new Set();
    var emit = function (type, ev) {
      if (!window.__qalabRecOn) return;
      var el = pick(ev.composedPath?.()[0] || ev.target);
      if (!el || !el.tagName) return;
      var cands = gen(el);
      if (!cands.length) return;
      window.__qalabRec.push({
        type: type, tag: el.tagName.toLowerCase(), elType: el.getAttribute("type") || "",
        text: (el.innerText || el.value || "").trim().slice(0, 40), value: el.isContentEditable ? el.textContent : (el.value || ""),
        checked: !!el.checked, values: el.tagName === 'SELECT' ? Array.from(el.selectedOptions).map(o => o.value) : undefined,
        key_name: ev.key, document_url: globalThis.location?.href || '',
        altKey: !!ev.altKey, candidates: cands, ts: performance.timeOrigin + performance.now(),
        event_id: window.__qalabRecSession + ":" + window.__qalabRecDocument + ":" + (++window.__qalabRecSeq),
      });
      // 尽早送进 Runner 的待确认区；页面导航销毁时仍能保留已捕获事件。
      if (window.__qalabRecorderEvent) {
        var pending = window.__qalabRecorderEvent(window.__qalabRec.at(-1)).catch(function() {});
        pendingBindings.add(pending);
        pending.finally(function() { pendingBindings.delete(pending); });
      }
    };
    // 断言选择在 capture 阶段阻断点击及提前绑定的 pointer/mouse 业务处理。
    var blockPick = function(e) {
      if (!window.__qalabRecOn || !e.altKey) return;
      if (e.type === "click") { flushInputs(); emit("click", e); }
      e.preventDefault(); e.stopImmediatePropagation();
    };
    ["pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick"].forEach(function(type) {
      window.addEventListener(type, blockPick, true);
    });
    var dirtyInputs = new Map();
    window.__qalabRecReset = function() { dirtyInputs.clear(); };
    var targetOf = function(e) { return e.composedPath?.()[0] || e.target; };
    var isTextInput = function(el) { return el && (el.isContentEditable || el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && !['checkbox','radio','file','button','submit','reset','range','color','hidden'].includes(el.type))); };
    var flushInputs = function() {
      for (var [el] of dirtyInputs) emit('change', { target: el });
      dirtyInputs.clear();
    };
    document.addEventListener('input', function(e) {
      if (window.__qalabRecOn && isTextInput(targetOf(e))) dirtyInputs.set(targetOf(e), true);
    }, true);
    document.addEventListener("click", function (e) {
      if (!window.__qalabRecOn || e.altKey) return;
      flushInputs();
      var el = targetOf(e);
      if (el?.tagName === 'INPUT' && ['checkbox', 'radio'].includes(el.type)) return; // change 记录最终状态。
      emit("click", e);
    }, true);
    document.addEventListener("change", function (e) {
      dirtyInputs.delete(targetOf(e));
      emit("change", e);
    }, true);
    document.addEventListener('keydown', function(e) {
      if (!window.__qalabRecOn || e.isComposing || e.altKey || e.ctrlKey || e.metaKey || !['Enter','Tab','Escape'].includes(e.key)) return;
      flushInputs();
      emit('press', e);
    }, true);
    window.__qalabRecFlush = async function() { flushInputs(); await Promise.all([...pendingBindings]); };
  } catch (e) { throw new Error("录制初始化失败: " + e.message); }
}

// 读取待确认页面缓冲，重复读取不删除事件。
export function DRAIN_SCRIPT() {
  var e = window.__qalabRec || [];
  return e.slice(); // 服务端确认后由 ACK_SCRIPT 清理。
}

// 停止捕获:置开关 false(addInitScript 撤不掉,靠开关空转)。
export async function STOP_SCRIPT() {
  const flushing = window.__qalabRecFlush?.(); // 先同步捕获尚未失焦的最终输入。
  window.__qalabRecOn = false;
  await flushing;
}

export function ACK_SCRIPT(ids) {
  var acked = new Set(ids || []);
  window.__qalabRec = (window.__qalabRec || []).filter(function(e) { return !acked.has(e.event_id); });
}

// ---- runner 侧纯映射:原始捕获事件 → 后端 event 步骤(§5 schema)。可单测。 ----
// altKey → assert(有稳定短文本则 text 断言,否则 visible);change → fill;其余 → click。
export function rawEventToStep(raw, frame) {
  if (!raw || typeof raw !== "object") return null;
  const cands = (raw.candidates || []).filter((c) => c && c.by && c.value);
  if (!cands.length) return null;
  const base = { tag: raw.tag || "", type: raw.elType || "", text: raw.text || "",
                 value: raw.value || "", candidates: cands, frame: frame || "auto",
                 ...(raw.event_id ? { event_id: raw.event_id } : {}), ...(raw.ts !== undefined ? { ts: raw.ts } : {}) };
  if (raw.altKey) {
    const txt = (raw.text || "").trim();
    return { ...base, action: "assert",
             assert: (txt.length >= 2 && txt.length <= 20) ? { kind: "text", expected: txt } : { kind: "visible" } };
  }
  if (raw.type === 'press') return { ...base, action: 'press', key_name: raw.key_name };
  if (raw.type === "change") {
    if (raw.tag === 'select') return { ...base, action: 'select_option', values: raw.values || [raw.value || ''] };
    if (raw.tag === 'input' && ['checkbox', 'radio'].includes(raw.elType)) return { ...base, action: 'set_checked', checked: raw.checked };
    if (raw.tag === 'input' && ['file', 'range', 'color'].includes(raw.elType)) return { ...base, action: 'unsupported_' + raw.elType };
    return { ...base, action: "fill" };
  }
  return { ...base, action: "click" };
}

// 仅按事件编号去重重传数据，真实连续操作保留，并按捕获时间排序。
export function dedupeSteps(steps) {
  const seen = new Set();
  return (steps || []).filter(s => {
    if (!s) return false;
    if (!s.event_id) return true; // 两次真实点击/输入即使目标相同也不能合并。
    if (seen.has(s.event_id)) return false;
    seen.add(s.event_id);
    return true;
  }).sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
}
