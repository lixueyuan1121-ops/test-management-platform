# 框选重点探测 + iframe/shadow 穿透 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 「设备探测」新增手动框选(框内 DOM 放宽全量采集)+ DOM 采集穿透嵌套 iframe 与 open shadow DOM,抓回页面上存在却探不到的按钮。

**Architecture:** runner 侧 DISCOVER_SCRIPT 重写为「递归穿 shadowRoot + bbox 放宽分支」(浏览器内 evaluate,自包含);bbox 相交筛选放 probe 主体(拿到整页 absRect 后过滤);可脱机测的纯逻辑(矩形相交、shadow 递归采集)抽到 probe-collect.mjs 用 node:test 验证。前端 SelectorAdmin.vue 加截图框选交互。平台后端零改动(bbox 走 params 自由透传)。

**Tech Stack:** runner Node18+ ESM / Playwright(CDP) / node:test;前端 Vue3 + ElementPlus。

**Spec:** `docs/superpowers/specs/2026-08-27-框选探测-iframe-shadow穿透-design.md`

## Global Constraints

- runner 测试用 `node:test` + `node:assert/strict`,文件 `*.test.mjs`,跑 `node --test <file>`。
- DISCOVER_SCRIPT 在浏览器 `frame.evaluate()` 内执行,**不能 import 外部模块、不能用闭包外变量**——必须自包含(参数只能传可序列化值)。故纯逻辑在 probe-collect.mjs 存可测副本,DISCOVER_SCRIPT 内联同款实现,靠测试保证行为一致。
- 回写结构**不变**:每元素 `{tag,type,text,rect:{x,y,w,h},candidates,best}`;probe 返回 `{groups,pageSize,screenshotBuffer}`。
- 框选放宽只去掉「白名单/父级去重/cursor」三项过滤;**保留** isVisible + genCandidates 非空 两个底线。
- 平台后端零改;bbox 是整页主文档绝对坐标 `{x,y,w,h}`。
- **本机无法端到端验真机**(360 Winsock 起不了 headed CDP):纯逻辑脱机测到位;iframe/shadow 真实穿透 + 框选定位由用户在真设备验证。
- 不破坏现有全页扫描(无 bbox 时行为等价于原逻辑 + 顺带穿 shadow)。

## 文件结构

**runner (tools/qalab-runner):**
- Create `gui-mcp/probe-collect.mjs` — 纯逻辑:`rectIntersect(a,b)`、`collectDeep(root)`,可 node:test 脱机测。
- Modify `gui-mcp/gui-core.mjs` — DISCOVER_SCRIPT 换穿透+放宽版;probe 主体加 bbox 参数 + 相交筛选。
- Create `gui-mcp/probe-collect.test.mjs` — rectIntersect + collectDeep 脱机测。

**前端:**
- Modify `frontend/src/views/SelectorAdmin.vue` — 截图框选交互 + 「框选探测」发起(params.bbox)。

**平台后端:** 零改动。

---

### Task 1: 抽出可脱机测的采集纯逻辑 probe-collect.mjs

先立可测的纯逻辑地基:矩形相交 + shadow 递归采集。

**Files:**
- Create: `tools/qalab-runner/gui-mcp/probe-collect.mjs`
- Test: `tools/qalab-runner/gui-mcp/probe-collect.test.mjs`

**Interfaces:**
- Produces:
  - `rectIntersect(a, b) -> boolean`:两个 `{x,y,w,h}` 矩形是否相交(含边界相接)。
  - `collectDeep(root, opts) -> Element[]`:从 root 递归收集所有元素,穿透 open shadowRoot
    (`el.shadowRoot` 存在则递归);opts.includeShadow 默认 true。纯 DOM 遍历。

- [ ] **Step 1: 写失败测试** `probe-collect.test.mjs`:
```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { rectIntersect, collectDeep } from "./probe-collect.mjs";

test("rectIntersect: 相交/包含/相离/边界相接", () => {
  assert.equal(rectIntersect({x:0,y:0,w:10,h:10}, {x:5,y:5,w:10,h:10}), true, "部分重叠");
  assert.equal(rectIntersect({x:0,y:0,w:100,h:100}, {x:10,y:10,w:5,h:5}), true, "包含");
  assert.equal(rectIntersect({x:0,y:0,w:10,h:10}, {x:20,y:20,w:5,h:5}), false, "相离");
  assert.equal(rectIntersect({x:0,y:0,w:10,h:10}, {x:10,y:0,w:5,h:10}), true, "右边界相接");
});

test("collectDeep: 穿透 shadowRoot 递归采集", () => {
  const inner = { tagName: "BUTTON", shadowRoot: null };
  const shadowHost = { tagName: "DIV", shadowRoot: { querySelectorAll: () => [inner] } };
  const top = { tagName: "SECTION", shadowRoot: null };
  const root = { querySelectorAll: () => [top, shadowHost] };
  const got = collectDeep(root);
  assert.ok(got.includes(top) && got.includes(shadowHost) && got.includes(inner),
    "顶层 + shadow host + shadow 内元素都采到");
});
```

