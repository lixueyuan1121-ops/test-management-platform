<template>
  <div class="my-reports">
    <el-card>
      <template #header>
        <div class="header">
          <span>我的日报</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:180px" @change="load">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-date-picker v-model="date" type="date" value-format="YYYY-MM-DD" size="small" style="width:150px" @change="load" />
          </div>
        </div>
      </template>
      <el-table :data="tasks" v-loading="loading" size="small" empty-text="该日没有指派给你的任务">
        <el-table-column prop="title" label="任务名称" min-width="150" />
        <el-table-column prop="developer" label="开发" width="100" />
        <el-table-column label="需求" width="80">
          <template #default="{ row }">
            <el-link v-if="row.requirement_url" :href="row.requirement_url" target="_blank" type="primary" :underline="false">需求</el-link>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="已报进度" width="180">
          <template #default="{ row }">
            <el-progress :percentage="row._progress ?? 0" :status="row._progress >= 100 ? 'success' : ''" />
          </template>
        </el-table-column>
        <el-table-column label="日报状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row._reported" type="success" size="small">已提交</el-tag>
            <el-tag v-else type="warning" size="small">未提交</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button link type="primary" @click="openReport(row)">填报</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialog.visible" :title="`填报日报 · ${form.title || ''}`" width="620px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="测试进度">
          <el-slider v-model="form.progress_pct" :max="100" show-input style="padding-right:8px" />
        </el-form-item>
        <el-form-item label="是否上线">
          <el-switch v-model="form.is_online" /> <span class="tip">{{ form.is_online ? '今日已上线' : '未上线' }}</span>
        </el-form-item>
        <el-form-item label="工作量(人时)">
          <el-input-number v-model="form.workload_hours" :min="0" :max="24" :step="0.5" />
        </el-form-item>
        <el-form-item label="今日小结"><el-input v-model="form.summary" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="遗留问题">
          <div v-for="(it, i) in form.issues" :key="i" class="issue-row">
            <el-input v-model="it.title" placeholder="问题标题" style="flex:1" />
            <el-select v-model="it.severity" style="width:90px">
              <el-option label="blocker" value="blocker" />
              <el-option label="major" value="major" />
              <el-option label="minor" value="minor" />
            </el-select>
            <el-button link type="danger" @click="form.issues.splice(i,1)">删</el-button>
          </div>
          <el-button size="small" @click="form.issues.push({ title: '', severity: 'minor', status: 'open' })">+ 添加遗留问题</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">提交日报</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listProjects, listTasks, listReports, upsertReport } from '@/api'

const projects = ref([])
const pid = ref(null)
const date = ref(new Date().toISOString().slice(0, 10))
const tasks = ref([])
const loading = ref(false)
const dialog = reactive({ visible: false, saving: false })
const form = reactive({
  task_id: null, title: '', report_date: '', progress_pct: 0, is_online: false,
  workload_hours: 0, summary: '', issues: [],
})

onMounted(async () => {
  projects.value = await listProjects()
  if (projects.value.length) { pid.value = projects.value[0].id; await load() }
})

async function load() {
  if (!pid.value) return
  loading.value = true
  try {
    const [myTasks, reports] = await Promise.all([
      listTasks({ project_id: pid.value, date: date.value, mine: true }),
      listReports(pid.value, date.value),
    ])
    const repByTask = {}
    reports.forEach((r) => { repByTask[r.task_id] = r })
    tasks.value = myTasks.map((t) => {
      const r = repByTask[t.id]
      return { ...t, _reported: !!r, _progress: r?.progress_pct ?? 0, _report: r }
    })
  } finally { loading.value = false }
}

function openReport(row) {
  form.task_id = row.id
  form.title = row.title
  form.report_date = date.value
  const r = row._report
  form.progress_pct = r?.progress_pct ?? 0
  form.is_online = r?.is_online ?? false
  form.workload_hours = r?.workload_hours ?? 0
  form.summary = r?.summary ?? ''
  form.issues = (r?.issues?.map((x) => ({ title: x.title, severity: x.severity, status: x.status })) || [])
  dialog.visible = true
}

async function submit() {
  if (!form.task_id) return
  dialog.saving = true
  try {
    await upsertReport({
      task_id: form.task_id, report_date: form.report_date,
      progress_pct: form.progress_pct, is_online: form.is_online,
      workload_hours: form.workload_hours, summary: form.summary,
      issues: form.issues.filter((i) => i.title),
    })
    ElMessage.success('日报已提交')
    dialog.visible = false
    await load()
  } finally { dialog.saving = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
.tip { color: #999; font-size: 12px; margin-left: 8px; }
.issue-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
</style>
