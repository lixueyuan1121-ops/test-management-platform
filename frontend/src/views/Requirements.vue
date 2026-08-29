<template>
  <div class="requirements">
    <el-card>
      <template #header>
        <div class="header">
          <span>需求覆盖</span>
          <div class="actions">
            <el-select v-model="projectId" size="small" style="width:200px" placeholder="选择项目" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="filterRelease" size="small" style="width:170px" clearable placeholder="按发版筛选" @change="reload">
              <el-option v-for="r in releases" :key="r.id" :label="r.version" :value="r.id" />
            </el-select>
            <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
            <el-button type="primary" size="small" :disabled="!projectId" @click="openCreate">新建需求</el-button>
          </div>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon class="intro">
        需求↔用例↔发版的<b>追溯链</b>：AI 测试助手用<b>需求链接</b>抓文生成时会自动建需求并挂上该批用例；
        也可手动新建/挂用例。覆盖状态：<b>未覆盖</b>(没挂用例)→<b>未执行</b>→<b>有失败</b>/<b>部分通过</b>→<b>全部通过</b>。
        需求挂到发版后，发版质量卡长出「需求覆盖」统计。
      </el-alert>

      <div v-if="rows.length" class="cov-summary">
        <span v-for="s in STATES" :key="s.key" class="cov-pill" :class="s.key">
          {{ s.label }} {{ stateCount[s.key] || 0 }}
        </span>
      </div>

      <el-table :data="rows" v-loading="loading" size="small" border stripe empty-text="暂无需求（AI 生成时带需求链接会自动创建）">
        <el-table-column label="覆盖" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="STATE_TYPE[row.state] || 'info'" size="small" :effect="row.state === 'failing' ? 'dark' : 'plain'">
              {{ STATE_LABEL[row.state] || row.state }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="需求" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">
            <a v-if="safeUrl(row.url)" :href="safeUrl(row.url)" target="_blank" rel="noopener" class="req-link">{{ row.title }}</a>
            <span v-else>{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column label="用例" width="140" align="center">
          <template #default="{ row }">
            <span v-if="row.case_count">{{ row.passed }}/{{ row.executed }}/{{ row.case_count }}
              <span class="hint">过/执/总</span></span>
            <span v-else class="none">未挂用例</span>
          </template>
        </el-table-column>
        <el-table-column label="发版" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.release_version" size="small" effect="plain">{{ row.release_version }}</el-tag>
            <span v-else class="none">—</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="140">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openCases(row)">用例</el-button>
            <el-button link size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="del(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 建/编辑需求 -->
    <el-dialog v-model="editDlg" :title="editing ? '编辑需求' : '新建需求'" width="480px">
      <el-form label-width="80px">
        <el-form-item label="标题"><el-input v-model="form.title" size="small" placeholder="需求名称" /></el-form-item>
        <el-form-item label="文档链接"><el-input v-model="form.url" size="small" placeholder="https://…（可选；同链接自动去重）" /></el-form-item>
        <el-form-item label="所属发版">
          <el-select v-model="form.release_id" size="small" style="width:100%" clearable placeholder="不关联">
            <el-option v-for="r in releases" :key="r.id" :label="`${r.version}（${r.release_date}）`" :value="r.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!form.title" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 需求下用例（含最新执行结论 + 挂/摘） -->
    <el-drawer v-model="casesDrawer" :title="`需求「${curReq?.title || ''}」的用例`" size="52%">
      <div class="drawer-actions">
        <el-button size="small" type="primary" @click="openLink">挂用例</el-button>
        <el-button size="small" type="danger" :disabled="!casesSelected.length" @click="unlinkSel">摘除选中（{{ casesSelected.length }}）</el-button>
        <el-button size="small" @click="reloadCases">刷新</el-button>
      </div>
      <el-table :data="reqCases" v-loading="casesLoading" size="small" border stripe empty-text="未挂用例"
                @selection-change="(s) => (casesSelected = s)">
        <el-table-column type="selection" width="42" />
        <el-table-column prop="id" label="ID" width="70" align="center" />
        <el-table-column prop="title" label="用例" min-width="220" show-overflow-tooltip />
        <el-table-column label="类型" width="70" align="center">
          <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.exec_kind }}</el-tag></template>
        </el-table-column>
        <el-table-column label="最新执行" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.last_exec" :type="EXEC_TYPE[row.last_exec] || 'info'" size="small">{{ EXEC_LABEL[row.last_exec] || row.last_exec }}</el-tag>
            <span v-else class="none">未执行</span>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <!-- 挂用例挑选（复用已采纳用例列表） -->
    <el-dialog v-model="linkDlg" title="挂用例到需求" width="680px" top="8vh">
      <el-input v-model="linkKeyword" size="small" placeholder="按标题搜索(已采纳用例)" clearable style="width:240px;margin-bottom:10px" @keyup.enter="reloadLinkCand" />
      <el-button size="small" style="margin-left:8px" @click="reloadLinkCand">查询</el-button>
      <el-table :data="linkCand" v-loading="linkLoading" size="small" border stripe height="360"
                empty-text="无候选" @selection-change="(s) => (linkSelected = s)">
        <el-table-column type="selection" width="42" />
        <el-table-column prop="id" label="ID" width="70" align="center" />
        <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip />
        <el-table-column prop="category" label="分类" width="80" align="center" />
      </el-table>
      <template #footer>
        <el-button @click="linkDlg = false">取消</el-button>
        <el-button type="primary" :loading="linking" :disabled="!linkSelected.length" @click="confirmLink">挂上选中（{{ linkSelected.length }}）</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createRequirement, deleteRequirement, linkRequirementCases, listCases,
  listReleases, listRequirements, requirementCases, unlinkRequirementCases, updateRequirement,
} from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const STATES = [
  { key: 'passed', label: '全部通过' },
  { key: 'partial', label: '部分通过' },
  { key: 'failing', label: '有失败' },
  { key: 'notrun', label: '未执行' },
  { key: 'uncovered', label: '未覆盖' },
]
const STATE_LABEL = { uncovered: '未覆盖', notrun: '未执行', failing: '有失败', partial: '部分通过', passed: '全部通过' }
const STATE_TYPE = { uncovered: 'info', notrun: 'warning', failing: 'danger', partial: 'warning', passed: 'success' }
const EXEC_LABEL = { passed: '通过', failed: '失败', blocked: '阻塞', pending: '排队', running: '执行中' }
const EXEC_TYPE = { passed: 'success', failed: 'danger', blocked: 'warning', pending: 'info', running: 'info' }

