import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { spawn } from 'node:child_process';
import test from 'node:test';
import assert from 'node:assert/strict';
import * as http from 'node:http';
import {
  toPlatformPriority,
  buildTaskTitle,
  entryToTaskPayload,
  mapBoardStatusToPlatform,
  findTaskIdByTicketId,
  reportEntries,
  reconcileEntriesWithPlatform,
  sourceMessageId,
  findTaskIdForEntry,
  findExistingTaskForReport,
  syncEntryStatus,
  SOURCE_GROUP_ID,
} from './platform-report';
import type { PlatformEntry as BoardEntry } from './platform-report';
import type { PlatformConfig, PlatformEntry } from './platform-report';

const entry: BoardEntry = {
  ticketId: 'JIRA-123',
  module: '支付',
  assignee: 'lixueyuan',
  priority: 'P0',
  status: 'queued',
  assignedAt: '2026-08-06',
  description: '任务分享页优化，统一框架与排版修复',
};

test('toPlatformPriority: P0/P1/P2/P3 转小写，未知回落 p2', () => {
  assert.equal(toPlatformPriority('P0'), 'p0');
  assert.equal(toPlatformPriority('P1'), 'p1');
  assert.equal(toPlatformPriority('p2'), 'p2');
  assert.equal(toPlatformPriority('P3'), 'p3');
  assert.equal(toPlatformPriority(''), 'p2');
  assert.equal(toPlatformPriority('urgent'), 'p2');
});

test('buildTaskTitle: 使用完整任务说明，不用单号', () => {
  assert.equal(buildTaskTitle(entry), entry.description);
  assert.throws(() => buildTaskTitle({ ...entry, description: '  ' }), /missing-description/);
});

test('entryToTaskPayload: 字段映射到平台建任务入参', () => {
  const payload = entryToTaskPayload(entry, 7, 42);
  assert.equal(payload.project_id, 7);
  assert.equal('assigned_to' in payload, false);
  assert.equal('status' in payload, false);
  assert.equal(payload.source_group_id, SOURCE_GROUP_ID);
  assert.equal(payload.source_message_id, 'qa-dispatch:JIRA-123');
  assert.equal(payload.priority, 'p0');
  assert.equal(payload.assigned_date, '2026-08-06');
  assert.equal(payload.module, '支付');
  assert.equal(payload.title, entry.description);
  assert.equal(payload.description, entry.description);
});

test('entryToTaskPayload: 带需求说明/地址/提测人的完整映射', () => {
  const full = {
    ...entry,
    description: '任务分享页优化，统一框架与排版修复',
    requirementUrl: 'https://geelib.qihoo.net/geelib/project/matterType/19262?subId=6548&matterId=963849&typeId=19262',
    requester: '谢淼',
  };
  const payload = entryToTaskPayload(full, 7, 42);
  assert.equal(payload.description, '任务分享页优化，统一框架与排版修复');
  assert.equal(
    payload.requirement_url,
    'https://geelib.qihoo.net/geelib/project/matterType/19262?subId=6548&matterId=963849&typeId=19262',
  );
  assert.equal(payload.developer, '谢淼');
});

test('entryToTaskPayload: 可选字段为空', () => {
  const payload = entryToTaskPayload(entry, 7, 42);
  assert.equal(payload.requirement_url, null);
  assert.equal(payload.developer, null);
});

test('mapBoardStatusToPlatform: board 状态映射到平台枚举', () => {
  assert.equal(mapBoardStatusToPlatform('queued'), 'pending');
  assert.equal(mapBoardStatusToPlatform('testing'), 'testing');
  assert.equal(mapBoardStatusToPlatform('done'), 'online');
  assert.equal(mapBoardStatusToPlatform(''), 'pending');
  assert.equal(mapBoardStatusToPlatform('DONE'), 'online');
});

