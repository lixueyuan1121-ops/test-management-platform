<template>
  <div class="case-library functional-workspace">
    <WorkspacePage title="功能用例库">
      <template #actions>
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
      </template>
      <template #filters>
            <el-select v-model="reviewStatus" placeholder="采纳状态" size="small" clearable style="width:120px" @change="reload">
              <el-option label="已采纳" value="adopted" />
              <el-option label="已否决" value="rejected" />
              <el-option label="待定" value="pending" />
            </el-select>
            <TaskPicker v-model="taskId" :tasks="tasks" placeholder="关联任务" @change="reload" />
            <el-select v-model="category" placeholder="维度" size="small" clearable style="width:110px" @change="reload">
              <el-option v-for="c in CATEGORIES" :key="c" :label="c" :value="c" />
            </el-select>
            <el-select v-model="execKindFilter" placeholder="执行类型" size="small" clearable style="width:120px" @change="reload">
              <el-option v-for="k in EXEC_KINDS" :key="k.value" :label="k.label" :value="k.value" />
            </el-select>
            <el-select v-model="platformFilter" placeholder="平台" size="small" clearable style="width:110px" @change="reload">
              <el-option v-for="p in PLATFORMS" :key="p.value" :label="p.label" :value="p.value" />
            </el-select>
            <el-select
              v-if="pageOptions.length" v-model="pageFilter" placeholder="页面" size="small"
              clearable filterable style="width:130px" @change="reload"
            >
              <el-option v-for="p in pageOptions" :key="p" :label="p" :value="p" />
            </el-select>
            <el-input
              v-model="keyword" placeholder="按测试点搜索" size="small" clearable style="width:180px"
              @keyup.enter="reload" @clear="reload"
            />
            <el-button size="small" :icon="Search" @click="reload">查询</el-button>
      </template>
      <template #selection>
      <div v-if="selected.length" class="dispatch-bar">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <el-select v-model="runner" size="small" style="width:180px"
                   :placeholder="myDevices.length ? '选择我的设备' : '未登记设备'" no-data-text="去『我的设备』注册">
          <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
        </el-select>
        <el-button type="primary" size="small" :loading="dispatching" @click="openDispatchOrder">发送到执行机</el-button>
        <el-checkbox v-model="autoPrepare">自动补齐条件并执行全部所选用例</el-checkbox>
        <span v-if="autoPrepare" class="text-secondary">缺少对话、任务时创建测试数据；无法准备的用例也会给出阻塞报告</span>
        <el-divider direction="vertical" />
        <el-button size="small" @click="bulkSetRegressionFlag(true)">标记回归</el-button>
        <el-button size="small" plain @click="bulkSetRegressionFlag(false)">取消回归</el-button>
        <el-button size="small" @click="bulkReview('adopted')">批量采纳</el-button>
        <el-button size="small" type="danger" plain @click="bulkDelete">批量删除</el-button>
        <el-divider direction="vertical" />
        <el-button v-if="selectedFixCount" size="small" type="warning" plain :loading="fixing"
                   @click="bulkFixSelectors">批量补选择器({{ selectedFixCount }} 条待补)</el-button>
      </div>
      </template>
      <el-table :data="displayRows" v-loading="loading" size="small" border stripe empty-text="没有符合条件的用例"
                @selection-change="(s) => (selected = s)">
        <el-table-column type="selection" width="42" />
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
        <el-table-column label="执行类型" width="150" align="center">
          <template #default="{ row }">
            <el-tooltip :disabled="!row.kind_reason" :content="'AI 判定：' + (row.kind_reason || '')" placement="top">
              <el-select :model-value="row.exec_kind || 'gui'" size="small" style="width:90px"
                         @change="(v) => onExecKindChange(row, v)">
                <el-option v-for="k in EXEC_KINDS" :key="k.value" :label="k.label" :value="k.value" />
              </el-select>
            </el-tooltip>
            <el-tooltip v-if="row.selector_fix" :content="row.kind_reason" placement="top">
              <el-tag type="warning" size="small" effect="plain" class="sel-fix-tag">
                补齐元素后可自动化
              </el-tag>
            </el-tooltip>
            <el-tooltip
              v-if="row.selector_fix && row.selector_fix_keys && row.selector_fix_keys.length"
              effect="light" placement="right" :show-after="150" :hide-after="100"
              popper-class="missing-selector-tooltip" @before-show="loadMissingTargets(row)"
            >
              <template #content>
                <div style="max-width:min(560px,75vw);max-height:55vh;overflow:auto">
                  <div v-for="key in row.selector_fix_keys" :key="key" style="padding:6px 0">
                    <SelectorTargetNotes :selector-key="key" :notes="row._targetNotes?.[key] || [{ title: row._targetError ? '说明加载失败，请移开后重新悬浮重试' : '正在读取对应的操作步骤…' }]" />
                  </div>
                </div>
              </template>
              <el-button link type="primary" size="small" style="max-width:100%;height:auto;white-space:normal;overflow-wrap:anywhere">{{ row.selector_fix_keys.join(', ') }}</el-button>
            </el-tooltip>
            <el-button
              v-if="row.selector_fix && row.selector_fix_keys && row.selector_fix_keys.length"
              link type="primary" size="small" class="locate-key-btn" @click="locateMissingKeys(row)"
            >去补充元素</el-button>
          </template>
        </el-table-column>
        <el-table-column label="页面" width="120" align="center">
          <template #default="{ row }">
            <template v-if="row.page">
              <el-tag v-for="p in row.page.split(',').filter(Boolean)" :key="p" size="small" effect="plain" class="page-tag">{{ p }}</el-tag>
            </template>
            <span v-else class="page-none">—</span>
          </template>
        </el-table-column>
        <el-table-column label="回归" width="60" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_regression" type="success" size="small" effect="dark">回归</el-tag>
            <span v-else class="page-none">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="测试点" min-width="200" show-overflow-tooltip />
        <el-table-column label="步骤" min-width="200">
          <template #default="{ row }"><span class="multiline">{{ row.steps || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="预期结果" min-width="180">
          <template #default="{ row }"><span class="multiline">{{ row.expected || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="采纳状态" width="100" align="center">
          <template #default="{ row }">
            <el-select :model-value="row.review_status || 'pending'" size="small" style="width:80px"
                       @change="(v) => onReviewChange(row, v)">
              <el-option label="采纳" value="adopted" />
              <el-option label="否决" value="rejected" />
              <el-option label="待定" value="pending" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="关联任务" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.task_title || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="info" size="small" @click="openDetail(row)">详情</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100, 200]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="load"
          @size-change="reload"
        />
      </div>
    </WorkspacePage>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="edit.visible" title="编辑用例" width="560px" :close-on-click-modal="!edit.regen" :close-on-press-escape="!edit.regen" :show-close="!edit.regen">
      <el-form label-width="72px" :disabled="edit.regen || edit.saving">
        <el-form-item label="标题"><el-input v-model="edit.title" /></el-form-item>
        <el-form-item label="维度">
          <el-select v-model="edit.category" clearable style="width:140px">
            <el-option v-for="c in CATEGORIES" :key="c" :label="c" :value="c" />
          </el-select>
          <el-select v-model="edit.priority" clearable style="width:100px;margin-left:8px" placeholder="优先级">
            <el-option v-for="p in ['P0','P1','P2','P3']" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="前置条件"><el-input v-model="edit.precondition" type="textarea" :rows="2" maxlength="4000" placeholder="执行前需要的页面、登录状态和测试数据；请与步骤保持一致" /></el-form-item>
        <el-form-item label="步骤"><el-input v-model="edit.steps" type="textarea" :rows="4" placeholder="可多步,换行分隔" /></el-form-item>
        <el-form-item label="预期"><el-input v-model="edit.expected" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="页面">
          <el-select
            v-model="edit.pages" multiple filterable allow-create default-first-option
            placeholder="关联选择器页面(可多选/输入);重生 script 会按用到的 key 自动校正" style="width:100%"
          >
            <el-option v-for="p in pageOptions" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="回归">
          <el-switch v-model="edit.is_regression" />
          <span class="edit-hint" style="margin-left:10px">纳入回归用例库后,可在「仅回归」视图按页面勾选直接执行(不依赖任务/采纳)</span>
        </el-form-item>
      </el-form>
      <el-alert v-if="edit.error" :title="edit.error" type="error" :closable="false" show-icon />
      <p v-if="edit.progress" role="status">{{ edit.progress }}</p>
      <template #footer>
        <span class="edit-hint">改了步骤建议重生 script 同步;「待补选择器」的降级用例——在「选择器管理」补齐 key 后,点此即可一键恢复为可执行 gui/e2e</span>
        <el-button :disabled="edit.regen || edit.saving" @click="edit.visible = false">取消</el-button>
        <el-button :loading="edit.regen" :disabled="edit.saving" @click="doEditAndRegen">保存并重生 script</el-button>
        <el-button type="primary" :loading="edit.saving" :disabled="edit.regen" @click="doEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detail.visible" title="用例详情" size="min(720px, 100vw)">
      <div v-if="detail.row" class="detail">
        <p><b>{{ detail.row.title }}</b></p>
        <p class="d-row"><span class="d-k">执行类型</span> {{ (detail.row.exec_kind || 'gui').toUpperCase() }}</p>
        <p v-if="detail.row.page" class="d-row"><span class="d-k">关联页面</span> {{ detail.row.page }}</p>
        <p v-if="detail.row.is_regression" class="d-row"><span class="d-k">回归</span> <el-tag type="success" size="small" effect="dark">回归用例</el-tag></p>
        <p v-if="detail.row.kind_reason" class="d-row"><span class="d-k">判定理由</span> {{ detail.row.kind_reason }}</p>
        <div v-if="detail.row.last_gen_error" class="d-row">
          <span class="d-k">上次重生失败</span>
          <pre class="d-pre d-err">{{ detail.row.last_gen_error }}</pre>
        </div>
        <p class="d-row"><span class="d-k">维度/优先级</span> {{ detail.row.category || '—' }} / {{ detail.row.priority || '—' }}</p>
        <p class="d-row"><span class="d-k">步骤</span></p>
        <pre class="d-pre">{{ detail.row.steps || '—' }}</pre>
        <p class="d-row"><span class="d-k">预期</span></p>
        <pre class="d-pre">{{ detail.row.expected || '—' }}</pre>
        <div class="d-row d-script-head">
          <span class="d-k">关联用例(前置)</span>
          <el-button link type="primary" size="small" @click="openPrereqPick">+ 添加前置</el-button>
        </div>
        <div v-if="prereq.loading" class="edit-hint">加载中…</div>
        <div v-else-if="!prereq.list.length" class="edit-hint">未关联前置用例。执行本用例时,关联的前置会按顺序先执行、再执行本用例。</div>
        <ol v-else class="prereq-list">
          <li v-for="(p, i) in prereq.list" :key="p.id" class="prereq-item">
            <span class="prereq-idx">{{ i + 1 }}</span>
            <span class="prereq-title">{{ p.title || ('用例#' + p.prereq_case_id) }}</span>
            <el-tag size="small" effect="plain">{{ (p.exec_kind || 'gui').toUpperCase() }}</el-tag>
            <el-button link type="danger" size="small" @click="delPrereq(p)">移除</el-button>
          </li>
        </ol>
        <template v-if="detail.row.acceptance_links?.length">
          <p class="d-row"><span class="d-k">验收依据</span></p>
          <div v-for="link in detail.row.acceptance_links" :key="link.criterion_id">
            <p>{{ link.criterion_id }} · {{ link.rule_title }} · 确认版本 #{{ link.baseline_id }}</p>
            <pre class="d-pre">{{ link.text }}</pre>
            <p>{{ link.source_section }}</p><pre class="d-pre">{{ link.source_quote || '按评审补充规则确认' }}</pre>
          </div>
        </template>
        <div class="d-row d-script-head">
          <span class="d-k">script</span>
          <el-button
            v-if="!detail.loading && canEditScript(detail.row) && !scriptEdit.on"
            link type="primary" size="small" @click="startScriptEdit"
          >编辑</el-button>
          <template v-if="scriptEdit.on">
            <el-button link size="small" @click="scriptEdit.on = false">取消</el-button>
            <el-button link type="primary" size="small" :loading="scriptEdit.saving" @click="saveScript">保存</el-button>
          </template>
        </div>
        <pre v-if="!scriptEdit.on" class="d-pre">{{ detail.loading ? '加载中…' : prettyScript(detail.row.script) }}</pre>
        <template v-else>
          <el-input v-model="scriptEdit.text" type="textarea" :rows="14" spellcheck="false" style="font-family:monospace" />
          <span class="edit-hint">直接编辑结构化步骤数组(JSON)。保存时按用例类型校验:gui/e2e 需 action 合法+定位步带 key/selector+至少一个断言,且 key 须已在选择器管理注册;api 校验请求-断言-提取。</span>
        </template>
      </div>
    </el-drawer>
    <el-dialog v-model="dispatchOrder.on" title="调整执行顺序" width="560px">
      <div class="order-tip">执行机将按下列顺序依次执行；不调整即按当前(选中)顺序执行。</div>
      <ol class="prereq-list">
        <li v-for="(it, i) in dispatchOrder.rows" :key="it.id" class="order-item">
          <span class="prereq-idx">{{ i + 1 }}</span>
          <span class="prereq-title">{{ it.title }}</span>
          <span>
            <el-button link size="small" :disabled="i === 0" @click="moveDispatch(i, -1)">↑ 上移</el-button>
            <el-button link size="small" :disabled="i === dispatchOrder.rows.length - 1" @click="moveDispatch(i, 1)">↓ 下移</el-button>
          </span>
        </li>
      </ol>
      <template #footer>
        <el-button @click="dispatchOrder.on = false">取消</el-button>
        <el-button type="primary" :loading="dispatching" @click="confirmDispatchOrder">按此顺序执行</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="prereq.pickVisible" title="选择前置用例" width="600px">
      <el-input v-model="prereq.pickKeyword" placeholder="按标题搜索" clearable size="small" style="margin-bottom:8px" />
      <div v-if="prereq.pickLoading" class="edit-hint">加载中…</div>
      <div v-else-if="!prereqCandidates.length" class="edit-hint">无可选用例(已排除自己和已关联项)。</div>
      <ul v-else class="prereq-pick-list">
        <li v-for="c in prereqCandidates" :key="c.id" class="prereq-pick-item">
          <span class="prereq-title">{{ c.title }}</span>
          <el-tag size="small" effect="plain">{{ (c.exec_kind || 'gui').toUpperCase() }}</el-tag>
          <el-button link type="primary" size="small" @click="pickPrereq(c)">添加</el-button>
        </li>
      </ul>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Search } from '@element-plus/icons-vue'
