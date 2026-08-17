# 深层 frame 执行定位 设计(方案 A:url 模式 + 扁平查找)

**日期**:2026-08-17
**状态**:已评审(用户确认:方案 A;frame 复用列;找不到回退 shell/vm;url 取 hostname)

## 背景与卡点

选择器执行侧定位引擎 `gui-core.mjs` 的 `scopesFor(frame)` 只认三态:
- `shell` → `page`(主文档)
- `vm` → `page.frameLocator(VM_IFRAME)`(单一 `.work.n.cn` iframe)
- `auto` → `[shell, vm]` 都试

探测 `probe()` 已遍历 `page.frames()`(Playwright 扁平列表,含任意深度嵌套),能扫到深层
iframe 元素、能加为 key,但执行时 `scopesFor` **进不去 vm 以外任何 frame** → 深层 key
定位必 fail(SelectorAdmin 弹窗里那条 ⚠ 警告即此)。

## 关键洞察(方案 A 的地基)

- Playwright `page.frames()` 是**扁平的全 frame 列表**(含任意深度嵌套),无需逐层
  frameLocator 链进入。
- 每个 `Frame` 对象都支持与 `Page` 同一套定位 API(`.locator()`/`.getByText()`/
  `.getByTestId()`…)。
- 因此"深层 frame 定位" = **用 url 子串从扁平列表找到 Frame 对象,直接在它上面定位**。
  现有 vm(`iframe[src*=".work.n.cn"]`)本质就是"url 含 .work.n.cn 的 iframe",深层
  frame 只是这个思路的自然延伸。

## 设计决策(已定)

1. **frame 字段承载 url 模式**(复用列,不新增):
   - `shell` / `vm` / `auto`:语义不变(向后兼容,老数据照跑)。
   - `url:<子串>`:新形式。执行时在 `page.frames()` 里找 `frame.url()` 含 `<子串>` 的
     第一个 Frame 作为定位 scope。
2. **找不到匹配 frame → 回退 `[shell, vm]`**:与现有 auto 容错一致。页面结构变化/目标
   frame 未加载时不直接崩,退回主 scope 再试一遍(定位引擎本就有多候选自愈)。
3. **url 模式取 hostname**:探测时取该 frame 的 `new URL(frame.url()).hostname`(如
   `url:app.example.com`),不含 path/query——SPA 路由变化不影响匹配。取不到 hostname
   (about:blank 等)时退回整段 url 的前 80 字符。

## 数据模型

`SelectorKey.frame`:`String(8)` → `String(128)`(容纳 `url:<hostname>`)。

- 新库:模型即新宽度。
- 老库:`migrate.ensure_selector_frame_width()` 放宽列宽。
  - MySQL:`ALTER TABLE selector_key MODIFY COLUMN frame VARCHAR(128) NOT NULL DEFAULT 'auto'`。
  - SQLite:列类型无强约束(VARCHAR(8) 不截断),幂等探测已达标即跳过;为统一仍执行探测,
    SQLite 无需真正 DDL(写入不受声明长度限制)。
- `schema.sql` 同步 `frame VARCHAR(128)`。

## 落点

**runner / step-executor / selectors.json:零改动。** frame 语义全封装在 gui-core,
runner 只调 `guiCore.click({key})` 等,不感知 frame 形式。

**tools/qalab-runner/gui-mcp/gui-core.mjs**
- `scopesFor(frame)`:
  - `shell`/`vm`:不变。
  - `url:<pat>`:遍历 `page.frames()` 找 `f.url().includes(pat)` 的首个 Frame,
    返回 `[{name:'urlframe', scope: thatFrame}]`;找不到 → 返回 `[shell, vm]`(回退)。
  - `auto` 及默认:`[shell, vm]`(不变)。
  - 注:scope.scope 现在可能是 Page / FrameLocator / **Frame** 三种;`byToLocator` 对三者
    统一(都支持 getByTestId/getByRole/locator…),无需分支。
- `probe()`:每个 frame 组附 `frameMatch` 字段:
  - main frame → `"shell"`;主 vm iframe(.work.n.cn)→ `"vm"`;
  - 其它 frame → `"url:<hostname>"`(hostname 取不到则 `url:<url 前 80 字符>`)。
  - 供前端加 key 时直接采用,用户无感。

**backend**
- `models/selector.py`:frame 列宽 128。
- `db/migrate.py`:`ensure_selector_frame_width()`;`main.py` init_db 调用 + import。
- `sql/schema.sql`:frame VARCHAR(128)。
- schema/api:**无需改**(frame 已是普通字符串字段,In/Patch/_key_out 已含)。

**frontend/src/views/SelectorAdmin.vue**
- 加为 key(`openAddAsKey`):frame 取 `g.frameMatch`(探测组带来),而非旧的
  `g.frame`(shell/vm/iframe 粗标签)。深层 frame 自动存 `url:<hostname>`。
- 去掉/改写"深层 frame 执行定位待完善"警告(现已支持)→ 改为提示"将按该 frame 的 url
  定位(url:<hostname>)"。
- 编辑弹窗 frame 输入:placeholder 补 `url:` 用法说明(shell/vm/auto/url:子串)。
- probe 结果模板:`g.frame`(shell/vm/iframe 分组标签)与 `g.frameMatch`(存库值)并存——
  分组头仍显示粗标签,加 key 用 frameMatch。

## 边界

- 多个 frame 同 hostname:取第一个匹配(与现有 vm「取第一个」一致,可接受)。
- 目标 frame 未加载/页面结构变:回退 shell/vm 再试(容错),仍找不到则定位失败(正常报错)。
- 跨域 frame:`frame.url()` 可读(url 非内容,不受同源策略限制),扁平查找不受跨域影响;
  真正 evaluate 该 frame 才受限,但定位/点击/断言不需要 evaluate。
- `url:` 模式命中主 vm iframe 时与 `vm` 等价,不冲突(都指向同一 Frame)。

## 验证

- gui-core:构造多层 iframe 测试页(或用现有被测客户端),对深层 key 用 `url:` 定位
  点击/断言应成功;url 找不到时回退 shell/vm 不崩。
- 后端:新库 frame 列宽 128;老库(VARCHAR(8))跑 migrate 后放宽;CRUD 存
  `url:app.example.com`(>8 字符)不截断、读回一致。
- 前端:构建通过;探测深层元素加为 key 后,frame 存为 `url:<hostname>`。
