const entry = (path, label, icon, extra = {}) => ({ path, label, icon, ...extra })

export const navigationGroups = [
  { id: 'workspace', label: '工作空间', icon: 'Odometer', items: [
    entry('/dashboard', '工作台', 'Odometer'), entry('/commander', '测试指挥官', 'ChatDotRound'),
    entry('/my-reports', '我的日报', 'EditPen', { reports: true }),
  ] },
  { id: 'eval', label: '对话测评', icon: 'ChatDotRound', items: [
    entry('/eval-tasks', '测评任务', 'Tickets'), entry('/eval-library', '测评用例库', 'Collection'),
    entry('/ai-eval-gen', '测评生成', 'MagicStick'), entry('/eval-results', '测评结果', 'Finished'),
  ] },
  { id: 'func', label: '功能测试', icon: 'MagicStick', items: [
    entry('/test-plans', '测试计划', 'Calendar'), entry('/tasks', '任务分配', 'List'),
    entry('/ai-testgen', 'AI 测试助手', 'MagicStick'), entry('/case-library', '功能用例库', 'Collection'),
    entry('/adopted-cases', '已采纳用例', 'Select'), entry('/regression-cases', '回归用例库', 'RefreshRight'),
    entry('/exec-results', '执行结果', 'Finished'), entry('/issues', '遗留问题', 'Warning'),
  ] },
  { id: 'feedback', label: '反馈回归', icon: 'ChatLineSquare', items: [
    entry('/feedback-imports', '导入记录', 'UploadFilled'), entry('/feedback-cases', '反馈用例库', 'Collection'),
    entry('/feedback-regression', '回归用例集', 'RefreshRight'), entry('/feedback-results', '回归结果', 'Finished'),
  ] },
  { id: 'perf', label: '性能测试', icon: 'Stopwatch', items: [
    entry('/perf-dispatch', '任务下发', 'Promotion'), entry('/perf-report', '性能报告', 'Histogram'),
  ] },
  { id: 'quality', label: '质量与发布', icon: 'DataLine', items: [
    entry('/war-room', '作战大屏', 'DataBoard'), entry('/requirements', '需求覆盖', 'Link'),
    entry('/release-checklist', '上线 checklist', 'Checked'), entry('/releases', '发版记录', 'Promotion'),
    entry('/stats', '日报统计', 'DataAnalysis'), entry('/workload', '工作量统计', 'TrendCharts'),
    entry('/ai-wall', 'AI 战绩墙', 'Trophy'), entry('/fail-clusters', '版本质量聚焦', 'Filter', { admin: true }),
    entry('/rts', '回归智选', 'Aim', { admin: true }),
  ] },
  { id: 'resources', label: '设备与工具', icon: 'Monitor', items: [
    entry('/device-board', '设备看板', 'Cpu'), entry('/my-devices', '我的设备', 'Monitor'), entry('/tool-plaza', '工具广场', 'Grid'),
  ] },
  { id: 'settings', label: '系统管理', icon: 'Setting', items: [
    entry('/projects', '项目管理', 'Files', { admin: true }), entry('/users', '用户管理', 'User', { admin: true }),
    entry('/selectors', '选择器管理', 'Aim', { admin: true }), entry('/api-env', 'API 环境', 'Connection', { admin: true }),
    entry('/tool-admin', '工具配置', 'Grid', { admin: true }),
  ] },
]

export function visibleNavigation({ admin = false, reports = false, search = '' } = {}) {
  const term = search.trim().toLocaleLowerCase()
  return navigationGroups.map(group => ({ ...group, items: group.items.filter(item =>
    (!item.admin || admin) && (!item.reports || reports) &&
    (!term || `${group.label} ${item.label}`.toLocaleLowerCase().includes(term))) })).filter(group => group.items.length)
}
