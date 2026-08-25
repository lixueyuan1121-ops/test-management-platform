<template>
  <div class="feedback-cases">
    <el-card>
      <template #header>
        <div class="header">
          <span>反馈用例库</span>
          <div class="filters">
            <el-select v-model="importFilter" placeholder="按导入批次" size="small" clearable style="width:170px" @change="reload">
              <el-option v-for="im in imports" :key="im.id" :label="`#${im.id} ${im.filename || ''}`" :value="im.id" />
            </el-select>
            <el-select v-model="kindFilter" placeholder="执行类型" size="small" clearable style="width:130px" @change="reload">
              <el-option v-for="k in EXEC_KINDS" :key="k" :label="k" :value="k" />
            </el-select>
            <el-select v-model="feasFilter" placeholder="自动化" size="small" clearable style="width:120px" @change="reload">
              <el-option label="yes" value="yes" /><el-option label="partial" value="partial" /><el-option label="no" value="no" />
            </el-select>
            <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
          </div>
        </div>
      </template>

      <el-alert type="success" :closable="false" show-icon class="intro">
        机器人推送的反馈已拆解成结构化用例（按需求/测试点分组）。可自动化用例（非 manual）会自动补 script。
        勾选后可<b>直接执行</b>（当场下发到执行机）或<b>加入回归集</b>（供定时/手动整集回归）。manual 用例不可执行。
      </el-alert>

      <div v-if="selected.length" class="dispatch-bar">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <el-select v-model="runner" size="small" style="width:170px" placeholder="选择执行设备">
          <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
        </el-select>
        <el-button type="success" size="small" :loading="dispatching" @click="runSelected">发送执行</el-button>
        <el-button type="primary" size="small" @click="openAddToSet">加入回归集</el-button>
        <span class="sel-hint">发送执行仅跳过 manual 用例</span>
      </div>

      <el-table :data="rows" v-loading="loading" size="small" border stripe
                empty-text="暂无反馈用例（去「导入记录」上传或等机器人推送）"
                @selection-change="(s) => (selected = s)">
        <el-table-column type="selection" width="42" :selectable="(row) => row.exec_kind !== 'manual'" />
        <el-table-column label="需求/测试点" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="req-line">{{ row.req_title || '—' }}</div>
            <div class="pt-line">{{ row.point_code }} {{ row.point_title || '' }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="case_no" label="编号" width="60" align="center" />
        <el-table-column label="类型" width="70" align="center">
          <template #default="{ row }">
            <el-tag :type="KIND_TYPE[row.exec_kind] || 'info'" size="small" effect="plain">{{ row.exec_kind }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="优先级" width="70" align="center">
          <template #default="{ row }"><el-tag :type="PRI_TYPE[(row.priority || '').toUpperCase()] || 'info'" size="small">{{ row.priority || '—' }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="title" label="用例标题" min-width="200" show-overflow-tooltip />
        <el-table-column label="自动化" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="FEAS_TYPE[row.auto_feasible] || 'info'" size="small">{{ row.auto_feasible }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="script" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.has_script" type="success" size="small" effect="plain">已补</el-tag>
            <el-tooltip v-else-if="row.script_error" :content="row.script_error" placement="top"><el-tag type="danger" size="small" effect="plain">失败</el-tag></el-tooltip>
            <span v-else class="none">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="72" align="center">
          <template #default="{ row }"><el-tag :type="row.status === 'ready' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button v-if="row.exec_kind !== 'manual'" link type="warning" size="small" :loading="row._regen" @click="regen(row)">重补script</el-button>
            <el-button link type="danger" size="small" @click="del(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 详情/编辑抽屉 -->
    <el-drawer v-model="detailDrawer" :title="`用例详情 #${cur?.id || ''}`" size="42%">
      <template v-if="cur">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="需求">{{ cur.req_title || '—' }}</el-descriptions-item>
          <el-descriptions-item label="需求链接">
            <a v-if="safeReqUrl" :href="safeReqUrl" target="_blank" rel="noopener noreferrer" class="link">{{ cur.req_url }}</a><span v-else>{{ cur.req_url || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="反馈概述"><div class="pre">{{ cur.feedback_summary || '—' }}</div></el-descriptions-item>
          <el-descriptions-item label="测试点">{{ cur.point_code }} {{ cur.point_title }}</el-descriptions-item>
          <el-descriptions-item label="自动化">{{ cur.auto_feasible }} · {{ cur.auto_reason || '—' }}</el-descriptions-item>
        </el-descriptions>

        <el-form label-width="72px" class="edit-form">
          <el-form-item label="标题"><el-input v-model="cur.title" size="small" /></el-form-item>
          <el-form-item label="执行类型">
            <el-select v-model="cur.exec_kind" size="small" style="width:140px">
              <el-option v-for="k in EXEC_KINDS" :key="k" :label="k" :value="k" />
            </el-select>
          </el-form-item>
          <el-form-item label="前置条件"><el-input v-model="cur.precondition" type="textarea" :rows="2" size="small" /></el-form-item>
          <el-form-item label="步骤"><el-input v-model="cur.steps" type="textarea" :rows="4" size="small" /></el-form-item>
          <el-form-item label="预期"><el-input v-model="cur.expected" type="textarea" :rows="3" size="small" /></el-form-item>
        </el-form>

        <div v-if="cur.script" class="script-box">
          <div class="script-title">已补 script（{{ cur.script.length }} 步）</div>
          <pre class="script-pre">{{ JSON.stringify(cur.script, null, 2) }}</pre>
        </div>

        <div class="drawer-foot">
          <el-button @click="detailDrawer = false">关闭</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        </div>
      </template>
    </el-drawer>

    <!-- 加入回归集 -->
    <el-dialog v-model="addSetDlg" title="加入回归集" width="420px">
      <el-form label-width="80px">
        <el-form-item label="选择集">
          <el-select v-model="targetSet" placeholder="选择回归集" style="width:100%">
            <el-option v-for="s in sets" :key="s.id" :label="`${s.name}（${s.case_count} 条）`" :value="s.id" />
          </el-select>
        </el-form-item>
        <div class="dlg-hint">将把选中的 {{ selected.length }} 条用例加入该集（已在集内的自动跳过）。</div>
      </el-form>
      <template #footer>
        <el-button @click="addSetDlg = false">取消</el-button>
        <el-button type="primary" :loading="adding" :disabled="!targetSet" @click="doAddToSet">加入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  feedbackCases, feedbackCase, updateFeedbackCase, regenFeedbackScript, deleteFeedbackCase,
  runFeedbackCases, feedbackImports, feedbackSets, addFeedbackSetCases, listMyDevices,
} from '@/api'

const EXEC_KINDS = ['gui', 'api', 'cli', 'e2e', 'manual']
const KIND_TYPE = { gui: 'success', api: 'warning', cli: 'info', e2e: 'primary', manual: 'info' }
const PRI_TYPE = { P0: 'danger', P1: 'warning', P2: 'info', P3: 'info' }
const FEAS_TYPE = { yes: 'success', partial: 'warning', no: 'info' }

const rows = ref([])
const imports = ref([])
const sets = ref([])
const myDevices = ref([])
const loading = ref(false)
const selected = ref([])
const importFilter = ref(null)
const kindFilter = ref(null)
const feasFilter = ref(null)
const runner = ref('mac-01')
const dispatching = ref(false)

const detailDrawer = ref(false)
const cur = ref(null)
// 需求链接只允许 http/https 才渲染成可点链接，杜绝 javascript: 等 URI 造成的 XSS（req_url 源自机器人 md，半可信）
const safeReqUrl = computed(() => {
  const u = cur.value?.req_url || ''
  return /^https?:\/\//i.test(u) ? u : null
})
const saving = ref(false)

const addSetDlg = ref(false)
const targetSet = ref(null)
const adding = ref(false)

async function reload() {
  loading.value = true
  try {
    const params = {}
    if (importFilter.value) params.import_id = importFilter.value
    if (kindFilter.value) params.exec_kind = kindFilter.value // 后端未按 kind 过滤，前端兜底
    if (feasFilter.value) params.auto_feasible = feasFilter.value
    let data = await feedbackCases(params)
    if (kindFilter.value) data = data.filter((c) => c.exec_kind === kindFilter.value)
    rows.value = data
  } catch { /* 拦截器已提示 */ } finally {
    loading.value = false
  }
}

async function loadAux() {
  try { imports.value = await feedbackImports() } catch { /* ignore */ }
  try { sets.value = await feedbackSets() } catch { /* ignore */ }
  try { myDevices.value = await listMyDevices() } catch { /* ignore */ }
}

async function runSelected() {
  const ids = selected.value.map((r) => r.id)
  if (!ids.length) return
  dispatching.value = true
  try {
    const res = await runFeedbackCases(ids, runner.value)
    ElMessage.success(`已下发 ${res.run_ids.length} 条（批次 ${res.batch_id}），去「回归结果」查看`)
  } catch { /* 拦截器已提示 */ } finally {
    dispatching.value = false
  }
}

function openAddToSet() {
  if (!sets.value.length) {
    ElMessage.warning('还没有回归集，请先去「回归用例集」创建')
    return
  }
  targetSet.value = null
  addSetDlg.value = true
}
async function doAddToSet() {
  adding.value = true
  try {
    const res = await addFeedbackSetCases(targetSet.value, selected.value.map((r) => r.id))
    ElMessage.success(`已加入 ${res.added} 条`)
    addSetDlg.value = false
    loadAux()
  } catch { /* ignore */ } finally {
    adding.value = false
  }
}

async function openDetail(row) {
  detailDrawer.value = true
  cur.value = null
  try { cur.value = await feedbackCase(row.id) } catch { detailDrawer.value = false }
}
async function save() {
  saving.value = true
  try {
    await updateFeedbackCase(cur.value.id, {
      title: cur.value.title, precondition: cur.value.precondition, steps: cur.value.steps,
      expected: cur.value.expected, exec_kind: cur.value.exec_kind,
    })
    ElMessage.success('已保存')
    detailDrawer.value = false
    reload()
  } catch { /* ignore */ } finally {
    saving.value = false
  }
}

async function regen(row) {
  row._regen = true
  try {
    await regenFeedbackScript(row.id)
    ElMessage.success('script 已重补')
    reload()
  } catch { /* 拦截器已提示 */ } finally {
    row._regen = false
  }
}

async function del(row) {
  try {
    await ElMessageBox.confirm(`确认删除用例「${row.title}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteFeedbackCase(row.id)
    ElMessage.success('已删除')
    reload()
  } catch { /* ignore */ }
}

onMounted(() => { reload(); loadAux() })
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; }
.intro { margin-bottom: 12px; }
.dispatch-bar { display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: #f0f9eb; border-radius: 6px; margin-bottom: 10px; }
.sel-info { font-weight: 600; color: #67c23a; }
.sel-hint { font-size: 12px; color: #909399; }
.req-line { font-size: 13px; color: #303133; }
.pt-line { font-size: 11px; color: #909399; }
.none { color: #c0c4cc; }
.link { color: #409eff; word-break: break-all; }
.pre { white-space: pre-wrap; font-size: 12px; max-height: 160px; overflow: auto; }
.edit-form { margin-top: 14px; }
.script-box { margin-top: 8px; }
.script-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.script-pre { background: #1f2d3d; color: #a6e3c0; padding: 10px; border-radius: 6px; font-size: 11px; max-height: 240px; overflow: auto; }
.drawer-foot { margin-top: 16px; text-align: right; }
.dlg-hint { font-size: 12px; color: #909399; }
</style>
