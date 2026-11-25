<template>
  <div class="signcheck functional-workspace">
    <WorkspacePage title="签名校验">
      <template #actions>
        <span v-if="svc && !mac" class="agent-state" :class="agentClass" role="status">
          <i class="dot" aria-hidden="true"></i>{{ agentLabel }}
        </span>
        <el-button size="small" :icon="Refresh" title="刷新" aria-label="刷新" :loading="loading" @click="refresh" />
      </template>

      <el-radio-group :model-value="platform" :disabled="uploading" aria-label="校验类型" class="modes"
        @update:model-value="$emit('switch-platform', $event)">
        <el-radio-button value="windows">Windows 签名校验</el-radio-button>
        <el-radio-button value="macos">macOS 静态初筛</el-radio-button>
      </el-radio-group>
      <el-alert v-if="!mac && svc && !svc.enabled" class="notice" type="warning" :closable="false" show-icon
        title="签名校验未启用"
        description="请管理员配置 SIGNCHECK_AGENT_TOKEN 并启动 Windows 校验机。" />
      <el-alert v-else-if="!mac && svc && !svc.agent_online" class="notice" type="warning" :closable="false" show-icon
        title="校验机离线"
        :description="offlineHint" />

      <div class="panels">
        <section class="panel" aria-labelledby="sc-upload-title">
          <h2 id="sc-upload-title">上传文件</h2>
          <el-input v-if="mac" v-model.trim="teamId" class="team" :disabled="busy" maxlength="10"
            placeholder="预期 Team ID（选填）" aria-label="预期 Team ID（选填）" />
          <div class="drop" :class="{ over: dragging, busy }" role="button" tabindex="0"
            :aria-disabled="busy || !canUpload" aria-label="选择或拖入要校验的文件"
            @click="pick" @keydown.enter="pick" @keydown.space.prevent="pick"
            @dragenter.prevent="onDragEnter" @dragover.prevent @dragleave="onDragLeave" @drop.prevent="onDrop">
            <el-icon class="drop-icon"><UploadFilled /></el-icon>
            <p class="drop-title">{{ mac ? "拖入 DMG 或 ZIP，或点击选择" : "拖入文件，或点击选择" }}</p>
            <p class="drop-hint">一次校验 1 个文件，最大 {{ maxLabel }}</p>
            <input ref="inputRef" type="file" :accept="mac ? '.dmg,.zip' : undefined" hidden @change="onPick" />
          </div>
          <div v-if="uploading" class="progress">
            <div class="progress-name">{{ uploadName }}</div>
            <el-progress :percentage="uploadPct" :stroke-width="6" />
            <el-button size="small" @click="cancelUpload">取消上传</el-button>
          </div>
          <p v-if="mac" class="privacy">平台服务器自动检查 DMG 或 ZIP，完成后清理上传文件。APP 或目录请先压成 ZIP。</p>
          <p v-else class="privacy">文件上传到平台后交给 Windows 校验机校验，出结果即从平台和校验机删除，只保留文件名、大小、SHA-256 与校验结果。</p>
        </section>

        <section class="panel" aria-labelledby="sc-result-title">
          <h2 id="sc-result-title">{{ mac ? (current?.check_mode === 'mac-native' ? '历史原生预检结果' : '静态初筛结果') : '签名结果' }}</h2>
          <div class="verdict" :class="`is-${banner.tone}`" role="status" aria-live="polite">
            <el-icon v-if="banner.tone === 'running'" class="verdict-icon is-loading"><Loading /></el-icon>
            <span v-else class="verdict-icon" aria-hidden="true">{{ banner.icon }}</span>
            <div class="verdict-text">
              <strong>{{ banner.title }}</strong>
              <p>{{ banner.desc }}</p>
            </div>
          </div>
          <div v-if="current" class="file-meta">
            <span class="file-name" :title="current.filename">{{ current.filename }}</span>
            <span>{{ fmtSize(current.file_size) }}</span>
            <button v-if="current.sha256" class="sha" type="button" :title="`点击复制 SHA-256：${current.sha256}`" @click="copy(current.sha256)">SHA-256 {{ current.sha256.slice(0, 12) }}…</button>
          </div>
          <MacNotaryResult v-if="mac" :current="current" :log="rawText" />
          <table v-else class="sig-table" aria-label="签名校验结果">
            <thead><tr><th scope="col">签名类型</th><th scope="col">校验状态</th></tr></thead>
            <tbody>
              <tr v-for="row in sigRows" :key="row.kind">
                <th scope="row">{{ row.label }}<small>{{ row.kind }}</small></th>
                <td>
                  <span class="badge" :class="`badge-${row.tone}`">{{ row.text }}</span>
                  <small v-if="row.detail" class="sig-detail">{{ row.detail }}</small>
                </td>
              </tr>
            </tbody>
          </table>
          <details v-if="rawText" class="raw">
            <summary>查看原始输出</summary>
            <pre tabindex="0">{{ rawText }}</pre>
          </details>
        </section>
      </div>

      <section class="history" aria-labelledby="sc-history-title">
        <div class="history-head">
          <h2 id="sc-history-title">校验记录</h2>
          <div class="history-filters">
            <el-checkbox v-model="mine" @change="reload">只看我的</el-checkbox>
            <el-select v-model="verdictFilter" placeholder="全部结论" clearable size="small" style="width:130px" @change="reload">
              <el-option v-for="(v, k) in VERDICTS" :key="k" :label="v.title" :value="k" />
            </el-select>
            <el-input v-model="keyword" clearable size="small" placeholder="文件名或 SHA-256 前缀" aria-label="搜索校验记录" style="width:220px" @keyup.enter="reload" @clear="reload" />
          </div>
        </div>
        <el-table :data="rows" v-loading="loading" size="small" empty-text="暂无校验记录" @row-click="open">
          <el-table-column prop="id" label="#" width="64" />
          <el-table-column label="文件名" min-width="220" show-overflow-tooltip>
            <template #default="{ row }"><button class="row-link" type="button" @click.stop="open(row)">{{ row.filename }}</button></template>
          </el-table-column>
          <el-table-column label="大小" width="96"><template #default="{ row }">{{ fmtSize(row.file_size) }}</template></el-table-column>
          <el-table-column v-if="mac" label="检查方式" width="100">
            <template #default="{ row }">{{ row.check_mode === 'mac-native' ? 'Mac 原生' : '静态初筛' }}</template>
          </el-table-column>
          <el-table-column label="结论" width="130">
            <template #default="{ row }"><span class="badge" :class="`badge-${rowTone(row)}`">{{ rowText(row) }}</span></template>
          </el-table-column>
          <el-table-column v-for="k in (mac ? [] : KINDS)" :key="k.kind" :label="k.short" width="104">
            <template #default="{ row }"><span class="cell-state" :class="`tone-${sigOf(row, k.kind).tone}`">{{ sigOf(row, k.kind).text }}</span></template>
          </el-table-column>
          <el-table-column label="提交人" width="100" show-overflow-tooltip>
            <template #default="{ row }">{{ row.created_by_name || (row.source === 'api' ? '接口' : '—') }}</template>
          </el-table-column>
          <el-table-column label="时间" min-width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
          <el-table-column label="操作" width="72">
            <template #default="{ row }">
              <el-button v-if="canDelete(row)" link type="danger" @click.stop="onDel(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[20, 50, 100]"
            layout="total, sizes, prev, pager, next" background @current-change="load" @size-change="reload" />
        </div>
      </section>
    </WorkspacePage>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, Refresh, UploadFilled } from '@element-plus/icons-vue'
