# 可选 Runner 双次验证工作流

本文件的设备 ID、Runner --verify-import 和双次通过要求只适用于用户明确选择的原 Runner 验证模式。默认使用 [Codex 实测交付](codex-import.md)，生成端不需要 Runner，业务失败也可导入；不要把下面旧模式约束套到 Codex 模式。

# 严格预检与逐场景交付

版本：2026.10.13.1。以下命令在技能目录运行；文件参数可用绝对路径，Windows 路径带空格时加引号。不安装额外 npm 包，使用 Runner 的 Node.js。

## 1. 只读执行预检

```sh
node scripts/preflight.mjs --runner "实际Runner目录" --cdp http://127.0.0.1:9222 --application "本次应用地址或可执行文件" --out execution-profile.json
```

端口是示例，使用现场真实值。预检读取 Runner 文件和本机 CDP，不启动应用、不读 `.env`、不换账号。浏览器页清单隐去 URL 查询参数。之后仍须确认目标 tab、真实业务 frame、登录权限、设备锁与平台能力。远端 Linux 应用不要误连本机桌面客户端。平台重跑设备也必须具备这份执行配置；只有本次临时浏览器可用时明确限制，不声称换设备可直接运行。

## 2. 建立场景清单

`plan.json` 保存用户验收范围，平台/项目/设备为本批次真实查询值：

```json
{
  "platform": "https://qalab.claw.qihoo.net",
  "project_id": 1,
  "runner_device_id": 2,
  "application": "本次待测地址",
  "requirement": "用户真实需求",
  "cases": [
    {"id":"main-1","title":"创建后进入我的列表","acceptance":["成功后进入我的列表","清空搜索并回到第一页"]},
    {"id":"retry-1","title":"列表读取失败可重试","acceptance":["展示重试入口","重试不重复提交创建"]}
  ]
}
```

示例 ID 不能照搬到另一平台。标题在本批次唯一，用于映射原始 Runner 包，不能重命名来绕过平台去重。

```sh
node scripts/delivery.mjs init --plan plan.json --manifest delivery.json
```

原清单不可覆盖。继续同一批次使用原文件；需求真实变化时建立新计划，不伪装原场景全部完成。

## 3. 先校验草稿，再实际执行

草稿格式遵循 [平台接口](platform.md#同队列流程验证)。lint 不需要报告或执行证据，检查 GUI/E2E 支持的动作参数、目标结构、动态变量、网络模拟引用和业务断言；不证明 DOM 唯一或业务通过。API动作需其执行器额外检查。

Windows：
```powershell
& ./scripts/qalab.ps1 -Action lint -BaseUrl https://qalab.claw.qihoo.net -PayloadPath draft.json
```

macOS：
```sh
python3 scripts/qalab.py lint --base-url https://qalab.claw.qihoo.net --payload draft.json
```

```sh
node scripts/delivery.mjs record --manifest delivery.json --id main-1 --status draft --packet draft.json
node "实际Runner目录/runner.mjs" --verify-import draft.json --output verified.json
```

Runner 使用当前平台和设备配置，连接真实目标 CDP。不能抢占其他任务或为复测永久改写设备 `.env`。最终完整脚本成功两次才生成 verified.json，修改脚本必须重跑，不能手填指纹。按场景单独验证可避免一条失败阻塞其他独立场景；存在依赖时先处理依赖并记录阻塞。

## 4. 每条场景都登记

```sh
node scripts/delivery.mjs record --manifest delivery.json --id main-1 --status verified --packet verified.json
node scripts/delivery.mjs record --manifest delivery.json --id retry-1 --status blocked --reason "账号没有该入口权限，无法触发列表请求"
node scripts/delivery.mjs report --manifest delivery.json
```

verified 必须提供当前通过包并经过严格证据校验；failed/blocked 必须有真实原因。原因保留缺陷位置或现场证据路径，不能用空泛原因跳过已有能力可完成的脚本。清单记录状态历史，已 submitted 的场景不能覆盖。它不是平台草稿接口，失败和阻塞不会在线上自动建例。

## 5. 原包提交并自动登记回执

Windows：
```powershell
& ./scripts/qalab.ps1 -Action validate -BaseUrl https://qalab.claw.qihoo.net -PayloadPath verified.json
& ./scripts/qalab.ps1 -Action import -BaseUrl https://qalab.claw.qihoo.net -PayloadPath verified.json -ManifestPath delivery.json
```

macOS：
```sh
python3 scripts/qalab.py validate --base-url https://qalab.claw.qihoo.net --payload verified.json
python3 scripts/qalab.py import --base-url https://qalab.claw.qihoo.net --payload verified.json --manifest delivery.json
```

GUI/E2E 严格检查两次报告指纹、执行器指纹、script 模式、复位、未使用文字前置，以及原包脚本/报告一致性。无证据、仅一次、改包均拒绝。现有 Runner 文本 actual 最多200字符；校验不猜测被截掉的后缀，依赖原始指纹并拒绝可确定的矛盾；这不是重新执行断言或服务端认证。

清单阻止遗漏 planned/draft 场景、投错平台和提交改动后的包。成功回执自动保存为清单旁 `receipt-<job_id>.json` 并登记 submitted；也可显式 `-ReceiptPath` / `--receipt-path`。接收后如果本地保存失败，客户端仍输出接收回执：保存它，再用下列命令修复清单，不重跑应用或生成新 external_id。

```sh
node scripts/delivery.mjs submitted --manifest delivery.json --packet verified.json --receipt receipt.json --platform https://qalab.claw.qihoo.net
node scripts/delivery.mjs report --manifest delivery.json
```

接受回执后按 [测试报告规范](test-report.md) 生成或更新本地 `report.html` 再结束，不为了等待去重而轮询。报告逐条包含验收点、实际结果、交付状态和未导入原因；无法导入或全部阻塞也必须交付报告。不要把 submitted 写成已入库。
