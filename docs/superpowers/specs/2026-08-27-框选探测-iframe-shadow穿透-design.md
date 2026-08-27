# 框选重点探测 + iframe/shadow 穿透 设计文档

**状态:** 已评审(对话逐点确认)｜**日期:** 2026-08-27

## Goal

「设备探测」页扫全页时漏掉页面上确实存在的按钮(元素在但探不到)。根因:runner 的 DOM
采集只扫主 frame + Playwright 可见 frame 的白名单元素,**不穿 shadow DOM**、深层/跨域
iframe 有薄弱点、父级去重会误杀。新增能力:

1. **手动框选**:在页面截图上拖出矩形,让探测器**只对框内 DOM 放宽过滤全量吐出**(不受
   白名单/父级去重/cursor 限制),抓回平时被过滤掉的按钮。
2. **iframe/shadow 全套穿透**:DOM 采集递归进入嵌套 iframe(含跨域尽力)和 shadow root
   (open shadow DOM),框选与全页扫描都受益。

## 现状(改造基线)

- 平台侧:`POST /api/probe` 发探测请求(`params` 自由 JSON),runner 拉取执行回写。**平台零改动**
  即可透传框选参数(params 加 `bbox` 字段)。
- runner:`tools/qalab-runner/gui-mcp/gui-core.mjs`
  - `probe({contains,limit,screenshot})`(260 行):遍历 `page.frames()` 逐 frame `evaluate(DISCOVER_SCRIPT)`,
    收集元素 + 候选选择器 + absRect(整页绝对坐标),回写分组。
  - `DISCOVER_SCRIPT`(18-72 行,在 frame 内执行):白名单选择器 + cursor:pointer 采集,
    `isVisible` 过滤,父级同文本去重(58-61),`genCandidates` 产候选。
  - **薄弱点**:①`querySelectorAll("body *")` 不穿 shadow DOM ②跨域 iframe evaluate 抛错被跳过
    ③深层 iframe boundingBox 取不到时坐标近似 ④父级去重可能误杀目标按钮。
- 前端:`SelectorAdmin.vue`,截图区展示元素框;`runProbe(mode, extraParams)` 发探测,
  params 现有 `contains`(关键词过滤)。

## 设计

### 数据流
```
前端框选(截图上拖矩形) → 归一化成整页绝对坐标 bbox{x,y,w,h}(用 probe 回的 pageSize 反算)
  → runProbe('box', { bbox }) → params.bbox 透传(平台零改)
  → runner probe 把 bbox 传入各 frame 的 DISCOVER_SCRIPT
  → 框内元素放宽全量采集(穿 iframe+shadow) → 回写候选 → 前端列出 → 加为 key
```

### 一、runner:DISCOVER_SCRIPT 重写(核心)

采集改为**递归穿透** + **bbox 分支**,保持回写结构不变(每元素 `{tag,type,text,rect,candidates,best}`)。

1. **递归采集根**:`collectRoots(root)` 从 `document` 出发,递归进入每个元素的 `shadowRoot`
   (open),把所有 root 的元素扁平收集。`walk(node)`:遍历 `node.querySelectorAll('*')`,
   对每个有 `shadowRoot` 的元素递归 `walk(el.shadowRoot)`。
2. **bbox 分支**(框选时 `bbox` 非空):
   - 不用白名单选择器,改为**框内所有元素**:遍历采集根的全部元素,`getBoundingClientRect`
     与 bbox 相交(视口坐标已按 frameBox 换算,见下)即入选。
   - **跳过父级去重、cursor 判断、白名单**——框选就是要放宽,把被误杀的吐出来。
   - 仍保留 `isVisible`(不可见元素加为 key 无意义)+ `genCandidates` 非空(无候选无法定位)。
3. **无 bbox 分支**(全页扫描):沿用白名单 + 父级去重 + cursor,但采集根改成递归穿 shadow
   (即全页扫描也顺带修了 shadow DOM 漏采)。