import WorkspacePage from '@/components/WorkspacePage.vue'
import { useAuthStore } from '@/store/auth'
import { deleteSignCheck, getSignCheck, listSignChecks, signCheckStatus, uploadSignCheck } from '@/api'

import MacNotaryResult from '@/components/MacNotaryResult.vue'

const props = defineProps({ platform: { type: String, required: true } })
defineEmits(['switch-platform'])
const mac = props.platform === 'macos'
const teamId = ref('')
const KINDS = [
  { kind: 'EmbeddedSign', label: '微软数字签名', short: '微软签名' },
  { kind: 'Old360Sign', label: '360 旧签名', short: '360 旧签名' },
  { kind: 'New360Sign', label: '360 超级签名', short: '360 超级' },
  { kind: 'S3360Sign', label: '360 第三代签名（S3）', short: '360 S3' },
]
// 单项状态 → [文案, 色调]
const STATES = {
  valid: ['签名有效', 'ok'], unsigned: ['无签名', 'muted'], invalid: ['签名无效', 'bad'], expired: ['签名过期', 'warn'],
  unsupported: ['状态不支持', 'warn'], error: ['校验出错', 'bad'], missing: ['无结果', 'idle'],
}
// 总体结论 → 标题 / 图标 / 色调 / 缺省说明
const VERDICTS = mac ? {
  no_findings: { title: '初筛未发现问题', icon: '✓', tone: 'ok', desc: '已完成本次静态检查。' },
  passed: { title: '原生预检通过', icon: '✓', tone: 'ok', desc: '' },
  invalid: { title: '发现签名问题', icon: '×', tone: 'bad', desc: '请查看文件明细。' },
  partial: { title: '检查不完整', icon: '!', tone: 'warn', desc: '请查看扫描数量和未覆盖内容。' },
  error: { title: '检查执行异常', icon: '×', tone: 'bad', desc: '请查看执行日志。' },
} : {
  passed: { title: '验签通过', icon: '✓', tone: 'ok', desc: '' },
  invalid: { title: '签名无效', icon: '×', tone: 'bad', desc: '' },
  expired: { title: '签名已过期', icon: '!', tone: 'warn', desc: '' },
  rejected: { title: '验签不通过', icon: '×', tone: 'bad', desc: '' },
  partial: { title: '部分结果', icon: '!', tone: 'warn', desc: '微软数字签名或 360 S3 签名未得到明确状态，请查看各项状态和原始输出。' },
  error: { title: '校验失败', icon: '×', tone: 'bad', desc: '校验程序未能返回结果。' },
}
const FINAL = ['completed', 'failed']
// 验签通过的硬性要求：这两项必须同时有效（与后端 services/signcheck.py 的 REQUIRED_KINDS 一致）
const REQUIRED = ['EmbeddedSign', 'S3360Sign']
const auth = useAuthStore()
const svc = ref(null)
const offlineHint = computed(() => '当前没有 Windows 校验机在线。' + `可先上传排队，超过 ${svc.value?.pending_minutes || 30} 分钟未认领会结束任务。`)
const current = ref(null)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const mine = ref(false)
const verdictFilter = ref('')
const keyword = ref('')
const loading = ref(false)
const uploading = ref(false)
const uploadPct = ref(0)
const uploadName = ref('')
const dragging = ref(false)
const inputRef = ref(null)
let dragDepth = 0
let abort = null
let pollTimer = null
let statusTimer = null
let alive = true

