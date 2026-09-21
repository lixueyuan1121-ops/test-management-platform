import { fork, execFile } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

// Only the claimed case lives in this process tree. Never kill the user's app.
async function killTree(child) {
  if (!child.pid) return;
  if (process.platform === "win32") {
    await new Promise((resolve, reject) => {
      execFile("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true }, (error) => {
        if (error && child.exitCode === null && child.signalCode === null) reject(error);
        else resolve();
      });
    });
  } else {
    try { process.kill(-child.pid, "SIGKILL"); }
    catch (error) { if (error.code !== "ESRCH") throw error; }
  }
}

export async function runInWorker({ file, item, heartbeat, log = () => {}, args = [],
  pollMs = 5000, timeoutMs = 900000, heartbeatFailures = 3 }) {
  // A queued cancellation may win the race with claim; do not start any GUI work.
  const initial = await heartbeat();
  const stopped = (reason) => ({ verdict: "fail", fail_kind: "selector", reason, report: [] });
  if (!initial?.alive || initial.cancel_requested) return stopped("执行已终止，未开始操作");
  const child = fork(file, ["--exec-worker", ...args], {
    stdio: ["ignore", "inherit", "inherit", "ipc"], windowsHide: true,
    detached: process.platform !== "win32",
  });
  let result, error, closed = false, failures = 0;
  const started = Date.now();
  const ended = new Promise(resolve => {
    child.once("error", e => { error = e; });
    child.once("close", (code, signal) => {
      closed = true;
      if (!result && !error) error = new Error(`执行子进程退出(code=${code}, signal=${signal})`);
      resolve();
    });
  });
  child.on("message", msg => {
    if (msg?.type === "result") result = msg.result;
  });
  child.send(item, e => { if (e) error = e; });
  async function terminate(reason) {
    log(reason);
    // Release the queue lock only AFTER the entire execution tree has stopped.
    for (;;) {
      try {
        await killTree(child);
        const controller = new AbortController();
        try { await Promise.race([ended, sleep(10000, undefined, { signal: controller.signal }).then(() => { throw new Error("等待执行子进程退出超时"); })]); }
        finally { controller.abort(); }
        break;
      } catch (e) {
        // A failed OS kill must not release the device or start another case.
        log(`终止失败，保留设备占用并重试：${e.message}`);
        await heartbeat().catch(() => {});
        await sleep(pollMs);
      }
    }
    return { ...stopped(reason), duration_ms: Date.now() - started };
  }
  while (!closed) {
    const controller = new AbortController();
    await Promise.race([ended, sleep(pollMs, undefined, { signal: controller.signal }).catch(() => {})]);
    controller.abort();
    if (closed) break;
    if (Date.now() - started > timeoutMs) return terminate("用例执行超时，已停止执行子进程");
    try {
      const state = await heartbeat();
      failures = 0;
      if (!state?.alive || state.cancel_requested) return terminate("手动终止：已停止执行子进程及其工具");
    } catch (e) {
      log(`心跳失败 ${++failures}/${heartbeatFailures}: ${e.message}`);
      if (failures >= heartbeatFailures) return terminate("执行机与平台失联，已停止当前用例");
    }
  }
  if (error) throw error;
  return result;
}
