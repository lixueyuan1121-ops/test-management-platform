<template>
  <div class="eval-results">
    <!-- 维度能力画像雷达(选中项目且有判定数据时显示) -->
    <div v-if="dimStats.dims.length" class="dr-panel">
      <div class="dr-head">
        <div class="dr-eyebrow">// CAPABILITY PROFILE · 测评维度能力画像</div>
        <div class="dr-overall">
          <span class="dr-rate">{{ dimStats.overall_rate }}<span class="dr-u">%</span></span>
          <span class="dr-lbl">综合通过率 · {{ dimStats.judged_total }} 条判定</span>
        </div>
      </div>
      <div class="dr-body">
        <!-- 维度 ≥ 3 用雷达图；< 3 退化为水平条形 -->
        <div v-if="dimStats.dims.length >= 3" ref="radarEl" class="dr-chart"></div>
        <div v-else class="dr-bars">
          <div v-for="d in dimStats.dims" :key="d.dimension" class="dr-bar-row">
            <span class="dr-bar-lbl">{{ dimLabel(d.dimension) }}</span>
            <div class="dr-bar-track">
              <div class="dr-bar-fill" :style="{ width: d.pass_rate + '%', background: drColor(d.pass_rate) }"></div>
            </div>
            <span class="dr-bar-val" :style="{ color: drColor(d.pass_rate) }">{{ d.pass_rate }}%</span>
          </div>
        </div>
        <div class="dr-dims">
          <div v-for="d in dimStats.dims" :key="d.dimension" class="dr-dim">
            <span class="dr-dim-dot" :style="{ background: drColor(d.pass_rate) }"></span>
            <span class="dr-dim-name">{{ dimLabel(d.dimension) }}</span>
            <span class="dr-dim-rate" :style="{ color: drColor(d.pass_rate) }">{{ d.pass_rate }}%</span>
            <span class="dr-dim-n">({{ d.total }})</span>
          </div>
        </div>
      </div>
    </div>
    <!-- 批次趋势:每批次一个点(通过率+均分),回答「比上次强吗」;≥2 批才有趋势可看 -->
    <div v-if="trend.length >= 2" class="tr-panel">
      <div class="tr-head">
        <div class="dr-eyebrow">// BATCH TREND · 批次趋势（近 {{ trend.length }} 批）</div>
        <div class="tr-legend">
          <span class="tr-lg"><i class="tr-dot tr-dot-rate"></i>通过率%</span>
          <span class="tr-lg"><i class="tr-dot tr-dot-score"></i>均分(1-5)</span>
        </div>
      </div>
      <div ref="trendEl" class="tr-chart"></div>
    </div>
    <!-- 判定质量:人工复核反推 AI 判定准不准(样本少的引擎不列,明细标注在展开区) -->
    <div v-if="quality.overall?.reviewed > 0" class="jq-panel">
      <div class="jq-head">
        <div class="dr-eyebrow">// JUDGE QUALITY · 判定质量（人工复核反推）</div>
        <div class="jq-hint">明细可在任一会话展开区标注误报/漏报</div>
      </div>
      <div class="jq-cards">
        <div class="jq-card">
          <div class="jq-n" :class="qAccClass(quality.overall.accuracy)">{{ quality.overall.accuracy ?? '—' }}<span class="jq-u">%</span></div>
          <div class="jq-l">判定准确率（{{ quality.overall.confirmed }}/{{ quality.overall.reviewed }} 复核）</div>
        </div>
        <div class="jq-card"><div class="jq-n warn">{{ quality.overall.fp_rate ?? '—' }}%</div><div class="jq-l">误报率</div></div>
        <div class="jq-card"><div class="jq-n danger">{{ quality.overall.fn_rate ?? '—' }}%</div><div class="jq-l">漏报率</div></div>
        <div class="jq-card">
          <div class="jq-n">{{ quality.overall.review_rate ?? '—' }}%</div>
          <div class="jq-l">复核覆盖率（{{ quality.overall.reviewed }}/{{ quality.overall.judged }} 已判定）</div>
        </div>
      </div>
      <div v-if="quality.by_engine?.length" class="jq-rows">
        <div v-for="e in quality.by_engine" :key="e.engine" class="jq-row">
          <span class="jq-eng">{{ e.engine }}</span>
          <span class="jq-bar"><i :style="{ width: (e.accuracy ?? 0) + '%' }" :class="qAccClass(e.accuracy)"></i></span>
          <span class="jq-val" :class="qAccClass(e.accuracy)">{{ e.accuracy ?? '—' }}%</span>
          <span class="jq-sub">{{ e.confirmed }}✓ / {{ e.false_positive }}误 / {{ e.false_negative }}漏（{{ e.reviewed }} 条复核）</span>
        </div>
      </div>
      <div v-else-if="!quality.by_engine?.length" class="jq-note">
        尚无引擎维度的复核样本。多判几条、多标几条误报/漏报后这里会显示各引擎的准确率横评——也正好验证稳健 3 票是否更准。
      </div>
    </div>
    <el-card>
      <template #header>
        <div class="header">
          <span>对话测评结果</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="batchFilter" placeholder="全部批次" size="small" clearable filterable style="width:210px" @change="load">
              <el-option v-for="b in batchOptions" :key="b.batch_id"
                :label="`${b.batch_id}${b.task_name ? ' · ' + b.task_name : ''}`" :value="b.batch_id" />
            </el-select>
            <el-select v-model="verdictFilter" placeholder="判定" size="small" clearable style="width:110px">
              <el-option label="通过" value="pass" />
              <el-option label="不通过" value="fail" />
              <el-option label="判定出错" value="error" />
              <el-option label="未判定" value="__none__" />
            </el-select>
            <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
            <el-checkbox v-model="robustJudge" size="small" class="robust-ck">
              <el-tooltip content="每条判 3 次取多数票（更稳，但 3 倍耗时）" placement="top"><span>稳健(3票)</span></el-tooltip>
            </el-checkbox>
            <el-button
              size="small" type="primary" :icon="DataAnalysis" :loading="batchJudging"
              :disabled="!pid || !doneCount"
              @click="batchJudge"
            >批量判定 done（{{ doneCount }}）</el-button>
            <el-button
              size="small" :icon="Upload"
              :disabled="!pid"
              @click="exportDialogVisible = true"
            >导出到飞书</el-button>
            <el-badge :value="multicaPending" :hidden="!multicaPending" :max="99" type="danger">
              <el-button
                size="small" type="warning" :icon="Promotion" :loading="pushingMultica"
                :disabled="!pid || !abnormalCount"
                @click="doPushMultica"
              >推送异常到 multica</el-button>
            </el-badge>
          </div>
        </div>
      </template>

      <el-empty v-if="!groupedRows.length" :description="loading ? '加载中…' : '暂无测评执行记录'" :image-size="70" />

      <el-table v-else :data="groupedRows" v-loading="loading" size="small" border stripe row-key="run_id"
        :tree-props="{ children: 'children' }" :expand-row-keys="expanded" @expand-change="onExpand">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="verdict-detail">
              <div v-if="row.isGroup" class="no-dims">
                <el-text type="info">
                  这是一次 {{ row.children.length }} 轮的多轮会话（批次 {{ row.batch_id || '—' }}），各轮在同一对话内连续发送。
                  各轮明细：{{ groupTurnSummary(row) }}。左侧另一个小箭头可展开逐轮行查看判定与评分。
                </el-text>
              </div>
              <div v-else-if="!row.verdict_dims" class="no-dims">
                <el-text type="info">尚未判定或无三维结果。点右侧「判定」触发。</el-text>
              </div>
              <template v-else>
                <div class="dims">
                  <div v-for="d in rowDims(row)" :key="d.k" class="dim">
                    <div class="dim-head">
                      <el-icon v-if="dimPass(row, d.k) === true" class="ok"><CircleCheck /></el-icon>
                      <el-icon v-else-if="dimPass(row, d.k) === false" class="ng"><CircleClose /></el-icon>
                      <el-icon v-else class="unk"><QuestionFilled /></el-icon>
                      <span class="dim-label">{{ d.label }}</span>
                    </div>
                    <div class="dim-note" v-html="renderMd(dimNote(row, d.k))"></div>
                  </div>
                </div>
                <div v-if="row.verdict_dims.summary" class="summary">
                  <b>判定小结：</b><span v-html="renderMd(row.verdict_dims.summary)"></span>
                </div>
              </template>
              <!-- 人工复核(失败收敛):对 AI 判定标 认可/误报/漏报,误报自动摘异常、漏报置异常 -->
              <div v-if="row.verdict && !row.isGroup" class="review-bar">
                <span class="review-lbl">人工复核：</span>
                <el-button size="small" :type="row.review_mark === 'confirmed' ? 'success' : ''" @click="doReview(row, 'confirmed')">认可判定</el-button>
                <el-button size="small" :type="row.review_mark === 'false_positive' ? 'warning' : ''" @click="doReview(row, 'false_positive')">误报（实际通过）</el-button>
                <el-button size="small" :type="row.review_mark === 'false_negative' ? 'danger' : ''" @click="doReview(row, 'false_negative')">漏报（实际有问题）</el-button>
                <el-button v-if="row.review_mark" size="small" text @click="doReview(row, null)">清除</el-button>
                <span v-if="row.review_note" class="review-note">备注：{{ row.review_note }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="#" width="84" align="center">
          <template #default="{ row }">
            <span v-if="row.isGroup" class="dim-muted">—</span>
            <el-tooltip v-else content="执行记录编号(run id)" placement="top" :show-after="500">
              <span>{{ row.run_id }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="query" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <template v-if="row.isGroup">
              <el-tag size="small" type="warning" effect="plain" class="turn-tag">多轮 ×{{ row.children.length }}</el-tag>{{ queryTitle(row) }}
            </template>
            <template v-else>
              <el-tag v-if="row._inGroup" size="small" effect="plain" class="turn-tag">第{{ (row.payload?.turn_index ?? 0) + 1 }}轮</el-tag>{{ queryTitle(row) }}
            </template>
          </template>
        </el-table-column>
        <el-table-column label="维度" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.dimension" :type="DIM_TAG_TYPE[row.dimension] || 'info'" size="small" effect="plain">{{ dimLabel(row.dimension) }}</el-tag>
            <span v-else class="dim-muted">—</span>
          </template>
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
            <el-tooltip v-if="row.review_mark" :content="REVIEW_LABEL[row.review_mark] + (row.review_note ? '：' + row.review_note : '')" placement="top">
              <span class="review-flag" :class="'rf-' + row.review_mark">{{ REVIEW_ICON[row.review_mark] }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="评分" width="60" align="center">
          <template #default="{ row }">
            <span v-if="rowScore(row) != null" class="score" :class="scoreClass(rowScore(row))">{{ rowScore(row) }}</span>
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
        <el-table-column label="对比" width="66" align="center">
          <template #default="{ row }">
            <!-- A/B 对比配对行:点开左右分栏并排看答辩+判定 -->
            <el-button v-if="!row.isGroup && row.payload?.compare_group" size="small" type="warning" text
              @click="openAbCompare(row)">对比</el-button>
            <span v-else class="dim-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="96" align="center">
          <template #default="{ row }">
            <el-button
              v-if="!row.isGroup"
              size="small" type="primary" text
              :loading="judgingIds.has(row.run_id)"
              :disabled="!canJudge(row)"
              @click="judgeOne(row)"
            >{{ row.verdict ? '重判' : '判定' }}</el-button>
            <span v-else class="dim-muted">逐轮判</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="foot-hint">共 {{ rows.length }} 条执行 / 已判 {{ judgedCount }} 条 / 异常 {{ abnormalCount }} 条（判定读会话轨迹调 AI 判三维，单条约 30-60s）</div>
    </el-card>

    <el-dialog v-model="exportDialogVisible" title="导出到飞书表" width="480px">
      <el-form label-width="90px">
        <el-form-item label="飞书表链接" required>
          <el-input v-model="exportSheetUrl" placeholder="粘贴目标飞书表格链接" clearable />
        </el-form-item>
        <el-form-item label="仅异常">
          <el-checkbox v-model="exportAbnormalOnly">只导出判定异常的会话</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="exportDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="exporting" @click="doExportFeishu">导出</el-button>
      </template>
    </el-dialog>

    <!-- A/B 并排对比(LMArena 式):同一题两套配置的答辩+判定并列,胜因一目了然 -->
    <el-dialog v-model="abCompareVisible" :title="`A/B 对比 · ${queryTitle(abPair.a)}`" width="920px" top="6vh">
      <div v-if="abPair.a || abPair.b" class="ab-wrap">
        <template v-for="side in ['a', 'b']" :key="side">
          <div class="ab-col" :class="abWin(side) ? 'ab-win' : ''">
            <div class="ab-hd">
              <span class="ab-tag" :class="side === 'a' ? 'ab-tag-a' : 'ab-tag-b'">{{ side === 'a' ? 'A' : 'B' }}</span>
              <span class="ab-opts">{{ abOpts(abPair[side]) }}</span>
              <span class="ab-verdict">{{ abPair[side]?.verdict ? VERDICT_LABEL[abPair[side].verdict] : '未判定' }}</span>
            </div>
            <div class="ab-body">
              <div class="ab-sec">回答</div>
              <div class="ab-text">{{ abPair[side]?.answer || '—' }}</div>
              <div class="ab-sec">判定理由</div>
              <div class="ab-text">{{ abPair[side]?.verdict_reason || '—' }}</div>
              <div v-if="abPair[side]?.score != null" class="ab-score">评分 {{ abPair[side].score }}/5</div>
              <el-link v-if="safeUrl(abPair[side]?.share_link)" type="primary" :href="safeUrl(abPair[side].share_link)" target="_blank" rel="noopener noreferrer">打开会话</el-link>
            </div>
          </div>
        </template>
        <div class="ab-prompt">
          <div class="ab-sec">题干 prompt</div>
          <div class="ab-text">{{ abPair.a?.payload?.prompt || abPair.b?.payload?.prompt || '—' }}</div>
          <div class="ab-sec">期望 expected</div>
          <div class="ab-text">{{ abPair.a?.payload?.expected || abPair.b?.payload?.expected || '—' }}</div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, DataAnalysis, Upload, Promotion, CircleCheck, CircleClose, QuestionFilled } from '@element-plus/icons-vue'
import { listEvalRuns, judgeEvalRun, judgeEvalBatch, exportEvalFeishu, pushEvalMultica, evalMulticaPending, evalDimensionStats, listEvalDimensions, evalBatchTrend, reviewEvalRun, evalJudgeQuality } from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { groupEvalRuns } from '@/utils/evalRunGroups'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

// 判定理由 markdown 渲染:AI 常输出代码块/列表/加粗,纯文本可读性差。
// html:false 禁内联 HTML + DOMPurify 消毒,双保险防 XSS(判定理由经 LLM 生成,视作不可信输入)。
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const renderMd = (text) => text ? DOMPurify.sanitize(md.render(String(text))) : '—'

// 判定核心三维（键与后端 parse_eval_verdict 一致）；dimension_ok 第四维按行动态追加
const DIMS = [
  { k: 'thinking_complete', label: '思考推理' },
  { k: 'tools_ok', label: '工具 / MCP 调用' },
  { k: 'artifact_expected', label: '产物 / 答案' },
]
// 行内展示的判定维度：核心三维 + 该行有 dimension_ok 时补一格「主考维度」
const rowDims = (row) => {
  const base = [...DIMS]
  if (row.verdict_dims?.dimension_ok) {
    base.push({ k: 'dimension_ok', label: `主考维度${row.dimension ? '·' + dimLabel(row.dimension) : ''}` })
  }
  return base
}
// 测评维度注册表(服务端拉取,雷达/列表统一用中文标签;失败用内置兜底)
const DIM_META = ref({
  thinking: '思考推理', tool_use: '工具·MCP调用', artifact: '产物生成',
  multi_turn: '多轮追问', instruction: '指令遵循',
  workflow: '工作流', clarification: '反问澄清', context: '上下文记忆',
  safety: '安全合规', refusal: '拒答质量',
  hallucination: '事实可靠', creativity: '创意生成', consistency: '一致性',
})
const dimLabel = (k) => (k ? (DIM_META.value[k] || k) : '未标注')
const DIM_TAG_TYPE = {
  thinking: 'primary', tool_use: 'success', artifact: 'warning', multi_turn: 'danger', instruction: 'info',
  workflow: 'warning', clarification: 'primary', context: 'success', safety: 'danger', refusal: 'info',
  hallucination: 'warning', creativity: 'primary', consistency: 'success',
}
// eval_run 生命周期（EvalRunStatus）
const STATUS_LABEL = { pending: '待执行', running: '执行中', done: '待判定', judging: '判定中', judged: '已判定', failed: '执行失败' }
const STATUS_TYPE = { pending: 'info', running: 'warning', done: 'primary', judging: 'warning', judged: 'success', failed: 'danger' }
// 总判定（EvalVerdict 值 pass/fail/error）：pass 绿 / fail 红 / error 灰
const VERDICT_LABEL = { pass: '通过', fail: '不通过', error: '判定出错' }
const VERDICT_TYPE = { pass: 'success', fail: 'danger', error: 'info' }
// 人工复核标记(失败收敛)
const REVIEW_LABEL = { confirmed: '已认可判定', false_positive: '误报（实际通过）', false_negative: '漏报（实际有问题）' }
const REVIEW_ICON = { confirmed: '✓', false_positive: '误', false_negative: '漏' }

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const rows = ref([])
const loading = ref(false)
const verdictFilter = ref(null)
// 批次筛选(选项复用 trend 批次列表,最新在前);清空=全部批次
const batchFilter = ref(null)
const batchOptions = computed(() => [...trend.value].reverse())
const expanded = ref([])
const judgingIds = ref(new Set())
const batchJudging = ref(false)
const robustJudge = ref(false)

// 导出飞书 / 推送 multica
const exportDialogVisible = ref(false)
const exportSheetUrl = ref('')
const exportAbnormalOnly = ref(false)
const exporting = ref(false)
const pushingMultica = ref(false)
const multicaPending = ref(0)

// done 状态（已执行完待判定）条数：批量判定针对这些
const doneCount = computed(() => rows.value.filter((r) => r.status === 'done').length)
const judgedCount = computed(() => rows.value.filter((r) => r.verdict).length)
const abnormalCount = computed(() => rows.value.filter((r) => r.is_abnormal).length)
const matchFilter = (r) => {
  if (!verdictFilter.value) return true
  if (verdictFilter.value === '__none__') return !r.verdict
  return r.verdict === verdictFilter.value
}

// 多轮会话分组(公共逻辑见 utils/evalRunGroups):组行树形展开,单轮原样平铺
const groupedRows = computed(() => groupEvalRuns(rows.value, matchFilter))

// row 可空容错:A/B 弹窗 title 在 abPair 尚为 {a:null} 时就会求值,不容错整页渲染崩溃(数据区全空)
const queryTitle = (row) => row?.payload?.title || row?.payload?.prompt || `query #${row?.eval_query_id ?? '—'}`
// 只放行 http(s) 链接（share_link 经 CLI 抓取回写，防 javascript: 等危险 scheme 的 XSS）
const safeUrl = (u) => /^https?:\/\//i.test(u || '') ? u : null
// A/B 并排对比:同 eval_query_id + compare_mode 配对的 A/B 两条;弹窗左右分栏
const abCompareVisible = ref(false)
const abPair = ref({ a: null, b: null })

function openAbCompare(row) {
  const g = row.payload?.compare_group
  if (!g) return
  const pair = { a: null, b: null }
  for (const r of rows.value) {
    if (r.payload?.compare_group === 'A' && r.eval_query_id === row.eval_query_id) pair.a = r
    if (r.payload?.compare_group === 'B' && r.eval_query_id === row.eval_query_id) pair.b = r
  }
  abPair.value = pair
  abCompareVisible.value = true
}

function abOpts(r) {
  if (!r) return ''
  const d = r.payload?.dialog_options || {}
  const parts = [d.model, d.chatMode, d.thinkingDepth && `深:${d.thinkingDepth}`].filter(Boolean)
  return parts.join(' · ') || '客户端默认'
}

function abWin(side) {
  const a = abPair.value.a?.verdict, b = abPair.value.b?.verdict
  if (side === 'a') return a === 'pass' && b === 'fail'
  return b === 'pass' && a === 'fail'
}
const dimPass = (row, k) => row.verdict_dims?.[k]?.pass
const dimNote = (row, k) => row.verdict_dims?.[k]?.note
// 评分(1-5,判定引擎给):组行取各轮均分(1 位小数),真实行取 score
const rowScore = (row) => {
  if (!row.isGroup) return row.score ?? null
  const ss = (row.children || []).map((t) => t.score).filter((s) => s != null)
  return ss.length ? +(ss.reduce((a, b) => a + b, 0) / ss.length).toFixed(1) : null
}
const scoreClass = (s) => (s >= 4 ? 'score-hi' : s >= 3 ? 'score-mid' : 'score-lo')
// 执行完成（done）或已判过（judged/有 verdict）才可判/重判；未跑完（pending/running/failed）不可判
const canJudge = (row) => row.status === 'done' || row.status === 'judged' || !!row.verdict

onMounted(async () => {
  // 维度注册表(标签映射)与项目列表并行拉;注册表失败沿用内置兜底
  const [projRes, dimRes] = await Promise.allSettled([app.fetchProjects(), listEvalDimensions()])
  if (dimRes.status === 'fulfilled' && dimRes.value?.dimensions?.length) {
    DIM_META.value = Object.fromEntries(dimRes.value.dimensions.map((d) => [d.key, d.label]))
  }
  projects.value = projRes.status === 'fulfilled' ? (projRes.value || []) : []
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await onProjectChange()
  }
})

async function onProjectChange() {
  verdictFilter.value = null
  batchFilter.value = null
  expanded.value = []
  if (!pid.value) { rows.value = []; multicaPending.value = 0; return }
  setLastProjectId(pid.value)
  await load()
  loadDimStats()   // 维度雷达:独立加载不阻塞列表
  loadTrend()      // 批次趋势:同上
  loadQuality()    // 判定质量:同上
}

async function load() {
  if (!pid.value) return
  loading.value = true
  try {
    rows.value = await listEvalRuns(pid.value, batchFilter.value || undefined)
    refreshMulticaPending()
  } finally { loading.value = false }
}

// 待推 multica 数（用于 badge）；失败静默（拦截器已提示），不阻断主流程。
async function refreshMulticaPending() {
  if (!pid.value) { multicaPending.value = 0; return }
  try {
    const res = await evalMulticaPending(pid.value)
    multicaPending.value = res.pending || 0
  } catch { /* http 拦截器已提示 */ }
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
    const res = await judgeEvalBatch({ project_id: pid.value, votes: robustJudge.value ? 3 : 1 })
    const errs = (res.results || []).filter((x) => x.error).length
    ElMessage.success(`已判定 ${res.judged} 条${errs ? `（${errs} 条失败）` : ''}`)
    await load()
  } catch { /* http 拦截器已提示 */ }
  finally { batchJudging.value = false }
}

