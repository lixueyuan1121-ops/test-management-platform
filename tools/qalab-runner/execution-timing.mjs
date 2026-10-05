import { performance } from 'node:perf_hooks';

// Monotonic durations on the device; queue/wall time is measured by the server.
export function createExecutionTimer(now = () => performance.now(), log = () => {}) {
  const started = now(), stages = {};
  return {
    async measure(name, work) {
      const at = now();
      log(`阶段开始：${name}`);
      try { return await work(); }
      finally {
        stages[name] = (stages[name] || 0) + Math.max(0, Math.round(now() - at));
        log(`阶段完成：${name} ${stages[name]}ms`);
      }
    },
    snapshot() { return { ...stages, runner_total_ms: Math.max(0, Math.round(now() - started)) }; },
  };
}
