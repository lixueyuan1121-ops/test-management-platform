<template>
  <div class="tasks">
    <el-card>
      <template #header>
        <div class="header">
          <span>任务分配</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:180px" @change="load">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-date-picker v-model="date" type="date" value-format="YYYY-MM-DD" size="small" style="width:150px" @change="load" />
            <el-button size="small" @click="onCopy" :disabled="!pid">复制昨日</el-button>
            <el-button type="primary" size="small" @click="openCreate" :disabled="!pid">新建任务</el-button>
          </div>
        </div>
      </template>
      <el-table :data="tasks" v-loading="loading" size="small" empty-text="该日无任务">
        <el-table-column prop="title" label="任务名称" min-width="160" />
        <el-table-column prop="module" label="模块" width="110" />
        <el-table-column prop="developer" label="开发" width="100" />
        <el-table-column label="需求地址" width="120">
          <template #default="{ row }">
            <el-link v-if="row.requirement_url" :href="row.requirement_url" target="_blank" type="primary" :underline="false">查看</el-link>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="80" />
        <el-table-column prop="assigned_to_name" label="指派给" width="100" />
        <el-table-column prop="status" label="状态" width="90" />
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="onDel(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑任务' : '新建任务'" width="560px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="任务名称" required><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="需求地址"><el-input v-model="form.requirement_url" placeholder="http://..." /></el-form-item>
        <el-form-item label="开发"><el-input v-model="form.developer" placeholder="开发人员姓名" /></el-form-item>
        <el-form-item label="模块"><el-input v-model="form.module" /></el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority" style="width:100%">
            <el-option label="P0" value="p0" /><el-option label="P1" value="p1" />
            <el-option label="P2" value="p2" /><el-option label="P3" value="p3" />
          </el-select>
        </el-form-item>
        <el-form-item label="指派给" required>
          <el-select v-model="form.assigned_to" filterable placeholder="选择项目成员" style="width:100%">
            <el-option v-for="m in members" :key="m.user_id" :label="`${m.name} (${m.username})`" :value="m.user_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="分配日期" required>
          <el-date-picker v-model="form.assigned_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
        </el-form-item>
        <el-form-item label="说明"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { listProjects, listMembers, listTasks, createTask, updateTask, deleteTask, copyYesterday } from '@/api'

const auth = useAuthStore()
const projects = ref([])
const pid = ref(null)
const date = ref(new Date().toISOString().slice(0, 10))
const tasks = ref([])
const members = ref([])
const loading = ref(false)
const dialog = reactive({ visible: false, id: null, saving: false })
const form = reactive({ title: '', requirement_url: '', developer: '', module: '', priority: 'p2', assigned_to: null, assigned_date: '', description: '' })

onMounted(async () => {
  projects.value = await listProjects()
  if (projects.value.length) { pid.value = projects.value[0].id; await load() }
})

async function loadMembers() {
  if (!pid.value) { members.value = []; return }
  members.value = await listMembers(pid.value)
}
watch(pid, loadMembers)

async function load() {
  if (!pid.value) return
  await loadMembers()
  loading.value = true
  try { tasks.value = await listTasks({ project_id: pid.value, date: date.value }) }
  finally { loading.value = false }
}

function openCreate() {
  dialog.id = null
  Object.assign(form, { title: '', requirement_url: '', developer: '', module: '', priority: 'p2', assigned_to: null, assigned_date: date.value, description: '' })
  dialog.visible = true
}
function openEdit(row) {
  dialog.id = row.id
  Object.assign(form, {
    title: row.title, requirement_url: row.requirement_url || '', developer: row.developer || '',
    module: row.module || '', priority: row.priority, assigned_to: row.assigned_to,
    assigned_date: row.assigned_date, description: row.description || '',
  })
  dialog.visible = true
}
async function submit() {
  if (!form.title || !form.assigned_to || !form.assigned_date) { ElMessage.warning('任务名称/指派/日期必填'); return }
  dialog.saving = true
  try {
    const payload = { ...form, project_id: pid.value }
    if (dialog.id) await updateTask(dialog.id, payload)
    else await createTask(payload)
    ElMessage.success('保存成功')
    dialog.visible = false
    await load()
  } finally { dialog.saving = false }
}
async function onDel(row) {
  await ElMessageBox.confirm(`删除任务「${row.title}」？`, '确认', { type: 'warning' })
  await deleteTask(row.id)
  ElMessage.success('已删除')
  await load()
}
async function onCopy() {
  await ElMessageBox.confirm(`把 ${date.value} 前一天的任务复制到 ${date.value}？`, '复制昨日', { type: 'info' })
  const r = await copyYesterday(pid.value, date.value)
  ElMessage.success(`已复制 ${r.copied} 条`)
  await load()
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
</style>