// 导出到飞书：填目标表链接 +（可选）仅异常，调 /eval-export/feishu。
async function doExportFeishu() {
  if (!exportSheetUrl.value) { ElMessage.warning('请填飞书表链接'); return }
  exporting.value = true
  try {
    const res = await exportEvalFeishu({ project_id: pid.value, sheet_url: exportSheetUrl.value, abnormal_only: exportAbnormalOnly.value })
    ElMessage.success(`已导出 ${res.exported} 行到飞书表`)
    exportDialogVisible.value = false
  } catch { /* http 拦截器已提示 */ }
  finally { exporting.value = false }
}

// 推送异常会话到 multica（后端只推 is_abnormal 且未 pushed 的，防重推）；完成后刷新列表 + 待推数。
async function doPushMultica() {
  pushingMultica.value = true
  try {
    const res = await pushEvalMultica({ project_id: pid.value })
    ElMessage.success(`推送 ${res.pushed}/${res.candidates} 条异常到 multica`)
    await load()
  } catch { /* http 拦截器已提示 */ }
  finally { pushingMultica.value = false }
}

// 组行展开区的各轮状态汇总,如「3 通过 / 1 不通过 / 2 待判定」
function groupTurnSummary(row) {
  const turns = row.children || []
  const n = (f) => turns.filter(f).length
  const parts = []
  const pass = n((t) => t.verdict === 'pass'); if (pass) parts.push(`${pass} 通过`)
  const fail = n((t) => t.verdict === 'fail'); if (fail) parts.push(`${fail} 不通过`)
  const err = n((t) => t.verdict === 'error'); if (err) parts.push(`${err} 判定出错`)
  const failedExec = n((t) => t.status === 'failed'); if (failedExec) parts.push(`${failedExec} 执行失败`)
  const waiting = n((t) => !t.verdict && t.status !== 'failed'); if (waiting) parts.push(`${waiting} 待判定/待执行`)
  return parts.join(' / ') || '—'
}

