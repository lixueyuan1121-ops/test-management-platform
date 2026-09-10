<template>
  <div class="eval-results">
    <header class="page-heading">
      <h1>对话测评结果</h1>
      <el-select v-model="pid" aria-label="选择项目" placeholder="选择项目" class="project-select" @change="onProjectChange">
        <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
    </header>
    <el-tabs v-model="activeView" class="view-tabs" aria-label="测评视图">
      <el-tab-pane label="结果明细" name="results" />
      <el-tab-pane label="分析概览" name="analysis" />
    </el-tabs>
    <section v-show="activeView === 'analysis'" class="analysis-view" aria-label="分析概览">
    <div class="scope-label">当前项目 · 能力画像近 30 天 · 趋势按批次 · 判定质量按人工复核</div>
    <el-empty v-if="!dimStats.dims.length && trend.length < 2 && !quality.overall?.reviewed" description="暂无可分析的数据" :image-size="70" />
    <!-- 维度能力画像雷达(选中项目且有判定数据时显示) -->
    <div v-if="dimStats.dims.length" class="dr-panel">
      <div class="dr-head">
        <h2 class="dr-eyebrow">测评维度能力画像</h2>
        <div class="dr-overall">
          <span class="dr-rate">{{ dimStats.overall_rate }}<span class="dr-u">%</span></span>
          <span class="dr-lbl">综合通过率 · {{ dimStats.judged_total }} 条判定</span>
        </div>
      </div>
      <div class="dr-body">
        <!-- 维度 ≥ 3 用雷达图；< 3 退化为水平条形 -->
        <div v-if="dimStats.dims.length >= 3" ref="radarEl" class="dr-chart"></div>
        <div v-else class="dr-bars">
          <div v-for="d in dimStats.dims" :key="d.dimension" class="dr-bar-row">
            <span class="dr-bar-lbl">{{ dimLabel(d.dimension) }}</span>
            <div class="dr-bar-track">
              <div class="dr-bar-fill" :style="{ width: d.pass_rate + '%', background: drColor(d.pass_rate) }"></div>
            </div>
            <span class="dr-bar-val" :style="{ color: drColor(d.pass_rate) }">{{ d.pass_rate }}%</span>
          </div>
        </div>
        <div class="dr-dims">
          <div v-for="d in dimStats.dims" :key="d.dimension" class="dr-dim">
            <span class="dr-dim-dot" :style="{ background: drColor(d.pass_rate) }"></span>
            <span class="dr-dim-name">{{ dimLabel(d.dimension) }}</span>
            <span class="dr-dim-rate" :style="{ color: drColor(d.pass_rate) }">{{ d.pass_rate }}%</span>
            <span class="dr-dim-n">({{ d.total }})</span>
          </div>
        </div>
      </div>
    </div>
    <!-- 批次趋势:每批次一个点(通过率+均分),回答「比上次强吗」;≥2 批才有趋势可看 -->
    <div v-if="trend.length >= 2" class="tr-panel">
      <div class="tr-head">
        <h2 class="dr-eyebrow">批次趋势（近 {{ trend.length }} 批）</h2>
        <div class="tr-legend">
          <span class="tr-lg"><i class="tr-dot tr-dot-rate"></i>通过率%</span>
          <span class="tr-lg"><i class="tr-dot tr-dot-score"></i>均分(1-5)</span>
        </div>
      </div>
      <div ref="trendEl" class="tr-chart"></div>
    </div>
    <!-- 判定质量:人工复核反推 AI 判定准不准(样本少的引擎不列,明细标注在展开区) -->
    <div v-if="quality.overall?.reviewed > 0" class="jq-panel">
      <div class="jq-head">
        <h2 class="dr-eyebrow">判定质量</h2>
        <div class="jq-hint">基于人工复核</div>
      </div>
      <div class="jq-cards">
        <div class="jq-card">
          <div class="jq-n" :class="qAccClass(quality.overall.accuracy)">{{ quality.overall.accuracy ?? '—' }}<span class="jq-u">%</span></div>
          <div class="jq-l">判定准确率（{{ quality.overall.confirmed }}/{{ quality.overall.reviewed }} 复核）</div>
        </div>
        <div class="jq-card"><div class="jq-n warn">{{ quality.overall.fp_rate ?? '—' }}%</div><div class="jq-l">误报率</div></div>
        <div class="jq-card"><div class="jq-n danger">{{ quality.overall.fn_rate ?? '—' }}%</div><div class="jq-l">漏报率</div></div>
        <div class="jq-card">
          <div class="jq-n">{{ quality.overall.review_rate ?? '—' }}%</div>
          <div class="jq-l">复核覆盖率（{{ quality.overall.reviewed }}/{{ quality.overall.judged }} 已判定）</div>
        </div>
      </div>
      <div v-if="quality.by_engine?.length" class="jq-rows">
        <div v-for="e in quality.by_engine" :key="e.engine" class="jq-row">
          <span class="jq-eng">{{ e.engine }}</span>
          <span class="jq-bar"><i :style="{ width: (e.accuracy ?? 0) + '%' }" :class="qAccClass(e.accuracy)"></i></span>
          <span class="jq-val" :class="qAccClass(e.accuracy)">{{ e.accuracy ?? '—' }}%</span>
          <span class="jq-sub">{{ e.confirmed }}✓ / {{ e.false_positive }}误 / {{ e.false_negative }}漏（{{ e.reviewed }} 条复核）</span>
        </div>
      </div>
      <div v-else-if="!quality.by_engine?.length" class="jq-note">
        暂无产品维度的复核样本
      </div>
    </div>
    </section>
    <section v-show="activeView === 'results'" class="results-view" aria-label="结果明细">
      <div class="result-summary" aria-label="当前加载执行统计">
        <div><span>当前加载</span><strong>{{ rows.length }}</strong></div>
        <div><span>已判定</span><strong>{{ judgedCount }}</strong></div>
        <div><span>待判定</span><strong>{{ doneCount }}</strong></div>
        <div><span>异常</span><strong class="summary-alert">{{ abnormalCount }}</strong></div>
      </div>
      <div class="result-toolbar" role="region" aria-label="结果筛选与操作">
        <div class="toolbar-mobile-head">
          <span>结果操作</span>
          <el-button size="small" :icon="Filter" :aria-expanded="mobileToolsOpen" aria-controls="result-toolbar-options" @click="mobileToolsOpen = !mobileToolsOpen">{{ mobileToolsOpen ? '收起筛选与操作' : '筛选与操作' }}</el-button>
        </div>
        <div id="result-toolbar-options" class="header toolbar-options" :class="{ 'mobile-open': mobileToolsOpen }">
          <div class="filters">
            <el-input v-model="searchText" :prefix-icon="Search" aria-label="搜索用例或提问" placeholder="搜索用例或提问" clearable class="result-search" />
            <el-select v-model="batchFilter" aria-label="筛选批次" placeholder="全部批次" size="small" clearable filterable style="width:210px" @change="load">
              <el-option v-for="b in batchOptions" :key="b.batch_id"
                :label="`${b.batch_id}${b.task_name ? ' · ' + b.task_name : ''}`" :value="b.batch_id" />
            </el-select>
            <el-select v-model="verdictFilter" aria-label="筛选判定" placeholder="全部判定" size="small" clearable style="width:110px">
              <el-option label="通过" value="pass" />
              <el-option label="不通过" value="fail" />
              <el-option label="待复核 / 判定出错" value="error" />
              <el-option label="未判定" value="__none__" />
            </el-select>
            <el-select v-if="engineOptions.length > 1" v-model="engineFilter" aria-label="筛选产品" placeholder="全部产品" size="small" clearable style="width:120px">
              <el-option v-for="e in engineOptions" :key="e" :label="ENGINE_LABEL[e] || e" :value="e" />
            </el-select>
            <el-tooltip content="刷新结果" placement="top"><el-button size="small" :icon="Refresh" aria-label="刷新结果" @click="load" /></el-tooltip>
          </div>
          <div class="batch-actions">
            <el-checkbox v-model="robustJudge" size="small" class="robust-ck">
              <el-tooltip content="每条判 3 次取多数票（更稳，但 3 倍耗时）" placement="top"><span>稳健(3票)</span></el-tooltip>
            </el-checkbox>
            <el-button
              size="small" type="primary" :icon="DataAnalysis" :loading="batchJudging"
              :disabled="!pid || !judgeableCount"
              @click="batchJudge"
            >{{ batchJudging && batchProgress ? batchProgress : `批量判定（${judgeableCount}）` }}</el-button>
            <el-popconfirm v-if="failedCount" :title="`重跑当前列表全部 ${failedCount} 条失败？`" width="240" @confirm="retryAllFailed">
              <template #reference>
                <el-button size="small" type="warning" plain>重跑失败（{{ failedCount }}）</el-button>
              </template>
            </el-popconfirm>
            <el-button
              size="small" :icon="Upload"
              :disabled="!pid"
              @click="exportDialogVisible = true"
            >导出到飞书</el-button>
          </div>
        </div>
      <div class="selection-status">
        <el-checkbox aria-label="全选当前结果（工具栏）" :model-value="allPushSelected" :indeterminate="somePushSelected && !allPushSelected" :disabled="pushingMultica || !selectableRuns.length" @change="checked => selectPushRuns(selectableRuns, checked)">全选</el-checkbox>
        <span class="visible-count">{{ groupedRows.length }} 个会话</span>
        <span>已选 {{ selectedRunIds.length }} 条对话</span>
        <el-button v-if="selectedRunIds.length" text size="small" :disabled="pushingMultica" @click="selectedRunIds = []">清空选择</el-button>
        <el-button class="push-action" size="small" :icon="Promotion" :loading="pushingMultica" :disabled="!pid || !selectedRunIds.length" @click="doPushMultica">推送到 Multica（{{ selectedRunIds.length }}）</el-button>
      </div>
      </div>

      <el-empty v-if="!groupedRows.length" :description="loading ? '加载中…' : '暂无测评执行记录'" :image-size="70" />

      <el-table v-else :data="groupedRows" v-loading="loading" size="small" border stripe row-key="run_id"
        :row-class-name="({ row }) => row.isGroup ? 'conversation-row' : 'single-run'"
        :tree-props="{ children: '_unusedChildren' }" :expand-row-keys="expanded" @expand-change="onExpand">
        <el-table-column width="42">
          <template #header><el-checkbox aria-label="全选当前结果" :model-value="allPushSelected" :indeterminate="somePushSelected && !allPushSelected" :disabled="pushingMultica || !selectableRuns.length" @change="checked => selectPushRuns(selectableRuns, checked)" /></template>
          <template #default="{ row }"><el-checkbox :aria-label="`选择 ${queryTitle(row)}`" :model-value="pushChecked(row)" :indeterminate="pushPartial(row)" :disabled="pushingMultica || !pushLeaves(row).length" @change="checked => selectPushRuns(pushLeaves(row), checked)" /></template>
        </el-table-column>
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="verdict-detail">
              <div v-if="row.isGroup" class="no-dims">
                <el-text type="info">
                  {{ row.children.length }} 轮对话 · {{ groupTurnSummary(row) }} · 批次 {{ row.batch_id || '—' }}
                </el-text>
                <el-table :data="row.children" row-key="run_id" border size="small">
                  <el-table-column width="42"><template #default="{ row: turn }"><el-checkbox :aria-label="`选择第${(turn.payload?.turn_index ?? 0) + 1}轮`" :model-value="selectedRunIds.includes(turn.run_id)" :disabled="pushingMultica || !!turn.pushed_multica" @change="checked => selectPushRuns([turn], checked)" /></template></el-table-column>
                  <el-table-column label="轮次" width="65"><template #default="{ row: turn }">{{ (turn.payload?.turn_index ?? 0) + 1 }}</template></el-table-column>
                  <el-table-column prop="run_id" label="执行ID" width="80" />
                  <el-table-column label="提问" min-width="200"><template #default="{ row: turn }"><button class="case-link" @click="openInspector(turn)">{{ turn.payload?.prompt || queryTitle(turn) }}</button></template></el-table-column>
                  <el-table-column label="回答摘要" min-width="220"><template #default="{ row: turn }"><div class="text-preview">{{ turn.answer || turn.reason || '暂无回答' }}</div></template></el-table-column>
                  <el-table-column label="判定" min-width="160"><template #default="{ row: turn }"><el-tag :type="VERDICT_TYPE[turn.verdict] || 'info'" size="small">{{ VERDICT_LABEL[turn.verdict] || STATUS_LABEL[turn.status] }}</el-tag><div class="text-preview">{{ turn.verdict_reason || '—' }}</div></template></el-table-column>
                  <el-table-column label="Multica" width="90"><template #default="{ row: turn }">{{ turn.pushed_multica ? '已推送' : '未推送' }}</template></el-table-column>
                  <el-table-column label="详情" width="66" fixed="right"><template #default="{ row: turn }"><el-tooltip content="查看结果详情"><el-button text :icon="Document" :aria-label="`查看执行 ${turn.run_id} 详情`" @click="openInspector(turn)" /></el-tooltip></template></el-table-column>
                </el-table>
              </div>
              <div v-else-if="row.status === 'failed'" class="no-dims">
                <el-text type="danger">执行失败：{{ row.reason || '（执行机未回写失败原因）' }}</el-text>
              </div>
              <div v-else-if="!row.verdict_dims" class="no-dims">
                <el-text type="info">尚未判定或无三维结果。点右侧「判定」触发。</el-text>
              </div>
              <template v-else>
                <div class="dims">
                  <div v-for="d in rowDims(row)" :key="d.k" class="dim">
                    <div class="dim-head">
                      <el-icon v-if="dimPass(row, d.k) === true" class="ok"><CircleCheck /></el-icon>
                      <el-icon v-else-if="dimPass(row, d.k) === false" class="ng"><CircleClose /></el-icon>
                      <el-icon v-else class="unk"><QuestionFilled /></el-icon>
                      <span class="dim-label">{{ d.label }}</span>
                    </div>
                    <div class="dim-note" v-html="renderMd(dimNote(row, d.k))"></div>
                  </div>
                </div>
                <div v-if="row.verdict_dims.summary" class="summary">
                  <b>判定小结：</b><span v-html="renderMd(row.verdict_dims.summary)"></span>
                </div>
              </template>
              <!-- 人工复核(失败收敛):对 AI 判定标 认可/误报/漏报,误报自动摘异常、漏报置异常 -->
              <div v-if="row.verdict && !row.isGroup" class="review-bar">
                <span class="review-lbl">人工复核：</span>
                <el-button size="small" :type="row.review_mark === 'confirmed' ? 'success' : ''" @click="doReview(row, 'confirmed')">认可判定</el-button>
                <el-button size="small" :type="row.review_mark === 'false_positive' ? 'warning' : ''" @click="doReview(row, 'false_positive')">误报（实际通过）</el-button>
                <el-button size="small" :type="row.review_mark === 'false_negative' ? 'danger' : ''" @click="doReview(row, 'false_negative')">漏报（实际有问题）</el-button>
                <el-button v-if="row.review_mark" size="small" text @click="doReview(row, null)">清除</el-button>
                <span v-if="row.review_note" class="review-note">备注：{{ row.review_note }}</span>
              </div>
              <!-- 原始 message：WorkBuddy「复制 message」抓到的完整结构化 JSON(含思维链/traceId/modelId 等),供分析。 -->
              <div v-if="!row.isGroup && row.raw_message" class="raw-msg">
                <el-collapse>
                  <el-collapse-item :title="`原始 message（复制 message · ${rawMsgSize(row.raw_message)}）`" name="raw">
                    <pre class="raw-msg-pre">{{ prettyRawMessage(row.raw_message) }}</pre>
                  </el-collapse-item>
                </el-collapse>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="#" width="84" align="center">
          <template #default="{ row }">
            <span v-if="row.isGroup" class="dim-muted">—</span>
            <el-tooltip v-else content="执行记录编号(run id)" placement="top" :show-after="500">
              <span>{{ row.run_id }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="用例 / 会话" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <template v-if="row.isGroup">
              <el-tag size="small" type="warning" effect="plain" class="turn-tag">多轮 ×{{ row.children.length }}</el-tag>{{ queryTitle(row) }}
            </template>
            <template v-else>
              <el-tag v-if="row._inGroup" size="small" effect="plain" class="turn-tag">第{{ (row.payload?.turn_index ?? 0) + 1 }}轮</el-tag><button class="case-link" @click="openInspector(row)">{{ queryTitle(row) }}</button>
              <el-tag v-if="row.pushed_multica" size="small" type="success" effect="plain">Multica 已推送</el-tag>
              <div class="reason-preview">{{ row.verdict_reason || row.reason || '暂无判定理由' }}</div>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="维度" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.dimension" :type="DIM_TAG_TYPE[row.dimension] || 'info'" size="small" effect="plain">{{ dimLabel(row.dimension) }}</el-tag>
            <span v-else class="dim-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="产品" width="112" align="center">
          <template #default="{ row }">
            <el-tag v-if="!row.isGroup && row.target_engine" size="small" effect="plain" type="info">{{ ENGINE_LABEL[row.target_engine] || row.target_engine }}</el-tag>
            <span v-else class="dim-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="执行" width="96" align="center">
          <template #default="{ row }">
            <el-tag :type="STATUS_TYPE[row.status] || 'info'" size="small" effect="plain">{{ STATUS_LABEL[row.status] || row.status || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="判定" width="96" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.verdict" :type="VERDICT_TYPE[row.verdict] || 'info'" size="small">{{ VERDICT_LABEL[row.verdict] || row.verdict }}</el-tag>
            <span v-else class="dim-muted">—</span>
            <el-tooltip v-if="row.review_mark" :content="REVIEW_LABEL[row.review_mark] + (row.review_note ? '：' + row.review_note : '')" placement="top">
              <span class="review-flag" :class="'rf-' + row.review_mark">{{ REVIEW_ICON[row.review_mark] }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="评分" width="60" align="center">
          <template #default="{ row }">
            <span v-if="rowScore(row) != null" class="score" :class="scoreClass(rowScore(row))">{{ rowScore(row) }}</span>
            <span v-else class="dim-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="上报耗时" width="110" align="center">
          <template #default="{ row }"><span v-if="!row.isGroup && row.reported_duration != null">{{ row.reported_duration }}{{ /^\d+(\.\d+)?$/.test(String(row.reported_duration)) ? ' 秒' : '' }}</span><span v-else class="dim-muted">—</span></template>
        </el-table-column>
        <el-table-column label="异常" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_abnormal" type="danger" size="small" effect="dark">异常</el-tag>
            <span v-else class="dim-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="分享" width="70" align="center">
          <template #default="{ row }">
            <el-link v-if="safeUrl(row.share_link)" type="primary" :href="safeUrl(row.share_link)" target="_blank" rel="noopener noreferrer">会话</el-link>
            <span v-else class="dim-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="对比" width="96" align="center">
          <template #default="{ row }">
            <!-- A/B 对比配对行:点开左右分栏并排看答辩+判定 -->
            <el-button v-if="!row.isGroup && row.payload?.compare_group" size="small" type="warning" text
              @click="openAbCompare(row)">A/B</el-button>
            <!-- 跨产品对比:同题在本批有 ≥2 个产品的 run 时可并排比 -->
            <el-button v-if="!row.isGroup && hasEngineCompare(row)" size="small" type="primary" text
              @click="openEngineCompare(row)">产品</el-button>
            <span v-if="!row.isGroup && !row.payload?.compare_group && !hasEngineCompare(row)" class="dim-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center" fixed="right">
          <template #default="{ row }">
            <el-tooltip v-if="!row.isGroup" content="查看结果详情"><el-button text :icon="Document" :aria-label="`查看执行 ${row.run_id} 详情`" @click="openInspector(row)" /></el-tooltip>
            <el-popconfirm v-if="!row.isGroup && row.status === 'failed'"
              title="重跑该条？(复位回待执行，执行机将重新拉走)" width="240" @confirm="retryOne(row)">
              <template #reference><el-button size="small" type="warning" text>重跑</el-button></template>
            </el-popconfirm>
            <el-button
              v-else-if="!row.isGroup"
              size="small" type="primary" text
              :loading="judgingIds.has(row.run_id)"
              :disabled="!canJudge(row)"
              @click="judgeOne(row)"
            >{{ row.verdict ? '重判' : '判定' }}</el-button>
            <span v-else class="dim-muted">逐轮判</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="foot-hint">当前加载 {{ rows.length }} 条执行 · 已判定 {{ judgedCount }} 条 · 异常 {{ abnormalCount }} 条</div>
    </section>

    <EvalRunInspector v-model:visible="inspectorVisible" :row="inspectedRun" :title="queryTitle(inspectedRun)">
      <template #review>
        <div v-if="inspectedRun" class="inspector-review">
          <el-button v-for="(label, mark) in REVIEW_LABEL" :key="mark" size="small" :type="inspectedRun.review_mark === mark ? 'primary' : ''" @click="doReview(inspectedRun, mark)">{{ mark === 'confirmed' ? '认可判定' : label }}</el-button>
          <el-button v-if="inspectedRun.review_mark" text size="small" @click="doReview(inspectedRun, null)">清除</el-button>
          <div v-if="inspectedRun.review_note" class="review-note">{{ inspectedRun.review_note }}</div>
        </div>
      </template>
      <template #actions>
        <template v-if="inspectedRun">
          <el-checkbox :model-value="selectedRunIds.includes(inspectedRun.run_id)" :disabled="pushingMultica || !!inspectedRun.pushed_multica" @change="checked => selectPushRuns([inspectedRun], checked)">{{ inspectedRun.pushed_multica ? 'Multica 已推送' : '加入推送选择' }}</el-checkbox>
          <el-popconfirm v-if="inspectedRun.status === 'failed'" title="重跑该条执行？" @confirm="retryOne(inspectedRun)"><template #reference><el-button type="warning" plain>重跑</el-button></template></el-popconfirm>
          <el-button v-else type="primary" :icon="DataAnalysis" :disabled="!canJudge(inspectedRun)" :loading="judgingIds.has(inspectedRun.run_id)" @click="judgeOne(inspectedRun)">{{ inspectedRun.verdict ? '重新判定' : '判定' }}</el-button>
        </template>
      </template>
    </EvalRunInspector>

    <el-dialog v-model="exportDialogVisible" title="导出到飞书表" width="480px">
      <el-form label-width="100px">
        <el-form-item label="飞书表链接" required>
          <el-input v-model="exportSheetUrl" placeholder="粘贴目标飞书表格链接" clearable />
        </el-form-item>
        <el-form-item label="仅异常">
          <el-checkbox v-model="exportAbnormalOnly">只导出判定异常的会话</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="exportDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="exporting" @click="doExportFeishu">导出</el-button>
      </template>
    </el-dialog>

    <!-- A/B 并排对比(LMArena 式):同一题两套配置的答辩+判定并列,胜因一目了然 -->
    <el-dialog v-model="abCompareVisible" :title="`A/B 对比 · ${queryTitle(abPair.a)}`" width="920px" top="6vh">
      <div v-if="abPair.a || abPair.b" class="ab-wrap">
        <template v-for="side in ['a', 'b']" :key="side">
          <div class="ab-col" :class="abWin(side) ? 'ab-win' : ''">
            <div class="ab-hd">
              <span class="ab-tag" :class="side === 'a' ? 'ab-tag-a' : 'ab-tag-b'">{{ side === 'a' ? 'A' : 'B' }}</span>
              <span class="ab-opts">{{ abOpts(abPair[side]) }}</span>
              <span class="ab-verdict">{{ abPair[side]?.verdict ? VERDICT_LABEL[abPair[side].verdict] : '未判定' }}</span>
            </div>
            <div class="ab-body">
              <div class="ab-sec">回答</div>
              <div class="ab-text">{{ abPair[side]?.answer || '—' }}</div>
              <div class="ab-sec">判定理由</div>
              <div class="ab-text">{{ abPair[side]?.verdict_reason || '—' }}</div>
              <div v-if="abPair[side]?.score != null" class="ab-score">评分 {{ abPair[side].score }}/5</div>
              <el-link v-if="safeUrl(abPair[side]?.share_link)" type="primary" :href="safeUrl(abPair[side].share_link)" target="_blank" rel="noopener noreferrer">打开会话</el-link>
            </div>
          </div>
        </template>
        <div class="ab-prompt">
          <div class="ab-sec">题干 prompt</div>
          <div class="ab-text">{{ abPair.a?.payload?.prompt || abPair.b?.payload?.prompt || '—' }}</div>
          <div class="ab-sec">期望 expected</div>
          <div class="ab-text">{{ abPair.a?.payload?.expected || abPair.b?.payload?.expected || '—' }}</div>
        </div>
      </div>
    </el-dialog>

    <el-dialog v-model="engineCompareVisible" :title="`产品对比 · ${queryTitle(enginePairList[0])}`" width="920px" top="6vh">
      <div v-if="enginePairList.length" class="ab-wrap">
        <div v-for="r in enginePairList" :key="r.run_id" class="ab-col" :class="engineWin(r) ? 'ab-win' : ''">
          <div class="ab-hd">
            <span class="ab-tag ab-tag-a">{{ ENGINE_LABEL[r.target_engine] || r.target_engine }}</span>
            <span class="ab-opts">{{ engineModel(r) }}</span>
            <span class="ab-verdict">{{ r.verdict ? VERDICT_LABEL[r.verdict] : '未判定' }}</span>
          </div>
          <div class="ab-body">
            <div class="ab-sec">回答</div>
            <div class="ab-text">{{ r.answer || '—' }}</div>
            <div class="ab-sec">判定理由</div>
            <div class="ab-text">{{ r.verdict_reason || '—' }}</div>
            <div v-if="r.score != null" class="ab-score">评分 {{ r.score }}/5</div>
            <el-link v-if="safeUrl(r.share_link)" type="primary" :href="safeUrl(r.share_link)" target="_blank" rel="noopener noreferrer">打开会话</el-link>
          </div>
        </div>
        <div class="ab-prompt">
          <div class="ab-sec">题干 prompt</div>
          <div class="ab-text">{{ enginePairList[0]?.payload?.prompt || '—' }}</div>
          <div class="ab-sec">期望 expected</div>
          <div class="ab-text">{{ enginePairList[0]?.payload?.expected || '—' }}</div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, DataAnalysis, Upload, Promotion, CircleCheck, CircleClose, QuestionFilled, Search, Document, Filter } from '@element-plus/icons-vue'
import EvalRunInspector from '@/components/EvalRunInspector.vue'
import { listEvalRuns, judgeEvalRun, judgeEvalBatch, notifyEvalJudgeBatchDone, pollAiJobs, exportEvalFeishu, pushEvalMultica, evalMulticaPending, evalDimensionStats, listEvalDimensions, evalBatchTrend, reviewEvalRun, evalJudgeQuality, retryEvalRunAny, retryFailedEvalRuns } from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { groupEvalRuns } from '@/utils/evalRunGroups'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

// 判定理由 markdown 渲染:AI 常输出代码块/列表/加粗,纯文本可读性差。
// html:false 禁内联 HTML + DOMPurify 消毒,双保险防 XSS(判定理由经 LLM 生成,视作不可信输入)。
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const renderMd = (text) => text ? DOMPurify.sanitize(md.render(String(text))) : '—'

// 判定核心三维（键与后端 parse_eval_verdict 一致）；dimension_ok 第四维按行动态追加
const DIMS = [
  { k: 'thinking_complete', label: '思考推理' },
  { k: 'tools_ok', label: '工具 / MCP 调用' },
  { k: 'artifact_expected', label: '产物 / 答案' },
]
// 行内展示的判定维度：核心三维 + 该行有 dimension_ok 时补一格「主考维度」
const rowDims = (row) => {
  const base = [...DIMS]
  if (row.verdict_dims?.dimension_ok) {
    base.push({ k: 'dimension_ok', label: `主考维度${row.dimension ? '·' + dimLabel(row.dimension) : ''}` })
  }
  return base
}
// 测评维度注册表(服务端拉取,雷达/列表统一用中文标签;失败用内置兜底)
const DIM_META = ref({
  thinking: '思考推理', tool_use: '工具·MCP调用', artifact: '产物生成',
  multi_turn: '多轮追问', instruction: '指令遵循',
  workflow: '工作流', clarification: '反问澄清', context: '上下文记忆',
  safety: '安全合规', refusal: '拒答质量',
  hallucination: '事实可靠', creativity: '创意生成', consistency: '一致性',
})
const dimLabel = (k) => (k ? (DIM_META.value[k] || k) : '未标注')
const DIM_TAG_TYPE = {
  thinking: 'primary', tool_use: 'success', artifact: 'warning', multi_turn: 'danger', instruction: 'info',
  workflow: 'warning', clarification: 'primary', context: 'success', safety: 'danger', refusal: 'info',
  hallucination: 'warning', creativity: 'primary', consistency: 'success',
}
// eval_run 生命周期（EvalRunStatus）
const STATUS_LABEL = { pending: '待执行', running: '执行中', done: '待判定', judging: '判定中', judged: '已判定', failed: '执行失败' }
const STATUS_TYPE = { pending: 'info', running: 'primary', done: 'primary', judging: 'primary', judged: 'info', failed: 'danger' }
// 总判定（EvalVerdict 值 pass/fail/error）：pass 绿 / fail 红 / error 灰
const VERDICT_LABEL = { pass: '通过', fail: '不通过', error: '待复核' }
const VERDICT_TYPE = { pass: 'success', fail: 'danger', error: 'info' }
// 人工复核标记(失败收敛)
const REVIEW_LABEL = { confirmed: '已认可判定', false_positive: '误报（实际通过）', false_negative: '漏报（实际有问题）' }
const REVIEW_ICON = { confirmed: '✓', false_positive: '误', false_negative: '漏' }

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const rows = ref([])
const searchText = ref('')
const mobileToolsOpen = ref(false)
const inspectorVisible = ref(false)
const inspectedRunId = ref(null)
const inspectedRun = computed(() => rows.value.find(r => r.run_id === inspectedRunId.value) || null)
function openInspector(row) {
  inspectedRunId.value = row.run_id
  inspectorVisible.value = true
}
watch(inspectedRun, row => { if (!row) inspectorVisible.value = false })
const loading = ref(false)
const activeView = ref('results')
const verdictFilter = ref(null)
const engineFilter = ref(null)
// 批次筛选(选项复用 trend 批次列表,最新在前);清空=全部批次
const batchFilter = ref(null)
const batchOptions = computed(() => [...trend.value].reverse())
const expanded = ref([])
const selectedRunIds = ref([])
const pushLeaves = row => (row.isGroup ? row.children : [row]).filter(r => !r.pushed_multica)
const selectableRuns = computed(() => groupedRows.value.flatMap(pushLeaves))
const allPushSelected = computed(() => selectableRuns.value.length > 0 && selectableRuns.value.every(r => selectedRunIds.value.includes(r.run_id)))
const somePushSelected = computed(() => selectableRuns.value.some(r => selectedRunIds.value.includes(r.run_id)))
const pushChecked = row => pushLeaves(row).length > 0 && pushLeaves(row).every(r => selectedRunIds.value.includes(r.run_id))
const pushPartial = row => !pushChecked(row) && pushLeaves(row).some(r => selectedRunIds.value.includes(r.run_id))
function selectPushRuns(runs, checked) {
  const ids = new Set(runs.map(r => r.run_id))
  selectedRunIds.value = checked
    ? [...new Set([...selectedRunIds.value, ...ids])]
    : selectedRunIds.value.filter(id => !ids.has(id))
}
const judgingIds = ref(new Set())
const batchJudging = ref(false)
const batchProgress = ref('')   // 批量判定进度文案「判定中 done/total」
const robustJudge = ref(false)

// 导出飞书 / 推送 multica
const exportDialogVisible = ref(false)
const exportSheetUrl = ref('')
const exportAbnormalOnly = ref(false)
const exporting = ref(false)
const pushingMultica = ref(false)
const multicaPending = ref(0)

// done 状态（已执行完待判定）条数：批量判定针对这些
const doneCount = computed(() => rows.value.filter((r) => r.status === 'done').length)
// 批量判定的实际范围:done(待判)+ judged(已判,允许按新标准重判);仅排除 pending/running/failed/cancelled。
const judgeableCount = computed(() => rows.value.filter((r) => r.status === 'done' || r.status === 'judged').length)
const failedCount = computed(() => rows.value.filter((r) => r.status === 'failed').length)
const judgedCount = computed(() => rows.value.filter((r) => r.verdict).length)
const abnormalCount = computed(() => rows.value.filter((r) => r.is_abnormal).length)
const matchFilter = (r) => {
  const term = searchText.value.trim().toLocaleLowerCase()
  if (term && !`${queryTitle(r)} ${r.payload?.prompt || ''}`.toLocaleLowerCase().includes(term)) return false
  if (engineFilter.value && r.target_engine !== engineFilter.value) return false
  if (!verdictFilter.value) return true
  if (verdictFilter.value === '__none__') return !r.verdict
  return r.verdict === verdictFilter.value
}

// 多轮会话分组(公共逻辑见 utils/evalRunGroups):组行树形展开,单轮原样平铺
const groupedRows = computed(() => groupEvalRuns(rows.value, matchFilter))

// row 可空容错:A/B 弹窗 title 在 abPair 尚为 {a:null} 时就会求值,不容错整页渲染崩溃(数据区全空)
const queryTitle = (row) => row?.payload?.title || row?.payload?.prompt || `query #${row?.eval_query_id ?? '—'}`
// 只放行 http(s) 链接（share_link 经 CLI 抓取回写，防 javascript: 等危险 scheme 的 XSS）
const safeUrl = (u) => /^https?:\/\//i.test(u || '') ? u : null
const prettyRawMessage = (raw) => {
  const s = String(raw || '')
  try { return JSON.stringify(JSON.parse(s), null, 2) } catch { return s }
}
const rawMsgSize = (raw) => {
  const n = String(raw || '').length
  return n >= 1000 ? ((n / 1000).toFixed(1) + 'k 字符') : (n + ' 字符')
}
// A/B 并排对比:同 eval_query_id + compare_mode 配对的 A/B 两条;弹窗左右分栏
const abCompareVisible = ref(false)
const abPair = ref({ a: null, b: null })

function openAbCompare(row) {
  const g = row.payload?.compare_group
  if (!g) return
  const pair = { a: null, b: null }
  // 配对必须限定同批次(与多轮分组同一教训):同题多批执行时各批都有 A/B,
  // 不限批次会拿到别批的对家——张冠李戴地对比两次不同执行
  for (const r of rows.value) {
    if (r.batch_id !== row.batch_id || r.eval_query_id !== row.eval_query_id) continue
    if (r.payload?.compare_group === 'A') pair.a = r
    if (r.payload?.compare_group === 'B') pair.b = r
  }
  abPair.value = pair
  abCompareVisible.value = true
}

function abOpts(r) {
  if (!r) return ''
  const d = r.payload?.dialog_options || {}
  const parts = [d.model, d.chatMode, d.thinkingDepth && `深:${d.thinkingDepth}`].filter(Boolean)
  return parts.join(' · ') || '客户端默认'
}

function abWin(side) {
  const a = abPair.value.a?.verdict, b = abPair.value.b?.verdict
  if (side === 'a') return a === 'pass' && b === 'fail'
  return b === 'pass' && a === 'fail'
}

// ── 跨产品对比(target_engine 轴,正交于 A/B) ──
const ENGINE_LABEL = { namiwork: '纳米Work', workbuddy: 'WorkBuddy' }
const engineCompareVisible = ref(false)
const enginePairList = ref([])
// 本次结果里出现过的产品(供筛选下拉;>1 才显示筛选/对比入口)
const engineOptions = computed(() => [...new Set(rows.value.map(r => r.target_engine).filter(Boolean))])
// 同题在本批有 ≥2 个不同产品的 run → 可产品对比
function _enginePeers(row) {
  return rows.value.filter(r => !r.isGroup && r.batch_id === row.batch_id && r.eval_query_id === row.eval_query_id && r.target_engine)
}
function hasEngineCompare(row) {
  if (!row.target_engine || !row.eval_query_id) return false
  return new Set(_enginePeers(row).map(r => r.target_engine)).size >= 2
}
function openEngineCompare(row) {
  // 按 target_engine 去重取每产品一条(同产品多条取首条),稳定按产品名排序
  const seen = new Map()
  for (const r of _enginePeers(row)) {
    if (!seen.has(r.target_engine)) seen.set(r.target_engine, r)
  }
  enginePairList.value = [...seen.values()].sort((a, b) => (a.target_engine > b.target_engine ? 1 : -1))
  engineCompareVisible.value = true
}
function engineModel(r) {
  // 实际用的模型:trace 里 WorkBuddy footer 抓的 model,或 dialog_options.model 兜底
  return r?.trace?.model || r?.payload?.dialog_options?.model || ENGINE_LABEL[r?.target_engine] || '—'
}
// 高亮:本产品 pass 且存在别的产品 fail(相对更优)
function engineWin(r) {
  if (r.verdict !== 'pass') return false
  return enginePairList.value.some(o => o !== r && o.verdict === 'fail')
}

const dimPass = (row, k) => row.verdict_dims?.[k]?.pass
const dimNote = (row, k) => row.verdict_dims?.[k]?.note
// 评分(1-5,判定引擎给):组行取各轮均分(1 位小数),真实行取 score
const rowScore = (row) => {
  if (!row.isGroup) return row.score ?? null
  const ss = (row.children || []).map((t) => t.score).filter((s) => s != null)
  return ss.length ? +(ss.reduce((a, b) => a + b, 0) / ss.length).toFixed(1) : null
}
const scoreClass = (s) => (s >= 4 ? 'score-hi' : s >= 3 ? 'score-mid' : 'score-lo')
// 执行完成（done）或已判过（judged/有 verdict）才可判/重判；未跑完（pending/running/failed）不可判
const canJudge = (row) => row.status === 'done' || row.status === 'judged' || !!row.verdict

onMounted(async () => {
  // 维度注册表(标签映射)与项目列表并行拉;注册表失败沿用内置兜底
  const [projRes, dimRes] = await Promise.allSettled([app.fetchProjects(), listEvalDimensions()])
  if (dimRes.status === 'fulfilled' && dimRes.value?.dimensions?.length) {
    DIM_META.value = Object.fromEntries(dimRes.value.dimensions.map((d) => [d.key, d.label]))
  }
  projects.value = projRes.status === 'fulfilled' ? (projRes.value || []) : []
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await onProjectChange()
  }
})

async function onProjectChange() {
  inspectorVisible.value = false
  inspectedRunId.value = null
  searchText.value = ''
  verdictFilter.value = null
  batchFilter.value = null
  expanded.value = []
  selectedRunIds.value = []
  if (!pid.value) { rows.value = []; multicaPending.value = 0; return }
  setLastProjectId(pid.value)
  await load()
  loadDimStats()   // 维度雷达:独立加载不阻塞列表
  loadTrend()      // 批次趋势:同上
  loadQuality()    // 判定质量:同上
}

async function load() {
  if (!pid.value) return
  loading.value = true
  try {
    rows.value = await listEvalRuns(pid.value, batchFilter.value || undefined)
    selectedRunIds.value = selectedRunIds.value.filter(id => rows.value.some(r => r.run_id === id && !r.pushed_multica))
    refreshMulticaPending()
  } finally { loading.value = false }
}

// 待推 multica 数（用于 badge）；失败静默（拦截器已提示），不阻断主流程。
async function refreshMulticaPending() {
  if (!pid.value) { multicaPending.value = 0; return }
  try {
    const res = await evalMulticaPending(pid.value)
    multicaPending.value = res.pending || 0
  } catch { /* http 拦截器已提示 */ }
}

function onExpand(row, expandedRows) {
  expanded.value = expandedRows.map((r) => r.run_id)
}

// 单条判定：调 /eval-judge/{run_id}，完成后把返回的判定结果就地合并进该行（避免整表刷新丢展开态）。
async function judgeOne(row) {
  if (judgingIds.value.has(row.run_id)) return
  judgingIds.value = new Set(judgingIds.value).add(row.run_id)
  try {
    const res = await judgeEvalRun(row.run_id)
    Object.assign(row, {
      status: res.status ?? row.status,
      verdict: res.verdict ?? null,
      verdict_dims: res.verdict_dims ?? null,
      score: res.score ?? null,
      verdict_reason: res.verdict_reason ?? null,
      is_abnormal: !!res.is_abnormal,
    })
    if (res.verdict === 'error') ElMessage.warning(res.verdict_reason || '判定出错，可重试')
    else ElMessage.success(`判定完成：${VERDICT_LABEL[res.verdict] || res.verdict}`)
  } catch (e) { ElMessage.error(e?.message || '判定失败') }
  finally {
    const s = new Set(judgingIds.value); s.delete(row.run_id); judgingIds.value = s
  }
}

// 批量判定改入队(方案2 P2):后端每条 run 建一个 job,前端轮询这批 job;完成后整表刷新。
async function batchJudge() {
  if (!pid.value || !judgeableCount.value) return
  batchJudging.value = true
  try {
    // 显式传当前视图里 done/judged 的 run_ids(judged 允许按新标准重判):后端 /batch 传了 run_ids
    // 就按 id 精确圈定,不走「默认只判 done」的范围(那会漏掉已判过的)。范围=当前批次筛选所见。
    const runIds = rows.value
      .filter((r) => (r.status === 'done' || r.status === 'judged') && Number.isInteger(r.run_id))
      .map((r) => r.run_id)
    if (!runIds.length) { ElMessage.info('没有可判定的执行项（done/judged 状态）'); return }
    const res = await judgeEvalBatch({ project_id: pid.value, run_ids: runIds,
      votes: robustJudge.value ? 3 : 1 })
    if (!res.count) { ElMessage.info('没有可判定的执行项'); return }
    const results = await pollAiJobs(res.job_ids, {
      onProgress: ({ done, total }) => { batchProgress.value = `判定中 ${done}/${total}` },
    })
    const errs = results.filter((x) => x.error || x.verdict === 'error').length
    ElMessage.success(`已判定 ${res.count} 条${errs ? `（${errs} 条失败）` : ''}`)
    // 整批判完补推推推通知;仅当这批 run 同属一个测评任务时带 task_id(→带在线报告链接),跨任务则不带。
    const judgedRows = rows.value.filter((r) => runIds.includes(r.run_id))
    const taskIds = [...new Set(judgedRows.map((r) => r.eval_task_id).filter(Boolean))]
    notifyEvalJudgeBatchDone({ project_id: pid.value, task_id: taskIds.length === 1 ? taskIds[0] : null,
      judged: res.count, failed: errs }).catch(() => {})
    await load()
  } catch (e) { ElMessage.error(e?.message || '批量判定失败') }
  finally { batchJudging.value = false; batchProgress.value = '' }
}

// 导出到飞书：填目标表链接 +（可选）仅异常，调 /eval-export/feishu。
async function doExportFeishu() {
  if (!exportSheetUrl.value) { ElMessage.warning('请填飞书表链接'); return }
  exporting.value = true
  try {
    const res = await exportEvalFeishu({ project_id: pid.value, sheet_url: exportSheetUrl.value, abnormal_only: exportAbnormalOnly.value })
    ElMessage.success(`已导出 ${res.exported} 行到飞书表`)
    exportDialogVisible.value = false
  } catch { /* http 拦截器已提示 */ }
  finally { exporting.value = false }
}

// 仅推送选中的真实 run,成功项刷新后移出勾选,失败项保留以便重试。
async function doPushMultica() {
  if (!selectedRunIds.value.length || pushingMultica.value) return
  if (selectedRunIds.value.length > 200) { ElMessage.warning('单次最多推送200条，请减少勾选'); return }
  pushingMultica.value = true
  try {
    const res = await pushEvalMultica({ project_id: pid.value, run_ids: [...selectedRunIds.value] })
    const failed = (res.results || []).filter(r => r.error || r.skipped)
    const message = `推送成功 ${res.pushed}/${res.candidates} 条` + (failed.length ? `；${failed.length} 条未推送：${failed[0].error || failed[0].skipped}` : '')
    if (failed.length || !res.pushed) ElMessage.warning(message)
    else ElMessage.success(message)
    await load()
  } catch { /* http 拦截器已提示 */ }
  finally { pushingMultica.value = false }
}

// 组行展开区的各轮状态汇总,如「3 通过 / 1 不通过 / 2 待判定」
function groupTurnSummary(row) {
  const turns = row.children || []
  const n = (f) => turns.filter(f).length
  const parts = []
  const pass = n((t) => t.verdict === 'pass'); if (pass) parts.push(`${pass} 通过`)
  const fail = n((t) => t.verdict === 'fail'); if (fail) parts.push(`${fail} 不通过`)
  const err = n((t) => t.verdict === 'error'); if (err) parts.push(`${err} 判定出错`)
  const failedExec = n((t) => t.status === 'failed'); if (failedExec) parts.push(`${failedExec} 执行失败`)
  const waiting = n((t) => !t.verdict && t.status !== 'failed'); if (waiting) parts.push(`${waiting} 待判定/待执行`)
  return parts.join(' / ') || '—'
}

// 批量重跑当前列表全部 failed(限定当前批次筛选;不传 run_ids 让后端按范围扫)
async function retryAllFailed() {
  try {
    const res = await retryFailedEvalRuns({
      project_id: pid.value,
      batch_id: batchFilter.value || null,
      run_ids: rows.value.filter((r) => r.status === 'failed').map((r) => r.run_id),
    })
    ElMessage.success(`已复位 ${res.retried} 条待执行，执行机将重新拉走`)
    await load()
  } catch { /* 拦截器已提示 */ }
}

// 单条重跑(failed → pending):就地更新该行状态,执行机下轮轮询拉走
async function retryOne(row) {
  try {
    const res = await retryEvalRunAny(row.run_id)
    Object.assign(row, { status: res.status, reason: res.reason, verdict: res.verdict, score: res.score,
      verdict_dims: res.verdict_dims, verdict_reason: res.verdict_reason, is_abnormal: !!res.is_abnormal,
      review_mark: res.review_mark, review_note: res.review_note })
    ElMessage.success(`run ${row.run_id} 已复位待执行，执行机将重新拉走`)
  } catch { /* 拦截器已提示 */ }
}

// 人工复核:点已选中的标记 = 无操作;新标记弹备注框(可空);mark=null 清除。就地更新该行。
async function doReview(row, mark) {
  if (mark && row.review_mark === mark) return
  let note = ''
  if (mark) {
    try {
      const r = await ElMessageBox.prompt('复核备注（可空，说明误判原因便于迭代题目期望/判定规则）', REVIEW_LABEL[mark], {
        confirmButtonText: '保存', cancelButtonText: '取消', inputValue: row.review_note || '',
      })
      note = r.value || ''
    } catch { return }  // 取消
  }
  try {
    const res = await reviewEvalRun(row.run_id, mark, note)
    Object.assign(row, { review_mark: res.review_mark, review_note: res.review_note, is_abnormal: !!res.is_abnormal })
    ElMessage.success(mark ? `已标注：${REVIEW_LABEL[mark]}` : '已清除复核标注')
  } catch { /* 拦截器已提示 */ }
}

// ==== 维度能力画像雷达 ====
import * as echarts from 'echarts/core'
import { RadarChart, LineChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
echarts.use([RadarChart, LineChart, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer])

const dimStats = ref({ dims: [], judged_total: 0, overall_rate: 0 })
const radarEl = ref(null)
let radarChart = null

function drColor(r) { return r >= 90 ? '#15803d' : r >= 70 ? '#b76a08' : '#c83e4d' }

function drawRadar() {
  if (activeView.value !== 'analysis' || !radarEl.value || dimStats.value.dims.length < 3) return
  if (radarChart && radarChart.getDom() !== radarEl.value) { radarChart.dispose(); radarChart = null }
  if (!radarChart) radarChart = echarts.init(radarEl.value)
  const dims = dimStats.value.dims
  radarChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', formatter: (p) => p.data.value.map((v, i) => `${dimLabel(dims[i].dimension)}: ${v}%`).join('<br/>') },
    radar: {
      indicator: dims.map((d) => ({ name: dimLabel(d.dimension), max: 100 })),
      radius: '65%',
      splitArea: { areaStyle: { color: ['rgba(37,99,235,.05)', 'rgba(37,99,235,.02)'] } },
      axisName: { color: '#7d8a9b', fontSize: 12 },
      splitLine: { lineStyle: { color: 'rgba(37,99,235,.2)' } },
      axisLine: { lineStyle: { color: 'rgba(37,99,235,.2)' } },
    },
    series: [{
      type: 'radar',
      data: [{ name: '通过率', value: dims.map((d) => d.pass_rate) }],
      symbol: 'circle', symbolSize: 5,
      lineStyle: { color: '#2563eb', width: 2 },
      areaStyle: { color: 'rgba(37,99,235,.18)' },
      itemStyle: { color: '#2563eb' },
    }],
  })
}

async function loadDimStats() {
  if (!pid.value) { dimStats.value = { dims: [], judged_total: 0, overall_rate: 0 }; return }
  try { dimStats.value = await evalDimensionStats(pid.value) } catch { /* 静默 */ }
  await nextTick()
  drawRadar()
}

// ==== 批次趋势(通过率+均分双轴折线) ====
const trend = ref([])
const trendEl = ref(null)
let trendChart = null

// ==== 判定质量(复核反推) ====
const quality = ref({ overall: null, by_engine: [] })
const qAccClass = (v) => (v == null ? 'q-mid' : v >= 90 ? 'q-hi' : v >= 75 ? 'q-mid' : 'q-lo')

async function loadQuality() {
  if (!pid.value) { quality.value = { overall: null, by_engine: [] }; return }
  try {
    const res = await evalJudgeQuality(pid.value)
    quality.value = { overall: res.overall?.reviewed > 0 ? res.overall : null, by_engine: res.by_engine || [] }
  } catch { /* 静默 */ }
}

function drawTrend() {
  if (activeView.value !== 'analysis' || !trendEl.value || trend.value.length < 2) return
  if (trendChart && trendChart.getDom() !== trendEl.value) { trendChart.dispose(); trendChart = null }
  if (!trendChart) trendChart = echarts.init(trendEl.value)
  const bs = trend.value
  const x = bs.map((b) => (b.date || '').slice(5, 16).replace('T', ' '))
  trendChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (ps) => {
        const b = bs[ps[0]?.dataIndex]
        if (!b) return ''
        return `<b>${b.task_name || '题库下发'}</b> · ${b.batch_id}<br/>`
          + `${(b.date || '').replace('T', ' ').slice(0, 19)}<br/>`
          + `判定 ${b.judged}/${b.total} · 通过率 ${b.pass_rate ?? '—'}%<br/>`
          + `均分 ${b.avg_score ?? '—'}/5`
      },
    },
    grid: { left: 44, right: 44, top: 16, bottom: 28 },
    xAxis: { type: 'category', data: x, axisLabel: { color: '#7d8a9b', fontSize: 10 }, axisLine: { lineStyle: { color: '#dce2e7' } } },
    yAxis: [
      { type: 'value', min: 0, max: 100, axisLabel: { color: '#7d8a9b', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#edf0f3' } } },
      { type: 'value', min: 0, max: 5, axisLabel: { color: '#b76a08' }, splitLine: { show: false } },
    ],
    series: [
      { name: '通过率', type: 'line', data: bs.map((b) => b.pass_rate), smooth: true, connectNulls: true,
        symbol: 'circle', symbolSize: 6, lineStyle: { color: '#2563eb', width: 2 }, itemStyle: { color: '#2563eb' },
        areaStyle: { color: 'rgba(37,99,235,.08)' } },
      { name: '均分', type: 'line', yAxisIndex: 1, data: bs.map((b) => b.avg_score), smooth: true, connectNulls: true,
        symbol: 'circle', symbolSize: 5, lineStyle: { color: '#b76a08', width: 2, type: 'dashed' }, itemStyle: { color: '#b76a08' } },
    ],
  })
}

