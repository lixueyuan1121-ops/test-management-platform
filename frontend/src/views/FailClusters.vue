<template>
  <div class="fc-page">
    <WorkspacePage title="版本质量聚焦">
      <template #actions>
            <el-select v-model="projectId" :disabled="running || issuing !== null" placeholder="项目" size="small" style="width:180px" @change="onProject">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="releaseId" :disabled="releasesLoading || running || issuing !== null" placeholder="版本" size="small" style="width:200px" clearable
                       no-data-text="该项目暂无发版记录" @change="onRelease">
              <el-option v-for="r in releases" :key="r.id" :label="`${r.version}（${r.release_date}）`" :value="r.id" />
            </el-select>
      </template>

      <!-- 需求勾选 -->
      <template #selection><div v-if="releaseId" class="selection-actions">
        <span>已选 {{ checkedReqs.length }} / {{ scopeReqs.length }} 个需求</span>
        <el-button type="primary" size="small" :loading="running" :disabled="loading || !!loadError || issuing !== null || !hasFails" @click="runAnalyze">AI 聚类去噪</el-button>
      </div></template>
      <el-result v-if="loadError" icon="error" :title="loadError"><template #extra><el-button @click="releaseId ? onRelease() : onProject()">重试</el-button></template></el-result>
      <el-skeleton v-else-if="loading || releasesLoading" :rows="4" animated />
      <el-empty v-else-if="!releaseId" description="请选择版本" />
      <div v-else class="scope">
        <div class="scope-h">纳入聚类的需求</div>
        <el-checkbox-group v-model="checkedReqs" :disabled="running">
          <el-checkbox v-for="rq in scopeReqs" :key="rq.id" :value="rq.id">
            {{ rq.title }} <el-tag size="small" :type="rq.fail_count ? 'danger' : 'info'">失败 {{ rq.fail_count }}</el-tag>
          </el-checkbox>
        </el-checkbox-group>
        <el-empty v-if="!scopeReqs.length" description="该版本暂无需求" :image-size="48" />
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
            <span v-for="rid in c.requirement_ids" :key="rid" class="reqtag">{{ reqTitle(rid) }}</span>
          </div>
          <div class="rc-act">
            <el-button v-if="!c.issue_id" size="small" type="danger" plain
                       :disabled="running || issuing !== null" :loading="issuing === c.id" @click="mkIssue(c)">一键建缺陷草稿</el-button>
            <el-tag v-else type="success" size="small">已建缺陷 #{{ c.issue_id }}</el-tag>
          </div>
        </div>
      </div>
      <el-empty v-if="releaseId && !clusters.length && !running && !loading && !loadError" description="暂无聚类结果" />
    </WorkspacePage>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import { ref, computed, onBeforeUnmount } from 'vue'
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
const loading = ref(false); const releasesLoading = ref(false); const loadError = ref('')
let projectVersion = 0; let releaseVersion = 0; let disposed = false; let analysisController

const KIND = { selector: ['选择器', 'info'], environment: ['环境', 'warning'], assertion: ['用例', ''], bug: ['缺陷', 'danger'] }
const kindLabel = (k) => (KIND[k] || ['未知', 'info'])[0]
const kindType = (k) => (KIND[k] || ['', 'info'])[1]
const hasFails = computed(() => scopeReqs.value.some((r) => checkedReqs.value.includes(r.id) && r.fail_count > 0))
const dedupRatio = computed(() => clusters.value.length ? `${(failCount.value / clusters.value.length).toFixed(1)}:1` : '—')
const reqTitle = (rid) => scopeReqs.value.find((r) => r.id === rid)?.title || `#${rid}`