// 人工复核:点已选中的标记 = 无操作;新标记弹备注框(可空);mark=null 清除。就地更新该行。
async function doReview(row, mark) {
  if (mark && row.review_mark === mark) return
  let note = ''
  if (mark) {
    try {
      const r = await ElMessageBox.prompt('复核备注（可空，说明误判原因便于迭代题目期望/判定规则）', REVIEW_LABEL[mark], {
        confirmButtonText: '保存', cancelButtonText: '取消', inputValue: row.review_note || '',
      })
      note = r.value || ''
    } catch { return }  // 取消
  }
  try {
    const res = await reviewEvalRun(row.run_id, mark, note)
    Object.assign(row, { review_mark: res.review_mark, review_note: res.review_note, is_abnormal: !!res.is_abnormal })
    ElMessage.success(mark ? `已标注：${REVIEW_LABEL[mark]}` : '已清除复核标注')
  } catch { /* 拦截器已提示 */ }
}

// ==== 维度能力画像雷达 ====
import * as echarts from 'echarts/core'
import { RadarChart, LineChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
echarts.use([RadarChart, LineChart, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer])

const dimStats = ref({ dims: [], judged_total: 0, overall_rate: 0 })
const radarEl = ref(null)
let radarChart = null

function drColor(r) { return r >= 90 ? '#00b386' : r >= 70 ? '#e8a23d' : '#e5565f' }

function drawRadar() {
  if (!radarEl.value || dimStats.value.dims.length < 3) return
  if (!radarChart) radarChart = echarts.init(radarEl.value)
  const dims = dimStats.value.dims
  radarChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', formatter: (p) => p.data.value.map((v, i) => `${dimLabel(dims[i].dimension)}: ${v}%`).join('<br/>') },
    radar: {
      indicator: dims.map((d) => ({ name: dimLabel(d.dimension), max: 100 })),
      radius: '65%',
      splitArea: { areaStyle: { color: ['rgba(0,179,134,.05)', 'rgba(0,179,134,.02)'] } },
      axisName: { color: '#7d8a9b', fontSize: 12 },
      splitLine: { lineStyle: { color: 'rgba(0,179,134,.2)' } },
      axisLine: { lineStyle: { color: 'rgba(0,179,134,.2)' } },
    },
    series: [{
      type: 'radar',
      data: [{ name: '通过率', value: dims.map((d) => d.pass_rate) }],
      symbol: 'circle', symbolSize: 5,
      lineStyle: { color: '#00b386', width: 2 },
      areaStyle: { color: 'rgba(0,179,134,.3)' },
      itemStyle: { color: '#00b386' },
    }],
  })
}

