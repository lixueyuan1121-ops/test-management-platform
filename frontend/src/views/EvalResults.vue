<template>
  <div class="eval-results">
    <el-card>
      <template #header>
        <div class="header">
          <span>对话测评结果</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="verdictFilter" placeholder="判定" size="small" clearable style="width:110px">
              <el-option label="通过" value="pass" />
              <el-option label="不通过" value="fail" />
              <el-option label="判定出错" value="error" />
              <el-option label="未判定" value="__none__" />
            </el-select>
            <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
            <el-button
              size="small" type="primary" :icon="DataAnalysis" :loading="batchJudging"
              :disabled="!pid || !doneCount"
              @click="batchJudge"
            >批量判定 done（{{ doneCount }}）</el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="!filteredRows.length" :description="loading ? '加载中…' : '暂无测评执行记录'" :image-size="70" />

      <el-table v-else :data="filteredRows" v-loading="loading" size="small" border stripe row-key="run_id"
        :expand-row-keys="expanded" @expand-change="onExpand">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="verdict-detail">
              <div v-if="!row.verdict_dims" class="no-dims">
                <el-text type="info">尚未判定或无三维结果。点右侧「判定」触发。</el-text>
              </div>
              <template v-else>
                <div class="dims">
                  <div v-for="d in DIMS" :key="d.k" class="dim">
                    <div class="dim-head">
                      <el-icon v-if="dimPass(row, d.k) === true" class="ok"><CircleCheck /></el-icon>
                      <el-icon v-else-if="dimPass(row, d.k) === false" class="ng"><CircleClose /></el-icon>
                      <el-icon v-else class="unk"><QuestionFilled /></el-icon>
                      <span class="dim-label">{{ d.label }}</span>
                    </div>
                    <div class="dim-note">{{ dimNote(row, d.k) || '—' }}</div>
                  </div>
                </div>
                <div v-if="row.verdict_dims.summary" class="summary">
                  <b>判定小结：</b>{{ row.verdict_dims.summary }}
                </div>
              </template>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="run_id" label="#" width="64" align="center" />
        <el-table-column label="query" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ queryTitle(row) }}</template>
        </el-table-column>
        <el-table-column label="执行" width="96" align="center">
          <template #default="{ row }">
            <el-tag :type="STATUS_TYPE[row.status] || 'info'" size="small" effect="plain">{{ STATUS_LABEL[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="判定" width="96" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.verdict" :type="VERDICT_TYPE[row.verdict] || 'info'" size="small">{{ VERDICT_LABEL[row.verdict] || row.verdict }}</el-tag>
            <span v-else class="dim-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="异常" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_abnormal" type="danger" size="small" effect="dark">异常</el-tag>
            <span v-else class="dim-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="分享" width="70" align="center">
          <template #default="{ row }">
            <el-link v-if="safeUrl(row.share_link)" type="primary" :href="safeUrl(row.share_link)" target="_blank" rel="noopener noreferrer">会话</el-link>
            <span v-else class="dim-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="96" align="center">
          <template #default="{ row }">
            <el-button
              size="small" type="primary" text
              :loading="judgingIds.has(row.run_id)"
              :disabled="!canJudge(row)"
              @click="judgeOne(row)"
            >{{ row.verdict ? '重判' : '判定' }}</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="foot-hint">共 {{ rows.length }} 条执行 / 已判 {{ judgedCount }} 条 / 异常 {{ abnormalCount }} 条（判定读会话轨迹调 AI 判三维，单条约 30-60s）</div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, DataAnalysis, CircleCheck, CircleClose, QuestionFilled } from '@element-plus/icons-vue'
import { listEvalRuns, judgeEvalRun, judgeEvalBatch } from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

// 三维（键与后端 parse_eval_verdict 一致）
const DIMS = [
  { k: 'thinking_complete', label: '思考推理' },
  { k: 'tools_ok', label: '工具 / MCP 调用' },
  { k: 'artifact_expected', label: '产物 / 答案' },
]
// eval_run 生命周期（EvalRunStatus）
const STATUS_LABEL = { pending: '待执行', running: '执行中', done: '待判定', judging: '判定中', judged: '已判定', failed: '执行失败' }
const STATUS_TYPE = { pending: 'info', running: 'warning', done: 'primary', judging: 'warning', judged: 'success', failed: 'danger' }
// 总判定（EvalVerdict 值 pass/fail/error）：pass 绿 / fail 红 / error 灰
const VERDICT_LABEL = { pass: '通过', fail: '不通过', error: '判定出错' }
const VERDICT_TYPE = { pass: 'success', fail: 'danger', error: 'info' }

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const rows = ref([])
const loading = ref(false)
const verdictFilter = ref(null)
const expanded = ref([])
const judgingIds = ref(new Set())
const batchJudging = ref(false)

// done 状态（已执行完待判定）条数：批量判定针对这些
const doneCount = computed(() => rows.value.filter((r) => r.status === 'done').length)
const judgedCount = computed(() => rows.value.filter((r) => r.verdict).length)
const abnormalCount = computed(() => rows.value.filter((r) => r.is_abnormal).length)
const filteredRows = computed(() => {
  if (!verdictFilter.value) return rows.value
  if (verdictFilter.value === '__none__') return rows.value.filter((r) => !r.verdict)
  return rows.value.filter((r) => r.verdict === verdictFilter.value)
})

const queryTitle = (row) => row.payload?.title || row.payload?.prompt || `query #${row.eval_query_id ?? '—'}`
// 只放行 http(s) 链接（share_link 经 CLI 抓取回写，防 javascript: 等危险 scheme 的 XSS）
const safeUrl = (u) => /^https?:\/\//i.test(u || '') ? u : null
const dimPass = (row, k) => row.verdict_dims?.[k]?.pass
const dimNote = (row, k) => row.verdict_dims?.[k]?.note
// 执行完成（done）或已判过（judged/有 verdict）才可判/重判；未跑完（pending/running/failed）不可判
const canJudge = (row) => row.status === 'done' || row.status === 'judged' || !!row.verdict

onMounted(async () => {
  projects.value = await app.fetchProjects()
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await onProjectChange()
  }
})

async function onProjectChange() {
  verdictFilter.value = null
  expanded.value = []
  if (!pid.value) { rows.value = []; return }
  setLastProjectId(pid.value)
  await load()
}

async function load() {
  if (!pid.value) return
  loading.value = true
  try {
    rows.value = await listEvalRuns(pid.value)
  } finally { loading.value = false }
}

function onExpand(row, expandedRows) {
  expanded.value = expandedRows.map((r) => r.run_id)
}

// 单条判定：调 /eval-judge/{run_id}，完成后把返回的判定结果就地合并进该行（避免整表刷新丢展开态）。
async function judgeOne(row) {
  if (judgingIds.value.has(row.run_id)) return
  judgingIds.value = new Set(judgingIds.value).add(row.run_id)
  try {
    const res = await judgeEvalRun(row.run_id)
    Object.assign(row, {
      status: res.status ?? row.status,
      verdict: res.verdict ?? null,
      verdict_dims: res.verdict_dims ?? null,
      verdict_reason: res.verdict_reason ?? null,
      is_abnormal: !!res.is_abnormal,
    })
    if (res.verdict === 'error') ElMessage.warning(res.verdict_reason || '判定出错，可重试')
    else ElMessage.success(`判定完成：${VERDICT_LABEL[res.verdict] || res.verdict}`)
  } catch { /* http 拦截器已提示 */ }
  finally {
    const s = new Set(judgingIds.value); s.delete(row.run_id); judgingIds.value = s
  }
}

// 批量判定该项目所有 done 的 run（run_ids 留空 → 后端判全部 done）；完成后整表刷新看结果。
async function batchJudge() {
  if (!pid.value || !doneCount.value) return
  batchJudging.value = true
  try {
    const res = await judgeEvalBatch({ project_id: pid.value })
    const errs = (res.results || []).filter((x) => x.error).length
    ElMessage.success(`已判定 ${res.judged} 条${errs ? `（${errs} 条失败）` : ''}`)
    await load()
  } catch { /* http 拦截器已提示 */ }
  finally { batchJudging.value = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.foot-hint { margin-top: 8px; color: #90a4ae; font-size: 12px; }
.dim-muted { color: #c0c4cc; }
/* 三维展开 */
.verdict-detail { padding: 8px 16px; background: #fafcfe; }
.no-dims { padding: 8px 0; }
.dims { display: flex; gap: 16px; flex-wrap: wrap; }
.dim { flex: 1; min-width: 200px; padding: 10px 12px; border: 1px solid #e4e7ed; border-radius: 6px; background: #fff; }
.dim-head { display: flex; align-items: center; gap: 6px; font-weight: 600; color: #334; margin-bottom: 4px; }
.dim-head .ok { color: #67c23a; }
.dim-head .ng { color: #f56c6c; }
.dim-head .unk { color: #909399; }
.dim-label { font-size: 13px; }
.dim-note { font-size: 12px; color: #5a6b7b; white-space: pre-line; }
.summary { margin-top: 10px; font-size: 13px; color: #5a6b7b; white-space: pre-line; }
</style>
