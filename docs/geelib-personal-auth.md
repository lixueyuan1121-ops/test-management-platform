# 极库云个人账号报送

手动报送使用当前平台登录人绑定的极库云账号。缺陷执行人仍优先取问题负责人的邮箱；执行人和报送人是两个概念。项目管理员和普通成员可以报送，访客不能报送；修改本地问题状态仍需管理员权限。

## 部署配置

在后端环境中设置：

```dotenv
GEELIB_ENABLED=true
GEELIB_SSO_URL=https://sts.login.ops.360.net:4436
GEELIB_OAUTH_CLIENT_ID=<为测试管理平台登记的 OAuth 客户端>
GEELIB_OAUTH_CLIENT_SECRET=<如 SSO 客户端要求则填写>
GEELIB_OAUTH_SCOPE=internal.read internal.write user.read
GEELIB_TOKEN_ENCRYPTION_KEY=<Fernet 密钥>
```

请 SSO 管理员确认客户端允许 `urn:ietf:wg:oauth:2.0:oob` 授权码回传、PKCE S256、上述 scope、refresh_token，以及对 `geelib` / `sso-geelib-project-skill` 的业务授权。端点契约参考已安装的 qihoo-sso-cli：`/oauth/authorize`、`/oauth/token`、`/oauth/cli/authorize`。这套组合需要在实际 SSO 环境联调确认。

在受保护的部署终端生成密钥：

```sh
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

所有后端实例须使用相同密钥。密钥单独备份，不提交到版本库；更换密钥后已有授权需要重新绑定。HTTPS 使用系统证书校验，内部 CA 可通过 requests 的 `REQUESTS_CA_BUNDLE` 配置。原有项目 `geelib_sub_id` / `GEELIB_SUB_MAP` 仍需配置。

重启后端会通过 `Base.metadata.create_all` 创建 `geelib_account` 表，无需修改现有用户表。表按平台 user_id 保存加密的 SSO 凭据和短期绑定会话。个人访问及刷新令牌不返回前端，也不进入 localStorage；个人业务 Token 按请求获取，不使用全局缓存。

## 用户操作

1. 使用自己的平台账号登录，在“遗留问题”点击“绑定我的极库云账号”。
2. 点击“开始个人授权”，打开 SSO 授权页面，用自己的企业账号登录。
3. 在五分钟内将 SSO 返回的授权码粘贴回平台，点击“完成绑定”。
4. 点击问题的“上报极库云”。如 SSO 要求业务授权确认，先在 SSO 的 IM 卡片确认后重试。

授权码仅可使用一次；交换失败需要重新开始。授权过期时后端尝试用该用户的 refresh_token 刷新；不可刷新时提示重新绑定，绝不回退到服务器账号。解除绑定会删除平台保存的个人凭据和待完成会话，不会撤销 SSO 中的全部授权或影响已创建的缺陷。

“标记解决”的极库云状态同步同样使用操作人的个人授权；授权失败会保留本地解决结果并提示同步失败。后台 `GEELIB_AUTO_REPORT` 属于无人操作的自动任务，继续使用服务端 CLI 账号；该开关默认关闭。

## 验证

```sh
cd backend
python -m scripts.test_geelib_personal_auth
python -m scripts.test_geelib_report
python -m scripts.test_auto_issue
```

自动化使用本地内存数据库和模拟 SSO/极库云响应，不向真实极库云创建缺陷。上线前用两个实际账号分别授权并在测试项目报送，核对极库云创建人；同时验证授权失效、缺少项目权限及解除绑定后的提示。
