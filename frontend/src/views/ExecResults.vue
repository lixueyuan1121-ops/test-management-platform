<template>
  <div class="exec-results functional-workspace">
    <WorkspacePage title="执行结果">
      <template #actions>
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <span class="refresh-hint">手动刷新</span>
            <el-button size="small" :loading="loading" :icon="Refresh" aria-label="刷新执行结果" title="刷新执行结果" @click="load" />
      </template>
      <template #filters>
            <TaskPicker v-model="taskId" :tasks="tasks" placeholder="任务" width="220px" @change="reload" />
            <el-select v-model="runner" placeholder="执行设备" size="small" clearable style="width:150px" @change="reload">
              <el-option v-for="rn in runners" :key="rn" :label="rn" :value="rn" />
            </el-select>
            <el-select v-model="runStatus" placeholder="执行状态" size="small" clearable style="width:150px" @change="reload">
              <el-option label="待执行 / 执行中" value="active" />
              <el-option label="待执行" value="pending" />
              <el-option label="执行中" value="running" />
            </el-select>
            <el-select v-model="verdict" placeholder="结果" size="small" clearable style="width:110px" @change="reload">
              <el-option label="通过" value="pass" />
              <el-option label="失败" value="fail" />
              <el-option label="选择器阻塞" value="blocked" />
            </el-select>
      </template>

      <el-tag v-if="batchFilter" closable @close="batchFilter = null; reload()" style="margin-bottom:12px">批次 {{ batchFilter }}</el-tag>
      <el-alert v-if="activeCount" type="info" :closable="false" style="margin-bottom:12px"
        :title="`本页有 ${activeCount} 条待执行或运行中的任务，可在展开明细的操作列手动终止。状态变化后请点击右上角刷新。`" />
      <el-empty v-if="!batches.length" :description="loading ? '加载中…' : '暂无执行记录'" :image-size="70" />

      <!-- 按批次分组:每批一块,组头显汇总,展开看该批每条用例;单条点「报告」下钻逐步截图 -->
      <el-collapse v-else v-model="activeBatches" v-loading="loading">
        <el-collapse-item v-for="b in batches" :key="b.id" :name="b.id">
          <template #title>
            <div class="batch-head">
              <el-tag :type="b.failed ? 'danger' : (b.blocked ? 'warning' : (b.total > 0 && b.passed === b.total ? 'success' : 'info'))" size="small" effect="dark">
                {{ b.failed ? '有失败' : (b.blocked ? '有阻塞' : (b.total > 0 && b.passed === b.total ? '本页全部通过' : (b.cancelled === b.total ? '已终止' : '尚未全部判定'))) }}
              </el-tag>
              <span class="batch-id">{{ b.label }}</span>
              <span class="batch-stat">
                本页 {{ b.total }} · <b class="ok">{{ b.passed }} 过</b> · <b class="ng">{{ b.failed }} 失</b>
                <template v-if="b.blocked"> · <b class="blk">{{ b.blocked }} 阻塞</b></template>
                <template v-if="b.cancelled"> · {{ b.cancelled }} 已终止</template>
                <template v-if="b.flaky"> · <b class="flk" title="重试后通过(不稳定)">{{ b.flaky }} 抖动</b></template>
                · 本页功能通过率 {{ b.rate }}%
              </span>
              <span class="batch-meta">{{ b.runner }} · {{ b.durationText }} · {{ fmtTime(b.time) }}</span>
            </div>
          </template>

          <el-table :data="b.rows" size="small" border stripe empty-text="无记录">
            <el-table-column label="结果" width="130" align="center">
              <template #default="{ row }">
                <el-tag :type="resultType(row)" size="small">
                  {{ resultLabel(row) }}
                </el-tag>
                <el-tag v-if="row.flaky" type="warning" size="small" effect="plain" class="chain-tag"
                        title="首试失败,自动重试后通过——结果不稳定(flaky),建议排查环境/时序">抖动</el-tag>
                <el-tag v-else-if="row.retry_of" type="info" size="small" effect="plain" class="chain-tag"
                        :title="`第 ${row.attempt} 次尝试(自动重试)`">重试</el-tag>
                <el-tag v-else-if="row._superseded" type="info" size="small" effect="plain" class="chain-tag"
                        title="该次失败已自动重试,以重试结果为准(不计入批次统计)">已重试</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="66" align="center">
              <template #default="{ row }"><el-tag :type="KIND_TYPE[row.kind] || 'info'" size="small" effect="plain">{{ KIND_LABEL[row.kind] || row.kind }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="title" label="用例" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.title || `#${row.case_id ?? '—'}` }}</template>
            </el-table-column>
            <el-table-column label="原因/结论" min-width="240">
              <template #default="{ row }">
                <el-tooltip v-if="row.triage_kind" placement="top" :content="triageTip(row)">
                  <el-tag :type="TRIAGE_TYPE[row.triage_kind] || 'info'" size="small" effect="dark" class="triage-tag">
                    归因:{{ TRIAGE_LABEL[row.triage_kind] || row.triage_kind }}
                  </el-tag>
                </el-tooltip>
                <span class="reason">{{ row.reason || '—' }}</span>
                <template v-if="isBlocked(row)">
                  <el-tooltip
                    v-if="row._fixKeys && row._fixKeys.length"
                    effect="light" placement="right" :show-after="150" :hide-after="100"
                    popper-class="missing-selector-tooltip" @before-show="loadFixTargets(row)"
                  >
                    <template #content>
                      <div style="max-width:min(560px,75vw);max-height:55vh;overflow:auto">
                        <div v-for="key in (row._fixKeys || [])" :key="key" style="padding:6px 0">
                          <SelectorTargetNotes :selector-key="key" :notes="row._fixNotes?.[key] || [{ title: '脚本未说明具体元素，请补充对应操作描述' }]" />
                        </div>
                      </div>
                    </template>
                    <el-link type="warning" class="fix-link" @click="fixSelector(row)">补齐选择器</el-link>
                  </el-tooltip>
                  <el-link v-else type="warning" class="fix-link" @mouseenter="loadFixTargets(row)" @focus="loadFixTargets(row)" @click="fixSelector(row)">补齐选择器</el-link>
                </template>
              </template>
            </el-table-column>
            <el-table-column label="耗时" width="80" align="center">
              <template #default="{ row }">{{ row.duration_ms != null ? (row.duration_ms / 1000).toFixed(1) + 's' : '—' }}</template>
            </el-table-column>
            <el-table-column label="报告" width="80" align="center">
              <template #default="{ row }">
                <el-link v-if="hasReport(row)" type="primary" @click="showReport(row)">报告</el-link>
                <el-link v-else-if="row.evidence_url" type="info" @click="showEvidence(row)">证据</el-link>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column label="执行时间" width="150">
              <template #default="{ row }">{{ fmtTime(row.updated_at || row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="180" align="center" fixed="right">
              <template #default="{ row }">
                <el-link v-if="!isActive(row)" type="primary" @click="openCorrect(row)">纠偏</el-link>
                <el-link v-if="!isActive(row)" type="warning" class="op-retry" @click="onRetry(row)">重试</el-link>
                <el-button v-if="isActive(row)" size="small" type="danger" plain
                  :loading="stopping.has(row.run_id)" :disabled="row.cancel_requested || !canStop(row)"
                  :title="!canStop(row) ? '当前项目为只读权限，无法终止' : row.cancel_requested ? '已请求终止，点击右上角刷新查看结果' : '停止本条用例执行'"
                  @click="onStop(row)">{{ row.cancel_requested ? '终止中…' : '手动终止' }}</el-button>
                <el-link v-if="canTriage(row)" type="warning" class="triage-btn"
                         :class="{ busy: row._triaging }" @click="doTriage(row)">
                  {{ row._triaging ? `归因中${row._pos > 0 ? `（排队第 ${row._pos + 1} 位）` : '…'}` : 'AI归因' }}
                </el-link>
              </template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>

      <div class="foot-hint">本页 {{ rows.length }} 条 / {{ batches.length }} 个批次；批次统计仅包含本页记录</div>
      <el-pagination v-model:current-page="page" :page-size="20" :total="total"
        layout="total, prev, pager, next, jumper" background @current-change="load" />
    </WorkspacePage>

    <!-- 单条执行的逐步报告(含截图) -->
    <el-dialog v-model="rep.visible" title="执行报告" width="720px" top="6vh">
      <div v-if="rep.row" v-loading="rep.loading" class="rep">
        <el-alert v-if="rep.error" type="error" :closable="false" title="报告加载失败">
          <el-button size="small" @click="showReport(rep.row)">重试加载报告</el-button>
        </el-alert>
        <div class="rep-head">
          <el-tag :type="resultType(rep.row)" size="small" effect="dark">
            {{ resultLabel(rep.row) }}
          </el-tag>
          <b>{{ rep.row.title || `#${rep.row.case_id}` }}</b>
          <span class="rep-meta">{{ KIND_LABEL[rep.row.kind] || rep.row.kind }} · {{ rep.row.runner }} · {{ rep.row.duration_ms != null ? (rep.row.duration_ms / 1000).toFixed(1) + 's' : '—' }}</span>
        </div>
        <p v-if="rep.row.reason" class="rep-reason">{{ rep.row.reason }}</p>

        <ol class="steps">
          <li v-for="(s, i) in (rep.row.report || [])" :key="i" class="step">
            <div class="step-line">
              <el-icon v-if="s.ok" class="ok"><CircleCheck /></el-icon>
              <el-icon v-else class="ng"><CircleClose /></el-icon>
              <span class="step-no">{{ s.no ?? i + 1 }}</span>
              <span class="step-act">{{ s.action }}</span>
              <span class="step-desc">{{ s.desc || '' }}</span>
            </div>
            <div v-if="s.error" class="step-err">{{ s.error }}</div>
            <div v-if="s.preparation" class="step-check">
              <div>{{ s.preparation.reason }}</div>
              <div v-for="(data, n) in (s.preparation.created || [])" :key="n">已创建：{{ data.type }} · {{ data.name }}</div>
              <details v-if="s.preparation.operations?.length"><summary>查看条件补齐过程</summary>
                <div v-for="(op, n) in s.preparation.operations" :key="n">{{ op.tool }} · {{ JSON.stringify(op.input) }}</div>
              </details>
            </div>
            <el-link v-if="s.trace_url" type="primary" @click="downloadTrace(rep.row.run_id)">下载执行追踪</el-link>
            <div v-if="s.trace_error" class="step-err">执行追踪未保存：{{ s.trace_error }}</div>
            <div v-if="s.check" class="step-check">
              期望{{ s.check.negate ? '不' : '' }}{{ s.check.mode === 'contains' ? '包含' : '等于' }}
              <code class="exp">{{ s.check.expected }}</code>
              <span class="vs">实际</span>
              <code class="act">{{ s.check.actual || '(空)' }}</code>
            </div>
            <img v-if="s.shot" :src="s.shot" class="step-shot" alt="步骤截图" @click="zoom(s.shot)" />
          </li>
        </ol>
        <el-empty v-if="!rep.loading && !rep.error && !(rep.row.report || []).length" description="该次执行无逐步报告" :image-size="60" />
      </div>
    </el-dialog>

    <!-- 截图放大 -->
    <el-dialog v-model="shot.visible" title="截图" width="80%" top="4vh">
      <img v-if="shot.url" :src="shot.url" class="shot-full" alt="截图" />
    </el-dialog>

    <!-- 人工纠偏执行结果 -->
    <el-dialog v-model="correct.visible" title="人工纠偏结果" width="440px">
      <div v-if="correct.row" class="correct">
        <p class="correct-case"><b>{{ correct.row.title || `#${correct.row.case_id}` }}</b></p>
        <p class="correct-cur">当前判定：<el-tag :type="resultType(correct.row)" size="small">{{ resultLabel(correct.row) }}</el-tag></p>
        <el-form label-width="72px">
          <el-form-item label="纠正为">
            <el-radio-group v-model="correct.verdict">
              <el-radio value="pass">通过</el-radio>
              <el-radio value="fail">失败(真 bug)</el-radio>
              <el-radio value="blocked">选择器阻塞</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="correct.reason" type="textarea" :rows="2" placeholder="纠偏原因(可选)，会记入结果说明" maxlength="2000" />
          </el-form-item>
        </el-form>
        <!-- 维护用例:文案/步骤变化导致的真失败,改正文+重生 script,下次不再需纠偏 -->
        <el-divider content-position="left" class="maint-divider">顺带维护用例(可选)</el-divider>
        <el-checkbox v-model="correct.maintain" :disabled="!canMaintain(correct.row)">
          用例已过时(如断言文案变了)，更新用例并重生 script
        </el-checkbox>
        <div v-if="correct.maintain && canMaintain(correct.row)" class="maint-box">
          <el-form label-width="72px">
            <el-form-item label="预期">
              <el-input v-model="correct.expected" type="textarea" :rows="2" placeholder="改成最新的预期/文案" />
            </el-form-item>
            <el-form-item label="步骤">
              <el-input v-model="correct.steps" type="textarea" :rows="2" placeholder="如步骤也变了才改，否则留空不动" />
            </el-form-item>
            <el-form-item label="script(JSON)">
              <el-input v-model="correct.script" type="textarea" :rows="6" spellcheck="false" style="font-family:monospace" placeholder="结构化步骤 JSON 数组;留空则不改 script。手改此处将优先于按正文重生 script" />
            </el-form-item>
          </el-form>
          <span class="correct-hint">改了「预期/步骤」而未手改 script → 保存后按新正文重新生成 script;若手改了上面的 script → 直接保存你写的 script(不再 AI 重生)。仅 gui/e2e/api。</span>
        </div>
        <span v-else-if="correct.maintain" class="correct-hint">该用例类型不支持重生 script(仅 gui/e2e/api)。</span>
        <p class="correct-hint" style="margin-top:10px">纠偏后结果说明会打「[人工纠偏]」前缀，并同步回填对应验收清单项。</p>
      </div>
      <template #footer>
        <el-button @click="correct.visible = false">取消</el-button>
        <el-button type="primary" :loading="correct.saving" @click="saveCorrect">保存纠偏</el-button>
      </template>
    </el-dialog>

    <!-- 旧记录:仅本地路径 -->
    <el-dialog v-model="ev.visible" title="执行证据" width="480px">
      <p class="ev-path">{{ ev.path }}</p>
      <el-alert type="info" :closable="false" show-icon>证据为执行机本地路径(截图等),在对应设备上查看。</el-alert>
    </el-dialog>
  </div>