async function init() {
  try {
    projects.value = await app.fetchProjects()
    if (disposed) return
    projectId.value = app.resolveProjectId(projects.value)
    if (projectId.value) await onProject()
  } catch { if (!disposed) loadError.value = '项目加载失败' }
}
async function onProject() {
  if (!projectId.value) return init()
  const version = ++projectVersion
  ++releaseVersion
  releases.value = []; releaseId.value = null; clusters.value = []; scopeReqs.value = []
  checkedReqs.value = []; failCount.value = 0; loadError.value = ''; loading.value = false
  releasesLoading.value = true
  app.setLastProject(projectId.value)   // 记住选择，跨页/下次进页复原
  try {
    const data = await listReleases({ project_id: projectId.value })
    if (version === projectVersion && !disposed) releases.value = data.items || []
  } catch { if (version === projectVersion && !disposed) loadError.value = '版本加载失败' }
  finally { if (version === projectVersion && !disposed) releasesLoading.value = false }
}
async function onRelease() {
  const version = ++releaseVersion, id = releaseId.value
  clusters.value = []; scopeReqs.value = []; checkedReqs.value = []
  failCount.value = 0; loadError.value = ''; loading.value = false
  if (!id) return
  loading.value = true
  try {
    const s = await failClusterScope(id)
    if (version !== releaseVersion || disposed) return
    const d = await listFailClusters(id)
    if (version !== releaseVersion || disposed) return
    scopeReqs.value = s.requirements || []
    checkedReqs.value = scopeReqs.value.map(r => r.id)
    clusters.value = d.items || []; failCount.value = d.fail_count || 0
  } catch { if (version === releaseVersion && !disposed) loadError.value = '需求或聚类结果加载失败' }
  finally { if (version === releaseVersion && !disposed) loading.value = false }
}
async function loadClusters(id) {
  const d = await listFailClusters(id)
  if (disposed || releaseId.value !== id) return
  clusters.value = d.items || []; failCount.value = d.fail_count || 0
}
async function runAnalyze() {
  if (running.value || issuing.value !== null || loading.value || loadError.value || !releaseId.value || !hasFails.value) return
  const id = releaseId.value
  running.value = true
  analysisController = new AbortController()
  try {
    const { job_id } = await analyzeFailClusters({
      project_id: projectId.value, release_id: id, requirement_ids: [...checkedReqs.value],
    })
    if (disposed) return
    await pollAiJob(job_id, { signal: analysisController.signal })
    if (disposed) return
    await loadClusters(id)
    if (disposed) return
    ElMessage.success('聚类完成')
  } catch (e) {
    // pollAiJob 是 silent（不经 http 拦截器弹错）且抛普通 Error → 必须自己提示，否则聚类失败零反馈
    if (!disposed) ElMessage.error('聚类失败：' + (e?.message || '请稍后重试'))
  } finally { running.value = false }
}
async function mkIssue(c) {
  if (issuing.value !== null || running.value || c.issue_id) return
  issuing.value = c.id
  try {
    const r = await createIssueFromCluster(c.id)
    if (disposed) return
    c.issue_id = r.issue_id
    ElMessage.success(r.already ? '该根因已建过缺陷' : `已建缺陷草稿 #${r.issue_id}`)
  } catch (e) { /* 拦截器已提示 */ } finally { issuing.value = null }
}
onBeforeUnmount(() => { disposed = true; ++projectVersion; ++releaseVersion; analysisController?.abort() })
init()
</script>

<style scoped>
.fc-page { padding: 4px; }
.scope { margin: 8px 0 16px; padding: 12px 0; border-bottom: 1px solid #ebeef5; }
.scope-h { font-size: 13px; color: #606266; margin-bottom: 10px; }
.selection-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; font-size: 13px; }
.scope :deep(.el-checkbox) { display: flex; height: auto; margin: 0 0 12px; align-items: flex-start; }
.scope :deep(.el-checkbox__input) { margin-top: 3px; }
.scope :deep(.el-checkbox__label) { white-space: normal; overflow-wrap: anywhere; min-width: 0; line-height: 1.6; }
.overview { font-size: 14px; margin: 12px 0; color: #303133; }
.overview .big { font-size: 24px; font-weight: 700; color: #303133; font-family: monospace; }
.cards { display: flex; flex-direction: column; gap: 10px; }
.rc-card { border: 1px solid #e3e8ef; border-radius: 8px; padding: 14px 16px; }
.rc-top { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.rc-title { font-weight: 600; font-size: 15px; overflow-wrap: anywhere; min-width: 0; }
.rc-sum { color: #606266; font-size: 13px; margin: 8px 0; }
.rc-reqs { font-size: 12px; color: #909399; }
.reqtag { display: block; margin: 4px 0; color: #606266; line-height: 1.6; }
.rc-act { margin-top: 10px; }
.rc-sum { overflow-wrap: anywhere; }
.reqtag { white-space: normal; overflow-wrap: anywhere; max-width: 100%; }
</style>