- [ ] **Step 2: 跑测试确认失败**
Run: `cd tools/qalab-runner && node --test gui-mcp/probe-collect.test.mjs`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 写 probe-collect.mjs 最小实现**
```javascript
// 采集纯逻辑(可脱机测)。DISCOVER_SCRIPT 在浏览器内内联同款实现(见 gui-core.mjs)。
export function rectIntersect(a, b) {
  if (!a || !b) return false;
  return a.x <= b.x + b.w && a.x + a.w >= b.x && a.y <= b.y + b.h && a.y + a.h >= b.y;
}
// 从 root 递归收集所有元素,穿透 open shadowRoot。纯 DOM 遍历。
export function collectDeep(root, { includeShadow = true } = {}) {
  const out = [];
  for (const el of root.querySelectorAll("*")) {
    out.push(el);
    if (includeShadow && el.shadowRoot) out.push(...collectDeep(el.shadowRoot, { includeShadow }));
  }
  return out;
}
```

- [ ] **Step 4: 跑测试确认通过**
Run: `cd tools/qalab-runner && node --test gui-mcp/probe-collect.test.mjs`
Expected: PASS(两个 test 全过)

- [ ] **Step 5: 提交(建议)** `git commit -m "feat(probe): 采集纯逻辑 rectIntersect + collectDeep(穿 shadow) + 脱机测"`

---

### Task 2: DISCOVER_SCRIPT 重写(穿透 + bbox 放宽分支)

浏览器内采集脚本换成穿 shadowRoot 版;relax 放宽分支。

**Files:**
- Modify: `tools/qalab-runner/gui-mcp/gui-core.mjs:18-72`(DISCOVER_SCRIPT)

**Interfaces:**
- Produces: `DISCOVER_SCRIPT(opts)` — 接收 `{ relax }`(`frame.evaluate(DISCOVER_SCRIPT, { relax })`)。
  `relax=true`(框选)→ 采集根内全部**可见+有候选**元素(跳过白名单/父级去重/cursor);
  `relax=false`(全页)→ 白名单+cursor+父级去重(原逻辑),但采集根穿 shadowRoot。
  返回不变 `[{tag,type,text,rect,candidates,best}]`。

- [ ] **Step 1: 改签名 + 内联穿透采集**。`function()` → `function({ relax } = {})`,脚本内**内联** collectDeep
  (穿 shadowRoot,同 probe-collect.mjs 逻辑,因 evaluate 不能 import)。采集根 `collectDeep(document)`。
  isVisible/genCandidates 保留。relax=true:`collectDeep(document).filter(isVisible)`(不套白名单/不去重/不判 cursor);
  relax=false:在 collectDeep 结果上套原白名单 Set + cursor 补充 + 父级同文本去重。末尾 genCandidates 非空才 push。

- [ ] **Step 2: 语法自检**
Run: `cd tools/qalab-runner && node --check gui-mcp/gui-core.mjs`
Expected: 无语法错误

- [ ] **Step 3: 确认 DISCOVER_SCRIPT 是接受参数的函数**
Run: `cd tools/qalab-runner && node -e "import('./gui-mcp/gui-core.mjs').then(m=>console.log(typeof m.DISCOVER_SCRIPT, m.DISCOVER_SCRIPT.length))"`
Expected: `function 1`

- [ ] **Step 4: 提交(建议)** `git commit -m "feat(probe): DISCOVER_SCRIPT 穿 shadowRoot + relax 放宽分支"`

---

### Task 3: probe 主体加 bbox 参数 + 相交筛选

**Files:**
- Modify: `tools/qalab-runner/gui-mcp/gui-core.mjs:260-319`(probe 方法)

**Interfaces:**
- Consumes: `rectIntersect`(import 自 probe-collect.mjs);`DISCOVER_SCRIPT({relax})`(Task 2)。
- Produces: `probe({ contains, bbox, limit, screenshot })` — bbox 为 `{x,y,w,h}` 或空。
  bbox 非空 → 各 frame `evaluate(DISCOVER_SCRIPT, { relax:true })`,算完 absRect 后 `rectIntersect(absRect,bbox)` 过滤,limit 200;
  bbox 空 → `{ relax:false }`,limit 40(原行为)。

