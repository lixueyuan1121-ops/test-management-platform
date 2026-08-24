// AI 对话测评自动化 - 默认配置模板
// 复制本文件按需修改。含凭证 / 账号登录态的内容切勿提交到公共仓库或外发。
module.exports = {
  // ========== 飞书电子表格配置 ==========
  feishu: {
    // 电子表格链接（支持 /sheets/ 直链与 /wiki/ 链接）
    url: 'https://my.feishu.cn/wiki/IcvdwLgPAi7YFAkxIZ3cflFEnSd?sheet=m9kQmP',
    startRow: 2,          // 用例起始行（有表头时从 2 开始）
    endRow: 100,          // 用例结束行
    queryCol: 'A',        // query 所在列
    attachmentCol: 'B',   // 附件所在列
    // 【多轮对话】会话ID列（可选，留空 '' = 关闭多轮，每行各自独立开新对话）。
    // 相同会话ID的多行 = 同一对话里按行序依次连发的多轮；每轮各自抓字段、回填到自己那一行。
    // 单元格为空的行自成一个单轮会话。示例填 'I'（放在回填列 C–H 之后的空列）。
    conversationCol: 'N',  // 会话id分组，用于多轮对话控制（mt-a,mt-b类似这样分组，相同值在同一对话session里）
    writeBack: true,      // 是否回填结果
    // 各字段回填到哪一列（非连续列会分区间写，不会覆盖中间列如 G）
    writeColumns: {
      conversationShareLink: 'C', // 对话分享链接
      artifactShareLink: 'D',     // 产物分享链接
      duration: 'E',              // 对话耗时
      beanCost: 'F',              // 算力豆消耗
      answer: 'H',                // 正文内容
      account: 'M'                // 执行该对话的账号（原 M 列表头「回放链接」，回填时覆盖为账号名）
    },

    // ⚠️ 凭证请优先通过环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET 提供。
    //    如需临时填在下方，务必确保本文件不会被提交到 git 或外发。
    //    重要：此前代码里硬编码过一个 appSecret，请立刻到飞书开放平台后台【重置】它。
    appId: process.env.FEISHU_APP_ID || '',
    appSecret: process.env.FEISHU_APP_SECRET || ''
  },

  // ========== 浏览器配置 ==========
  browser: {
    // 本机实测：有头(headless:false)启动会崩溃，只有无头可用，故默认 true。
    // 此处 headless 仅决定「默认的无头并发模式」是否有头。
    // 如需有头「单账号串行」可视化调试，直接用命令行 `run --headed -a <账号名>`（请在有桌面会话的
    // 机器上跑）——有头模式会强制单账号串行，且启动失败直接报错、不静默降级为无头。
    headless: true,
    maxConcurrent: 5,        // 同时并行的「账号」数（账号之间并行）
    // 单个账号内同时并行执行的「任务」数（每个任务占一个浏览器标签页）。
    // 账号内任务并发：发送严格有序（上一条确认进入“生成中”后，才发下一条），
    // 发出后各任务在后台并行等待生成完成与抓取。1 = 退回旧的账号内串行。
    perAccountConcurrent: 5,
    viewport: { width: 1440, height: 900 },
    // 启动方式：留空 = 优先内置 Chromium，失败自动回退系统 Chrome / Edge。
    // 也可显式填 'chrome' 或 'msedge' 强制用系统浏览器（仅当内置不可用而系统浏览器可用时）。
    channel: ''
  },

  // ========== 目标平台配置（work.n.cn 对话 UI 在跨域 iframe 内） ==========
  // ⚠️ 以下选择器需按目标页面用 F12 核对后填写；标注「占位」的尤其需要确认。
  platform: {
    chatUrl: 'https://work.n.cn/launcher',
    iframeSelector: 'iframe[src*=".work.n.cn"]',           // 对话 UI 所在跨域 iframe
    newTaskSelector: '.aside-panel__chat-button',          // “新建任务”：每条用例开启独立新对话
    // 左侧任务列表（--watch-switch 并发观察用：在独立观察标签页里定时点击前 N 个条目，切换显示对应对话看是否串）
    taskListItemSelector: '.aside-panel-task-list__item',        // 列表中每个任务条目
    taskListTitleSelector: '.aside-panel-task-list__title-text', // 条目标题文本
    inputSelector: '.chat-compose-rich__content',          // 输入框 (ProseMirror contenteditable)
    sendBtnSelector: 'button.send-btn',                    // 发送按钮
    // 生成中（停止态）：该元素出现=正在生成，消失=生成结束
    stopSignalSelector: 'button.send-btn:not(.send-btn--noop):not([disabled])',
    fileInputSelector: 'input[type="file"]',               // 文件上传
    // 附件上传成功信号：输入区「草稿附件卡片」（每个附件一项）。在 open shadow DOM 内，
    // Playwright locator 会自动穿透。发送前据此确认「附件真的挂上了」，否则本条判失败（不裸发 query）。
    attachmentCardSelector: 'attachment-item',

    // —— 回答正文 ——
    answerGroupSelector: '.chat-group.assistant',          // 每条 AI 回答容器
    answerSelector: '.chat-bubble.has-copy',               // 回答正文 bubble
    // 正文规范文本：点回答下方操作区「复制」按钮，从剪贴板取平台规范化正文（带 Markdown 格式，比 innerText 准）。
    // 实测确认：复制按钮是 ui-tooltip[content="复制"] 内的 .chat-group-actions__item（hover 才显形）。
    // 留空 = 关闭「点复制取正文」增强，回填仍用 DOM innerText。可用 execution.copyAnswer=false 全局关闭。
    answerCopyBtnSelector: 'ui-tooltip[content="复制"] .chat-group-actions__item',

    // —— 对话分享链接 → C 列（点“分享”→勾“全选”→“生成链接”，链接经系统剪贴板）——
    shareBtnSelector: 'button.topbar-chat-share-btn',          // 顶栏分享按钮
    shareSelectAllSelector: '.chat-share-panel__select-all',   // 面板“全选”
    shareCheckboxSelector: '.chat-share-panel__checkbox',      // 单项复选框（判断是否已全选）
    shareGenerateSelector: '.chat-share-panel__copy-link-btn', // 面板“生成链接”

    // —— 产物分享链接 → D 列（打开预览→「分享」→在分享弹窗「分享文件」抓永久链接）——
    // 打开预览有两条途径：① 点正文里的产物卡片；② 点顶栏「预览」按钮（有的产物不以卡片呈现、只能靠顶栏预览）。
    artifactCardSelector: 'a.chat-artifact-file-card',                       // 产物文件卡片
    artifactPreviewBtnSelector: 'button.topbar-chat-edge-tab:has-text("预览")', // 顶栏“预览”按钮（文件/预览同 class .topbar-chat-edge-tab，按文本区分）
    artifactShareActionSelector: '.task-files__preview-action[aria-label="分享"]', // 预览工具栏“分享”按钮
    // 新版分享弹窗（section.share-link__modal「分享文件」）：链接异步生成在只读输入框 input.share-link__url，
    // 顶部「通过链接可访问内容」开关默认开启。程序优先直读该 input 的 value（不碰剪贴板、不受并发污染），
    // 直读不到再点「复制链接」按钮(.share-link__action--primary)读系统剪贴板兜底。
    artifactShareModalSelector: '.share-link__modal',          // 分享弹窗容器（role=dialog）
    artifactShareUrlSelector: 'input.share-link__url',         // 弹窗内只读链接输入框（链接生成于此）
    artifactCopyLinkSelector: '.share-link__action--primary',  // 「复制链接」按钮（直读失败时点它读剪贴板）
    artifactAccessSwitchSelector: '.share-link__switch',       // 「通过链接可访问内容」开关（默认开；未开则点开）
    // 开关「on 态」的可靠信号：track 的 svg <use> 的 href（class/aria 不稳，见分享弹窗选中态经验）。
    // 点「复制链接」前必须先确认开关是此 on 态；off 态为 #icon-share-link-switch-off-track。
    artifactAccessOnHint: '#icon-share-link-switch-on-track',
    // 旧版弹窗兜底（新弹窗未出现时回退到「创建链接+有效期」旧 UI）：
    artifactPermanentSelector: '.share-link__option-title',    // 旧版有效期选项标题（配合“永久有效”定位）
    artifactPermanentText: '永久有效',                         // 旧版目标有效期文案
    artifactCreateLinkSelector: '.share-link__create',         // 旧版“创建链接”按钮

    // —— 消耗 tokens（footer，仅记录到 JSON，不回填表格）——
    costSelector: '.chat-token-cost__text',

    // —— 对话耗时 → E 列 ——
    // 主入口：回答上方「思考折叠条」状态文本（.chat-thinking-toggle 内的 __label，如“已完成 2分43秒”）。
    // 任务一完成即渲染成 DOM，无需 hover、无需刷新，最稳。只在「本轮回答组」内读，避免多轮抓到上一轮旧耗时。
    statusDurationSelector: '.chat-thinking-toggle__label',
    // 兜底入口：hover 小问号触发，读 ui-tooltip 的 content 属性（“…耗时10秒”，答完几秒后才追加）。
    // 仅当状态条抓不到时才用（如极简对话没有思考折叠条）。
    durationHoverSelector: '.chat-token-cost__icon-wrap',
    durationAttrSelector: '.chat-token-cost ui-tooltip',
    durationAttrName: 'content',

    // —— 算力豆消耗 → F 列（头像→“算力豆”，按 query 匹配“消耗来源”行，读“变动”值）——
    avatarSelector: '.topbar-avatar-image',
    // 进入算力豆账本的入口菜单项：点头像展开菜单后点它。平台已把入口从“明细”改名为“算力豆”，
    // 故默认用正则兼容两者（新 UI“算力豆”/旧 UI“明细”）。若页面顶栏也有“算力豆”文字导致误点，
    // 请用 F12 核对后改成菜单项专属 class（如 '.user-menu__item:has-text("算力豆")'）。
    ledgerEntrySelector: 'text=/算力豆|明细/',
    ledgerRowSelector: '.coin-info__row',
    ledgerRowQuerySelector: '.coin-info__row-query',
    ledgerRowChangeSelector: '.coin-info__change-amount',
    // 明细面板（config-modal 设置弹窗「算力豆明细」）关闭按钮。实测：该弹窗 Escape 关不掉，
    // 必须点右上角 × .config-modal__close，否则读完账本后面板不关、遮住左侧任务列表致后续任务定位失败。
    ledgerCloseSelector: '.config-modal__close',
    // 带附件用例的「消耗来源」精确匹配：来源须含下列任一「上传字样」，且其“用户问题：”后文本≈用例 query。
    // 平台把带附件来源包成“用户上传了以下文件…用户问题：<query>”；靠此避免误配到别的行。留空/失配则退回宽松匹配。
    ledgerAttachmentMarkers: ['用户上传了以下图片', '用户上传了以下文件'],
    ledgerUserQuestionMarker: '用户问题',

    // —— 执行过程中的确认弹窗（反问/授权/风险确认等）——
    // 任务执行时若平台弹出确认框，自动点击“肯定”类按钮让任务继续。
    // confirmDialogSelector 命中任一即视为“出现了确认弹窗”；confirmButtonTexts 为按钮文案（按文本匹配，命中第一个）。
    // 平台侧按钮 class 若已知，可直接填进 confirmButtonSelectors 优先用选择器点击（比文本更稳）。
    confirmDialogSelector: '.n-modal, .n-dialog, [role="dialog"], .el-message-box, .el-dialog, .ant-modal, .modal',
    confirmButtonSelectors: [],
    confirmButtonTexts: ['确认', '确定', '继续', '允许', '同意', '好的', '是', '开始', '执行'],

    // —— 执行过程中的「选择题弹窗」（需先勾选选项、再点提交才能继续）——
    // 自动为每道选择题勾选一个选项（每题选第一个），全部勾完后点“提交”，让任务继续。
    // 选择器留空 = 用内置通用兼容规则：原生 radio/checkbox 优先，无则兜底常见自定义控件；
    // 分组不依赖 class，按“最近的、含≥2个选项的祖先”自动聚成一道题（原生 radio 按 name 分组）。
    choiceOptionSelector: '',   // 选项元素（留空=内置兼容；若内置不准，按实际页面填精确选择器）
    choiceGroupSelector: '',    // 选择题分组容器（留空=按“最近含≥2选项的祖先”自动分组）
    choiceSelectedHints: [],    // 判定选项“已选中”的 class 关键词（留空=selected/active/checked）
    submitButtonSelectors: [],  // “提交”按钮选择器（优先，比文案稳）
    submitButtonTexts: ['提交', '确定', '确认', '完成', '下一步', '继续', '发送', '开始', '执行'],

    // —— 执行中「专家反问」的选择题卡片（work.n.cn 实测：内联渲染，不是 modal 弹窗）——
    // 平台把反问渲染成 chat-question-form-card：每个 .ask-form__options 是一道题，
    // 选项为 .ask-form__option（role=option，选中态 class 含 is-selected），提交按钮 .ask-form__btn--ok。
    // 程序自动为每题选第一个选项（已选则跳过），全部选完点“提交”，让专家继续执行。
    askFormSelector: '.chat-ask-form-card, chat-question-form-card',
    askFormOptionsGroupSelector: '.ask-form__options',
    askFormOptionSelector: '.ask-form__option',
    askFormSelectedHints: ['is-selected'],
    askFormSubmitSelector: '.ask-form__btn--ok',

    // —— 执行中「工作流/工具确认」弹窗（work.n.cn 实测：Web Component tool-confirm-modal，渲染在 open Shadow DOM 内）——
    // 复杂任务(如"输出PPT")agent 会规划一个 Workflow，弹出“确认要运行工作流完成该任务吗?”的选择卡片，
    // 需选一个选项(默认"是，运行工作流"已预选)并点“提交”才继续；不处理则任务永久挂起直到超时。
    // 该卡片与 ask-form 结构不同(.panel/.opt/.btn-submit)，且在 Shadow DOM 内；shadowRoot 为 open，
    // Playwright 的 CSS locator 可穿透命中，故无需特殊 shadow 处理。选项容器即卡片本身(单题)。
    toolConfirmSelector: 'tool-confirm-modal, .panel[role="dialog"]',
    toolConfirmOptionSelector: '.opt',
    toolConfirmSelectedHints: ['selected'],
    toolConfirmSubmitSelector: '.btn-submit',

    // —— 【并发观察 / 诊断】以下供 --watch-switch 观察器「只切执行中任务」与「诊断」用。——
    // ⚠️ 均为按现有命名规律的「默认猜测」，请务必用 F12 核对后填准；未匹配到元素时，
    //    诊断会降级为「无法检测(unknown)」而不是误报，故填错不会造成假异常，但会漏检。
    // 任务条目「执行中」标志（在单个任务条目内查找，命中=该任务正在执行）。
    // 桌面版实测(2026-08)：执行中态=条目 __trailing 内 <img class="aside-panel-task-list__status--running">，
    // 完成态该 img 移除、__badges 出现。用精确 .aside-panel-task-list__status--running 命中；
    // loading/spin/svg.animate-spin 作其他态兜底（去掉宽泛 [class*=running] 避免误命中别的含 running 的元素）。
    taskListRunningSelector: '.aside-panel-task-list__status--running, [class*="loading"], [class*="spin"], svg.animate-spin',
    // 用户提问气泡容器（与 AI 回答容器 .chat-group.assistant 对应；用于「串台/连续提问无正文」诊断）
    userGroupSelector: '.chat-group.user',
    // 用户提问正文（气泡内文本；留空则取 userGroupSelector 整组文本）
    userBubbleSelector: '.chat-bubble',
    // 每个任务所用 agent 名（实测确认：在左侧任务条目内的 .aside-panel-task-list__name，
    // 如「办公助手」「纳米AI助理12」「纳米Work」）；诊断 b 据此判断“agent 不是期望值”。
    taskListAgentSelector: '.aside-panel-task-list__name',
    // 期望的 agent 名（诊断 b：任务 agent 名不含此值即告警）。按你的测评要求填；
    // 实测该账号历史任务里 agent 有「办公助手」「纳米AI助理12」等，若期望统一用某助手请改这里。
    expectedAgentName: '纳米Work',

    // —— 【对话选项】发送前设置：模型 / 对话模式 / 深度思考。均实测确认；选项统一用「文本匹配」点击。——
    // 模型下拉（输入框左下「GLM-5.2 ▾」）：点 model-dropdown 展开，选项为 div.item-name（文本=模型名）。
    modelDropdownSelector: 'model-dropdown',                 // 触发（点它即展开）
    modelOptionSelector: '.item-name',                       // 选项（文本匹配模型名）
    // 对话模式（下方左「⚡标准模式 ▾」）：点该 button 展开，选项为 span.row-title（文本=模式名）。
    chatModeTriggerSelector: 'button.trigger[aria-haspopup="listbox"]',
    chatModeOptionSelector: '.row-title',
    // 深度思考（下方右「思考深度 | 标准 ▾」）：点该 button 展开，选项按文本匹配（低/中/标准/高/超高）。
    // ⚠️ 选项 class 未在无头下捞到，用文本匹配兜底；如实测点不中，用 F12 核对后填 thinkingDepthOptionSelector。
    thinkingDepthTriggerSelector: 'button.trigger[aria-haspopup="menu"]',
    thinkingDepthOptionSelector: ''                          // 留空=纯文本匹配可见选项
  },

  // ========== 平台对接（测试管理平台 eval-queue）· 平台模式用 ==========
  // ⚠️ 命名区分：本段 platformApi = 平台对接凭据（node bin/ai-eval.js platform 用，拉/回写任务）；
  //    上方 platform = work.n.cn 页面选择器段（对话执行/抓取用）。两者用途不同，勿混。
  // 凭据优先环境变量（.env 里配 BASE_URL/RUNNER_TOKEN/RUNNER_ID），避免写进配置文件被提交。
  platformApi: {
    baseUrl: process.env.BASE_URL || '',        // 测试管理平台地址，如 http://10.x.x.x:8000
    token: process.env.RUNNER_TOKEN || '',      // 设备 token（平台「我的设备」注册获取）
    runnerId: process.env.RUNNER_ID || 'mac-01',// 本执行机标识（平台按此派发任务）
    pollMs: 5000,                               // 常驻轮询间隔（毫秒）；--once 时不用
  },

  // ========== 账号配置 ==========
  accounts: [
    { name: 'taigu1212', storageState: './accounts/taigu1212.json' },
    { name: 'taigu1313', storageState: './accounts/taigu1313.json' }
  ],

  // ========== 执行配置 ==========
  execution: {
    // 【对话选项】发送前统一设置（本次运行所有用例通用）。留空 = 不设置、保持页面当前默认。
    // 可用命令行 --model / --chat-mode / --thinking-depth 覆盖。
    dialogOptions: {
      model: '',          // 模型名。实测(2026-07 dump)下拉共 12 项：智能路由 / DeepSeek-V4-Pro /
                          //   DeepSeek-V4-Flash / Qwen3.7-Plus / Qwen3.7-Max / GLM-5.2 / Kimi (K3) /
                          //   Kimi (K2.7) / 豆包（seed-2.1） / Deepseek-V3.2 / MiniMax (M3) / 智脑（360gpt-Pro）。
                          //   ⚠️ 豆包/智脑用「全角括号（）无空格」，Kimi/MiniMax 用「半角括号()带空格」；
                          //   匹配已容忍全半角/空格/大小写差异，仍拿不准就跑 `node bin/dump-models.js` 看真实文本。
      chatMode: '',       // 对话模式：智能模式 / 计划模式 / 目标模式（实测 dump；旧注释的“标准模式/选择工作流”已不存在）
      thinkingDepth: ''   // 深度思考：低 / 中 / 标准 / 高 / 超高（选项 class 未知，按文案填）
    },
    timeout: 60000,            // 页面加载 / 元素等待超时
    responseTimeout: 18000000, // 等待对话生成完成（5 小时上限；超长 Agent 任务用）
    generationStartTimeout: 120000, // 生成“开始”的最长等待（并发/排队时启动会慢，勿调太小）
    // query 超过此长度(字符)改用剪贴板粘贴输入，而非逐字输入——逐字 delay 累加会超时
    // （如几百行的 prompt 模板，逐字输入几千字符会超 30s 卡死）。短文仍逐字（对 ProseMirror 更稳）。
    pasteThreshold: 800,
    retryTimes: 1,
    retryDelay: 3000,
    takeScreenshotOnError: true,
    // 账号内相邻两条对话「创建/发送」之间的最小间隔（毫秒）：上一条确认进入生成后，等这么久再发下一条，
    // 避免并发时多条对话创建挤在一起。默认 3000ms（3 秒）；设 0 = 上一条一进入生成就立刻发下一条。
    sendIntervalMs: 3000,
    // 账号内多任务并发时，后台巡检各标签「是否正在执行」的间隔（毫秒）；打印巡检日志。设为 0 关闭巡检。
    watchIntervalMs: 15000,
    // 【有头观察】多任务并发时，开一个独立观察标签页，定时轮流点击左侧任务列表的前 N 个条目，
    // 切换显示各任务对话，供肉眼确认多个并发对话是否会串。仅在「有头 + 开启 --watch-switch」时生效；
    // 每次切换会打印当前查看的任务标题与正文片段。单位毫秒，0=关闭自动切换。
    // 可用命令行 `--switch-interval <秒>` 覆盖，现场调快慢。
    switchIntervalMs: 5000,
    // 【有头观察】兜底刷新：若平台不实时把新建任务推送到左侧列表，观察标签会看不到本次任务。
    // 当列表可见条目数还没达到并发数、且距上次刷新超过此毫秒数时，刷新观察页面（回 launcher）拉取最新列表。
    // 带内置次数上限（避免任务始终不足时无限刷新打断画面）。0=关闭兜底刷新。
    // 可用命令行 `--reload-interval <秒>` 覆盖。
    watchReloadMs: 30000,

    // 【桌面并发】单窗口内轮流切到各在跑任务「查看+串台诊断+完成检测」的间隔（毫秒）。
    // 桌面版每次切换会重挂对话 iframe（较重），故间隔比 Web 观察大一些，默认 8 秒。
    desktopPatrolMs: 8000,

    // 【桌面附件】上传后等「附件草稿卡片出现」的上限（毫秒）。大附件(如数十 MB 代码 zip)上传较慢，
    // 超时仍未出现卡片=上传失败，本条用例判失败（不裸发无附件的 query）。
    attachmentUploadTimeout: 60000,

    // ========== 完成判定 / 数据质量（避免把“思考中…”中间态当成最终答案回填）==========
    // 停滞快速失败：当任务既非“生成中”、又无完成信号(footer)、又无真实答案气泡，且持续超过此毫秒数
    // 无任何进展（多为卡在无法自动处理的弹窗/异常），判为“未完成”提前结束，不再干等到 responseTimeout。
    // 反问已能自动应答，此项是防“未知卡点”的兜底。0 = 关闭（坚持等满 responseTimeout）。
    stallTimeout: 1200000, // 20 分钟无进展即判卡死
    // 正文以这些词开头，视为“未完成中间态”（思考过程而非最终答案）：该条判不成功、不回填此垃圾正文。
    incompleteMarkers: ['思考中', 'Thinking'],
    // 因“超时/中间态未完成”导致的失败，是否整条重跑（重跑一条超长任务大概率再次超时，默认否，避免空耗数小时）。
    rerunOnIncomplete: false,
    // 正文（H 列）是否优先用「点回答下方复制按钮」取到的规范文本（带 Markdown 格式，比 DOM innerText 准）。
    // true(默认)=点复制、拿到就用、拿不到沿用 DOM 文本；false=只用 DOM innerText（不点复制、不碰剪贴板）。
    // 仅在「答案取自真实气泡」时才点复制（思考中/未完成态不点）。选择器见 platform.answerCopyBtnSelector。
    copyAnswer: true,
    // 从 costSelector 文本“本次回答消耗：8.5K tokens”中提取消耗（取“消耗：”之后）
    costRegex: '消耗[：:]*\\s*([^，,。（(\\n]+)',
    // 从状态条“已完成 2分43秒”提取耗时“2分43秒”（取“已完成”之后到行尾）
    statusDurationRegex: '已完成[\\s:：]*(.+)',
    // 状态条出现此词=任务已完成、耗时已定；未出现（如仍“思考中”）则视为未就绪，交给 tooltip 兜底
    statusDoneMarker: '已完成',
    // 等状态条从“思考中”过渡到“已完成 X”的最长轮询（毫秒）；到点仍未见则走 tooltip 兜底
    statusDurationTimeout: 8000,
    // 兜底 tooltip：从 content“…耗时10秒”中提取耗时；不匹配时回填原文
    durationRegex: '耗时[：:]*\\s*([^，,。\\n]+)',

    // ========== 算力豆(F)账本抓取稳健化（降低回填失败率）==========
    // 「头像→明细」账本面板的打开、以及平台把本次消耗记账落库，都可能有延迟：
    //  · beanCostAttempts   ：每条用例内最多重开明细面板几次（记账延迟/AI 标题晚生成时多试几轮）。
    //  · beanCostRetryGapMs ：两次重开之间的等待毫秒（给记账入库留时间，别刚答完就判定没有）。
    //  · ledgerRowsTimeoutMs：点「明细」后，轮询等账本行渲染出来的最长毫秒（网络慢时行会晚出现）。
    beanCostAttempts: 4,
    beanCostRetryGapMs: 2500,
    ledgerRowsTimeoutMs: 8000,

    // ========== 补填机制（“应有却为空”的回填字段，当场重抓）==========
    // 回答完成、对话还开着时，检查这些字段是否“应有却为空”，为空则当场重抓（代价小、无需翻历史）。
    // artifactShareLink 特殊：仅当确有产物卡片时才算缺失（本来无产物则空是正确的，不补）。
    requiredFields: ['conversationShareLink', 'artifactShareLink', 'duration', 'beanCost'],
    refillRounds: 2, // 每条用例内最多重抓几轮（每轮把仍为空的字段各抓一次）
    // 就地补填若干轮后仍缺关键字段时，刷新一次当前对话页再抓（应对「耗时等字段答完后需刷新才渲染」）。
    // 刷新只在本对话内进行、不新建对话；true=开启，false=关闭。
    refillReloadOnce: true,
    // 当场补填仍为空、且属于以下“关键字段”时，才整条重跑（新对话可稳定重生成分享链接）。
    // 耗时/算力豆多为平台侧时序问题，重跑一条数分钟的 Agent 任务收益低，故默认不因它们重跑。
    rerunOnMissingFields: ['conversationShareLink', 'artifactShareLink']
  },

  // ========== 输出配置 ==========
  output: {
    saveJson: true,
    saveSummary: true,
    outputDir: './output'
  },

  // ========== 桌面客户端场景（纳米 Work 桌面版 · 并发验证） ==========
  // 纳米 Work 桌面版是 Electron 应用（内部就是 work.n.cn 前端），故所有页面选择器与对话/抓取/
  // 诊断逻辑与 Web 版完全复用；仅「连接方式」不同：程序带远程调试端口启动客户端，再用 CDP 连接。
  // 触发命令：node bin/ai-eval.js desktop（或双击「桌面并发验证.bat」）。
  desktop: {
    // 客户端可执行文件路径（本机实测安装位置；换机器请改）。
    executablePath: 'D:\\program files\\namiwork\\namiwork.exe',
    cdpHost: '127.0.0.1',
    cdpPort: 9222,             // 远程调试端口（带 --remote-debugging-port 启动 + CDP 连接）
    processName: 'Namiwork.exe', // 自动重启前用 taskkill 关这个进程名
    // 自动重启开关：端口未就绪时，是否关闭现有客户端并带调试端口重启（用户已确认默认 true）。
    // 若想「连接已手动带端口开着的客户端、绝不动进程」，用命令行 --attach（等价 attachOnly=true）。
    attachOnly: false,
    killExisting: true,        // 自动重启时是否强杀现有进程（Electron 单实例锁下必须先关，端口才开得起来）
    userDataDir: '',           // 留空=复用客户端默认 userData（保留当前登录态；这是「复用客户端登录态」的关键）
    launchArgs: [],            // 追加启动参数（一般不用）
    launchTimeout: 45000,      // 等调试端口就绪超时
    readyTimeout: 60000        // 等主窗口对话界面就绪超时
  },

  // ========== 批量录制账号登录态（bin/record-accounts.js / 批量录制登录态.bat） ==========
  // 从「账号台账表」读 statusCol 列「未录制（否/空）」的账号，逐个用账号密码登录 work.n.cn
  // （风控偶发弹图形验证码时，用离线 ddddocr 自动识别，无需人工），成功后：
  //   · 存登录态到 accounts/<qid>.json      · 台账回填：recordedQidCol=实际 qid、statusCol=doneValue（是）
  // 幂等：已录（台账=是 或 本地已有 accounts/<qid>.json）自动跳过，可反复/断点续跑。
  // ⚠️ 本表是「账号台账」，与最上方「测评用例表 feishu.url」是两张不同的表，别填混。
  accountRecording: {
    url: 'https://my.feishu.cn/wiki/EtPYwCeiJioXFEk2gkZc4IVUnpf?sheet=7MBW2a', // 账号台账表链接（/wiki/ 或 /sheets/ 均可）
    startRow: 2,            // 台账起始行（表头在第 1 行）
    endRow: 400,            // 台账结束行（读取窗口上限，账号更多就调大）
    qidCol: 'D',            // qid 列：命名 accounts/<qid>.json、回填「已录 qid」的取值来源（也用作预判是否已录）
    phoneCol: 'E',          // 登录手机号列（作为账号 userName）
    passwordCol: 'F',       // 登录密码列
    statusCol: 'G',         // 「是否已录制」列：值 ≠ doneValue 视为待录（否/空都会录）；成功后写 doneValue
    recordedQidCol: 'C',    // 「已录 qid」回填列（成功后写入实际登录得到的 qid）
    doneValue: '是',        // 「已录」标记值（statusCol 等于它即视为已录、跳过）

    // 独立 Chrome 远程调试地址（用 9333 避开常被纳米桌面版占用的 9222）。
    cdpUrl: 'http://127.0.0.1:9333',
    // 独立 Chrome 可执行路径（留空=自动探测常见安装路径）。端口没开时用它带调试端口自动拉起。
    chromePath: '',
    // 自动拉起 Chrome 用的用户数据目录（专用，避免污染日常 Chrome）。留空=系统临时目录下 chrome-eval-record。
    chromeUserDataDir: '',
    // Python 解释器路径（跑 ddddocr 验证码识别）。留空=自动探测（PATH 里的 python / 本机 openclaw 内置 python）。
    // 依赖：pip install ddddocr 后 pip install onnxruntime==1.20.0（新版 onnxruntime 在部分机器会 DLL 崩溃）。
    pythonPath: '',

    maxCaptcha: 14,         // 单账号验证码最多重试张数（识别出 4~6 位才真正提交；长度不固定，故非严格 4 位）
    cooldownEvery: 6,       // 每录 N 个账号做一次额外冷却
    cooldownMs: 25000,      // 上述额外冷却时长（毫秒，降低风控连续弹验证码）
    baseGapMs: 4000,        // 账号间基础间隔（毫秒，慢速重跑调大到 30000+ 可让登录回到免验证码区间）
    passes: 1,              // 整批重跑轮数（每轮只处理未录，捡回验证码卡壳的账号；命令行 --passes 覆盖）
    passCooldownSec: 60     // 轮间冷却（秒，让风控衰减后再重试上一轮失败的）
  }
};