async function loadDimStats() {
  if (!pid.value) { dimStats.value = { dims: [], judged_total: 0, overall_rate: 0 }; return }
  try { dimStats.value = await evalDimensionStats(pid.value) } catch { /* 静默 */ }
  await nextTick()
  drawRadar()
}

// ==== 批次趋势(通过率+均分双轴折线) ====
const trend = ref([])
const trendEl = ref(null)
let trendChart = null

// ==== 判定质量(复核反推) ====
const quality = ref({ overall: null, by_engine: [] })
const qAccClass = (v) => (v == null ? 'q-mid' : v >= 90 ? 'q-hi' : v >= 75 ? 'q-mid' : 'q-lo')

async function loadQuality() {
  if (!pid.value) { quality.value = { overall: null, by_engine: [] }; return }
  try {
    const res = await evalJudgeQuality(pid.value)
    quality.value = { overall: res.overall?.reviewed > 0 ? res.overall : null, by_engine: res.by_engine || [] }
  } catch { /* 静默 */ }
}

function drawTrend() {
  if (!trendEl.value || trend.value.length < 2) return
  if (!trendChart) trendChart = echarts.init(trendEl.value)
  const bs = trend.value
  const x = bs.map((b) => (b.date || '').slice(5, 16).replace('T', ' '))
  trendChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (ps) => {
        const b = bs[ps[0]?.dataIndex]
        if (!b) return ''
        return `<b>${b.task_name || '题库下发'}</b> · ${b.batch_id}<br/>`
          + `${(b.date || '').replace('T', ' ').slice(0, 19)}<br/>`
          + `判定 ${b.judged}/${b.total} · 通过率 ${b.pass_rate ?? '—'}%<br/>`
          + `均分 ${b.avg_score ?? '—'}/5`
      },
    },
    grid: { left: 44, right: 44, top: 16, bottom: 28 },
    xAxis: { type: 'category', data: x, axisLabel: { color: '#7d8a9b', fontSize: 10 }, axisLine: { lineStyle: { color: 'rgba(255,255,255,.15)' } } },
    yAxis: [
      { type: 'value', min: 0, max: 100, axisLabel: { color: '#7d8a9b', formatter: '{value}%' }, splitLine: { lineStyle: { color: 'rgba(255,255,255,.06)' } } },
      { type: 'value', min: 0, max: 5, axisLabel: { color: '#d98b00' }, splitLine: { show: false } },
    ],
    series: [
      { name: '通过率', type: 'line', data: bs.map((b) => b.pass_rate), smooth: true, connectNulls: true,
        symbol: 'circle', symbolSize: 6, lineStyle: { color: '#00e5a0', width: 2 }, itemStyle: { color: '#00e5a0' },
        areaStyle: { color: 'rgba(0,229,160,.12)' } },
      { name: '均分', type: 'line', yAxisIndex: 1, data: bs.map((b) => b.avg_score), smooth: true, connectNulls: true,
        symbol: 'circle', symbolSize: 5, lineStyle: { color: '#d98b00', width: 2, type: 'dashed' }, itemStyle: { color: '#d98b00' } },
    ],
  })
}

