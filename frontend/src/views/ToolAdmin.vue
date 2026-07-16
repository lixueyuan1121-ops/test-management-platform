<template>
  <div class="tool-admin">
    <!-- 分类管理 -->
    <el-card style="margin-bottom:16px">
      <template #header>
        <div class="header">
          <span>工具分类</span>
          <el-button type="primary" size="small" @click="openCatCreate">新建分类</el-button>
        </div>
      </template>
      <el-table :data="categories" v-loading="catLoading" size="small">
        <el-table-column prop="name" label="分类名称" />
        <el-table-column prop="sort_order" label="排序" width="80" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button link type="primary" @click="openCatEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="onCatDel(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 工具管理 -->
    <el-card>
      <template #header>
        <div class="header">
          <span>工具列表</span>
          <div>
            <el-select v-model="toolCatFilter" placeholder="筛选分类" clearable size="small" style="width:140px;margin-right:8px" @change="loadTools">
              <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
            </el-select>
            <el-button type="primary" size="small" @click="openToolCreate">新建工具</el-button>
          </div>
        </div>
      </template>
      <el-table :data="tools" v-loading="toolLoading" size="small">
        <el-table-column prop="name" label="工具名称" min-width="120" />
        <el-table-column prop="category_name" label="分类" width="100" />
        <el-table-column prop="version" label="版本" width="80" />
        <el-table-column label="下载地址" min-width="160">
          <template #default="{ row }"><el-link v-if="row.download_url" :href="row.download_url" target="_blank" type="primary" :underline="false">下载</el-link><span v-else>-</span></template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }"><el-tag :type="row.status === 'online' ? 'success' : 'info'" size="small">{{ row.status === 'online' ? '在线' : '下线' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button link :type="row.status === 'online' ? 'warning' : 'success'" @click="onToggle(row)">{{ row.status === 'online' ? '下线' : '上线' }}</el-button>
            <el-button link type="primary" @click="openToolEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="onToolDel(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 分类对话框 -->
    <el-dialog v-if="catDialog.visible" v-model="catDialog.visible" :title="catDialog.id ? '编辑分类' : '新建分类'" width="400px">
      <el-form label-width="80px">
        <el-form-item label="名称"><el-input v-model="catForm.name" /></el-form-item>
        <el-form-item label="排序"><el-input-number v-model="catForm.sort_order" :min="0" /></el-form-item>
        <el-form-item v-if="catDialog.id" label="状态"><el-switch v-model="catForm.is_active" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="catDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="catDialog.saving" @click="submitCat">保存</el-button>
      </template>
    </el-dialog>

    <!-- 工具对话框 -->
    <el-dialog v-if="toolDialog.visible" v-model="toolDialog.visible" :title="toolDialog.id ? '编辑工具' : '新建工具'" width="560px">
      <el-form :model="toolForm" label-width="90px">
        <el-form-item label="分类" required>
          <el-select v-model="toolForm.category_id" placeholder="选择分类" style="width:100%">
            <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="工具名称" required><el-input v-model="toolForm.name" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="toolForm.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="下载地址"><el-input v-model="toolForm.download_url" placeholder="http://..." /></el-form-item>
        <el-form-item label="文档地址"><el-input v-model="toolForm.doc_url" placeholder="http://..." /></el-form-item>
        <el-form-item label="图标"><el-input v-model="toolForm.icon" placeholder="emoji 如 🔧 或图片URL" /></el-form-item>
        <el-form-item label="版本"><el-input v-model="toolForm.version" placeholder="1.0.0" /></el-form-item>
        <el-form-item label="排序"><el-input-number v-model="toolForm.sort_order" :min="0" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="toolDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="toolDialog.saving" @click="submitTool">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listCategories, createCategory, updateCategory, deleteCategory, listTools, createTool, updateTool, deleteTool, toggleTool } from '@/api'

const categories = ref([])
const catLoading = ref(false)
const catDialog = reactive({ visible: false, id: null, saving: false })
const catForm = reactive({ name: '', sort_order: 0, is_active: true })

const tools = ref([])
const toolLoading = ref(false)
const toolCatFilter = ref(null)
const toolDialog = reactive({ visible: false, id: null, saving: false })
const toolForm = reactive({ category_id: null, name: '', description: '', download_url: '', doc_url: '', icon: '', version: '', sort_order: 0 })

onMounted(async () => { await loadCats(); await loadTools() })

async function loadCats() {
  catLoading.value = true
  try { categories.value = await listCategories(true) } finally { catLoading.value = false }
}
async function loadTools() {
  toolLoading.value = true
  try { tools.value = await listTools({ category_id: toolCatFilter.value || undefined }) } finally { toolLoading.value = false }
}

function openCatCreate() { catDialog.id = null; Object.assign(catForm, { name: '', sort_order: 0, is_active: true }); catDialog.visible = true }
function openCatEdit(row) { catDialog.id = row.id; Object.assign(catForm, { name: row.name, sort_order: row.sort_order, is_active: row.is_active }); catDialog.visible = true }
async function submitCat() {
  if (!catForm.name) { ElMessage.warning('名称必填'); return }
  catDialog.saving = true
  try {
    if (catDialog.id) await updateCategory(catDialog.id, catForm)
    else await createCategory(catForm)
    ElMessage.success('保存成功'); catDialog.visible = false; await loadCats()
  } finally { catDialog.saving = false }
}
async function onCatDel(row) {
  await ElMessageBox.confirm(`删除分类「${row.name}」？其下工具也会删除。`, '确认', { type: 'warning' })
  await deleteCategory(row.id); ElMessage.success('已删除'); await loadCats(); await loadTools()
}

function openToolCreate() { toolDialog.id = null; Object.assign(toolForm, { category_id: null, name: '', description: '', download_url: '', doc_url: '', icon: '', version: '', sort_order: 0 }); toolDialog.visible = true }
function openToolEdit(row) { toolDialog.id = row.id; Object.assign(toolForm, { category_id: row.category_id, name: row.name, description: row.description || '', download_url: row.download_url || '', doc_url: row.doc_url || '', icon: row.icon || '', version: row.version || '', sort_order: row.sort_order }); toolDialog.visible = true }
async function submitTool() {
  if (!toolForm.name || !toolForm.category_id) { ElMessage.warning('名称和分类必填'); return }
  toolDialog.saving = true
  try {
    if (toolDialog.id) await updateTool(toolDialog.id, toolForm)
    else await createTool(toolForm)
    ElMessage.success('保存成功'); toolDialog.visible = false; await loadTools()
  } finally { toolDialog.saving = false }
}
async function onToolDel(row) {
  await ElMessageBox.confirm(`删除工具「${row.name}」？`, '确认', { type: 'warning' })
  await deleteTool(row.id); ElMessage.success('已删除'); await loadTools()
}
async function onToggle(row) {
  await toggleTool(row.id); ElMessage.success('已切换'); await loadTools()
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
</style>