4. **坐标**:bbox 是整页绝对坐标;各 frame 元素 rect 是 frame 视口相对。probe 主体已算
   `frameBox`(frame 视口在 main 的偏移)+ `mainScroll`,故 bbox 比对在**主文档绝对坐标系**
   进行:`absRect = frameBox + rect + mainScroll`,与 bbox 求交。DISCOVER_SCRIPT 内拿不到
   frameBox(跨 frame),故 **bbox 相交筛选放在 probe 主体**(拿到 absRect 后过滤),
   DISCOVER_SCRIPT 只负责"穿透全量采集 + 放宽",probe 主体负责"按 bbox 收窄"。

### 二、runner:probe 主体调整(gui-core.mjs 260-319)

- `probe({contains, bbox, limit, screenshot})` 加 `bbox` 参数。
- 各 frame `evaluate(DISCOVER_SCRIPT, { relax: !!bbox })` 传入是否放宽(框选放宽,全页不放宽)。
- 算完 `absRect` 后:若 `bbox` 非空,`els = els.filter(e => rectIntersect(e.absRect, bbox))`。
- limit:框选时可调大(框内元素通常不多,但放宽后可能多),给 `bbox ? 200 : 40`。
- iframe 跨域加强:evaluate 抛错的 frame,记录 error 分组(现有);**新增**:对 error frame
  尝试 `target.frameElement()` 存在但 evaluate 失败的,标注"跨域不可采集"提示前端。

### 三、执行侧 shadow DOM 定位(gui-core.mjs resolveKey/scopesFor)

采集能穿 shadow 后,加为 key 的候选可能是 shadow 内元素的选择器。执行侧定位需能进 shadow:
- Playwright 的 CSS/text locator **默认穿 open shadow DOM**(Playwright 引擎特性),故 `scopesFor`
  产生的 `frame.locator(css)` 大多能直接命中 shadow 内元素——**执行侧大概率无需改**。
- 但 `>>>`(深度组合)或纯 XPath 候选在 shadow 内不可靠。`genCandidates` 产候选时**优先产
  Playwright 友好的候选**(css/text/role),避免 XPath 穿 shadow 失效。验证时重点测这条。

### 四、前端:SelectorAdmin.vue 框选交互

- 截图区叠加**框选层**:鼠标 down-move-up 画矩形(相对截图元素的像素坐标)。
- 换算:截图按 `pageSize` 归一化展示,反算出**整页绝对坐标 bbox**(截图像素 → 整页坐标)。
- 加**「框选探测」按钮/模式**:`runProbe('box', { bbox })`。框选模式结果只显示框内元素(后端已按 bbox 筛)。
- 清除框选、重新框选的交互;框选态可视化(半透明矩形)。

## 改动文件

**runner(tools/qalab-runner):**
- `gui-mcp/gui-core.mjs` — DISCOVER_SCRIPT 递归穿透重写 + bbox 分支;probe 主体 bbox 筛选/传参
- `gui-mcp/candidates.mjs` — 若需调整候选优先级(Playwright 友好优先)
- 新增纯函数测试:`gui-mcp/discover.test.mjs`(rectIntersect、shadow 递归采集的纯逻辑脱机测)

**前端:**
- `frontend/src/views/SelectorAdmin.vue` — 框选交互 + bbox 参数

**平台后端:** 零改动(params 自由透传 bbox)。

## 验证

- **纯函数脱机测**(我能做):`rectIntersect(bbox, absRect)` 相交判定;shadow 递归 walk 的
  采集逻辑(用 jsdom 或纯 DOM mock 构造嵌套 shadow/iframe 结构,断言采集到目标元素)。
- **真机端到端**(需你在真设备验证):本开发机 360 Winsock 注入起不了 headed CDP,无法端到端
  跑 runner 的真实 DOM 穿透。你在真设备上:打开含 shadow/iframe 的目标页 → 框选那个探不到的
  按钮 → 确认能吐出候选、加为 key、执行侧能定位。

## 已知限制

- 跨域 iframe(非同源)的 `evaluate` 受浏览器同源策略限制,CDP 下 Playwright 可访问但个别
  站点仍可能抛错——尽力采集 + 标注,不保证 100%。
- closed shadow DOM(`{mode:'closed'}`)JS 无法访问 `shadowRoot`,采集不到(浏览器限制,无解)。
- fullPage 截图不展开 iframe 内部滚动区,框选深层 iframe 内滚出可视区的元素时框可能偏。
