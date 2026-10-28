import * as fs from 'node:fs';
import * as path from 'node:path';

/** 未分配负责人标记：board.assignee 为此值时，上报平台 assigned_to=null（不派活，等人工分配） */
export const UNASSIGNED = 'unassigned';
export const SOURCE_GROUP_ID = '7652669649127481';

export type PlatformEntry = {
  ticketId: string;
  module: string;
  assignee: string;
  priority: string;
  status: 'queued' | 'testing' | 'done';
  assignedAt: string;
  reportedToPlatform?: boolean;
  description?: string;
  requirementUrl?: string;
  requester?: string;
  sourceMessageId?: string;
  platformTaskId?: number;
  platformDeleted?: boolean;
};

// ---- 配置类型 ----
export type PlatformConfig = {
  enabled: boolean;
  baseUrl: string;
  projectCode: string;
  username: string;
  password: string;
  timeoutMs?: number;
};

// ---- 纯函数（可测，无 I/O）----

/** dispatch 优先级 P0/P1/P2/P3 → 平台枚举 p0/p1/p2/p3；未知回落 p2 */
export function toPlatformPriority(p: string): string {
  const v = (p || '').trim().toLowerCase();
  return ['p0', 'p1', 'p2', 'p3'].includes(v) ? v : 'p2';
}

/** 功能/需求使用任务说明全文；缺失说明时保留待回填，不能再用单号建占位任务。 */
export function buildTaskTitle(entry: PlatformEntry): string {
  if (!entry.description?.trim()) throw new Error('missing-description: 缺少任务说明，保留待回填');
  return entry.description;
}

/** 原消息 ID 始终用字符串，避免 64 位 ID 精度损失；兼容旧登记的 run 命名。 */
export function sourceMessageId(entry: PlatformEntry): string {
  if (entry.sourceMessageId !== undefined) {
    if (typeof entry.sourceMessageId !== 'string' || !entry.sourceMessageId.trim()) {
      throw new Error('invalid-source-message-id');
    }
    return entry.sourceMessageId.trim();
  }
  const original = entry.ticketId.match(/^\d{4}-\d{2}-\d{2}-.+-(\d{16,22})$/)?.[1];
  // 无原消息 ID 的旧记录也必须通过服务端幂等入口，不回退普通建任务接口。
  return original ?? `qa-dispatch:${entry.ticketId}`;
}

/** 只录入提测，不传负责人和工作流状态；单号不混入业务说明。 */
export function entryToTaskPayload(
  entry: PlatformEntry,
  projectId: number,
  _assignedToId?: number | null,
): Record<string, unknown> {
  const description = buildTaskTitle(entry);
  return {
    project_id: projectId,
    source_group_id: SOURCE_GROUP_ID,
    source_message_id: sourceMessageId(entry),
    title: description,
    description,
    module: entry.module,
    priority: toPlatformPriority(entry.priority),
    assigned_date: entry.assignedAt,
    requirement_url: entry.requirementUrl ?? null,
    developer: entry.requester ?? null,
  };
}

// ---- 配置读取 ----

export function loadPlatformConfig(baseDir: string): PlatformConfig | null {
  const p = path.join(baseDir, 'config', 'platform.json');
  if (!fs.existsSync(p)) return null;
  try {
    const raw = JSON.parse(fs.readFileSync(p, 'utf8'));
    return raw as PlatformConfig;
  } catch (e) {
    console.error('[platform] config/platform.json 解析失败：' + (e as Error).message);
    return null;
  }
}

// ---- HTTP 辅助（依赖 Node 18+ 内置 fetch）----

export async function apiFetch(
  cfg: PlatformConfig,
  method: string,
  urlPath: string,
  body?: unknown,
  token?: string,
): Promise<any> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.timeoutMs ?? 8000);
  try {
    const resp = await fetch(cfg.baseUrl.replace(/\/$/, '') + urlPath, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const json: any = await resp.json().catch(() => null);
    // 平台统一信封 {code,msg,data}；code!==0 视为失败
    if (!resp.ok || !json || json.code !== 0) {
      const msg = json?.msg || `HTTP ${resp.status}`;
      throw new Error(msg);
    }
    return json.data;
  } finally {
    clearTimeout(timer);
  }
}