async function loadTrend() {
  if (!pid.value) { trend.value = []; return }
  try { trend.value = (await evalBatchTrend(pid.value))?.batches || [] } catch { trend.value = [] }
  await nextTick()
  drawTrend()
}

function resizeCharts() {
  if (activeView.value !== 'analysis') return
  radarChart?.resize()
  trendChart?.resize()
}
watch(activeView, async () => {
  await nextTick()
  drawRadar()
  drawTrend()
  resizeCharts()
})
// Sidebar and viewport changes both affect the available chart width.
let chartObserver
onMounted(() => {
  chartObserver = new ResizeObserver(resizeCharts)
  const container = document.querySelector('.analysis-view')
  if (container) chartObserver.observe(container)
})
onBeforeUnmount(() => {
  chartObserver?.disconnect()
  if (radarChart) { radarChart.dispose(); radarChart = null }
  if (trendChart) { trendChart.dispose(); trendChart = null }
})
</script>

<style scoped>
.eval-results { min-width: 0; color: #27333e; font-variant-numeric: tabular-nums; }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.page-heading h1 { margin: 0; font-size: 24px; font-weight: 650; line-height: 1.4; }
.project-select { width: 220px; flex-shrink: 0; }
.view-tabs :deep(.el-tabs__header) { margin-bottom: 0; }
.view-tabs :deep(.el-tabs__nav-wrap::after) { height: 1px; }
.results-view, .analysis-view { min-width: 0; background: #fff; }
.scope-label { padding: 16px 20px; color: #637181; font-size: 12px; }
.result-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); padding: 20px 0; border-bottom: 1px solid #e7ebef; }
.result-summary > div { padding: 0 24px; border-right: 1px solid #e7ebef; }
.result-summary > div:last-child { border: 0; }
.result-summary span { display: block; color: #637181; font-size: 12px; margin-bottom: 6px; }
.result-summary strong { font-size: 24px; font-weight: 650; }
.result-summary .summary-alert { color: var(--tech-danger); }
.header { padding: 16px 20px 0; }
/* 抵消 MainLayout 内容区 20px 顶部内边距，吸顶后紧贴全局导航。 */
.result-toolbar { position: sticky; top: -20px; z-index: 20; background: #fff; border-bottom: 1px solid #e7ebef; box-shadow: 0 3px 6px rgb(32 35 41 / 5%); }
.toolbar-mobile-head { display: none; }
.batch-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding-top: 12px; }
.batch-actions :deep(.el-button + .el-button) { margin-left: 0; }
.selection-status { box-sizing: border-box; display: flex; align-items: center; flex-wrap: wrap; gap: 12px; min-height: 52px; padding: 10px 20px; border-top: 1px solid #e7ebef; margin-top: 16px; font-size: 12px; color: #637181; }
.visible-count { color: #27333e; font-weight: 600; }
.push-action { margin-left: auto; }
.result-search { width: 230px; }
.case-link { display: inline-block; max-width: 100%; padding: 0; border: 0; background: none; color: #27333e; text-align: left; font: inherit; font-weight: 600; cursor: pointer; overflow-wrap: anywhere; white-space: normal; }
.case-link:hover { color: var(--el-color-primary); text-decoration: underline; }
.case-link:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.reason-preview { color: #68717d; font-size: 12px; line-height: 1.6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 5px; }
.text-preview { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; white-space: pre-wrap; line-height: 1.6; margin-top: 4px; }
.inspector-review { display: flex; flex-wrap: wrap; gap: 8px; }
.inspector-review :deep(.el-button) { margin-left: 0; }
.inspector-review .review-note { width: 100%; }
.results-view :deep(.el-table) { font-size: 13px; --el-table-header-bg-color: #f5f7f9; --el-table-header-text-color: #536170; }
.results-view :deep(.el-table__cell) { padding: 10px 0; }
.results-view :deep(.single-run .el-table__expand-icon) { display: none; }
.eval-results :deep(.el-dialog) { max-width: calc(100vw - 32px); }
/* A/B 并排对比 */
.ab-wrap { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.ab-col { min-width: 0; border: 1px solid #e4e7ed; border-radius: 6px; padding: 12px 14px; background: #fbfdfe; }
.ab-col.ab-win { border-color: var(--tech-success); background: var(--el-color-success-light-9); }
.ab-hd { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.ab-tag { min-width: 20px; padding: 0 5px; min-height: 20px; border-radius: 4px; text-align: center; line-height: 20px; font-weight: 600; font-size: 12px; color: #fff; flex-shrink: 0; }
.ab-tag-a { background: #2f7dd1; }
.ab-tag-b { background: var(--tech-warn); }
.ab-opts { font-size: 11px; color: #8099aa; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ab-verdict { font-size: 13px; font-weight: 700; flex: none; }
.ab-body { display: flex; flex-direction: column; gap: 6px; }
.ab-sec { font-size: 11px; color: #8a94a6; font-weight: 600; margin-top: 6px; }
.ab-text { font-size: 14px; color: #34495e; line-height: 1.7; max-height: 280px; overflow: auto; word-break: break-word; }
.ab-score { font-size: 12px; color: var(--tech-warn); font-weight: 700; }
.ab-prompt { grid-column: 1 / -1; border-top: 1px solid #e4e7ed; padding: 12px 0; }
/* 判定质量面板 */
.jq-panel { background: #fff; border-top: 1px solid #e4e7ed; padding: 20px; }
.jq-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.jq-head .dr-eyebrow { color: #27333e; }
.jq-hint { font-size: 12px; color: #8a94a6; }
.jq-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px; }
.jq-card { padding: 10px 14px; text-align: left; }
.jq-n { font-family: 'JetBrains Mono', monospace; font-size: 22px; font-weight: 800; color: #1f2d3d; }
.jq-n .jq-u { font-size: 12px; color: #8a94a6; }
.jq-n.warn { color: var(--tech-warn); }
.jq-n.danger { color: var(--tech-danger); }
.jq-l { font-size: 11px; color: #8a94a6; margin-top: 3px; }
.jq-rows { display: flex; flex-direction: column; gap: 8px; border-top: 1px dashed #e4e7ed; padding-top: 10px; }
.jq-row { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.jq-eng { width: 110px; font-family: 'JetBrains Mono', monospace; color: #4a5568; flex: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.jq-bar { flex: 1; height: 10px; background: #eef1f5; border-radius: 5px; overflow: hidden; }
.jq-bar i { display: block; height: 100%; border-radius: 5px; }
.q-hi { color: var(--tech-success); background-color: var(--tech-success); }
.q-mid { color: var(--tech-warn); background-color: var(--tech-warn); }
.q-lo { color: var(--tech-danger); background-color: var(--tech-danger); }
.jq-val { width: 48px; text-align: right; font-family: 'JetBrains Mono', monospace; font-weight: 700; flex: none; }
.jq-n, .jq-val { background-color: transparent; }
.jq-sub { font-size: 11px; color: #9aa5b1; flex: none; }
.jq-note { font-size: 12px; color: #8a94a6; border-top: 1px dashed #e4e7ed; padding-top: 10px; }
/* 批次趋势 */
.tr-panel { background: #fff; border-top: 1px solid #e4e7ed; padding: 20px; }
.tr-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.tr-legend { display: flex; gap: 14px; }
.tr-lg { font-size: 12px; color: #637181; display: inline-flex; align-items: center; gap: 5px; }
.tr-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.tr-dot-rate { background: var(--tech-signal); }
.tr-dot-score { background: var(--tech-warn); }
.tr-chart { width: 100%; height: 200px; }
/* 维度能力画像雷达 */
.dr-panel { background: #fff; padding: 20px; color: #27333e; }
.dr-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.dr-eyebrow { margin: 0; font-size: 16px; font-weight: 600; letter-spacing: 0; color: #27333e; }
.dr-overall { text-align: right; }
.dr-rate { font-size: 28px; font-weight: 650; color: var(--tech-fg); }
.dr-u { font-size: 18px; color: #7d8a9b; }
.dr-lbl { font-size: 12px; color: #8b98a9; display: block; margin-top: 2px; }
.dr-body { display: grid; grid-template-columns: 1fr 200px; gap: 24px; align-items: center; }
.dr-chart { width: 100%; height: 260px; }
.dr-bars { display: flex; flex-direction: column; gap: 10px; }
.dr-bar-row { display: flex; align-items: center; gap: 10px; }
.dr-bar-lbl { font-size: 12px; color: #637181; width: 80px; flex: none; }
.dr-bar-track { flex: 1; height: 10px; background: #edf0f3; border-radius: 4px; overflow: hidden; }
.dr-bar-fill { height: 100%; border-radius: 4px; transition: width .5s ease; }
.dr-bar-val { font-family: 'JetBrains Mono', monospace; font-size: 12px; width: 40px; text-align: right; flex: none; }
.dr-dims { display: flex; flex-direction: column; gap: 8px; }
.dr-dim { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.dr-dim-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.dr-dim-name { color: #637181; }
.dr-dim-rate { margin-left: auto; font-family: 'JetBrains Mono', monospace; font-weight: 700; }
.dr-dim-n { color: #5f6b7a; font-size: 11px; }
@media (max-width: 900px) { .dr-body { grid-template-columns: 1fr; } .dr-chart { height: 220px; } }

.header { display: flex; flex-direction: column; align-items: stretch; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.foot-hint { padding: 12px 20px; color: #637181; font-size: 12px; line-height: 1.6; }
.dim-muted { color: #c0c4cc; }
.turn-tag { margin-right: 6px; }
.score { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 14px; }
.score-hi { color: var(--tech-success); }
.score-mid { color: var(--tech-warn); }
.score-lo { color: var(--tech-danger); }
/* 人工复核 */
.review-bar { display: flex; align-items: center; gap: 8px; margin-top: 12px; padding-top: 10px; border-top: 1px dashed #e4e7ed; flex-wrap: wrap; }
.review-lbl { font-size: 12px; color: #8a94a6; font-weight: 600; }
.review-note { font-size: 12px; color: #8a94a6; }
.raw-msg { margin-top: 10px; }
.raw-msg-pre { max-height: 360px; overflow: auto; margin: 0; padding: 10px 12px; background: #0d1117; color: #c9d1d9; border-radius: 6px; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.review-flag { display: inline-block; margin-left: 4px; font-size: 11px; font-weight: 700; width: 16px; height: 16px; line-height: 16px; text-align: center; border-radius: 50%; cursor: default; }
.rf-confirmed { background: var(--el-color-success-light-9); color: var(--tech-success); }
.rf-false_positive { background: var(--el-color-warning-light-9); color: var(--tech-warn); }
.rf-false_negative { background: var(--el-color-danger-light-9); color: var(--tech-danger); }
.robust-ck { margin-right: 0; }
/* 三维展开 */
.verdict-detail { padding: 8px 16px; background: #fafcfe; }
.no-dims { padding: 8px 0; }
.dims { display: flex; gap: 16px; flex-wrap: wrap; }
.dim { flex: 1; min-width: 200px; padding: 10px 12px; border: 1px solid #e4e7ed; border-radius: 6px; background: #fff; }
.dim-head { display: flex; align-items: center; gap: 6px; font-weight: 600; color: #334; margin-bottom: 4px; }
.dim-head .ok { color: var(--tech-success); }
.dim-head .ng { color: var(--tech-danger); }
.dim-head .unk { color: #909399; }
.dim-label { font-size: 13px; }
.dim-note { font-size: 12px; color: #5a6b7b; }
.summary { margin-top: 10px; font-size: 13px; color: #5a6b7b; }
/* markdown 渲染的判定理由:紧凑排版(嵌在表格展开区,间距要小) */
.dim-note :deep(p), .summary :deep(p) { margin: 2px 0; }
.dim-note :deep(ul), .dim-note :deep(ol), .summary :deep(ul), .summary :deep(ol) { padding-left: 18px; margin: 2px 0; }
.dim-note :deep(code), .summary :deep(code) { background: #eef2f6; border-radius: 3px; padding: 0 4px; font-family: 'JetBrains Mono', monospace; font-size: 11px; }
.dim-note :deep(pre), .summary :deep(pre) { background: #f6f8fa; border-radius: 5px; padding: 6px 10px; overflow: auto; margin: 4px 0; }
.summary :deep(p:first-child) { display: inline; }
@media (max-width: 700px) {
  .toolbar-mobile-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; font-size: 12px; font-weight: 600; }
  .header.toolbar-options { display: none; }
  .header.toolbar-options.mobile-open { display: flex; max-height: 35dvh; overflow-y: auto; padding-bottom: 12px; }
  .result-toolbar .selection-status { margin-top: 0; }
  .result-toolbar .visible-count { display: none; }
  .page-heading { align-items: stretch; flex-direction: column; gap: 10px; }
  .page-heading h1 { font-size: 22px; }
  .project-select { width: 100%; }
  .result-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 0; }
  .result-summary > div { padding: 0 16px; }
  .result-summary > div:nth-child(2) { border: 0; }
  .header { padding: 12px 12px 0; }
  .filters :deep(.el-select) { max-width: 100%; flex: 1 1 140px; }
  .result-search { width: 100%; }
  .selection-status { padding: 12px; gap: 8px; }
  .selection-status .push-action { margin-left: 0; }
  .batch-actions { align-items: flex-start; }
  .dr-head, .tr-head, .jq-head { gap: 12px; flex-wrap: wrap; }
  .dr-overall { text-align: left; }
  .jq-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .jq-row { flex-wrap: wrap; }
  .jq-sub { flex-basis: 100%; }
  .ab-wrap { grid-template-columns: minmax(0, 1fr); }
  .ab-hd { flex-wrap: wrap; }
}
</style>
