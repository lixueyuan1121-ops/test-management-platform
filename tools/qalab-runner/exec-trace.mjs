import { mkdir } from "node:fs/promises";
import { join } from "node:path";

// Capture the reset gate and the test together. Recording failures are explicit
// diagnostics and do not replace an otherwise valid business verdict.
export async function runWithTrace(gui, execute, { runId, directory, mode = "failures" } = {}) {
  let recording = false, traceError = null, result;
  if (mode !== "off") {
    try { await gui.startTrace(); recording = true; }
    catch (e) { traceError = e.message; }
  }
  try { result = await execute(); }
  catch (e) { result = { verdict: "fail", fail_kind: e.fail_kind || "selector", reason: `执行异常：${e.message}`, report: [] }; }
  if (recording) {
    let path;
    try {
      if (result.verdict !== "pass" || mode === "all") {
        await mkdir(directory, { recursive: true });
        path = join(directory, `trace-${String(runId).replace(/[^a-zA-Z0-9_-]/g, "_")}.zip`);
      }
      await gui.stopTrace(path);
      if (path) result.tracePath = path;
    } catch (e) {
      traceError = e.message;
      // mkdir/stop may have failed before closing the recorder. Always release it.
      try { await gui.stopTrace(); } catch {}
    }
  }
  if (traceError) {
    result.report ||= [];
    if (!result.report.length) result.report.push({ action: "diagnostics", desc: "执行追踪", ok: result.verdict === "pass" });
    result.report.at(-1).trace_error = traceError;
  }
  return result;
}
