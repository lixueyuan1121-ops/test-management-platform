<template>
  <div class="exec-results">
    <el-card>
      <template #header>
        <div class="header">
          <span>执行结果</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <TaskPicker v-model="taskId" :tasks="tasks" placeholder="任务" width="220px" @change="load" />
            <el-select v-model="runner" placeholder="执行设备" size="small" clearable style="width:150px" @change="load">
              <el-option v-for="rn in runners" :key="rn" :label="rn" :value="rn" />
            </el-select>
            <el-select v-model="verdict" placeholder="结果" size="small" clearable style="width:110px" @change="load">
              <el-option label="通过" value="pass" />
              <el-option label="失败" value="fail" />
              <el-option label="选择器阻塞" value="blocked" />
            </el-select>
            <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="!batches.length" :description="loading ? '加载中…' : '暂无执行记录'" :image-size="70" />

      <!-- 按批次分组:每批一块,组头显汇总,展开看该批每条用例;单条点「报告」下钻逐步截图 -->
      <el-collapse v-else v-model="activeBatches" v-loading="loading">
        <el-collapse-item v-for="b in batches" :key="b.id" :name="b.id">
          <template #title>
            <div class="batch-head">
              <el-tag :type="b.failed ? 'danger' : (b.blocked ? 'warning' : 'success')" size="small" effect="dark">
                {{ b.failed ? '有失败' : (b.blocked ? '有阻塞' : '全部通过') }}
              </el-tag>
              <span class="batch-id">{{ b.label }}</span>
              <span class="batch-stat">
                共 {{ b.total }} · <b class="ok">{{ b.passed }} 过</b> · <b class="ng">{{ b.failed }} 失</b>
                <template v-if="b.blocked"> · <b class="blk">{{ b.blocked }} 阻塞</b></template>
                · 功能通过率 {{ b.rate }}%
              </span>
              <span class="batch-meta">{{ b.runner }} · {{ b.durationText }} · {{ fmtTime(b.time) }}</span>
            </div>
          </template>

          <el-table :data="b.rows" size="small" border stripe empty-text="无记录">
            <el-table-column label="结果" width="96" align="center">
              <template #default="{ row }">
                <el-tag :type="resultType(row)" size="small">
                  {{ resultLabel(row) }}
                </el-tag>
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
                <span class="reason">{{ row.reason || '—' }}</span>
                <el-link v-if="isBlocked(row)" type="warning" class="fix-link" @click="fixSelector(row)">补齐选择器</el-link>
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
          </el-table>
        </el-collapse-item>
      </el-collapse>

      <div class="foot-hint">共 {{ rows.length }} 条 / {{ batches.length }} 个批次(每次执行都留痕,不覆盖;按批次与时间倒序)</div>
    </el-card>

    <!-- 单条执行的逐步报告(含截图) -->
    <el-dialog v-model="rep.visible" title="执行报告" width="720px" top="6vh">
      <div v-if="rep.row" class="rep">
        <div class="rep-head">
          <el-tag :type="rep.row.verdict === 'pass' ? 'success' : 'danger'" size="small" effect="dark">
            {{ rep.row.verdict === 'pass' ? '通过' : '失败' }}
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
            <img v-if="s.shot" :src="s.shot" class="step-shot" alt="步骤截图" @click="zoom(s.shot)" />
          </li>
        </ol>
        <el-empty v-if="!(rep.row.report || []).length" description="该次执行无逐步报告" :image-size="60" />
      </div>
    </el-dialog>

    <!-- 截图放大 -->
    <el-dialog v-model="shot.visible" title="截图" width="80%" top="4vh">
      <img v-if="shot.url" :src="shot.url" class="shot-full" alt="截图" />
    </el-dialog>

    <!-- 旧记录:仅本地路径 -->
    <el-dialog v-model="ev.visible" title="执行证据" width="480px">
      <p class="ev-path">{{ ev.path }}</p>
      <el-alert type="info" :closable="false" show-icon>证据为执行机本地路径(截图等),在对应设备上查看。</el-alert>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh, CircleCheck, CircleClose } from '@element-plus/icons-vue'
import { listTasks, listExecHistory } from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import TaskPicker from '@/components/TaskPicker.vue'

const router = useRouter()

const KIND_TYPE = { gui: 'success', api: 'primary', cli: 'warning', e2e: 'danger', manual: 'info' }
const KIND_LABEL = { gui: 'GUI', api: 'API', cli: 'CLI', e2e: 'E2E', manual: '人工' }
const STATUS_LABEL = { pending: '待执行', running: '执行中', passed: '通过', failed: '失败', blocked: '选择器阻塞' }

const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const taskId = ref(null)
const runner = ref(null)
const verdict = ref(null)
const rows = ref([])
const loading = ref(false)
const ev = ref({ visible: false, path: '' })
const rep = ref({ visible: false, row: null })
const shot = ref({ visible: false, url: '' })
const activeBatches = ref([])
const app = useAppStore()