import WorkspacePage from '@/components/WorkspacePage.vue'
import '@/styles/workspace-overlays.css'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter, useRoute } from 'vue-router'
import { useAppStore } from '@/store/app'
import { listTasks, listCases, getTestcase, setCaseExecKind, attachChecklist, enqueueExec, enqueueCases, listMyDevices, reviewTestcase, updateTestcase, deleteTestcase, genTestcaseScript, listSelectors, bulkSetRegression, listPrereqs, addPrereq, removePrereq } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { collectMissingKeys } from '@/utils/bulk-fix-selectors'
import SelectorTargetNotes from '@/components/SelectorTargetNotes.vue'
import { describeSelectorTarget } from '@/utils/selector-target-description'
import TaskPicker from '@/components/TaskPicker.vue'

// 维度 / 优先级 → el-tag 配色（与 AITestGen 口径一致）
const CAT_TYPE = { 功能: 'primary', 边界: 'warning', 异常: 'danger', 兼容: 'info', 性能: 'success' }
const PRI_TYPE = { P0: 'danger', P1: 'warning', P2: 'primary', P3: 'info' }
const CATEGORIES = ['功能', '边界', '异常', '兼容', '性能']
// 采纳三态 → 配色/文案（采纳=success / 否决=danger / 待定=info）
const RV_TYPE = { adopted: 'success', rejected: 'danger', pending: 'info' }
const RV_LABEL = { adopted: '已采纳', rejected: '已否决', pending: '待定' }
// 自动化执行类型：gui(客户端 UI) / api(接口) / cli(命令行) / e2e(多步端到端) / manual(人工，不下发)。
// 下发到 runner 时决定 Claude Code 怎么跑；manual 不派发到执行机。
const EXEC_KINDS = [
  { value: 'gui', label: 'GUI' },
  { value: 'api', label: 'API' },
  { value: 'cli', label: 'CLI' },
  { value: 'e2e', label: 'E2E' },
  { value: 'manual', label: '人工' },
]
const PLATFORMS = [
  { value: 'web', label: 'PC/Web' },
  { value: 'android', label: 'Android' },
  { value: 'ios', label: 'iOS' },
]