const canUpload = computed(() => !!svc.value?.enabled)
const waiting = computed(() => !!current.value && !FINAL.includes(current.value.status))
const busy = computed(() => uploading.value || waiting.value)
const maxBytes = computed(() => (svc.value?.max_mb || 2048) * 1024 * 1024)
const maxLabel = computed(() => fmtSize(maxBytes.value))
const agentClass = computed(() => !svc.value.enabled ? 'off' : svc.value.agent_online ? 'on' : 'off')
const agentLabel = computed(() => {
  if (!svc.value.enabled) return '未启用'
  if (!svc.value.agent_online) return '校验机离线'
  return svc.value.queued ? `校验机在线 · 排队 ${svc.value.queued}` : '校验机在线'
})

const banner = computed(() => {
  if (uploading.value) return { tone: 'running', title: '正在上传', desc: `${uploadName.value}（${uploadPct.value}%）` }
  const c = current.value
  if (!c) return { tone: 'idle', icon: '·', title: '等待校验', desc: '选择文件后自动开始。' }
  if (c.check_mode === 'server-static' && !FINAL.includes(c.status)) return {
    tone: 'running', title: c.status === 'pending' ? '等待初筛' : '正在初筛', desc: '平台服务器将读取安装包并检查签名元数据。',
  }
  if (c.status === 'pending') return { tone: 'running', title: '排队中', desc: c.agent_online === false ? (mac ? '等待 Mac 校验机上线…' : '等待 Windows 校验机上线…') : '等待校验机认领…' }
  if (c.status === 'running') return { tone: 'running', title: '正在校验', desc: `校验机 ${c.agent || ''} 正在读取文件并检查签名…` }
  const v = VERDICTS[c.verdict] || VERDICTS.error
  return { tone: v.tone, icon: v.icon, title: v.title, desc: c.error || (!mac && countsOf(c)) || v.desc }
})
const sigRows = computed(() => KINDS.map(k => {
  if (uploading.value || waiting.value) return { ...k, text: uploading.value ? '待校验' : '校验中', tone: uploading.value ? 'idle' : 'running', detail: '' }
  if (!current.value) return { ...k, text: '待校验', tone: 'idle', detail: '' }
  const s = sigOf(current.value, k.kind)
  return { ...k, text: s.text, tone: s.tone, detail: s.state === 'error' ? s.detail : '' }
}))
const rawText = computed(() => {
  const c = current.value
  if (!c || !FINAL.includes(c.status) || (c.exit_code == null && !c.stdout && !c.stderr && !(mac && c.error))) return ''
  // Windows 的 NTSTATUS 退出码（如 0xC0000135）按十六进制展示，便于搜索
  const code = c.exit_code == null ? '未知' : (c.exit_code < 0 || c.exit_code > 0x7fffffff) ? `0x${(c.exit_code >>> 0).toString(16).padStart(8, '0')}` : c.exit_code
  const parts = [`进程退出码 ${code}`]
  if (mac && c.error) parts.push(c.error)
  if (c.stdout) parts.push(c.stdout.trimEnd())
  if (c.stderr) parts.push(`[stderr]\n${c.stderr.trimEnd()}`)
  return parts.join('\n\n').replace(/\r+\n/g, '\n')
})

