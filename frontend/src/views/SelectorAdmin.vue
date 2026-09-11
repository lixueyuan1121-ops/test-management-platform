<template>
  <div class="selector-admin functional-workspace">
    <WorkspacePage title="选择器管理">
      <template #actions>
        <el-select v-model="pid" :disabled="contextLocked" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange"><el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" /></el-select>
        <el-select v-model="subProduct" :disabled="contextLocked" placeholder="作用域" size="small" style="width:150px" @change="onScopeChange"><el-option label="项目级共享" :value="''" /><el-option v-for="sp in SUB_PRODUCTS" :key="sp" :label="sp" :value="sp" /></el-select>
        <el-button type="primary" size="small" :disabled="!pid || contextLocked" @click="openCreate">新增 key</el-button>
      </template>
      <template #selection><el-tabs v-model="activeView"><el-tab-pane label="注册表" name="registry" /><el-tab-pane label="设备探测" name="probe" /><el-tab-pane label="候选评审" name="learned" /></el-tabs></template>
    <!-- 设备探测面板：选在线设备，扫当前页元素产候选 → 加为 key(新建/更新已有)；或校验现有 key 是否失效 -->
    <section v-show="activeView === 'probe'">
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
              size="small" :type="boxMode ? 'warning' : 'default'"
              :disabled="!pid || !probe.runner || probe.running" @click="toggleBoxMode"
            >{{ boxMode ? '框选中…点击取消' : '框选探测' }}</el-button>
            <el-button
              size="small" :loading="probe.running && probe.mode === 'verify'"
              :disabled="!pid || !probe.runner || probe.running" @click="onVerify"
            >校验失效 key</el-button>
          </div>
        </div>

      <!-- 目标提示：当前落库作用域 + 若处于「更新已有」预置目标 -->
      <div class="probe-scope">
        <span>探测结果将落到当前作用域：<b>{{ subProduct || '项目级共享' }}</b></span>
        <el-tag v-if="probe.updateTarget" type="warning" size="small" closable @close="probe.updateTarget = ''">
          「加为 key」默认更新已有：{{ probe.updateTarget }}
        </el-tag>
      </div>

      <!-- 「定位缺失 key」待办条：从用例库跳来时列出待补 key，选中后按语义匹配高亮元素，点「加为 key」预填该 key 名 -->
      <!-- 批量模式(bulk):展示待补清单(已补/未补) + 一键「批量加为 key」把本次探测匹配上的 key 一次建好 -->
      <el-alert v-if="fixCtx.bulk && fixCtx.keys.length" type="warning" :closable="false" class="fix-bar">
        <div class="fix-bar-in">
          <span class="fix-bar-hint">批量补选择器：在客户端切到目标页/弹窗后「探测」，系统自动把探测元素匹配到下列待补 key，核对后一键批量建（分几次覆盖不同弹窗状态）：</span>
          <div class="fix-keys-chips">
            <el-tag v-for="k in fixCtx.keys" :key="k" size="small"
                    :type="fixCtx.done.includes(k) ? 'success' : (bulkChipInfo.has(k) ? 'primary' : 'info')"
                    :effect="(fixCtx.done.includes(k) || (bulkChipInfo.get(k) && bulkChipInfo.get(k).uid === hoverKey)) ? 'dark' : 'plain'"
                    :class="{ 'chip-hover': bulkChipInfo.get(k) && bulkChipInfo.get(k).uid === hoverKey }"
                    style="cursor:default"
                    @mouseenter="hoverKey = bulkChipInfo.get(k) ? bulkChipInfo.get(k).uid : ''"
                    @mouseleave="hoverKey = ''">
              <template v-if="fixCtx.done.includes(k)">✓ {{ k }}</template>
              <template v-else-if="bulkChipInfo.has(k)">#{{ bulkChipInfo.get(k).no }} {{ k }}<span v-if="bulkChipInfo.get(k).label" class="chip-tid"> · {{ bulkChipInfo.get(k).label }}</span></template>
              <template v-else>{{ k }}</template>
            </el-tag>
          </div>
          <div class="fix-bar-actions">
            <el-button type="primary" size="small" :loading="bulkAdding" :disabled="!bulkMatches.length" @click="batchAddMatched">
              批量加为 key（本次匹配 {{ bulkMatches.length }} 个）
            </el-button>
            <span class="form-hint">已补 {{ fixCtx.done.length }} / {{ fixCtx.keys.length }}</span>
            <el-button link type="info" size="small" @click="fixCtx.keys = []; fixCtx.bulk = false">退出批量</el-button>
          </div>
        </div>
      </el-alert>
      <el-alert v-else-if="fixCtx.keys.length" type="warning" :closable="false" class="fix-bar">
        <div class="fix-bar-in">
          <span class="fix-bar-hint">待补选择器 key（选一个 → 下方高亮页面上最可能的元素 → 点该元素「加为 key」新建）：</span>
          <el-radio-group v-model="fixCtx.activeKey" size="small">
            <el-radio-button v-for="k in fixCtx.keys" :key="k" :value="k">{{ k }}</el-radio-button>
          </el-radio-group>
          <el-button link type="info" size="small" @click="fixCtx.keys = []; fixCtx.activeKey = ''">退出定位</el-button>
        </div>
      </el-alert>

      <el-empty v-if="!probe.done && !probe.running" description="选择在线设备后点「探测」，会扫描该设备当前页面的可交互元素" :image-size="80" />
      <div v-else-if="probe.running" class="probe-loading" v-loading="true" element-loading-text="探测中，请在设备上停留在目标页面…" style="min-height:120px" />

      <!-- discover/box 结果：按 shell/vm/iframe 分组；组内 新增/可更新 排前、已存在垫底，可一键隐藏已存在 -->
      <template v-else-if="(probe.mode === 'discover' || probe.mode === 'box') && probe.result">
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
                <div
                  class="shot-overlay" :class="{ 'box-selecting': boxMode }"
                  @mousedown="onBoxDown" @mousemove="onBoxMove" @mouseup="onBoxUp" @mouseleave="onBoxUp"
                >
                  <!-- 框选提交后:标注本次框选区(醒目高亮),让用户看清"选了这里 → 命中下面高亮的元素框" -->
                  <div v-if="probe.mode === 'box' && selectedRegionStyle" class="box-selected-region" :style="selectedRegionStyle"></div>
                  <div
                    v-for="box in shotBoxes" :key="box.uid"
                    class="el-box" :class="['box-' + box.type, { active: hoverKey === box.uid, approx: box.approx, 'box-fix': box.fixKey }]"
                    :style="box.style" :title="box.label"
                    @mouseenter="hoverKey = box.uid" @mouseleave="hoverKey = ''"
                    @click="openAddAsKey(box.el, box.frameMatch)"
                  ><span v-if="box.fixNo" class="box-fix-no">{{ box.fixNo }}</span></div>
                  <!-- 框选中的矩形(拖拽实时) -->
                  <div v-if="boxRect" class="box-select-rect" :style="boxRect"></div>
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
                  <code v-if="row.best">{{ candLabel(row.best) }}</code>
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
                          <li v-for="(c, ci) in row._hitCands" :key="ci"><code>{{ candLabel(c) }}</code></li>
                        </ul>
                        <div class="cand-preview-best">
                          本次 best：<code>{{ candLabel(row.best) }}</code>
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
                    :disabled="!row.best || !row.candidates?.length" @click="openAddAsKey(row, row._frameMatch)"
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
    </section>

    <el-card v-if="pid" class="module-card">
      <template #header>
        <div class="header"><span>模块入口（从首页确定性到达各模块）</span>
          <el-button size="small" type="primary" @click="openModuleEdit(null)">新增模块入口</el-button></div>
      </template>
      <el-table :data="modules" size="small" border empty-text="尚未配置模块入口（配置后执行时会先确定性导航到该模块再跑用例）">
        <el-table-column prop="page" label="模块(page)" width="140" />
        <el-table-column label="入口导航 key(依次点)" min-width="200">
          <template #default="{ row }"><el-tag v-for="k in row.nav_keys" :key="k" size="small" class="nav-key-tag">{{ k }}</el-tag>
            <span v-if="!row.nav_keys.length" class="form-hint">（未设）</span></template>
        </el-table-column>
        <el-table-column prop="ready_key" label="就绪锚点 key" width="180" show-overflow-tooltip />
        <el-table-column prop="key_count" label="该模块 key 数" width="110" align="center" />
        <el-table-column label="操作" width="240" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openModuleEdit(row)">编辑</el-button>
            <el-button link type="warning" size="small" @click="refreshModule(row)">刷新选择器</el-button>
            <el-button link type="danger" size="small" @click="onDeleteModule(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="moduleDlg.visible" title="模块入口" width="520px">
      <el-form label-width="120px">
        <el-form-item label="模块(page)">
          <el-select v-model="moduleDlg.page" filterable allow-create default-first-option
                     placeholder="选或输入模块名，须与选择器 page 一致" style="width:100%">
            <el-option v-for="p in pageOptions" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="入口导航 key"><el-input v-model="moduleDlg.navText" placeholder="逗号分隔，依次点，如 navAutomation" /></el-form-item>
        <el-form-item label="就绪锚点 key"><el-input v-model="moduleDlg.readyKey" placeholder="导航后等它可见=到达，如 automationPageTitle" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="moduleDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="moduleDlg.saving" @click="submitModule">保存</el-button>
      </template>
    </el-dialog>

    <section v-show="activeView === 'registry'" class="registry-card">
        <div class="header">
          <div class="filters">
            <el-button size="small" :disabled="!pid" @click="openImport">手动导入</el-button>
            <el-button
              v-if="selectedIds.length" type="danger" size="small" :loading="batchDeleting" @click="onBatchDelete"
            >批量删除（{{ selectedIds.length }}）</el-button>
            <el-button
              v-if="selectedIds.length" size="small" :loading="batchPaging" @click="onBatchSetPage"
            >批量设页面（{{ selectedIds.length }}）</el-button>
            <el-button
              v-if="canImport" size="small" :disabled="!pid || contextLocked" :loading="importing" @click="onImport"
            >导入内置纳米Work注册表</el-button>
          </div>
        </div>

      <!-- 主动探测:配置扫描分支(本地脚本据此拉代码扫 testid)。分支存在平台,脚本发给别人也能用、免配凭据。 -->
      <div v-if="pid" class="scan-bar">
        <span class="scan-label">扫描分支</span>
        <el-input
          v-model="scanBranch" size="small" style="width:280px" clearable
          placeholder="如 feature-add-testid_20260903（当前作用域）"
        />
        <el-button size="small" type="primary" plain :loading="savingBranch" @click="saveScanBranch">保存分支</el-button>
        <span class="form-hint">本机 backend 目录运行：<code>python -m scripts.scan_selectors_from_branch --base-url … --project {{ pid }} --repo &lt;openclaw360-web 本地路径&gt; --import</code> 自动拉此分支扫描并导入</span>
      </div>

      <el-result v-if="loadError" icon="error" title="注册表加载失败"><template #extra><el-button @click="reload">重试注册表</el-button></template></el-result>
      <el-empty v-else-if="!rows.length" :description="loading ? '加载中…' : '该作用域暂无选择器 key'" :image-size="70" />
      <el-collapse v-else v-model="activePages" v-loading="loading">
        <el-collapse-item v-for="grp in groupedRows" :key="grp.name" :name="grp.name">
          <template #title>
            <span class="page-title">{{ grp.pageLabel }}</span>
            <el-tag size="small" type="info" effect="plain" class="page-count">{{ grp.keys.length }}</el-tag>
          </template>
          <el-table :data="grp.keys" size="small" border stripe @row-click="markSelectorSeen" @selection-change="(sel) => onGroupSelect(grp.name, sel)">
            <el-table-column type="selection" width="40" />
            <el-table-column prop="key" label="key" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="registry-key">
                  <button v-if="unreadSelectorIds.has(row.id)" class="new-selector-dot" type="button"
                          :aria-label="`${row.key} 最新添加，点击标为已读`" title="最新添加，点击标为已读"
                          @click.stop="markSelectorSeen(row)"></button>
                  <span>{{ row.key }}</span>
                </span>
              </template>
            </el-table-column>
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
                <el-button link type="primary" size="small" :disabled="contextLocked" @click="openEdit(row)">编辑</el-button>
                <el-button link type="danger" size="small" :disabled="contextLocked" @click="onDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
    </section>

    <!-- 新增 / 编辑弹窗 -->
    <el-dialog v-model="dialog.visible" :show-close="!dialog.saving" :close-on-click-modal="!dialog.saving" :close-on-press-escape="!dialog.saving" :title="dialog.id ? '编辑 key' : '新增 key'" width="600px">
      <el-form label-width="80px" :disabled="dialog.saving">
        <el-form-item label="key" required>
          <el-input v-model="dialog.key" :disabled="!!dialog.id" placeholder="语义 key，如 login_button" />
        </el-form-item>
        <el-form-item label="平台">
          <el-radio-group v-model="dialog.platform">
            <el-radio-button value="web">PC/Web</el-radio-button>
            <el-radio-button value="android">Android</el-radio-button>
            <el-radio-button value="ios">iOS</el-radio-button>
          </el-radio-group>
          <div class="form-hint">该 key 适用的平台；PC 端留 PC/Web，移动端按实际选择</div>
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
        <el-button :disabled="dialog.saving" @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 手动导入选择器：粘贴/上传 JSON（registry 或数组格式）→ 导入当前作用域 -->
    <el-dialog v-model="imp.visible" title="手动导入选择器" width="640px">
      <div class="form-hint" style="margin-bottom:8px">
        导入到作用域：<b>{{ subProduct || '项目级共享' }}</b>。支持两种格式：
        ① 注册表 <code>{ registry: { key: { frame, page, desc, candidates } } }</code>（见 选择器格式说明.md，扫描脚本输出即此格式）；
        ② 提测清单数组 <code>[{ key, testid, desc, page }]</code>（自动转成 testid 候选）。
      </div>
      <div class="imp-toolbar">
        <el-upload :auto-upload="false" :show-file-list="false" accept=".json" :on-change="onImpFile">
          <el-button size="small">选择 .json 文件</el-button>
        </el-upload>
        <el-checkbox v-model="imp.overwrite" size="small">同名 key 覆盖（默认跳过）</el-checkbox>
      </div>
      <el-input
        v-model="imp.text" type="textarea" :rows="12"
        placeholder='粘贴 JSON。例如 {"vmIframe":"...","registry":{"navHome":{"frame":"vm","page":"首页","desc":"[全局]-[左侧导航栏]-[进入首页]-[导航按钮]","candidates":[{"by":"testid","value":"nav-home"}]}}}'
      />
      <template #footer>
        <el-button @click="imp.visible = false">取消</el-button>
        <el-button type="primary" :loading="imp.saving" @click="submitImport">导入</el-button>
      </template>
    </el-dialog>

    <!-- 加为 key：探测元素 → 落库为选择器（新建 / 更新已有 key 追加候选到头部）-->
    <el-dialog v-model="add.visible" :show-close="!add.saving" :close-on-click-modal="!add.saving" :close-on-press-escape="!add.saving" title="加为 key" width="560px">
      <div class="add-preview">
        <div class="form-hint">来源元素（{{ add.frame }} frame）</div>
        <div><el-tag size="small" type="info" effect="plain">{{ add.tag }}{{ add.type ? `[${add.type}]` : '' }}</el-tag> <span class="probe-el-text">{{ add.text || '（无文本）' }}</span></div>
        <div class="add-cand">best 候选：<code>{{ add.cand ? candLabel(add.cand) : '—' }}</code></div>
        <div v-if="add.mode === 'create' && add.cands.length" class="add-cands">
          <span class="form-hint">新建将存 {{ add.cands.length }} 个候选（testid 优先 + css 兜底，执行期可回落）：</span>
          <el-tag v-for="(c, ci) in add.cands" :key="ci" size="small" effect="plain"
                  :type="c.by === 'testid' ? 'success' : (c.by === 'css' ? 'primary' : 'info')" class="cand-chip">
            {{ candLabel(c) }}
          </el-tag>
        </div>
        <div v-if="add.status && add.status.type !== 'new'" class="add-status">
          <el-tag :type="STATUS_META[add.status.type].tag" size="small" effect="plain">{{ STATUS_META[add.status.type].label }}</el-tag>
          <span class="form-hint">已匹配库中 key「{{ add.status.key }}」，{{ add.status.type === 'exists' ? '该候选已登记' : '建议更新已有以补充候选' }}</span>
        </div>
        <div v-if="add.frame && add.frame.startsWith('url:')" class="form-hint add-deep-hint">
          该元素在嵌套 iframe，将按 frame url 定位：<code>{{ add.frame }}</code>（执行时从页面所有 frame 按此 url 匹配，找不到回退 shell/vm）
        </div>
      </div>
      <el-form label-width="90px" :disabled="add.saving" style="margin-top:12px">
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
          <el-form-item label="说明(四段式)">
            <div class="seg-row">
              <el-input v-model="add.segTab" placeholder="导航Tab，如 自动化" />
              <span class="seg-sep">-</span>
              <el-input :model-value="add.page" disabled placeholder="页面(取上方)" />
              <span class="seg-sep">-</span>
              <el-input v-model="add.segScene" placeholder="场景，如 新建任务" />
              <span class="seg-sep">-</span>
              <el-input v-model="add.segElem" placeholder="控件类型，如 输入框/下拉列表/按钮" />
            </div>
            <div class="form-hint">
              格式 [导航Tab]-[页面]-[场景]-[元素:输入框还是下拉列表]；控件类型已按元素/用例步骤自动推断，可改。
              预览：<code>{{ addComposedDesc || '（四段皆空=不填说明）' }}</code>
            </div>
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
              <li v-for="(c, ci) in (addTarget.candidates || [])" :key="ci"><code>{{ candLabel(c) }}</code></li>
              <li v-if="!(addTarget.candidates || []).length" class="form-hint">（空）</li>
            </ul>
            <div class="form-hint">合并后顺序（稳定优先，脆弱文案候选降到末尾）</div>
            <ol class="cand-list merged">
              <li v-for="(c, ci) in addMergedPreview" :key="ci"><code>{{ candLabel(c) }}</code> <el-tag v-if="c._new" type="warning" size="small" effect="plain">新</el-tag></li>
            </ol>
          </div>
        </el-form-item>
        <!-- XPath 定位:新建/更新两种模式都可用。CSS 满足不了(同 class 多命中)时用它按文本精确定位。 -->
        <el-form-item label="XPath 定位">
          <el-alert v-if="add.cssCount >= 2" type="warning" :closable="false" show-icon class="xpath-alert">
            该元素的 CSS 类在本次探测命中 <b>{{ add.cssCount }}</b> 个（多为同 class、仅文本不同的控件），CSS 会定位串到别的元素。建议用下方 XPath 按文本精确定位。
          </el-alert>
          <el-input v-model="add.xpath" clearable placeholder='可选：CSS 满足不了时填 XPath，如 //button[normalize-space(.)="打开文件夹"]'>
            <template #append>
              <el-button :disabled="!add.xpathAuto" @click="add.xpath = add.xpathAuto">自动生成</el-button>
            </template>
          </el-input>
          <div class="form-hint">
            填了就并入候选并<b>排在 CSS 之前</b>（testid &gt; xpath &gt; css）；{{ add.mode === 'update' ? '合并进所选已有 key' : '随新 key 一起存' }}。留空则不加。
            <span v-if="add.xpathAuto">建议：<code class="xpath-suggest" @click="add.xpath = add.xpathAuto" title="点击采纳">{{ add.xpathAuto }}</code></span>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="add.saving" @click="add.visible = false">取消</el-button>
        <el-button type="primary" :loading="add.saving" @click="submitAddAsKey">保存</el-button>
      </template>
    </el-dialog>

    <!-- 运行时自学习候选评审:runner 全候选失败→按语义自愈成功后上报;转正=去试用标,拒绝=移出注册表 -->
    <section v-show="activeView === 'learned'" class="learned-card">
        <div class="header">
          <span>自学习候选评审
            <el-tag v-if="learned.rows.length" type="warning" size="small" effect="dark" class="learned-badge">{{ learned.rows.length }}</el-tag>
          </span>
          <div class="filters">
            <el-select v-model="learned.status" size="small" style="width:130px" @change="reloadLearned">
              <el-option label="待评审" value="pending" />
              <el-option label="已转正" value="approved" />
              <el-option label="已拒绝" value="rejected" />
            </el-select>
            <el-button size="small" :loading="learned.loading" @click="reloadLearned">刷新</el-button>
          </div>
        </div>
      <el-alert type="info" :closable="false" class="learned-intro" show-icon>
        执行机在<b>所有已注册候选都定位失败</b>时,按 key 语义在页面上找回元素并铸造新候选（已临时挂在该 key 候选链<b>尾部试用</b>）。
        <b>转正</b>=去掉试用标永久保留；<b>拒绝</b>=从注册表移除且不再自动挂回。
      </el-alert>
      <el-result v-if="learned.error" icon="error" title="候选加载失败"><template #extra><el-button @click="reloadLearned">重试候选</el-button></template></el-result>
      <el-table v-else :data="learned.rows" v-loading="learned.loading" size="small" border stripe
                :empty-text="learned.status === 'pending' ? '暂无待评审的自学习候选' : '无记录'">
        <el-table-column prop="key" label="key" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <code>{{ row.key }}</code>
            <div v-if="row.desc" class="form-hint">{{ row.desc }}</div>
          </template>
        </el-table-column>
        <el-table-column label="自学习候选" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <code>{{ row.candidate.by }} = {{ row.candidate.value }}</code>
            <span v-if="row.candidate.name" class="form-hint">（name: {{ row.candidate.name }}）</span>
          </template>
        </el-table-column>
        <el-table-column label="证据" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.evidence.text">文本「{{ row.evidence.text }}」</span>
            <span v-if="row.evidence.matched" class="form-hint"> · 匹配 {{ row.evidence.matched }}</span>
            <span v-if="row.evidence.score" class="form-hint"> · 分 {{ row.evidence.score }}</span>
          </template>
        </el-table-column>
        <el-table-column label="命中" width="60" align="center">
          <template #default="{ row }">{{ row.hit_count }}</template>
        </el-table-column>
        <el-table-column prop="runner" label="来源设备" width="110" show-overflow-tooltip />
        <el-table-column label="时间" width="140">
          <template #default="{ row }">{{ (row.created_at || '').replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column v-if="learned.status === 'pending'" label="操作" width="140" align="center">
          <template #default="{ row }">
            <el-button link type="success" size="small" :disabled="reviewing" @click="reviewLearnedRow(row, 'approve')">转正</el-button>
            <el-button link type="danger" size="small" :disabled="reviewing" @click="reviewLearnedRow(row, 'reject')">拒绝</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>
    </WorkspacePage>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import '@/styles/workspace-overlays.css'