const app = useAppStore()
const router = useRouter(), route = useRoute()
const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const taskId = ref(null)
const reviewStatus = ref(null)
const category = ref(null)
const pageFilter = ref(null)        // 页面筛选(null=全部),下推后端精确匹配
const pageOptions = ref([])         // 页面下拉候选:当前项目选择器里已有的 page(去重)
const keyword = ref('')
const rows = ref([])
const loading = ref(false)
const execKindFilter = ref(null)   // 执行类型筛选(null=全部),下推后端
const platformFilter = ref(null)   // 平台筛选(null=全部):web/android/ios

// 分页(后端分页:total 为过滤后总数)
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)

// 展示行直接用后端返回的当前页(排序/筛选均已下推后端)
const displayRows = rows

// ---- 下发到执行机(用例库入口)----
// 可下发的执行机 = 当前成员登记的"我的设备"(下发到自己机器执行)。
const myDevices = ref([])
const selected = ref([])
const runner = ref('')
const dispatching = ref(false)
const dispatchOrder = reactive({ on: false, rows: [] })   // 发送到执行机前的排序弹窗:rows=[{id,title,_row}]
const autoPrepare = ref(true)
const fixing = ref(false)
const selectedFixCount = computed(() => selected.value.filter((r) => r.selector_fix).length)

