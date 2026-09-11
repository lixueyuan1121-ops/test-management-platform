// 录制捕获:注入被测页的事件监听脚本 + runner 侧的原始事件→步骤 纯映射(便于单测)。
// 设计:捕获脚本把每次 click/change 的目标元素候选(内联 genCandidates,与 DISCOVER_SCRIPT 同款)
// push 进页面全局 window.__qalabRec;runner 每轮 evaluate 排空(跨 frame),不用 exposeBinding(避时序坑)。
// Alt+点击 = 标断言。stop 时置 window.__qalabRecOn=false,捕获脚本空转(addInitScript 撤不掉,用开关关)。

// ---- 注入进页面的捕获脚本(必须自包含:addInitScript 会序列化函数源,不能引用模块作用域) ----
export function CAPTURE_INIT() {
  try {
    // 新一轮录制丢弃上一轮尚未排空的事件，监听器仍只安装一次。
    window.__qalabRec = [];
    if (window.__qalabRecHooked) { window.__qalabRecOn = true; return; }
    window.__qalabRecHooked = true;
    window.__qalabRecOn = true;
    window.__qalabRec = window.__qalabRec || [];
    var isBEM = function (c) { return /^[a-z][a-z0-9]*(?:[-_]{1,2}[a-z0-9]+)+$/i.test(c); };
    var isHash = function (c) { return /[A-Za-z0-9]{6,}$/.test(c) && !/[-_]/.test(c.slice(-8)); };
    var gen = function (el) {
      var cands = [];
      var tid = el.getAttribute("data-testid") || el.getAttribute("data-test-id") || el.getAttribute("data-test");
      if (tid) cands.push({ by: "testid", value: tid });
      if (el.id && !/^\d/.test(el.id) && el.id.length < 50) cands.push({ by: "css", value: "#" + el.id });
      var aria = el.getAttribute("aria-label");
      if (aria && aria.length < 60) cands.push({ by: "label", value: aria });
      var name = el.getAttribute("name");
      if (name) cands.push({ by: "css", value: '[name="' + name + '"]' });
      var ph = el.getAttribute("placeholder");
      if (ph) cands.push({ by: "placeholder", value: ph });
      var classes = Array.prototype.slice.call(el.classList || []);
      var bem = classes.filter(isBEM);
      var stable = bem.length ? bem : classes.filter(function (c) { return !isHash(c) && c.length > 3; });
      var stableSel = stable.length ? stable.map(function (c) { return "." + c; }).join("") : "";
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
      if (txt && txt.length >= 2 && txt.length <= 20) cands.push({ by: "text", value: txt });
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
    var emit = function (type, ev) {
      if (!window.__qalabRecOn) return;
      var el = pick(ev.target);
      if (!el || !el.tagName) return;
      var cands = gen(el);
      if (!cands.length) return;
      window.__qalabRec.push({
        type: type, tag: el.tagName.toLowerCase(), elType: el.getAttribute("type") || "",
        text: (el.innerText || el.value || "").trim().slice(0, 40), value: el.value || "",
        altKey: !!ev.altKey, candidates: cands, ts: Date.now(),
      });
    };
    document.addEventListener("click", function (e) { emit("click", e); }, true);
    document.addEventListener("change", function (e) { emit("change", e); }, true);
  } catch (e) { /* SSR/受限环境忽略 */ }
}

// 排空页面缓冲(runner 每帧 evaluate 调用):取出并清空 window.__qalabRec。
export function DRAIN_SCRIPT() {
  var e = window.__qalabRec || [];
  window.__qalabRec = [];
  return e;
}

// 停止捕获:置开关 false(addInitScript 撤不掉,靠开关空转)。
export function STOP_SCRIPT() {
  window.__qalabRecOn = false;
  window.__qalabRec = [];
}

// ---- runner 侧纯映射:原始捕获事件 → 后端 event 步骤(§5 schema)。可单测。 ----
// altKey → assert(有稳定短文本则 text 断言,否则 visible);change → fill;其余 → click。
export function rawEventToStep(raw, frame) {
  if (!raw || typeof raw !== "object") return null;
  const cands = (raw.candidates || []).filter((c) => c && c.by && c.value);
  if (!cands.length) return null;
  const base = { tag: raw.tag || "", type: raw.elType || "", text: raw.text || "",
                 value: raw.value || "", candidates: cands, frame: frame || "auto" };
  if (raw.altKey) {
    const txt = (raw.text || "").trim();
    return { ...base, action: "assert",
             assert: (txt.length >= 2 && txt.length <= 20) ? { kind: "text", expected: txt } : { kind: "visible" } };
  }
  if (raw.type === "change") return { ...base, action: "fill" };
  return { ...base, action: "click" };
}

// 去抖:丢掉与上一条"同候选签名 + 同 action"的连续重复(点击常触发合成事件;change 前的 click 等)。
// sig 用首候选 by+value + action。返回过滤后的步骤数组。
export function dedupeSteps(steps) {
  const out = [];
  let last = "";
  for (const s of steps || []) {
    if (!s) continue;
    const c0 = s.candidates && s.candidates[0];
    const sig = s.action + "|" + (c0 ? c0.by + " " + c0.value : "");
    if (sig === last) {
      // 同签名连续:若是 fill(取最后一次 value)则覆盖上一条,否则跳过
      if (s.action === "fill" && out.length) out[out.length - 1] = s;
      continue;
    }
    last = sig;
    out.push(s);
  }
  return out;
}
