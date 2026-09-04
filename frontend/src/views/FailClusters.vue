<template>
  <div class="fc-page">
    <el-card>
      <template #header>
        <div class="hd">
          <span>版本质量聚焦 · 失败根因去噪</span>
          <div class="hd-r">
            <el-select v-model="projectId" placeholder="项目" size="small" style="width:180px" @change="onProject">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="releaseId" placeholder="版本" size="small" style="width:200px" clearable
                       no-data-text="该项目暂无发版记录" @change="onRelease">
              <el-option v-for="r in releases" :key="r.id" :label="`${r.version}（${r.release_date}）`" :value="r.id" />
            </el-select>
          </div>
        </div>
      </template>

      <!-- 需求勾选 -->
      <div v-if="releaseId" class="scope">
        <div class="scope-h">纳入聚类的需求（勾选，默认全选，可排除未测完的）：</div>
        <el-checkbox-group v-model="checkedReqs">
          <el-checkbox v-for="rq in scopeReqs" :key="rq.id" :value="rq.id">
            {{ rq.title }} <el-tag size="small" :type="rq.fail_count ? 'danger' : 'info'">失败 {{ rq.fail_count }}</el-tag>
          </el-checkbox>
        </el-checkbox-group>
        <el-button type="primary" size="small" :loading="running" :disabled="!hasFails" @click="runAnalyze">
          AI 聚类去噪
        </el-button>
      </div>

      <!-- 概览 -->
      <div v-if="clusters.length" class="overview">
        <span class="big">{{ failCount }}</span> 条失败 →
        <span class="big">{{ clusters.length }}</span> 个根因（去噪比 {{ dedupRatio }}）
      </div>

      <!-- 根因卡片（按根因聚合） -->
      <div class="cards">
        <div v-for="c in clusters" :key="c.id" class="rc-card">
          <div class="rc-top">
            <el-tag size="small" :type="kindType(c.triage_kind)">{{ kindLabel(c.triage_kind) }}</el-tag>
            <span class="rc-title">{{ c.root_cause_title }}</span>
            <el-tag size="small" effect="plain">影响 {{ c.member_count }} 条</el-tag>
            <el-tag v-if="c.severity" size="small" type="warning">{{ c.severity }}</el-tag>
          </div>
          <div class="rc-sum">{{ c.summary }}</div>
          <div class="rc-reqs">涉及需求：
            <el-tag v-for="rid in c.requirement_ids" :key="rid" size="small" class="reqtag">{{ reqTitle(rid) }}</el-tag>
          </div>
          <div class="rc-act">
            <el-button v-if="!c.issue_id" size="small" type="danger" plain
                       :loading="issuing === c.id" @click="mkIssue(c)">一键建缺陷草稿</el-button>
            <el-tag v-else type="success" size="small">已建缺陷 #{{ c.issue_id }}</el-tag>
          </div>
        </div>
      </div>
      <el-empty v-if="releaseId && !clusters.length && !running" description="暂无聚类结果，点上方「AI 聚类去噪」" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useAppStore } from '@/store/app'
import {
  listReleases, failClusterScope, analyzeFailClusters,
  listFailClusters, createIssueFromCluster, pollAiJob,
} from '@/api'

const app = useAppStore()
const projects = ref([])
const releases = ref([])
const projectId = ref(null)
const releaseId = ref(null)
const scopeReqs = ref([])
const checkedReqs = ref([])
const clusters = ref([])
const failCount = ref(0)
const running = ref(false)
const issuing = ref(null)

const KIND = { selector: ['选择器', 'info'], environment: ['环境', 'warning'], assertion: ['用例', ''], bug: ['缺陷', 'danger'] }
const kindLabel = (k) => (KIND[k] || ['未知', 'info'])[0]
const kindType = (k) => (KIND[k] || ['', 'info'])[1]
const hasFails = computed(() => scopeReqs.value.some((r) => r.fail_count > 0))
const dedupRatio = computed(() => clusters.value.length ? `${(failCount.value / clusters.value.length).toFixed(1)}:1` : '—')
const reqTitle = (rid) => scopeReqs.value.find((r) => r.id === rid)?.title || `#${rid}`

async function init() {
  projects.value = await app.fetchProjects()
  if (projects.value.length) { projectId.value = projects.value[0].id; await onProject() }
}
async function onProject() {
  releases.value = (await listReleases({ project_id: projectId.value })).items || []
  releaseId.value = null; clusters.value = []; scopeReqs.value = []
}
async function onRelease() {
  clusters.value = []; scopeReqs.value = []; checkedReqs.value = []
  if (!releaseId.value) return
  const s = await failClusterScope(releaseId.value)
  scopeReqs.value = s.requirements || []
  checkedReqs.value = scopeReqs.value.map((r) => r.id)   // 默认全选
  await loadClusters()
}
async function loadClusters() {
  const d = await listFailClusters(releaseId.value)
  clusters.value = d.items || []; failCount.value = d.fail_count || 0
}
async function runAnalyze() {
  running.value = true
  try {
    const { job_id } = await analyzeFailClusters({
      project_id: projectId.value, release_id: releaseId.value, requirement_ids: checkedReqs.value,
    })
    await pollAiJob(job_id)
    await loadClusters()
    ElMessage.success('聚类完成')
  } catch (e) { /* 拦截器已提示 */ } finally { running.value = false }
}
async function mkIssue(c) {
  issuing.value = c.id
  try {
    const r = await createIssueFromCluster(c.id)
    c.issue_id = r.issue_id
    ElMessage.success(r.already ? '该根因已建过缺陷' : `已建缺陷草稿 #${r.issue_id}`)
  } catch (e) { /* 拦截器已提示 */ } finally { issuing.value = null }
}
init()
</script>

<style scoped>
.fc-page { padding: 4px; }
.hd { display: flex; justify-content: space-between; align-items: center; }
.hd-r { display: flex; gap: 10px; }
.scope { margin: 8px 0 16px; padding: 12px; background: #f7f9fc; border-radius: 6px; }
.scope-h { font-size: 13px; color: #606266; margin-bottom: 10px; }
.overview { font-size: 14px; margin: 12px 0; color: #303133; }
.overview .big { font-size: 24px; font-weight: 700; color: #00b386; font-family: monospace; }
.cards { display: flex; flex-direction: column; gap: 10px; }
.rc-card { border: 1px solid #e3e8ef; border-radius: 8px; padding: 14px 16px; }
.rc-top { display: flex; align-items: center; gap: 10px; }
.rc-title { font-weight: 600; font-size: 15px; }
.rc-sum { color: #606266; font-size: 13px; margin: 8px 0; }
.rc-reqs { font-size: 12px; color: #909399; }
.reqtag { margin: 0 4px 4px 0; }
.rc-act { margin-top: 10px; }
</style>
