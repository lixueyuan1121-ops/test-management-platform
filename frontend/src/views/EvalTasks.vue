<template>
  <div class="eval-tasks">
    <section class="task-workspace">
        <div class="head">
          <div class="title-wrap">
            <el-icon class="title-icon"><Tickets /></el-icon>
            <div>
              <div class="title">测评任务</div>
            </div>
          </div>
          <div class="head-right">
            <el-select v-model="pid" placeholder="选择项目" style="width:200px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-button type="primary" :icon="Plus" :disabled="!pid" @click="openEdit(null)">新建任务</el-button>
          </div>
        </div>
      <div class="task-filters">
        <el-input v-model="taskSearch" clearable placeholder="搜索任务名称或描述" aria-label="搜索任务" />
        <el-select v-model="taskStatus" clearable placeholder="全部状态" aria-label="任务状态"><el-option v-for="(label, value) in TS_LABEL" :key="value" :label="label" :value="value" /></el-select>
        <span class="muted">共 {{ visibleTasks.length }} 个任务</span>
      </div>

      <el-empty v-if="!loading && !tasks.length" description="暂无测评任务，点右上角「新建任务」创建" :image-size="70" />
      <el-table v-else :data="visibleTasks" v-loading="loading" size="small" border stripe empty-text="没有符合筛选条件的任务">
        <el-table-column prop="id" label="#" width="60" align="center" />
        <el-table-column label="任务名" min-width="160">
          <template #default="{ row }">
            <button class="tname" @click="openDetail(row)">{{ row.name }}</button>
            <el-tooltip v-if="row.schedule_enabled" :content="`定时 ${row.schedule_cron} → ${row.schedule_runner}${row.last_auto_run_at ? '，上次自动执行 ' + row.last_auto_run_at.replace('T',' ').slice(0,16) : ''}`" placement="top">
              <span class="sched-flag">⏰</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="描述" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '—' }}</template>
        </el-table-column>
        <el-table-column label="用例数" width="80" align="center">
          <template #default="{ row }"><span class="mono">{{ row.query_ids.length }}</span></template>
        </el-table-column>
        <el-table-column label="对话选项" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="fmtDialogOptions(row.dialog_options)" class="opts">{{ fmtDialogOptions(row.dialog_options) }}</span>
            <span v-else class="muted">默认</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="TS_TYPE[row.status] || 'info'" size="small" effect="plain">{{ TS_LABEL[row.status] || row.status || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近执行" width="150" align="center">
          <template #default="{ row }">
            <span v-if="row.last_batch_id" class="mono batch">{{ row.last_batch_id }}<br/>{{ row.done_count }}/{{ row.run_count }} 完成</span>
            <span v-else class="muted">未执行</span>
            <div v-if="row.pipeline_status==='running'" class="pipe-tip">🔄 一条龙执行中</div>
            <div v-else-if="row.pipeline_status==='done'" class="pipe-tip done">✅ 一条龙完成</div>
            <div v-else-if="row.auto_pipeline" class="pipe-tip muted">⚡ 已开一条龙</div>
          </template>
        </el-table-column>
        <el-table-column label="耗时/算力豆" width="110" align="center">
          <template #default="{ row }">
            <span v-if="row.last_batch_id" class="mono">
              ⏱ {{ fmtReported(row.total_reported_duration_s) }}<br/>
              <el-tooltip :content="`算力豆采集覆盖率 ${row.bean_coverage_rate ?? '—'}%，缺失 ${row.bean_missing_count ?? 0} 条`"><span :class="{ neg: row.total_bean_cost < 0 }">🫘 {{ row.total_bean_cost ?? '—' }}</span></el-tooltip>
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="综合评价" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.summary_status==='done'" type="success" size="small">已生成</el-tag>
            <el-tag v-else-if="row.summary_status==='running'" type="warning" size="small">生成中</el-tag>
            <el-tag v-else-if="row.summary_status==='failed'" type="danger" size="small">失败</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="268" align="center">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="openDetail(row)">详情/结果</el-button>
            <el-button size="small" type="success" text @click="openRun(row)">执行</el-button>
            <el-popconfirm v-if="row.status === 'running'"
              title="停止该测评任务？未执行的不再下发、执行中的结果作废，并关闭定时" width="280" @confirm="stopTask(row)">
              <template #reference><el-button size="small" type="danger" text>停止</el-button></template>
            </el-popconfirm>
            <el-button size="small" text @click="openEdit(row)">编辑</el-button>
            <el-dropdown trigger="click" @command="command => onTaskCommand(command, row)">
              <el-button size="small" text :icon="MoreFilled" :aria-label="`更多操作 ${row.name}`" title="更多操作" />
              <template #dropdown><el-dropdown-menu>
                <el-dropdown-item command="schedule">{{ row.schedule_enabled ? '修改定时' : '设置定时' }}</el-dropdown-item>
                <el-dropdown-item command="delete" divided>删除任务</el-dropdown-item>
              </el-dropdown-menu></template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 新建/编辑任务 -->
    <el-dialog v-model="editVisible" :title="editing?.id ? '编辑测评任务' : '新建测评任务'" width="860px" top="6vh">
      <el-form label-width="80px">
        <el-form-item label="任务名" required>
          <el-input v-model="editForm.name" maxlength="128" placeholder="如：多轮上下文专项 / v2.3 回归测评" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="editForm.description" type="textarea" :rows="2" placeholder="这个任务考察什么(会喂给综合评价 AI 做背景)" />
        </el-form-item>
        <el-form-item v-if="engineList.length > 1" label="被测产品">
          <el-checkbox-group v-model="editForm.target_engines">
            <el-checkbox v-for="e in engineList" :key="e.engine" :value="e.engine">{{ e.label }}</el-checkbox>
          </el-checkbox-group>
          <div class="muted" style="font-size:12px">勾选多个产品→执行时每题对每个产品各跑一遍，同批横向对比（如 纳米Work vs WorkBuddy）。留空=仅纳米Work。</div>
        </el-form-item>
        <el-form-item label="用例">
          <div class="qpick">
            <div class="qpick-head">
              <el-radio-group v-model="queryView" size="small" aria-label="用例范围"><el-radio-button value="all">全部用例</el-radio-button><el-radio-button value="selected">已选 {{ editForm.query_ids.length }}</el-radio-button></el-radio-group>
              <el-button size="small" type="primary" text :icon="Plus" @click="customVisible = true">新增自定义用例</el-button>
            </div>
            <div class="qpick-filters">
              <el-select v-model="queryTaskFilter" clearable filterable placeholder="按任务筛选" aria-label="按任务筛选">
                <el-option v-for="task in tasks" :key="task.id" :label="task.name" :value="task.id" />
              </el-select>
              <el-input v-model="queryTitleFilter" clearable placeholder="搜索用例标题" aria-label="搜索用例标题" />
              <span class="muted">共 {{ filteredQueries.length }} 条</span>
            </div>
            <el-table :data="filteredQueries" row-key="id" size="small" border max-height="360">
              <el-table-column width="40">
                <template #header>
                  <el-checkbox aria-label="全选筛选结果" :model-value="allFilteredSelected" :indeterminate="someFilteredSelected && !allFilteredSelected" :disabled="!filteredQueries.length" @change="selectFilteredQueries" />
                </template>
                <template #default="{ row }">
                  <el-checkbox :aria-label="`选择 ${row.title}`" :model-value="editForm.query_ids.includes(row.id)" @change="checked => selectQuery(row.id, checked)" />
                </template>
              </el-table-column>
              <el-table-column label="维度" width="104" align="center">
                <template #default="{ row }"><el-tag size="small" effect="plain" :type="DIM_TAG_TYPE[row.dimension] || 'info'">{{ dimLabel(row.dimension) }}</el-tag></template>
              </el-table-column>
              <el-table-column label="标题" min-width="150" show-overflow-tooltip><template #default="{ row }">{{ row.title }}</template></el-table-column>
              <el-table-column label="提问" min-width="220" show-overflow-tooltip><template #default="{ row }">{{ row.prompt }}</template></el-table-column>
              <el-table-column label="来源" width="80" align="center">
                <template #default="{ row }"><span class="muted">{{ row.ai_task_id ? 'AI' : '手工' }}</span></template>
              </el-table-column>
            </el-table>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!editForm.name.trim()" @click="saveTask">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新增自定义用例 -->
    <el-dialog v-model="customVisible" title="新增自定义用例" width="560px" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="标题" required><el-input v-model="customForm.title" maxlength="512" /></el-form-item>
        <el-form-item label="维度">
          <el-select v-model="customForm.dimension" clearable placeholder="选主考维度(可空)">
            <el-option v-for="d in DIMENSIONS" :key="d.k" :label="d.label" :value="d.k" />
          </el-select>
        </el-form-item>
        <el-form-item label="提问" required>
          <el-input v-model="customForm.prompt" type="textarea" :rows="4" placeholder="发给被测大模型的完整提问" />
        </el-form-item>
        <el-form-item label="期望">
          <el-input v-model="customForm.expected" type="textarea" :rows="3" placeholder="期望被测模型做到什么(判定参照,建议填写)" />
        </el-form-item>
        <el-form-item label="产物检查"><EvalArtifactRules v-model="customForm.verification_rules" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="customVisible = false">取消</el-button>
        <el-button type="primary" :loading="customSaving" :disabled="!customForm.title.trim() || !customForm.prompt.trim()" @click="saveCustom">保存并加入</el-button>
      </template>
    </el-dialog>

    <!-- 执行 -->
    <el-dialog v-model="runVisible" title="执行测评任务" width="480px">
      <el-form label-width="90px">
        <el-form-item label="独立执行">
          <el-input-number v-model="runForm.trial_count" :min="1" :max="5" :precision="0" aria-label="每题独立执行次数" />
          <span class="cmp-hint">每题执行 {{ runForm.trial_count }} 次；多轮题每次新建完整会话。次数增加会增加耗时和算力消耗。</span>
        </el-form-item>
        <el-form-item label="执行机" required>
          <el-select v-model="runForm.runners" multiple collapse-tags collapse-tags-tooltip
            :disabled="runForm.auto" style="width:100%"
            :placeholder="devices.length ? '选择执行机(可多选,多台并行分片)' : '未登记设备,去「我的设备」注册'"
            @change="loadClientDevices">
            <el-option v-for="d in devices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="自动调度">
          <el-switch v-model="runForm.auto" />
          <span class="cmp-hint">⚡ 开启后自动铺到当前所有在线执行机并行分片(忽略上方手选)</span>
        </el-form-item>
        <el-form-item v-if="!runForm.auto && runForm.runners.length === 1" label="目标设备">
          <el-select v-model="runForm.target_device" clearable style="width:100%" :placeholder="clientDevices.length ? '选目标设备(可空)' : '该执行机未上报设备'">
            <el-option v-for="dev in clientDevices" :key="dev.vm_id" :label="`${dev.name || dev.vm_id}${(dev.status==='online'||dev.status==='active')?' 🟢':' ⚪'}`" :value="dev.vm_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="对话模式">
          <el-select v-model="runForm.chat_mode" :disabled="runTask?.target_engines?.includes('workbuddy')" clearable style="width:100%" placeholder="留空=沿用执行时配置">
            <el-option v-for="m in CHAT_MODES" :key="m.value" :label="m.label" :value="m.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="runForm.model" clearable :placeholder="MODEL_PLACEHOLDER" />
        </el-form-item>
        <el-form-item label="思考深度">
          <el-select v-model="runForm.thinking_depth" :disabled="runTask?.target_engines?.includes('workbuddy')" clearable style="width:100%" placeholder="留空=沿用执行时配置">
            <el-option v-for="d in THINKING_DEPTHS" :key="d" :label="d" :value="d" />
          </el-select>
        </el-form-item>
        <el-form-item label="A/B 对比">
          <el-switch v-model="runForm.compare" />
          <span class="cmp-hint">开启后每道题按 A/B 两套选项各执行 {{ runForm.trial_count }} 次；未指定的选项沿用执行时客户端配置，对比时请明确填写。</span>
        </el-form-item>
        <template v-if="runForm.compare">
          <el-divider content-position="left"><span class="cmp-b-title">B 组选项（上方为 A 组）</span></el-divider>
          <el-form-item label="对话模式">
            <el-select v-model="runForm.b_chat_mode" :disabled="runTask?.target_engines?.includes('workbuddy')" clearable style="width:100%" placeholder="留空=沿用执行时配置">
              <el-option v-for="m in CHAT_MODES" :key="m.value" :label="m.label" :value="m.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="模型">
            <el-input v-model="runForm.b_model" clearable :placeholder="MODEL_PLACEHOLDER" />
          </el-form-item>
          <el-form-item label="思考深度">
            <el-select v-model="runForm.b_thinking_depth" :disabled="runTask?.target_engines?.includes('workbuddy')" clearable style="width:100%" placeholder="留空=沿用执行时配置">
              <el-option v-for="d in THINKING_DEPTHS" :key="d" :label="d" :value="d" />
            </el-select>
          </el-form-item>
        </template>
        <el-form-item label="一条龙">
          <el-switch v-model="runForm.auto_pipeline" />
          <span class="cmp-hint">⚡ 全部执行完自动「批量判定 → 综合评价」，并推推分步通知</span>
        </el-form-item>
        <el-alert type="info" :closable="false" show-icon
          :title="`将下发 ${(runTask?.query_ids?.length || 0) * (runTask?.target_engines?.length || 1) * (runForm.compare ? 2 : 1) * runForm.trial_count} 条执行${runForm.auto ? '(自动铺到在线执行机并行)' : (runForm.runners.length > 1 ? `(分片到 ${runForm.runners.length} 台并行)` : '')}；本次每题每产品每组独立执行 ${runForm.trial_count} 次，生成新批次，综合评价需重新生成`" />
      </el-form>
      <template #footer>
        <el-button @click="runVisible = false">取消</el-button>
        <el-button type="success" :loading="running" :disabled="!runForm.auto && !runForm.runners.length" @click="doRun">下发执行</el-button>
      </template>
    </el-dialog>

    <!-- 定时执行(CI 回归守卫) -->
    <el-dialog v-model="schedVisible" title="定时执行（回归守卫）" width="460px">
      <el-form label-width="90px">
        <el-form-item label="启用">
          <el-switch v-model="schedForm.enabled" />
        </el-form-item>
        <template v-if="schedForm.enabled">
          <el-form-item label="cron" required>
            <el-input v-model="schedForm.cron" placeholder="5 段表达式，如 0 9 * * * = 每天 9:00" />
          </el-form-item>
          <el-form-item label="执行机" required>
            <el-select v-model="schedForm.runner" style="width:100%" :placeholder="devices.length ? '选择执行机' : '未登记设备,去「我的设备」注册'">
              <el-option v-for="d in devices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
            </el-select>
          </el-form-item>
        </template>
        <el-alert type="info" :closable="false" show-icon
          title="到点自动下发整任务，沿用最近一次执行的对话选项（含 A/B 对比）；上一批还没跑完会自动跳过本次，防止堆积" />
      </el-form>
      <template #footer>
        <el-button @click="schedVisible = false">取消</el-button>
        <el-button type="primary" :loading="schedSaving"
          :disabled="schedForm.enabled && (!schedForm.cron.trim() || !schedForm.runner)" @click="saveSchedule">保存</el-button>
      </template>
    </el-dialog>

    <!-- 详情/结果 -->
    <el-drawer v-model="detailVisible" :title="detail?.task?.name || '任务详情'" size="min(1200px, 100vw)" destroy-on-close>
      <div v-if="detail" class="detail">
        <section v-if="detail.experiment" class="experiment-overview">
          <p>有效样本通过率 <b>{{ detail.experiment.metrics.pass_rate ?? '—' }}%</b> · 判定覆盖率 <b>{{ detail.experiment.metrics.coverage_rate ?? '—' }}%</b> · 已确认成功占比 <b>{{ detail.experiment.metrics.confirmed_success_rate ?? '—' }}%</b></p>
          <p class="muted">执行完成 {{ detail.experiment.metrics.completed }}/{{ detail.experiment.metrics.total }} · 配置错误 {{ detail.experiment.metrics.config_errors }} · 执行错误 {{ detail.experiment.metrics.execution_errors }} · 判定错误或证据不足 {{ detail.experiment.metrics.judge_errors }}</p>
          <p class="muted">当前结果算力豆 {{ detail.task.total_bean_cost ?? '—' }} · 重试算力豆 {{ detail.task.retry_bean_cost ?? '—' }} · 含重试合计 {{ detail.task.total_actual_bean_cost ?? '—' }} · 成本采集覆盖率 {{ detail.task.actual_bean_coverage_rate ?? '—' }}%</p>
          <el-alert v-if="detail.experiment.metrics.coverage_rate < 100" type="info" :closable="false" title="判定覆盖不完整，请结合缺失或错误原因解读成绩，不宜直接据此判断产品优劣。" />
          <details v-if="detail.experiment.manifest"><summary>实验配置 · 每题 {{ detail.experiment.manifest.trial_count }} 次独立执行</summary><p class="mono">题库版本 {{ detail.experiment.manifest.dataset_hash }}</p><pre class="experiment-json">{{ JSON.stringify(detail.experiment.manifest, null, 2) }}</pre></details>
          <el-table v-if="detail.experiment.manifest?.trial_count > 1" :data="detail.experiment.trial_metrics.by_engine_variant" size="small">
            <el-table-column prop="engine" label="产品" /><el-table-column prop="variant" label="A/B组" />
            <el-table-column prop="task_count" label="独立题目数" />
            <el-table-column label="稳定成功率"><template #default="{ row }">{{ row.success_rate }}%</template></el-table-column>
            <el-table-column label="判定覆盖"><template #default="{ row }">{{ row.coverage_rate }}%</template></el-table-column>
            <el-table-column prop="mean_score" label="题目等权均分" />
          </el-table>
          <details v-if="detail.experiment.manifest?.trial_count > 1"><summary>逐题重复执行与得分波动</summary>
            <el-table :data="detail.experiment.trial_metrics.tasks" size="small">
              <el-table-column prop="engine" label="产品" /><el-table-column prop="case" label="题目/会话" show-overflow-tooltip /><el-table-column prop="variant" label="组" width="55" />
              <el-table-column label="成功频率"><template #default="{ row }">{{ row.success_rate }}%</template></el-table-column>
              <el-table-column label="分数范围"><template #default="{ row }">{{ row.score_min ?? '—' }}～{{ row.score_max ?? '—' }}</template></el-table-column>
              <el-table-column label="各次结果"><template #default="{ row }">{{ row.attempts.map(a => `${a.trial_index}: ${a.verdict === 'pass' ? '通过' : a.verdict === 'fail' ? '未通过' : '未定论'}`).join('；') }}</template></el-table-column>
            </el-table>
          </details>
        </section>
        <div class="detail-toolbar">
        <div class="d-meta">
          <el-tag :type="TS_TYPE[detail.task.status] || 'info'" effect="plain">{{ TS_LABEL[detail.task.status] || detail.task.status || '—' }}</el-tag>
          <template v-if="taskBatches.length > 1">
            <span class="mono">执行历史</span>
            <el-select :model-value="selectedBatchId || detail.task.last_batch_id" size="small" style="width:300px"
              placeholder="选择执行批次" @change="switchBatch">
              <el-option v-for="b in taskBatches" :key="b.batch_id" :value="b.batch_id" :label="fmtBatchOption(b)" />
            </el-select>
          </template>
          <span v-else-if="detail.task.last_batch_id" class="mono">批次 {{ detail.task.last_batch_id }}</span>
          <span v-if="avgScore" class="avg-score">均分 {{ avgScore }}/5</span>
          <span v-if="fmtDialogOptions(detail.task.dialog_options)" class="opts">{{ fmtDialogOptions(detail.task.dialog_options) }}</span>
          <span class="muted">{{ detail.task.description || '' }}</span>
          <div class="d-actions">
            <el-button size="small" :icon="Refresh" @click="refreshDetail">刷新</el-button>
            <el-checkbox v-show="detailTab === 'results'" v-model="robustJudge" size="small" class="robust-ck">
              <el-tooltip content="每条判 3 次取多数票（更稳，但 3 倍耗时）" placement="top"><span>稳健(3票)</span></el-tooltip>
            </el-checkbox>
            <el-button v-show="detailTab === 'results'" size="small" type="primary" :loading="batchJudging" :disabled="!judgeableRuns.length" @click="judgeAll">
              {{ batchJudging && batchProgress ? batchProgress : `批量判定（${judgeableRuns.length}）` }}
            </el-button>
            <el-popconfirm v-if="detailTab === 'results' && failedRunIds.length" :title="`重跑该批次全部 ${failedRunIds.length} 条失败？`" width="240" @confirm="retryAllFailed">
              <template #reference>
                <el-button size="small" type="success">重跑失败（{{ failedRunIds.length }}）</el-button>
              </template>
            </el-popconfirm>
            <el-button v-show="detailTab === 'summary'" size="small" type="primary" :loading="summarizing" :disabled="!canSummarize" @click="genSummary">
              {{ detail.task.summary_status === 'done' ? '重新生成综合评价' : '生成综合评价' }}
            </el-button>
            <el-button v-show="detailTab === 'summary'" size="small" :icon="Download" :disabled="!canExport" @click="exportReport">导出 HTML</el-button>
          </div>
        </div>

        <el-tabs v-model="detailTab" class="detail-tabs"><el-tab-pane label="执行结果" name="results" /><el-tab-pane label="综合评价" name="summary" /></el-tabs>
        </div>
        <section v-show="detailTab === 'results'">
        <!-- A/B 对比批次:按题配对的胜率统计(pass/fail 对比;任一侧未判定/error 计未决) -->
        <div v-if="compareInfo" class="cmp-bar">
          <span class="cmp-seg cmp-a">A 胜 {{ compareInfo.aWin }}</span>
          <span class="cmp-seg cmp-bw">B 胜 {{ compareInfo.bWin }}</span>
          <span class="cmp-seg">平 {{ compareInfo.tie }}</span>
          <span class="cmp-seg cmp-und">未决 {{ compareInfo.undecided }}</span>
          <span v-if="compareInfo.aAvg != null || compareInfo.bAvg != null" class="cmp-seg">
            均分 <span class="cmp-a">A {{ compareInfo.aAvg ?? '—' }}</span> / <span class="cmp-bw">B {{ compareInfo.bAvg ?? '—' }}</span>
          </span>
          <span class="cmp-total">共 {{ compareInfo.total }} 对（判定后自动更新）</span>
        </div>

        <el-table :data="groupedDetailRuns" size="small" border stripe class="d-table"
          row-key="run_id" :tree-props="{ children: 'children' }">
          <el-table-column label="#" width="80" align="center">
            <template #default="{ row }">
              <span v-if="row.isGroup" class="muted">—</span>
              <span v-else>{{ row.run_id }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="compareInfo" label="组" width="52" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.payload?.compare_group" size="small" effect="dark"
                :type="row.payload.compare_group === 'A' ? 'primary' : 'warning'">{{ row.payload.compare_group }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="用例" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">
              <el-tag v-if="row.payload?.trial_count > 1" size="small" effect="plain">第{{ row.payload.trial_index }}次执行</el-tag>
              <el-tag v-if="row.isGroup" size="small" type="warning" effect="plain" class="turn-tag">多轮 ×{{ row.children.length }}</el-tag>
              <el-tag v-else-if="row._inGroup" size="small" effect="plain" class="turn-tag">第{{ (row.payload?.turn_index ?? 0) + 1 }}轮</el-tag>
              {{ row.payload?.title || row.payload?.prompt || `query#${row.eval_query_id}` }}
              <el-tooltip v-if="!row.isGroup && hasPayloadOpts(row)" effect="dark" placement="top" :show-after="300">
                <template #content>
                  <div class="payload-tip">
                    <div><strong>下发配置快照</strong></div>
                    <div v-if="row.payload.dialog_options?.model">模型: {{ row.payload.dialog_options.model }}</div>
                    <div v-if="row.payload.dialog_options?.chatMode">模式: {{ row.payload.dialog_options.chatMode }}</div>
                    <div v-if="row.payload.dialog_options?.thinkingDepth">深度: {{ row.payload.dialog_options.thinkingDepth }}</div>
                    <div v-if="row.payload.dimension">维度: {{ dimLabel(row.payload.dimension) }}</div>
                    <div v-if="row.payload.compare_group">A-B组: {{ row.payload.compare_group }}</div>
                  </div>
                </template>
                <el-icon class="payload-ico"><InfoFilled /></el-icon>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="维度" width="104" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.dimension" size="small" effect="plain" :type="DIM_TAG_TYPE[row.dimension] || 'info'">{{ dimLabel(row.dimension) }}</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="执行" width="88" align="center">
            <template #default="{ row }">
              <el-tag :type="STATUS_TYPE[row.status] || 'info'" size="small" effect="plain">{{ STATUS_LABEL[row.status] || row.status || '—' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="判定" width="88" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.verdict" :type="VERDICT_TYPE[row.verdict] || 'info'" size="small">{{ VERDICT_LABEL[row.verdict] || row.verdict }}</el-tag>
              <span v-else class="muted">—</span>
              <el-tooltip v-if="row.review_mark" :content="REVIEW_LABEL[row.review_mark] + (row.review_note ? '：' + row.review_note : '')" placement="top">
                <span class="review-flag" :class="'rf-' + row.review_mark">{{ REVIEW_ICON[row.review_mark] }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="评分" width="64" align="center">
            <template #default="{ row }">
              <span v-if="rowScore(row) != null" class="score" :class="scoreClass(rowScore(row))">{{ rowScore(row) }}</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="耗时/豆" width="92" align="center">
            <template #default="{ row }">
              <span v-if="!row.isGroup" class="mono">
                ⏱ {{ fmtReported(row.reported_duration) }}<br/>
                <span :class="{ neg: String(row.bean_cost || '').trim().startsWith('-') }">🫘 {{ row.bean_cost || '—' }}</span>
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="判定理由" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.status === 'failed'" class="fail-reason">执行失败：{{ row.reason || '（未回写原因）' }}</span>
              <template v-else>{{ row.verdict_reason || '—' }}</template>
            </template>
          </el-table-column>
          <el-table-column label="会话" width="70" align="center">
            <template #default="{ row }">
              <el-link v-if="safeUrl(row.share_link)" type="primary" :href="safeUrl(row.share_link)" target="_blank" rel="noopener noreferrer">打开</el-link>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="96" align="center">
            <template #default="{ row }">
              <el-popconfirm v-if="!row.isGroup && (row.status === 'running' || row.status === 'pending')"
                title="标记为执行失败？(会话未回填/执行中断时用于收口)" width="240" @confirm="markFailed(row)">
                <template #reference><el-button size="small" type="danger" text>标记失败</el-button></template>
              </el-popconfirm>
              <el-popconfirm v-else-if="!row.isGroup && row.status === 'failed'"
                title="重跑该条？(复位回待执行，执行机将重新拉走)" width="240" @confirm="retryRun(row)">
                <template #reference><el-button size="small" type="success" text>重跑</el-button></template>
              </el-popconfirm>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>

        </section>
        <!-- 综合评价 -->
        <div v-show="detailTab === 'summary'" class="summary-sec">
          <div class="summary-head">
            <span class="summary-title">AI 综合评价</span>
            <span v-if="detail.task.summary_at" class="muted">{{ (detail.task.summary_at || '').replace('T',' ').slice(0,19) }} · {{ detail.task.summary_provider }}</span>
            <template v-if="summaryShareUrl">
              <el-button size="small" text type="primary" @click="openShareReport">打开在线报告</el-button>
              <el-button size="small" text @click="copyShareLink">复制链接</el-button>
            </template>
          </div>
          <pre v-if="summarizing && summaryStream" class="summary-stream">{{ summaryStream }}</pre>
          <div v-else-if="detail.task.summary_html" class="summary-html" v-html="detail.task.summary_html"></div>
          <el-empty v-else description="尚未生成综合评价。执行 + 判定完成后点上方「生成综合评价」" :image-size="60" />
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import EvalArtifactRules from '@/components/EvalArtifactRules.vue'
import { ref, computed, nextTick, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Tickets, Plus, Refresh, InfoFilled, Download, MoreFilled } from '@element-plus/icons-vue'
import {
  listEvalTasks, createEvalTask, updateEvalTask, deleteEvalTask, runEvalTask, stopEvalTask, listEvalTaskRuns, listEvalTaskBatches,
  streamEvalTaskSummary, listEvalQueries, createEvalQueryManual, listMyDevices, listEvalDevices,
  listEvalDimensions, judgeEvalBatch, notifyEvalJudgeBatchDone, pollAiJobs, markEvalRunFailed, setEvalTaskSchedule, retryEvalRun, retryFailedEvalRuns,
  listEvalEngines,
} from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { CHAT_MODES, THINKING_DEPTHS, MODEL_PLACEHOLDER, buildDialogOptions, fmtDialogOptions } from '@/utils/dialogOptions'
import { groupEvalRuns, compareEvalRuns } from '@/utils/evalRunGroups'
import { buildEvalReportHtml } from '@/utils/evalReportHtml'

const TS_LABEL = { draft: '草稿', running: '执行中', done: '已完成', stopped: '已停止', archived: '已归档' }
const TS_TYPE = { draft: 'info', running: 'warning', done: 'success', stopped: 'info', archived: 'info' }
const STATUS_LABEL = { pending: '待执行', running: '执行中', done: '待判定', judging: '判定中', judged: '已判定', failed: '执行失败', cancelled: '已取消' }
const STATUS_TYPE = { pending: 'info', running: 'warning', done: 'primary', judging: 'warning', judged: 'success', failed: 'danger', cancelled: 'info' }
const VERDICT_LABEL = { pass: '通过', fail: '不通过', error: '判定出错' }
const VERDICT_TYPE = { pass: 'success', fail: 'danger', error: 'info' }
// 人工复核标记(只读展示;复核操作在「测评结果」页)
const REVIEW_LABEL = { confirmed: '已认可判定', false_positive: '误报（实际通过）', false_negative: '漏报（实际有问题）' }
const REVIEW_ICON = { confirmed: '✓', false_positive: '误', false_negative: '漏' }
const DIM_TAG_TYPE = {
  thinking: 'primary', tool_use: 'success', artifact: 'warning', multi_turn: 'danger', instruction: 'info',
  workflow: 'warning', clarification: 'primary', context: 'success', safety: 'danger', refusal: 'info',
  hallucination: 'warning', creativity: 'primary', consistency: 'success',
}

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const taskSearch = ref('')
const taskStatus = ref('')
const visibleTasks = computed(() => tasks.value.filter(task =>
  (!taskStatus.value || task.status === taskStatus.value) &&
  `${task.name || ''} ${task.description || ''}`.toLocaleLowerCase().includes(taskSearch.value.trim().toLocaleLowerCase())))
const loading = ref(false)

// 维度注册表
const DIMENSIONS = ref([])
const dimLabel = (k) => (DIMENSIONS.value.find((d) => d.k === k)?.label) || k || '未标注'

// 编辑
const editVisible = ref(false)
const editing = ref(null)
const editForm = ref({ name: '', description: '', query_ids: [], target_engines: [] })
const engineList = ref([])   // 被测产品注册表(listEvalEngines);>1 才显示产品勾选
const allQueries = ref([])
const queryTaskFilter = ref(null)
const queryTitleFilter = ref('')
const queryView = ref('all')
const filteredQueries = computed(() => {
  const task = tasks.value.find(t => t.id === queryTaskFilter.value)
  const ids = task ? new Set(task.query_ids) : null
  const title = queryTitleFilter.value.trim().toLocaleLowerCase()
  return allQueries.value.filter(q => (queryView.value !== 'selected' || editForm.value.query_ids.includes(q.id)) && (!ids || ids.has(q.id)) &&
    (!title || (q.title || '').toLocaleLowerCase().includes(title)))
})
const allFilteredSelected = computed(() => filteredQueries.value.length > 0 && filteredQueries.value.every(q => editForm.value.query_ids.includes(q.id)))
const someFilteredSelected = computed(() => filteredQueries.value.some(q => editForm.value.query_ids.includes(q.id)))
function selectQuery(id, checked) {
  editForm.value.query_ids = checked
    ? [...new Set([...editForm.value.query_ids, id])]
    : editForm.value.query_ids.filter(value => value !== id)
}
function selectFilteredQueries(checked) {
  const visible = new Set(filteredQueries.value.map(q => q.id))
  editForm.value.query_ids = [...new Set([
    ...editForm.value.query_ids.filter(id => !visible.has(id)),
    ...(checked ? [...visible] : []),
  ])]
}
const saving = ref(false)

// 自定义用例
const customVisible = ref(false)
const customForm = ref({ title: '', prompt: '', dimension: null, expected: '', verification_rules: [] })
const customSaving = ref(false)

// 执行
const runVisible = ref(false)
const runTask = ref(null)
const runForm = ref({ trial_count: 1, runners: [], auto: false, auto_pipeline: false, target_device: '', chat_mode: '', model: '', thinking_depth: '',
  compare: false, b_chat_mode: '', b_model: '', b_thinking_depth: '' })
const devices = ref([])
const clientDevices = ref([])
const running = ref(false)

// 详情
const detailVisible = ref(false)
const detailTab = ref('results')
const detail = ref(null)
const batchJudging = ref(false)
const batchProgress = ref('')   // 批量判定进度「判定中 done/total」
const robustJudge = ref(false)
const summarizing = ref(false)
const summaryStream = ref('')

const safeUrl = (u) => /^https?:\/\//i.test(u || '') ? u : null
// 详情表:多轮会话聚合成组行树形展开(公共逻辑见 utils/evalRunGroups),单轮原样
const groupedDetailRuns = computed(() => groupEvalRuns(detail.value?.runs || []))
// 评分(1-5,判定引擎给):组行取各轮均分(1 位小数),真实行取 score;无评分 null
const rowScore = (row) => {
  if (!row.isGroup) return row.score ?? null
  const ss = (row.children || []).map((t) => t.score).filter((s) => s != null)
  return ss.length ? +(ss.reduce((a, b) => a + b, 0) / ss.length).toFixed(1) : null
}
const scoreClass = (s) => (s >= 4 ? 'score-hi' : s >= 3 ? 'score-mid' : 'score-lo')
// 该 run 是否有可展示的下发配置(对话选项任一项/维度/A-B 组);全空不显示 ⓘ 图标免噪音
const hasPayloadOpts = (row) => {
  const p = row.payload
  if (!p) return false
  const d = p.dialog_options || {}
  return !!(d.model || d.chatMode || d.thinkingDepth || p.dimension || p.compare_group)
}
// 批次平均分(已评分 run 的均值;A/B 对比时分组各算)
const avgScore = computed(() => {
  const ss = (detail.value?.runs || []).map((r) => r.score).filter((s) => s != null)
  return ss.length ? (ss.reduce((a, b) => a + b, 0) / ss.length).toFixed(1) : null
})
// A/B 对比批次统计:按批次、产品和 eval_query_id 配对(多轮逐轮配对),pass/fail 定胜负——
// A pass B fail 记 A 胜,反之 B 胜,同 pass/同 fail 记平,任一侧无判定或 error 记未决;
// 另算 A/B 各自均分(评分比 pass/fail 更细腻,平局多时靠它分高下)
const compareInfo = computed(() => {
  return compareEvalRuns(detail.value?.runs || [])
})
const judgeableRuns = computed(() =>
  (detail.value?.runs || []).filter((r) => r.status === 'done' || r.status === 'judged'))
const failedRunIds = computed(() =>
  (detail.value?.runs || []).filter((r) => r.status === 'failed').map((r) => r.run_id))
const canSummarize = computed(() => {
  const t = detail.value?.task
  if (!t || !t.last_batch_id || summarizing.value) return false
  const runs = detail.value?.runs || []
  // 至少有一条非 failed 的 run 才开放综合评价
  return runs.some((r) => r.status !== 'failed' && r.status !== 'pending' && r.status !== 'running')
})
// 有任何执行记录即可导出(综合评价没生成也能导——胜率/评分/明细本身有分享价值)
const canExport = computed(() => !!(detail.value?.runs?.length))

onMounted(async () => {
  const [projRes, devRes, dimRes, engRes] = await Promise.allSettled([app.fetchProjects(), listMyDevices(), listEvalDimensions(), listEvalEngines()])
  projects.value = projRes.status === 'fulfilled' ? (projRes.value || []) : []
  devices.value = devRes.status === 'fulfilled' ? (devRes.value || []) : []
  engineList.value = engRes.status === 'fulfilled' ? (engRes.value || []) : []
  DIMENSIONS.value = dimRes.status === 'fulfilled' && dimRes.value?.dimensions?.length
    ? dimRes.value.dimensions.map((d) => ({ k: d.key, label: d.label }))
    : [{ k: 'thinking', label: '思考推理' }, { k: 'workflow', label: '工作流' }, { k: 'clarification', label: '反问澄清' }]
  if (devices.value.length) runForm.value.runners = [devices.value[0].runner_id]
  if (projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await onProjectChange() }
})

async function onProjectChange() {
  tasks.value = []
  if (!pid.value) return
  setLastProjectId(pid.value)
  await load()
}

async function load() {
  loading.value = true
  try { tasks.value = await listEvalTasks(pid.value) || [] } finally { loading.value = false }
}

// ── 编辑 ──
async function openEdit(row) {
  queryView.value = 'all'
  queryTaskFilter.value = null
  queryTitleFilter.value = ''
  editing.value = row
  // 新建默认勾选全部已知产品(多产品横评是接入 WorkBuddy 的主用途);编辑回填任务已存的
  const defaultEngines = engineList.value.map(e => e.engine)
  editForm.value = row
    ? { name: row.name, description: row.description || '', query_ids: [...row.query_ids], target_engines: [...(row.target_engines || [])] }
    : { name: '', description: '', query_ids: [], target_engines: defaultEngines }
  try { allQueries.value = await listEvalQueries(pid.value) || [] } catch { allQueries.value = [] }
  editVisible.value = true
  await nextTick()
}

async function saveTask() {
  saving.value = true
  try {
    if (editing.value?.id) {
      await updateEvalTask(editing.value.id, editForm.value)
      ElMessage.success('已保存')
    } else {
      await createEvalTask({ project_id: pid.value, ...editForm.value })
      ElMessage.success('已创建')
    }
    editVisible.value = false
    await load()
  } catch { /* 拦截器已提示 */ }
  finally { saving.value = false }
}

async function saveCustom() {
  customSaving.value = true
  try {
    const q = await createEvalQueryManual({ project_id: pid.value, ...customForm.value })
    allQueries.value = [q, ...allQueries.value]
    editForm.value.query_ids.push(q.id)
    customVisible.value = false
    customForm.value = { title: '', prompt: '', dimension: null, expected: '', verification_rules: [] }
    ElMessage.success('用例已创建并加入任务')
  } catch { /* 拦截器已提示 */ }
  finally { customSaving.value = false }
}

async function removeTask(row) {
  try { await deleteEvalTask(row.id); ElMessage.success('已删除'); await load() } catch { /* 拦截器已提示 */ }
}
async function onTaskCommand(command, row) {
  if (command === 'schedule') return openSchedule(row)
  if (command !== 'delete') return
  try { await ElMessageBox.confirm('删除该任务？执行记录保留。', '删除任务', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  await removeTask(row)
}

// ── 定时执行(回归守卫) ──
const schedVisible = ref(false)
const schedTask = ref(null)
const schedForm = ref({ enabled: false, cron: '', runner: '' })
const schedSaving = ref(false)

function openSchedule(row) {
  schedTask.value = row
  schedForm.value = {
    enabled: !!row.schedule_enabled,
    cron: row.schedule_cron || '0 9 * * *',
    runner: row.schedule_runner || (devices.value[0]?.runner_id || ''),
  }
  schedVisible.value = true
}

async function saveSchedule() {
  schedSaving.value = true
  try {
    await setEvalTaskSchedule(schedTask.value.id, {
      enabled: schedForm.value.enabled,
      cron: schedForm.value.cron.trim() || null,
      runner: schedForm.value.runner || null,
    })
    ElMessage.success(schedForm.value.enabled ? '定时已开启' : '定时已关闭')
    schedVisible.value = false
    await load()
  } catch { /* 拦截器已提示 */ }
  finally { schedSaving.value = false }
}

async function stopTask(row) {
  try {
    const d = await stopEvalTask(row.id)
    ElMessage.success(`已停止，收口 ${d.cancelled_count} 条未完成用例`)
    await load()
  } catch { /* 拦截器已提示 */ }
}

// ── 执行 ──
async function openRun(row) {
  if (!row.query_ids.length) { ElMessage.warning('任务内还没有用例，先编辑添加'); return }
  runTask.value = row
  // 回填该任务最近一次执行的对话选项(没有则清空=默认);compareB 键=上次是 A/B 对比执行
  const d = row.dialog_options || {}
  runForm.value.trial_count = d.trial_count || 1
  runForm.value.chat_mode = d.chatMode || ''
  runForm.value.model = d.model || ''
  runForm.value.thinking_depth = d.thinkingDepth || ''
  const b = d.compareB
  runForm.value.compare = b !== undefined
  runForm.value.b_chat_mode = b?.chatMode || ''
  runForm.value.b_model = b?.model || ''
  runForm.value.b_thinking_depth = b?.thinkingDepth || ''
  if (row.target_engines?.includes('workbuddy')) {
    runForm.value.chat_mode = runForm.value.thinking_depth = ''
    runForm.value.b_chat_mode = runForm.value.b_thinking_depth = ''
  }
  runForm.value.auto_pipeline = !!row.auto_pipeline
  runVisible.value = true
  if (runForm.value.runners.length === 1) await loadClientDevices()
}

async function loadClientDevices() {
  runForm.value.target_device = ''
  clientDevices.value = []
  // 仅单台选中时才有意义选目标设备(多台/auto 各机用各自当前设备)
  const only = runForm.value.runners.length === 1 ? runForm.value.runners[0] : ''
  if (!only) return
  try { clientDevices.value = await listEvalDevices(only) || [] } catch { clientDevices.value = [] }
}

// 上报耗时值 → 总秒数(容错),与后端 _parse_seconds / runner _durationToSeconds 同口径。
// 详情表传的是单条 run 原始 reported_duration(现为纯秒,但历史/兜底可能是分秒原文);列表行传的
// 已是后端算好的纯秒数。纯秒→原值,分秒/时分秒→换算,解析不出→null。
//   "1418"/1418→1418  "23m 38s"→1418  "2分43秒"→163  "10m"→600  "01:05:02"→3902  ""/null→null
function durToSeconds(v) {
  if (v == null) return null
  if (typeof v === 'number') return isFinite(v) ? Math.round(v) : null
  const s = String(v).trim()
  if (!s) return null
  if (/^\d{1,2}(?::\d{1,2}){1,2}$/.test(s)) return s.split(':').reduce((a, n) => a * 60 + parseInt(n, 10), 0)
  let total = 0, matched = false
  const h = s.match(/(\d+(?:\.\d+)?)\s*(?:小时|小時|時|时|h(?![a-z]))/i); if (h) { total += parseFloat(h[1]) * 3600; matched = true }
  const m = s.match(/(\d+(?:\.\d+)?)\s*(?:分钟|分|m(?![a-z]))/i);        if (m) { total += parseFloat(m[1]) * 60; matched = true }
  const c = s.match(/(\d+(?:\.\d+)?)\s*(?:秒|s(?![a-z]))/i);            if (c) { total += parseFloat(c[1]); matched = true }
  if (matched) return Math.round(total)
  if (/^\d+(?:\.\d+)?$/.test(s)) return Math.round(parseFloat(s))
  return null
}

// 上报耗时 → 对齐对话页「已完成 Ns」的呈现:"15s" / "23m 38s" / "1h 5m 2s"。
// 整分/整时省略下级 0(如 "10m"、"1h")。空/无法解析 → "—"。入参可为纯秒数、分秒原文或数字。
function fmtReported(val) {
  const n = durToSeconds(val)
  if (n == null || n <= 0) return '—'
  const h = Math.floor(n / 3600), m = Math.floor((n % 3600) / 60), s = n % 60
  const parts = []
  if (h) parts.push(h + 'h')
  if (m) parts.push(m + 'm')
  if (s || !parts.length) parts.push(s + 's')
  return parts.join(' ')
}

async function doRun() {
  running.value = true
  try {
    // auto=后端自动铺到在线执行机;否则传手选的多台(单台=数组含一项,后端一视同仁分片)
    const payload = {
      trial_count: runForm.value.trial_count,
      // 被测产品:传任务级勾选的 target_engines(多产品横评);为空则后端回落 namiwork。
      target_engines: (runTask.value.target_engines && runTask.value.target_engines.length)
        ? runTask.value.target_engines : ['namiwork'],
      // 仅单台时目标设备生效;多台/auto 每机用各自当前设备
      target_device: (!runForm.value.auto && runForm.value.runners.length === 1)
        ? (runForm.value.target_device || null) : null,
      dialog_options: buildDialogOptions({
        chatMode: runForm.value.chat_mode, model: runForm.value.model, thinkingDepth: runForm.value.thinking_depth,
      }),
      // 对比开关开启才传 B 组(传了即启用对比,B 三项全空 = B 沿用执行时客户端配置);关闭传 null=单套执行
      dialog_options_b: runForm.value.compare ? (buildDialogOptions({
        chatMode: runForm.value.b_chat_mode, model: runForm.value.b_model, thinkingDepth: runForm.value.b_thinking_depth,
      }) || {}) : null,
    }
    if (runForm.value.auto) payload.runner = 'auto'
    else payload.runners = runForm.value.runners
    payload.auto_pipeline = runForm.value.auto_pipeline
    const res = await runEvalTask(runTask.value.id, payload)
    const n = res.runners?.length || 1
    ElMessage.success(`已下发 ${res.run_ids.length} 条${n > 1 ? `，分发到 ${n} 台并行` : ''}（批次 ${res.batch_id}）`)
    runVisible.value = false
    await load()
  } catch { /* 拦截器已提示 */ }
  finally { running.value = false }
}

// ── 详情/判定/综合评价 ──
async function openDetail(row) {
  detailTab.value = 'results'
  detailVisible.value = true
  detail.value = null
  summaryStream.value = ''
  selectedBatchId.value = null   // 默认看最新批次
  taskBatches.value = []
  try {
    detail.value = await listEvalTaskRuns(row.id)
    await loadBatches(row.id)
  } catch { /* 拦截器已提示 */ }
}

async function refreshDetail() {
  if (!detail.value?.task) return
  const tid = detail.value.task.id
  try {
    detail.value = await listEvalTaskRuns(tid, selectedBatchId.value || undefined)  // 保持当前查看的批次
    await loadBatches(tid)
  } catch { /* 拦截器已提示 */ }
}

// 执行批次历史:列该任务历次执行(批次),下拉切换查看任一批的整组结果。
const selectedBatchId = ref(null)   // null=最新批次
const taskBatches = ref([])

async function loadBatches(taskId) {
  try { taskBatches.value = (await listEvalTaskBatches(taskId)).batches || [] } catch { /* 忽略 */ }
}

async function switchBatch(batchId) {
  if (!detail.value?.task) return
  selectedBatchId.value = batchId
  try { detail.value = await listEvalTaskRuns(detail.value.task.id, batchId) } catch { /* 拦截器已提示 */ }
}

function fmtBatchOption(b) {
  const t = (b.t0 || '').replace('T', ' ').slice(5, 16)   // MM-DD HH:mm
  const pass = `通过 ${b.passed}/${b.total}`
  const score = b.avg_score != null ? ` · 均分 ${b.avg_score}` : ''
  return `${t} · ${pass}${score}${b.is_current ? ' ·(最新)' : ''}`
}

async function judgeAll() {  const runs = judgeableRuns.value
  if (!runs.length) return
  batchJudging.value = true
  try {
    // 只传 done 状态的 run_ids（judged 的也允许重判）；用任务的 project_id 而非全局 pid
    const taskProjectId = detail.value?.task?.project_id || pid.value
    if (!taskProjectId) { ElMessage.warning('缺少项目信息，请刷新页面后重试'); return }
    // done(待判)与 judged(已判,允许按新标准重判)都纳入;仅排除 pending/running/failed/cancelled。
    // 双保险过滤非法 id：任何 null/undefined 混入都会触发后端 422「参数校验失败」
    const runIds = runs.filter((r) => r.status === 'done' || r.status === 'judged').map((r) => r.run_id).filter((id) => Number.isInteger(id))
    if (!runIds.length) { ElMessage.warning('没有可判定的用例（done/judged 状态）'); return }
    const res = await judgeEvalBatch({ project_id: taskProjectId, run_ids: runIds, votes: robustJudge.value ? 3 : 1 })
    if (!res.count) { ElMessage.info('没有可判定的用例'); return }
    // 每条 run 一个 job,轮询这批 job(方案2 P2);done 结果里 verdict=error 计失败
    const results = await pollAiJobs(res.job_ids, {
      onProgress: ({ done, total }) => { batchProgress.value = `判定中 ${done}/${total}` },
    })
    const bad = results.filter((x) => x.error || x.verdict === 'error').length
      + (res.skipped?.length || 0)
    if (bad) ElMessage.warning(`已处理 ${res.count} 条，其中 ${bad} 条判定失败/跳过（未回填的会话请重跑后再判）`)
    else ElMessage.success(`已判定 ${res.count} 条`)
    // 整批判完补推推推通知(带该任务在线报告链接);静默失败,不打扰。
    notifyEvalJudgeBatchDone({ project_id: taskProjectId, task_id: detail.value?.task?.id,
      judged: res.count, failed: bad }).catch(() => {})
    await refreshDetail()
  } catch (e) { ElMessage.error(e?.message || '批量判定失败') }
  finally { batchJudging.value = false; batchProgress.value = '' }
}

async function markFailed(row) {
  try {
    await markEvalRunFailed(detail.value.task.id, row.run_id)
    ElMessage.success(`run ${row.run_id} 已标记失败`)
    await refreshDetail()
  } catch { /* 拦截器已提示 */ }
}

async function retryRun(row) {
  try {
    await retryEvalRun(detail.value.task.id, row.run_id)
    ElMessage.success(`run ${row.run_id} 已复位待执行，执行机将重新拉走`)
    await refreshDetail()
  } catch { /* 拦截器已提示 */ }
}

// 批量重跑该批次全部 failed(传 run_ids 精确圈定当前详情里的失败行)
async function retryAllFailed() {
  try {
    const taskProjectId = detail.value?.task?.project_id || pid.value
    const res = await retryFailedEvalRuns({ project_id: taskProjectId, run_ids: failedRunIds.value })
    ElMessage.success(`已复位 ${res.retried} 条待执行，执行机将重新拉走`)
    await refreshDetail()
  } catch { /* 拦截器已提示 */ }
}

function genSummary() {
  detailTab.value = 'summary'
  if (!detail.value?.task) return
  summarizing.value = true
  summaryStream.value = ''
  streamEvalTaskSummary(detail.value.task.id, {}, {
    onDelta: (t) => { summaryStream.value += t },
    onDone: async (evt) => {
      summarizing.value = false
      if (evt.status === 'done') { ElMessage.success('综合评价已生成'); await refreshDetail(); await load() }
      else ElMessage.error(evt.msg || '生成失败')
    },
    onError: (msg) => { summarizing.value = false; ElMessage.error(msg || '生成失败') },
  })
}

// 综合评价在线短链:后端 /r/<code> 与本页同源(uvicorn 同源托管),故用当前 origin 拼。
// 仅 summary_status=done 且有短链码时可用。推推通知里的链接走后端 PLATFORM_BASE_URL(可能是外网基址)。
const summaryShareUrl = computed(() => {
  const t = detail.value?.task
  if (!t || t.summary_status !== 'done' || !t.summary_share_code) return null
  return `${window.location.origin}/r/${t.summary_share_code}`
})
function openShareReport() {
  if (summaryShareUrl.value) window.open(summaryShareUrl.value, '_blank', 'noopener')
}
async function copyShareLink() {
  if (!summaryShareUrl.value) return
  try {
    await navigator.clipboard.writeText(summaryShareUrl.value)
    ElMessage.success('在线报告链接已复制')
  } catch {
    ElMessage.warning(`复制失败，请手动复制：${summaryShareUrl.value}`)
  }
}

// ── 导出 HTML 报告（综合评价 + A/B 胜率/均分 + 逐条明细，自包含单文件，离线可分享）──
const pad2 = (n) => String(n).padStart(2, '0')
function nowStr() {
  const d = new Date()
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}
function downloadHtml(filename, html) {
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
function exportReport() {
  const d = detail.value
  if (!d?.task) return
  const html = buildEvalReportHtml({
    experiment: detail.value.experiment,
    task: d.task,
    groupedRuns: groupedDetailRuns.value,
    compareInfo: compareInfo.value,
    dimLabel,
    statusLabel: STATUS_LABEL,
    verdictLabel: VERDICT_LABEL,
    taskStatusLabel: TS_LABEL,
    reviewLabel: REVIEW_LABEL,
    dialogOptionsText: fmtDialogOptions(d.task.dialog_options) || '',
    avgScore: avgScore.value || '',
    exportedAt: nowStr(),
  })
  const safeName = (d.task.name || '测评报告').replace(/[\\/:*?"<>|]/g, '_')
  const filename = `测评报告-${safeName}${d.task.last_batch_id ? '-' + d.task.last_batch_id : ''}.html`
  downloadHtml(filename, html)
  ElMessage.success('已导出 HTML 报告')
}
</script>

<style scoped>
.task-workspace { min-width: 0; }
.task-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 16px 0; }
.task-filters .el-input { width: 280px; max-width: 100%; }
.task-filters .el-select { width: 160px; }
.head { position: sticky; top: -20px; z-index: 20; background: var(--tech-bg); padding: 16px 0; gap: 12px; flex-wrap: wrap; }
.head-right { flex-wrap: wrap; }
.detail-toolbar { position: sticky; top: -20px; background: #fff; z-index: 3; padding-top: 12px; }
.detail-toolbar .d-meta { max-height: 30dvh; overflow: auto; padding-bottom: 12px; }
.detail :deep(.el-tabs__header) { margin: 0; }
.d-actions { flex-wrap: wrap; }
.eval-tasks :deep(.el-dialog) { max-width: calc(100vw - 24px); }
.eval-tasks :deep(.el-dialog__body) { max-height: 68dvh; overflow: auto; }
.eval-tasks :deep(.el-dialog__footer) { border-top: 1px solid var(--el-border-color-lighter); padding-top: 16px; }
.qpick-head { gap: 8px; flex-wrap: wrap; }
.payload-tip { font-size: 12px; line-height: 1.6; }
.payload-tip > div { margin: 2px 0; }
.payload-ico { margin-left: 4px; font-size: 13px; color: #909399; cursor: help; vertical-align: -1px; }
.eval-tasks { display: flex; flex-direction: column; gap: 16px; }
.head { display: flex; align-items: center; justify-content: space-between; }
.head-right { display: flex; gap: 10px; align-items: center; }
.title-wrap { display: flex; align-items: center; gap: 12px; }
.title-icon { font-size: 24px; color: var(--el-color-primary); }
.title { font-size: 16px; font-weight: 600; color: #1f2d3d; }
.subtitle { font-size: 12px; color: #8a94a6; margin-top: 2px; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; }
.muted { color: #c0c4cc; font-size: 12px; }
.tname { color: var(--el-color-primary); cursor: pointer; border: 0; padding: 0; background: none; font: inherit; font-weight: 600; text-align: left; }
.tname:hover { text-decoration: underline; }
.sched-flag { margin-left: 6px; font-size: 13px; cursor: default; }
.fail-reason { color: #e5565f; font-size: 12px; }
.batch { line-height: 1.5; color: #5a6b7b; }
.opts { color: #5a6b7b; font-size: 12px; }
.turn-tag { margin-right: 6px; }
/* A/B 对比 */
.cmp-hint { margin-left: 10px; font-size: 12px; color: #8a94a6; }
.pipe-tip { margin-top: 3px; font-size: 11px; color: #e6a23c; }
.pipe-tip.done { color: #67c23a; }
.pipe-tip.muted { color: #a8abb2; }
.neg { color: #f56c6c; }
.cmp-b-title { font-size: 12px; color: #8a94a6; }
.cmp-bar { display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: #f6f9fc; border: 1px solid #e4ecf4; border-radius: 8px; }
.cmp-seg { font-weight: 700; font-size: 13px; color: #5a6b7b; }
.cmp-a { color: #2f7dd1; }
.cmp-bw { color: #d98b00; }
.cmp-und { color: #a0a8b3; }
.cmp-total { margin-left: auto; font-size: 12px; color: #8a94a6; }
/* 评分(1-5) */
.avg-score { font-weight: 700; font-size: 13px; color: #d98b00; }
.score { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 14px; }
.score-hi { color: #00b386; }
.score-mid { color: #d98b00; }
.score-lo { color: #e5565f; }
/* 人工复核标记 */
.review-flag { display: inline-block; margin-left: 4px; font-size: 11px; font-weight: 700; width: 16px; height: 16px; line-height: 16px; text-align: center; border-radius: 50%; cursor: default; }
.rf-confirmed { background: #e7f7f1; color: #00b386; }
.rf-false_positive { background: #fdf3e3; color: #d98b00; }
.rf-false_negative { background: #fdeaea; color: #e5565f; }
/* 用例选择 */
.qpick { width: 100%; }
.qpick-filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; }
.qpick-filters .el-select { width: 220px; max-width: 100%; }
.qpick-filters .el-input { flex: 1; min-width: 160px; }
.qpick-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; font-size: 13px; color: #5a6b7b; }
/* 详情 */
.detail { display: flex; flex-direction: column; gap: 14px; padding: 0 4px; }
.d-meta { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.d-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.robust-ck { margin-right: 0; }
.summary-sec { border: 1px solid #e4e7ed; border-radius: 8px; padding: 14px 18px; background: #fbfdfe; }
.summary-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.summary-title { font-weight: 700; color: #1f2d3d; }
.summary-stream {
  max-height: 320px; overflow: auto; background: #0f1c2e; color: #7fe7c4;
  border-radius: 6px; padding: 12px; font-size: 12px; white-space: pre-wrap; word-break: break-all;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
}
/* AI 产出的 HTML 评价:限定样式作用域,基础排版 */
.summary-html { line-height: 1.7; color: #34495e; font-size: 13px; }
.summary-html :deep(h2) { font-size: 16px; margin: 14px 0 8px; color: #1f2d3d; border-left: 3px solid #00b386; padding-left: 8px; }
.summary-html :deep(h3) { font-size: 14px; margin: 10px 0 6px; color: #34495e; }
.summary-html :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0; }
.summary-html :deep(th), .summary-html :deep(td) { border: 1px solid #dfe6ec; padding: 6px 10px; text-align: left; font-size: 12px; }
.summary-html :deep(th) { background: #f3f8f7; color: #1f2d3d; }
.summary-html :deep(ul), .summary-html :deep(ol) { padding-left: 22px; margin: 6px 0; }
.summary-html :deep(blockquote) { border-left: 3px solid #dfe6ec; margin: 8px 0; padding: 4px 12px; color: #7d8a9b; background: #f8fafc; }
.summary-html :deep(code) { background: #eef2f6; border-radius: 3px; padding: 1px 5px; font-family: 'JetBrains Mono', monospace; font-size: 12px; }
.d-table { width: 100%; }
</style>

<style scoped>
.experiment-overview { margin-bottom: 18px; padding: 14px; border: 1px solid #e4e7ed; border-radius: 8px; }
.experiment-overview details { margin-top: 12px; }
.experiment-json { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 300px; overflow: auto; }
</style>