test('findTaskIdByTicketId: 按 title/description 里的 ticketId 精确找平台任务', () => {
  const tasks = [
    { id: 1, title: '【支付】提测 JIRA-123' },
    { id: 2, title: '【官网】提测 JIRA-456', description: 'xx' },
    { id: 3, title: '无关任务' },
  ];
  assert.equal(findTaskIdByTicketId(tasks, 'JIRA-123'), 1);
  assert.equal(findTaskIdByTicketId(tasks, 'JIRA-456'), 2);
  assert.equal(findTaskIdByTicketId(tasks, 'JIRA-789'), null);
  assert.equal(findTaskIdByTicketId([], 'JIRA-123'), null);
});

test('reconcileEntriesWithPlatform: 平台 testing 领先 queued → board 反向置 testing', () => {
  const entries: BoardEntry[] = [
    { ...entry, ticketId: 'T-1', status: 'queued' },
  ];
  const tasks = [{ id: 10, title: '【支付】提测 T-1', status: 'testing' }];
  const r = reconcileEntriesWithPlatform(entries, tasks);
  assert.equal(entries[0].status, 'testing');
  assert.equal(r.reversed.length, 1);
  assert.deepEqual(r.closedTicketIds, []);
});

test('reconcileEntriesWithPlatform: 平台 closed → board 置 done 且记入 closedTicketIds', () => {
  const entries: BoardEntry[] = [
    { ...entry, ticketId: 'T-1', status: 'queued' },
    { ...entry, ticketId: 'T-2', status: 'done' },
  ];
  const tasks = [
    { id: 10, title: '【支付】提测 T-1', status: 'closed' },
    { id: 11, title: '【支付】提测 T-2', status: 'closed' },
  ];
  const r = reconcileEntriesWithPlatform(entries, tasks);
  assert.equal(entries[0].status, 'done', 'queued 被平台关闭 → done');
  assert.equal(entries[1].status, 'done', '已是 done 保持 done');
  assert.deepEqual(r.closedTicketIds, ['T-1', 'T-2']);
  assert.equal(r.reversed.length, 1, '只有 T-1 状态发生了变化');
});

test('reconcileEntriesWithPlatform: 状态一致或平台落后 → 不改动', () => {
  const entries: BoardEntry[] = [
    { ...entry, ticketId: 'T-1', status: 'done' },     // 平台 online，一致
    { ...entry, ticketId: 'T-2', status: 'testing' },  // 平台 pending，平台落后
  ];
  const tasks = [
    { id: 10, title: '【支付】提测 T-1', status: 'online' },
    { id: 11, title: '【支付】提测 T-2', status: 'pending' },
  ];
  const r = reconcileEntriesWithPlatform(entries, tasks);
  assert.equal(entries[0].status, 'done');
  assert.equal(entries[1].status, 'testing');
  assert.deepEqual(r.reversed, []);
  assert.deepEqual(r.closedTicketIds, []);
});

test('reconcileEntriesWithPlatform: 平台 online 领先 testing → board 置 done', () => {
  const entries: BoardEntry[] = [
    { ...entry, ticketId: 'T-1', status: 'testing' },
  ];
  const tasks = [{ id: 10, title: '【支付】提测 T-1', status: 'online' }];
  const r = reconcileEntriesWithPlatform(entries, tasks);
  assert.equal(entries[0].status, 'done');
  assert.equal(r.reversed.length, 1);
});


