const interval = (value, fallback) => Number.isFinite(Number(value)) && Number(value) >= 200 ? Number(value) : fallback;

// Queue pickup is latency-sensitive; probes/perf keep their existing cadence.
// Explicit EXEC_POLL_MS can restore a slower interval on constrained servers.
export function createPollSchedule({ pollMs = 5000, execPollMs, now = Date.now } = {}) {
  const maintenanceMs = interval(pollMs, 5000);
  const executionMs = interval(execPollMs, Math.min(1000, maintenanceMs));
  let nextMaintenance = 0;
  return {
    executionMs, maintenanceMs,
    maintenanceDue() {
      const current = now();
      if (current < nextMaintenance) return false;
      nextMaintenance = current + maintenanceMs;
      return true;
    },
    delay(completed) { return completed > 0 ? 0 : executionMs; },
  };
}