- [ ] **Step 1: import rectIntersect** gui-core.mjs 顶部加 `import { rectIntersect } from "./probe-collect.mjs";`(与 candidates.mjs import 同处)。

- [ ] **Step 2: probe 签名 + relax + bbox 筛选**:
```javascript
async probe({ contains = "", bbox = null, limit = 0, screenshot = false } = {}) {
  const relax = !!bbox;
  const cap = limit || (bbox ? 200 : 40);
  // 各 frame: els = await target.evaluate(DISCOVER_SCRIPT, { relax });
  // 算完 el.absRect 后:
  //   if (bbox) els = els.filter((e) => e.absRect && rectIntersect(e.absRect, bbox));
  //   else if (contains) els = els.filter((e) => (e.text || "").includes(contains));
  // groups.push(...) 用 cap 切片(原 limit → cap)
}
```
  其余(frameBox/absRect/absApprox/error 分组/screenshot/mainScroll)不变。

- [ ] **Step 3: 语法自检**
Run: `cd tools/qalab-runner && node --check gui-mcp/gui-core.mjs`
Expected: 通过

- [ ] **Step 4: rectIntersect 筛选逻辑回归**
Run: `cd tools/qalab-runner && node --test gui-mcp/probe-collect.test.mjs`
Expected: PASS(相交判定已覆盖 bbox 筛选核心;probe 主体依赖真 Playwright,端到端留真机)

- [ ] **Step 5: 提交(建议)** `git commit -m "feat(probe): probe 支持 bbox 框选(相交筛选 + relax 采集)"`

---

### Task 4: 前端 SelectorAdmin.vue 框选交互

**Files:**
- Modify: `frontend/src/views/SelectorAdmin.vue`

**Interfaces:**
- Consumes: 后端 `POST /api/probe`(params 加 bbox);probe 回的 pageSize(归一化反算)。
- Produces: 「框选探测」发起 `runProbe('box', { bbox })`。

- [ ] **Step 1: 截图区叠框选层**。截图容器加绝对定位透明层,`@mousedown/@mousemove/@mouseup` 记起止点画半透明矩形;响应式 `boxSel={active,x,y,w,h}`。
- [ ] **Step 2: 像素→整页绝对坐标**。截图按 pageSize 等比展示;框选像素 ÷ 展示尺寸 × pageSize = 整页 bbox;`computed bboxAbs`。
- [ ] **Step 3: 「框选探测」按钮**。「探测(扫当前页)」旁加按钮 `:disabled="!boxSel.w"`,点击 `runProbe('box',{bbox:bboxAbs.value})`;加清除框选按钮。
- [ ] **Step 4: 结果区提示**。框选模式结果标注「框选区域探测(已放宽,含 shadow/iframe)」。
- [ ] **Step 5: build 验证**
Run: `cd frontend && npm run build`
Expected: 构建成功,无编译错误
- [ ] **Step 6: 提交(建议)** `git commit -m "feat(probe): 前端截图框选探测交互 + bbox 参数"`

---

### Task 5: 全量测试 + 前端产物 + 真机验证清单

**Files:**
- Modify: `frontend/dist/*`(build 产物)

- [ ] **Step 1: runner 全量测试不回归** `cd tools/qalab-runner && node --test gui-mcp/*.test.mjs *.test.mjs`
  Expected: 原有 + 新增 probe-collect 测试全 PASS
- [ ] **Step 2: 构建前端** `cd frontend && npm run build`,确认 dist 更新
- [ ] **Step 3: 真机验证清单(交用户)**:真设备打开含 shadow/iframe 的目标页 → 全页探测看 shadow 内元素能否采到 → 框选那个探不到的按钮 → 确认吐候选、加为 key、执行侧能定位命中
- [ ] **Step 4: 提交(建议)** `git commit -m "build(probe): 框选探测前端产物 + 全量测试通过"`

## 自审记录

- **Spec 覆盖**:框选放宽(T2 relax+T3 bbox 筛)/shadow 穿透(T1 collectDeep+T2 内联)/iframe(现有 frames 遍历保留,T3 不破)/前端框选(T4)/平台零改/脱机测(T1)+真机清单(T5)——全覆盖。
- **evaluate 自包含**:DISCOVER_SCRIPT 内联 collectDeep(不 import),probe-collect.mjs 存可测副本。
- **类型一致**:rectIntersect(a,b)、collectDeep(root,opts)、probe({contains,bbox,limit,screenshot})、DISCOVER_SCRIPT({relax}) 全程一致。
- **回写结构不变**:元素 {tag,type,text,rect,candidates,best} + probe {groups,pageSize,screenshotBuffer} 未改。
