# AI 对话测评自动化工具

从飞书表格读取 query（含附件）→ 自动在测评平台（work.n.cn / 纳米Work）逐条对话 →
把【对话分享链接 / 产物分享链接 / 耗时 / 算力豆消耗 / 正文】实时回填到飞书表格。

## 快速上手（Windows）

1. 装 [Node.js 18+](https://nodejs.org/zh-cn/)（LTS，默认安装即可）
2. 双击 **`安装.bat`**
3. 记事本打开 **`.env`** 填飞书应用 ID/密钥；打开 **`config\default.config.js`** 改账号名和表格链接
4. 双击 **`录制账号.bat`** 录登录态
5. 双击 **`运行测评.bat`** 开跑

**完整步骤、飞书配置、表格格式、排查方法见 →《部署手册.md》**（务必先读）。

## 各文件用途

| 文件 | 用途 |
|---|---|
| `安装.bat` | 一键安装（Node 检查 / 依赖 / 浏览器内核 / 生成配置） |
| `录制账号.bat` | 录制测评平台登录态 |
| `运行测评.bat` | 批量执行测评并回填 |
| `有头调试.bat` | 有头模式单账号执行（弹出可见浏览器，用于观察/调试） |
| `并发观察.bat` | 有头单账号多任务并发，独立观察标签轮流点击「执行中」的任务，看是否串并自动诊断（仅用于看串+诊断，正式回填用「运行测评.bat」） |
| `桌面并发验证.bat` | **打开本地纳米 Work 桌面客户端**做对话并发验证：自动带调试端口重启客户端、复用其登录态，在单窗口内连发多条对话形成并发，切换观察+自动诊断是否串台，并抓取字段回填（复用全部现有验证能力） |
| `桌面并发验证-连接已开客户端.bat` | 同上，但**只连接你已手动带调试端口开着的**客户端（不重启进程）；先 `namiwork.exe --remote-debugging-port=9222` 再双击 |
| `_对话选项.bat` | 公共子脚本：运行前一步步选择 模型/对话模式/深度思考（被上面几个 bat 调用，勿单独双击） |
| `查看用例.bat` | 只读取表格用例、验证配置连通 |
| `打包.bat` | 生成发给同事的干净压缩包（自动排除密钥/登录态） |
| `部署手册.md` | 面向零基础同事的完整部署文档 |
| `config\default.config.js` | 配置：表格链接、账号、并发、选择器等 |

## 命令行用法（可选）

```bash
node bin\ai-eval.js list                            # 查看用例
node bin\ai-eval.js run                              # 执行测评
node bin\ai-eval.js run --dry-run                    # 只读用例不执行
node bin\ai-eval.js run -p 3                          # 覆盖单账号内任务并发数（每账号同时跑几条）
node bin\ai-eval.js run --headed -a 账号名            # 有头模式：单账号（可视化调试/观察）
node bin\ai-eval.js run --headed -a 账号名 -p 3 --watch-switch  # 有头并发+独立观察标签轮流点左侧任务列表，看对话是否串
node bin\ai-eval.js login -a 账号名                   # 录制登录态
```

## 桌面客户端并发验证（纳米 Work 桌面版）

除 Web 版外，还可直接**打开本地已安装的纳米 Work 桌面客户端**做对话并发验证。桌面版是 Electron 应用
（内部就是 work.n.cn 前端），故对话执行 / 分享链接·耗时·算力豆·正文抓取 / 串台诊断 / 回填 / 汇总
**全部复用 Web 版逻辑**；差异仅在：① 连接方式改为「带远程调试端口重启客户端 + CDP 连接」，复用客户端
当前登录的账号（无需录制）；② Electron 不支持多标签页，故并发改为「单窗口内新建任务连发多条对话 +
左侧列表切换观察」——这也正是真实桌面用户的并发用法。

```bash
node bin\ai-eval.js desktop                           # 自动重启客户端、并发跑飞书用例（默认并发 3）
node bin\ai-eval.js desktop -p 5 --limit 10           # 并发 5 条、最多跑前 10 条用例
node bin\ai-eval.js desktop --dry-run                 # 只看将并发哪些用例，不启动客户端
node bin\ai-eval.js desktop --attach                  # 只连接「已手动带端口开着」的客户端，不重启进程
node bin\ai-eval.js desktop --skip-writeback          # 只跑对话+串台诊断，不回填飞书
node bin\ai-eval.js desktop --exe "D:\path\namiwork.exe" --cdp-port 9222  # 覆盖 exe 路径/端口
```

客户端 exe 路径、调试端口等在 `config\default.config.js` 的 `desktop` 段配置。零基础同事直接双击
**`桌面并发验证.bat`** 即可。