const app = useAppStore()
const projects = ref([])
const projectId = ref(null)
// 文档链接只放行 http(s):挡 javascript:/data: 等可执行 scheme(存储型 XSS);同 EvalTasks.vue 口径
const safeUrl = (u) => /^https?:\/\//i.test(u || '') ? u : null
const releases = ref([])
const filterRelease = ref(null)
const rows = ref([])
const loading = ref(false)

const editDlg = ref(false)
const editing = ref(false)
const form = reactive({ id: null, title: '', url: '', release_id: null })
const saving = ref(false)

const casesDrawer = ref(false)
const curReq = ref(null)
const reqCases = ref([])
const casesLoading = ref(false)
const casesSelected = ref([])

const linkDlg = ref(false)
const linkCand = ref([])
const linkLoading = ref(false)
const linkSelected = ref([])
const linkKeyword = ref('')
const linking = ref(false)

const stateCount = computed(() => {
  const m = {}
  for (const r of rows.value) m[r.state] = (m[r.state] || 0) + 1
  return m
})

function fmt(s) { return s ? s.replace('T', ' ').slice(0, 16) : '—' }

async function onProjectChange() {
  if (!projectId.value) return
  setLastProjectId(projectId.value)
  filterRelease.value = null
  try { releases.value = await listReleases({ project_id: projectId.value }) } catch { releases.value = [] }
  await reload()
}