const activeView = ref('registry')
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { useAppStore } from '@/store/app'
import {
  listSelectors, createSelector, patchSelector, deleteSelector, importLegacySelectors,
  batchDeleteSelectors, importSelectors, setSelectorScope, batchSetSelectorPage,
  selectorUsage, backfillTestcases,
  listMyDevices, startProbe, getProbe,
  listModules, saveModule, deleteModule,
  listLearnedSelectors, reviewLearnedSelector,
} from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { isFragile, orderCandidates } from '@/utils/selector-ranking'
import { autoXPath, cssSelectorValue, countCssMatches } from '@/utils/xpath-locator'
import { rankElements } from '@/utils/selector-match'
import { matchElementsToKeys } from '@/utils/bulk-fix-selectors'
import { keysOfModule, staleKeysFromVerify } from '@/utils/module-refresh'
import { useRoute } from 'vue-router'

// 子产品固定枚举，须与后端 api/release.py 的 SUB_PRODUCTS 一致（选择器按 (项目, 子产品) 分域）。
const SUB_PRODUCTS = ['纳米Work云端版', '纳米Work桌面版', '360安全龙虾云端版', '360安全龙虾WSL']

const auth = useAuthStore()
const app = useAppStore()

const projects = ref([])
const pid = ref(null)
const subProduct = ref('')   // '' = 项目级共享
const rows = ref([])
// 未查看的新 key 按用户保存在当前标签页，刷新或离开列表后仍可继续查看。
const unreadStorageKey = `tp_selector_unread:${auth.user?.id || 'anonymous'}`
const unreadSelectorIds = ref(new Set())
try {
  const saved = JSON.parse(sessionStorage.getItem(unreadStorageKey) || '[]')
  if (Array.isArray(saved)) unreadSelectorIds.value = new Set(saved.filter(Number.isInteger))
} catch { /* 存储不可用时仍保留本次页面内的提示 */ }
function saveUnreadSelectors() {
  try { sessionStorage.setItem(unreadStorageKey, JSON.stringify([...unreadSelectorIds.value])) }
  catch { /* 存储失败不影响添加和查看 */ }
}
function markSelectorNew(row) {
  if (!Number.isInteger(row?.id)) return
  unreadSelectorIds.value.add(row.id)
  saveUnreadSelectors()
}
function markSelectorSeen(row) {
  if (unreadSelectorIds.value.delete(row.id)) saveUnreadSelectors()
}
const loading = ref(false)
const importing = ref(false)
const devices = ref([])   // 我的在线设备（探测目标）
const activePages = ref([])   // 管理页展开的分组(page name 列表)

