// 成功的空表也是权威结果；网络失败不能偷偷运行旧表或另一项目的内置表。
export function registrySnapshot(data) {
  if (!data || !data.registry || typeof data.registry !== 'object' || Array.isArray(data.registry)) {
    throw Object.assign(new Error('选择器注册表响应无效'), { fail_kind: 'selector' });
  }
  return JSON.parse(JSON.stringify(data));
}

export async function loadRegistry(api, projectId, sub = '') {
  if (!projectId) throw Object.assign(new Error('缺少选择器项目上下文'), { fail_kind: 'selector' });
  try {
    const data = await api('GET', `/api/selectors?project_id=${projectId}&sub_product=${encodeURIComponent(sub)}`);
    return registrySnapshot(data?.data || data);
  } catch (error) {
    error.fail_kind = 'selector';
    throw error;
  }
}