function sigOf(row, kind) {
  const s = (row.signatures || []).find(x => x.kind === kind)
  if (!s) return { state: '', text: FINAL.includes(row.status) ? '无结果' : '—', tone: 'idle', detail: '' }
  const [text, tone] = STATES[s.state] || STATES.missing
  return { state: s.state, text, tone, detail: s.detail || '' }
}
function countsOf(c) {
  const n = { valid: 0, invalid: 0, expired: 0, unsigned: 0 }
  for (const s of c.signatures || []) if (s.state in n) n[s.state] += 1
  const known = n.valid + n.invalid + n.expired + n.unsigned
  if (c.verdict === 'rejected') {
    const lack = KINDS.filter(k => REQUIRED.includes(k.kind) && sigOf(c, k.kind).state !== 'valid').map(k => k.label)
    return `微软数字签名和 360 第三代签名（S3）须同时有效；${lack.join('、')}无签名。`
  }
  return [n.valid && `${n.valid} 项有效`, n.invalid && `${n.invalid} 项无效`, n.expired && `${n.expired} 项过期`,
    n.unsigned && `${n.unsigned} 项无签名`, known < KINDS.length && `${KINDS.length - known} 项待确认`].filter(Boolean).join('，')
}
function rowTone(row) {
  if (!FINAL.includes(row.status)) return 'running'
  return (VERDICTS[row.verdict] || VERDICTS.error).tone
}
function rowText(row) {
  if (row.status === 'pending') return '排队中'
  if (row.status === 'running') return '校验中'
  return (VERDICTS[row.verdict] || VERDICTS.error).title
}
function fmtSize(bytes) {
  if (bytes == null) return '—'
  for (const [size, unit] of [[1024 ** 3, 'GB'], [1024 ** 2, 'MB'], [1024, 'KB']]) {
    if (bytes >= size) return `${(bytes / size).toFixed(bytes / size >= 100 ? 0 : 1).replace(/\.0$/, '')} ${unit}`
  }
  return `${bytes} B`
}
function fmtTime(iso) { if (!iso) return '—'; const d = new Date(iso); return isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false }) }
function canDelete(row) { return auth.isPlatformAdmin || row.created_by === auth.user?.id }
function copy(text) {
  navigator.clipboard?.writeText(text).then(() => ElMessage.success('已复制 SHA-256')).catch(() => ElMessage.warning('复制失败，请手动选择'))
}

