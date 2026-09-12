# 选择器可靠性与生产效率改进

2026-09-12：本地实施、验证完成。覆盖两轮 review 的元素选择、回填、生成、录制和执行问题。发布按 `scripts/release.sh` 提交源码与前端构建产物；线上需另执行 `scripts/update.sh` 并验证后端迁移和 Runner 升级。

## 已完成

- [x] 统一候选身份与排序，保留 role.name/exact、真实 testid 属性及 frame；标准 data-testid 的精确 CSS 写法可复用同一 key。
- [x] 自学习候选只进评审区，过滤历史 learned/pending/rejected/retired/disabled 候选，批准才进入有效注册表。
- [x] 匹配增加语义门槛、分差及冲突处理；读取完整脚本提取逐 key 上下文；复用已有 key 只改引用，不改输入文本。
- [x] Alt 选择阻止业务 pointer/mouse/click；保存前检查可见性、唯一性、frame 和原 DOM 身份，同文案其他元素及重渲染旧截图均被拒绝。
- [x] 录制完整名称占用保护、同批复用、跨 frame 隔离、共享 key 复用；非法步骤不再静默删除，无有效断言不能保存为可执行用例。
- [x] 录制回填、用例、清单项统一事务，保存幂等；手工更新校验 revision；候选合并、评审、导入覆盖和页面归组保留历史，支持恢复。
- [x] 录制/探测绑定设备 ID 和所有者；同设备领取加锁，与执行互斥；录制领取进程有租约，失联保留已收步骤并标记失败。
- [x] 事件唯一 ID、保序、确认重传；binding 到达立即落盘，旧 ACK 不覆盖新事件；停止等待最终确认，完成后拒绝新事件。
- [x] 子产品贯穿生成、校验、回填、录制、导出、执行快照；删除与引用统计仅影响实际使用该作用域版本的用例。
- [x] 区分空注册表与读取失败；内容哈希覆盖删除、候选、iframe 配置；每个执行任务固定注册表快照。
- [x] runtime 按候选优先级跨 frame 查找，重复目标消歧，隐藏候选正确兜底；配置 iframe 缺失时不误用外壳。已验证嵌套 within、open Shadow DOM、否定断言查询异常。
- [x] 录制支持 set_checked/select_option 与 Enter/Tab/Escape，停止前收集未失焦输入；保存、执行、导出共用动作语义。文件、范围、颜色控件暂不转换为错误的 fill，而是明确阻止保存。
- [x] 页面保留候选元数据，异步操作锁定上下文，准确显示停止状态；回填后可试运行关联用例并查看本批结果。

## 验收记录

测试使用隔离数据，没有对线上用例执行操作。

| 验证 | 结果 |
| --- | --- |
| 后端 34 个相关脚本模块 | 全部通过，含新可靠性模块 18 项；覆盖权限、设备隔离、事务、幂等、评审、历史、作用域、生成、回填、执行、导出和迁移 |
| Node 专项 54 项 | 全部通过：候选、匹配、停止状态、事件确认、网络失败、重启恢复、上传期间新事件、执行器 |
| 真实 Chrome 34 项 | 全部通过：录制经 API 保存再用执行快照回放、实际导出源码回放、iframe、Shadow DOM、within、否定断言、Trace、元素身份校验 |
| 实际前端 UI 1 项 | 通过：role 元数据与 revision 提交、409 保留编辑、stopping 不可保存、确认后显示表单复审控件；无页面 JS 异常 |
| 前端与静态检查 | 4 个页面 SFC 编译、Vite 生产构建、JS 语法、Python 编译、git diff --check 通过；构建仍有已有第三方注释和大 chunk 提示 |

主要证据：`backend/scripts/test_selector_reliability.py`、`tools/qalab-runner/gui-mcp/selector-reliability.test.mjs`、`runtime.test.mjs`、`selector-ui.test.mjs`、`tools/qalab-runner/recording-pump.test.mjs`、`frontend/src/views/Recorder.test.mjs`、`frontend/src/utils/bulk-fix-selectors.test.js`。

扩大回归时，未修改的 `os-key.test.mjs` 有 2 项取消：模拟子进程没有保持事件循环活跃，unref 超时器结束前测试进程退出。该既有夹具问题未计入上述专项通过数量。

## 升级和验证边界

1. 后端、前端、Runner 应配套更新。新协议需要设备专属 token、consumer_id、event_id、最终确认和探测元素引用；旧 Runner 更新重启后须重新探测。
2. 启动迁移增加用例 sub_product、录制设备/领取凭据/已保存用例关联、探测设备关联及索引，新增 selector_revision 表；MySQL 扩宽 frame 到 2048，执行 payload 到 LONGTEXT。存量用例默认共享域；无设备绑定的旧会话不会自动交给任意同名设备。
3. 迁移幂等与旧行保留已在 SQLite 验证。未在生产 MySQL 执行迁移，未做真实产品 Electron 联调或线上压测。Chrome/CDP 测试替换连接层，页面与定位、操作实际执行。
4. `.record-outbox` 排除版本控制及升级分发。磁盘故障、页面与进程同时崩溃前未送达 binding 的事件不承诺零丢失；失败会话不能伪装成完整录制保存。
5. 深层 frame 使用去掉 query/hash 的 URL 路径；同路径多 frame 拒绝自动选择。关闭的 Shadow DOM、原生系统控件不在当前定位协议覆盖范围。

## 后续可提升的内容

以下尚未实现，属于后续效率建设：

- 将 within/has_text 做成“选列表行 → 选行内按钮”的可视化引导，减少手写容器定位。
- 统计首次回填成功率、待补恢复量、复用比例、定位失败率、用例制作耗时；本次没有前后对照的生产率数据。
- 为不同弹窗和页面状态建立可复用探测清单，对受影响用例持续巡检。

原生表单动作依据 [Playwright Locator 官方文档](https://playwright.dev/docs/api/class-locator)，使用 setChecked/selectOption；Runner 与导出脚本共用 runtime。
