# 日报 HTML 短链发布与恢复

日报发布主动请求 `https://<子域名>.zhaomi.cn/` 短链，不修改测评报告的全局短链开关。上传服务与短链服务的鉴权行为不同：HTML 上传成功并不代表短链请求成功。2026-10-29 线上短链接口返回 `110005 Unauthorized request`，而上传仍成功，因此旧代码返回了长链。

## 发布行为

- 新日报上传 HTML 后请求短链，只有有效的 HTTPS zhaomi.cn 子域名首页才记为短链成功。
- 相同日报已有短链时直接复用。
- 相同日报只有 `ns.chat.360.cn/zhaomi-so/client-up/.../index.html` 长链时，再次发布只为该上传目录请求短链，不重新上传 HTML。
- 日报正文改变后创建新快照，不把旧正文的上传链接用于新正文。
- 短链失败时保留已保存的日报及可访问的临时链接；界面明确提示失败，按钮改为“重试生成短链”，不再显示成功提示。
- 后端返回 `share_link_type=short/direct/platform`，历史日报也据此显示待重试状态。较晚到达的失败结果不得覆盖已经生成的短链。

## 登录态恢复

服务当前使用 Nami 网页登录 Cookie，该凭据会失效。仅设置 `NAMI_SHORTLINK_ENABLED=true` 不能解决鉴权失败，日报本来就会单独请求短链。不要通过修改日报正文强行重新上传来绕过长链缓存。

在平台服务器的 `backend/` 目录，通过可信终端执行：

```sh
.venv/bin/python -m scripts.refresh_nami_cookie
```

从已登录 `https://www.n.cn` 的浏览器网络请求头取得完整 Cookie，粘贴到上述不回显的输入提示。不要放入聊天、命令参数、源码或日志。脚本先用无业务内容的测试页验证上传和短链，成功后备份原配置并写入权限为 600 的新文件。后端按次读取，无需重启。完成后打开已保存日报，点击“重试生成短链”。

## 长期避免

### 账号登录后的补救工具

浏览器账号密码登录通过后，可在 DevTools 的 Network 中对发往
`https://www.n.cn` 的请求选择“复制 → 以 cURL 格式复制”，然后在服务器运行：

```sh
cd /home/lixueyuan1/test-management-platform/backend
.venv/bin/python -m scripts.recover_nami_login
```

只在隐藏输入提示出现后粘贴请求，回车后单独输入 `END_NAMI_REQUEST` 并回车。
工具只解析文本，不执行复制的命令；检查来源与 Q/T 字段，用无业务内容的测试页验证
上传及真实短链，然后以 600 权限备份并原子替换 Cookie。验证失败保留原配置。
复制的请求包含凭据，不得直接粘贴到 shell、聊天或工单；处理后清理剪贴板。

这是网页登录后的手工恢复方式。此工具不保存账号密码；登录要求短信、
扫码或验证码时仍需本人操作。Cookie 写入后服务按次读取，无需重启或重新发布业务代码。

2026-10-29 已完成一次真实恢复：账号登录后取得 Cookie，验证新测试页短链成功，
备份并更新服务器凭据；随后从配置文件重新读取 Cookie，为已有测试页取得短链并确认
HTTP 200，平台健康检查通过。恢复工具的 9 项检查及短链服务的 5 项检查通过。
服务器另保存独立副本（代码未合入发布版本时也可使用）：

```sh
cd /home/lixueyuan1/test-management-platform/backend
PYTHONPATH=. .venv/bin/python ~/.local/share/qalab-tools/recover_nami_login.py
```

### 服务端认证

代码可以避免静默退成长链和长链永久缓存，但不能让网页登录 Cookie 永不过期。若短链服务提供正式服务端认证或续期协议，应使用该协议替代人工网页登录态；确认接口契约前不能伪造 token 或承诺自动续期。也可在获得监控授权后增加定期鉴权检查和失效提醒。本次未新增定时任务或消息提醒。

### 按需自动登录补救（默认关闭）

服务收到 HTTP 401 或 Nami 错误码 `110005` 后，可启动独立浏览器，用服务器私密配置中的
账号密码登录 Nami。网络错误、限流、普通权限错误不会触发登录。登录取得的新 Cookie
必须通过真实短链接口验证，随后备份旧文件、以 600 权限原子替换；后端按次读取文件。
短链失败时重用当前报告的上传目录；上传本身鉴权失败时只重试上传一次。

此功能不增加循环任务。跨进程文件锁保证同一时间最多一个登录操作；尝试后有 15 分钟
冷却期，进程异常退出也保留该状态。密码错误、验证码、短信、身份验证、未确认的服务条款、
提交凭据后的不确定结果会停止自动重试，需管理员处理后显式检查。

Linux x86_64 服务器在 `backend/` 目录执行：