async function loadStatus() { try { svc.value = await signCheckStatus(props.platform) } catch { /* 拦截器已提示 */ } }
async function load() {
  loading.value = true
  try {
    const params = { platform: props.platform, page: page.value, page_size: pageSize.value }
    if (mine.value) params.mine = true
    if (verdictFilter.value) params.verdict = verdictFilter.value
    if (keyword.value.trim()) params.keyword = keyword.value.trim()
    const data = await listSignChecks(params)
    rows.value = data.items
    total.value = data.total
  } finally { loading.value = false }
}
function reload() { page.value = 1; return load() }
function refresh() { loadStatus(); load() }

function stopPoll() { clearTimeout(pollTimer); pollTimer = null }
function poll(id) {
  stopPoll()
  pollTimer = setTimeout(async () => {
    if (!alive || current.value?.id !== id) return
    try {
      const fresh = await getSignCheck(id, { silent: true })
      if (!alive || current.value?.id !== id) return
      current.value = fresh
      if (FINAL.includes(fresh.status)) { loadStatus(); load(); return }
    } catch (e) {
      if (e?.response?.status === 404) { current.value = null; return }   // 记录被删了
    }
    if (alive && current.value?.id === id) poll(id)
  }, 1500)
}

function pick() { if (!busy.value && guard()) inputRef.value?.click() }
function guard() {
  if (!canUpload.value) { ElMessage.warning('签名校验未启用，请联系管理员配置'); return false }
  return true
}
function onPick(e) { const files = [...e.target.files]; e.target.value = ''; accept(files) }
function onDragEnter(e) { if (e.dataTransfer?.types?.includes('Files')) { dragDepth += 1; dragging.value = !busy.value } }
function onDragLeave() { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) dragging.value = false }
function onDrop(e) { dragDepth = 0; dragging.value = false; accept([...(e.dataTransfer?.files || [])]) }
function accept(files) {
  if (busy.value || !files.length || !guard()) return
  if (files.length > 1) { ElMessage.warning('请一次选择一个文件'); return }
  const file = files[0]
  if (mac && !/\.(dmg|zip)$/i.test(file.name)) { ElMessage.warning('请选择 DMG 或 ZIP 安装包'); return }
  if (mac && teamId.value && !/^[A-Z0-9]{10}$/.test(teamId.value)) { ElMessage.warning('Team ID 应为 10 位大写字母或数字'); return }
  if (!file.size) { ElMessage.warning('文件内容为空（如果拖入的是文件夹，请改为选择其中的文件）'); return }
  if (file.size > maxBytes.value) { ElMessage.warning(`单个文件不能超过 ${maxLabel.value}`); return }
  upload(file)
}
async function upload(file) {
  stopPoll()
  current.value = null
  uploading.value = true
  uploadPct.value = 0
  uploadName.value = file.name
  abort = new AbortController()
  try {
    const row = await uploadSignCheck(file, {
      signal: abort.signal, platform: props.platform, teamId: mac ? teamId.value : undefined,
      onProgress: (e) => { if (e.total) uploadPct.value = Math.min(100, Math.round(e.loaded / e.total * 100)) },
    })
    if (!alive) return
    current.value = { ...row, agent_online: svc.value?.agent_online }
    load()
    poll(row.id)
  } catch (e) {
    if (e?.code !== 'ERR_CANCELED') loadStatus()   // 失败提示由拦截器给出；顺手刷新状态（可能是刚被关闭/队列满）
  } finally { uploading.value = false; abort = null }
}
function cancelUpload() { abort?.abort() }

