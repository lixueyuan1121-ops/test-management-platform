import { mkdirSync, existsSync, readFileSync, writeFileSync, renameSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import { dedupeSteps, rawEventToStep } from './record-capture.mjs';

// 网络失败或回包丢失时保留待确认事件；进程重启后可重新发送相同 event_id。
export function createRecordingPump({ gui, upload, directory, consumerId }) {
  let activeId = null;
  const fileFor = id => {
    if (!Number.isSafeInteger(id) || id < 1) throw new Error('录制会话 ID 非法');
    return join(directory, `${id}.json`);
  };
  const save = (file, events) => {
    mkdirSync(directory, { recursive: true });
    writeFileSync(`${file}.tmp`, JSON.stringify(events), { mode: 0o600 });
    renameSync(`${file}.tmp`, file);
  };
  // 导航前已交给 binding 的事件立即落盘，避免进程在下一次轮询前退出而丢失。
  gui.setRecordEventSink?.((id, raw) => {
    const file = fileFor(id);
    const pending = existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : [];
    const step = rawEventToStep(raw.ev, raw.frame);
    if (step) save(file, dedupeSteps([...pending, step]));
  });
  return {
    get activeId() { return activeId; },
    async advance(session) {
      const file = fileFor(session.id);
      if (activeId !== session.id) {
        if (activeId !== null) throw new Error('不能同时录制两个会话');
        await gui.startRecording(session.id);
        activeId = session.id;
      }
      const final = session.status === 'stopping';
      if (final) await gui.stopRecording();
      const drained = await gui.drainRecordEvents();
      const pending = existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : [];
      const events = dedupeSteps([...pending, ...drained.map(({ ev, frame }) => rawEventToStep(ev, frame)).filter(Boolean)]);
      save(file, events); // 必须先落盘，再上传；页面缓冲此时仍未清理。
      if (events.length || final) {
        const result = await upload(session.id, { events, final, consumer_id: consumerId });
        const acked = new Set(result.acked || []);
        const remaining = events.filter(e => !acked.has(e.event_id));
        // 若页面确认调用失败，下轮继续以同 ID 上传，后端返回相同确认。
        await gui.ackRecordEvents([...acked]);
        const latest = existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : [];
        const unconfirmed = dedupeSteps([...latest.filter(e => !acked.has(e.event_id)), ...remaining]);
        save(file, unconfirmed);
        if (final && result.status === 'stopped' && !unconfirmed.length) {
          unlinkSync(file);
          activeId = null;
          return false;
        }
      }
      return true;
    },
    async release() {
      if (activeId !== null) await gui.stopRecording();
      activeId = null;
      // 异常结束不删除本地未确认事件，保留供恢复/排查。
    },
  };
}