// ---- 主动探测：扫描分支配置（当前作用域）----
const scanBranch = ref('')          // 输入框绑定的扫描分支
const scopeVmIframe = ref('')       // 当前作用域的 vm_iframe（保存分支时一并回传，避免被清空）
const savingBranch = ref(false)

// ---- 批量删除：跨分组收集选中行 ----
const selectedByPage = reactive({})   // 分组名 → 该组选中的 row 数组
const batchDeleting = ref(false)
const selectedIds = computed(() => {
  const ids = []
  for (const name of Object.keys(selectedByPage)) {
    for (const r of (selectedByPage[name] || [])) ids.push(r.id)
  }
  return ids
})
function onGroupSelect(name, sel) { selectedByPage[name] = sel }
function clearSelection() { for (const k of Object.keys(selectedByPage)) delete selectedByPage[k] }

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
  // 从用例库「定位缺失 key」带 query 跳来：预填项目/页面/缺失 key + 语义上下文，并自动探测。
  const q = route.query
  const qPid = q.project_id ? Number(q.project_id) : null
  if (qPid && projects.value.some((p) => p.id === qPid)) {
    pid.value = qPid
    await reload()
    fixCtx.keys = String(q.fix_keys || '').split(',').filter(Boolean)
    if (fixCtx.keys.length) activeView.value = 'probe'
    fixCtx.ctx = String(q.ctx || '')
    fixCtx.bulk = q.bulk === '1'
    fixCtx.done = []
    // 批量模式不预选单个 activeKey(整批一起匹配);单条模式仍激活第一个 key 走原高亮排序。
    fixCtx.activeKey = fixCtx.bulk ? '' : (fixCtx.keys[0] || '')
    if (q.page) probe.page = String(q.page)
    // 预选在线设备。单条「定位缺失 key」自动探一次(用户通常已停在目标页);批量模式**不自动探**——
    // 页面加载时客户端多半还在首页(没切到目标页/没开弹窗),自动探是空跑,且批量需分多次切换弹窗状态探测,
    // 故预选设备后由用户切到目标页/弹窗再手动点「探测」。
    if (fixCtx.keys.length && devices.value.length) {
      probe.runner = devices.value[0].runner_id || probe.runner
      if (probe.runner && !fixCtx.bulk) onDiscover()
    }
    return
  }
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await reload()
  }
})

