<template>
  <div class="issues functional-workspace">
    <WorkspacePage title="遗留问题">
      <template #actions>
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="load">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
      </template>
      <template #filters>
            <el-select v-model="statusFilter" placeholder="状态" size="small" style="width:120px" @change="load">
              <el-option label="未解决" value="open" />
              <el-option label="已解决" value="resolved" />
              <el-option label="全部" value="" />
            </el-select>
      </template>
      <el-result v-if="loadError" icon="error" title="遗留问题加载失败"><template #extra><el-button @click="load">重试问题</el-button></template></el-result>
      <el-table v-else :data="issues" v-loading="loading" size="small" empty-text="暂无遗留问题">
        <el-table-column prop="title" label="问题" min-width="160" />
        <el-table-column prop="task_title" label="关联任务" min-width="140" />
        <el-table-column label="严重度" width="100">
          <template #default="{ row }">
            <el-tag :type="sevType(row.severity)" size="small">{{ row.severity }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="owner_name" label="负责人" width="100" />
        <el-table-column label="外部缺陷" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.external_ref && row.external_ref.startsWith('geelib#')" type="success" size="small">{{ row.external_ref }}</el-tag>
            <el-link v-else-if="safeRef(row.external_ref)" :href="row.external_ref" target="_blank" rel="noopener noreferrer" type="primary" :underline="false">{{ row.external_ref }}</el-link>
            <span v-else-if="row.external_ref">{{ row.external_ref }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'open' ? 'warning' : 'success'" size="small">{{ row.status === 'open' ? '未解决' : '已解决' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="160">
          <template #default="{ row }">{{ row.created_at?.slice(0,16).replace('T',' ') }}</template>
        </el-table-column>
        <el-table-column v-if="canManage" label="操作" width="230">
          <template #default="{ row }">
            <el-button v-if="row.status==='open'" link type="success" :disabled="updating" @click="resolve(row)">标记解决</el-button>
            <el-button v-else link type="warning" :disabled="updating" @click="reopen(row)">重开</el-button>
            <el-button link type="primary" @click="openEdit(row)">关联缺陷</el-button>
            <el-button v-if="!row.external_ref" link type="danger" :loading="reporting===row.id" @click="reportGeelib(row)">上报极库云</el-button>
          </template>
        </el-table-column>
      </el-table>
    </WorkspacePage>

    <el-dialog v-if="dialog.visible" v-model="dialog.visible" :show-close="!dialog.saving" :close-on-click-modal="!dialog.saving" :close-on-press-escape="!dialog.saving" title="关联外部缺陷" width="440px">
      <el-form label-width="90px" :disabled="dialog.saving">
        <el-form-item label="问题">{{ dialog.title }}</el-form-item>
        <el-form-item label="缺陷链接"><el-input v-model="dialog.external_ref" placeholder="http://jira/BUG-123" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="dialog.saving" @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="saveRef">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import '@/styles/workspace-overlays.css'
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { useAppStore } from '@/store/app'
import { listIssues, updateIssue, reportIssueToGeelib } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const auth = useAuthStore()
const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const statusFilter = ref('open')
const issues = ref([])
const loading = ref(false)
const loadError = ref(false), updating = ref(false)
let version = 0, disposed = false
const safeRef = value => /^https?:\/\//i.test(value || '')
onBeforeUnmount(() => { disposed = true; ++version })
const reporting = ref(null)
const canManage = computed(() => auth.roleIn(pid.value) === 'admin')
const dialog = reactive({ visible: false, id: null, title: '', external_ref: '', saving: false })

onMounted(async () => {
  try {
    projects.value = await app.fetchProjects()
    if (!disposed && projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await load() }
  } catch { if (!disposed) loadError.value = true }
})

async function load() {
  const current = ++version
  issues.value = []; loadError.value = false; loading.value = false
  if (!pid.value) return
  setLastProjectId(pid.value)
  loading.value = true
  try { const data = await listIssues(pid.value, statusFilter.value || undefined); if (!disposed && current === version) issues.value = data }
  catch { if (!disposed && current === version) loadError.value = true }
  finally { if (!disposed && current === version) loading.value = false }
}

function sevType(s) { return { blocker: 'danger', major: 'warning', minor: 'info' }[s] || '' }

async function resolve(row) {
  if (updating.value) return
  updating.value = true
  try {
  const res = await updateIssue(row.id, { status: 'resolved' })
  // 已上报极库云的问题，后端会联动把缺陷流转「已验证」，结果在 geelib_sync 里
  const sync = res?.geelib_sync
  if (sync) {
    if (sync.ok) ElMessage.success(`已标记解决，${sync.msg}`)
    else ElMessage.warning(`已标记解决，但${sync.msg}`)
  } else {
    ElMessage.success('已标记解决')
  }
  await load()
  } catch { /* 请求拦截器已提示。 */ } finally { updating.value = false }
}
async function reopen(row) {
  if (updating.value) return
  updating.value = true
  try { await updateIssue(row.id, { status: 'open' }); ElMessage.success('已重开'); await load() }
  catch { /* 请求拦截器已提示。 */ } finally { updating.value = false }
}

async function reportGeelib(row) {
  if (reporting.value !== null) return
  reporting.value = row.id
  try {
    await ElMessageBox.confirm(`确认把「${row.title}」作为缺陷上报到极库云？上报后会回填工作项编号。`, '上报极库云', { type: 'warning', confirmButtonText: '确认上报', cancelButtonText: '取消' })
  } catch { reporting.value = null; return }
  try {
    const res = await reportIssueToGeelib(row.id)
    ElMessage.success(res?.already_reported ? '该问题已上报过' : `已上报极库云：${res?.external_ref || '成功'}`)
    await load()
  } catch { /* 拦截器已弹友好提示(如「通道未启用」);此处吞掉,避免 reject 冒泡到全局兜底再弹一条「页面异常」 */
  } finally { reporting.value = null }
}

function openEdit(row) {
  dialog.id = row.id; dialog.title = row.title; dialog.external_ref = row.external_ref || ''
  dialog.visible = true
}
async function saveRef() {
  if (dialog.saving) return
  dialog.saving = true
  try { await updateIssue(dialog.id, { external_ref: dialog.external_ref }); ElMessage.success('已保存'); dialog.visible = false; await load() }
  catch { /* 保留输入，拦截器已提示。 */ } finally { dialog.saving = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
</style>