type MockOptions = { tasks?: any[]; staleList?: boolean; failIds?: string[]; failIngest?: boolean };
async function withPlatform(run: (cfg: PlatformConfig, state: any) => Promise<void>, options: MockOptions = {}) {
  const state = { tasks: structuredClone(options.tasks ?? []), posts: [] as any[], patches: [] as any[], nextId: 100 };
  const server = http.createServer(async (req, res) => {
    res.setHeader('Content-Type', 'application/json');
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : null;
    const respond = (data: unknown) => res.end(JSON.stringify({ code: 0, data }));
    if (req.url === '/api/auth/login') return void respond({ access_token: 'local-test-token' });
    if (req.url === '/api/projects') return void respond([{ id: 1, code: 'nami_pc' }]);
    if (req.method === 'GET' && req.url === '/api/tasks?project_id=1') {
      return void respond(options.staleList ? [] : state.tasks);
    }
    if (req.method === 'POST' && req.url === '/api/tasks/ingest') {
      state.posts.push(body);
      if (options.failIngest || options.failIds?.includes(body.source_message_id)) {
        res.statusCode = 503;
        return void res.end(JSON.stringify({ code: 503, msg: 'test unavailable' }));
      }
      let task = state.tasks.find((t: any) => t.source_message_id === body.source_message_id &&
        t.source_group_id === body.source_group_id && t.project_id === body.project_id);
      const created = !task;
      if (!task) {
        task = { ...body, id: state.nextId++, status: 'unassigned', assigned_to_ids: [] };
        state.tasks.push(task);
      }
      return void respond({ task, created });
    }
    if (req.method === 'PATCH') {
      state.patches.push({ url: req.url, body });
      return void respond({});
    }
    state.posts.push({ unexpected: `${req.method} ${req.url}` });
    res.statusCode = 404;
    res.end(JSON.stringify({ code: 404, msg: 'unexpected route' }));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const cfg: PlatformConfig = { enabled: true, baseUrl: `http://127.0.0.1:${(server.address() as any).port}`,
      projectCode: 'nami_pc', username: 'test', password: 'test' };
    await run(cfg, state);
  } finally { await new Promise<void>((resolve) => server.close(() => resolve())); }
}
const messageId = '7701555006439668209';
const submission: PlatformEntry = { ...entry, ticketId: `2026-10-28-manual-${messageId}`, assignee: 'unassigned',
  requester: '周令', requirementUrl: 'https://example.test/requirements/1' };

test('原消息 ID 跨日期/run 名不变，保持 64 位字符串精度', () => {
  assert.equal(sourceMessageId(submission), messageId);
  assert.equal(sourceMessageId({ ...submission, ticketId: `2026-10-29-retry-${messageId}` }), messageId);
  assert.throws(() => sourceMessageId({ ...submission, sourceMessageId: 123 as any }), /invalid-source/);
});

test('旧单号不会误中更长的单号，正则字符按字面匹配', () => {
  const tasks = [{ id: 1, title: '【支付】提测 JIRA-123' }, { id: 2, description: '提测单：A[1]+B' }];
  assert.equal(findTaskIdByTicketId(tasks, 'JIRA-12'), null);
  assert.equal(findTaskIdByTicketId(tasks, 'A[1]+B'), 2);
});

test('重复回填按原消息 ID 关联，人工改名/负责人不会影响识别', async () => {
  const task = { id: 440, title: '管理员改名', source_group_id: SOURCE_GROUP_ID,
    source_message_id: messageId, assigned_to_ids: [5], status: 'testing' };
  await withPlatform(async (cfg, state) => {
    const candidate = { ...submission };
    const r = await reportEntries(cfg, [candidate]);
    assert.equal(r.created, 0);
    assert.deepEqual(r.alreadyExists, [candidate.ticketId]);
    assert.equal(candidate.platformTaskId, 440);
    assert.equal(candidate.reportedToPlatform, true);
    assert.deepEqual(state.tasks, [task]);
    assert.equal(state.posts.length, 0);
  }, { tasks: [task] });
});

test('新回填使用 ingest，完整保留 20000 字说明，不派单、不写占位单号', async () => {
  await withPlatform(async (cfg, state) => {
    const description = '长说明\n'.repeat(5000);
    const candidate = { ...submission, description, assignee: 'old-owner' };
    const r = await reportEntries(cfg, [candidate]);
    assert.equal(r.ok, true);
    assert.equal(r.created, 1);
    assert.equal(candidate.platformTaskId, 100);
    assert.equal(state.tasks[0].title, description);
    assert.equal(state.tasks[0].description, description);
    assert.equal('assigned_to' in state.posts[0], false);
    assert.equal('status' in state.posts[0], false);
    assert.equal(state.tasks[0].source_message_id, messageId);
  });
});

test('并发回填即使列表都是旧快照，也只有一条任务', async () => {
  await withPlatform(async (cfg, state) => {
    const first = { ...submission };
    const second = { ...submission, ticketId: `2026-10-29-retry-${messageId}` };
    const results = await Promise.all([reportEntries(cfg, [first]), reportEntries(cfg, [second])]);
    assert.equal(state.tasks.length, 1);
    assert.equal(results.reduce((n, r) => n + r.created, 0), 1);
    assert.equal(first.platformTaskId, second.platformTaskId);
    assert.ok(first.reportedToPlatform && second.reportedToPlatform);
  }, { staleList: true });
});

test('原消息 ID 同时兼容旧标题/说明中的不同 run 单号', () => {
  const title = `【左侧栏导航与首页】提测 2026-10-27-cron-${messageId}`;
  assert.equal(findTaskIdForEntry([{ id: 8, title }], submission), 8);
  assert.equal(findTaskIdForEntry([{ id: 9, description: `任务说明\n提测单：2026-10-27-other-${messageId}` }], submission), 9);
});

test('同说明同开发且需求地址一致的手工任务只关联，保留负责人和状态', async () => {
  const task = { id: 440, title: submission.description, developer: '周令', requirement_url: submission.requirementUrl,
    status: 'testing', assigned_to_ids: [5] };
  await withPlatform(async (cfg, state) => {
    const candidate = { ...submission };
    const r = await reportEntries(cfg, [candidate]);
    assert.equal(r.created, 0);
    assert.equal(candidate.platformTaskId, 440);
    await syncEntryStatus(cfg, candidate);
    assert.equal(state.posts.length, 0);
    assert.equal(state.patches.length, 0);
    assert.deepEqual(state.tasks[0], task);
  }, { tasks: [task] });
});

test('截图场景：短标题疑似已有任务但需求链接冲突，保留待回填，不重复创建', async () => {
  const task = { id: 440, title: '小纳入口移动到左侧一级导航', developer: '周令',
    requirement_url: 'https://example.test/requirements/1058878', assigned_to_ids: [5], status: 'pending' };
  await withPlatform(async (cfg, state) => {
    const candidate = { ...submission, description: '小纳入口移动到左侧一级导航，这个一起测下，部署到测试环境了',
      requirementUrl: 'https://example.test/requirements/1058877' };
    const r = await reportEntries(cfg, [candidate]);
    assert.equal(r.ok, false);
    assert.match(r.errors[0], /ambiguous-existing-task.*440/);
    assert.equal(candidate.reportedToPlatform, undefined);
    assert.equal(state.posts.length, 0);
    assert.equal(state.patches.length, 0);
  }, { tasks: [task] });
});

test('同需求链接不同业务内容不合并，来源不同的消息不靠相似内容合并', () => {
  assert.equal(findExistingTaskForReport([{ id: 1, title: '其他业务功能', requirement_url: submission.requirementUrl }], submission), null);
  assert.equal(findExistingTaskForReport([{ id: 2, title: submission.description, source_message_id: 'other',
    source_group_id: SOURCE_GROUP_ID }], submission), null);
  assert.throws(() => findExistingTaskForReport([{ id: 1, title: submission.description },
    { id: 2, title: submission.description }], submission), /ambiguous-existing-task/);
});

test('持久化关联在平台改名后仍有效，已删除的关联不重建', async () => {
  assert.equal(findTaskIdForEntry([{ id: 440, title: '新名称' }], { ...submission, platformTaskId: 440 }), 440);
  await withPlatform(async (cfg, state) => {
    const candidate = { ...submission, platformTaskId: 440 };
    const r = await reportEntries(cfg, [candidate]);
    assert.equal(r.ok, false);
    assert.match(r.errors[0], /task-not-found/);
    assert.equal(state.posts.length, 0);
  });
});

test('混合批次只标记已确认成功的条目，失败、缺失说明和删除项不冒充成功', async () => {
  await withPlatform(async (cfg, state) => {
    const good = { ...submission };
    const failed = { ...submission, ticketId: 'FAILED' };
    const blank = { ...submission, ticketId: 'EMPTY', description: '' };
    const deleted = { ...submission, ticketId: 'DELETED', platformDeleted: true };
    const r = await reportEntries(cfg, [failed, good, blank, deleted]);
    assert.equal(r.created, 1);
    assert.equal(r.ok, false);
    assert.deepEqual(r.confirmed, [{ ticketId: good.ticketId, taskId: 100, created: true }]);
    assert.equal(good.reportedToPlatform, true);
    for (const item of [failed, blank, deleted]) assert.equal(item.reportedToPlatform, undefined);
    assert.equal(state.tasks.length, 1);
  }, { failIds: ['qa-dispatch:FAILED'] });
});

test('ingest 不可用时保留待回填，不回退普通建任务接口', async () => {
  await withPlatform(async (cfg, state) => {
    const candidate = { ...submission };
    const r = await reportEntries(cfg, [candidate]);
    assert.equal(r.ok, false);
    assert.equal(candidate.reportedToPlatform, undefined);
    assert.equal(state.posts.length, 1);
    assert.equal(state.tasks.length, 0);
  }, { failIngest: true });
});

test('真实 sync-platform 入口：混合批次仅落盘成功关联，失败项可重试，既有人工状态不回退', async () => {
  const existing = { id: 440, title: '人工改名', source_group_id: SOURCE_GROUP_ID,
    source_message_id: 'already', status: 'testing', assigned_to_ids: [5] };
  await withPlatform(async (cfg, state) => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-sync-test-'));
    try {
      fs.mkdirSync(path.join(root, 'runs'));
      fs.mkdirSync(path.join(root, 'config'));
      fs.writeFileSync(path.join(root, 'config/platform.json'), JSON.stringify(cfg));
      fs.writeFileSync(path.join(root, 'runs/board.json'), JSON.stringify({ entries: [
        { ...submission }, { ...submission, ticketId: 'FAILED' },
        { ...submission, ticketId: 'EXISTING', sourceMessageId: 'already' },
      ] }));
      await new Promise<void>((resolve, reject) => {
        const child = spawn(process.execPath, [...process.execArgv, path.join(__dirname, 'sync-platform.ts')],
          { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
        let output = '';
        child.stdout.on('data', (data) => output += data);
        child.stderr.on('data', (data) => output += data);
        child.on('error', reject);
        child.on('close', (code) => code === 0 ? resolve() : reject(new Error(output)));
      });
      const saved = JSON.parse(fs.readFileSync(path.join(root, 'runs/board.json'), 'utf8')).entries;
      assert.equal(saved[0].reportedToPlatform, true);
      assert.equal(saved[0].platformTaskId, 100);
      assert.equal(saved[1].reportedToPlatform, undefined);
      assert.equal(saved[1].platformTaskId, undefined);
      assert.equal(saved[2].reportedToPlatform, true);
      assert.equal(saved[2].platformTaskId, 440);
      assert.equal(saved[2].status, 'testing');
      assert.equal(state.tasks.length, 2);
      assert.equal(state.patches.length, 0);
      assert.equal(fs.existsSync(path.join(root, 'runs/.sync.lock')), false);
    } finally { fs.rmSync(root, { recursive: true, force: true }); }
  }, { tasks: [existing], failIds: ['qa-dispatch:FAILED'] });
});
