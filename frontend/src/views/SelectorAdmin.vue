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

    <!-- 设备探测面板：选在线设备，扫当前页元素产候选 → 加为 key(新建/更新已有)；或校验现有 key 是否失效 -->
    <el-card class="probe-card">
      <template #header>
        <div class="header">
          <span>设备探测</span>
          <div class="filters">
            <el-select
              v-model="probe.runner" placeholder="选择在线设备" size="small" style="width:200px"
              no-data-text="你还没有登记设备（去「我的设备」登记）"
            >
              <el-option
                v-for="d in devices" :key="d.runner_id" :value="d.runner_id"
                :label="`${d.name || d.runner_id}（${d.runner_id}）`"
              />
            </el-select>
            <el-input
              v-model="probe.contains" placeholder="关键词过滤(可选，按文本 contains)" size="small"
              clearable style="width:220px"
            />
            <el-button
              type="primary" size="small" :loading="probe.running && probe.mode === 'discover'"
              :disabled="!pid || !probe.runner || probe.running" @click="onDiscover"
            >探测(扫当前页)</el-button>
            <el-button
              size="small" :loading="probe.running && probe.mode === 'verify'"
              :disabled="!pid || !probe.runner || probe.running" @click="onVerify"
            >校验失效 key</el-button>
          </div>
        </div>
      </template>

      <!-- 目标提示：当前落库作用域 + 若处于「更新已有」预置目标 -->
      <div class="probe-scope">
        <span>探测结果将落到当前作用域：<b>{{ subProduct || '项目级共享' }}</b></span>
        <el-tag v-if="probe.updateTarget" type="warning" size="small" closable @close="probe.updateTarget = ''">
          「加为 key」默认更新已有：{{ probe.updateTarget }}
        </el-tag>
      </div>

      <el-empty v-if="!probe.done && !probe.running" description="选择在线设备后点「探测」，会扫描该设备当前页面的可交互元素" :image-size="80" />
      <div v-else-if="probe.running" class="probe-loading" v-loading="true" element-loading-text="探测中，请在设备上停留在目标页面…" style="min-height:120px" />

      <!-- discover 结果：按 shell/vm 分组，列元素文本 + best 候选，逐个「加为 key」 -->
      <template v-else-if="probe.mode === 'discover' && probe.result">
        <div v-if="!(probe.result.groups || []).length" class="form-hint">未扫到元素（页面可能未加载或无可交互元素）</div>
        <div v-for="g in (probe.result.groups || [])" :key="g.frame" class="probe-group">
          <div class="probe-group-head">
            <el-tag size="small" :type="g.frame === 'vm' ? 'success' : 'info'">{{ g.frame }}</el-tag>
            <span class="probe-group-url" :title="g.url">{{ g.url || '' }}</span>
            <span class="form-hint">共 {{ g.total ?? (g.elements || []).length }} 个{{ g.error ? `（错误：${g.error}）` : '' }}</span>
          </div>
          <el-table :data="g.elements || []" size="small" border empty-text="该 frame 无元素">
            <el-table-column label="元素" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tag size="small" type="info" effect="plain">{{ row.tag }}{{ row.type ? `[${row.type}]` : '' }}</el-tag>
                <span class="probe-el-text">{{ row.text || '（无文本）' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="best 候选" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <code v-if="row.best">{{ row.best.by }}={{ row.best.value }}</code>
                <span v-else class="form-hint">—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100" align="center">
              <template #default="{ row }">
                <el-button link type="primary" size="small" :disabled="!row.best || !row.candidates?.length" @click="openAddAsKey(row, g.frame)">加为 key</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>

      <!-- verify 结果：命中/失效逐 key 展示，失效标红并给「重新探测更新」 -->
      <template v-else-if="probe.mode === 'verify' && probe.result">
        <el-table :data="verifyRows" size="small" border empty-text="当前作用域没有已登记的 key">
          <el-table-column prop="key" label="key" min-width="200" show-overflow-tooltip />
          <el-table-column label="状态" width="120" align="center">
            <template #default="{ row }">
              <el-tag :type="row.ok ? 'success' : 'danger'" size="small">{{ row.ok ? '命中' : '失效' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" align="center">
            <template #default="{ row }">
              <el-button v-if="!row.ok" link type="warning" size="small" @click="reprobeForKey(row.key)">重新探测更新</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
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
          <el-input
            v-model="dialog.candidatesText" type="textarea" :rows="6"
            placeholder='JSON 对象数组，如 [{"by":"text","value":"登录"},{"by":"css","value":".login-btn"}]'
          />
          <div class="form-hint">候选定位器数组（JSON），每项 {"by":"text|css|...","value":"..."}，按顺序尝试。空数组 [] 亦可</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 加为 key：探测元素 → 落库为选择器（新建 / 更新已有 key 追加候选到头部）-->
    <el-dialog v-model="add.visible" title="加为 key" width="560px">
      <div class="add-preview">
        <div class="form-hint">来源元素（{{ add.frame }} frame）</div>
        <div><el-tag size="small" type="info" effect="plain">{{ add.tag }}{{ add.type ? `[${add.type}]` : '' }}</el-tag> <span class="probe-el-text">{{ add.text || '（无文本）' }}</span></div>
        <div class="add-cand">best 候选：<code>{{ add.cand ? `${add.cand.by}=${add.cand.value}` : '—' }}</code></div>
      </div>
      <el-form label-width="90px" style="margin-top:12px">
        <el-form-item label="模式">
          <el-radio-group v-model="add.mode">
            <el-radio value="create">新建 key</el-radio>
            <el-radio value="update" :disabled="!rows.length">更新已有 key</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="add.mode === 'create'" label="key 名" required>
          <el-input v-model="add.key" placeholder="语义 key，如 login_button" />
        </el-form-item>
        <el-form-item v-else label="目标 key" required>
          <el-select v-model="add.targetId" placeholder="选择当前作用域的已有 key" filterable style="width:100%">
            <el-option v-for="r in rows" :key="r.id" :value="r.id" :label="`${r.key}（${(r.candidates || []).length} 候选）`" />
          </el-select>
          <div class="form-hint">best 候选将追加到该 key 候选列表的<b>头部</b>（优先尝试）</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="add.visible = false">取消</el-button>
        <el-button type="primary" :loading="add.saving" @click="submitAddAsKey">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { useAppStore } from '@/store/app'
import {
  listSelectors, createSelector, patchSelector, deleteSelector, importLegacySelectors,
  listMyDevices, startProbe, getProbe,
} from '@/api'
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
const devices = ref([])   // 我的在线设备（探测目标）

// 导入旧注册表仅项目 admin（后端 import-legacy 要求 admin）；平台管理员在任何项目都视为 admin。
const canImport = computed(() => !!pid.value && auth.roleIn(pid.value) === 'admin')

onMounted(async () => {
  try { projects.value = await app.fetchProjects() } catch { projects.value = [] }
  try { devices.value = await listMyDevices() } catch { devices.value = [] }
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await reload()
  }
})

onUnmounted(stopPoll)   // 离开页面时清轮询定时器，避免泄漏

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

// ---- 设备探测（discover / verify）----
// probe.result 存最近一次探测结果；mode 记录当前展示的是 discover 还是 verify。
// updateTarget：verify 里点「重新探测更新」预置的目标 key，下次「加为 key」默认选它（更新已有）。
const probe = reactive({
  runner: '', contains: '', mode: 'discover',
  running: false, done: false, result: null, updateTarget: '',
})
let pollTimer = null

function stopPoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

// 发起一次探测并轮询到 done/failed；60s 超时。extraParams 合并进 params（如 { mode:'verify' }）。
async function runProbe(mode, extraParams = {}) {
  if (!pid.value || !probe.runner) { ElMessage.warning('请先选择项目和在线设备'); return }
  stopPoll()
  probe.mode = mode
  probe.running = true
  probe.done = false
  probe.result = null
  let id
  try {
    const params = { contains: probe.contains || '', ...extraParams }
    const res = await startProbe({ project_id: pid.value, sub_product: subProduct.value, runner: probe.runner, params })
    id = res?.id
  } catch { probe.running = false; return /* http 拦截器已提示 */ }
  if (!id) { probe.running = false; ElMessage.error('发起探测失败'); return }

  const startedAt = Date.now()
  pollTimer = setInterval(async () => {
    let r
    try { r = await getProbe(id) } catch { return /* 单次轮询失败忽略，等下次 */ }
    if (r.status === 'done') {
      stopPoll()
      probe.running = false; probe.done = true; probe.result = r.result || {}
    } else if (r.status === 'failed') {
      stopPoll()
      probe.running = false; probe.done = true; probe.result = null
      ElMessage.error(`探测失败：${r.error || '设备未响应（请确认已开被测客户端且 CDP 端口可达）'}`)
    } else if (Date.now() - startedAt > 60000) {
      stopPoll()
      probe.running = false
      ElMessage.error('探测超时（60s）：请确认设备 runner 在线且停留在目标页面')
    }
  }, 1500)
}

function onDiscover() { probe.updateTarget = ''; runProbe('discover') }
function onVerify() { runProbe('verify', { mode: 'verify' }) }

// verify 结果 {key:bool} → 表格行；失效(false)排前面便于处理。
const verifyRows = computed(() => {
  const v = probe.result?.verify || {}
  return Object.entries(v)
    .map(([key, ok]) => ({ key, ok: !!ok }))
    .sort((a, b) => Number(a.ok) - Number(b.ok))
})

// 「重新探测更新」：切回 discover，并把该失效 key 预置为「加为 key」的更新目标。
function reprobeForKey(key) {
  probe.updateTarget = key
  runProbe('discover')
}

// ---- 加为 key 弹窗（新建 / 更新已有）----
const add = reactive({ visible: false, mode: 'create', tag: '', type: '', text: '', frame: 'auto', cand: null, key: '', targetId: null, saving: false })

// 把探测候选归一成注册表存储的 {by,value}（丢弃 runner 内部的 sel/score）。
function toCand(c) {
  return c ? { by: c.by, value: c.value } : null
}

function openAddAsKey(el, frame) {
  const cand = toCand(el.best)
  // 若有预置更新目标（来自 verify 的「重新探测更新」）且该 key 仍在当前作用域，默认切到更新已有模式。
  const preset = probe.updateTarget && rows.value.find((r) => r.key === probe.updateTarget)
  Object.assign(add, {
    visible: true, saving: false,
    tag: el.tag, type: el.type || '', text: el.text || '', frame: frame || 'auto', cand,
    mode: preset ? 'update' : 'create',
    key: '',
    targetId: preset ? preset.id : null,
  })
}

async function submitAddAsKey() {
  if (!add.cand) { ElMessage.error('该元素没有可用候选'); return }
  if (add.mode === 'create' && !add.key.trim()) { ElMessage.warning('key 名不能为空'); return }
  if (add.mode === 'update' && !add.targetId) { ElMessage.warning('请选择要更新的已有 key'); return }
  add.saving = true
  try {
    if (add.mode === 'create') {
      await createSelector({
        project_id: pid.value, sub_product: subProduct.value, key: add.key.trim(),
        frame: add.frame || 'auto', desc: '', candidates: [add.cand],
      })
      ElMessage.success('已新建 key')
    } else {
      const target = rows.value.find((r) => r.id === add.targetId)
      const existing = target?.candidates || []
      // best 候选追加到头部（优先尝试）；去掉与新候选完全相同的旧项，避免重复。
      const merged = [add.cand, ...existing.filter((c) => !(c.by === add.cand.by && c.value === add.cand.value))]
      await patchSelector(add.targetId, { candidates: merged })
      ElMessage.success('已更新已有 key 的候选')
      if (target && probe.updateTarget === target.key) probe.updateTarget = ''
    }
    add.visible = false
    await reload()
  } catch { /* http 拦截器已提示（如 key 冲突）*/ }
  finally { add.saving = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.form-hint { color: #90a4ae; font-size: 12px; }
.probe-card { margin-top: 16px; }
.probe-scope { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; color: #607d8b; font-size: 13px; }
.probe-group { margin-bottom: 14px; }
.probe-group-head { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.probe-group-url { color: #607d8b; font-size: 12px; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.probe-el-text { margin-left: 6px; }
.add-preview { background: #f5f7fa; border-radius: 4px; padding: 10px 12px; }
.add-cand { margin-top: 6px; }
code { background: #f0f2f5; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
</style>
