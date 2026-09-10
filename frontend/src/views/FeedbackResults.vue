<template>
  <div class="feedback-results">
    <WorkspacePage title="回归结果">
      <template #actions>
        <el-button size="small" :icon="Refresh" aria-label="刷新回归结果" title="刷新回归结果" :loading="loading" @click="reload" />
      </template>
      <template #filters>
            <el-select v-model="setFilter" placeholder="按回归集" size="small" clearable style="width:180px" @change="reload">
              <el-option v-for="s in sets" :key="s.id" :label="s.name" :value="s.id" />
            </el-select>
      </template>

      <el-result v-if="loadError" icon="error" title="回归记录加载失败"><template #extra><el-button @click="reload">重新加载</el-button></template></el-result>
      <el-table v-else :data="rows" v-loading="loading" size="small" border stripe empty-text="暂无回归记录">
        <el-table-column prop="id" label="ID" width="56" align="center" />
        <el-table-column label="来源" width="120">
          <template #default="{ row }">
            <el-tag :type="row.trigger === 'auto' ? 'success' : 'primary'" size="small">{{ row.trigger === 'auto' ? '定时' : '手动' }}</el-tag>
            <span v-if="row.set_name" class="set-name">{{ row.set_name }}</span>
            <span v-else class="set-name adhoc">临时执行</span>
          </template>
        </el-table-column>
        <el-table-column label="用例数" width="72" align="center"><template #default="{ row }">{{ row.case_count }}</template></el-table-column>
        <el-table-column label="进度" min-width="200">
          <template #default="{ row }">
            <div class="stat-bar">
              <el-tag type="success" size="small" effect="plain">通过 {{ row.stats.passed }}</el-tag>
              <el-tag type="danger" size="small" effect="plain">失败 {{ row.stats.failed }}</el-tag>
              <el-tag type="warning" size="small" effect="plain">阻塞 {{ row.stats.blocked }}</el-tag>
              <el-tag type="info" size="small" effect="plain">待跑 {{ row.stats.pending + row.stats.running }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.stats.finished ? 'success' : 'warning'" size="small">{{ row.stats.finished ? '已完成' : '进行中' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="batch_id" label="批次" width="160" show-overflow-tooltip />
        <el-table-column prop="created_at" label="触发时间" width="160"><template #default="{ row }">{{ fmt(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="80" align="center">
          <template #default="{ row }"><el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button></template>
        </el-table-column>
      </el-table>
    </WorkspacePage>

    <!-- 批次详情 -->
    <el-drawer v-model="detailDrawer" :title="`批次详情 #${detailId || ''}`" size="min(960px, 100vw)" @closed="closeDetail">
      <el-skeleton v-if="detailLoading" :rows="5" animated />
      <el-result v-else-if="detailError" icon="error" title="批次详情加载失败"><template #extra><el-button @click="openDetail({ id: detailId })">重新加载详情</el-button></template></el-result>
      <template v-if="cur">
        <div class="detail-head">
          <el-tag :type="cur.trigger === 'auto' ? 'success' : 'primary'" size="small">{{ cur.trigger === 'auto' ? '定时' : '手动' }}</el-tag>
          <span class="batch">{{ cur.batch_id }}</span>
          <span class="agg">通过 {{ cur.stats.passed }} / 失败 {{ cur.stats.failed }} / 阻塞 {{ cur.stats.blocked }} / 待跑 {{ cur.stats.pending + cur.stats.running }}（共 {{ cur.stats.total }}）</span>
        </div>
        <el-table :data="cur.items" size="small" border stripe empty-text="无执行项">
          <el-table-column type="expand" width="42">
            <template #default="{ row }"><div class="full-reason"><h3>{{ row.title }}</h3><p>{{ row.reason || '暂无原因记录' }}</p></div></template>
          </el-table-column>
          <el-table-column prop="run_id" label="run" width="64" align="center" />
          <el-table-column prop="title" label="用例" min-width="200" show-overflow-tooltip />
          <el-table-column label="类型" width="64" align="center"><template #default="{ row }"><el-tag size="small" effect="plain">{{ row.kind }}</el-tag></template></el-table-column>
          <el-table-column label="结果" width="84" align="center">
            <template #default="{ row }"><el-tag :type="ST_TYPE[row.status] || 'info'" size="small">{{ ST_LABEL[row.status] || row.status }}</el-tag></template>
          </el-table-column>
          <el-table-column label="原因/证据" min-width="180">
            <template #default="{ row }">
              <span v-if="row.reason" class="reason">{{ row.reason }}</span>
              <a v-if="safeEvidence(row.evidence_url)" :href="row.evidence_url" target="_blank" rel="noopener noreferrer" class="ev">证据</a>
              <span v-else-if="row.evidence_url" class="none">证据地址不可打开</span>
              <span v-if="!row.reason && !row.evidence_url" class="none">—</span>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="80" align="center"><template #default="{ row }">{{ row.duration_ms ? (row.duration_ms / 1000).toFixed(1) + 's' : '—' }}</template></el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import { Refresh } from '@element-plus/icons-vue'
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { feedbackRuns, feedbackRunDetail, feedbackSets } from '@/api'

const ST_TYPE = { passed: 'success', failed: 'danger', blocked: 'warning', running: 'primary', pending: 'info' }
const ST_LABEL = { passed: '通过', failed: '失败', blocked: '阻塞', running: '执行中', pending: '待跑' }

const rows = ref([])
const sets = ref([])
const loading = ref(false)
const loadError = ref(false)
let loadVersion = 0
const setFilter = ref(null)

const detailDrawer = ref(false)
const cur = ref(null)
const detailId = ref(null)
const detailLoading = ref(false)
const detailError = ref(false)
let detailVersion = 0
const safeEvidence = url => typeof url === 'string' && (/^https?:\/\//i.test(url) || /^\/(?![\\/])/.test(url)) && !/[\u0000-\u0020\\]/.test(url)
function closeDetail() { detailVersion++; cur.value = null }
onBeforeUnmount(() => { loadVersion++; detailVersion++ })

function fmt(s) { return s ? s.replace('T', ' ').slice(0, 19) : '—' }

async function reload() {
  const version = ++loadVersion
  loading.value = true
  loadError.value = false
  try {
    const data = await feedbackRuns(setFilter.value || undefined)
    if (version === loadVersion) rows.value = data
  } catch {
    if (version === loadVersion) { rows.value = []; loadError.value = true }
  } finally { if (version === loadVersion) loading.value = false }
}

async function openDetail(row) {
  const version = ++detailVersion
  detailId.value = row.id
  detailDrawer.value = true
  cur.value = null
  detailError.value = false
  detailLoading.value = true
  try {
    const data = await feedbackRunDetail(row.id)
    if (version === detailVersion) cur.value = data
  } catch { if (version === detailVersion) detailError.value = true }
  finally { if (version === detailVersion) detailLoading.value = false }
}

onMounted(async () => {
  reload()
  try { sets.value = await feedbackSets() } catch { /* ignore */ }
})
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.actions { display: flex; gap: 8px; }
.intro { margin-bottom: 12px; }
.set-name { margin-left: 6px; font-size: 12px; color: #606266; }
.adhoc { color: #909399; font-style: italic; }
.stat-bar { display: flex; gap: 6px; flex-wrap: wrap; }
.detail-head { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin-bottom:12px; overflow-wrap:anywhere; }
.full-reason { padding:12px 20px; font:14px/1.7 system-ui,sans-serif; overflow-wrap:anywhere; }
.full-reason h3 { font-size:14px; margin:0; }
.full-reason p { white-space:pre-wrap; }
.batch { font-family: monospace; font-size: 12px; color: #606266; }
.agg { font-size: 13px; color: #303133; }
.reason { font-size:12px; color:#606266; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
.ev { margin-left: 8px; color: #409eff; font-size: 12px; }
.none { color: #c0c4cc; }
</style>