async function loadTrend() {
  if (!pid.value) { trend.value = []; return }
  try { trend.value = (await evalBatchTrend(pid.value))?.batches || [] } catch { trend.value = [] }
  await nextTick()
  drawTrend()
}

onBeforeUnmount(() => {
  if (radarChart) { radarChart.dispose(); radarChart = null }
  if (trendChart) { trendChart.dispose(); trendChart = null }
})
</script>

<style scoped>
/* A/B 并排对比 */
.ab-wrap { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.ab-col { border: 1px solid #e4e7ed; border-radius: 10px; padding: 12px 14px; background: #fbfdfe; }
.ab-col.ab-win { border-color: #00b386; background: #f5fcf9; box-shadow: 0 0 0 1px #00b38633; }
.ab-hd { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.ab-tag { width: 20px; height: 20px; border-radius: 5px; text-align: center; line-height: 20px; font-weight: 800; font-size: 12px; color: #fff; flex: none; }
.ab-tag-a { background: #2f7dd1; }
.ab-tag-b { background: #d98b00; }
.ab-opts { font-size: 11px; color: #8099aa; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ab-verdict { font-size: 13px; font-weight: 700; flex: none; }
.ab-body { display: flex; flex-direction: column; gap: 6px; }
.ab-sec { font-size: 11px; color: #8a94a6; font-weight: 600; margin-top: 6px; }
.ab-text { font-size: 12px; color: #34495e; line-height: 1.65; max-height: 180px; overflow: auto; word-break: break-word; }
.ab-score { font-size: 12px; color: #d98b00; font-weight: 700; }
.ab-prompt { grid-column: 1 / -1; border: 1px dashed #e4e7ed; border-radius: 10px; padding: 12px 14px; }
/* 判定质量面板 */
.jq-panel { background: #fff; border: 1px solid #e4e7ed; border-radius: 12px; padding: 16px 20px; margin-bottom: 16px; }
.jq-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.jq-head .dr-eyebrow { color: #7a4fd0; }
.jq-hint { font-size: 12px; color: #8a94a6; }
.jq-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px; }
.jq-card { background: #f8fafc; border: 1px solid #eef1f5; border-radius: 8px; padding: 10px 14px; text-align: center; }
.jq-n { font-family: 'JetBrains Mono', monospace; font-size: 22px; font-weight: 800; color: #1f2d3d; }
.jq-n .jq-u { font-size: 12px; color: #8a94a6; }
.jq-n.warn { color: #d98b00; }
.jq-n.danger { color: #e5565f; }
.jq-l { font-size: 11px; color: #8a94a6; margin-top: 3px; }
.jq-rows { display: flex; flex-direction: column; gap: 8px; border-top: 1px dashed #e4e7ed; padding-top: 10px; }
.jq-row { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.jq-eng { width: 110px; font-family: 'JetBrains Mono', monospace; color: #4a5568; flex: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.jq-bar { flex: 1; height: 10px; background: #eef1f5; border-radius: 5px; overflow: hidden; }
.jq-bar i { display: block; height: 100%; border-radius: 5px; }
.q-hi { color: #00b386; background-color: #00b386; }
.q-mid { color: #d98b00; background-color: #d98b00; }
.q-lo { color: #e5565f; background-color: #e5565f; }
.jq-val { width: 48px; text-align: right; font-family: 'JetBrains Mono', monospace; font-weight: 700; flex: none; }
.jq-sub { font-size: 11px; color: #9aa5b1; flex: none; }
.jq-note { font-size: 12px; color: #8a94a6; border-top: 1px dashed #e4e7ed; padding-top: 10px; }
/* 批次趋势 */
.tr-panel { background: linear-gradient(135deg, #1a2836 0%, #212f43 100%); border-radius: 14px; padding: 18px 24px 10px; margin-bottom: 16px; }
.tr-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.tr-legend { display: flex; gap: 14px; }
.tr-lg { font-size: 12px; color: #a7b4c4; display: inline-flex; align-items: center; gap: 5px; }
.tr-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.tr-dot-rate { background: #00e5a0; }
.tr-dot-score { background: #d98b00; }
.tr-chart { width: 100%; height: 200px; }
/* 维度能力画像雷达 */
.dr-panel { background: linear-gradient(135deg, #1a2836 0%, #212f43 100%); border-radius: 14px; padding: 20px 24px; margin-bottom: 16px; color: #e6edf3; }
.dr-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.dr-eyebrow { font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: 2px; color: #00e5a0; }
.dr-overall { text-align: right; }
.dr-rate { font-family: 'JetBrains Mono', monospace; font-size: 36px; font-weight: 800; color: #fff; }
.dr-u { font-size: 18px; color: #7d8a9b; }
.dr-lbl { font-size: 12px; color: #8b98a9; display: block; margin-top: 2px; }
.dr-body { display: grid; grid-template-columns: 1fr 200px; gap: 24px; align-items: center; }
.dr-chart { width: 100%; height: 260px; }
.dr-bars { display: flex; flex-direction: column; gap: 10px; }
.dr-bar-row { display: flex; align-items: center; gap: 10px; }
.dr-bar-lbl { font-size: 12px; color: #a7b4c4; width: 64px; flex: none; font-family: 'JetBrains Mono', monospace; }
.dr-bar-track { flex: 1; height: 14px; background: rgba(255,255,255,.08); border-radius: 4px; overflow: hidden; }
.dr-bar-fill { height: 100%; border-radius: 4px; transition: width .5s ease; }
.dr-bar-val { font-family: 'JetBrains Mono', monospace; font-size: 12px; width: 40px; text-align: right; flex: none; }
.dr-dims { display: flex; flex-direction: column; gap: 8px; }
.dr-dim { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.dr-dim-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.dr-dim-name { color: #a7b4c4; }
.dr-dim-rate { margin-left: auto; font-family: 'JetBrains Mono', monospace; font-weight: 700; }
.dr-dim-n { color: #5f6b7a; font-size: 11px; }
@media (max-width: 900px) { .dr-body { grid-template-columns: 1fr; } .dr-chart { height: 220px; } }

.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.foot-hint { margin-top: 8px; color: #90a4ae; font-size: 12px; }
.dim-muted { color: #c0c4cc; }
.turn-tag { margin-right: 6px; }
.score { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 14px; }
.score-hi { color: #00b386; }
.score-mid { color: #d98b00; }
.score-lo { color: #e5565f; }
/* 人工复核 */
.review-bar { display: flex; align-items: center; gap: 8px; margin-top: 12px; padding-top: 10px; border-top: 1px dashed #e4e7ed; flex-wrap: wrap; }
.review-lbl { font-size: 12px; color: #8a94a6; font-weight: 600; }
.review-note { font-size: 12px; color: #8a94a6; }
.review-flag { display: inline-block; margin-left: 4px; font-size: 11px; font-weight: 700; width: 16px; height: 16px; line-height: 16px; text-align: center; border-radius: 50%; cursor: default; }
.rf-confirmed { background: #e7f7f1; color: #00b386; }
.rf-false_positive { background: #fdf3e3; color: #d98b00; }
.rf-false_negative { background: #fdeaea; color: #e5565f; }
.robust-ck { margin-right: 0; }
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
.dim-note { font-size: 12px; color: #5a6b7b; }
.summary { margin-top: 10px; font-size: 13px; color: #5a6b7b; }
/* markdown 渲染的判定理由:紧凑排版(嵌在表格展开区,间距要小) */
.dim-note :deep(p), .summary :deep(p) { margin: 2px 0; }
.dim-note :deep(ul), .dim-note :deep(ol), .summary :deep(ul), .summary :deep(ol) { padding-left: 18px; margin: 2px 0; }
.dim-note :deep(code), .summary :deep(code) { background: #eef2f6; border-radius: 3px; padding: 0 4px; font-family: 'JetBrains Mono', monospace; font-size: 11px; }
.dim-note :deep(pre), .summary :deep(pre) { background: #f6f8fa; border-radius: 5px; padding: 6px 10px; overflow: auto; margin: 4px 0; }
.summary :deep(p:first-child) { display: inline; }
</style>
