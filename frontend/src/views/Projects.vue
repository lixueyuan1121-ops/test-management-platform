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
import { listProjects, createProject, updateProject } from '@/api'

const projects = ref([])
const loading = ref(false)
const dialog = reactive({ visible: false, id: null, saving: false })
const form = reactive({ name: '', code: '', description: '', status: 'active' })

async function load() {
  loading.value = true
  try { projects.value = await listProjects() } finally { loading.value = false }
}
onMounted(load)

function openCreate() {
  dialog.id = null
  Object.assign(form, { name: '', code: '', description: '', status: 'active' })
  dialog.visible = true
}
function openEdit(row) {
  dialog.id = row.id
  Object.assign(form, { name: row.name, code: row.code, description: row.description || '', status: row.status })
  dialog.visible = true
}
async function submit() {
  if (!form.name || !form.code) { ElMessage.warning('名称和编码必填'); return }
  dialog.saving = true
  try {
    if (dialog.id) {
      await updateProject(dialog.id, { name: form.name, description: form.description, status: form.status })
    } else {
      await createProject({ name: form.name, code: form.code, description: form.description })
    }
    ElMessage.success('保存成功')
        await load()
  } finally { dialog.saving = false; dialog.visible = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
</style>