const runners = computed(() => [...new Set(rows.value.map((r) => r.runner).filter(Boolean))])

const hasReport = (row) => Array.isArray(row.report) && row.report.length > 0

// 按 batch_id 分组;无 batch_id 的老记录归到 "(未分批)"。组内保持后端的时间倒序。
// 组间按该组最新一条执行时间倒序。汇总数(总/过/失/通过率/耗时和/设备/时间)现算。
const batches = computed(() => {
  const map = new Map()
  for (const r of rows.value) {
    const key = r.batch_id || '__none__'
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(r)
  }
  const out = []
  for (const [key, list] of map) {
    const passed = list.filter((r) => r.verdict === 'pass').length
    const failed = list.filter((r) => r.verdict === 'fail').length
    const blocked = list.filter((r) => r.verdict === 'blocked' || r.status === 'blocked').length
    const total = list.length
    const durSum = list.reduce((n, r) => n + (r.duration_ms || 0), 0)
    const time = list.reduce((t, r) => {
      const s = r.updated_at || r.created_at || ''
      return s > t ? s : t
    }, '')
    const fnDenom = passed + failed
    out.push({
      id: key,
      label: key === '__none__' ? '(未分批 · 历史记录)' : `批次 ${key}`,
      rows: list,
      total, passed, failed, blocked,
      rate: fnDenom ? Math.round((passed / fnDenom) * 100) : 0,
      runner: [...new Set(list.map((r) => r.runner).filter(Boolean))].join(', ') || '—',
      durationText: durSum ? (durSum / 1000).toFixed(1) + 's' : '—',
      time,
    })
  }
  out.sort((a, b) => (a.time < b.time ? 1 : -1))
  return out
})

onMounted(async () => {
  projects.value = await app.fetchProjects()
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await onProjectChange()
  }
})

async function onProjectChange() {
  taskId.value = null; runner.value = null; verdict.value = null
  if (!pid.value) { tasks.value = []; rows.value = []; return }
  setLastProjectId(pid.value)
  tasks.value = await listTasks({ project_id: pid.value })
  await load()
}

async function load() {
  if (!pid.value) return
  loading.value = true
  try {
    rows.value = await listExecHistory({
      project_id: pid.value,
      task_id: taskId.value || undefined,
      runner: runner.value || undefined,
      verdict: verdict.value || undefined,
    })
    // 默认展开最新批次,方便一进来就看到结果
    activeBatches.value = batches.value.slice(0, 1).map((b) => b.id)
  } finally { loading.value = false }
}

function showReport(row) { rep.value = { visible: true, row } }
function showEvidence(row) { ev.value = { visible: true, path: row.evidence_url } }
function zoom(url) { shot.value = { visible: true, url } }
function fmtTime(s) { return s ? String(s).replace('T', ' ').slice(0, 16) : '—' }

// 三态结果:pass 通过 / fail 功能失败(真 bug) / blocked 选择器阻塞(不计功能失败率)。
// 后端把 selector 阻塞的 verdict 直接写成 blocked;老数据可能仅 status=blocked,一并识别。
function isBlocked(row) { return row.verdict === 'blocked' || row.status === 'blocked' }
function resultType(row) {
  if (row.verdict === 'pass') return 'success'
  if (isBlocked(row)) return 'warning'
  if (row.verdict === 'fail') return 'danger'
  return 'info'
}
function resultLabel(row) {
  if (row.verdict === 'pass') return '通过'
  if (isBlocked(row)) return '选择器阻塞'
  if (row.verdict === 'fail') return '失败'
  return STATUS_LABEL[row.status] || row.status
}
// blocked 行一键跳选择器管理:带项目 + 用例上下文(title/reason),SelectorAdmin 按上下文探测并高亮匹配元素。
// reason 常含"未命中 key xxx",作为定位线索一并带上。复用 CaseLibrary「定位缺失 key」的 selectors 路由桥接。
function fixSelector(row) {
  router.push({
    name: 'selectors',
    query: {
      project_id: pid.value,
      ctx: `${row.title || ''} ${row.reason || ''}`.trim().slice(0, 200),
    },
  })
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
.batch-id { font-weight: 600; color: #334; }
.batch-stat { font-size: 13px; color: #5a6b7b; }
.batch-stat .ok { color: #00926e; }
.batch-stat .ng { color: #c45656; }
.batch-stat .blk { color: #e6a23c; }
.fix-link { margin-left: 10px; font-size: 12px; }
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
.step-shot { display: block; max-width: 360px; max-height: 220px; margin: 6px 0 2px 26px; border: 1px solid #e4e7ed; border-radius: 4px; cursor: zoom-in; }
.shot-full { width: 100%; height: auto; }
</style>