// 某行能否下发:必须已采纳(attachChecklist 要求)+ 有关联任务 + 非 manual。
function canDispatch(row) {
  return row.review_status === 'adopted' && !!row.task_id && (row.exec_kind || 'gui') !== 'manual'
}

// 「仅因选择器缺失而降级」由后端算好 selector_fix / selector_fix_keys(见 _to_case_out),
// 前端直接用,无需解析 kind_reason 文本。

// 用例库下发:先让用户确认/调整执行顺序,再按序下发。两条下发路径(autoPrepare→enqueueCases、
// 普通→attachChecklist+enqueueExec)都保序(数组顺序即执行顺序),故排序只需产出有序行数组。
function openDispatchOrder() {
  if (!selected.value.length) return
  if (!runner.value) { ElMessage.warning('请先选择执行设备(去『我的设备』注册)'); return }
  // 参与排序/下发的行:autoPrepare 模式全选中项都发;普通模式只发可下发项。
  const rows = autoPrepare.value ? selected.value.slice() : selected.value.filter(canDispatch)
  if (!rows.length) {
    ElMessage.warning(autoPrepare.value ? '没有选中用例' : '选中项里没有可下发的用例(需:已采纳 + 有关联任务 + 非人工)')
    return
  }
  dispatchOrder.rows = rows.map((r) => ({ id: r.id, title: r.title, _row: r }))
  if (dispatchOrder.rows.length === 1) { doDispatch(dispatchOrder.rows.map((x) => x._row)); return }
  dispatchOrder.on = true
}

function moveDispatch(i, dir) {
  const j = i + dir
  const arr = dispatchOrder.rows
  if (j < 0 || j >= arr.length) return
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
}

async function confirmDispatchOrder() {
  await doDispatch(dispatchOrder.rows.map((x) => x._row))
  if (!dispatching.value) dispatchOrder.on = false
}

