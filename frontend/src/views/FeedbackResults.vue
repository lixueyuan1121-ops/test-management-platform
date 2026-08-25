<template>
  <div class="feedback-results">
    <el-card>
      <template #header>
        <div class="header">
          <span>回归结果</span>
          <div class="actions">
            <el-select v-model="setFilter" placeholder="按回归集" size="small" clearable style="width:180px" @change="reload">
              <el-option v-for="s in sets" :key="s.id" :label="s.name" :value="s.id" />
            </el-select>
            <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
          </div>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon class="intro">
        每次回归/执行是一个批次（feedback_run）。结果按批次实时聚合执行机回写的 exec_run 状态。
        点「详情」看批次内逐条用例的 pass/fail。
      </el-alert>

      <el-table :data="rows" v-loading="loading" size="small" border stripe empty-text="暂无回归记录">
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
    </el-card>

    <!-- 批次详情 -->
    <el-drawer v-model="detailDrawer" :title="`批次详情 #${cur?.id || ''}`" size="55%">
      <template v-if="cur">
        <div class="detail-head">
          <el-tag :type="cur.trigger === 'auto' ? 'success' : 'primary'" size="small">{{ cur.trigger === 'auto' ? '定时' : '手动' }}</el-tag>
          <span class="batch">{{ cur.batch_id }}</span>
          <span class="agg">通过 {{ cur.stats.passed }} / 失败 {{ cur.stats.failed }} / 阻塞 {{ cur.stats.blocked }} / 待跑 {{ cur.stats.pending + cur.stats.running }}（共 {{ cur.stats.total }}）</span>
        </div>
        <el-table :data="cur.items" size="small" border stripe empty-text="无执行项">
          <el-table-column prop="run_id" label="run" width="64" align="center" />
          <el-table-column prop="title" label="用例" min-width="200" show-overflow-tooltip />
          <el-table-column label="类型" width="64" align="center"><template #default="{ row }"><el-tag size="small" effect="plain">{{ row.kind }}</el-tag></template></el-table-column>
          <el-table-column label="结果" width="84" align="center">
            <template #default="{ row }"><el-tag :type="ST_TYPE[row.status] || 'info'" size="small">{{ ST_LABEL[row.status] || row.status }}</el-tag></template>
          </el-table-column>
          <el-table-column label="原因/证据" min-width="180">
            <template #default="{ row }">
              <span v-if="row.reason" class="reason">{{ row.reason }}</span>
              <a v-if="row.evidence_url" :href="row.evidence_url" target="_blank" class="ev">证据</a>
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
import { ref, onMounted } from 'vue'
import { feedbackRuns, feedbackRunDetail, feedbackSets } from '@/api'

const ST_TYPE = { passed: 'success', failed: 'danger', blocked: 'warning', running: 'primary', pending: 'info' }
const ST_LABEL = { passed: '通过', failed: '失败', blocked: '阻塞', running: '执行中', pending: '待跑' }

const rows = ref([])
const sets = ref([])
const loading = ref(false)
const setFilter = ref(null)

const detailDrawer = ref(false)
const cur = ref(null)

function fmt(s) { return s ? s.replace('T', ' ').slice(0, 19) : '—' }

async function reload() {
  loading.value = true
  try { rows.value = await feedbackRuns(setFilter.value || undefined) } catch { /* ignore */ } finally { loading.value = false }
}

async function openDetail(row) {
  detailDrawer.value = true
  cur.value = null
  try { cur.value = await feedbackRunDetail(row.id) } catch { detailDrawer.value = false }
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
.detail-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.batch { font-family: monospace; font-size: 12px; color: #606266; }
.agg { font-size: 13px; color: #303133; }
.reason { font-size: 12px; color: #f56c6c; }
.ev { margin-left: 8px; color: #409eff; font-size: 12px; }
.none { color: #c0c4cc; }
</style>