let disposed = false, listVersion = 0, learnedVersion = 0, probeVersion = 0
const loadError = ref(false), reviewing = ref(false), deleting = ref(false)
const contextLocked = computed(() => dialog.visible || dialog.saving || add.visible || add.saving || probe.running || importing.value || reviewing.value || deleting.value)
onUnmounted(() => { disposed = true; ++listVersion; ++learnedVersion; ++probeVersion; stopPoll() })

async function onProjectChange() {
  if (pid.value) setLastProjectId(pid.value)
  fixCtx.keys = []; fixCtx.ctx = ''; fixCtx.activeKey = ''
  await onScopeChange()
}

async function onScopeChange() {
  probe.result = null; probe.screenshotUrl = ''; probe.done = false; probe.updateTarget = ''
  boxMode.value = false; lastBox.value = null
  await reload()
}

// 按当前 (项目, 子产品) 取列表：'' 取 shared，否则取 by_sub[子产品]。
async function reload() {
  const version = ++listVersion, project = pid.value, scope = subProduct.value
  rows.value = []; activePages.value = []; loadError.value = false; loading.value = false
  reloadLearned()
  if (!pid.value) { rows.value = []; activePages.value = []; return }
  clearSelection()   // 切项目/作用域清空批量选择，避免跨作用域误删
  loading.value = true
  try {
    const data = await listSelectors(project, scope)
    if (disposed || version !== listVersion) return
    rows.value = scope ? (data.by_sub?.[scope] || []) : (data.shared || [])
    // 回显当前作用域的扫描分支 / vm_iframe（保存分支时回传 vm_iframe，避免被清）
    scanBranch.value = data.scope?.scan_branch || ''
    scopeVmIframe.value = data.scope?.vm_iframe || ''
    activePages.value = groupedRows.value.map((g) => g.name)   // 默认全部展开
  } catch { if (!disposed && version === listVersion) loadError.value = true }
  finally { if (!disposed && version === listVersion) loading.value = false }
  if (!disposed && version === listVersion) await loadModules()
}

// ---- 模块入口：登记每个模块从首页确定性到达的导航链 ----
const modules = ref([])
const moduleDlg = reactive({ visible: false, page: '', navText: '', readyKey: '', saving: false })

async function loadModules() {
  if (!pid.value) { modules.value = []; return }
  try { modules.value = await listModules(pid.value, subProduct.value) } catch { modules.value = [] }
}
function openModuleEdit(row) {
  Object.assign(moduleDlg, {
    visible: true, saving: false,
    page: row?.page || '', navText: (row?.nav_keys || []).join(','), readyKey: row?.ready_key || '',
  })
}
async function submitModule() {
  if (!moduleDlg.page.trim()) { ElMessage.warning('模块(page) 不能为空'); return }
  moduleDlg.saving = true
  try {
    await saveModule({
      project_id: pid.value, sub_product: subProduct.value, page: moduleDlg.page.trim(),
      nav_keys: moduleDlg.navText.split(',').map((s) => s.trim()).filter(Boolean),
      ready_key: moduleDlg.readyKey.trim(),
    })
    ElMessage.success('已保存模块入口'); moduleDlg.visible = false; await loadModules()
  } catch { /* 拦截器已提示 */ } finally { moduleDlg.saving = false }
}
async function onDeleteModule(row) {
  try { await ElMessageBox.confirm(`删除模块入口「${row.page}」？`, '删除', { type: 'warning' }) } catch { return }
  try { await deleteModule(row.id); ElMessage.success('已删除'); await loadModules() } catch { /* 已提示 */ }
}

// 按模块刷新:verify 该模块所有 key,失效的列出并提示去重探更新。需先选在线设备+客户端停在该模块页。
// runProbe 为轮询式(启动即返回,结果异步落 probe.result),故用一次性 watch 等 probe.running 归 false 再按本模块筛失效——
// 直接在 runProbe 后同步读 probe.result 会拿到 null。后端认不认 keys 参数都对:staleKeysFromVerify 在客户端按本模块 key 筛。
function refreshModule(row) {
  if (!probe.runner) { ElMessage.warning('先选在线设备，并让客户端停在该模块页面'); return }
  const mkeys = keysOfModule(rows.value, row.page)
  if (!mkeys.length) { ElMessage.info(`模块「${row.page}」下暂无 key`); return }
  runProbe('verify', { mode: 'verify', keys: mkeys.map((r) => r.key) })
  const stop = watch(() => probe.running, (running) => {
    if (running) return
    stop()
    if (probe.mode !== 'verify' || !probe.result) return   // 探测失败/超时,已由 runProbe 提示
    const stale = staleKeysFromVerify(probe.result.verify, mkeys)
    if (!stale.length) ElMessage.success(`模块「${row.page}」的 ${mkeys.length} 个 key 均有效`)
    else ElMessage.warning(`模块「${row.page}」有 ${stale.length} 个 key 失效：${stale.join('、')}。点下方失效行「重新探测更新」逐个刷新`)
  })

}

// ---- 运行时自学习候选评审 ----
const learned = reactive({ rows: [], status: 'pending', loading: false, error: false })

async function reloadLearned() {
  const version = ++learnedVersion
  learned.rows = []; learned.error = false; learned.loading = false
  if (!pid.value) { learned.rows = []; return }
  learned.loading = true
  try {
    const data = await listLearnedSelectors(pid.value, learned.status)
    if (!disposed && version === learnedVersion) learned.rows = data
  } catch { if (!disposed && version === learnedVersion) learned.error = true }
  finally { if (!disposed && version === learnedVersion) learned.loading = false }
}

async function reviewLearnedRow(row, action) {
  if (reviewing.value) return
  reviewing.value = true
  const label = action === 'approve' ? '转正' : '拒绝'
  try {
    await ElMessageBox.confirm(
      action === 'approve'
        ? `转正候选 ${row.candidate.by}=${row.candidate.value}？将去掉试用标、永久保留在「${row.key}」候选链中。`
        : `拒绝候选 ${row.candidate.by}=${row.candidate.value}？将从「${row.key}」注册表移除，且不再自动挂回。`,
      `${label}确认`, { type: action === 'approve' ? 'success' : 'warning', confirmButtonText: `确认${label}`, cancelButtonText: '取消' },
    )
  } catch { reviewing.value = false; return }
  try {
    if (disposed) return
    await reviewLearnedSelector(row.id, action)
    ElMessage.success(`已${label}`)
    if (!disposed) await reload()
  } catch { /* 拦截器已提示 */ } finally { reviewing.value = false }
}

function fmtTime(s) {
  if (!s) return '—'
  return String(s).replace('T', ' ').slice(0, 16)
}

// ---- 新增 / 编辑 ----
const dialog = reactive({ visible: false, id: null, key: '', frame: '', page: '', desc: '', candidatesText: '[]', platform: 'web', saving: false })