// 按给定有序行数组下发(autoPrepare / 普通两条路径,均按数组顺序)。
async function doDispatch(orderedRows) {
  if (autoPrepare.value) {
    dispatching.value = true
    try {
      const res = await enqueueCases(pid.value, runner.value, orderedRows.map(r => r.id), null, true)
      ElMessage.success(`全部 ${res.run_ids.length} 条已按指定顺序下发，执行时自动检查并补齐条件；每条都会保留结果`)
      dispatchOrder.on = false
      router.push({ path: '/exec-results', query: { project_id: pid.value, batch_id: res.batch_id } })
    } catch { /* http 拦截器已提示 */ }
    finally { dispatching.value = false }
    return
  }
  const items = orderedRows.filter(canDispatch)
  if (!items.length) { ElMessage.warning('选中项里没有可下发的用例(需:已采纳 + 有关联任务 + 非人工)'); return }
  const skipped = orderedRows.length - items.length
  dispatching.value = true
  try {
    // 普通路径要经 attachChecklist 拿 itemIds。为保执行顺序,逐条 attach、按 items 顺序收集 itemId
    //(而非按任务分组,否则同任务会打乱用户排序)。attachChecklist 幂等,单条调用可接受。
    const itemIds = []
    for (const r of items) {
      const checklist = await attachChecklist(r.task_id, [r.id])
      const hit = checklist.find((it) => it.test_case_id === r.id)
      if (hit) itemIds.push(hit.id)
    }
    if (!itemIds.length) { ElMessage.warning('未能生成可下发的清单项'); return }
    const res = await enqueueExec(pid.value, runner.value, itemIds)
    const n = res?.run_ids?.length || itemIds.length
    ElMessage.success(`已按指定顺序下发 ${n} 条到 ${runner.value}${skipped ? `(跳过 ${skipped} 条不可下发)` : ''},执行机跑完会自动回写结果`)
    dispatchOrder.on = false
  } catch { /* http 拦截器已提示 */ }
  finally { dispatching.value = false }
}

// ---- 回归:批量标记/取消 ----
async function bulkSetRegressionFlag(flag) {
  if (!selected.value.length) return
  const ids = selected.value.map((r) => r.id)
  try {
    const res = await bulkSetRegression(pid.value, ids, flag)
    ElMessage.success(`${flag ? '已标记' : '已取消'}回归 ${res?.updated ?? ids.length} 条`)
    await load()
  } catch { /* 已提示 */ }
}

onMounted(async () => {
  // 设备与项目列表互不依赖,并行拉取;项目列表走 store 缓存
  const [devicesRes, projectsRes] = await Promise.allSettled([listMyDevices(), app.fetchProjects()])
  myDevices.value = devicesRes.status === 'fulfilled' ? devicesRes.value : []
  if (myDevices.value.length) runner.value = myDevices.value[0].runner_id
  projects.value = projectsRes.status === 'fulfilled' ? projectsRes.value : []
  if (projects.value.length) {
    pid.value = projects.value.find(p => p.id === Number(route.query.project_id))?.id || pickDefaultProjectId(projects.value)
    await onProjectChange()
    if (tasks.value.some(t => t.id === Number(route.query.task_id))) { taskId.value = Number(route.query.task_id); await reload() }
  }
})

async function onProjectChange() {
  taskId.value = null
  pageFilter.value = null
  pageOptions.value = []
  if (!pid.value) { tasks.value = []; rows.value = []; total.value = 0; return }
  setLastProjectId(pid.value)
  tasks.value = await listTasks({ project_id: pid.value })
  // 页面候选:从项目选择器(共享域)派生 distinct page,失败不影响列表。
  try {
    const data = await listSelectors(pid.value)
    pageOptions.value = [...new Set((data.shared || []).map((k) => k.page).filter(Boolean))].sort()
  } catch { pageOptions.value = [] }
  await reload()
}

// 筛选条件变化:回到第 1 页再查(翻页/改页大小则直接 load)
async function reload() {
  page.value = 1
  await load()
}