```sh
.venv/bin/python -m scripts.install_nami_login_runtime
.venv/bin/python -m scripts.configure_nami_login
.venv/bin/python -m scripts.manage_nami_login check --accept-current-terms-once
.venv/bin/python -m scripts.manage_nami_login remember-terms
.venv/bin/python -m scripts.manage_nami_login enable
```

第一条从 Google 官方下载匹配的 Chrome for Testing/ChromeDriver，并在
`~/.local/share/qalab-nami-login/venv` 安装独立 Selenium 依赖，不修改平台 Python 环境。
下载支持分段缓存与超时重试；Selenium 主安装包从官方 PyPI 获取并校验发布的 SHA-256。
保持 Chrome 沙箱和 HTTPS 证书校验；系统缺少依赖时先修复运行环境，不以关闭保护绕过。
第二条在可信终端隐藏输入账号和两次密码，写入
`~/.openclaw/workspace/config/.nami-login.json`（600、Git 外），初始为关闭。
第三条仅在用户明确同意当次页面展示的条款后使用，执行一次真实登录和短链验证。
第四条还需要用户明确授权保存已确认版本用于后续补救；它要求最近 30 分钟有该账号的
成功验证，保存两份公开条款的 SHA-256、确认时间及账号指纹。第五条再验证真实登录，
仅成功才启用。没有完成实测时不得声称功能已启用。

配置可用 `NAMI_LOGIN_CONFIG_PATH` 指向私密 JSON，字段包括 `enabled`、`username`、
`password`、`python_path`、`browser_binary`、`driver_binary`、`terms_acknowledged` 和
`probe_base_url`、可选的 `approved_terms`。`probe_base_url` 必须是已有无业务内容验证页的官方上传目录；只用于上传鉴权失效时
验证新登录态，正常短链补救直接使用当前报告目录。`terms_acknowledged` 记录用户已阅读条款，
不授权浏览器勾选新的未确认条款。只有另行确认并保存的 `approved_terms` 与当前账号、
页面中的两条链接以及重新下载的两份条款全文哈希全部匹配，才复用已确认状态。
内容变化、账号变化或新链接都会停止；下载失败也不会继续勾选。
浏览器使用临时独立配置，不使用日常 Chrome 用户数据。
工作进程以 Python `-I` 隔离模式运行，防止应用目录内的同名模块（例如 `selectors.py`）
覆盖标准库。验证必须包含完整应用目录，不能只检查最小隔离副本。
密码、Cookie 不出现在命令参数、日志、网页响应或截图中。

运维命令（不显示账号、密码、Cookie）：

```sh
.venv/bin/python -m scripts.manage_nami_login status
.venv/bin/python -m scripts.manage_nami_login check
.venv/bin/python -m scripts.manage_nami_login disable
```

`check` 是管理员主动的一次登录检查，成功也不会自动打开开关；`disable` 不影响当前可用
Cookie。更新密码用隐藏配置工具重新输入，再执行 `enable`。失败原因保存在同目录
`.nami-login-state.json`：20=交互验证，21=登录被拒绝，22=运行环境，23=提交后超时或结果未知，
24=条款待确认，25=非预期登录域名，26=短链验证/写入异常，27=管理员同时更新了配置。
状态中不含明文凭据。反向代理的日报发布请求超时应覆盖最多 210 秒的补救流程；若代理提前
超时，服务端可能仍在完成补救，稍后重试会复用已更新 Cookie。

服务器预检已确认 Chrome 156 与 Selenium 4.51 可运行，依赖完整性检查通过；固定中文语言
并兼容英文 `Log in` 入口及 `input[type=submit]` 后，已完成账号登录、真实短链验证、
旧 Cookie 备份和新 Cookie 写入。单次条款授权只写入临时配置，检查完成即删除；
它不会自动变成长期授权。长期条款版本只能通过上述显式管理命令保存。

回退：运行 `disable` 即停止后续自动登录；已有 Cookie 及手工恢复工具继续可用。
不要把配置文件、Cookie 备份、浏览器用户数据或状态文件提交到 Git。

## 验证

自动补救相关回归共 54 项通过。服务器 Python 3.10 环境中原 28 项补救与短链检查通过，
并已补充完整应用目录下的标准库导入隔离检查。先使用当次确认完成真实登录，再使用已保存的条款版本（不带临时确认参数）
重复验证成功；两次均取得真实短链并备份更新 Cookie。配置回读生成的短链 HTTP 200、
平台健康检查 HTTP 200、Cookie 权限 600。代码发布和自动补救开关需单独完成。

`test_report_publishing`、`test_nami_shortlink`、`test_task_ledger` 共 30 项检查通过，原 Nami 上传/部署脚本 10 个场景通过。前端生产构建成功，并通过隔离浏览器验证：已有长链失败时显示警告，再次重试获得短链，正文和日报 ID 保持不变，两个请求均复用原上传地址。