export async function login(cfg: PlatformConfig): Promise<string> {
  const data = await apiFetch(cfg, 'POST', '/api/auth/login', {
    username: cfg.username,
    password: cfg.password,
  });
  if (!data?.access_token) throw new Error('登录未返回 access_token');
  return data.access_token as string;
}

/** 按 projectCode 找 project_id（列表里匹配 code） */
export async function resolveProjectId(cfg: PlatformConfig, token: string): Promise<number> {
  const list = await apiFetch(cfg, 'GET', '/api/projects', undefined, token);
  const arr = Array.isArray(list) ? list : [];
  const hit = arr.find((p: any) => p.code === cfg.projectCode);
  if (!hit) throw new Error(`项目 code=${cfg.projectCode} 在平台不存在或当前账号无权访问`);
  return hit.id as number;
}

// ---- 对外主函数：上报一批 entry ----

export type ReportResult = {
  ok: boolean;
  created: number;
  alreadyExists: string[]; // 已确认关联到平台任务的提测单
  skipped: string[]; // 已删除或缺失必要信息的提测单
  confirmed: { ticketId: string; taskId: number; created: boolean }[];
  errors: string[];
};

/** board 状态 → 平台状态枚举（pending/testing/blocked/online） */
export function mapBoardStatusToPlatform(
  s: string,
): 'pending' | 'testing' | 'blocked' | 'online' {
  switch ((s || '').trim().toLowerCase()) {
    case 'testing':
      return 'testing';
    case 'done':
      return 'online';
    case 'queued':
    default:
      return 'pending';
  }
}