async function load() {
  const version = ++listLoadVersion
  if (!pid.value) return
  loading.value = true
  try {
    const { items, total: t } = await listCases({
      project_id: pid.value,
      task_id: taskId.value || undefined,
      review_status: reviewStatus.value || undefined,
      category: category.value || undefined,
      exec_kind: execKindFilter.value || undefined,
      platform: platformFilter.value || undefined,
      page: pageFilter.value || undefined,
      keyword: keyword.value.trim() || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (version !== listLoadVersion) return
    rows.value = items || []
    total.value = t || 0
  } finally { if (version === listLoadVersion) loading.value = false }
}

let listLoadVersion = 0
async function loadMissingTargets(row) {
  if (row._targetLoading || row._targetNotes) return
  row._targetLoading = true
  row._targetError = false
  try {
    const detail = await getTestcase(row.id)
    row._targetNotes = Object.fromEntries((row.selector_fix_keys || []).map(key => {
      const notes = describeSelectorTarget(key, detail.script)
      return [key, notes.length ? notes : [{ title: '脚本未说明具体元素，请补充对应操作描述' }]]
    }))
  } catch { row._targetError = true }
  finally { row._targetLoading = false }
}

function fmtTime(s) {
  if (!s) return '—'
  return String(s).replace('T', ' ').slice(0, 16)
}

async function onExecKindChange(row, val) {
  const prev = row.exec_kind || 'gui'
  if (val === prev) return
  row.exec_kind = val   // 乐观更新
  try {
    await setCaseExecKind(row.id, val)
    ElMessage.success(`已设为 ${val.toUpperCase()} 执行`)
  } catch {
    row.exec_kind = prev   // 失败回滚（http 拦截器已弹错）
  }
}

// 改采纳状态(乐观更新,失败回滚)
async function onReviewChange(row, val) {
  const prev = row.review_status || 'pending'
  if (val === prev) return
  row.review_status = val
  try {
    await reviewTestcase(row.id, val)
    ElMessage.success('采纳状态已更新')
  } catch { row.review_status = prev }
}

// 「定位缺失 key」：带上项目/页面/缺失 key/用例上下文跳到选择器管理，触发按上下文探测并高亮匹配元素。
// steps/title 作为语义匹配上下文（中文文案命中元素可见文字，key 名命中英文属性）。见 SelectorAdmin fixKeys 分支。
async function locateMissingKeys(row) {
  const detail = await getTestcase(row.id)
  // 只带**当前仍未注册**的 key：优先用后端算的 missing_selector_keys(script 引用了但注册表没有的),
  // 退回生成期 selector_fix_keys。否则会把整条链路里已补好的 key 也带过去,导致重复添加(图11/12)。
  const genKeys = row.selector_fix_keys || detail.selector_fix_keys || []
  const fixKeys = (detail.missing_selector_keys && detail.missing_selector_keys.length)
    ? detail.missing_selector_keys
    : (detail.missing_selector_keys ? [] : genKeys)   // 后端返回了空数组=全已注册,不再带任何 key
  if (!fixKeys.length) { ElMessage.info('该用例引用的选择器 key 均已注册，可直接「批量回填」恢复执行'); return }
  const { contexts, hints } = collectMissingKeys([{ ...detail, selector_fix: true, selector_fix_keys: fixKeys }], [])
  router.push({
    name: 'selectors',
    query: {
      project_id: pid.value,
      page: (row.page || '').split(',').filter(Boolean)[0] || '',
      sub_product: row.sub_product || '',
      case_ids: String(row.id),
      fix_keys: fixKeys.join(','),
      key_contexts: JSON.stringify(contexts),
      key_hints: JSON.stringify(hints),
      ctx: `${row.title || ''} ${row.steps || ''}`.trim().slice(0, 200),
    },
  })
}

// 「批量补选择器」:汇总选中待补用例缺的 key(去重、剔除已注册 key=遵循已有覆盖),带全部缺 key +
// 合并上下文跳「选择器管理」,在那批量探测+匹配+建 key(候选来自真实探测)。补齐后自动回填复活。
async function bulkFixSelectors() {
  const fixCases = selected.value.filter((r) => r.selector_fix)
  if (!fixCases.length) { ElMessage.warning('选中的用例里没有「选择器待补」的'); return }
  const scopes = new Set(fixCases.map(c => c.sub_product || ''))
  if (scopes.size !== 1) { ElMessage.warning('请按相同子产品作用域分批补选择器，避免同名 key 串用'); return }
  const scope = [...scopes][0]
  fixing.value = true
  try {
    // 读当前项目已注册 key(共享 + 各子产品),剔除已覆盖的(遵循已有覆盖,不重复建)。
    const registered = new Set()
    try {
      const sel = await listSelectors(pid.value)
      const merged = new Map((sel?.shared || []).map(row => [row.key, row]))
      for (const row of (sel?.by_sub?.[scope] || [])) merged.set(row.key, row)
      for (const row of merged.values()) if ((row.candidates || []).some(c => c.by && c.value && c.src !== 'learned' && !['pending','rejected','retired'].includes(c.status) && !c.disabled)) registered.add(row.key)
    } catch { /* 读不到就不剔除,交由探测阶段 matchStatus 兜底判已存在 */ }
    // 列表不含脚本，读取所选用例的完整步骤，逐 key 提取相关描述。
    const detailedCases = []
    for (let i = 0; i < fixCases.length; i += 8) {
      detailedCases.push(...await Promise.all(fixCases.slice(i, i + 8).map(c => getTestcase(c.id))))
    }
    const { keys, skipped, caseCount, ctx, contexts, hints } = collectMissingKeys(detailedCases, registered)
    if (!keys.length) {
      ElMessage.info(skipped.length
        ? `选中 ${caseCount} 条待补用例的 key 均已注册,试试「批量回填」或去选择器管理确认`
        : '选中的待补用例没有可补的 key')
      return
    }
    const msg = `将为选中的 ${caseCount} 条待补用例补齐 ${keys.length} 个缺失选择器 key`
      + `${skipped.length ? `(另 ${skipped.length} 个已注册,跳过)` : ''}。`
      + `\n即将跳转「选择器管理」:请在被测客户端切到目标页/弹窗后探测,系统会自动把探测元素匹配到这些 key,一键批量建。`
    try { await ElMessageBox.confirm(msg, '批量补选择器', { confirmButtonText: '去探测补齐', type: 'info' }) }
    catch { return }
    router.push({
      name: 'selectors',
      query: {
        project_id: pid.value,
        page: (fixCases.map((r) => (r.page || '').split(',').filter(Boolean)[0]).find(Boolean)) || '',
        sub_product: scope,
        case_ids: fixCases.map(c => c.id).join(','),
        fix_keys: keys.join(','),
        key_contexts: JSON.stringify(contexts),
        key_hints: JSON.stringify(hints),
        ctx: ctx.slice(0, 200),
        bulk: '1',   // 标记批量模式:SelectorAdmin 展示待补清单 + 批量匹配/建 key
      },
    })
  } finally { fixing.value = false }
}

// ---- 编辑 ----
const edit = reactive({ visible: false, id: null, title: '', precondition: '', steps: '', expected: '', category: null, priority: null, pages: [], is_regression: false, saving: false, regen: false, error: '', progress: '' })
function openEdit(row) {
  edit.error = ''
  edit.progress = ''
  edit.id = row.id
  edit.title = row.title || ''
  edit.steps = row.steps || ''
  edit.precondition = row.precondition || ''
  edit.expected = row.expected || ''
  edit.category = row.category || null
  edit.priority = (row.priority || '').toUpperCase() || null
  edit.pages = row.page ? row.page.split(',').filter(Boolean) : []
  edit.is_regression = !!row.is_regression
  edit.visible = true
}
async function doEdit() {
  if (!edit.title.trim()) { ElMessage.warning('标题不能为空'); return }
  edit.saving = true
  try {
    await updateTestcase(edit.id, {
      title: edit.title.trim(), precondition: edit.precondition, steps: edit.steps, expected: edit.expected,
      category: edit.category || '', priority: edit.priority || '', page: edit.pages.join(','),
      is_regression: edit.is_regression,
    })
    edit.visible = false
    ElMessage.success('已保存')
    await load()
  } catch { /* 已提示 */ }
  finally { edit.saving = false }
}

// 保存正文后重生 script。gui/e2e 按最新 steps 重生;「待补选择器」降级的 manual 用例后端会一键按原意图恢复。
async function doEditAndRegen() {
  if (edit.regen || edit.saving) return
  if (!edit.title.trim()) { ElMessage.warning('标题不能为空'); return }
  edit.regen = true
  edit.error = ''
  edit.progress = '正在保存用例…'
  let saved = false
  try {
    await updateTestcase(edit.id, {
      title: edit.title.trim(), precondition: edit.precondition, steps: edit.steps, expected: edit.expected,
      category: edit.category || '', priority: edit.priority || '', page: edit.pages.join(','),
      is_regression: edit.is_regression,
    })
    saved = true
    edit.progress = '正文已保存，正在提交脚本生成任务…'
    await genTestcaseScript(edit.id, { forceRegenerate: true, silent: true, onTick: job => {
      edit.progress = job.status === 'pending' ? '正文已保存，脚本生成正在排队…' : '正文已保存，正在按最新步骤和预期生成脚本…'
    } })
    edit.visible = false
    ElMessage.success('已保存并重生 script')
    await load()
  } catch (error) {
    const reason = error.response?.data?.detail || error.message || '请重试'
    edit.error = `${saved ? '正文已保存，但脚本未生成成功' : '保存失败'}：${reason}`
  } finally { edit.regen = false; edit.progress = '' }
}

// ---- 详情 ----
// 列表行已瘦身不含 script,打开详情时按 id 单取完整用例补上 script。
const detail = reactive({ visible: false, row: null, loading: false })
// script 直编态:on=编辑中,text=编辑区 JSON 文本,saving=保存中。
const scriptEdit = reactive({ on: false, text: '', saving: false })
// 关联前置用例:list=已挂前置;pick* = 挑选弹窗
const prereq = reactive({ loading: false, list: [], pickVisible: false, pickLoading: false, candidates: [], pickKeyword: '' })
const prereqCandidates = computed(() => {
  const kw = prereq.pickKeyword.trim().toLowerCase()
  return kw ? prereq.candidates.filter((c) => (c.title || '').toLowerCase().includes(kw)) : prereq.candidates
})
async function openDetail(row) {
  detail.row = { ...row }   // 先用列表行(含 steps/expected)即时展示
  detail.visible = true
  detail.loading = true
  scriptEdit.on = false     // 每次打开详情重置编辑态
  loadPrereqs(row.id)       // 并行拉前置列表
  try {
    const full = await getTestcase(row.id)
    if (detail.row && detail.row.id === row.id) detail.row = full
  } catch { /* http 拦截器已提示;steps/expected 仍可见,仅 script 缺 */ }
  finally { detail.loading = false }
}

// ---- 关联前置用例 ----
async function loadPrereqs(cid) {
  prereq.list = []
  prereq.loading = true
  try { prereq.list = await listPrereqs(cid) } catch { prereq.list = [] }
  finally { prereq.loading = false }
}
async function openPrereqPick() {
  prereq.pickVisible = true
  prereq.pickKeyword = ''
  prereq.candidates = []
  prereq.pickLoading = true
  try {
    // 拉本项目候选用例(排除自己 + 已关联的);复用 listCases。
    const { items } = await listCases({ project_id: pid.value, page: 1, page_size: 200 })
    const linked = new Set(prereq.list.map((p) => p.prereq_case_id))
    prereq.candidates = (items || []).filter((c) => c.id !== detail.row.id && !linked.has(c.id))
  } catch { prereq.candidates = [] }
  finally { prereq.pickLoading = false }
}
async function pickPrereq(c) {
  try {
    await addPrereq(detail.row.id, c.id)
    await loadPrereqs(detail.row.id)
    prereq.pickVisible = false
    ElMessage.success('已添加前置用例')
  } catch { /* http 拦截器已提示(如成环/跨项目) */ }
}
async function delPrereq(p) {
  try {
    await removePrereq(detail.row.id, p.id)
    await loadPrereqs(detail.row.id)
    ElMessage.success('已移除前置')
  } catch { /* http 拦截器已提示 */ }
}
function prettyScript(s) {
  if (!s) return '(无 script,该用例由 claude 兜底执行或非结构化)'
  try { return JSON.stringify(typeof s === 'string' ? JSON.parse(s) : s, null, 2) } catch { return String(s) }
}
// 仅 gui/e2e/api 用例支持编辑 script(manual/cli 无结构化 script)。
function canEditScript(row) {
  return row && ['gui', 'e2e', 'api'].includes((row.exec_kind || 'gui'))
}
function startScriptEdit() {
  // 用当前 script 预填编辑区(格式化);空 script 给个空数组模板。
  const s = detail.row?.script
  scriptEdit.text = s ? prettyScript(s) : '[]'
  scriptEdit.on = true
}
async function saveScript() {
  let parsed
  try {
    parsed = JSON.parse(scriptEdit.text)
  } catch (e) {
    ElMessage.error('JSON 格式错误:' + e.message)
    return
  }
  if (!Array.isArray(parsed)) { ElMessage.error('script 必须是步骤数组(以 [ 开头)'); return }
  scriptEdit.saving = true
  try {
    const updated = await updateTestcase(detail.row.id, { script: parsed })   // 后端按 kind 校验;不合法弹 msg
    detail.row = updated                 // 用回写结果刷新(含重推的 page)
    scriptEdit.on = false
    ElMessage.success('script 已保存')
    await load()                         // 列表可能有 page 等展示变化
  } catch { /* 校验失败等已由 http 拦截器提示 */ }
  finally { scriptEdit.saving = false }
}

// ---- 删除 ----
async function onDelete(row) {
  try {
    await ElMessageBox.confirm(`删除用例「${row.title}」?其验收清单项会一并清理(执行历史保留)。`, '删除用例', { type: 'warning' })
  } catch { return }
  try { await deleteTestcase(row.id); ElMessage.success('已删除'); await load() } catch { /* 已提示 */ }
}

// ---- 批量 ----
async function bulkReview(statusVal) {
  const ids = selected.value.map((r) => r.id)
  if (!ids.length) return
  try {
    for (const id of ids) await reviewTestcase(id, statusVal)
    ElMessage.success(`已批量${statusVal === 'adopted' ? '采纳' : '更新'} ${ids.length} 条`)
    await load()
  } catch { /* 已提示 */ }
}
async function bulkDelete() {
  const ids = selected.value.map((r) => r.id)
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(`删除选中的 ${ids.length} 条用例?其验收清单项会一并清理。`, '批量删除', { type: 'warning' })
  } catch { return }
  try {
    for (const id of ids) await deleteTestcase(id)
    ElMessage.success(`已删除 ${ids.length} 条`)
    await load()
  } catch { /* 已提示 */ }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.prereq-list { margin: 4px 0; padding: 0; list-style: none; }
.prereq-item { display: flex; align-items: center; gap: 8px; padding: 4px 0; }
.prereq-idx { flex: none; width: 20px; height: 20px; line-height: 20px; text-align: center; background: #ecf5ff; color: #409eff; border-radius: 50%; font-size: 12px; }
.prereq-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.prereq-pick-list { margin: 0; padding: 0; list-style: none; max-height: 50vh; overflow: auto; }
.prereq-pick-item { display: flex; align-items: center; gap: 8px; padding: 6px 4px; border-bottom: 1px solid #f0f0f0; }
.order-tip { color: #909399; font-size: 13px; margin-bottom: 10px; }
.order-item { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-bottom: 1px solid #f0f0f0; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.dispatch-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; padding: 12px 0; border-top: 1px solid var(--el-border-color); }
.dispatch-bar .el-select { flex-shrink: 0; max-width: 100%; }
.dispatch-bar .el-button { margin-left: 0; }
.sel-info { font-size: 13px; font-weight: 600; color: var(--el-color-primary); white-space: nowrap; }
.sel-hint { color: #90a4ae; font-size: 12px; }
.edit-hint { color: #90a4ae; font-size: 12px; margin-right: auto; }
.sel-fix-tag { margin-top: 4px; cursor: help; display: block; height: auto; line-height: 1.5; white-space: normal; padding: 2px 6px; }
.page-tag { margin: 1px 2px; }
.page-none { color: #c0c4cc; }
.detail { font-size: 13px; color: #334; }
.detail .d-row { margin: 8px 0 2px; }
.detail .d-k { display: inline-block; min-width: 72px; color: #90a4ae; }
.detail .d-pre { background: #f5f7fa; border-radius: 6px; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; font-size: 12px; max-height: 220px; overflow: auto; }
.detail .d-err { background: #fef0f0; color: #c45656; }
.detail .d-script-head { display: flex; align-items: center; gap: 6px; }
.detail .d-script-head .d-k { min-width: auto; margin-right: auto; }
</style>
