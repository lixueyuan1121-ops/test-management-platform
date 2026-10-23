<template>
  <el-dialog :model-value="true" title="填写发版记录" width="min(820px, 96vw)" :close-on-click-modal="false" :show-close="!saving" :before-close="close">
    <el-alert title="仅填写当前范围中已上线、已填写归属版本且尚未关联发版记录的任务。追加保留已有上线内容，需求数按新增任务累加。" type="info" :closable="false" />
    <el-form label-width="100px" :disabled="saving" class="release-form">
      <el-form-item label="归属版本"><el-select v-model="version" aria-label="发版归属版本" @change="selectVersion"><el-option v-for="v in versions" :key="v" :value="v" :label="v" /></el-select></el-form-item>
      <el-form-item label="目标记录">
        <el-select v-model="form.release_id" :loading="loading" aria-label="目标发版记录" style="width:100%">
          <el-option :value="0" label="新建发版记录" />
          <el-option v-for="r in matchingReleases" :key="r.id" :value="r.id" :label="`${r.version} · ${r.release_date} · ${r.sub_product || '未指定子产品'} (#${r.id})`" />
        </el-select>
      </el-form-item>
      <template v-if="!form.release_id">
        <el-form-item label="发版日期" required><el-date-picker v-model="form.release_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item label="子产品"><el-select v-model="form.sub_product" clearable style="width:100%"><el-option v-for="s in subProducts" :key="s" :value="s" :label="s" /></el-select></el-form-item>
        <el-form-item v-if="platform === 'app'" label="发版渠道"><el-select v-model="form.channel" multiple filterable allow-create default-first-option><el-option v-for="s in ['官网', '自升级', '全渠道', 'App Store', '内测']" :key="s" :value="s" :label="s" /></el-select></el-form-item>
        <el-form-item label="备忘"><el-input v-model="form.memo" type="textarea" :rows="2" /></el-form-item>
      </template>
      <el-form-item v-else label="已有上线内容"><pre class="existing-content">{{ targetRelease?.content || '（无）' }}</pre></el-form-item>
      <el-form-item label="本次任务">
        <el-checkbox-group v-model="selectedIds" @change="rebuildContent">
          <el-checkbox v-for="t in versionTasks" :key="t.id" :value="t.id">#{{ t.id }} {{ t.title }}</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
      <el-form-item label="本次上线内容"><el-input v-model="form.content" type="textarea" :rows="7" maxlength="50000" aria-label="本次上线内容" /><span class="hint">可编辑；调整版本或任务勾选会重新生成内容。</span></el-form-item>
    </el-form>
    <el-alert v-if="loadError" title="发版记录加载失败" type="error" :closable="false"><el-button link @click="load">重试</el-button></el-alert>
    <template #footer>
      <el-button :disabled="saving" @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="!selectedIds.length || loading || loadError" @click="save">{{ form.release_id ? '追加' : '创建' }}发版记录（{{ selectedIds.length }} 项）</el-button>
    </template>
  </el-dialog>
</template>
<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listReleases, listReleaseProducts } from '@/api'
import { fillTaskRelease } from '@/api/taskLedger'
import { localDate } from '@/utils/taskLedger'
const props = defineProps({ rows: Array, projectId: Number, platform: String })
const emit = defineEmits(['close', 'saved'])
const versions = [...new Set(props.rows.map(t => t.target_version).filter(Boolean))].sort()
const version = ref(versions[0] || ''), selectedIds = ref([]), releases = ref([])
const loading = ref(false), saving = ref(false), loadError = ref(false)
const form = reactive({ release_id: 0, release_date: localDate(), sub_product: null, channel: [], content: '', memo: '' })
const subProducts = ref([])
const matchingReleases = computed(() => releases.value.filter(r => r.version === version.value))
const targetRelease = computed(() => releases.value.find(r => r.id === form.release_id))
const versionTasks = computed(() => props.rows.filter(t => t.target_version === version.value))
function rebuildContent() {
  form.content = versionTasks.value.filter(t => selectedIds.value.includes(t.id)).map((t, i) => `${i + 1}. ${t.title}${t.conclusion ? `\n   ${t.conclusion}` : ''}`).join('\n')
}
function selectVersion() {
  selectedIds.value = versionTasks.value.map(t => t.id); form.release_id = 0; rebuildContent()
}
function close() { if (!saving.value) emit('close') }
async function load() {
  loading.value = true; loadError.value = false
  try {
    subProducts.value = await listReleaseProducts(props.projectId)
    const rows = []; let offset = 0
    while (true) {
      const result = await listReleases({ project_id: props.projectId, limit: 200, offset })
      rows.push(...result.items); offset += result.items.length
      if (offset >= result.total || !result.items.length) break
    }
    releases.value = rows
  } catch { loadError.value = true }
  finally { loading.value = false }
}
async function save() {
  if ((!form.release_id && !form.release_date) || !form.content.trim()) { ElMessage.warning('请填写发版日期和上线内容'); return }
  saving.value = true
  try {
    const result = await fillTaskRelease({ ...form, release_date: targetRelease.value?.release_date || form.release_date, release_id: form.release_id || null, project_id: props.projectId, task_ids: selectedIds.value, version: version.value })
    ElMessage.success(`发版记录已保存，新增 ${result.added} 项任务`); emit('saved', result.release)
  } finally { saving.value = false }
}
onMounted(() => { selectVersion(); load() })
</script>
<style scoped>
.release-form { margin-top:20px; }
.el-checkbox-group { display:flex; flex-direction:column; max-height:180px; overflow:auto; width:100%; }
.el-checkbox { height:auto; min-height:32px; white-space:normal; }
.existing-content { white-space:pre-wrap; max-height:130px; overflow:auto; margin:0; }
.hint { font-size:12px; color:var(--tech-muted); }
</style>
