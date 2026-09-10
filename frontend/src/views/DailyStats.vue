<template>
  <div class="stats">
    <WorkspacePage title="日报统计">
      <template #actions>
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:180px" @change="load">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-date-picker v-model="date" type="date" value-format="YYYY-MM-DD" size="small" style="width:150px" @change="load" />
      </template>
      <el-row :gutter="12" v-loading="loading">
        <el-col :span="4"><div class="stat"><div class="num">{{ data.should_submit }}</div><div class="lbl">应交人数</div></div></el-col>
        <el-col :span="4"><div class="stat"><div class="num green">{{ data.submitted }}</div><div class="lbl">已交</div></div></el-col>
        <el-col :span="4"><div class="stat"><div class="num red">{{ data.not_submitted?.length || 0 }}</div><div class="lbl">未交</div></div></el-col>
        <el-col :span="4"><div class="stat"><div class="num">{{ data.avg_progress }}%</div><div class="lbl">平均进度</div></div></el-col>
        <el-col :span="4"><div class="stat"><div class="num green">{{ data.online_cnt }}</div><div class="lbl">当日上线</div></div></el-col>
        <el-col :span="4"><div class="stat"><div class="num red">{{ data.open_issues }}</div><div class="lbl">未解决遗留</div></div></el-col>
      </el-row>
      <el-row :gutter="12" style="margin-top:12px">
        <el-col :span="12">
          <section class="stat-section">
            <h2>未交名单</h2>
            <div v-if="data.not_submitted?.length">
              <el-tag v-for="u in data.not_submitted" :key="u.user_id" type="warning" style="margin:2px 6px">{{ u.name }}</el-tag>
            </div>
            <el-empty v-else description="全部已交" :image-size="40" />
          </section>
        </el-col>
        <el-col :span="12">
          <section class="stat-section">
            <h2>工作量合计：{{ data.workload_total }} 人时</h2>
            <div class="wl">当日合计 {{ data.workload_total }} 人时，上线 {{ data.online_cnt }} 项，新增遗留 {{ data.new_issues }} 项</div>
          </section>
        </el-col>
      </el-row>
    <section class="stat-section">
      <h2>已交日报明细</h2>
      <el-table :data="data.reports || []" size="small" empty-text="当日无日报">
        <el-table-column prop="name" label="成员" width="120" />
        <el-table-column label="进度" width="160">
          <template #default="{ row }"><el-progress :percentage="row.progress_pct" :stroke-width="12" /></template>
        </el-table-column>
        <el-table-column label="上线" width="80">
          <template #default="{ row }"><el-tag v-if="row.is_online" type="success" size="small">已上线</el-tag><span v-else>-</span></template>
        </el-table-column>
        <el-table-column prop="workload_hours" label="人时" width="80" />
        <el-table-column prop="summary" label="今日小结" />
      </el-table>
    </section>
    </WorkspacePage>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import { ref, reactive, onMounted } from 'vue'
import { dailyStats } from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const date = ref(new Date().toISOString().slice(0, 10))
const data = reactive({ should_submit: 0, submitted: 0, not_submitted: [], avg_progress: 0, online_cnt: 0, open_issues: 0, new_issues: 0, workload_total: 0, reports: [] })
const loading = ref(false)

onMounted(async () => {
  projects.value = await app.fetchProjects()
  if (projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await load() }
})

async function load() {
  if (!pid.value) return
  setLastProjectId(pid.value)
  loading.value = true
  try {
    const d = await dailyStats(pid.value, date.value)
    Object.assign(data, d)
  } finally { loading.value = false }
}
</script>

<style scoped>
.stat-section { padding: 20px 0; border-top: 1px solid var(--el-border-color); margin-top: 16px; }
.stat-section h2 { font-size: 16px; margin: 0 0 12px; }
@media (max-width: 700px) {
  .stats :deep(.el-col-4) { flex: 0 0 50%; max-width: 50%; margin-bottom: 8px; }
  .stats :deep(.el-col-12) { flex: 0 0 100%; max-width: 100%; }
}
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
.stat { text-align: center; padding: 8px 0; }
.stat .num { font-size: 22px; font-weight: 600; color: #1f2d3d; }
.stat .num.green { color: #67c23a; }
.stat .num.red { color: #f56c6c; }
.stat .lbl { color: #999; font-size: 12px; margin-top: 4px; }
.wl { color: #555; line-height: 2; }
</style>
