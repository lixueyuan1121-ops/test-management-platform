<template>
  <div class="projects">
    <el-card>
      <template #header>
        <div class="header">
          <span>项目管理</span>
          <el-button type="primary" size="small" @click="openCreate">新建项目</el-button>
        </div>
      </template>
      <el-table :data="projects" v-loading="loading" size="small">
        <el-table-column prop="code" label="编码" width="160" />
        <el-table-column prop="name" label="名称" />
        <el-table-column label="平台类型" width="100">
          <template #default="{ row }">{{ platformLabel(row.platform_type) }}</template>
        </el-table-column>
        <el-table-column prop="description" label="描述" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button link type="primary" @click="$router.push(`/projects/${row.id}/members`)">成员</el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-if="dialog.visible" v-model="dialog.visible" :title="dialog.id ? '编辑项目' : '新建项目'" width="460px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="编码"><el-input v-model="form.code" :disabled="!!dialog.id" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" /></el-form-item>
        <el-form-item label="平台类型">
          <el-select v-model="form.platform_type" placeholder="未分类" clearable style="width:100%">
            <el-option label="PC端" value="pc" />
            <el-option label="APP端" value="app" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="dialog.id" label="状态">
          <el-select v-model="form.status" style="width:100%">
            <el-option label="活跃" value="active" />
            <el-option label="归档" value="archived" />
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
import { reactive, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { createProject, updateProject, listProjects } from '@/api'
import { useAppStore } from '@/store/app'

const app = useAppStore()
const projects = ref([])
const loading = ref(false)
const dialog = reactive({ visible: false, id: null, saving: false })
const form = reactive({ name: '', code: '', description: '', status: 'active', platform_type: '' })

// 平台类型展示：pc→PC端 / app→APP端 / 空→—
function platformLabel(v) { return { pc: 'PC端', app: 'APP端' }[v] || '—' }

async function load() {
  loading.value = true
  // 管理页直调（带内部项目，如反馈测试专用项目），不走 fetchProjects 缓存——避免污染生成页的下拉
  try { projects.value = await listProjects(true) } finally { loading.value = false }
}
onMounted(load)

function openCreate() {
  dialog.id = null
  Object.assign(form, { name: '', code: '', description: '', status: 'active', platform_type: '' })
  dialog.visible = true
}
function openEdit(row) {
  dialog.id = row.id
  Object.assign(form, { name: row.name, code: row.code, description: row.description || '', status: row.status, platform_type: row.platform_type || '' })
  dialog.visible = true
}
async function submit() {
  if (!form.name || !form.code) { ElMessage.warning('名称和编码必填'); return }
  dialog.saving = true
  try {
    if (dialog.id) {
      await updateProject(dialog.id, { name: form.name, description: form.description, status: form.status, platform_type: form.platform_type || null })
      app.invalidateProjects()
    } else {
      await createProject({ name: form.name, code: form.code, description: form.description, platform_type: form.platform_type || null })
      app.invalidateProjects()
    }
    ElMessage.success('保存成功')
        await load()
  } finally { dialog.saving = false; dialog.visible = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
</style>
