<template>
  <div class="ai-testgen">
    <!-- 输入区 -->
    <el-card class="input-card">
      <template #header>
        <div class="card-head">
          <div class="title-wrap">
            <el-icon class="title-icon"><MagicStick /></el-icon>
            <div>
              <div class="title">AI 测试助手 · QA Copilot</div>
              <div class="subtitle">粘贴需求，AI 秒级拆解出结构化、可执行的测试点清单</div>
            </div>
          </div>
          <el-tag v-if="!aiAvailable" type="danger" effect="light" round>AI 服务不可用</el-tag>
          <!-- 仅在有 2 个及以上可用引擎时才显示切换器;只有一个引擎无需切换,直接隐藏 -->
          <div v-else-if="availProviders.length > 1" class="engine-picker">
            <span class="engine-label">生成引擎</span>
            <el-radio-group v-model="engine" size="small" :disabled="running" class="engine-seg">
              <el-radio-button
                v-for="p in availProviders"
                :key="p.id"
                :value="p.id"
              >
                <span class="eng-opt">
                  <i class="eng-dot" :style="{ background: engineMeta(p.id).dot }"></i>
                  {{ engineMeta(p.id).label }}
                </span>
              </el-radio-button>
            </el-radio-group>
          </div>
        </div>
      </template>

      <div class="form-row">
        <el-select v-model="pid" placeholder="选择项目" style="width:200px" @change="onProjectChange">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <TaskPicker v-model="taskId" :tasks="tasks" placeholder="关联任务（必填）" @change="onTaskChange" />
        <el-select
          v-model="targetPages" multiple filterable collapse-tags collapse-tags-tooltip
          placeholder="目标页面（可选，聚焦该页选择器）" style="width:260px" :disabled="running"
        >
          <el-option v-for="p in pageOptions" :key="p" :label="p" :value="p" />
        </el-select>
      </div>
      <div v-if="pageOptions.length" class="page-hint">
        选目标页面后:生成时只注入该页(+未分类)的选择器 key、更聚焦少降级,并给该批用例打上页面标(便于后续按页维护)。
      </div>

      <el-alert
        v-if="taskCasesTotal"
        type="warning" :closable="false" show-icon class="task-cases-tip"
      >
        <template #title>
          该任务已有 <b>{{ taskCasesTotal }}</b> 条历史用例，请确认是否需要重复生成。
          <el-button link type="primary" size="small" @click="showTaskCases = !showTaskCases">
            {{ showTaskCases ? '收起' : '查看' }}
          </el-button>
        </template>
        <div v-if="showTaskCases" class="task-cases-list">
          <div v-for="c in taskCases" :key="c.id" class="task-case-item">
            <el-tag :type="KIND_TYPE[c.exec_kind || 'gui'] || 'info'" size="small" effect="plain">{{ KIND_LABEL[c.exec_kind || 'gui'] }}</el-tag>
            <span class="tc-title">{{ c.title }}</span>
          </div>
        </div>
      </el-alert>

      <div class="mode-row">
        <el-button
          v-for="m in MODES"
          :key="m.k"
          :type="inputType === m.k ? 'primary' : 'default'"
          size="small"
          :disabled="running || extracting"
          @click="inputType = m.k"
        >{{ m.label }}</el-button>
      </div>

      <div v-if="inputType === 'url'" class="extract-row-wrap">
        <div class="extract-row">
          <el-input v-model="urlInput" placeholder="需求网页链接，或飞书 docx/wiki/sheets/base 链接" :disabled="extracting" @keyup.enter="doExtractUrl()">
            <template #prepend>URL</template>
          </el-input>
          <el-button type="primary" :loading="extracting" :disabled="!urlInput.trim()" @click="doExtractUrl()">抓取正文</el-button>
        </div>
        <div class="extract-tip">支持普通网页与飞书文档（飞书需管理员配置应用凭据并把文档共享给应用）</div>
      </div>

      <el-upload
        v-else-if="inputType === 'file'"
        :auto-upload="false"
        :show-file-list="false"
        :on-change="onFilePick"
        accept=".txt,.md,.markdown,.docx,.pdf"
        drag
        class="upload-row"
      >
        <el-icon class="up-icon"><UploadFilled /></el-icon>
        <div class="up-text">拖拽或点击上传需求文档<br><span>支持 txt / md / docx / pdf，≤ 5MB</span></div>
      </el-upload>

      <div v-else class="text-tools">
        <el-button text type="primary" @click="fillDemo" :disabled="running">填充示例需求</el-button>
      </div>

      <div v-if="sourceInfo" class="source-info">
        <el-icon><Document /></el-icon>
        <span>来源：{{ sourceInfo.label }} · 提取 {{ sourceInfo.chars }} 字（可切「编辑」修改后再生成）</span>
      </div>

      <!-- api 用例引导：有契约展示可选接口清单（可「引用」到需求）；无契约提示去配置。附「插入需求模板」。 -->
      <div v-if="pid" class="api-guide">
        <template v-if="apiContract.has_contract">
          <el-collapse class="api-collapse">
            <el-collapse-item name="c">
              <template #title>
                <el-icon class="api-guide-icon"><Connection /></el-icon>
                <span class="api-guide-title">本项目 api 接口清单（{{ apiLines.length }}）</span>
                <span class="api-guide-sub">写 api 用例需求时可「引用」，AI 据此打对接口</span>
              </template>
              <div class="api-line" v-for="(l, i) in apiLines" :key="i">
                <code class="api-code">{{ l }}</code>
                <el-button link type="primary" size="small" @click.stop="refInterface(l)">引用</el-button>
              </div>
            </el-collapse-item>
          </el-collapse>
          <el-button link type="primary" size="small" class="tpl-btn" @click="insertApiTemplate">
            插入 api 用例需求模板
          </el-button>
        </template>
        <div v-else class="api-guide-empty">
          <el-icon><Connection /></el-icon>
          <span>本项目未配置 api 契约 · api 用例可能降级人工。</span>
          <router-link to="/api-env" class="api-guide-link">去配置 api 环境 →</router-link>
          <el-button link type="primary" size="small" @click="insertApiTemplate">插入 api 用例需求模板</el-button>
        </div>
      </div>

      <div class="req-head">
        <span class="req-label">需求内容</span>
        <el-radio-group v-model="reqMode" size="small" :disabled="running">
          <el-radio-button label="preview">预览</el-radio-button>
          <el-radio-button label="edit">编辑</el-radio-button>
        </el-radio-group>
      </div>

      <!-- 预览态：渲染 Markdown（默认）。空内容给引导提示，点后即可编辑。 -->
      <div
        v-if="reqMode === 'preview'"
        v-loading="extracting"
        class="req-preview"
        @click="!requirement && (reqMode = 'edit')"
      >
        <div v-if="requirement" class="md-body" v-html="renderedReq" />
        <div v-else class="req-empty">暂无需求内容 · 点上方「编辑」输入，或用上方链接/文件抓取</div>
      </div>

      <!-- 编辑态：原始 Markdown 源码，可改。 -->
      <el-input
        v-else
        v-model="requirement"
        type="textarea"
        :autosize="{ minRows: 6, maxRows: 14 }"
        :placeholder="reqPlaceholder"
        :disabled="running"
        class="req-input"
        v-loading="extracting"
      />

      <div class="actions">
        <el-button
          type="primary"
          size="large"
          :loading="running"
          :disabled="!aiAvailable || !pid || !requirement.trim()"
          @click="generate"
        >
          <el-icon v-if="!running" class="btn-icon"><MagicStick /></el-icon>
          {{ running ? '生成中…' : '生成测试点' }}
        </el-button>
        <el-button v-if="running" size="large" @click="cancel">取消</el-button>
        <el-checkbox v-model="scenarioOnly" :disabled="running" class="scenario-only">
          仅生成场景组合用例
          <el-tooltip placement="top">
            <template #content>
              只产「多场景组合(前置状态分支)」维度的用例，聚焦不同前置状态下的分支差异。<br>
              产出更快、更聚焦；此模式<b>不生成 script</b>，采纳后到「用例库」点「保存并重生 script」逐条补齐。
            </template>
            <el-icon class="scenario-help"><QuestionFilled /></el-icon>
          </el-tooltip>
        </el-checkbox>
      </div>
    </el-card>

    <!-- 生成过程反馈 -->
    <el-card v-if="running" class="stream-card">
      <div class="running-head">
        <span class="pulse-dot" :class="{ queued: jobStatus === 'pending' }" />
        <el-tag v-if="jobStatus" :type="jobStatus === 'pending' ? 'warning' : 'success'" size="small" effect="light">
          {{ jobStatus === 'pending' ? '排队中' : '执行中' }}
        </el-tag>
        <span class="running-text">{{ phaseText }}</span>
        <span class="elapsed mono">{{ (elapsed / 1000).toFixed(1) }}s</span>
      </div>
      <el-progress :percentage="100" :indeterminate="true" :duration="3" :show-text="false" color="#00b386" />
      <pre v-if="rawStream" class="raw-stream">{{ rawStream }}</pre>
      <div v-else class="raw-hint">{{ statusHint }}</div>
    </el-card>

    <!-- 结果区 -->
    <el-card v-if="cases.length || viewingId" class="result-card">
      <template #header>
        <div class="card-head">
          <span class="result-title">测试点清单<template v-if="cases.length"> · {{ cases.length }} 条</template></span>
          <div class="result-tools">
            <el-select
              v-model="viewingId"
              placeholder="查看历史生成"
              clearable
              size="small"
              style="width:260px"
              @change="onViewHistory"
            >
              <el-option
                v-for="h in history"
                :key="h.id"
                :label="`#${h.id} · ${fmtTime(h.created_at)} · ${h.case_count}条 · ${h.status}`"
                :value="h.id"
              />
            </el-select>
          </div>
        </div>
      </template>

      <!-- 战绩统计条 -->
      <div v-if="meta" class="stat-strip">
        <div class="stat"><div class="stat-num">{{ meta.case_count ?? cases.length }}</div><div class="stat-label">测试点</div></div>
        <div class="stat"><div class="stat-num">{{ meta.duration_ms ? (meta.duration_ms / 1000).toFixed(1) + 's' : '—' }}</div><div class="stat-label">生成耗时</div></div>
        <div class="stat"><div class="stat-num">{{ meta.output_tokens ?? '—' }}</div><div class="stat-label">输出 tokens</div></div>
        <div class="stat"><div class="stat-num">{{ meta.cost_usd != null ? '$' + meta.cost_usd.toFixed(3) : '—' }}</div><div class="stat-label">成本</div></div>
        <div class="stat adopted"><div class="stat-num">{{ adoptedCount }}</div><div class="stat-label">已采纳</div></div>
      </div>

      <el-table :data="cases" size="small" border stripe class="case-table">
        <el-table-column label="维度" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="CAT_TYPE[row.category] || 'info'" effect="plain" size="small">{{ row.category || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="优先级" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="PRI_TYPE[(row.priority || '').toUpperCase()] || 'info'" size="small">{{ row.priority || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="引擎" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="PROVIDER_TYPE[row.provider] || 'info'" size="small" effect="plain">{{ row.provider || 'claude' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="测试点" min-width="200">
          <template #default="{ row }">
            <div>{{ row.title }}</div>
            <el-tooltip v-if="row.selector_fix" :content="row.kind_reason" placement="top">
              <el-tag type="warning" size="small" effect="plain" class="sel-fix-tag">
                补选择器可自动化<template v-if="row.selector_fix_keys && row.selector_fix_keys.length"> · 补: {{ row.selector_fix_keys.join(', ') }}</template>
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="步骤" min-width="220">
          <template #default="{ row }"><span class="multiline">{{ row.steps || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="预期结果" min-width="200">
          <template #default="{ row }"><span class="multiline">{{ row.expected || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="评审" width="220" align="center" fixed="right">
          <template #default="{ row }">
            <el-radio-group
              :model-value="row.review_status || 'pending'"
              size="small"
              :disabled="reviewingId === row.id"
              @change="(v) => reviewRow(row, v)"
              class="review-group"
            >
              <el-radio-button value="adopted" class="rv-adopted">采纳</el-radio-button>
              <el-radio-button value="rejected" class="rv-rejected">否决</el-radio-button>
              <el-radio-button value="pending" class="rv-pending">待定</el-radio-button>
            </el-radio-group>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { MagicStick, UploadFilled, Document, Connection, QuestionFilled } from '@element-plus/icons-vue'
import {
  listTasks, aiStatus, listAiTasks, listAiCases, listCases, reviewTestcase, streamTestcases,
  cancelAiJob,
  extractUrl, extractFile, getApiContract, listSelectors,
} from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { renderMarkdown } from '@/utils/markdown'
import TaskPicker from '@/components/TaskPicker.vue'

// 维度 / 优先级 → el-tag 配色
const CAT_TYPE = { 功能: 'primary', 边界: 'warning', 异常: 'danger', 兼容: 'info', 性能: 'success' }
const PRI_TYPE = { P0: 'danger', P1: 'warning', P2: 'primary', P3: 'info' }
// 生成引擎 → el-tag 配色（claude 暖色、deepseek 冷色，便于一眼区分）
const PROVIDER_TYPE = { claude: 'warning', deepseek: 'primary' }
// 引擎在选择器里的友好名 + 圆点色（圆点色与用例引擎 tag 呼应）；未知引擎回落 id + 灰点
const ENGINE_META = {
  claude: { label: 'Claude', dot: '#f59e0b' },
  deepseek: { label: 'DeepSeek', dot: '#3b82f6' },
}
const engineMeta = (id) => ENGINE_META[id] || { label: id, dot: '#94a3b8' }
const PHASES = ['正在拆解需求要点…', '主流程 / 边界 / 异常 / 场景组合 多路并行生成中…', '合并去重、校验脚本…', '评估优先级并成稿…']
// 前端轮询兜底阈值(告别无限转圈):后端单次生成硬上限 900s=15min,
// running 超 16min 必是执行卡死;总时长(含排队)超 30min 兜底停等,不武断判失败。
const RUNNING_TIMEOUT_MS = 16 * 60 * 1000
const TOTAL_TIMEOUT_MS = 30 * 60 * 1000
const MODES = [
  { k: 'text', label: '粘贴文本' },
  { k: 'url', label: '需求链接' },
  { k: 'file', label: '上传文档' },
]
const PLACEHOLDERS = {
  text: '粘贴需求描述、PRD 片段或验收标准……越具体，生成的测试点越贴合。',
  url: '点上方「抓取正文」后，提取的网页/飞书文档正文会填到这里，可编辑后再生成。',
  file: '上传文档后，解析出的文本会填到这里，可编辑后再生成。',
}
const DEMO = `用户登录功能：
1. 支持"账号 + 密码"登录；账号可为用户名或邮箱。
2. 密码错误时给出明确提示，不暴露账号是否存在。
3. 连续 5 次密码错误锁定账号 15 分钟，锁定期内拒绝登录。
4. 勾选"记住我"后登录态保持 30 天，否则关闭浏览器即失效。
5. 登录成功跳转到工作台首页。`

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const taskId = ref(null)
const targetPages = ref([])        // 生成的目标页面(收窄注入 key + 给该批用例打页面标)
const pageOptions = ref([])        // 目标页面下拉候选:当前项目选择器里已有的 page(去重)
const requirement = ref('')
// 仅生成场景组合用例:只产 scenario 维度、不产 script(采纳后到用例库逐条重生 script)
const scenarioOnly = ref(false)
const aiAvailable = ref(true)
const providers = ref([])          // [{id, available}] 来自 /ai/status
const engine = ref('claude')       // 当前选中的生成引擎
// 只展示「可用」引擎:未启用/不可用的引擎(如未配置的 deepseek)对用户完全不可见,而非置灰。
// 选择器仅在可用引擎 ≥2 个时才渲染(见模板);单引擎场景直接隐藏,界面更干净。
const availProviders = computed(() => providers.value.filter((p) => p.available))

// 需求内容展示模式：'preview' 渲染 Markdown（默认，好看）/ 'edit' textarea 可编辑。
// 抓取网页/飞书、上传文档、填充示例后自动切 preview 让用户先看渲染效果。
const reqMode = ref('preview')
const renderedReq = computed(() => renderMarkdown(requirement.value))

const inputType = ref('text')
const urlInput = ref('')
const extracting = ref(false)
const sourceInfo = ref(null)   // { label, chars }
const sourceUrl = ref('')      // 需求文档来源 url(extract-url 成功后记录,生成时随请求上报做需求追溯)

// 项目 api 契约(接口清单,成员可读,不含凭据)。有契约时生成页展示"可选接口清单"辅助 QA 圈定 api 用例范围。
const apiContract = ref({ has_contract: false, contract: '', base_url: '' })
const apiLines = computed(() =>
  (apiContract.value.contract || '').split('\n').map((s) => s.trim()).filter(Boolean))

const running = ref(false)
const rawStream = ref('')
const elapsed = ref(0)          // 毫秒(总耗时:从提交开始)
const phaseIdx = ref(0)
// 真实任务状态(后端 /ai-jobs/{id} 轮询回传):pending=排队 / running=执行中;queuePos=排队位次
const jobStatus = ref('')
const queuePos = ref(0)
const currentJobId = ref(null)  // 当前 job id,供「取消」调后端释放队列位
const runningSince = ref(0)     // 进入 running 的时刻(ms),做执行超时判定 + PHASES 动画基准
const abortReason = ref('')     // 中断原因:'user'(主动取消)/'timeout'(前端兜底停等),区分提示文案
const cases = ref([])
const meta = ref(null)
const history = ref([])
const viewingId = ref(null)
const taskCases = ref([])       // 选定关联任务后,该任务已有的历史用例(提示避免重复生成)
const taskCasesTotal = ref(0)   // 该任务历史用例总数(以后端 total 为准,不受分页截断)
const showTaskCases = ref(false)
const KIND_TYPE = { gui: 'success', api: 'primary', cli: 'warning', e2e: 'danger', manual: 'info' }
const KIND_LABEL = { gui: 'GUI', api: 'API', cli: 'CLI', e2e: 'E2E', manual: '人工' }
const reviewingId = ref(null)   // 正在提交评审的行 id，禁用该行控件避免连点

let timer = null
let ctrl = null

const adoptedCount = computed(() => cases.value.filter((c) => (c.review_status || (c.adopted ? 'adopted' : 'pending')) === 'adopted').length)
// 进度文案:真实状态驱动(不再是纯时间假动画)。pending 显示排队位次,running 显示多路生成动态文案。
const phaseText = computed(() => {
  if (jobStatus.value === 'pending') {
    return queuePos.value > 0 ? `排队中 · 前面还有 ${queuePos.value} 个任务` : '即将开始…'
  }
  if (jobStatus.value === 'running') {
    return PHASES[Math.min(phaseIdx.value, PHASES.length - 1)]
  }
  return '提交中…'
})
// 进度卡辅助说明:随状态给合理预期(替换原「30–60 秒」死文案)
const statusHint = computed(() => {
  if (jobStatus.value === 'pending') return '任务已提交，正在排队等待空闲执行槽…'
  if (jobStatus.value === 'running') return 'AI 正在多路并行生成，通常 1–3 分钟，长需求或高峰期更久，请稍候…'
  return '正在提交任务…'
})
const reqPlaceholder = computed(() => PLACEHOLDERS[inputType.value] || PLACEHOLDERS.text)

onMounted(async () => {
  // aiStatus 与项目列表无依赖，并行拉取
  const [aiRes, projRes] = await Promise.allSettled([aiStatus(), app.fetchProjects()])
  const aiData = aiRes.status === 'fulfilled' ? (aiRes.value || {}) : {}
  aiAvailable.value = !!aiData.available
  providers.value = aiData.providers || []
  // 默认选中:后端 default 若可用则用它,否则第一个可用引擎,再兜底 claude
  const firstAvail = providers.value.find((p) => p.available)
  const dft = providers.value.find((p) => p.id === aiData.default && p.available)
  engine.value = (dft || firstAvail || { id: 'claude' }).id
  projects.value = projRes.status === 'fulfilled' ? (projRes.value || []) : []
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await onProjectChange()
  }
})

async function onProjectChange() {
  taskId.value = null
  cases.value = []
  meta.value = null
  viewingId.value = null
  targetPages.value = []
  pageOptions.value = []
  apiContract.value = { has_contract: false, contract: '', base_url: '' }
  if (!pid.value) { tasks.value = []; history.value = []; return }
  setLastProjectId(pid.value)
  ;[tasks.value, history.value] = await Promise.all([
    listTasks({ project_id: pid.value }),
    listAiTasks(pid.value, 20),
  ])
  // api 契约独立拉取,失败不影响主流程(无契约就是常态)。
  try { apiContract.value = await getApiContract(pid.value) } catch { /* 忽略 */ }
  // 目标页面候选:从项目选择器(共享域)派生 distinct page,失败不影响生成。
  try {
    const data = await listSelectors(pid.value)
    pageOptions.value = [...new Set((data.shared || []).map((k) => k.page).filter(Boolean))].sort()
  } catch { pageOptions.value = [] }
}

// 引用某接口到需求(追加一行),切编辑态。
function refInterface(line) {
  requirement.value = requirement.value.trim() ? `${requirement.value.trimEnd()}\n${line}` : line
  reqMode.value = 'edit'
  ElMessage.success('已引用接口到需求')
}

// 插入 api 用例需求模板(关联接口 + 场景清单 + 业务判定,呼应设计稿 §8.2)。
const API_REQ_TEMPLATE = `## 关联接口
（从下方"接口清单"选择「引用」，或直接写 method + path）

## 测试场景
- 正常流程：
- 必填 / 参数校验：
- 重复 / 冲突：
- 越权 / 鉴权：

## 业务判定
- 成功：HTTP 200 且 code==0，返回 …
- 失败：错误码 … 对应 …
`
function insertApiTemplate() {
  requirement.value = requirement.value.trim()
    ? `${requirement.value.trimEnd()}\n\n${API_REQ_TEMPLATE}`
    : API_REQ_TEMPLATE
  reqMode.value = 'edit'
}

// 飞书文档主机（与后端 feishu.is_feishu_url 保持一致）
const FEISHU_HOSTS = ['feishu.cn', 'feishu.net', 'larksuite.com', 'larkoffice.com']
const URL_IN_TEXT_RE = /https?:\/\/[^\s"'<>()（）【】]+/g

function isFeishuUrl(u) {
  try {
    const host = new URL(u).hostname
    return FEISHU_HOSTS.some((h) => host === h || host.endsWith('.' + h))
  } catch { return false }
}

// 从一段正文里挑出第一个飞书文档链接（需求页常只放一个飞书跳转链接）
function findFeishuUrl(text) {
  const matches = (text || '').match(URL_IN_TEXT_RE) || []
  return matches.find(isFeishuUrl) || null
}

// 选中任务：带入其需求地址并自动抓取正文
async function onTaskChange(id) {
  // 选定关联任务:查该任务是否已有历史用例,有则提示避免重复生成
  taskCases.value = []
  taskCasesTotal.value = 0
  if (id && pid.value) {
    try {
      // 单任务用例量有限,取一页(上限 200)展示;数量以 total 为准(不受分页截断)
      const { items, total } = await listCases({ project_id: pid.value, task_id: id, limit: 200 })
      taskCases.value = items || []
      taskCasesTotal.value = total || 0
    } catch { taskCases.value = []; taskCasesTotal.value = 0 }
  }
  const t = tasks.value.find((x) => x.id === id)
  const url = (t?.requirement_url || '').trim()
  if (!url) return
  inputType.value = 'url'
  urlInput.value = url
  doExtractUrl(url)
}

function fillDemo() { requirement.value = DEMO; sourceInfo.value = null; sourceUrl.value = ''; reqMode.value = 'preview' }

async function doExtractUrl(overrideUrl) {
  let url = (typeof overrideUrl === 'string' ? overrideUrl : urlInput.value).trim()
  if (!url) return
  urlInput.value = url
  extracting.value = true
  try {
    let r = await extractUrl(url)
    // 抓回来的"正文"其实指向一个飞书文档链接 → 替换 URL 再抓一次
    if (!isFeishuUrl(url)) {
      const fs = findFeishuUrl(r.text)
      if (fs) {
        ElMessage.info('检测到飞书文档链接，跳转抓取…')
        url = fs
        urlInput.value = fs
        r = await extractUrl(fs)
      }
    }
    requirement.value = r.text
    sourceInfo.value = { label: r.title || url, chars: r.chars }
    sourceUrl.value = url   // 记录需求来源:生成时后端据此 upsert 需求实体并给用例挂 requirement_id
    reqMode.value = 'preview'   // 抓取后先看渲染效果
    ElMessage.success(`已提取 ${r.chars} 字`)
  } finally {
    extracting.value = false
  }
}

async function onFilePick(uploadFile) {
  const raw = uploadFile?.raw
  if (!raw) return
  if (raw.size > 5 * 1024 * 1024) { ElMessage.warning('文件过大（>5MB）'); return }
  extracting.value = true
  try {
    const r = await extractFile(raw)
    requirement.value = r.text
    sourceInfo.value = { label: r.filename, chars: r.chars }
    reqMode.value = 'preview'   // 解析后先看渲染效果
    ElMessage.success(`已解析 ${r.chars} 字`)
  } finally {
    extracting.value = false
  }
}

function generate() {
  if (!pid.value || !requirement.value.trim()) return
  if (!taskId.value) { ElMessage.warning('请先选择关联任务(必填)'); return }
  cases.value = []
  meta.value = null
  viewingId.value = null
  rawStream.value = ''
  elapsed.value = 0
  phaseIdx.value = 0
  jobStatus.value = ''
  queuePos.value = 0
  currentJobId.value = null
  runningSince.value = 0
  abortReason.value = ''
  running.value = true

  const startedAt = Date.now()
  timer = setInterval(() => {
    elapsed.value = Date.now() - startedAt
    // PHASES 动画只在进入 running 后推进(排队再久也不该把文案顶到最后一档)
    if (runningSince.value) phaseIdx.value = Math.floor((Date.now() - runningSince.value) / 12000)
    checkTimeout()
  }, 100)

  ctrl = new AbortController()
  streamTestcases(
    { project_id: pid.value, task_id: taskId.value, input_type: inputType.value, provider: engine.value, requirement: requirement.value, pages: targetPages.value.length ? targetPages.value : undefined,
      requirement_url: sourceUrl.value || undefined, requirement_title: sourceInfo.value?.label || undefined,
      scenario_only: scenarioOnly.value || undefined },
    {
      signal: ctrl.signal,
      // 轮询回传真实状态:排队位次 / 执行中;记录进入 running 的时刻供超时判定
      onTick: (job) => {
        jobStatus.value = job.status || ''
        queuePos.value = job.queue_position || 0
        if (job.id) currentJobId.value = job.id
        if (job.status === 'running' && !runningSince.value) runningSince.value = Date.now()
      },
      onDone: (evt) => {
        cases.value = evt.cases || []
        meta.value = evt.meta || null
        if (evt.status === 'failed') ElMessage.error(evt.msg || '生成失败，未得到有效测试点')
        else {
          ElMessage.success(`已生成 ${cases.value.length} 条测试点`)
          // 场景组合模式不产 script:提示去用例库逐条补齐(复用「保存并重生 script」)
          if (scenarioOnly.value && cases.value.length) {
            ElMessage.info({ message: '场景组合用例未生成 script，采纳后到「用例库」点「保存并重生 script」逐条补齐', duration: 8000 })
          }
          // 分片并行:个别维度没产出时用例仍落库,单独告警,别让人以为覆盖是全的
          if (evt.partialErrors?.length) {
            ElMessage.warning({ message: `部分维度未产出：${evt.partialErrors.join('；')}`, duration: 8000 })
          }
        }
        stop()
      },
      // 中断原因分流:主动取消 / 超时停等(均已各自提示过)不再重复弹错,只有真实失败才报错
      onError: (msg) => {
        if (!abortReason.value) ElMessage.error(msg || '生成失败')
        stop()
      },
    },
  )
}

// 前端超时兜底:running 超 16min(后端单次硬上限 15min,超了必是卡死)或总时长超 30min → 停止等待。
// 不武断判「失败」——running 的后端 job 即使前端不看了,worker 仍会跑完落库,故提示去历史查看/重试。
function checkTimeout() {
  if (!running.value || abortReason.value) return
  const now = Date.now()
  if (runningSince.value && now - runningSince.value > RUNNING_TIMEOUT_MS) {
    abortReason.value = 'timeout'
    ElMessage.warning({ message: '生成执行已超过 16 分钟仍未完成，疑似卡死。已停止等待——任务可能仍在后台完成，请稍后在「查看历史生成」中查看结果，或重试。', duration: 0, showClose: true })
    ctrl?.abort()
    return
  }
  if (elapsed.value > TOTAL_TIMEOUT_MS) {
    abortReason.value = 'timeout'
    ElMessage.warning({ message: '生成等待已超过 30 分钟仍未完成，已停止等待。可稍后在「查看历史生成」中查看结果，或重试。', duration: 0, showClose: true })
    ctrl?.abort()
  }
}

function stop() {
  running.value = false
  if (timer) { clearInterval(timer); timer = null }
  ctrl = null
  jobStatus.value = ''
  queuePos.value = 0
  if (pid.value) listAiTasks(pid.value, 20).then((h) => { history.value = h })
}

// 取消:排队中(pending)调后端取消并释放队列位;执行中(running)后端无法中断(409),仅停止前端等待。
async function cancel() {
  abortReason.value = 'user'
  const jid = currentJobId.value
  ctrl?.abort()   // 先停前端轮询(pollAiJob 下一轮检测 aborted 抛出 → onError,abortReason 已置不再弹错)
  if (jid) {
    try {
      await cancelAiJob(jid)
      ElMessage.info('已取消排队任务，队列位已释放')
    } catch {
      // 409=running 无法远程中断:后台仍会完成,前端仅停止等待
      ElMessage.info('任务执行中无法中断，已停止等待（后台仍会完成，可稍后在历史中查看）')
    }
  }
}

async function onViewHistory(id) {
  if (!id) { cases.value = []; meta.value = null; return }
  const h = history.value.find((x) => x.id === id)
  cases.value = await listAiCases(id)
  meta.value = h
    ? { case_count: h.case_count, duration_ms: h.duration_ms, cost_usd: h.cost_usd, output_tokens: h.output_tokens }
    : null
  rawStream.value = ''
}

// 三态评审：采纳/否决/待定。用后端返回的 data 回写本地行（review_status/reviewed_at/adopted）。
async function reviewRow(row, val) {
  const prev = { review_status: row.review_status, reviewed_at: row.reviewed_at, adopted: row.adopted }
  reviewingId.value = row.id
  // 乐观更新：先切控件选中态，失败再回滚
  row.review_status = val
  try {
    const data = await reviewTestcase(row.id, val)
    row.review_status = data.review_status
    row.reviewed_at = data.reviewed_at
    row.adopted = data.adopted
  } catch {
    Object.assign(row, prev)  // 回滚
    ElMessage.error('操作失败，请重试')
  } finally {
    reviewingId.value = null
  }
}

function fmtTime(s) {
  if (!s) return ''
  return String(s).replace('T', ' ').slice(5, 16)
}
</script>

<style scoped>
.ai-testgen { display: flex; flex-direction: column; gap: 16px; }
.actions { display: flex; align-items: center; gap: 12px; }
.scenario-only { margin-left: 4px; }
.scenario-help { color: #909399; margin-left: 2px; vertical-align: -2px; cursor: help; }
.task-cases-tip { margin: 4px 0 12px; }
.task-cases-list { margin-top: 6px; max-height: 180px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
.task-case-item { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #5a6b7b; }
.task-case-item .tc-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.title-wrap { display: flex; align-items: center; gap: 12px; }
.title-icon {
  font-size: 26px; color: #00b386;
  filter: drop-shadow(0 0 6px rgba(0, 179, 134, 0.35));
}
.title { font-size: 16px; font-weight: 600; color: #1f2d3d; }
.subtitle { font-size: 12px; color: #8a94a6; margin-top: 2px; }

/* 生成引擎分段切换 */
.engine-picker { display: flex; align-items: center; gap: 10px; }
.engine-label { font-size: 12px; color: #8a94a6; white-space: nowrap; }
.eng-opt { display: inline-flex; align-items: center; gap: 6px; }
.eng-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; flex: none;
  box-shadow: 0 0 0 2px rgba(255,255,255,.6); }
/* 分段整体：圆角胶囊、细边、浅底 */
.engine-seg :deep(.el-radio-button__inner) {
  border: none; background: transparent; color: #5b6472; font-weight: 500;
  padding: 5px 14px; box-shadow: none; transition: all .15s ease;
}
.engine-seg { background: #f0f2f5; border: 1px solid #e3e8ef; border-radius: 16px; padding: 2px; }
.engine-seg :deep(.el-radio-button:first-child .el-radio-button__inner),
.engine-seg :deep(.el-radio-button:last-child .el-radio-button__inner) { border-radius: 14px; }
.engine-seg :deep(.el-radio-button__inner:hover) { color: #1f2d3d; }
/* 选中态：signal-green 填充 + 白字 + 柔和阴影 */
.engine-seg :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: #00b386; color: #fff; border-radius: 14px;
  box-shadow: 0 1px 4px rgba(0,179,134,.35);
}
.engine-seg :deep(.el-radio-button.is-active .eng-dot) { box-shadow: 0 0 0 2px rgba(255,255,255,.5); }
/* 不可用引擎：降透明度 + 禁用光标 */
.engine-seg :deep(.el-radio-button.is-disabled .el-radio-button__inner) { opacity: .45; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }

.mode-row { display: flex; gap: 8px; margin-bottom: 12px; }
.extract-row-wrap { margin-bottom: 12px; }
.extract-row { display: flex; gap: 10px; }
.extract-tip { font-size: 12px; color: #a0a8b3; margin-top: 6px; }
.upload-row { margin-bottom: 12px; }
.upload-row :deep(.el-upload-dragger) { padding: 18px; }
.up-icon { font-size: 34px; color: #b3c0d1; }
.up-text { font-size: 13px; color: #606266; line-height: 1.6; }
.up-text span { font-size: 12px; color: #a0a8b3; }
.text-tools { margin-bottom: 12px; }
.source-info {
  display: flex; align-items: center; gap: 6px; margin-bottom: 10px;
  font-size: 12px; color: #00926e;
  background: rgba(0,179,134,.06); border: 1px solid rgba(0,179,134,.2);
  border-radius: 4px; padding: 6px 10px;
}
.form-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.page-hint { margin: -4px 0 12px; font-size: 12px; color: #90a4ae; line-height: 1.5; }
.req-input { font-size: 14px; }

/* api 用例引导面板 */
.api-guide { margin-bottom: 12px; }
.api-collapse { border: 1px solid #e3e8ef; border-radius: 6px; }
.api-collapse :deep(.el-collapse-item__header) { padding: 0 12px; height: 40px; line-height: 40px; }
.api-guide-icon { color: #00b386; margin-right: 6px; }
.api-guide-title { font-size: 13px; font-weight: 600; color: #1f2d3d; }
.api-guide-sub { font-size: 12px; color: #a0a8b3; margin-left: 10px; }
.api-line { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 3px 12px; }
.api-line:hover { background: #f5f7fa; }
.api-code { background: #f2f4f7; border-radius: 3px; padding: 2px 8px; font-size: 12px; color: #475569;
  font-family: 'JetBrains Mono', ui-monospace, monospace; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tpl-btn { margin-top: 6px; }
.api-guide-empty {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 12px; color: #90a4ae;
  background: rgba(0,179,134,.04); border: 1px dashed rgba(0,179,134,.25);
  border-radius: 6px; padding: 8px 12px;
}
.api-guide-empty .el-icon { color: #00b386; }
.api-guide-link { color: #00926e; text-decoration: none; font-weight: 500; }
.api-guide-link:hover { text-decoration: underline; }
.req-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.req-label { font-size: 13px; font-weight: 600; color: #1a1d21; }
.req-preview {
  min-height: 140px; max-height: 340px; overflow: auto;
  border: 1px solid #dcdfe6; border-radius: 4px; padding: 12px 14px;
  background: #fff; font-size: 14px; line-height: 1.7; cursor: text;
}
.req-empty { color: #a8abb2; font-size: 13px; cursor: pointer; }
/* Markdown 渲染正文排版 */
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3),
.md-body :deep(h4) { margin: 12px 0 6px; font-weight: 600; line-height: 1.3; }
.md-body :deep(h1) { font-size: 20px; }
.md-body :deep(h2) { font-size: 17px; }
.md-body :deep(h3) { font-size: 15px; }
.md-body :deep(h4) { font-size: 14px; }
.md-body :deep(p) { margin: 6px 0; }
.md-body :deep(ul), .md-body :deep(ol) { margin: 6px 0; padding-left: 22px; }
.md-body :deep(li) { margin: 2px 0; }
.md-body :deep(a) { color: #00926e; text-decoration: none; }
.md-body :deep(a:hover) { text-decoration: underline; }
.md-body :deep(code) {
  background: #f2f4f7; border-radius: 3px; padding: 1px 5px;
  font-family: var(--tech-mono, monospace); font-size: 13px;
}
.md-body :deep(pre) {
  background: #f5f7fa; border-radius: 6px; padding: 10px 12px; overflow: auto;
}
.md-body :deep(pre code) { background: none; padding: 0; }
.md-body :deep(blockquote) {
  margin: 8px 0; padding: 4px 12px; color: #5a6b7b;
  border-left: 3px solid #d6e9e2; background: rgba(0,179,134,.04);
}
.md-body :deep(table) { border-collapse: collapse; margin: 8px 0; width: 100%; }
.md-body :deep(th), .md-body :deep(td) { border: 1px solid #dcdfe6; padding: 5px 9px; text-align: left; }
.md-body :deep(th) { background: #f5f7fa; font-weight: 600; }
.md-body :deep(img) { max-width: 100%; }
.md-body :deep(hr) { border: none; border-top: 1px solid #e4e7ed; margin: 12px 0; }
.actions { margin-top: 14px; display: flex; gap: 12px; }
.btn-icon { margin-right: 4px; }

/* 生成过程 */
.stream-card :deep(.el-card__body) { padding: 16px 20px; }
.running-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.running-text { font-size: 14px; color: #303133; flex: 1; }
.elapsed { font-size: 13px; color: #00926e; }
.pulse-dot {
  width: 10px; height: 10px; border-radius: 50%; background: #00b386;
  box-shadow: 0 0 0 0 rgba(0, 179, 134, 0.6); animation: pulse 1.4s infinite;
}
/* 排队态:琥珀色脉冲,与「执行中」绿色一眼区分 */
.pulse-dot.queued {
  background: #e6a23c;
  box-shadow: 0 0 0 0 rgba(230, 162, 60, 0.6); animation: pulse-queued 1.4s infinite;
}
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(0, 179, 134, 0.5); }
  70% { box-shadow: 0 0 0 10px rgba(0, 179, 134, 0); }
  100% { box-shadow: 0 0 0 0 rgba(0, 179, 134, 0); }
}
@keyframes pulse-queued {
  0% { box-shadow: 0 0 0 0 rgba(230, 162, 60, 0.5); }
  70% { box-shadow: 0 0 0 10px rgba(230, 162, 60, 0); }
  100% { box-shadow: 0 0 0 0 rgba(230, 162, 60, 0); }
}
.raw-stream {
  margin-top: 12px; max-height: 220px; overflow: auto;
  background: #0f1c2e; color: #7fe7c4; border-radius: 6px; padding: 12px;
  font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px;
  white-space: pre-wrap; word-break: break-all;
}
.raw-hint { margin-top: 10px; font-size: 13px; color: #909399; }

/* 战绩统计条 */
.stat-strip {
  display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap;
}
.stat {
  flex: 1; min-width: 96px; text-align: center;
  padding: 12px 8px; border-radius: 8px;
  background: linear-gradient(160deg, #f3fbf8, #eaf5ff);
  border: 1px solid #e3eef0;
}
.stat-num { font-size: 22px; font-weight: 700; color: #00926e; font-family: 'JetBrains Mono', ui-monospace, monospace; }
.stat-label { font-size: 12px; color: #8a94a6; margin-top: 4px; }
.stat.adopted .stat-num { color: #3b9ad9; }

.result-title { font-weight: 600; color: #1f2d3d; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.sel-fix-tag { margin-top: 4px; cursor: help; display: inline-block; height: auto; line-height: 1.5; white-space: normal; padding: 2px 6px; }
.case-table { margin-top: 4px; }

/* 三态评审控件：采纳=青绿 / 否决=danger / 待定=灰，与 Dashboard .dtag 配色一致 */
.review-group { --el-border-radius-base: 4px; }
.review-group :deep(.el-radio-button__inner) {
  font-family: var(--tech-mono);
  letter-spacing: .5px;
  padding: 5px 12px;
}
/* 采纳（选中）→ 青绿 */
.review-group :deep(.rv-adopted.is-active .el-radio-button__inner) {
  background: var(--tech-signal);
  border-color: var(--tech-signal);
  box-shadow: -1px 0 0 0 var(--tech-signal);
  color: #fff;
}
/* 否决（选中）→ danger 红 */
.review-group :deep(.rv-rejected.is-active .el-radio-button__inner) {
  background: var(--tech-danger);
  border-color: var(--tech-danger);
  box-shadow: -1px 0 0 0 var(--tech-danger);
  color: #fff;
}
/* 待定（选中）→ 灰 */
.review-group :deep(.rv-pending.is-active .el-radio-button__inner) {
  background: var(--tech-muted);
  border-color: var(--tech-muted);
  box-shadow: -1px 0 0 0 var(--tech-muted);
  color: #fff;
}
</style>
