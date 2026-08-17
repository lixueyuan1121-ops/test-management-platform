# 选择器「页面」组织维度 设计

**日期**:2026-08-17
**状态**:已评审(用户确认:取值=自由文本+历史建议;展示=按页面分组折叠)

## 目标

给语义选择器注册表增加**页面(page)**组织维度:同一作用域下的 key 按所属页面
(首页/任务/登录…)归类,以便"某页面改版 → 只重探/更新/校验该页面这一批 key",
让维护更精准、更方便。

## 关键设计原则

**page 只做「组织/维护」维度,不参与 runner 定位。** 这是本设计的地基:

- runner 执行用例时在**当前 DOM** 里按 `key + candidates + frame` 找元素,不需要知道
  key 属于哪个页面。故 `resolved_registry`(runner 消费的合并注册表)、`gui-core`、
  执行链路**全部零改动**,老数据 `page` 为空也照跑——**完全向后兼容**。
- page 是 `selector_key` 上的一个**属性列**,不进唯一约束:key 在 (project, sub_product)
  内仍唯一,page 只是它的归类属性(一个 key 归一个 page)。
- `page = ''` 语义为"未分类/通用"——全局元素(如导航栏)归此,不强制分页。

## 取值与展示(已定)

- **取值**:自由文本 + 历史建议。加 key/探测时可选已有页面名(前端从当前作用域已有
  page 去重给下拉建议),也可直接输入新页面名。灵活、零预设、靠建议收敛命名一致性。
- **展示**:管理页按页面分组的**折叠视图**(el-collapse)。每个页面一个可折叠面板,
  标题显示页面名 + key 数,内容是该页面的 key 表格。未分类归"(未分类)"面板置底。

## 数据模型

`SelectorKey` 加列:

```
page: Mapped[str] = mapped_column(String(64), default="", server_default="")
```

- 新库:`create_all` / `ensure_selector_tables` 依模型建表即含 page。
- 老库:`migrate.ensure_selector_page_column()` 走 `ALTER TABLE selector_key ADD COLUMN
  page VARCHAR(64) NOT NULL DEFAULT ''`(MySQL/SQLite 通用),存量行 page=''(未分类)。
- 唯一约束不变(`uq_selkey_scope_key` = project_id+sub_product+key)。

## 落点

**后端**
- `models/selector.py`:`SelectorKey` 加 page 列。
- `db/migrate.py`:加 `ensure_selector_page_column()`。
- `main.py`:init_db 在 `ensure_selector_tables()` 后调 `ensure_selector_page_column()`;import 补名。
- `schemas/selector.py`:`SelectorKeyIn` 加 `page: str = ""`;`SelectorKeyPatch` 加 `page: str | None = None`。
- `api/selectors.py`:`_key_out` 输出 page;`create_key` 存 page;`patch_key` 改 page。
- `services/selectors.py`:**不动**(resolved_registry/shared_key_* 都不需要 page)。

**前端**(`SelectorAdmin.vue`,api/index.js 薄封装透传 body 无需改)
- 管理页:单表格 → 按 page 分组折叠(`groupedRows`/`activePages`),未分类置底。
- 新增/编辑弹窗:加"页面"字段(el-select filterable allow-create + `pageOptions` 建议)。
- 探测面板:加"当前探测页面"(同款 select);加 key 时默认归属该页面。
- 加为 key 弹窗:create 模式带 page(默认=探测面板当前页面);update 模式不动目标 page。
- `pageOptions`:当前作用域 rows 的非空 page 去重(历史建议来源)。

## 边界

- 全局/跨页面元素:page 留空 = 未分类/通用,不强制分页。
- 历史建议随作用域切换(项目/子产品)刷新——不同作用域各自的页面集。
- import-legacy 导入的 key:page 默认 ''(未分类),用户后续按页面归类。
