<template>
  <div class="users">
    <el-card>
      <template #header>
        <div class="header">
          <span>用户管理</span>
          <div>
            <el-input v-model="keyword" placeholder="搜索用户名/姓名" clearable size="small" style="width:200px;margin-right:8px" @input="onSearch" />
            <el-button type="primary" size="small" @click="openCreate">新建用户</el-button>
          </div>
        </div>
      </template>
      <el-table :data="users" v-loading="loading" size="small">
        <el-table-column prop="username" label="用户名" width="160" />
        <el-table-column prop="name" label="姓名" width="160" />
        <el-table-column prop="email" label="邮箱" />
        <el-table-column label="身份" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.is_platform_admin" type="danger">平台管理员</el-tag>
            <el-tag v-else type="info">普通用户</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'warning'">
              {{ row.status === 'active' ? '正常' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" @click="openPwd(row)">重置密码</el-button>
            <el-button link :type="row.status === 'active' ? 'warning' : 'success'" @click="toggleStatus(row)">
              {{ row.status === 'active' ? '禁用' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建/编辑 -->
    <el-dialog v-if="dialog.visible" v-model="dialog.visible" :title="dialog.id ? '编辑用户' : '新建用户'" width="460px">
      <el-form :model="form" label-width="100px">
        <el-form-item v-if="!dialog.id" label="用户名">
          <el-input v-model="form.username" placeholder="字母/数字/_.-" />
        </el-form-item>
        <el-form-item v-else label="用户名">
          <el-input :model-value="form.username" disabled />
        </el-form-item>
        <el-form-item v-if="!dialog.id" label="密码" required>
          <el-input v-model="form.password" type="password" show-password placeholder="至少6位" />
        </el-form-item>
        <el-form-item label="姓名"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="form.email" /></el-form-item>
        <el-form-item label="平台管理员">
          <el-switch v-model="form.is_platform_admin" />
          <span class="tip">勾选后该用户可管理所有项目与全局用户</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-if="pwd.visible" v-model="pwd.visible" title="重置密码" width="420px">
      <el-form label-width="80px">
        <el-form-item label="用户">
          <el-input :model-value="pwd.username" disabled />
        </el-form-item>
        <el-form-item label="新密码" required>
          <el-input v-model="pwd.password" type="password" show-password placeholder="至少6位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwd.visible = false">取消</el-button>
        <el-button type="primary" :loading="pwd.saving" @click="submitPwd">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listUsers, createUser, updateUser, resetPassword } from '@/api'

const users = ref([])
const loading = ref(false)
const keyword = ref('')
let searchTimer = null

const dialog = reactive({ visible: false, id: null, saving: false })
const form = reactive({ username: '', password: '', name: '', email: '', is_platform_admin: false })
const pwd = reactive({ visible: false, id: null, username: '', password: '', saving: false })

async function load() {
  loading.value = true
  try { users.value = await listUsers(keyword.value || undefined) } finally { loading.value = false }
}
onMounted(load)

function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(load, 300)
}

function openCreate() {
  dialog.id = null
  Object.assign(form, { username: '', password: '', name: '', email: '', is_platform_admin: false })
  dialog.visible = true
}
function openEdit(row) {
  dialog.id = row.id
  Object.assign(form, {
    username: row.username, password: '',
    name: row.name, email: row.email || '', is_platform_admin: row.is_platform_admin,
  })
  dialog.visible = true
}
async function submit() {
  if (!form.name) { ElMessage.warning('请填姓名'); return }
  dialog.saving = true
  try {
    if (dialog.id) {
      await updateUser(dialog.id, {
        name: form.name, email: form.email, is_platform_admin: form.is_platform_admin,
      })
    } else {
      if (!form.username || !form.password) { ElMessage.warning('用户名和密码必填'); dialog.saving = false; return }
      await createUser({
        username: form.username, password: form.password,
        name: form.name, email: form.email, is_platform_admin: form.is_platform_admin,
      })
    }
    ElMessage.success('保存成功')
        await load()
  } finally { dialog.saving = false; dialog.visible = false }
}

function openPwd(row) {
  pwd.id = row.id
  pwd.username = row.username
  pwd.password = ''
  pwd.visible = true
}
async function submitPwd() {
  if (!pwd.password || pwd.password.length < 6) { ElMessage.warning('密码至少6位'); return }
  pwd.saving = true
  try {
    await resetPassword(pwd.id, { password: pwd.password })
    ElMessage.success('密码已重置')
    pwd.visible = false
  } finally { pwd.saving = false }
}

async function toggleStatus(row) {
  const next = row.status === 'active' ? 'disabled' : 'active'
  await updateUser(row.id, { status: next })
  ElMessage.success(next === 'active' ? '已启用' : '已禁用')
  await load()
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.tip { color: #999; font-size: 12px; margin-left: 8px; }
</style>
