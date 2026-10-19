<template>
  <div class="tasks functional-workspace">
    <WorkspacePage title="任务分配">
      <template #actions>
            <el-select v-model="pid" :disabled="dialog.visible || !!digestContext || releaseVisible" placeholder="选择项目" size="small" style="width:180px" @change="load">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <div @keydown.capture="onDateKeydown">
              <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" size="small" style="width:280px" clearable :editable="false" aria-label="筛选任务日期范围" start-placeholder="开始日期" end-placeholder="结束日期" range-separator="至" @change="load" />
            </div>
            <el-button v-if="canManage" size="small" @click="onCopy" :disabled="!pid || !singleDate" :title="singleDate ? '' : '请选择一个日期作为复制目标'">复制昨日</el-button>
            <el-button type="primary" size="small" v-if="canManage" @click="openCreate" :disabled="!pid">新建任务</el-button>
      </template>
      <template #filters>
        <el-input v-model="filters.keyword" clearable aria-label="搜索任务名称或负责人" placeholder="搜索任务、负责人、开发或卡点" style="width:260px" />
        <el-select v-model="filters.status" multiple collapse-tags collapse-tags-tooltip clearable aria-label="筛选任务状态" placeholder="全部状态" style="width:180px"><el-option v-for="(meta, key) in STATUS_META" :key="key" :label="meta.label" :value="key" /></el-select>
        <el-select v-model="filters.owner" multiple collapse-tags collapse-tags-tooltip clearable filterable placeholder="测试负责人" aria-label="筛选测试负责人" style="width:180px"><el-option label="待分配" value="__unset" /><el-option v-for="m in members" :key="m.user_id" :label="m.name" :value="m.user_id" /></el-select>
        <el-select v-model="filters.version" multiple collapse-tags collapse-tags-tooltip clearable filterable placeholder="归属版本" aria-label="筛选归属版本" style="width:180px"><el-option label="未填写版本" value="__unset" /><el-option v-for="v in versions" :key="v" :label="v" :value="v" /></el-select>
        <el-select v-model="filters.risk" clearable placeholder="风险等级" aria-label="筛选风险等级" style="width:150px"><el-option label="未评估" value="__unset" /><el-option v-for="(m,k) in RISK_META" :key="k" :label="m.label" :value="k" /></el-select>
        <el-select v-model="filters.priority" multiple collapse-tags collapse-tags-tooltip clearable placeholder="优先级" aria-label="筛选优先级" style="width:140px"><el-option v-for="p in ['p0','p1','p2','p3']" :key="p" :label="p.toUpperCase()" :value="p" /></el-select>
        <el-select v-model="filters.impact" clearable placeholder="是否影响发版" aria-label="筛选发版影响" style="width:160px"><el-option label="影响发版" value="yes" /><el-option label="不影响发版" value="no" /><el-option label="未评估" value="__unset" /></el-select>
        <el-switch v-model="filters.riskOnly" active-text="仅看风险/阻塞" />
        <el-button link @click="resetFilters">重置筛选</el-button>
      </template>
      <template #selection>
        <div class="ledger-summary">
          <span>筛选结果 <strong>{{ filteredTasks.length }}</strong> 项</span>
          <span>测试完成/已上线 <strong>{{ filteredTasks.filter(t => ['ready_online','online'].includes(t.status)).length }}</strong></span>
          <span>风险/阻塞 <strong>{{ filteredTasks.filter(hasRisk).length }}</strong></span>
          <span v-if="selectedTasks.length">已选 {{ selectedTasks.length }} 项 <el-button link @click="taskTable?.clearSelection()">清除选择</el-button></span>
          <div class="ledger-actions">
            <el-button v-if="canManage" size="small" :disabled="!actionTasks.length || loading" @click="openDigest(false)">编写日报{{ selectedTasks.length ? '（已选）' : '（筛选结果）' }}</el-button>
            <el-button v-if="canManage" size="small" :disabled="!pid" @click="openDigest(true)">历史日报</el-button>
            <el-button v-if="auth.isPlatformAdmin" size="small" :disabled="!releasableTasks.length || loading" @click="releaseRows = [...releasableTasks]; releaseVisible = true">填写发版记录（{{ releasableTasks.length }}）</el-button>
          </div>
        </div>
        <p class="cl-hint">{{ singleDate ? '单日跟踪包含当日任务、历史尚未上线/关闭的任务，以及当日上线或关闭的任务；历史任务标注顺延日期。' : dateRange?.length === 2 ? '按分配日期范围筛选（含起止日期）；清空日期可查看全部台账。' : '当前展示全部日期的任务台账。' }} 空白风险与发版影响表示未评估。</p>
      </template>
      <el-result v-if="loadError" icon="error" title="任务加载失败"><template #extra><el-button @click="load">重试</el-button></template></el-result>
      <el-table v-else ref="taskTable" :data="filteredTasks" v-loading="loading" size="small" empty-text="没有符合条件的任务" row-key="id" @expand-change="onExpandChange" @selection-change="selection = $event">
        <el-table-column v-if="canManage" type="selection" width="42" fixed />
        <el-table-column type="expand" fixed>
          <template #default="{ row }">
            <div class="cl-expand">
              <div v-if="!row._summary && !row._summaryFailed" class="cl-empty">该任务暂无验收清单</div>
              <template v-else>
                <div v-if="canManage" class="cl-toolbar">
                  <el-select v-model="row._runner" size="small" style="width:160px"
                             :placeholder="myDevices.length ? '选择我的设备' : '未登记设备'" no-data-text="去『我的设备』注册">
                    <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
                  </el-select>
                  <el-button type="primary" size="small" plain
                             :loading="row._enqueuing"
                             :disabled="!(row._checked && row._checked.length)"
                             @click="sendToRunner(row)">
                    发送到本地执行{{ row._checked && row._checked.length ? `（${row._checked.length}）` : '' }}
                  </el-button>
                  <span class="cl-hint">勾选后下发；执行机跑完自动回写通过/失败</span>
                </div>
                <el-table :data="row._items || []" v-loading="row._itemsLoading" size="small"
                          empty-text="无验收项"
                          @selection-change="(sel) => (row._checked = sel)">
                  <el-table-column type="selection" width="40" />
                  <el-table-column prop="title" label="测试点" min-width="180" show-overflow-tooltip />
                  <el-table-column prop="category" label="维度" width="80" />
                  <el-table-column label="类型" width="70" align="center">
                    <template #default="{ row: it }">
                      <el-tag :type="KIND_TYPE[it.exec_kind || 'gui'] || 'info'" size="small" effect="plain">
                        {{ KIND_LABEL[it.exec_kind || 'gui'] || it.exec_kind }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="结果" width="90">
                    <template #default="{ row: it }">
                      <el-popover v-if="it.exec_reason" placement="left" :width="360" trigger="click">
                        <template #reference>
                          <el-tag :type="EXEC_META[it.exec_status]?.type || 'info'" size="small" effect="light"
                                  style="cursor:pointer" title="点击查看执行原因">
                            {{ EXEC_META[it.exec_status]?.label || it.exec_status }}
                            <el-icon style="margin-left:2px"><InfoFilled /></el-icon>
                          </el-tag>
                        </template>
                        <div class="exec-detail">
                          <div class="exec-detail-head">
                            <el-tag :type="it.exec_verdict === 'pass' ? 'success' : 'danger'" size="small">
                              {{ it.exec_verdict === 'pass' ? '通过' : '失败' }}
                            </el-tag>
                            <span class="exec-meta">{{ it.exec_runner || '' }} · {{ fmtExecAt(it.exec_run_at) }}</span>
                          </div>
                          <div class="exec-reason">{{ it.exec_reason }}</div>
                        </div>
                      </el-popover>
                      <el-tag v-else :type="EXEC_META[it.exec_status]?.type || 'info'" size="small" effect="light">
                        {{ EXEC_META[it.exec_status]?.label || it.exec_status }}
                      </el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </template>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="功能/需求" min-width="260" fixed show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag v-if="row.is_carried" size="small" type="warning" effect="light" style="margin-right:6px">
              顺延自 {{ (row.assigned_date || '').slice(5) }}
            </el-tag>
            {{ row.title || row.description || '—' }}
          </template>
        </el-table-column>
        <el-table-column prop="assigned_date" label="日期" width="110" />
        <el-table-column prop="module" label="模块" width="110" />
        <el-table-column prop="developer" label="开发负责人" width="100" />
        <el-table-column label="需求文档" width="90">
          <template #default="{ row }">
            <el-link v-if="row.requirement_url" :href="row.requirement_url" target="_blank" type="primary" :underline="false">查看</el-link>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="80"><template #default="{row}">{{ row.priority?.toUpperCase() }}</template></el-table-column>
        <el-table-column label="测试负责人" width="145" show-overflow-tooltip><template #default="{row}">{{ taskOwners(row) }}</template></el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-dropdown :disabled="!canManage" trigger="click" @command="(s) => changeStatus(row, s)">
              <el-tag :type="STATUS_META[row.status]?.type || 'info'" effect="light" size="small" class="status-trigger">
                {{ STATUS_META[row.status]?.label || row.status }}<el-icon class="el-icon--right"><ArrowDown /></el-icon>
              </el-tag>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-for="(m, k) in STATUS_META" :key="k" :command="k" :disabled="k === row.status">
                    {{ m.label }}
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
        <el-table-column label="风险等级" width="135"><template #default="{row}"><el-tag v-if="row.risk_level" :type="RISK_META[row.risk_level]?.type" size="small">{{ RISK_META[row.risk_level]?.label }}</el-tag><span v-else class="cl-hint">未评估</span></template></el-table-column>
        <el-table-column prop="target_version" label="归属版本" width="150" show-overflow-tooltip><template #default="{row}">{{ row.target_version || '—' }}<el-tag v-if="row.release_record_id" size="small" type="success">已归档</el-tag></template></el-table-column>
        <el-table-column prop="progress_note" label="当前卡点/进度" min-width="230" show-overflow-tooltip />
        <el-table-column prop="expected_completion" label="预计完成" width="115" />
        <el-table-column prop="next_action" label="下一步动作" min-width="200" show-overflow-tooltip />
        <el-table-column prop="action_owner" label="动作负责人" width="120" />
        <el-table-column label="最晚反馈时间" width="155"><template #default="{row}">{{ fmtDetailTime(row.feedback_due_at) }}</template></el-table-column>
        <el-table-column label="是否影响发版" width="120"><template #default="{row}"><el-tag v-if="row.affects_release != null" :type="row.affects_release ? 'danger' : 'info'" size="small">{{ row.affects_release ? '是' : '否' }}</el-tag><span v-else class="cl-hint">未评估</span></template></el-table-column>
        <el-table-column prop="conclusion" label="结论/备注" min-width="220" show-overflow-tooltip />
        <el-table-column label="验收进度" width="190">
          <template #default="{ row }">
            <div v-if="row._summary" class="cl-prog">
              <el-progress
                :percentage="row._summary.total ? Math.round(row._summary.passed / row._summary.total * 100) : 0"
                :stroke-width="8" :show-text="false" color="var(--tech-signal)" style="width:70px"
              />
              <span class="cl-nums">{{ row._summary.passed }}/{{ row._summary.total }}</span>
              <el-tag v-if="row._summary.failed" type="danger" size="small" effect="light">失败{{ row._summary.failed }}</el-tag>
            </div>
            <span v-else class="cl-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" :width="canManage ? 180 : 70">
          <template #default="{ row }">
            <el-button link type="info" @click="openDetail(row)">详情</el-button>
            <el-button v-if="canManage" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="canManage" link type="danger" @click="onDel(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </WorkspacePage>

    <el-dialog v-if="dialog.visible" v-model="dialog.visible" :title="dialog.id ? '编辑任务' : '新建任务'" width="min(720px, 96vw)" :close-on-click-modal="false" :show-close="!dialog.saving" :close-on-press-escape="!dialog.saving">
      <el-form :model="form" :disabled="dialog.saving" label-width="116px">
        <el-form-item label="功能/需求" required><el-input v-model="form.title" maxlength="255" /></el-form-item>
        <el-form-item label="需求文档"><el-input v-model="form.requirement_url" placeholder="http://..." /></el-form-item>
        <el-form-item label="开发负责人"><el-input v-model="form.developer" placeholder="开发人员姓名" /></el-form-item>
        <el-form-item label="模块"><el-input v-model="form.module" /></el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority" style="width:100%">
            <el-option label="P0" value="p0" /><el-option label="P1" value="p1" />
            <el-option label="P2" value="p2" /><el-option label="P3" value="p3" />
          </el-select>
        </el-form-item>
        <el-form-item label="测试负责人">
          <el-select v-model="form.assigned_to_ids" :disabled="dialog.ingested && !canAssignIngested" multiple filterable placeholder="选择项目成员（可多选或留空）" style="width:100%">
            <el-option v-for="m in (dialog.ingested ? members.filter(m => m.role !== 'guest') : members)" :key="m.user_id" :label="`${m.name} (${m.username})`" :value="m.user_id" />
          </el-select>
        </el-form-item>
        <p v-if="dialog.ingested" class="cl-hint">此任务由提测机器人自动录入；管理员填写测试负责人并保存后，系统会向派单群发送通知。</p>
        <el-form-item label="分配日期" required>
          <el-date-picker v-model="form.assigned_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
        </el-form-item>
        <el-form-item v-if="dialog.id" label="状态">
          <el-select v-model="form.status" style="width:100%">
            <el-option v-for="(m, k) in STATUS_META" :key="k" :label="m.label" :value="k" />
          </el-select>
        </el-form-item>
        <TaskLedgerFields :form="form" :versions="versions" :linked="dialog.linked" />
        <el-form-item label="任务说明"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="dialog.saving" @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 任务详情抽屉（只读回溯）-->
    <el-drawer v-model="detail.visible" :title="`任务详情 · ${detail.row?.title || ''}`" size="min(640px, 100vw)">
      <div v-if="detail.row" class="detail">
        <p v-if="detail.row.is_ingested" class="d-row"><span class="d-k">任务来源</span> 机器人自动录入</p>
        <p class="d-row"><span class="d-k">任务名称</span> {{ detail.row.title || '—' }}</p>
        <p class="d-row"><span class="d-k">状态</span>
          <el-tag :type="STATUS_META[detail.row.status]?.type || 'info'" effect="light" size="small">
            {{ STATUS_META[detail.row.status]?.label || detail.row.status }}
          </el-tag>
          <el-tag v-if="detail.row.is_carried" size="small" type="warning" effect="light" style="margin-left:6px">
            顺延自 {{ (detail.row.assigned_date || '').slice(5) }}
          </el-tag>
        </p>
        <p class="d-row"><span class="d-k">模块</span> {{ detail.row.module || '—' }}</p>
        <p class="d-row"><span class="d-k">开发</span> {{ detail.row.developer || '—' }}</p>
        <p class="d-row"><span class="d-k">优先级</span> {{ (detail.row.priority || '').toUpperCase() || '—' }}</p>
        <p class="d-row"><span class="d-k">指派给</span> {{ detail.row.assigned_to_name || '待分配' }}</p>
        <p class="d-row"><span class="d-k">分配日期</span> {{ detail.row.assigned_date || '—' }}</p>
        <p class="d-row"><span class="d-k">上线时间</span> {{ fmtDetailTime(detail.row.online_at) }}</p>
        <p v-if="detail.row.status === 'closed'" class="d-row"><span class="d-k">关闭时间</span> {{ fmtDetailTime(detail.row.closed_at) }}</p>
        <p class="d-row"><span class="d-k">需求地址</span>
          <el-link v-if="detail.row.requirement_url" :href="detail.row.requirement_url" target="_blank" type="primary" :underline="false">{{ detail.row.requirement_url }}</el-link>
          <span v-else>—</span>
        </p>
        <p class="d-row"><span class="d-k">归属版本</span>{{ detail.row.target_version || '—' }}<el-tag v-if="detail.row.release_record_id" size="small">发版记录 #{{ detail.row.release_record_id }}</el-tag></p>
        <p class="d-row"><span class="d-k">风险等级</span>{{ RISK_META[detail.row.risk_level]?.label || '未评估' }}</p>
        <p class="d-row"><span class="d-k">预计完成</span>{{ detail.row.expected_completion || '—' }}</p>
        <p class="d-row"><span class="d-k">动作负责人</span>{{ detail.row.action_owner || '—' }}</p>
        <p class="d-row"><span class="d-k">最晚反馈</span>{{ fmtDetailTime(detail.row.feedback_due_at) }}</p>
        <p class="d-row"><span class="d-k">影响发版</span>{{ detail.row.affects_release == null ? '未评估' : detail.row.affects_release ? '是' : '否' }}</p>
        <template v-for="(label,field) in { progress_note: '当前卡点/进度', next_action: '下一步动作', conclusion: '结论/备注' }" :key="field"><p class="d-row"><span class="d-k">{{ label }}</span></p><pre class="d-pre">{{ detail.row[field] || '—' }}</pre></template>
        <p class="d-row"><span class="d-k">任务说明</span></p>
        <pre class="d-pre">{{ detail.row.description || '—' }}</pre>
        <template v-if="detail.row.status === 'closed'">
          <p class="d-row"><span class="d-k">关闭备注</span></p>
          <pre class="d-pre">{{ detail.row.close_note || '（未填写）' }}</pre>
        </template>
      </div>
    </el-drawer>
    <TaskDigestDialog v-if="digestContext" :context="digestContext" @close="digestContext = null" />
    <TaskReleaseDialog v-if="releaseVisible" :rows="releaseRows" :project-id="pid" :platform="projects.find(p => p.id === pid)?.platform_type" @close="releaseVisible = false" @saved="releaseVisible = false; load()" />
  </div>
</template>

<script setup>
import { useRoute } from 'vue-router'
import { ref, reactive, computed, onMounted, watch } from 'vue'
import TaskLedgerFields from '@/components/TaskLedgerFields.vue'
import TaskDigestDialog from '@/components/TaskDigestDialog.vue'
import TaskReleaseDialog from '@/components/TaskReleaseDialog.vue'
import { TASK_STATUS, RISK_META, LEDGER_DEFAULTS, localDate, taskOwners, hasRisk, filterLedger, taskDateQuery } from '@/utils/taskLedger'
import WorkspacePage from '@/components/WorkspacePage.vue'
import '@/styles/workspace-overlays.css'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { useAppStore } from '@/store/app'
import { listMembers, listTasks, createTask, updateTask, deleteTask, copyYesterday, getChecklistSummary, getTaskChecklist, enqueueExec, listMyDevices } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { ArrowDown, InfoFilled } from '@element-plus/icons-vue'

// 任务状态：待测 → 测试中 → 待上线 → 已上线；也可标记阻塞或关闭。
const STATUS_META = TASK_STATUS

// 验收清单项三态结果配色（沿用全局口径：通过青绿/失败红/阻塞warn/待测灰）
const EXEC_META = {
  passed: { label: '通过', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  blocked: { label: '阻塞', type: 'warning' },
  pending: { label: '待测', type: 'info' },
}
// 执行类型 → 标签配色/文案 + 排序权重(清单项按类型聚拢,方便按类勾选)
const KIND_TYPE = { gui: 'success', api: 'primary', cli: 'warning', e2e: 'danger', manual: 'info' }
const KIND_LABEL = { gui: 'GUI', api: 'API', cli: 'CLI', e2e: 'E2E', manual: '人工' }
const KIND_ORDER = { gui: 0, e2e: 1, api: 2, cli: 3, manual: 4 }

// 可下发的执行机 = 当前成员登记的"我的设备"(下发到自己的机器执行)。runner_id 为下发标识。
const myDevices = ref([])

const route = useRoute()
const auth = useAuthStore()
const app = useAppStore()
const projects = ref([])
const pid = ref(null)
// 能否管理任务(编辑/删除/复制/新建/改指派等):平台管理员 或 当前项目的 admin/member。
// 任务分配已放开到成员——admin/member 均可完整管理;访客(guest)只读列表。
const canManage = computed(() => {
  const r = auth.roleIn(pid.value)
  return auth.isPlatformAdmin || r === 'admin' || r === 'member'
})
const canAssignIngested = computed(() => auth.isPlatformAdmin || auth.roleIn(pid.value) === 'admin')
function today() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}
const dateRange = ref([today(), today()])
const singleDate = computed(() => dateRange.value?.length === 2 && dateRange.value[0] === dateRange.value[1] ? dateRange.value[0] : '')
function onDateKeydown(event) {
  // 支持 Delete/Backspace 清空整个日期范围，与清空按钮行为一致。
  if (event.target?.tagName === 'INPUT' && event.target.readOnly &&
      ['Delete', 'Backspace'].includes(event.key) && dateRange.value?.length) {
    event.preventDefault()
    dateRange.value = null
    load()
  }
}
const tasks = ref([]), taskTable = ref(null), selection = ref([]), loadError = ref(false)
const initialFilters = () => ({ keyword: '', status: [], owner: [], priority: [], version: [], risk: '', impact: '', riskOnly: false })
const filters = reactive(initialFilters())
const filteredTasks = computed(() => filterLedger(tasks.value, filters))
const versions = computed(() => [...new Set(tasks.value.map(t => t.target_version).filter(Boolean))].sort())
const selectedTasks = computed(() => filteredTasks.value.filter(t => selection.value.some(s => s.id === t.id)))
const actionTasks = computed(() => selectedTasks.value.length ? selectedTasks.value : filteredTasks.value)
const releasableTasks = computed(() => actionTasks.value.filter(t => t.status === 'online' && t.target_version && !t.release_record_id))
const digestContext = ref(null), releaseVisible = ref(false), releaseRows = ref([])
watch(filters, () => { selection.value = []; taskTable.value?.clearSelection() })
function resetFilters() { Object.assign(filters, initialFilters()) }
function openDigest(history = false) {
  const parts = [singleDate.value ? `单日跟踪 ${singleDate.value}（含历史顺延及当日上线/关闭任务）` : dateRange.value?.length === 2 ? `分配日期 ${dateRange.value[0]} 至 ${dateRange.value[1]}（含首尾）` : '全部日期']
  if (filters.keyword) parts.push(`关键词：${filters.keyword}`)
  if (filters.status.length) parts.push(`状态：${filters.status.map(status => STATUS_META[status].label).join('、')}`)
  if (filters.owner.length) parts.push(`负责人：${filters.owner.map(id => id === '__unset' ? '待分配' : members.value.find(m => m.user_id === id)?.name || `#${id}`).join('、')}`)
  if (filters.priority.length) parts.push(`优先级：${filters.priority.map(p => p.toUpperCase()).join('、')}`)
  if (filters.version.length) parts.push(`版本：${filters.version.map(version => version === '__unset' ? '未填写' : version).join('、')}`)
  if (filters.risk) parts.push(`风险：${RISK_META[filters.risk]?.label || '未评估'}`)
  if (filters.impact) parts.push(`影响发版：${({ yes: '是', no: '否', __unset: '未评估' })[filters.impact]}`)
  if (filters.riskOnly) parts.push('仅风险/阻塞')
  if (selectedTasks.value.length) parts.push(`手动选中 ${selectedTasks.value.length} 项`)
  digestContext.value = { project_id: pid.value, report_date: dateRange.value?.[1] || localDate(), task_ids: actionTasks.value.map(t => t.id), filter_summary: parts.join('；'), history }
}
const members = ref([])
const loading = ref(false)
const dialog = reactive({ visible: false, id: null, saving: false, linked: false, ingested: false })
const form = reactive({ ...LEDGER_DEFAULTS, title: '', requirement_url: '', developer: '', module: '', priority: 'p2', assigned_to_ids: [], assigned_date: '', description: '', status: 'pending' })

onMounted(async () => {
  // 设备与项目列表互不依赖,并行拉取;项目列表走 store 缓存
  const [devicesRes, projectsRes] = await Promise.allSettled([listMyDevices(), app.fetchProjects()])
  myDevices.value = devicesRes.status === 'fulfilled' ? devicesRes.value : []
  projects.value = projectsRes.status === 'fulfilled' ? projectsRes.value : []
  const requestedProject = Number(route.query.project_id)
  pid.value = projects.value.some(p => p.id === requestedProject) ? requestedProject : pickDefaultProjectId(projects.value)
  if (route.query.task_id) dateRange.value = null
  if (pid.value) await load()
  const linkedTask = tasks.value.find(t => t.id === Number(route.query.task_id))
  if (linkedTask) openDetail(linkedTask)
})

let loadVersion = 0
async function load() {
  const seq = ++loadVersion, projectId = pid.value
  const filterDates = taskDateQuery(dateRange.value)
  tasks.value = []; selection.value = []; members.value = []; loadError.value = false
  if (!projectId) return
  setLastProjectId(projectId)
  loading.value = true
  try {
    const [rows, summaryResult, projectMembers] = await Promise.all([
      listTasks({ project_id: projectId, ...filterDates }),
      getChecklistSummary(projectId, filterDates).catch(() => null),
      listMembers(projectId),
    ])
    if (seq !== loadVersion) return
    members.value = projectMembers
    const summary = summaryResult || {}
    tasks.value = rows.map(t => ({ ...t, _summary: summary[String(t.id)] || null,
      _summaryFailed: summaryResult === null, _items: null, _itemsLoading: false,
      _runner: myDevices.value[0]?.runner_id || '', _checked: [], _enqueuing: false }))
  } catch { if (seq === loadVersion) loadError.value = true }
  finally { if (seq === loadVersion) loading.value = false }
}

async function onExpandChange(row, expandedRows) {
  const isExpanded = expandedRows.some((r) => r.id === row.id)
  if (!isExpanded) return
  if (!row._summary && !row._summaryFailed) return  // 确定无清单：不请求；汇总失败则仍尝试拉明细
  if (row._items !== null) return    // 已缓存：不重复请求
  row._itemsLoading = true
  try {
    const items = await getTaskChecklist(row.id)
    // 按执行类型聚拢,方便按类勾选
    row._items = items.sort((a, b) => (KIND_ORDER[a.exec_kind || 'gui'] ?? 9) - (KIND_ORDER[b.exec_kind || 'gui'] ?? 9))
  }
  catch { row._items = [] }
  finally { row._itemsLoading = false }
}

async function sendToRunner(row) {
  const items = row._checked || []
  if (!items.length) return
  if (!row._runner) { ElMessage.warning('请选择执行机'); return }
  // 人工(manual)用例不可自动化执行,不下发(后端也会 400 兜底;这里前置拦截更友好)
  const manualItem = items.find((it) => (it.exec_kind || 'gui') === 'manual')
  if (manualItem) { ElMessage.warning(`含人工(manual)用例「${manualItem.title || ''}」,不能下发到执行机;请取消勾选后重试`); return }
  row._enqueuing = true
  try {
    const res = await enqueueExec(pid.value, row._runner, items.map((it) => it.id))
    const n = res?.run_ids?.length || items.length
    ElMessage.success(`已下发 ${n} 条到 ${row._runner}，执行机跑完会自动回写结果`)
  } catch { /* http 拦截器已提示 */ }
  finally { row._enqueuing = false }
}

function fmtExecAt(s) {
  if (!s) return ''
  return String(s).replace('T', ' ').slice(0, 16)
}

function openCreate() {
  dialog.id = null
  dialog.linked = false
  dialog.ingested = false
  // 新建入口仅 canManage(admin/member)可见,均可自由选指派人,默认不预填。
  Object.assign(form, { ...LEDGER_DEFAULTS, title: '', requirement_url: '', developer: '', module: '', priority: 'p2', assigned_to_ids: [], assigned_date: singleDate.value || localDate(), description: '', status: 'pending' })
  dialog.visible = true
}
function openEdit(row) {
  dialog.id = row.id
  dialog.linked = !!row.release_record_id
  dialog.ingested = !!row.is_ingested
  Object.assign(form, {
    ...Object.fromEntries(Object.entries(LEDGER_DEFAULTS).map(([k,v]) => [k, row[k] ?? v])),
    title: row.title, requirement_url: row.requirement_url || '', developer: row.developer || '',
    module: row.module || '', priority: row.priority, assigned_to_ids: [...(row.assigned_to_ids || [row.assigned_to])],
    assigned_date: row.assigned_date, description: row.description || '', status: row.status,
  })
  dialog.visible = true
}

// ---- 任务详情（只读）----
const detail = reactive({ visible: false, row: null })
function openDetail(row) { detail.row = row; detail.visible = true }
function fmtDetailTime(s) {
  if (!s) return '—'
  return String(s).replace('T', ' ').slice(0, 16)
}
async function submit() {
  if (!form.title || !form.assigned_date) { ElMessage.warning('任务名称和日期必填'); return }
  dialog.saving = true
  try {
    const payload = { ...form, project_id: pid.value, risk_level: form.risk_level || null, expected_completion: form.expected_completion || null, feedback_due_at: form.feedback_due_at || null }
    const result = dialog.id ? await updateTask(dialog.id, payload) : await createTask(payload)
    ElMessage.success(result.assignment_notification_queued ? '保存成功，派单通知已进入发送队列' : '保存成功')
    dialog.visible = false
    await load()
  } catch { /* 请求拦截器提示错误，保留表单供重试。 */ }
  finally { dialog.saving = false }
}
async function changeStatus(row, s) {
  if (!canManage.value || s === row.status) return
  const payload = { status: s }
  // 关闭任务时收集关闭备注（可选，允许留空）
  if (s === 'closed') {
    try {
      const { value } = await ElMessageBox.prompt('请填写关闭备注（可选）', '关闭任务', {
        confirmButtonText: '确认关闭', cancelButtonText: '取消',
        inputType: 'textarea', inputPlaceholder: '例如：需求取消 / 合并到其他任务 / 不再跟进…',
      })
      payload.close_note = (value || '').trim()
    } catch { return }   // 用户取消，不改状态
  }
  const updated = await updateTask(row.id, payload)
  // 用后端返回回写整行（含 online_at/closed_at/close_note），使详情抽屉立即反映
  Object.assign(row, updated)
  ElMessage.success(`状态已更新为「${STATUS_META[s].label}」`)
}
async function onDel(row) {
  await ElMessageBox.confirm(`删除任务「${row.title}」？`, '确认', { type: 'warning' })
  await deleteTask(row.id)
  ElMessage.success('已删除')
  await load()
}
async function onCopy() {
  const targetDate = singleDate.value
  const project = pid.value
  if (!project || !targetDate) { ElMessage.warning('请选择一个日期作为复制目标'); return }
  try { await ElMessageBox.confirm(`把 ${targetDate} 前一天的任务复制到 ${targetDate}？`, '复制昨日', { type: 'info' }) }
  catch { return }
  const r = await copyYesterday(project, targetDate)
  ElMessage.success(`已复制 ${r.copied} 条`)
  await load()
}
</script>

<style scoped>
.ledger-summary { display:flex; flex-wrap:wrap; gap:18px; align-items:center; padding-top:14px; font-size:13px; }
.ledger-summary strong { font-size:16px; margin-left:4px; }
.ledger-actions { display:flex; flex-wrap:wrap; gap:8px; margin-left:auto; }
.ledger-actions .el-button { margin-left:0; }
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
.status-trigger { cursor: pointer; }
.cl-prog { display: flex; align-items: center; gap: 8px; }
.cl-nums { font-family: var(--tech-mono, monospace); font-size: 12px; color: var(--tech-fg, var(--tech-fg)); }
.cl-dim { color: var(--tech-dim, var(--tech-muted)); }
.cl-expand { padding: 8px 16px; }
.cl-empty { color: var(--tech-dim, var(--tech-muted)); font-size: 13px; padding: 4px 0; }
.cl-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }
.cl-hint { color: var(--tech-dim, var(--tech-muted)); font-size: 12px; }
.exec-detail-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.exec-meta { color: var(--tech-dim, var(--tech-muted)); font-size: 12px; }
.exec-reason { white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.5; max-height: 240px; overflow: auto; }
.detail { font-size: 13px; color: var(--tech-fg); }
.detail .d-row { margin: 8px 0 2px; display: flex; align-items: baseline; gap: 4px; flex-wrap: wrap; }
.detail .d-k { display: inline-block; min-width: 72px; color: var(--tech-muted); flex-shrink: 0; }
.detail .d-pre { background: var(--tech-panel-2); border-radius: 6px; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; font-size: 12px; max-height: 220px; overflow: auto; margin: 2px 0 0; }
</style>
