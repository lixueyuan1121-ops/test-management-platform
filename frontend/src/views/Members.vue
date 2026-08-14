<template>
  <div class="members">
    <el-card>
      <template #header>
        <div class="header">
          <span>成员管理 <small v-if="project">/ {{ project.name }}</small>
            <small class="hint">同一用户在不同项目可设不同角色，按项目独立勾选</small>
          </span>
          <el-button v-if="canManage" type="primary" size="small" @click="openAdd">添加成员</el-button>
        </div>
      </template>
      <el-table :data="members" v-loading="loading" size="small">
        <el-table-column prop="username" label="用户名" width="160" />
        <el-table-column prop="name" label="姓名" width="160" />
        <el-table-column label="角色" width="160">
          <template #default="{ row }">
            <el-tag :type="tagType(row.role)">{{ roleText(row.role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="加入时间" />
        <el-table-column v-if="canManage" label="操作" width="200">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">改角色</el-button>
            <el-button link type="danger" @click="onRemove(row)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-if="dialog.visible" v-model="dialog.visible" :title="dialog.mode === 'add' ? '添加成员' : '修改角色'" width="440px">
      <el-form :model="form" label-width="80px">
        <el-form-item v-if="dialog.mode === 'add'" label="用户">
          <el-select
            v-model="form.user_id" filterable remote :remote-method="searchUsers"
            :loading="userLoading" placeholder="搜索用户名/姓名" style="width:100%"
          >
            <el-option v-for="u in userOptions" :key="u.id" :label="`${u.name} (${u.username})`" :value="u.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width:100%">
            <el-option label="管理员" value="admin" />
            <el-option label="成员" value="member" />
            <el-option label="嘉宾（只读）" value="guest" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { useAppStore } from '@/store/app'
import { listMembers, addMember, updateMember, removeMember, listUsers } from '@/api'

const route = useRoute()
const auth = useAuthStore()
const app = useAppStore()
const pid = computed(() => Number(route.params.id))

const project = ref(null)
const members = ref([])
const loading = ref(false)

const canManage = computed(() => auth.roleIn(pid.value) === 'admin')

const dialog = reactive({ visible: false, mode: 'add', saving: false })
const form = reactive({ user_id: null, role: 'member' })
const userOptions = ref([])
const userLoading = ref(false)

async function load() {
  loading.value = true
  try {
    members.value = await listMembers(pid.value)
    if (!project.value) {
      const all = await app.fetchProjects()
      project.value = all.find((p) => p.id === pid.value) || null
    }
  } finally { loading.value = false }
}
onMounted(load)

function roleText(r) { return { admin: '管理员', member: '成员', guest: '嘉宾' }[r] || r }
function tagType(r) { return { admin: 'danger', member: 'success', guest: 'info' }[r] || '' }

function openAdd() {
  dialog.mode = 'add'
  Object.assign(form, { user_id: null, role: 'member' })
  userOptions.value = []
  dialog.visible = true
}
function openEdit(row) {
  dialog.mode = 'edit'
  Object.assign(form, { user_id: row.user_id, role: row.role })
  userOptions.value = [{ id: row.user_id, name: row.name, username: row.username }]
  dialog.visible = true
}
async function searchUsers(kw) {
  if (!kw) return
  userLoading.value = true
  try { userOptions.value = await listUsers(kw) } finally { userLoading.value = false }
}
async function submit() {
  if (dialog.mode === 'add' && !form.user_id) { ElMessage.warning('请选择用户'); return }
  dialog.saving = true
  try {
    if (dialog.mode === 'add') {
      await addMember(pid.value, { user_id: form.user_id, role: form.role })
    } else {
      await updateMember(pid.value, form.user_id, { role: form.role })
    }
    ElMessage.success('保存成功')
        await load()
  } finally { dialog.saving = false; dialog.visible = false }
}
async function onRemove(row) {
  await ElMessageBox.confirm(`确定将 ${row.name} 移出项目？`, '确认', { type: 'warning' })
  await removeMember(pid.value, row.user_id)
  ElMessage.success('已移除')
  await load()
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
small { color: #999; }
.hint { color: #b0b0b0; font-size: 12px; margin-left: 6px; }
</style>
