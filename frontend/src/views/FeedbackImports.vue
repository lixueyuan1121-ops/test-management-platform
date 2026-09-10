<template>
  <div class="feedback-imports functional-workspace">
    <WorkspacePage title="反馈导入记录">
      <template #actions>
            <el-button size="small" :icon="Refresh" aria-label="刷新导入记录" title="刷新导入记录" :loading="loading" @click="reload" />
            <el-button type="primary" size="small" @click="uploadDlg = true">手动上传 md/zip</el-button>
      </template>
      <template #filters>
        <el-input v-model="keyword" clearable placeholder="搜索文件或来源" aria-label="搜索导入记录" style="width:260px" />
        <el-select v-model="statusFilter" clearable placeholder="全部状态" aria-label="导入状态" style="width:160px"><el-option v-for="(label, value) in STATUS_LABEL" :key="value" :label="label" :value="value" /></el-select>
        <span>共 {{ filteredRows.length }} 条</span>
      </template>
      <el-result v-if="loadError" icon="error" title="导入记录加载失败"><template #extra><el-button @click="reload">重新加载</el-button></template></el-result>
      <el-table v-else :data="filteredRows" v-loading="loading" size="small" border stripe empty-text="暂无符合条件的导入记录">
        <el-table-column prop="id" label="ID" width="60" align="center" />
        <el-table-column prop="filename" label="文件" min-width="200" show-overflow-tooltip />
        <el-table-column prop="source_bot" label="来源" width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.source_bot || '—' }}</template>
        </el-table-column>
        <el-table-column label="文件数" width="80" align="center"><template #default="{ row }">{{ row.file_count }}</template></el-table-column>
        <el-table-column label="用例数" width="80" align="center"><template #default="{ row }">{{ row.case_count }}</template></el-table-column>
        <el-table-column label="补 script" width="130" align="center">
          <template #default="{ row }">
            <template v-if="row.script_total > 0">
              <el-progress :percentage="pct(row)" :status="row.script_done >= row.script_total ? 'success' : ''" :stroke-width="10" />
              <span class="prog-txt">{{ row.script_done }}/{{ row.script_total }}</span>
            </template>
            <span v-else class="none">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="STATUS_TYPE[row.status] || 'info'" size="small">{{ STATUS_LABEL[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="导入时间" width="160">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="错误" min-width="120">
          <template #default="{ row }">
            <el-tooltip v-if="row.error" :content="row.error" placement="top"><el-tag type="danger" size="small">有</el-tag></el-tooltip>
            <span v-else class="none">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center">
          <template #default="{ row }">
            <el-button
              v-if="row.script_total > 0 && row.script_done < row.script_total"
              link type="warning" size="small" :loading="row._refill" @click="refill(row)"
            >续补 script</el-button>
            <span v-else class="none">—</span>
          </template>
        </el-table-column>
      </el-table>
    </WorkspacePage>

    <!-- 手动上传兜底 -->
    <el-dialog v-model="uploadDlg" title="手动上传反馈 md/zip" width="460px">
      <el-alert type="warning" :closable="false" class="dlg-tip">
        走机器人对接接口，需填 Bot Token（后端 <code>FEEDBACK_BOT_TOKEN</code>）。仅用于测试兜底。
      </el-alert>
      <el-form label-width="90px" class="up-form">
        <el-form-item label="Bot Token">
          <el-input v-model="botToken" placeholder="X-Bot-Token" size="small" show-password />
        </el-form-item>
        <el-form-item label="来源标识">
          <el-input v-model="srcBot" placeholder="可选，如 手动上传" size="small" />
        </el-form-item>
        <el-form-item label="文件">
          <input ref="fileInput" type="file" accept=".md,.zip" @change="onFile" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadDlg = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!file || !botToken" @click="doUpload">上传</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import WorkspacePage from '@/components/WorkspacePage.vue'
import { Refresh } from '@element-plus/icons-vue'
import '@/styles/workspace-overlays.css'
import { ElMessage } from 'element-plus'
import { feedbackImports, refillScripts } from '@/api'

const STATUS_TYPE = { parsing: 'warning', done: 'success', failed: 'danger' }
const STATUS_LABEL = { parsing: '解析中', done: '完成', failed: '失败' }

const rows = ref([])
const keyword = ref('')
const statusFilter = ref('')
const filteredRows = computed(() => rows.value.filter(row =>
  `${row.filename || ''} ${row.source_bot || ''}`.toLocaleLowerCase().includes(keyword.value.trim().toLocaleLowerCase()) &&
  (!statusFilter.value || row.status === statusFilter.value)))
const loading = ref(false)
const loadError = ref(false)
const uploadDlg = ref(false)
const botToken = ref(localStorage.getItem('tp_feedback_bot_token') || '')
const srcBot = ref('手动上传')
const file = ref(null)
const fileInput = ref(null)
const uploading = ref(false)

function pct(row) {
  if (!row.script_total) return 0
  return Math.round((row.script_done / row.script_total) * 100)
}
function fmt(s) {
  return s ? s.replace('T', ' ').slice(0, 19) : '—'
}

async function reload() {
  if (loading.value) return
  loading.value = true
  loadError.value = false
  try {
    rows.value = await feedbackImports()
  } catch { rows.value = []; loadError.value = true } finally {
    loading.value = false
  }
}

function onFile(e) {
  file.value = e.target.files?.[0] || null
}

async function refill(row) {
  row._refill = true
  try {
    const res = await refillScripts(row.id)
    if (res.started) {
      ElMessage.success(`已在后台续补 ${res.pending} 条 script，可稍后刷新看进度`)
    } else {
      ElMessage.info(res.msg || '无需续补')
    }
  } catch { /* 拦截器已提示 */ } finally {
    row._refill = false
  }
}

async function doUpload() {
  if (uploading.value || !file.value || !botToken.value) return
  uploading.value = true
  localStorage.setItem('tp_feedback_bot_token', botToken.value)
  const fd = new FormData()
  fd.append('file', file.value)
  if (srcBot.value) fd.append('source_bot', srcBot.value)
  try {
    const resp = await fetch('/api/feedback/ingest', {
      method: 'POST',
      headers: { 'X-Bot-Token': botToken.value },
      body: fd,
    })
    const j = await resp.json()
    if (!resp.ok || j.code !== 0) {
      ElMessage.error(j.detail || j.msg || `上传失败(${resp.status})`)
    } else {
      ElMessage.success(`导入成功：${j.data.case_count} 条用例（${j.data.script_total} 条待补 script）`)
      uploadDlg.value = false
      file.value = null
      if (fileInput.value) fileInput.value.value = ''
      reload()
    }
  } catch (e) {
    ElMessage.error('网络错误，上传失败')
  } finally {
    uploading.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.actions { display: flex; gap: 8px; }
.intro { margin-bottom: 12px; }
.prog-txt { font-size: 11px; color: #909399; }
.none { color: #c0c4cc; }
.dlg-tip { margin-bottom: 14px; }
.up-form { margin-top: 6px; }
</style>