async function open(row) {
  if (uploading.value) return
  stopPoll()
  current.value = await getSignCheck(row.id)
  if (!FINAL.includes(current.value.status)) poll(row.id)
  document.querySelector('.signcheck .panels')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}
async function onDel(row) {
  try { await ElMessageBox.confirm(`删除校验记录 #${row.id}（${row.filename}）？`, '确认', { type: 'warning' }) } catch { return }
  await deleteSignCheck(row.id)
  if (current.value?.id === row.id) { stopPoll(); current.value = null }
  ElMessage.success('已删除')
  load(); loadStatus()
}

// 拖到页面其它位置时不让浏览器直接打开/下载该文件
function swallow(e) { if (e.dataTransfer?.types?.includes('Files')) e.preventDefault() }
onMounted(() => {
  refresh()
  statusTimer = setInterval(loadStatus, 10000)
  document.addEventListener('dragover', swallow)
  document.addEventListener('drop', swallow)
})
onBeforeUnmount(() => {
  alive = false
  stopPoll()
  clearInterval(statusTimer)
  abort?.abort()
  document.removeEventListener('dragover', swallow)
  document.removeEventListener('drop', swallow)
})
</script>

<style scoped>
.modes, .team { margin-bottom: 12px; }
.notice { margin-bottom: 12px; }
.agent-state { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--tech-muted); white-space: nowrap; }
.agent-state .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--tech-dim); }
.agent-state.on { color: var(--tech-success); }
.agent-state.on .dot { background: var(--tech-success); }
.agent-state.off { color: var(--tech-warn); }
.agent-state.off .dot { background: var(--tech-warn); }

.panels { display: grid; grid-template-columns: minmax(280px, 5fr) minmax(320px, 7fr); gap: 12px; align-items: start; }
.panel, .history { background: var(--tech-panel); border: 1px solid var(--tech-line); border-radius: 8px; padding: 16px; min-width: 0; }
.panel h2, .history h2 { margin: 0 0 12px; font-size: 14px; font-weight: 600; color: var(--tech-fg); }

.drop { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; min-height: 188px; padding: 20px 12px;
  border: 1px dashed var(--tech-line-strong); border-radius: 8px; background: var(--tech-panel-2); cursor: pointer; text-align: center; transition: border-color .15s, background .15s; }
