// API 保留单页数组契约，页面依次读取全部游标页后再做“已选”/任务/标题筛选。
export async function loadEvalQueryPages(fetchPage, filters) {
  const pageSize = 200
  const rows = []
  let beforeId = null
  while (true) {
    const page = await fetchPage({ ...filters, limit: pageSize, ...(beforeId ? { before_id: beforeId } : {}) })
    if (!Array.isArray(page)) throw new Error('用例列表格式异常，请重试')
    if (!page.length) return rows
    const ids = page.map(row => row.id)
    if (ids.some(id => !Number.isSafeInteger(id) || id <= 0 || (beforeId && id >= beforeId)) || new Set(ids).size !== ids.length) {
      throw new Error('用例分页未正确返回，请确认平台已更新后重试')
    }
    rows.push(...page)
    if (page.length < pageSize) return rows
    beforeId = Math.min(...ids)
  }
}