function openCreate() {
  Object.assign(dialog, { id: null, key: '', frame: 'auto', page: '', desc: '', candidatesText: '[]', platform: 'web', saving: false, visible: true })
}
function openEdit(row) {
  markSelectorSeen(row)
  Object.assign(dialog, {
    id: row.id, key: row.key, frame: row.frame || 'auto', page: row.page || '', desc: row.desc || '',
    candidatesText: JSON.stringify(row.candidates || [], null, 2), platform: row.platform || 'web', saving: false, visible: true,
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
  if (dialog.saving) return
  if (!dialog.id && !dialog.key.trim()) { ElMessage.warning('key 不能为空'); return }
  const candidates = parseCandidates()
  if (candidates === null) return
  dialog.saving = true
  try {
    if (dialog.id) {
      await patchSelector(dialog.id, { platform: dialog.platform, frame: dialog.frame || 'auto', page: dialog.page || '', desc: dialog.desc || '', candidates })
    } else {
      await createSelector({
        project_id: pid.value, sub_product: subProduct.value, platform: dialog.platform, key: dialog.key.trim(),
        frame: dialog.frame || 'auto', page: dialog.page || '', desc: dialog.desc || '', candidates,
      })
    }
    ElMessage.success('已保存')
    dialog.visible = false
    await reload()
    await autoBackfill()
  } catch { /* http 拦截器已提示（如 key 冲突）*/ }
  finally { dialog.saving = false }
}

// 保存 key 后联动:批量确定性回填「选择器待补」用例(纯校验不调 AI,静默失败不打扰保存主流程)。
// 补齐的 key 恰是某些用例缺的 → 它们自动恢复 gui/e2e,无需去用例库逐条点重生。
async function autoBackfill() {
  try {
    const res = await backfillTestcases(pid.value)
    if (res?.restored > 0) {
      ElMessage.success(`已联动回填 ${res.restored} 条「选择器待补」用例${res.remaining ? `,仍有 ${res.remaining} 条待补` : ''}`)
    }
  } catch { /* 回填失败不影响保存,用例库仍可逐条重生 */ }
}

// ---- 删除 ----
async function onDelete(row) {
  if (contextLocked.value) return
  deleting.value = true
  try {
  // 删除前查影响范围:被可执行用例引用时列出明细,告知将联动降级为「选择器待补」。
  let usage = null
  try { usage = await selectorUsage(row.id) } catch { ElMessage.warning('无法确认引用范围，暂未删除，请重试'); return }
  if (disposed) return
  const n = usage?.count || 0
  const detail = n
    ? `该 key 被 ${n} 条可执行用例引用：${usage.cases.slice(0, 5).map((c) => `「${c.title}」`).join('、')}${n > 5 ? ` 等 ${n} 条` : ''}。删除后这些用例将降级为「选择器待补」（重新补 key 可一键恢复）。`
    : ''
  try {
    await ElMessageBox.confirm(`删除选择器 key「${row.key}」？${detail}`, '删除', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' })
  } catch { return }
  try {
    if (disposed) return
    const res = await deleteSelector(row.id)
    if (disposed) return
    ElMessage.success(res?.downgraded ? `已删除,${res.downgraded} 条用例已降级为「选择器待补」` : '已删除')
    await reload()
  } catch { /* 已提示 */ }
  } finally { deleting.value = false }
}

// ---- 导入内置旧注册表（写入项目级共享）----
async function onImport() {
  if (contextLocked.value) return
  importing.value = true
  try {
    await ElMessageBox.confirm(
      '将内置纳米Work注册表导入为本项目【项目级共享】的 key（同名 key 跳过），确认？',
      '导入内置注册表', { type: 'warning', confirmButtonText: '确认导入', cancelButtonText: '取消' },
    )
  } catch { importing.value = false; return }
  try {
    if (disposed) return
    const res = await importLegacySelectors(pid.value)
    if (disposed) return
    ElMessage.success(`导入完成：新增 ${res.imported} 个，跳过 ${res.skipped} 个`)
    if (subProduct.value !== '') subProduct.value = ''   // 导入写入共享域，切过去看结果
    await reload()
  } catch { /* 已提示 */ }
  finally { importing.value = false }
}

// 单个 key 候选链上限：脆弱候选在尾，超出上限时 slice 自然丢弃最不稳的，防链膨胀/优先级倒置。
const MAX_CANDIDATES = 6

// ---- 保存扫描分支（当前作用域）----
async function saveScanBranch() {
  if (!pid.value) return
  savingBranch.value = true
  try {
    // 回传 scopeVmIframe，避免只存分支把已配的 vm_iframe 清掉。
    await setSelectorScope({
      project_id: pid.value, sub_product: subProduct.value,
      vm_iframe: scopeVmIframe.value, scan_branch: scanBranch.value.trim(),
    })
    ElMessage.success('已保存扫描分支')
  } catch { /* 拦截器已提示 */ } finally { savingBranch.value = false }
}

// ---- 批量删除选中的 key ----
async function onBatchDelete() {
  const ids = selectedIds.value
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(
      `确认批量删除选中的 ${ids.length} 个选择器 key？被可执行用例引用的将联动降级为「选择器待补」（重新补 key 可一键恢复）。`,
      '批量删除', { type: 'warning' },
    )
  } catch { return }
  batchDeleting.value = true
  try {
    const res = await batchDeleteSelectors(ids)
    ElMessage.success(`已删除 ${res.deleted} 个${res.downgraded ? `，${res.downgraded} 条用例降级为「选择器待补」` : ''}`)
    clearSelection()
    await reload()
    await autoBackfill()
  } catch { /* 已提示 */ } finally { batchDeleting.value = false }
}

// ---- 批量设置选中 key 的页面（page 分组）----
const batchPaging = ref(false)
async function onBatchSetPage() {
  const ids = selectedIds.value
  if (!ids.length) return
  let value
  try {
    ({ value } = await ElMessageBox.prompt(
      `把选中的 ${ids.length} 个选择器 key「页面」整体设为(逗号分隔多页；留空=清空为未分类)：`, '批量设页面',
      { inputPlaceholder: '如 会话', inputValue: '', confirmButtonText: '保存' }))
  } catch { return }
  batchPaging.value = true
  try {
    const res = await batchSetSelectorPage(ids, (value || '').trim())
    ElMessage.success(`已更新 ${res?.updated ?? ids.length} 个 key 的页面`)
    clearSelection()
    await reload()
  } catch { /* 已提示 */ } finally { batchPaging.value = false }
}
const imp = reactive({ visible: false, text: '', overwrite: false, saving: false })

function openImport() {
  Object.assign(imp, { visible: true, text: '', overwrite: false, saving: false })
}

// el-upload on-change：读取本地 .json 文件文本填入文本域（不真正上传）。
function onImpFile(file) {
  const raw = file.raw || file
  const reader = new FileReader()
  reader.onload = (e) => { imp.text = String(e.target.result || '') }
  reader.onerror = () => ElMessage.error('读取文件失败')
  reader.readAsText(raw)
}

// kebab-testid → camelCase key（与后端/脚本口径一致），供数组格式无 key 时兜底。
function toCamelKey(s) {
  const parts = String(s).split('-')
  return parts[0] + parts.slice(1).map((p) => p.slice(0, 1).toUpperCase() + p.slice(1)).join('')
}

// 把用户粘贴的 JSON 归一成后端要的 registry：支持 ①{registry:{...}} ②裸 {key:{...}} ③数组[{key,testid,...}]。
// 返回 { registry, vmIframe } 或抛错。
function normalizeImport(parsed) {
  if (Array.isArray(parsed)) {
    const registry = {}
    for (const it of parsed) {
      if (!it || typeof it !== 'object') continue
      const key = (it.key || toCamelKey(it.testid || '')).trim()
      if (!key) continue
      const candidates = Array.isArray(it.candidates)
        ? it.candidates
        : (it.testid ? [{ by: 'testid', value: it.testid }] : [])
      registry[key] = { frame: it.frame || 'vm', page: it.page || '', desc: it.desc || '', candidates }
    }
    return { registry, vmIframe: '' }
  }
  if (parsed && typeof parsed === 'object') {
    if (parsed.registry && typeof parsed.registry === 'object') {
      return { registry: parsed.registry, vmIframe: parsed.vmIframe || '' }
    }
    return { registry: parsed, vmIframe: '' }   // 裸 map
  }
  throw new Error('JSON 顶层须是对象或数组')
}

async function submitImport() {
  const raw = imp.text.trim()
  if (!raw) { ElMessage.warning('请粘贴或选择 JSON'); return }
  let parsed
  try { parsed = JSON.parse(raw) } catch { ElMessage.error('不是合法 JSON'); return }
  let norm
  try { norm = normalizeImport(parsed) } catch (e) { ElMessage.error(e.message || '格式无法识别'); return }
  const count = Object.keys(norm.registry || {}).length
  if (!count) { ElMessage.warning('未解析到任何 key'); return }
  imp.saving = true
  try {
    const res = await importSelectors({
      project_id: pid.value, sub_product: subProduct.value,
      registry: norm.registry, vm_iframe: norm.vmIframe, overwrite: imp.overwrite,
    })
    const inv = res.invalid?.length ? `，非法跳过 ${res.invalid.length}` : ''
    ElMessage.success(`导入完成：新增 ${res.imported}，覆盖 ${res.updated}，跳过 ${res.skipped}${inv}`)
    imp.visible = false
    await reload()
    await autoBackfill()
  } catch { /* 拦截器已提示 */ } finally { imp.saving = false }
}

// 「定位缺失 key」上下文（从用例库带 query 跳来）：待补的 key 列表 + 语义匹配上下文 + 当前选中的 key。
// bulk=true 时为「批量补选择器」模式:显示待补清单 + 探测后批量匹配/建 key(不逐个选 activeKey)。
const fixCtx = reactive({ keys: [], ctx: '', activeKey: '', bulk: false, done: [] })
const route = useRoute()

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
  if (probe.running || disposed) return
  const version = ++probeVersion
  if (!pid.value || !probe.runner) { ElMessage.warning('请先选择项目和在线设备'); return }
  stopPoll()
  probe.mode = mode
  if (mode !== 'box') lastBox.value = null   // 非框选:清掉上次的框选标注
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
  if (disposed || version !== probeVersion) return
  if (!id) { probe.running = false; ElMessage.error('发起探测失败'); return }

  const startedAt = Date.now()
  let polling = false
  pollTimer = setInterval(async () => {
    if (disposed || version !== probeVersion) return
    if (Date.now() - startedAt > 60000) {
      stopPoll(); ++probeVersion; probe.running = false
      ElMessage.error('探测超时（60s）：请确认设备 runner 在线且停留在目标页面')
      return
    }
    if (polling) return
    polling = true
    let r
    try { r = await getProbe(id) } catch { return /* 单次轮询失败忽略，等下次 */ } finally { polling = false }
    if (disposed || version !== probeVersion) return
    if (r.status === 'done') {
      stopPoll()
      probe.running = false; probe.done = true; probe.result = r.result || {}
      probe.screenshotUrl = r.screenshot_url || ''
      // 成功/失败提示(尤其框选:让用户明确知道识别到没有、识别了几个)。
      const n = (probe.result.groups || []).reduce((s, g) => s + (g.elements || []).length, 0)
      if (mode === 'box') {
        if (n) ElMessage.success(`框选识别到 ${n} 个元素，已在下方截图高亮标注(点框/点行可加为 key)`)
        else ElMessage.warning('框内未识别到可交互元素,试试把框画大一点或换个区域')
      } else if (mode === 'discover') {
        ElMessage.success(`探测完成,共识别 ${n} 个元素`)
      }
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

// ---- 框选探测:先「扫当前页」出截图,再在截图上拖框 → 对框内 DOM 放宽探测(穿 iframe/shadow)----
const boxMode = ref(false)          // 框选模式开关
const boxRect = ref(null)           // 拖拽中的矩形样式(overlay 百分比定位),null=未画
let boxStart = null                 // 拖拽起点(overlay 内像素)
const lastBox = ref(null)           // 上次提交的框选区(整页绝对坐标),供结果截图上标注"选中区"

function toggleBoxMode() {
  if (!boxMode.value) {
    // 需要先有截图才能框选(截图 = 页面绝对坐标基准)
    if (!probe.screenshotUrl || !probe.result?.pageSize?.w) {
      ElMessage.warning('请先点「探测(扫当前页)」出页面截图,再框选'); return
    }
    boxMode.value = true
  } else {
    boxMode.value = false; boxRect.value = null; boxStart = null
  }
}

function onBoxDown(e) {
  if (!boxMode.value) return
  const rect = e.currentTarget.getBoundingClientRect()
  boxStart = { x: e.clientX - rect.left, y: e.clientY - rect.top, ow: rect.width, oh: rect.height }
  boxRect.value = { left: '0%', top: '0%', width: '0%', height: '0%' }
}
function onBoxMove(e) {
  if (!boxMode.value || !boxStart) return
  const rect = e.currentTarget.getBoundingClientRect()
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top
  const x = Math.min(boxStart.x, cx), y = Math.min(boxStart.y, cy)
  const w = Math.abs(cx - boxStart.x), h = Math.abs(cy - boxStart.y)
  boxRect.value = {
    left: `${(x / boxStart.ow) * 100}%`, top: `${(y / boxStart.oh) * 100}%`,
    width: `${(w / boxStart.ow) * 100}%`, height: `${(h / boxStart.oh) * 100}%`,
  }
}
function onBoxUp(e) {
  if (!boxMode.value || !boxStart) return
  const rect = e.currentTarget.getBoundingClientRect()
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top
  const px = Math.min(boxStart.x, cx), py = Math.min(boxStart.y, cy)
  const pw = Math.abs(cx - boxStart.x), ph = Math.abs(cy - boxStart.y)
  const ow = boxStart.ow, oh = boxStart.oh
  boxStart = null
  if (pw < 5 || ph < 5) { boxRect.value = null; return }   // 太小视为误触
  // overlay 像素 → 整页绝对坐标(overlay 显示尺寸对应 pageSize)
  const ps = probe.result.pageSize
  const bbox = {
    x: Math.round((px / ow) * ps.w), y: Math.round((py / oh) * ps.h),
    w: Math.round((pw / ow) * ps.w), h: Math.round((ph / oh) * ps.h),
  }
  boxMode.value = false; boxRect.value = null
  lastBox.value = bbox   // 记住框选区,结果截图上标注选中区
  runProbe('box', { bbox, screenshot: true })   // 带截图:框内结果叠回整页图(同源 pageSize，坐标一致)
}

// ---- 探测元素 vs 已入库对比标识(#3)----
// 口径:按候选定位器 by+value 重叠判定。对当前作用域已登记的 key 建索引:
//   candKey(c) = `${by} ${value}` → 该候选属于哪个 key。
// 元素标识:
//   已存在(exists):元素 best 候选已在某 key 里(该 key 已能定位到它,无需再加)。
//   更新(update):元素与某 key 有共同候选、但 best 是新的(可把 best 补进该 key)。
//   新增(new):元素所有候选与所有 key 均无重叠。
const candKey = (c) => `${c.by} ${c.value}`

// 候选展示 label:by:'testid' 是我们候选结构里的内部简写,页面上开发写的实际属性是 data-testid;
// 故展示成 data-testid=<值>(更贴合真实 DOM),其余 by 原样 <by>=<值>(css/placeholder/label/text/role)。
const _BY_LABEL = { testid: 'data-testid' }
function candLabel(c) {
  if (!c || !c.by) return '—'
  return `${_BY_LABEL[c.by] || c.by}=${c.value}`
}

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
  if ((probe.mode !== 'discover' && probe.mode !== 'box') || !probe.result) return []
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
    // 「定位缺失 key」选中了某 key：按语义匹配度排序、给 Top3 打 _matchTop 高亮；否则按状态排序。
    if (fixCtx.activeKey) {
      const ranked = rankElements(fixCtx.activeKey, fixCtx.ctx, els)
      els = ranked.map((r, i) => ({ ...r.el, _matchScore: r.score, _matchTop: r.score > 0 && i < 3 }))
    } else {
      els.sort((a, b) => STATUS_ORDER[a._status.type] - STATUS_ORDER[b._status.type])
    }
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
  // 批量补选择器:把「待补 key ↔ 匹配到的探测元素」按 _uid 建编号索引,给对应框加醒目标注(编号+key)。
  const fixIdx = new Map()
  if (fixCtx.bulk) bulkMatches.value.forEach((m, i) => { if (m.el?._uid) fixIdx.set(m.el._uid, { key: m.key, no: i + 1 }) })
  const boxes = []
  for (const g of enrichedGroups.value) {
    for (const el of g.elements) {
      if (!el.absRect) continue
      const fix = fixIdx.get(el._uid) || null
      boxes.push({
        uid: el._uid, el, frameMatch: el._frameMatch, type: el._status.type, approx: !!el.absApprox,
        fixKey: fix ? fix.key : '', fixNo: fix ? fix.no : 0,
        label: fix
          ? `#${fix.no} ${fix.key}${el.best ? ` · ${candLabel(el.best)}` : ''}`
          : `${el.text || el.tag}${el.best ? ` · ${candLabel(el.best)}` : ''}${el.absApprox ? '（位置近似）' : ''}`,
        style: {
          left: `${(el.absRect.x / ps.w) * 100}%`, top: `${(el.absRect.y / ps.h) * 100}%`,
          width: `${(el.absRect.w / ps.w) * 100}%`, height: `${(el.absRect.h / ps.h) * 100}%`,
        },
      })
    }
  }
  return boxes
})

// 框选提交区的截图标注样式(整页绝对坐标 → 截图百分比,与 shotBoxes 同源 pageSize)。null=不画。
const selectedRegionStyle = computed(() => {
  const ps = probe.result?.pageSize
  const b = lastBox.value
  if (!ps || !ps.w || !ps.h || !b) return null
  return {
    left: `${(b.x / ps.w) * 100}%`, top: `${(b.y / ps.h) * 100}%`,
    width: `${(b.w / ps.w) * 100}%`, height: `${(b.h / ps.h) * 100}%`,
  }
})

// 表格行 class 高亮当前 hover 的元素；cell hover 设 hoverKey（框↔行双向联动）。
const rowClass = ({ row }) => {
  if (row._uid && row._uid === hoverKey.value) return 'row-hi'
  return row._matchTop ? 'row-match' : ''   // 「定位缺失 key」的高分匹配行
}
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
// cand=best 候选(展示/更新合并用);cands=全部候选(testid 优先 + css 兜底,新建时整串落库)。
// segTab/segScene/segElem=四段式 desc 的第1/3/4段(第2段=页面 add.page);desc 保存时由它们拼成。
const add = reactive({
  visible: false, mode: 'create', tag: '', type: '', text: '', frame: 'auto',
  cand: null, cands: [], key: '', page: '', desc: '',
  segTab: '', segScene: '', segElem: '', targetId: null, saving: false, status: null,
  // XPath 手动纠正:cssCount=该元素 CSS 类在本次探测里命中几个(≥2=多命中,建议 XPath);
  // xpath=用户采纳/编辑的 XPath 值(非空则并入候选,排在 css 之前);xpathAuto=自动生成的建议值。
  cssCount: 0, xpath: '', xpathAuto: '',
})

// 更新已有：目标 key 当前 row（取现有候选做对比预览）；仅 update 模式且选定目标时有值。
const addTarget = computed(() => (add.mode === 'update' && add.targetId ? rows.value.find((r) => r.id === add.targetId) || null : null))

// 合并后候选顺序预览：与 submitAddAsKey 的 update 分支完全一致（含 XPath 并入），标记新增项。
const addMergedPreview = computed(() => {
  if (!addTarget.value) return []
  const existing = addTarget.value.candidates || []
  const merged = updateMergedCandidates(existing)
  const isOld = (c) => existing.some((e) => e.by === c.by && e.value === c.value)
  return merged.map((c) => ({ ...c, _new: !isOld(c) }))
})

// 把探测候选归一成注册表存储的 {by,value}（丢弃 runner 内部的 sel/score）。
function toCand(c) {
  return c ? { by: c.by, value: c.value } : null
}

// 一个探测元素的**全部**候选 → 存储用 {by,value} 列表:去重、testid/css 稳定优先、脆弱(text/role)降尾、限长。
// 修复「加为 key 只存 best(testid)、丢了 css 兜底」——存 testid 优先 + css 兜底,执行期 testid 变了还能回落 css。
function toCands(el) {
  const raw = (el?.candidates || []).map(toCand).filter((c) => c && c.by && c.value)
  const seen = new Set()
  const uniq = raw.filter((c) => { const k = `${c.by} ${c.value}`; return seen.has(k) ? false : (seen.add(k), true) })
  return orderCandidates(uniq).slice(0, MAX_CANDIDATES)
}

// 推断控件类型(四段式第4段:输入框/下拉列表/按钮…)。优先按元素 tag/type 判原生控件;
// 自定义控件(div/span)按用例步骤/预期/key 名/元素文本等 hint 关键词推断;都判不出留空由人工填。
const _CT_RULES = [
  [/下拉|选择框|选择器|select|dropdown|picker/i, '下拉列表'],
  [/多行|文本域|textarea/i, '多行输入框'],
  [/勾选|复选|checkbox/i, '复选框'],
  [/单选|radio/i, '单选框'],
  [/搜索框|搜索输入|search/i, '搜索输入框'],
  [/输入|填写|文本框|input/i, '输入框'],
  [/切换|标签页|选项卡|\btab\b/i, 'Tab项'],
  [/菜单项|menu[-\s]?item/i, '菜单项'],
  [/下拉菜单|菜单|menu|dropdown-menu/i, '菜单容器'],
  [/弹窗|对话框|modal|dialog|popup/i, '弹窗容器'],
  [/链接|超链接|link/i, '链接'],
  [/复选|多选/i, '复选框'],
  [/按钮|提交|确认|保存|取消|删除|新建|btn|button|点击|click/i, '按钮'],
]
function inferControlType(el, hint = '') {
  const tag = (el?.tag || '').toLowerCase()
  const type = (el?.type || '').toLowerCase()
  if (tag === 'select') return '下拉列表'
  if (tag === 'textarea') return '多行输入框'
  if (tag === 'input') {
    if (type === 'checkbox') return '复选框'
    if (type === 'radio') return '单选框'
    if (['button', 'submit', 'reset'].includes(type)) return '按钮'
    if (type === 'search') return '搜索输入框'
    return '输入框'
  }
  for (const [re, label] of _CT_RULES) if (re.test(String(hint))) return label
  if (tag === 'a') return '链接'
  if (tag === 'button') return '按钮'
  return ''
}

// 新建模式的四段式 desc 实时预览/落库值：[导航Tab]-[页面(=add.page)]-[场景]-[控件类型]。
// 四段全空 → desc 置空(不硬塞空括号)。
const addComposedDesc = computed(() => {
  const segs = [add.segTab, add.page, add.segScene, add.segElem].map((s) => (s || '').trim())
  return segs.some((s) => s) ? segs.map((s) => `[${s}]`).join('-') : ''
})

function openAddAsKey(el, frame) {
  const cand = toCand(el.best)
  const cands = toCands(el)
  const status = matchStatus(el)   // #3 标识:exists/update/new
  const scene = (el.text || '').trim().slice(0, 16)
  // XPath 纠正:统计该元素 CSS 类在本次探测里命中几个;≥2=CSS 多命中(如同 class 的按钮),自动备一条 XPath。
  const allEls = enrichedGroups.value.flatMap((g) => g.elements)
  const cssCount = countCssMatches(allEls, cssSelectorValue(el))
  const xpathAuto = autoXPath(el)
  // best 是易多命中的 css(非 testid) 且确实多命中 → 默认采纳自动 XPath;否则留空(用户可手填)。
  const xpath = (cssCount >= 2 && cand && cand.by === 'css') ? xpathAuto : ''
  // 「定位缺失 key」模式:直接新建选中的那个待补 key(预填 key 名),不走更新预置。
  // 控件类型推断:先按「元素 tag/type + 元素自身文本」(最贴合该元素),判不出再退到用例步骤上下文。
  if (fixCtx.activeKey) {
    const segElem = inferControlType(el, el.text || '')
      || inferControlType({}, `${fixCtx.ctx || ''} ${fixCtx.activeKey || ''}`)
    Object.assign(add, {
      visible: true, saving: false, status,
      tag: el.tag, type: el.type || '', text: el.text || '', frame: frame || 'auto',
      cand, cands, mode: 'create', key: fixCtx.activeKey, page: probe.page || '', targetId: null,
      segTab: '', segScene: scene, segElem, cssCount, xpathAuto, xpath,
    })
    return
  }
  // 预置更新目标优先级:verify 的「重新探测更新」预置 > 对比标识命中的已有 key。
  const presetByVerify = probe.updateTarget && rows.value.find((r) => r.key === probe.updateTarget)
  const presetByMatch = status.key && rows.value.find((r) => r.key === status.key)
  const preset = presetByVerify || presetByMatch
  Object.assign(add, {
    visible: true, saving: false, status,
    tag: el.tag, type: el.type || '', text: el.text || '', frame: frame || 'auto',
    cand, cands,
    // exists/update/有预置 → 默认更新已有;new → 默认新建。
    mode: preset ? 'update' : 'create',
    key: '', page: probe.page || '', targetId: preset ? preset.id : null,
    segTab: '', segScene: scene, segElem: inferControlType(el, el.text || ''),
    cssCount, xpathAuto, xpath,
  })
}

// 新建时最终落库的候选:把用户采纳/编辑的 XPath 并入,排在 css 之前(orderCandidates 已给 xpath 固定档)。
function addCreateCandidates() {
  const base = (add.cands && add.cands.length) ? add.cands.slice() : (add.cand ? [add.cand] : [])
  const xp = (add.xpath || '').trim()
  if (!xp) return base
  const withXp = [{ by: 'xpath', value: xp }, ...base.filter((c) => !(c.by === 'xpath' && c.value === xp))]
  return orderCandidates(withXp).slice(0, MAX_CANDIDATES)
}

// 更新已有 key 时的合并候选:把 best + (可选)XPath 并入目标 key 现有候选;
// 去重、脆弱同 by 就地替换、orderCandidates 排序(testid>xpath>css>脆弱)、限长。新建/更新共用此口径。
function updateMergedCandidates(existing) {
  const list = existing || []
  const xp = (add.xpath || '').trim()
  const nc = add.cand
  const isDup = (c) => (nc && c.by === nc.by && c.value === nc.value) || (xp && c.by === 'xpath' && c.value === xp)
  const dropSameFragile = (c) => nc && isFragile(nc) && c.by === nc.by
  const kept = list.filter((c) => !isDup(c) && !dropSameFragile(c))
  const head = []
  if (xp) head.push({ by: 'xpath', value: xp })
  if (nc) head.push(nc)
  return orderCandidates([...head, ...kept]).slice(0, MAX_CANDIDATES)
}

async function submitAddAsKey() {
  if (add.saving) return
  if (!add.cand) { ElMessage.error('该元素没有可用候选'); return }
  if (add.mode === 'create' && !add.key.trim()) { ElMessage.warning('key 名不能为空'); return }
  if (add.mode === 'update' && !add.targetId) { ElMessage.warning('请选择要更新的已有 key'); return }
  add.saving = true
  try {
    if (add.mode === 'create') {
      // 存全部候选(testid > xpath > css 兜底;xpath 为用户采纳/编辑的精确定位);desc 用四段式拼装值。
      const candidates = addCreateCandidates()
      await createSelector({
        project_id: pid.value, sub_product: subProduct.value, platform: 'web', key: add.key.trim(),
        frame: add.frame || 'auto', page: add.page || '', desc: addComposedDesc.value, candidates,
      })
      ElMessage.success('已新建 key')
    } else {
      const target = rows.value.find((r) => r.id === add.targetId)
      const existing = target?.candidates || []
      // 合并 best +（可选）XPath 到目标 key 现有候选;去重、脆弱同 by 就地替换、排序(testid>xpath>css>脆弱)、限长。
      const merged = updateMergedCandidates(existing)
      await patchSelector(add.targetId, { candidates: merged })
      ElMessage.success('已更新已有 key 的候选')
      if (target && probe.updateTarget === target.key) probe.updateTarget = ''
    }
    add.visible = false
    await reload()
    await autoBackfill()
  } catch { /* http 拦截器已提示（如 key 冲突）*/ }
  finally { add.saving = false }
}

// ---- 批量补选择器(bulk 模式):把本次探测元素批量匹配到「尚未补齐」的待补 key ----
// 复用 matchElementsToKeys(纯逻辑,已单测):对每个待补 key 贪心配一个最匹配的探测元素。
// 仅取「有 best 候选、matchStatus=new(库里还没有)、且已配上元素」的 key —— 遵循已有覆盖(exists 跳过)、
// 没有的新建。返回 [{key, el, cand, score}],供面板预览与一键批量建。
const bulkMatches = computed(() => {
  if (!fixCtx.bulk) return []
  const remaining = fixCtx.keys.filter((k) => !fixCtx.done.includes(k))
  if (!remaining.length) return []
  const els = enrichedGroups.value.flatMap((g) => g.elements.map((el) => ({ ...el, _frame: el._frameMatch })))
  const withBest = els.filter((el) => el.best && matchStatus(el).type === 'new')  // 已存在的元素不重复建
  return matchElementsToKeys(remaining, fixCtx.ctx, withBest)
    .filter((p) => p.el && p.el.best)
    .map((p) => ({ key: p.key, el: p.el, cand: toCand(p.el.best), frame: p.el._frame || 'auto', score: p.score }))
})

const bulkAdding = ref(false)

// 批量补 chip 信息:key → { no 编号, uid 联动高亮, label 展示(data-testid=…) }。
// 与 shotBoxes 的 fixNo 同源(bulkMatches 顺序),使 chip 编号 ↔ 截图框编号一致。
const bulkChipInfo = computed(() => {
  const m = new Map()
  bulkMatches.value.forEach((x, i) => {
    m.set(x.key, { no: i + 1, uid: x.el?._uid || '', label: x.cand ? candLabel(x.cand) : '' })
  })
  return m
})

// 一键批量建 key:对 bulkMatches 里每对逐个 createSelector(key 名唯一,候选取元素 best)。
// 逐个建而非批量端点——量小(单次探测匹配上的 key 通常个位数),且复用后端已有唯一约束/校验。
async function batchAddMatched() {
  const matches = bulkMatches.value
  if (!matches.length) { ElMessage.warning('本次探测没有匹配到可新建的待补 key,试试切到目标页/弹窗再探'); return }
  bulkAdding.value = true
  let ok = 0
  const failed = []
  try {
    for (const m of matches) {
      try {
        // 存全部候选(testid 优先 + css 兜底);desc 四段式,控件类型先按元素文本、再退到用例上下文推断。
        const scene = (m.el.text || '').trim().slice(0, 16)
        const elem = inferControlType(m.el, m.el.text || '')
          || inferControlType({}, `${fixCtx.ctx || ''} ${m.key || ''}`)
        const desc = (probe.page || scene || elem) ? `[]-[${probe.page || ''}]-[${scene}]-[${elem}]` : ''
        const created = await createSelector({
          project_id: pid.value, sub_product: subProduct.value, platform: 'web', key: m.key,
          frame: m.frame || 'auto', page: probe.page || '', desc, candidates: toCands(m.el),
        })
        markSelectorNew(created)
        ok += 1
        if (!fixCtx.done.includes(m.key)) fixCtx.done.push(m.key)
      } catch { failed.push(m.key) }  // 单个失败(如 key 冲突)不阻断其余
    }
    const remain = fixCtx.keys.filter((k) => !fixCtx.done.includes(k))
    ElMessage.success(`已批量新建 ${ok} 个 key${failed.length ? `,${failed.length} 个失败(${failed.join(',')})` : ''}`
      + `${remain.length ? `;还剩 ${remain.length} 个待补,可切到对应页/弹窗继续探测` : ',全部补齐！'}`)
    await reload()
    await autoBackfill()
  } finally { bulkAdding.value = false }
}
</script>

<style scoped>
.registry-key { display: inline-flex; align-items: center; gap: 6px; }
.new-selector-dot { flex: 0 0 8px; width: 8px; height: 8px; padding: 0; border: 0; border-radius: 50%; background: var(--el-color-danger, #f56c6c); cursor: pointer; }
.new-selector-dot:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 3px; }
.header { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
.header > span { flex-shrink: 0; }
.header .filters { min-width: 0; }
@media (max-width: 700px) { .header .filters { width: 100%; } .header .filters .el-select, .header .filters .el-input { max-width: 100%; } }
.page-title { font-weight: 600; margin-right: 8px; }
.page-count { vertical-align: middle; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.form-hint { color: #90a4ae; font-size: 12px; }
.registry-card { margin-top: 16px; }
.scan-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; padding: 8px 10px; background: #f5f7fa; border-radius: 4px; }
.scan-label { font-size: 13px; color: #606266; font-weight: 600; }
.imp-toolbar { display: flex; gap: 16px; align-items: center; margin-bottom: 8px; }
.probe-scope { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; color: #607d8b; font-size: 13px; }
.probe-group { margin-bottom: 14px; }
.probe-group-head { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.probe-group-url { color: #607d8b; font-size: 12px; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.probe-el-text { margin-left: 6px; }
.add-preview { background: #f5f7fa; border-radius: 4px; padding: 10px 12px; }
.add-cand { margin-top: 6px; }
.add-cands { margin-top: 6px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
.cand-chip { font-family: var(--tech-mono, monospace); }
.seg-row { display: flex; align-items: center; gap: 4px; width: 100%; }
.seg-sep { color: #909399; }
.xpath-alert { margin-bottom: 8px; }
.xpath-suggest { cursor: pointer; color: #409eff; word-break: break-all; }
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
/* 批量补选择器:待补 key 匹配到的元素框——橙色实线醒目框 + 左上角编号角标,与上方 chip 编号/联动一致 */
.el-box.box-fix { border: 2px solid #e6a23c; background: rgba(230,162,60,.14); box-shadow: 0 0 0 1px rgba(230,162,60,.4); z-index: 3; }
.el-box.box-fix.active { background: rgba(230,162,60,.3); box-shadow: 0 0 0 3px rgba(230,162,60,.55); z-index: 4; }
.box-fix-no { position: absolute; top: -9px; left: -9px; min-width: 16px; height: 16px; padding: 0 3px; box-sizing: border-box; background: #e6a23c; color: #fff; font-size: 11px; line-height: 16px; text-align: center; border-radius: 8px; font-weight: 700; pointer-events: none; }
/* 框选模式:overlay 接管鼠标(盖住元素框),十字光标;拖拽出的矩形 */
.shot-overlay.box-selecting { pointer-events: auto; cursor: crosshair; }
.shot-overlay.box-selecting .el-box { pointer-events: none; }
.box-select-rect { position: absolute; box-sizing: border-box; border: 2px dashed #e6a23c; background: rgba(230,162,60,.15); z-index: 5; pointer-events: none; }
/* 框选提交区标注:聚光灯高亮"你选的这块"(overflow:hidden 把外扩遮罩裁在截图内)。z-index:0 垫底,
   命中的元素框(el-box,DOM 在其后)叠加显示在上层,一眼看清"选了这里 → 识别到这几个元素" */
.box-selected-region { position: absolute; box-sizing: border-box; border: 2px solid #409eff; background: rgba(64,158,255,.08); box-shadow: 0 0 0 9999px rgba(0,0,0,.14); border-radius: 3px; z-index: 0; pointer-events: none; }
:deep(.el-table .row-hi) { background: #ecf5ff; }
:deep(.el-table .row-match) { background: #fdf6ec; }
:deep(.el-table .row-match td:first-child) { box-shadow: inset 3px 0 0 0 #e6a23c; }
.fix-bar { margin: 8px 0; }
.fix-bar-in { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.fix-bar-hint { font-size: 12px; color: #7a5b00; }
.fix-keys-chips { display: flex; flex-wrap: wrap; gap: 4px; width: 100%; }
.chip-tid { opacity: .75; font-family: var(--tech-mono, monospace); }
.chip-hover { box-shadow: 0 0 0 2px rgba(230,162,60,.6); }
.fix-bar-actions { display: flex; align-items: center; gap: 10px; width: 100%; }
.locate-key-btn { margin-left: 4px; }
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
.learned-card { margin-top: 16px; }
.learned-badge { margin-left: 6px; }
.learned-intro { margin-bottom: 12px; }
</style>