/** 旧单号必须按完整 token 匹配，JIRA-12 不能命中 JIRA-123。 */
function containsTicket(value: unknown, ticketId: string): boolean {
  if (typeof value !== 'string' || !ticketId) return false;
  const escaped = ticketId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?:^|[^A-Za-z0-9_-])${escaped}(?=$|[^A-Za-z0-9_-])`).test(value);
}

function uniqueTaskId(tasks: any[]): number | null {
  const ids = [...new Set(tasks.map((task) => task.id))];
  if (ids.length > 1) throw new Error(`ambiguous-existing-task: ${ids.join(',')}`);
  return ids[0] ?? null;
}

export function findTaskIdByTicketId(tasks: any[], ticketId: string): number | null {
  return uniqueTaskId((Array.isArray(tasks) ? tasks : []).filter((task) =>
    containsTicket(task.title, ticketId) || containsTicket(task.description, ticketId)));
}

/** 只使用已保存的关联、原消息 ID 或完整旧单号进行状态同步。 */
export function findTaskIdForEntry(tasks: any[], entry: PlatformEntry): number | null {
  const list = Array.isArray(tasks) ? tasks : [];
  if (entry.platformTaskId !== undefined) {
    if (!Number.isSafeInteger(entry.platformTaskId) || entry.platformTaskId <= 0) {
      throw new Error('invalid-platform-task-id');
    }
    if (!list.some((task) => task.id === entry.platformTaskId)) {
      throw new Error('task-not-found: 已关联的平台任务不存在，禁止重新创建');
    }
    return entry.platformTaskId;
  }
  const messageId = sourceMessageId(entry);
  const id = uniqueTaskId(list.filter((task) =>
    task.source_message_id === messageId && task.source_group_id === SOURCE_GROUP_ID ||
    !task.source_message_id && [task.title, task.description].some((value) =>
      typeof value === 'string' && [...value.matchAll(/\d{4}-\d{2}-\d{2}-[A-Za-z0-9_-]+-(\d{16,22})(?![A-Za-z0-9_-])/g)]
        .some((match) => match[1] === messageId))));
  return id ?? findTaskIdByTicketId(list, entry.ticketId);
}

const normalized = (value: unknown): string => typeof value === 'string'
  ? value.trim().replace(/\s+/g, ' ') : '';

function normalizedUrl(value: unknown): string {
  const raw = normalized(value);
  if (!raw) return '';
  try {
    const url = new URL(raw);
    url.searchParams.sort();
    return url.toString();
  } catch { return raw; }
}

/** 兼容手工录入的历史任务；疑似同任务但信息冲突时暂停，绝不猜测合并或继续新建。 */
export function findExistingTaskForReport(tasks: any[], entry: PlatformEntry): number | null {
  const known = findTaskIdForEntry(tasks, entry);
  if (known !== null) return known;
  const description = normalized(entry.description);
  if (!description) return null;
  const firstClause = normalized(entry.description?.split(/[，,。；;\n]/)[0]);
  const candidates = (Array.isArray(tasks) ? tasks : []).filter((task) => {
    if (task.source_message_id) return false; // 另一条原消息不能仅靠内容合并
    const title = normalized(task.title);
    return title === description || normalized(task.description) === description ||
      // 短业务标题 + 提测补充说明只视为疑似重复，必须人工确认关联。
      firstClause.length >= 6 && title === firstClause;
  });
  if (!candidates.length) return null;
  const safe = candidates.filter((task) => {
    const exact = normalized(task.title) === description || normalized(task.description) === description;
    const developer = normalized(entry.requester);
    const taskDeveloper = normalized(task.developer);
    const url = normalizedUrl(entry.requirementUrl);
    const taskUrl = normalizedUrl(task.requirement_url);
    const conflict = developer && taskDeveloper && developer !== taskDeveloper || url && taskUrl && url !== taskUrl;
    const corroborated = developer && developer === taskDeveloper || url && url === taskUrl;
    return exact && !conflict && corroborated;
  });
  if (candidates.length === 1 && safe.length === 1) return safe[0].id;
  throw new Error(`ambiguous-existing-task: 疑似已有任务 ${candidates.map((task) => task.id).join(',')}，保留待回填并核对关联`);
}

/**
 * 反向校准（纯函数，原地修改 entries）：平台状态比 board 领先时，以平台为准回写 board。
 * 场景：负责人在平台上手动把任务改为 testing / 关闭（closed），若仍用 board 单向覆盖平台，
 * 每轮同步都会把人工操作改回去，形成拉锯。
 * 规则：
 *  - 平台 closed：尊重关闭，不做正向覆盖；board 非 done 则置 done；记入 closedTicketIds（正向同步跳过）
 *  - 平台 testing/online 领先于 board（queued/testing）：board 反向升级（testing→testing，online→done）
 *  - 其余（一致或平台落后）：不动，交给正向同步处理
 * 返回被反向校准的说明列表与平台已关闭的 ticketId 集合。
 */
export function reconcileEntriesWithPlatform(
  entries: PlatformEntry[],
  tasks: any[],
): { reversed: string[]; closedTicketIds: string[] } {
  const rank: Record<string, number> = { pending: 0, testing: 1, online: 2, closed: 2 };
  const reversed: string[] = [];
  const closedTicketIds: string[] = [];
  const taskList = Array.isArray(tasks) ? tasks : [];
  for (const e of entries) {
    const taskId = findTaskIdForEntry(taskList, e);
    if (taskId === null) continue;
    const cur = taskList.find((t: any) => t.id === taskId);
    if (!cur || typeof cur.status !== 'string') continue;
    if (cur.status === 'closed') {
      closedTicketIds.push(e.ticketId);
      if (e.status !== 'done') {
        reversed.push(`${e.ticketId}: ${e.status}→done（平台已关闭）`);
        e.status = 'done';
      }
      continue;
    }
    const target = mapBoardStatusToPlatform(e.status);
    if ((rank[cur.status] ?? 0) > (rank[target] ?? 0)) {
      const next: PlatformEntry['status'] = cur.status === 'testing' ? 'testing' : 'done';
      reversed.push(`${e.ticketId}: ${e.status}→${next}（平台 ${cur.status}）`);
      e.status = next;
    }
  }
  return { reversed, closedTicketIds };
}

/**
 * 同步一条 board entry 的状态到平台（幂等：平台已是目标状态则跳过）。
 * 按 ticketId 从 tasks 列表匹配平台任务，然后 PATCH /tasks/{id} {status}。
 */
export async function syncEntryStatus(
  cfg: PlatformConfig,
  entry: PlatformEntry,
): Promise<{ ok: boolean; taskId?: number; skipped?: boolean; error?: string }> {
  const target = mapBoardStatusToPlatform(entry.status);
  if (!cfg.enabled) {
    console.error('[platform] 状态同步已禁用 (enabled=false)，跳过');
    return { ok: false, error: 'disabled' };
  }
  try {
    const token = await login(cfg);
    const projectId = await resolveProjectId(cfg, token);
    const tasks = await apiFetch(cfg, 'GET', `/api/tasks?project_id=${projectId}`, undefined, token);
    const taskId = findTaskIdForEntry(tasks, entry);
    if (taskId === null) {
      console.error(`[platform] ⚠ ticket ${entry.ticketId} 在平台无对应任务，跳过状态同步`);
      return { ok: false, error: 'task-not-found' };
    }
    const cur = tasks.find((t: any) => t.id === taskId);
    const rank: Record<string, number> = { unassigned: 0, pending: 0, testing: 1, online: 2 };
    if (cur && (cur.status === target || cur.status === 'closed' || cur.status === 'blocked' ||
      (rank[cur.status] ?? 0) > (rank[target] ?? 0) ||
      cur.status === 'unassigned' && target === 'pending')) {
      // 平台已是目标状态，无需重复 PATCH
      return { ok: true, taskId, skipped: true };
    }
    const updated = await apiFetch(cfg, 'PATCH', `/api/tasks/${taskId}`, { status: target }, token);
    return { ok: true, taskId, skipped: false };
  } catch (e) {
    const msg = (e as Error).message;
    console.error(`[platform] ✗ ticket ${entry.ticketId} 状态同步失败：${msg}`);
    return { ok: false, error: msg };
  }
}

/** 批量同步多条 entry 的状态 */
export async function syncEntriesStatus(
  cfg: PlatformConfig,
  entries: PlatformEntry[],
): Promise<{ ok: number; skipped: number; failed: number; errors: string[] }> {
  const result = { ok: 0, skipped: 0, failed: 0, errors: [] as string[] };
  for (const entry of entries) {
    const r = await syncEntryStatus(cfg, entry);
    if (r.ok) {
      if (r.skipped) result.skipped += 1;
      else result.ok += 1;
      console.log(`[platform] ✓ ticket ${entry.ticketId} 状态已同步 → ${mapBoardStatusToPlatform(entry.status)}`);
    } else {
      result.failed += 1;
      result.errors.push(`${entry.ticketId}: ${r.error}`);
    }
  }
  return result;
}

/**
 * 统一使用原消息 ID + /tasks/ingest 的数据库唯一键，重复/并发回填不会多建任务。
 * 只有得到平台确认才回写单条 reportedToPlatform；失败项保留供重试。
 */
export async function reportEntries(cfg: PlatformConfig, entries: PlatformEntry[]): Promise<ReportResult> {
  const result: ReportResult = {
    ok: false, created: 0, alreadyExists: [], skipped: [], errors: [], confirmed: [],
  };
  if (!cfg.enabled) return result;
  if (!entries.length) return { ...result, ok: true };
  try {
    const token = await login(cfg);
    const projectId = await resolveProjectId(cfg, token);
    for (const entry of entries) {
      if (entry.platformDeleted) {
        result.skipped.push(entry.ticketId);
        continue;
      }
      try {
        const tasks = await apiFetch(cfg, 'GET', `/api/tasks?project_id=${projectId}`, undefined, token);
        let taskId = findExistingTaskForReport(tasks, entry);
        let created = false;
        if (taskId === null) {
          const data = await apiFetch(cfg, 'POST', '/api/tasks/ingest', entryToTaskPayload(entry, projectId), token);
          taskId = data?.task?.id;
          created = data?.created;
          if (!Number.isSafeInteger(taskId) || (taskId as number) <= 0 || typeof created !== 'boolean') {
            throw new Error('invalid-ingest-response');
          }
        }
        if (created) result.created += 1;
        else result.alreadyExists.push(entry.ticketId);
        entry.platformTaskId = taskId!;
        entry.reportedToPlatform = true;
        result.confirmed.push({ ticketId: entry.ticketId, taskId: taskId!, created });
      } catch (error) {
        const message = (error as Error).message;
        result.errors.push(`${entry.ticketId}: ${message}`);
        console.error(`[platform] ✗ ticket ${entry.ticketId} 上报失败：${message}`);
      }
    }
    result.ok = result.errors.length === 0 && result.skipped.length === 0;
  } catch (error) {
    result.errors.push((error as Error).message);
  }
  return result;
}
