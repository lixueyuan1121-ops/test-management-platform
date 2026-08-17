<template>
  <div class="selector-admin">
    <el-card>
      <template #header>
        <div class="header">
          <span>选择器管理</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="subProduct" placeholder="作用域" size="small" style="width:150px" @change="reload">
              <el-option label="项目级共享" :value="''" />
              <el-option v-for="sp in SUB_PRODUCTS" :key="sp" :label="sp" :value="sp" />
            </el-select>
            <el-button type="primary" size="small" :disabled="!pid" @click="openCreate">新增 key</el-button>
            <el-button
              v-if="canImport" size="small" :disabled="!pid" :loading="importing" @click="onImport"
            >导入内置纳米Work注册表</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" size="small" border stripe empty-text="该作用域暂无选择器 key">
        <el-table-column prop="key" label="key" min-width="180" show-overflow-tooltip />
        <el-table-column prop="frame" label="frame" width="120">
          <template #default="{ row }">{{ row.frame || 'auto' }}</template>
        </el-table-column>
        <el-table-column prop="desc" label="说明" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.desc || '—' }}</template>
        </el-table-column>
        <el-table-column label="候选数" width="90" align="center">
          <template #default="{ row }">{{ (row.candidates || []).length }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新增 / 编辑弹窗 -->
    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑 key' : '新增 key'" width="600px">
      <el-form label-width="80px">
        <el-form-item label="key" required>
          <el-input v-model="dialog.key" :disabled="!!dialog.id" placeholder="语义 key，如 login_button" />
        </el-form-item>
        <el-form-item label="frame">
          <el-input v-model="dialog.frame" placeholder="auto / main / iframe 名，缺省 auto" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="dialog.desc" type="textarea" :rows="2" placeholder="这个 key 找的是什么元素" />
        </el-form-item>
        <el-form-item label="候选">
          <el-input v-model="dialog.candidatesText" type="textarea" :rows="6" placeholder='JSON 数组，如 ["text=登录", "css=.login-btn"]' />
          <div class="form-hint">候选定位器数组（JSON），按顺序尝试。空数组 [] 亦可</div>
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { useAppStore } from '@/store/app'
import { listSelectors, createSelector, patchSelector, deleteSelector, importLegacySelectors } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

// 子产品固定枚举，须与后端 api/release.py 的 SUB_PRODUCTS 一致（选择器按 (项目, 子产品) 分域）。
const SUB_PRODUCTS = ['纳米Work云端版', '纳米Work桌面版', '360安全龙虾云端版', '360安全龙虾WSL']

const auth = useAuthStore()
const app = useAppStore()

const projects = ref([])
const pid = ref(null)
const subProduct = ref('')   // '' = 项目级共享
const rows = ref([])
const loading = ref(false)
const importing = ref(false)

// 导入旧注册表仅项目 admin（后端 import-legacy 要求 admin）；平台管理员在任何项目都视为 admin。
const canImport = computed(() => !!pid.value && auth.roleIn(pid.value) === 'admin')

onMounted(async () => {
  try { projects.value = await app.fetchProjects() } catch { projects.value = [] }
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await reload()
  }
})

async function onProjectChange() {
  if (pid.value) setLastProjectId(pid.value)
  await reload()
}

// 按当前 (项目, 子产品) 取列表：'' 取 shared，否则取 by_sub[子产品]。
async function reload() {
  if (!pid.value) { rows.value = []; return }
  loading.value = true
  try {
    const data = await listSelectors(pid.value)
    rows.value = subProduct.value ? (data.by_sub?.[subProduct.value] || []) : (data.shared || [])
  } finally { loading.value = false }
}

function fmtTime(s) {
  if (!s) return '—'
  return String(s).replace('T', ' ').slice(0, 16)
}

// ---- 新增 / 编辑 ----
const dialog = reactive({ visible: false, id: null, key: '', frame: '', desc: '', candidatesText: '[]', saving: false })

function openCreate() {
  Object.assign(dialog, { id: null, key: '', frame: 'auto', desc: '', candidatesText: '[]', saving: false, visible: true })
}
function openEdit(row) {
  Object.assign(dialog, {
    id: row.id, key: row.key, frame: row.frame || 'auto', desc: row.desc || '',
    candidatesText: JSON.stringify(row.candidates || [], null, 2), saving: false, visible: true,
  })
}

// candidates 文本域按 JSON 解析成数组；非法 JSON 或非数组给出错误提示并中断。
function parseCandidates() {
  let arr
  try { arr = JSON.parse(dialog.candidatesText || '[]') } catch { ElMessage.error('候选不是合法 JSON'); return null }
  if (!Array.isArray(arr)) { ElMessage.error('候选必须是 JSON 数组'); return null }
  return arr
}

async function submit() {
  if (!dialog.id && !dialog.key.trim()) { ElMessage.warning('key 不能为空'); return }
  const candidates = parseCandidates()
  if (candidates === null) return
  dialog.saving = true
  try {
    if (dialog.id) {
      await patchSelector(dialog.id, { frame: dialog.frame || 'auto', desc: dialog.desc || '', candidates })
    } else {
      await createSelector({
        project_id: pid.value, sub_product: subProduct.value, key: dialog.key.trim(),
        frame: dialog.frame || 'auto', desc: dialog.desc || '', candidates,
      })
    }
    ElMessage.success('已保存')
    dialog.visible = false
    await reload()
  } catch { /* http 拦截器已提示（如 key 冲突）*/ }
  finally { dialog.saving = false }
}

// ---- 删除 ----
async function onDelete(row) {
  try {
    await ElMessageBox.confirm(`删除选择器 key「${row.key}」？`, '删除', { type: 'warning' })
  } catch { return }
  try { await deleteSelector(row.id); ElMessage.success('已删除'); await reload() } catch { /* 已提示 */ }
}

// ---- 导入内置旧注册表（写入项目级共享）----
async function onImport() {
  try {
    await ElMessageBox.confirm(
      '将内置纳米Work注册表导入为本项目【项目级共享】的 key（同名 key 跳过），确认？',
      '导入内置注册表', { type: 'warning' },
    )
  } catch { return }
  importing.value = true
  try {
    const res = await importLegacySelectors(pid.value)
    ElMessage.success(`导入完成：新增 ${res.imported} 个，跳过 ${res.skipped} 个`)
    if (subProduct.value !== '') subProduct.value = ''   // 导入写入共享域，切过去看结果
    await reload()
  } catch { /* 已提示 */ }
  finally { importing.value = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.form-hint { color: #90a4ae; font-size: 12px; }
</style>
