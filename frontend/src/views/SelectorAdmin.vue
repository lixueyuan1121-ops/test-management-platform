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

      <el-empty v-if="!rows.length" :description="loading ? '加载中…' : '该作用域暂无选择器 key'" :image-size="70" />
      <el-collapse v-else v-model="activePages" v-loading="loading">
        <el-collapse-item v-for="grp in groupedRows" :key="grp.name" :name="grp.name">
          <template #title>
            <span class="page-title">{{ grp.pageLabel }}</span>
            <el-tag size="small" type="info" effect="plain" class="page-count">{{ grp.keys.length }}</el-tag>
          </template>
          <el-table :data="grp.keys" size="small" border stripe>
            <el-table-column prop="key" label="key" min-width="180" show-overflow-tooltip />
            <el-table-column prop="frame" label="frame" width="110">
              <template #default="{ row }">{{ row.frame || 'auto' }}</template>
            </el-table-column>
            <el-table-column prop="desc" label="说明" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">{{ row.desc || '—' }}</template>
            </el-table-column>
            <el-table-column label="候选数" width="80" align="center">
              <template #default="{ row }">{{ (row.candidates || []).length }}</template>
            </el-table-column>
            <el-table-column label="更新时间" width="150">
              <template #default="{ row }">{{ fmtTime(row.updated_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="120" align="center">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
                <el-button link type="danger" size="small" @click="onDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
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
            <el-select
              v-model="probe.page" filterable allow-create default-first-option clearable
              placeholder="当前页面(可选，加 key 默认归属)" size="small" style="width:200px"
            >
              <el-option v-for="p in pageOptions" :key="p" :label="p" :value="p" />
            </el-select>
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

      <!-- discover 结果：按 shell/vm/iframe 分组；组内 新增/可更新 排前、已存在垫底，可一键隐藏已存在 -->
      <template v-else-if="probe.mode === 'discover' && probe.result">
        <div v-if="!enrichedGroups.length" class="form-hint">未扫到元素（页面可能未加载或无可交互元素）</div>
        <template v-else>
          <div v-if="probe.screenshotUrl && shotBoxes.length" class="shot-panel">
            <div class="shot-bar">
              <span class="form-hint">页面截图（{{ shotBoxes.length }}/{{ boxTotal }} 个元素已框选{{ approxCount ? `，其中 ${approxCount} 个位置近似（虚线）` : '' }}）</span>
              <div class="shot-zoom">
                <el-button-group size="small">
                  <el-button :disabled="zoom <= 0.25" @click="zoom = Math.max(0.25, +(zoom - 0.25).toFixed(2))">－</el-button>
                  <el-button @click="zoom = 1">适应</el-button>
                  <el-button :disabled="zoom >= 3" @click="zoom = Math.min(3, +(zoom + 0.25).toFixed(2))">＋</el-button>
                </el-button-group>
                <span class="form-hint zoom-val">{{ Math.round(zoom * 100) }}%</span>
              </div>
            </div>
            <div class="shot-viewport">
              <div class="shot-wrap" :style="{ width: (zoom * 100) + '%' }">
                <img :src="probe.screenshotUrl" class="shot-img" alt="页面截图" />
                <div class="shot-overlay">
                  <div
                    v-for="box in shotBoxes" :key="box.uid"
                    class="el-box" :class="['box-' + box.type, { active: hoverKey === box.uid, approx: box.approx }]"
                    :style="box.style" :title="box.label"
                    @mouseenter="hoverKey = box.uid" @mouseleave="hoverKey = ''"
                    @click="openAddAsKey(box.el, box.frameMatch)"
                  ></div>
                </div>
              </div>
            </div>
          </div>
          <div class="probe-toolbar">
            <el-checkbox v-model="probe.hideExists" size="small">隐藏「已存在」（{{ totalCounts.exists }}）</el-checkbox>
            <span class="form-hint">新增 {{ totalCounts.new }} · 可更新 {{ totalCounts.update }} · 已存在 {{ totalCounts.exists }}</span>
          </div>
          <div v-for="(g, gi) in enrichedGroups" :key="gi" class="probe-group">
            <div class="probe-group-head">
              <el-tag size="small" :type="g.frame === 'vm' ? 'success' : 'info'">{{ g.frame }}</el-tag>
              <span class="probe-group-url" :title="g.url">{{ g.url || '' }}</span>
              <span class="form-hint">共 {{ g.total ?? (g.elements || []).length }} 个 · 新增 {{ g.counts.new }}／可更新 {{ g.counts.update }}／已存在 {{ g.counts.exists }}{{ g.error ? `（错误：${g.error}）` : '' }}</span>
            </div>
            <el-table
              :data="g.elements" size="small" border empty-text="该 frame 无待显示元素（或已隐藏「已存在」）"
              :row-class-name="rowClass" @cell-mouse-enter="onCellEnter" @cell-mouse-leave="() => hoverKey = ''"
            >
              <el-table-column label="元素" min-width="180" show-overflow-tooltip>
                <template #default="{ row }">
                  <el-tag size="small" type="info" effect="plain">{{ row.tag }}{{ row.type ? `[${row.type}]` : '' }}</el-tag>
                  <span class="probe-el-text">{{ row.text || '（无文本）' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="best 候选" min-width="200" show-overflow-tooltip>
                <template #default="{ row }">
                  <code v-if="row.best">{{ row.best.by }}={{ row.best.value }}</code>
                  <span v-else class="form-hint">—</span>
                </template>
              </el-table-column>
              <el-table-column label="标识" width="120" align="center">
                <template #default="{ row }">
                  <template v-if="row.best">
                    <el-popover placement="left" :width="340" trigger="hover" :disabled="!row._status.key">
                      <template #reference>
                        <span class="status-cell">
                          <el-tag :type="STATUS_META[row._status.type].tag" size="small" effect="plain">{{ STATUS_META[row._status.type].label }}</el-tag>
                          <div v-if="row._status.key" class="form-hint match-key" :title="row._status.key">{{ row._status.key }}</div>
                        </span>
                      </template>
                      <div class="cand-preview">
                        <div class="cand-preview-title">命中库 key「{{ row._status.key }}」<span v-if="row._hitFrame" class="form-hint">（{{ row._hitFrame }}）</span></div>
                        <div class="form-hint">现有候选（{{ row._hitCands.length }}）</div>
                        <ul class="cand-list">
                          <li v-for="(c, ci) in row._hitCands" :key="ci"><code>{{ c.by }} = {{ c.value }}</code></li>
                        </ul>
                        <div class="cand-preview-best">
                          本次 best：<code>{{ row.best.by }} = {{ row.best.value }}</code>
                          <div class="form-hint" :class="row._status.type === 'exists' ? 'hint-ok' : 'hint-warn'">{{ row._status.type === 'exists' ? '→ 已在库中，无需再加' : '→ 新候选，加为 key 时将追加到头部' }}</div>
                        </div>
                      </div>
                    </el-popover>
                  </template>
                  <span v-else class="form-hint">—</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="110" align="center">
                <template #default="{ row }">
                  <el-button
                    v-if="row._status.type === 'exists'" link type="info" size="small" disabled
                  >已存在</el-button>
                  <el-button
                    v-else link type="primary" size="small"
                    :disabled="!row.best || !row.candidates?.length" @click="openAddAsKey(row, g.frameMatch)"
                  >{{ row._status.type === 'update' ? '更新已有' : '加为 key' }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
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
          <el-input v-model="dialog.frame" placeholder="shell / vm / auto / url:<iframe host>，缺省 auto" />
        </el-form-item>
        <el-form-item label="页面">
          <el-select v-model="dialog.page" filterable allow-create default-first-option clearable placeholder="所属页面，可选已有或直接输入新页面；留空=未分类" style="width:100%">
            <el-option v-for="p in pageOptions" :key="p" :label="p" :value="p" />
          </el-select>
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
        <div v-if="add.status && add.status.type !== 'new'" class="add-status">
          <el-tag :type="STATUS_META[add.status.type].tag" size="small" effect="plain">{{ STATUS_META[add.status.type].label }}</el-tag>
          <span class="form-hint">已匹配库中 key「{{ add.status.key }}」，{{ add.status.type === 'exists' ? '该候选已登记' : '建议更新已有以补充候选' }}</span>
        </div>
        <div v-if="add.frame && add.frame.startsWith('url:')" class="form-hint add-deep-hint">
          该元素在嵌套 iframe，将按 frame url 定位：<code>{{ add.frame }}</code>（执行时从页面所有 frame 按此 url 匹配，找不到回退 shell/vm）
        </div>
      </div>
      <el-form label-width="90px" style="margin-top:12px">
        <el-form-item label="模式">
          <el-radio-group v-model="add.mode">
            <el-radio value="create">新建 key</el-radio>
            <el-radio value="update" :disabled="!rows.length">更新已有 key</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="add.mode === 'create'">
          <el-form-item label="key 名" required>
            <el-input v-model="add.key" placeholder="语义 key，如 login_button" />
          </el-form-item>
          <el-form-item label="页面">
            <el-select v-model="add.page" filterable allow-create default-first-option clearable placeholder="所属页面，默认取上方「当前页面」；留空=未分类" style="width:100%">
              <el-option v-for="p in pageOptions" :key="p" :label="p" :value="p" />
            </el-select>
          </el-form-item>
          <el-form-item label="说明">
            <el-input v-model="add.desc" placeholder="可选：这个 key 找的是什么元素" />
          </el-form-item>
        </template>
        <el-form-item v-else label="目标 key" required>
          <el-select v-model="add.targetId" placeholder="选择当前作用域的已有 key" filterable style="width:100%">
            <el-option v-for="r in rows" :key="r.id" :value="r.id" :label="`${r.key}（${(r.candidates || []).length} 候选）`" />
          </el-select>
          <div class="form-hint">best 候选将按<b>稳定优先</b>并入该 key（文案类候选自动降到末尾，超出上限丢弃最不稳的）</div>
          <div v-if="addTarget" class="add-compare">
            <div class="form-hint">「{{ addTarget.key }}」现有候选（{{ (addTarget.candidates || []).length }}）</div>
            <ul class="cand-list">
              <li v-for="(c, ci) in (addTarget.candidates || [])" :key="ci"><code>{{ c.by }} = {{ c.value }}</code></li>
              <li v-if="!(addTarget.candidates || []).length" class="form-hint">（空）</li>
            </ul>
            <div class="form-hint">合并后顺序（稳定优先，脆弱文案候选降到末尾）</div>
            <ol class="cand-list merged">
              <li v-for="(c, ci) in addMergedPreview" :key="ci"><code>{{ c.by }} = {{ c.value }}</code> <el-tag v-if="c._new" type="warning" size="small" effect="plain">新</el-tag></li>
            </ol>
          </div>
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
import { isFragile, orderCandidates } from '@/utils/selector-ranking'

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
const activePages = ref([])   // 管理页展开的分组(page name 列表)

// 页面历史建议:当前作用域 rows 的非空 page 去重(供新增/编辑/探测/加 key 的下拉建议)。
const pageOptions = computed(() => {
  const set = new Set()
  for (const r of rows.value) if (r.page) set.add(r.page)
  return [...set].sort()
})

// 管理页按 page 分组:每组 {name, pageLabel, keys};page 为空归"(未分类)"并置底。
const UNGROUPED_NAME = '__ungrouped__'
const groupedRows = computed(() => {
  const map = new Map()
  for (const r of rows.value) {
    const p = r.page || ''
    if (!map.has(p)) map.set(p, [])
    map.get(p).push(r)
  }
  const entries = [...map.entries()].sort((a, b) => {
    if (a[0] === '') return 1
    if (b[0] === '') return -1
    return a[0].localeCompare(b[0])
  })
  return entries.map(([page, keys]) => ({
    name: page || UNGROUPED_NAME, pageLabel: page || '（未分类）', keys,
  }))
})

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
  if (!pid.value) { rows.value = []; activePages.value = []; return }
  loading.value = true
  try {
    const data = await listSelectors(pid.value)
    rows.value = subProduct.value ? (data.by_sub?.[subProduct.value] || []) : (data.shared || [])
    activePages.value = groupedRows.value.map((g) => g.name)   // 默认全部展开
  } finally { loading.value = false }
}

function fmtTime(s) {
  if (!s) return '—'
  return String(s).replace('T', ' ').slice(0, 16)
}

// ---- 新增 / 编辑 ----
const dialog = reactive({ visible: false, id: null, key: '', frame: '', page: '', desc: '', candidatesText: '[]', saving: false })

function openCreate() {
  Object.assign(dialog, { id: null, key: '', frame: 'auto', page: '', desc: '', candidatesText: '[]', saving: false, visible: true })
}
function openEdit(row) {
  Object.assign(dialog, {
    id: row.id, key: row.key, frame: row.frame || 'auto', page: row.page || '', desc: row.desc || '',
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
      await patchSelector(dialog.id, { frame: dialog.frame || 'auto', page: dialog.page || '', desc: dialog.desc || '', candidates })
    } else {
      await createSelector({
        project_id: pid.value, sub_product: subProduct.value, key: dialog.key.trim(),
        frame: dialog.frame || 'auto', page: dialog.page || '', desc: dialog.desc || '', candidates,
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

// 单个 key 候选链上限：脆弱候选在尾，超出上限时 slice 自然丢弃最不稳的，防链膨胀/优先级倒置。
const MAX_CANDIDATES = 6

// ---- 设备探测（discover / verify）----
// probe.result 存最近一次探测结果；mode 记录当前展示的是 discover 还是 verify。
// updateTarget：verify 里点「重新探测更新」预置的目标 key，下次「加为 key」默认选它（更新已有）。
const probe = reactive({
  runner: '', contains: '', mode: 'discover', page: '',
  running: false, done: false, result: null, updateTarget: '', hideExists: false, screenshotUrl: '',
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
  probe.screenshotUrl = ''
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
      probe.screenshotUrl = r.screenshot_url || ''
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

// ---- 探测元素 vs 已入库对比标识(#3)----
// 口径:按候选定位器 by+value 重叠判定。对当前作用域已登记的 key 建索引:
//   candKey(c) = `${by} ${value}` → 该候选属于哪个 key。
// 元素标识:
//   已存在(exists):元素 best 候选已在某 key 里(该 key 已能定位到它,无需再加)。
//   更新(update):元素与某 key 有共同候选、但 best 是新的(可把 best 补进该 key)。
//   新增(new):元素所有候选与所有 key 均无重叠。
const candKey = (c) => `${c.by} ${c.value}`

// 当前作用域 rows 的候选反查索引:candKey → key 名(取第一个命中的 key)。
const candIndex = computed(() => {
  const idx = new Map()
  for (const r of rows.value) {
    for (const c of (r.candidates || [])) {
      const k = candKey(c)
      if (!idx.has(k)) idx.set(k, r.key)
    }
  }
  return idx
})

// 给一个探测元素算标识:{ type:'exists'|'update'|'new', key?:命中的已有 key }
// 口径:best 已在库→已存在;否则看其它候选与哪个 key 重叠——
//   稳定候选命中且 best 是脆弱(纯文案漂移)→ 已存在(不更新,避免堆积脆弱候选);
//   稳定候选命中且 best 也是稳定(锚点变更)→ 更新;
//   仅脆弱候选命中 → 更新;都不命中 → 新增。
function matchStatus(el) {
  const idx = candIndex.value
  const best = el.best
  if (best && idx.has(candKey(best))) return { type: 'exists', key: idx.get(candKey(best)) }
  let stableHit = null
  let fragileHit = null
  for (const c of (el.candidates || [])) {
    if (!idx.has(candKey(c))) continue
    if (isFragile(c)) { if (!fragileHit) fragileHit = idx.get(candKey(c)) }
    else if (!stableHit) stableHit = idx.get(candKey(c))
  }
  if (stableHit) {
    return best && isFragile(best)
      ? { type: 'exists', key: stableHit }   // 纯文案漂移:稳定锚点已在库,best 只是文案 → 不更新
      : { type: 'update', key: stableHit }   // best 是新的稳定锚点 → 值得更新
  }
  if (fragileHit) return { type: 'update', key: fragileHit }
  return { type: 'new' }
}

const STATUS_META = {
  exists: { label: '已存在', tag: 'success' },
  update: { label: '更新', tag: 'warning' },
  new: { label: '新增', tag: 'primary' },
}

// key 名 → 当前作用域该 key 的 row（供标识 popover 展示命中 key 的现有候选/frame）。
const keyIndex = computed(() => {
  const idx = new Map()
  for (const r of rows.value) if (!idx.has(r.key)) idx.set(r.key, r)
  return idx
})

// discover 结果按标识增强：每元素附 _status/_hitCands/_hitFrame；组内计数；排序 新增→可更新→已存在；可隐藏已存在。
const STATUS_ORDER = { new: 0, update: 1, exists: 2, none: 3 }
const enrichedGroups = computed(() => {
  if (probe.mode !== 'discover' || !probe.result) return []
  const kIdx = keyIndex.value
  return (probe.result.groups || []).map((g, gi) => {
    let els = (g.elements || []).map((el, ei) => {
      const status = el.best ? matchStatus(el) : { type: 'none' }
      const hit = status.key ? kIdx.get(status.key) : null
      return { ...el, _uid: `${gi}-${ei}`, _frameMatch: g.frameMatch || g.frame || 'auto', _status: status, _hitCands: hit ? (hit.candidates || []) : [], _hitFrame: hit ? (hit.frame || '') : '' }
    })
    const counts = { new: 0, update: 0, exists: 0 }
    for (const e of els) if (counts[e._status.type] !== undefined) counts[e._status.type] += 1
    if (probe.hideExists) els = els.filter((e) => e._status.type !== 'exists')
    els.sort((a, b) => STATUS_ORDER[a._status.type] - STATUS_ORDER[b._status.type])
    return { ...g, elements: els, counts }
  })
})

// 汇总各组计数（开关标签/总览用），基于过滤前的全量。
const totalCounts = computed(() => {
  const t = { new: 0, update: 0, exists: 0 }
  for (const g of enrichedGroups.value) { t.new += g.counts.new; t.update += g.counts.update; t.exists += g.counts.exists }
  return t
})

// ---- 截图叠框（元素框选）----
// hoverKey：当前高亮的元素 uid，驱动「截图框 ↔ 表格行」双向高亮。
const hoverKey = ref('')
// zoom：截图显示缩放（1=适应视口宽度）；框用百分比定位，随 wrap 宽度自动缩放。
const zoom = ref(1)

// 截图上的框：每个有 absRect 的元素按 absRect/pageSize 归一化成百分比定位（响应式，自动消 dpr）。
// 基于 enrichedGroups（已按 hideExists 过滤/排序），故隐藏已存在时框也同步减少。
const shotBoxes = computed(() => {
  const ps = probe.result?.pageSize
  if (!probe.screenshotUrl || !ps || !ps.w || !ps.h) return []
  const boxes = []
  for (const g of enrichedGroups.value) {
    for (const el of g.elements) {
      if (!el.absRect) continue
      boxes.push({
        uid: el._uid, el, frameMatch: el._frameMatch, type: el._status.type, approx: !!el.absApprox,
        label: `${el.text || el.tag}${el.best ? ` · ${el.best.by}=${el.best.value}` : ''}${el.absApprox ? '（位置近似）' : ''}`,
        style: {
          left: `${(el.absRect.x / ps.w) * 100}%`, top: `${(el.absRect.y / ps.h) * 100}%`,
          width: `${(el.absRect.w / ps.w) * 100}%`, height: `${(el.absRect.h / ps.h) * 100}%`,
        },
      })
    }
  }
  return boxes
})

// 表格行 class 高亮当前 hover 的元素；cell hover 设 hoverKey（框↔行双向联动）。
const rowClass = ({ row }) => (row._uid && row._uid === hoverKey.value ? 'row-hi' : '')
const onCellEnter = (row) => { hoverKey.value = row._uid || '' }

// 框覆盖统计：boxTotal=当前列表元素总数（含无坐标者），approxCount=位置近似（虚线）的框数。
const boxTotal = computed(() => enrichedGroups.value.reduce((n, g) => n + g.elements.length, 0))
const approxCount = computed(() => shotBoxes.value.filter((b) => b.approx).length)

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
const add = reactive({ visible: false, mode: 'create', tag: '', type: '', text: '', frame: 'auto', cand: null, key: '', page: '', desc: '', targetId: null, saving: false, status: null })

// 更新已有：目标 key 当前 row（取现有候选做对比预览）；仅 update 模式且选定目标时有值。
const addTarget = computed(() => (add.mode === 'update' && add.targetId ? rows.value.find((r) => r.id === add.targetId) || null : null))

// 合并后候选顺序预览：与 submitAddAsKey 的 merged 完全一致（就地替换脆弱同 by + 稳定优先 + 上限），标记新增项。
const addMergedPreview = computed(() => {
  if (!add.cand || !addTarget.value) return []
  const existing = addTarget.value.candidates || []
  const isDup = (c) => c.by === add.cand.by && c.value === add.cand.value
  const dropSameFragile = (c) => isFragile(add.cand) && c.by === add.cand.by
  const kept = existing.filter((c) => !isDup(c) && !dropSameFragile(c))
  const dup = existing.some(isDup)
  return orderCandidates([{ ...add.cand, _new: !dup }, ...kept.map((c) => ({ ...c, _new: false }))]).slice(0, MAX_CANDIDATES)
})

// 把探测候选归一成注册表存储的 {by,value}（丢弃 runner 内部的 sel/score）。
function toCand(c) {
  return c ? { by: c.by, value: c.value } : null
}

function openAddAsKey(el, frame) {
  const cand = toCand(el.best)
  const status = matchStatus(el)   // #3 标识:exists/update/new
  // 预置更新目标优先级:verify 的「重新探测更新」预置 > 对比标识命中的已有 key。
  const presetByVerify = probe.updateTarget && rows.value.find((r) => r.key === probe.updateTarget)
  const presetByMatch = status.key && rows.value.find((r) => r.key === status.key)
  const preset = presetByVerify || presetByMatch
  Object.assign(add, {
    visible: true, saving: false, status,
    tag: el.tag, type: el.type || '', text: el.text || '', frame: frame || 'auto',
    cand,
    // exists/update/有预置 → 默认更新已有;new → 默认新建。
    mode: preset ? 'update' : 'create',
    key: '', page: probe.page || '', desc: '',
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
        frame: add.frame || 'auto', page: add.page || '', desc: add.desc.trim(), candidates: [add.cand],
      })
      ElMessage.success('已新建 key')
    } else {
      const target = rows.value.find((r) => r.id === add.targetId)
      const existing = target?.candidates || []
      // 合并：去掉与新候选完全相同的旧项；新候选若脆弱(text/role)则替换同 by 的旧脆弱项(就地替换、不累加)；
      // 再按稳定优先排序(脆弱降尾)、裁剪到上限，避免链膨胀与优先级倒置。
      const dropSameFragile = (c) => isFragile(add.cand) && c.by === add.cand.by
      const kept = existing.filter((c) => !(c.by === add.cand.by && c.value === add.cand.value) && !dropSameFragile(c))
      const merged = orderCandidates([add.cand, ...kept]).slice(0, MAX_CANDIDATES)
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
.page-title { font-weight: 600; margin-right: 8px; }
.page-count { vertical-align: middle; }
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
.add-status { margin-top: 6px; display: flex; gap: 6px; align-items: center; }
.add-deep-hint { margin-top: 8px; line-height: 1.5; }
.match-key { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
.probe-toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; }
.shot-panel { margin-bottom: 12px; }
.shot-bar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 6px; }
.shot-zoom { display: flex; align-items: center; gap: 8px; }
.zoom-val { min-width: 40px; text-align: right; }
.shot-viewport { max-height: 60vh; overflow: auto; border: 1px solid #e4e7ed; border-radius: 4px; background: #fafafa; }
.shot-wrap { position: relative; }
.shot-img { display: block; width: 100%; height: auto; }
.shot-overlay { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
.el-box { position: absolute; box-sizing: border-box; border: 1.5px solid rgba(64,158,255,.55); background: rgba(64,158,255,.07); cursor: pointer; pointer-events: auto; transition: box-shadow .1s, background .1s; }
.el-box.box-update { border-color: rgba(230,162,60,.75); background: rgba(230,162,60,.1); }
.el-box.box-exists { border-color: rgba(103,194,58,.5); background: rgba(103,194,58,.06); }
.el-box.approx { border-style: dashed; }
.el-box.active { border-width: 2px; box-shadow: 0 0 0 2px rgba(64,158,255,.35); background: rgba(64,158,255,.18); z-index: 2; }
:deep(.el-table .row-hi) { background: #ecf5ff; }
.status-cell { display: inline-flex; flex-direction: column; align-items: center; gap: 2px; cursor: help; }
.cand-preview-title { font-weight: 600; margin-bottom: 4px; }
.cand-list { margin: 4px 0 8px; padding-left: 18px; }
.cand-list li { line-height: 1.7; }
.cand-list.merged { background: #f5f7fa; border-radius: 4px; padding: 6px 6px 6px 22px; }
.cand-preview-best { border-top: 1px dashed #dcdfe6; padding-top: 6px; }
.hint-ok { color: #67c23a; }
.hint-warn { color: #e6a23c; }
.add-compare { margin-top: 10px; border-top: 1px dashed #dcdfe6; padding-top: 8px; }
code { background: #f0f2f5; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
</style>