.drop:hover, .drop:focus-visible, .drop.over { border-color: var(--tech-signal); background: var(--tech-signal-weak); outline: none; }
.drop.busy, .drop[aria-disabled="true"] { cursor: not-allowed; opacity: .6; }
.drop-icon { font-size: 40px; color: var(--tech-signal); }
.drop-title { margin: 6px 0 0; font-size: 14px; color: var(--tech-fg); }
.drop-hint { margin: 0; font-size: 12px; color: var(--tech-muted); }
.progress { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; align-items: flex-start; }
.progress .el-progress { width: 100%; }
.progress-name { font-size: 13px; color: var(--tech-fg); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.privacy { margin: 12px 0 0; font-size: 12px; line-height: 1.6; color: var(--tech-muted); }

.verdict { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border-radius: 8px; background: var(--tech-panel-2); border: 1px solid var(--tech-line); }
.verdict-icon { flex: none; width: 32px; height: 32px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 700; color: #fff; background: var(--tech-dim); }
.verdict-text { min-width: 0; }
.verdict-text strong { font-size: 15px; color: var(--tech-fg); }
.verdict-text p { margin: 2px 0 0; font-size: 12px; line-height: 1.5; color: var(--tech-muted); overflow-wrap: anywhere; }
.verdict.is-ok { background: var(--el-color-success-light-9); border-color: var(--el-color-success-light-7); }
.verdict.is-ok .verdict-icon { background: var(--tech-success); }
.verdict.is-bad { background: var(--el-color-danger-light-9); border-color: var(--el-color-danger-light-7); }
.verdict.is-bad .verdict-icon { background: var(--tech-danger); }
.verdict.is-warn { background: var(--el-color-warning-light-9); border-color: var(--el-color-warning-light-7); }
.verdict.is-warn .verdict-icon { background: var(--tech-warn); }
.verdict.is-running { background: var(--tech-signal-weak); border-color: var(--tech-signal-line); }
.verdict.is-running .verdict-icon { background: none; color: var(--tech-signal); font-size: 24px; }

.file-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; margin-top: 10px; font-size: 12px; color: var(--tech-muted); }
.file-name { color: var(--tech-fg); font-weight: 600; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sha { border: 0; padding: 0; background: none; cursor: pointer; color: var(--tech-muted); font: 12px var(--tech-mono); }
.sha:hover { color: var(--tech-signal); }

.sig-table { width: 100%; margin-top: 10px; border-collapse: collapse; font-size: 13px; }
.sig-table th, .sig-table td { padding: 9px 8px; text-align: left; border-bottom: 1px solid var(--tech-line); vertical-align: middle; }
.sig-table thead th { font-size: 12px; font-weight: 500; color: var(--tech-muted); }
.sig-table tbody th { font-weight: 500; color: var(--tech-fg); }
.sig-table tbody th small { display: block; margin-top: 1px; font: 11px var(--tech-mono); color: var(--tech-dim); }
.sig-detail { display: block; margin-top: 3px; font-size: 12px; color: var(--tech-danger); overflow-wrap: anywhere; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; line-height: 18px; white-space: nowrap; color: var(--tech-muted); background: var(--tech-panel-2); }
.badge-ok { color: var(--el-color-success-dark-2); background: var(--el-color-success-light-9); }
.badge-bad { color: var(--el-color-danger-dark-2); background: var(--el-color-danger-light-9); }
.badge-warn { color: var(--el-color-warning-dark-2); background: var(--el-color-warning-light-9); }
.badge-running { color: var(--el-color-primary-dark-2); background: var(--tech-signal-weak); }
.badge-muted { color: #414651; background: #eceef2; }
.cell-state { font-size: 12px; color: var(--tech-muted); }
.cell-state.tone-ok { color: var(--el-color-success-dark-2); }
.cell-state.tone-bad { color: var(--el-color-danger-dark-2); }
.cell-state.tone-warn { color: var(--el-color-warning-dark-2); }
.cell-state.tone-idle { color: var(--tech-dim); }

.raw { margin-top: 10px; }
.raw summary { cursor: pointer; font-size: 13px; color: var(--tech-signal); padding: 4px 0; }
.raw pre { max-height: 260px; margin: 6px 0 0; padding: 12px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; border-radius: 6px; color: #e6e9ef; background: #202329; font: 12px/1.65 var(--tech-mono); }

.history { margin-top: 12px; }
.history-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; margin-bottom: 8px; }
.history-head h2 { margin: 0; }
.history-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
.history-filters .el-input, .history-filters .el-select { max-width: 100%; }
.history :deep(.el-table__row) { cursor: pointer; }
.row-link { border: 0; padding: 0; background: none; cursor: pointer; color: var(--tech-signal); font: inherit; text-align: left; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-link:hover { text-decoration: underline; }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; overflow-x: auto; }

@media (max-width: 900px) { .panels { grid-template-columns: 1fr; } }
</style>
