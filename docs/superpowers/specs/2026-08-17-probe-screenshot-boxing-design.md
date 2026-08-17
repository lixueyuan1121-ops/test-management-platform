# 探测截图 + 元素框选 设计

**日期**:2026-08-17
**状态**:已评审(用户确认:截图独立文件+静态服务;hover 双向高亮+点框加 key;整页 fullPage)

## 目标

探测(discover)输出的元素列表旁,增加一张**当前页面整页截图**,截图上按每个元素的位置
**画框**;鼠标 hover 表格行 ↔ 高亮截图对应框(双向联动),**点框可直接「加为 key」**。
让维护者一眼看清"这个候选选择器对应页面上哪个元素",添加/核对信息更直观。

## 三个技术难点与处理

### 难点 1:坐标映射(核心)
- `DISCOVER_SCRIPT` 里每个元素有 `getBoundingClientRect()`——**frame 视口相对**坐标。
- 整页(fullPage)截图坐标系 = **文档左上角**。对齐需累加三层偏移:
  1. **元素所在 frame 的滚动**:rect 已是视口相对,需加该 frame 的 `scrollX/scrollY`
     → 得到"该 frame 文档内绝对坐标"。DISCOVER_SCRIPT 内 `window.scrollX/scrollY` 可取。
  2. **iframe 嵌套偏移**:深层 frame 元素还要加"该 iframe 元素在父文档中的位置"。
     runner 侧用 Playwright `frame.frameElement()` → `boundingBox()` 逐层向上累加
     (main frame 偏移 0)。
  3. **devicePixelRatio**:fullPage 截图像素 = CSS 像素 × dpr。前端按截图**渲染宽度**
     缩放叠框(用截图自然宽/naturalWidth 归一化),不硬编码 dpr——canvas/div 坐标按
     `框.x / 截图逻辑宽 × 展示宽` 算,dpr 自动消掉。
- **产出**:每个元素带 `rect{x,y,w,h}`(整页文档绝对 CSS 坐标)+ 一个 `pageSize{w,h}`
  (整页文档 CSS 尺寸,供前端归一化)。

### 难点 2:截图存储(MySQL 5.6 致命点,已定方案)
- `probe_request.result` 是 TEXT(MySQL 5.6 上限 64KB)——base64 截图直接塞会**静默截断**。
- **截图走独立二进制通道,不进 result**:
  - runner 截图后,用**新增上传端点** `POST /api/probe/{id}/screenshot`(multipart 二进制,
    runner token 鉴权、归属校验)上传 PNG。
  - 后端存服务器文件系统:`<repo>/backend/uploads/probes/<probe_id>.png`(目录不存在则建)。
  - 静态托管:`main.py` 挂 `/uploads` → `StaticFiles(backend/uploads)`(与 `/assets` 同款)。
  - `probe_request` 加列 `screenshot_path`(VARCHAR(255),存相对路径如 `probes/12.png`);
    `_to_out` 输出 `screenshot_url`(拼 `/uploads/<path>`),无图则 null。
- `result` 里只放元素 `rect` + `pageSize`(坐标数据小,TEXT 够);截图二进制不进 DB/JSON。

### 难点 3:截图与元素一致性
- runner 侧顺序:先 `page.screenshot({fullPage:true})` 截图 → **紧接着**逐 frame 跑
  DISCOVER_SCRIPT 取 rect(同一静止时刻,中间不操作页面)。probe 本就无交互,页面静止,
  偏差可忽略。

## 数据流

```
网页发起探测 → runner 拉取
runner: page.screenshot(fullPage) → 逐 frame DISCOVER_SCRIPT(含 rect+scroll)
        → 累加 frameElement 偏移 → 每元素整页绝对 rect
        → PATCH result(groups 含 rect + pageSize)           [坐标,小,进 TEXT]
        → POST /api/probe/{id}/screenshot(PNG 二进制)        [截图,大,独立通道]
后端: result 落 TEXT;截图存 uploads/probes/<id>.png,screenshot_path 落列
网页轮询 getProbe → result(含 rect) + screenshot_url
前端: 渲染截图 + 绝对定位叠框;hover 行↔框双向高亮;点框→加为 key
```

## 落点

**runner:tools/qalab-runner/gui-mcp/gui-core.mjs**
- `DISCOVER_SCRIPT`:每个元素 out 追加 `rect:{x,y,w,h}`(视口相对)+ 返回结构加
  `scroll:{x:scrollX,y:scrollY}`、`size:{w:doc宽,h:doc高}`(每 frame 一份)。
