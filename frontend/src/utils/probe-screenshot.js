// Screenshot upload may finish after the element result has been marked done.
// Keep this lifecycle separate so polling never replaces a saved/selected snapshot.
export function createProbeScreenshotLoader({ fetchProbe, onUrl, onError, now = Date.now,
  setTimer = setTimeout, clearTimer = clearTimeout, intervalMs = 1500, timeoutMs = 30000 }) {
  let version = 0, timer = null
  function cancel() {
    ++version
    if (timer !== null) clearTimer(timer)
    timer = null
  }
  function load(id) {
    cancel()
    const token = version, deadline = now() + timeoutMs
    async function poll() {
      timer = null
      if (token !== version) return
      if (now() >= deadline) { onError('设备未返回截图，请重新探测'); return }
      let result
      try { result = await fetchProbe(id) } catch { /* bounded retry on transient failure */ }
      if (token !== version) return
      if (result?.screenshot_url) { onUrl(result.screenshot_url); return }
      if (result?.result?.screenshot_error) { onError('设备截图失败，请重新探测：' + result.result.screenshot_error); return }
      if (now() >= deadline) { onError('设备未返回截图，请重新探测'); return }
      timer = setTimer(poll, intervalMs)
    }
    return poll()
  }
  return { load, cancel }
}