async function reload() {
  if (!projectId.value) return
  loading.value = true
  try {
    rows.value = await listRequirements({
      project_id: projectId.value,
      release_id: filterRelease.value || undefined,
    })
  } catch { /* 拦截器已提示 */ } finally { loading.value = false }
}

function openCreate() {
  editing.value = false
  Object.assign(form, { id: null, title: '', url: '', release_id: filterRelease.value || null })
  editDlg.value = true
}
function openEdit(row) {
  editing.value = true
  Object.assign(form, { id: row.id, title: row.title, url: row.url || '', release_id: row.release_id })
  editDlg.value = true
}
async function save() {
  saving.value = true
  try {
    if (editing.value) {
      await updateRequirement(form.id, {
        title: form.title, url: form.url || null,
        release_id: form.release_id == null ? 0 : form.release_id,   // null→0=摘除
      })
    } else {
      await createRequirement({
        project_id: projectId.value, title: form.title,
        url: form.url || null, release_id: form.release_id,
      })
    }
    ElMessage.success('已保存')
    editDlg.value = false
    reload()
  } catch { /* 拦截器已提示 */ } finally { saving.value = false }
}

async function del(row) {
  try {
    await ElMessageBox.confirm(`确认删除需求「${row.title}」？（用例保留，仅摘除关联）`, '删除确认', { type: 'warning' })
  } catch { return }
  try { await deleteRequirement(row.id); ElMessage.success('已删除'); reload() } catch { /* ignore */ }
}

async function openCases(row) {
  curReq.value = row
  casesDrawer.value = true
  reloadCases()
}
async function reloadCases() {
  casesLoading.value = true
  try { reqCases.value = await requirementCases(curReq.value.id) }
  catch { /* ignore */ } finally { casesLoading.value = false }
}
async function unlinkSel() {
  try {
    await unlinkRequirementCases(curReq.value.id, casesSelected.value.map((r) => r.id))
    ElMessage.success('已摘除')
    reloadCases()
    reload()
  } catch { /* ignore */ }
}

function openLink() {
  linkKeyword.value = ''
  linkDlg.value = true
  reloadLinkCand()
}
async function reloadLinkCand() {
  linkLoading.value = true
  try {
    const res = await listCases({
      project_id: projectId.value, review_status: 'adopted',
      keyword: linkKeyword.value || undefined, limit: 200,
    })
    const inReq = new Set(reqCases.value.map((c) => c.id))
    linkCand.value = (res.items || []).filter((c) => !inReq.has(c.id))
  } catch { /* ignore */ } finally { linkLoading.value = false }
}
async function confirmLink() {
  linking.value = true
  try {
    const res = await linkRequirementCases(curReq.value.id, linkSelected.value.map((r) => r.id))
    ElMessage.success(`已挂上 ${res.linked} 条`)
    linkDlg.value = false
    reloadCases()
    reload()
  } catch { /* ignore */ } finally { linking.value = false }
}

onMounted(async () => {
  try {
    projects.value = await app.fetchProjects()
    if (projects.value.length) {
      projectId.value = pickDefaultProjectId(projects.value)
      await onProjectChange()
    }
  } catch { /* ignore */ }
})
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.actions { display: flex; gap: 8px; align-items: center; }
.intro { margin-bottom: 12px; }
.cov-summary { display: flex; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.cov-pill { font-size: 12px; padding: 3px 10px; border-radius: 12px; background: #f4f4f5; color: #666; }
.cov-pill.passed { background: rgba(0,179,134,.1); color: #00926e; }
.cov-pill.failing { background: rgba(229,86,95,.1); color: #c45656; }
.cov-pill.partial, .cov-pill.notrun { background: rgba(230,162,60,.12); color: #b88230; }
.req-link { color: #2b7de9; text-decoration: none; }
.req-link:hover { text-decoration: underline; }
.hint { font-size: 11px; color: #909399; margin-left: 4px; }
.none { color: #c0c4cc; }
.drawer-actions { display: flex; gap: 10px; margin-bottom: 10px; }
</style>