</template>

<script setup>
import { execResultBatches } from '@/utils/execResultBatches'
import WorkspacePage from '@/components/WorkspacePage.vue'
import '@/styles/workspace-overlays.css'
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, CircleCheck, CircleClose } from '@element-plus/icons-vue'
import { listTasks, listExecHistory, getExecRun, correctExecVerdict, getTestcase, updateTestcase, genTestcaseScript, retryExecRun, cancelExecRun, triageExecRun } from '@/api'
import { collectMissingKeys } from '@/utils/bulk-fix-selectors'
import { describeSelectorTarget } from '@/utils/selector-target-description'
import SelectorTargetNotes from '@/components/SelectorTargetNotes.vue'
import { useAppStore } from '@/store/app'
import { useAuthStore } from '@/store/auth'
import http from '@/api/http'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import TaskPicker from '@/components/TaskPicker.vue'

const router = useRouter()
const route = useRoute()

async function downloadTrace(runId) {
  try {
    const blob = await http.get(`/exec-queue/${runId}/trace`, { responseType: 'blob' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `exec-${runId}-trace.zip`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch { /* http reports download errors */ }
}

const KIND_TYPE = { gui: 'success', api: 'primary', cli: 'warning', e2e: 'danger', manual: 'info' }
const KIND_LABEL = { gui: 'GUI', api: 'API', cli: 'CLI', e2e: 'E2E', manual: '人工' }
const STATUS_LABEL = { pending: '待执行', running: '执行中', passed: '通过', failed: '失败', blocked: '选择器阻塞' }
// AI 失败归因四类(对标 ReportPortal 分类,映射本仓库口径)
const TRIAGE_LABEL = { selector: '选择器', environment: '环境', assertion: '用例过时', bug: '疑似缺陷' }
const TRIAGE_TYPE = { selector: 'warning', environment: 'info', assertion: 'primary', bug: 'danger' }

const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const taskId = ref(null)
const runner = ref(null)
const verdict = ref(null)
const runStatus = ref(null)
const rows = ref([])
const page = ref(1)
const total = ref(0)
const runners = ref([])
const batchFilter = ref(route.query.batch_id || null)
const loading = ref(false)
const ev = ref({ visible: false, path: '' })
const rep = ref({ visible: false, row: null, loading: false, error: false })
const shot = ref({ visible: false, url: '' })
const correct = ref({ visible: false, row: null, verdict: 'pass', reason: '', saving: false, maintain: false, expected: '', steps: '', script: '', scriptOrig: '' })
const activeBatches = ref([])
const app = useAppStore()
const auth = useAuthStore()
const canStop = row => typeof row.can_cancel === 'boolean' ? row.can_cancel : ['admin', 'member'].includes(auth.roleIn(Number(row.project_id ?? pid.value)))
const stopping = ref(new Set())
const isActive = row => ['pending', 'running'].includes(row.status)
const activeCount = computed(() => rows.value.filter(isActive).length)
let listRequest = 0
async function onStop(row) {
  if (stopping.value.has(row.run_id) || row.cancel_requested) return
  stopping.value.add(row.run_id)
  try {
    const updated = await cancelExecRun(row.run_id)
    rows.value = rows.value.map(item => item.run_id === row.run_id ? { ...item, ...updated } : item)
    ElMessage.success(updated.cancel_requested ? '已请求终止；请保持 Runner 在线，点击刷新查看确认结果' : '已终止')
  } catch { /* API interceptor displays the failure */ }
  finally { stopping.value.delete(row.run_id) }
}



const hasReport = row => row.has_report ?? (Array.isArray(row.report) && row.report.length > 0)

// 按 batch_id 分组;无 batch_id 的老记录归到 "(未分批)"。组内保持后端的时间倒序。
// 组间按该组最新一条执行时间倒序。汇总数(总/过/失/通过率/耗时和/设备/时间)现算。
// 重试链聚合:被自动重试覆盖的原始行(id 出现在他行 retry_of)标 _superseded,
// 展示保留(留痕)但**不计入**批次汇总——与后端批次/门禁/质量卡同口径。
const batches = computed(() => {
  return execResultBatches(rows.value)
})

onMounted(async () => {
  projects.value = await app.fetchProjects()
  if (projects.value.length) {
    // 深链支持:?project_id=&batch_id=(测试计划历史/飞书告警卡跳转);无 query 走默认项目
    const qPid = Number(route.query.project_id)
    pid.value = (qPid && projects.value.some((p) => p.id === qPid)) ? qPid : pickDefaultProjectId(projects.value)
    await onProjectChange()
    if (tasks.value.some(t => t.id === Number(route.query.task_id))) { taskId.value = Number(route.query.task_id); await load() }
    const qBatch = route.query.batch_id
    if (qBatch && batches.value.some((b) => b.id === qBatch)) activeBatches.value = [qBatch]
  }
})

async function onProjectChange() {
  taskId.value = null; runner.value = null; verdict.value = null; runStatus.value = null
  page.value = 1
  rows.value = []; total.value = 0; runners.value = []; ++listRequest
  if (!pid.value) { tasks.value = []; loading.value = false; return }
  if (Number(route.query.project_id) !== pid.value) batchFilter.value = null
  setLastProjectId(pid.value)
  const project = pid.value
  await Promise.all([
    listTasks({ project_id: project }).then(items => { if (pid.value === project) tasks.value = items }),
    load(),
  ])
}

async function reload() {
  page.value = 1
  await load()
}

async function load(silent = false) {
  silent = silent === true
  if (!pid.value) return
  const request = ++listRequest
  loading.value = true
  try {
    const queryProject = pid.value
    const result = await listExecHistory({
      project_id: pid.value,
      task_id: taskId.value || undefined,
      runner: runner.value || undefined,
      verdict: verdict.value || undefined,
      status: runStatus.value || undefined,
      summary: true,
      page: page.value,
      page_size: 20,
      batch_id: batchFilter.value || undefined,
    })
    if (pid.value !== queryProject || request !== listRequest) return
    rows.value = result.items || []
    total.value = result.total || 0
    runners.value = result.runners || []
    const lastPage = Math.max(1, Math.ceil(total.value / 20))
    if (page.value > lastPage) { page.value = lastPage; await load(); return }
    if (!silent) {
      const active = batches.value.filter(b => b.rows.some(isActive))
      activeBatches.value = (active.length ? active : batches.value.slice(0, 1)).map(b => b.id)
    }
  } finally { if (request === listRequest) loading.value = false }
}

async function showReport(row) {
  rep.value = { visible: true, row, loading: true, error: false }
  try {
    const detail = await getExecRun(row.run_id)
    if (rep.value.row?.run_id === row.run_id) rep.value.row = { ...row, ...detail }
  } catch { if (rep.value.row?.run_id === row.run_id) rep.value.error = true }
  finally { if (rep.value.row?.run_id === row.run_id) rep.value.loading = false }
}
function showEvidence(row) { ev.value = { visible: true, path: row.evidence_url } }
function zoom(url) { shot.value = { visible: true, url } }
function fmtTime(s) { return s ? String(s).replace('T', ' ').slice(0, 16) : '—' }

// 三态结果:pass 通过 / fail 功能失败(真 bug) / blocked 选择器阻塞(不计功能失败率)。
// 后端把 selector 阻塞的 verdict 直接写成 blocked;老数据可能仅 status=blocked,一并识别。
function isBlocked(row) { return row.fail_kind !== 'cancelled' && (row.verdict === 'blocked' || row.status === 'blocked') }

// ---- AI 失败归因:失败/阻塞可归因;结果落行上(徽标+悬浮详情),失败可重试 ----
function canTriage(row) { return row.status === 'failed' || isBlocked(row) }
function triageTip(row) {
  const t = row.triage || {}
  const conf = t.confidence != null ? ` 置信 ${Math.round(t.confidence * 100)}%` : ''
  return `${t.reason || ''}${conf}${t.suggestion ? `\n建议:${t.suggestion}` : ''}` || '无详情'
}
async function doTriage(row) {
  if (row._triaging) return
  row._triaging = true
  row._pos = 0
  try {
    // 入队+轮询;onTick 回传排队位次驱动按钮文案。错误在此自行提示(silent 已让拦截器不弹)。
    const res = await triageExecRun(row.run_id, undefined, {
      onTick: (job) => { row._pos = job.queue_position || 0 },
    })
    row.triage_kind = res.kind
    row.triage = res
    ElMessage.success(`归因完成:${TRIAGE_LABEL[res.kind] || res.kind}(置信 ${Math.round((res.confidence || 0) * 100)}%)`)
  } catch (e) {
    ElMessage.error(e?.message || '归因失败')
  } finally {
    row._triaging = false
  }
}
function resultType(row) {
  if (row.cancel_requested || row.fail_kind === 'cancelled') return 'info'
  if (row.verdict === 'pass') return 'success'
  if (isBlocked(row)) return 'warning'
  if (row.verdict === 'fail') return 'danger'
  return 'info'
}
function resultLabel(row) {
  if (row.cancel_requested) return '终止中'
  if (row.fail_kind === 'cancelled') return '已终止'
  if (row.verdict === 'pass') return '通过'
  if (isBlocked(row)) return '选择器阻塞'
  if (row.verdict === 'fail') return '失败'
  return STATUS_LABEL[row.status] || row.status
}
// 悬浮「补齐选择器」时懒加载说明:按 case_id 拉用例详情,取 selector_fix_keys + 对每个 key
// 用 describeSelectorTarget 从 script 抽出"补哪个 key、对应哪个 DOM/操作"。结果缓存到 row 上,
// 移开再悬浮不重复请求(除非上次出错)。执行记录行本身无这些字段,必须现拉详情。
async function loadFixTargets(row) {
  if (row._fixLoading || (row._fixNotes && !row._fixError)) return
  const cid = row.case_id ?? row.test_case_id
  if (!cid) { row._fixKeys = []; row._fixNotes = {}; return }
  row._fixLoading = true; row._fixError = false
  try {
    const detail = await getTestcase(cid)
    // 待补 key 取**当前仍未注册**的:后端 missing_selector_keys(script 引用了但注册表现在没有的)最准,
    // 优先用它;它是明确的空数组=全已注册→不显示待补;后端没这字段(旧版)才退回生成期 keys / script 兜底。
    // 修「待补单列出用例所有 key、实际只缺一个」——链路里已补好的 key 不再带过去,避免重复添加。
    const keys = Array.isArray(detail.missing_selector_keys)
      ? detail.missing_selector_keys
      : (detail.selector_fix_keys?.length ? detail.selector_fix_keys : scriptKeys(detail.script))
    row._fixKeys = keys
    row._fixNotes = Object.fromEntries(keys.map((key) => {
      const notes = describeSelectorTarget(key, detail.script)
      return [key, notes.length ? notes : [{ title: '脚本未说明具体元素，请补充对应操作描述' }]]
    }))
  } catch { row._fixError = true }
  finally { row._fixLoading = false }
}

// 从结构化 script 抽出所有 target.key(去重),作为 selector_fix_keys 为空时的兜底 key 来源。
// blocked 常是运行期判定,用例此刻未必有生成期的 selector_fix_keys;但脚本里引用的 key 就是本次要补的候选。
function scriptKeys(script) {
  try { if (typeof script === 'string') script = JSON.parse(script) } catch { return [] }
  const out = []
  for (const s of (Array.isArray(script) ? script : [])) {
    const k = s?.target?.key
    if (k && !out.includes(k)) out.push(k)
  }
  return out
}

// blocked 行一键跳选择器管理的「设备探测」tab:按 case_id 拉用例详情,带齐 fix_keys/
// sub_product/key_contexts 等桥接参数(对齐 CaseLibrary::locateMissingKeys)。所有 blocked 行都可点;
// 生成期 selector_fix_keys 为空时,从 script(detail 或行内 payload)的 target.key 兜底,尽量带上 key。
async function fixSelector(row) {
  const cid = row.case_id ?? row.test_case_id
  if (!cid) {
    router.push({ name: 'selectors', query: {
      project_id: pid.value, view: 'probe',
      ctx: `${row.title || ''} ${row.reason || ''}`.trim().slice(0, 200),
    } })
    return
  }
  let detail
  try { detail = await getTestcase(cid) } catch { detail = null }
  if (!detail) {
    // 详情拉不到也要能跳:用行内 payload.script 兜底抽 key
    const fbKeys = scriptKeys(row.payload?.script)
    router.push({ name: 'selectors', query: {
      project_id: pid.value, view: 'probe', case_ids: String(cid),
      sub_product: row.payload?.sub_product || '',
      fix_keys: fbKeys.join(','),
      ctx: `${row.title || ''} ${row.reason || ''}`.trim().slice(0, 200),
    } })
    return
  }
  // 只带**当前仍未注册**的 key:后端 missing_selector_keys 最准(优先);它是明确空数组=全已注册→不跳,
  // 提示直接回填;旧版后端无此字段才退回生成期 selector_fix_keys / script 兜底。避免把已补 key 带去重复添加。
  const script = detail.script || row.payload?.script
  const fixKeys = Array.isArray(detail.missing_selector_keys)
    ? detail.missing_selector_keys
    : (detail.selector_fix_keys?.length ? detail.selector_fix_keys : scriptKeys(script))
  if (Array.isArray(detail.missing_selector_keys) && !fixKeys.length) {
    ElMessage.info('该用例引用的选择器 key 均已注册，可直接「批量回填」恢复执行')
    return
  }
  const genKeys = detail.selector_fix_keys || []
  let contexts, hints
  if (genKeys.length) {
    ({ contexts, hints } = collectMissingKeys([{ ...detail, selector_fix: true, selector_fix_keys: fixKeys }], []))
  } else {
    // 兜底 key 不走 collectMissingKeys(它跳过非「待补」用例 → 无说明)。
    // 直接用 describeSelectorTarget 基于 script 为每个 key 产出步骤说明,让跳转后悬浮能显示具体步骤。
    contexts = {}; hints = {}
    for (const k of fixKeys) {
      const targets = describeSelectorTarget(k, script)
      hints[k] = { targets }
      contexts[k] = targets.map(t => `${t.description || t.title || ''} ${t.expected || ''}`.trim()).join(' ').slice(0, 1200)
    }
  }
  router.push({ name: 'selectors', query: {
    project_id: pid.value,
    view: 'probe',
    sub_product: detail.sub_product || row.payload?.sub_product || '',
    page: (detail.page || '').split(',').filter(Boolean)[0] || '',
    case_ids: String(cid),
    fix_keys: fixKeys.join(','),
    key_contexts: JSON.stringify(contexts),
    key_hints: JSON.stringify(hints),
    ctx: `${detail.title || row.title || ''} ${detail.steps || ''}`.trim().slice(0, 200),
  } })
}

// 重试:对该条执行记录的用例重新入队,执行机会重跑。run 标识为 run_id(与纠偏一致),老记录回落 id。
async function onRetry(row) {
  const runId = row.run_id || row.id
  try {
    await retryExecRun(runId)
    ElMessage.success('已重新入队,执行机将重跑该用例')
    await load(true)
  } catch { /* http 拦截器已提示 */ }
}

// script 存库为 JSON 字符串(也可能是对象);格式化成缩进 JSON 供编辑,坏值原样兜底。
function prettyScript(s) {
  if (!s) return ''
  try { return JSON.stringify(typeof s === 'string' ? JSON.parse(s) : s, null, 2) } catch { return String(s) }
}
// 人工纠偏:预置为"当前判定的反面"更符合直觉——通过则默认改失败,否则默认改通过。
function openCorrect(row) {
  correct.value = {
    visible: true, row, saving: false, reason: '',
    verdict: row.verdict === 'pass' ? 'fail' : 'pass',
    maintain: false, expected: '', steps: '', script: '', scriptOrig: '',
  }
  // 预填用例正文(供"维护用例"编辑):列表行 payload 里有 steps/expected 快照,取不到再单查详情。
  const cid = row.case_id ?? row.test_case_id
  correct.value.expected = row.payload?.expected || ''
  correct.value.steps = row.payload?.steps || ''
  // script 不在 payload 快照里,单查用例详情取(顺带补 payload 缺失的 expected/steps)。
  if (cid) {
    getTestcase(cid).then((tc) => {
      if (correct.value.row === row) {
        if (!correct.value.expected) correct.value.expected = tc.expected || ''
        if (!correct.value.steps) correct.value.steps = tc.steps || ''
        const pretty = prettyScript(tc.script)
        correct.value.script = pretty
        correct.value.scriptOrig = pretty
      }
    }).catch(() => {})
  }
}
// 仅 gui/e2e/api 用例能重生 script(manual/cli 不支持)。
function canMaintain(row) {
  return row && ['gui', 'e2e', 'api'].includes(row.kind || 'gui')
}
async function saveCorrect() {
  const c = correct.value
  if (!c.row) return
  const cid = c.row.case_id ?? c.row.test_case_id
  c.saving = true
  try {
    // 1) 先纠偏本次执行结果
    await correctExecVerdict(c.row.run_id, c.verdict, c.reason.trim() || undefined)
    // 2) 手改 script(可选):非空且相对原值有变更才提交。手写 script 优先于 AI 重生
    //    (两者都改 script,会互相覆盖),故本次跳过下面的 genTestcaseScript。
    const scriptText = (c.script || '').trim()
    let scriptEdited = false
    if (cid && scriptText && !sameScript(scriptText, c.scriptOrig)) {
      let parsed
      try { parsed = JSON.parse(scriptText) } catch { ElMessage.error('script 不是合法 JSON'); c.saving = false; return }
      if (!Array.isArray(parsed)) { ElMessage.error('script 必须是步骤数组(以 [ 开头)'); c.saving = false; return }
      await updateTestcase(cid, { script: parsed })   // 后端按 kind 校验;不合法弹 msg
      scriptEdited = true
    }
    // 3) 维护用例(可选):更新正文 → 按新正文重生 script,闭环"文案变了"这类真失败
    if (c.maintain && canMaintain(c.row) && cid) {
      const patch = {}
      if (c.expected.trim()) patch.expected = c.expected.trim()
      if (c.steps.trim()) patch.steps = c.steps.trim()
      if (Object.keys(patch).length) await updateTestcase(cid, patch)
      if (!scriptEdited) {
        await genTestcaseScript(cid)   // 后端按最新 steps/expected 重生并写回(手改 script 时跳过,避免覆盖)
        ElMessage.success('已纠偏，并已更新用例、重生 script')
      } else {
        ElMessage.success('已纠偏，并已更新用例正文与手改 script')
      }
    } else if (scriptEdited) {
      ElMessage.success('已纠偏，并已保存手改 script')
    } else {
      ElMessage.success('已纠偏')
    }
    correct.value.visible = false
    await load()
  } catch { /* http 拦截器已提示 */ }
  finally { c.saving = false }
}
// 比较两段 script 文本是否语义等价(忽略缩进/键序差异);解析失败则按文本原样比。
function sameScript(a, b) {
  try { return JSON.stringify(JSON.parse(a)) === JSON.stringify(JSON.parse(b || 'null')) }
  catch { return (a || '').trim() === (b || '').trim() }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.reason { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.foot-hint { margin-top: 8px; color: #90a4ae; font-size: 12px; }
.ev-path { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; margin-bottom: 10px; }
/* 批次头 */
.batch-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; width: 100%; }
.batch-head > .el-tag { flex-shrink: 0; }
.exec-results :deep(.el-collapse-item__header) { height: auto; min-height: 48px; padding: 12px; line-height: 1.6; }
.batch-id { overflow-wrap: anywhere; }
@media (max-width: 700px) { .batch-meta { width: 100%; margin-left: 0; } .batch-head { gap: 6px; } }
.batch-id { font-weight: 600; color: #334; }
.batch-stat { font-size: 13px; color: #5a6b7b; }
.batch-stat .ok { color: #00926e; }
.batch-stat .ng { color: #c45656; }
.batch-stat .blk { color: #e6a23c; }
.batch-stat .flk { color: #b88230; }
.chain-tag { margin-left: 4px; }
.fix-link { margin-left: 10px; font-size: 12px; }
.op-retry { margin-left: 10px; }
.triage-tag { margin-right: 6px; }
.triage-btn { margin-left: 8px; font-size: 12px; }
.triage-btn.busy { pointer-events: none; opacity: .6; }
.correct-case { margin: 0 0 6px; }
.correct-cur { margin: 0 0 12px; color: #5a6b7b; font-size: 13px; }
.correct-hint { color: #90a4ae; font-size: 12px; }
.maint-divider { margin: 14px 0 10px; }
.maint-box { margin-top: 10px; padding: 8px 12px; background: #f7faf9; border: 1px solid #e3ecea; border-radius: 6px; }
.batch-meta { margin-left: auto; font-size: 12px; color: #90a4ae; }
/* 报告 */
.rep-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.rep-meta { color: #90a4ae; font-size: 12px; }
.rep-reason { color: #5a6b7b; font-size: 13px; margin: 4px 0 12px; white-space: pre-line; }
.steps { list-style: none; margin: 0; padding: 0; }
.step { border-left: 2px solid #e4e7ed; padding: 6px 0 6px 12px; margin-left: 6px; }
.step-line { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.step-line .ok { color: #67c23a; }
.step-line .ng { color: #f56c6c; }
.step-no { color: #90a4ae; min-width: 18px; }
.step-act { font-weight: 600; color: #334; }
.step-desc { color: #5a6b7b; }
.step-err { color: #c45656; font-size: 12px; margin: 4px 0 4px 26px; }
.step-check { font-size: 12px; margin: 4px 0 4px 26px; color: #5a6b7b; }
.step-check code { padding: 1px 5px; border-radius: 3px; font-family: ui-monospace, Menlo, Consolas, monospace; }
.step-check .exp { background: #eef4ff; color: #3a5ccc; }
.step-check .act { background: #fff3e6; color: #c47a1e; }
.step-check .vs { margin: 0 6px; color: #90a4ae; }
.step-shot { display: block; max-width: 360px; max-height: 220px; margin: 6px 0 2px 26px; border: 1px solid #e4e7ed; border-radius: 4px; cursor: zoom-in; }
.shot-full { width: 100%; height: auto; }
</style>