- `probe()`:新增 `screenshot=true` 参数(默认开)。逻辑:
  1. `page.screenshot({fullPage:true})` → 返回 Buffer(base64 或落临时文件)。
  2. 逐 frame:取该 frame 的 `frameElement().boundingBox()` 累加到祖先偏移得 `frameOffset`;
     每元素绝对 rect = `{x: el.rect.x + frame.scroll.x*0? ...}`——**注**:rect 已是视口相对,
     frame 内文档绝对 = rect + frame.scroll;叠加 frameOffset(父文档中 iframe 位置)。
     main frame frameOffset=0、scroll 取主文档。
  3. 每 group 的 elements 每项加 `absRect`;返回加 `pageSize`(主文档 fullPage CSS 尺寸)。
  4. 截图 Buffer 交 runner.mjs 上传(probe 返回 `{groups, pageSize, screenshot:Buffer}`)。
- **注**:probe 返回值多一个 screenshot 字段(Buffer/base64),runner.mjs 取出单独上传,
  不塞进 reportProbe 的 result。

**runner:tools/qalab-runner/runner.mjs**
- `handleProbes()`:discover 分支拿到 `out.screenshot` 后,`POST /api/probe/{id}/screenshot`
  上传二进制(fetch + FormData / 直接 body);再 `reportProbe(id, {result:{groups,pageSize}})`。
- 新增 `uploadProbeShot(id, buffer)` 封装(镜像 reportProbe,multipart)。

**backend**
- `models/selector.py`:`ProbeRequest` 加 `screenshot_path`(VARCHAR(255) nullable)。
- `db/migrate.py`:`ensure_probe_screenshot_column`(ADD COLUMN,幂等);`main.py` 挂载+import。
- `sql/schema.sql`:probe_request 加 `screenshot_path`。
- `api/probe.py`:
  - 新增 `POST /{probe_id}/screenshot`:runner token + 归属校验;收 multipart 文件,
    存 `uploads/probes/<id>.png`,写 `screenshot_path`。限制大小(如 ≤10MB)、仅 PNG。
  - `_to_out`:加 `screenshot_url`(有 path 拼 `/uploads/<path>`,否则 null)。
- `main.py`:挂 `/uploads` StaticFiles(目录不存在先建);`uploads/` 加进 `.gitignore`
  (截图是运行时数据,不入库)。

**frontend:src/views/SelectorAdmin.vue**
- 探测结果区顶部:`<div class="shot-wrap">` 内 `<img :src="screenshot_url">` +
  绝对定位叠框层(每 group 每元素一个 `<div class="el-box">`,按 `absRect` 归一化定位)。
- 双向联动:表格行 hover → 高亮对应框(加 class);框 hover → 高亮对应行 + tooltip 显示
  best 候选;点框 → 调 `openAddAsKey`(复用现有加 key 弹窗)。
- 归一化:框位置 = `absRect / pageSize × 展示尺寸`(响应式,不依赖 dpr)。
- 截图缺失(旧探测/上传失败)时,叠框层隐藏,仅显示表格(向后兼容)。

## 边界

- 截图上传失败:reportProbe 仍照常回写 result(有框数据、无底图);前端降级为纯表格。
- 跨域深层 frame:`frameElement().boundingBox()` 是父文档侧测量,不受子 frame 跨域限制;
  但该 frame 内 evaluate 取 rect 若跨域失败,该组已有 error 处理(现状),整组跳过、无框。
- 大页面 fullPage 截图可能很大(几 MB):限制 ≤10MB,超限 runner 只回 result 不传图。
- 截图是运行时数据:`uploads/` 不入 git;老截图不清理(可后续加定期清理,YAGNI 暂不做)。
- probe verify 模式:不截图(无元素列表,截图无意义)。

## 验证

- runner:多层 iframe 测试页,probe 返回每元素 absRect 落在合理范围;截图 Buffer 非空。
- 坐标:主文档元素 absRect≈rect;深层 iframe 元素 absRect = rect + iframe 偏移(手工核一个)。
- 后端:上传端点存文件成功、screenshot_path 落库、screenshot_url 可访问;非 runner token 拒绝。
- 前端:构建通过;截图 + 框渲染对齐(hover 联动、点框加 key);无截图时降级纯表格。
