# Android 和 iPhone 真机对话回归

同一条业务用例在两部手机上独立执行。AI 根据验收条件生成具体的用户输入、操作顺序和可验证断言。可在同一用例中验证草稿、空白发送状态、多轮消息、回复中的文本、停止生成和历史恢复。每部手机使用已登录的专用测试账号，避免跨端同步改变另一部手机的会话列表。

## 首次接入

1. 在「设备与工具 → 我的设备」分别登记一台 Android 和一台 iOS，保存各自的执行设备标识及专属令牌。
2. 用 USB 连接手机并解锁。Android 开启 USB 调试并授权本机，`adb devices -l` 应能列出 USB 设备。iPhone 信任 Mac、开启开发者模式，`idevice_id -l` 应能列出设备。在 Mac 配置 Xcode 签名，使该手机能启动 WebDriverAgent（WDA）。
3. 在宿主机准备 Node.js 22 或更高版本、本机 Appium 服务及 XCUITest 驱动。Android 默认也使用 Appium，需要 UiAutomator2 驱动。也可在 Android 的环境文件设置 `MOBILE_ANDROID_TRANSPORT=native` 和 `MOBILE_ANDROID_PYTHON=/path/to/python`，用已安装 `uiautomator2`、`lxml` 的 Python 通过 USB 操作真机。默认 Appium 地址为 `http://127.0.0.1:4723`。两端均需已安装目标构建，可在手机上打开 APP 进入对话。若希望使用真机控件的 AI 候选定位，宿主机还需配置本地 Claude CLI。平台的需求分析和用例审查沿用项目已启用的模型引擎。
4. 为两台手机分别创建私有环境文件。例如 Android 文件可写成下面这样。iOS 文件使用 iPhone 对应的标识和令牌。用实际地址替换示例中的平台地址，令牌保留在本机。

```dotenv
BASE_URL=http://127.0.0.1:8000
RUNNER_ID=android-lab
RUNNER_TOKEN=替换为这台设备的专属令牌
```

在仓库根目录运行下列命令，先把两个路径换成本机环境文件路径。

```sh
export MOBILE_ANDROID_ENV=/path/to/android.env
export MOBILE_IOS_ENV=/path/to/iphone.env
node tools/qalab-runner/mobile/pair.mjs doctor
node tools/qalab-runner/mobile/pair.mjs serve
```

启动器会自动选取各一台已授权的 USB 真机。默认 APP 标识为 `com.qihoo.namiwork`。可在各端环境文件中设置 `MOBILE_APP_ID`，多台同系统手机时用 `MOBILE_UDID` 指定目标。iPhone 若已有可用并转发到本机的 WDA 服务，可指定 `MOBILE_WDA_URL`，或配置 `MOBILE_XCODE_ORG_ID`、`MOBILE_WDA_BUNDLE_ID` 让 XCUITest 启动已签名的 WDA。

`doctor` 分别读取实机型号、系统和已安装 APP 构建。`serve` 持续在线领取平台任务，保持这个进程运行。两台手机都在「我的设备」显示在线后即可开始测试。独立排查某台手机时，可在该端文件补齐 `MOBILE_PLATFORM`、`MOBILE_UDID` 和 `MOBILE_APP_ID`，使用 `node --env-file=/path/to/iphone.env tools/qalab-runner/mobile/runner.mjs doctor`。将 `doctor` 换成 `serve` 可单独接入该端。

## 从需求开始

进入「测试指挥官 → 测试目标 → 新建测试目标」，选择「Android + iPhone 对话」。例如输入“验证本次双端升级后，连续追问仍在同一会话，离开后可以从历史恢复”。选择已有需求分析，或关联任务并导入材料；直接输入目标时也可以用目标文字开始分析。

确认需求范围后，平台复用已有覆盖，AI 根据未覆盖条件编写新用例及共用的手机操作脚本。例如输入中英文多行和 emoji 后核对草稿，清空草稿验证不能发送；另一条用例可以连续发送三轮，核对首轮气泡，再从历史恢复。方案内可展开“核对前置、步骤、预期与来源”，查看每个动作、真实输入和待核验的轮次。脚本需经过动作与顺序校验，以及单独的验收覆盖审查。缺少驱动能力或证据的条件进入覆盖缺口。核对方案并授权两部手机，勾选「保存为双端测试计划」即可保存本次已选用例供后续回归。修改已绑定用例的步骤或预期后，需要重新审查映射。

## 从回归计划开始

进入「功能测试 → 测试计划」，选择「AI 生成双端对话用例」可直接基于新目标编排。已有用例也可选择「新建计划 → 双真机对话」。系统会预选各端唯一在线的手机，可以指定构建号；勾选冒烟选项可额外加入四条固定模板。已有计划可在用例抽屉中点「补齐对话冒烟用例」。手机具备领取条件后，点击「立即执行」。后续可沿用计划已有的定时设置或 CI 触发。

CI 通过现有的 `POST /api/hooks/run-plan` 提交计划标识及本次构建号，使用既有的 `X-CI-Token` 鉴权。请求体例如 `{"plan_id":12,"mobile_builds":{"android":"520","ios":"143"}}`。返回的 `batch_id` 可用于查询门禁和执行明细。计划内两端的构建号可以不同。

## 阅读结果

在目标报告查看每条验收条件的证据，在测试计划的「历史 → 查看明细」按用例并排查看 Android 和 iPhone 的状态、构建和步骤截图。运行四条冒烟用例会有八条设备执行记录。单端掉线会留下阻塞原因，另一端仍可提供结果。环境阻塞按所授权的策略有限复测，首次结果及复测证据可追溯。

AI 脚本中的输入保留空格、换行和 emoji。测试历史恢复时，首轮消息中的 `{{marker}}` 会替换为该设备本次运行的会话标记，用于找回对应对话。平台按派发时的脚本摘要核对动作顺序，并要求每条断言具有对应的实际值和当步上传的截图。两端均取得稳定通过才计为该条双端用例通过。

现场排查某条用例时，保存其 JSON 步骤数组，先停止占用这两部手机的 `serve` 进程，再运行 `node tools/qalab-runner/mobile/pair.mjs direct script /path/to/steps.json`。两端各执行一次，终端提供每部手机的结果和本地证据目录。需在平台报告中跟踪的任务从测试目标或测试计划发起。
