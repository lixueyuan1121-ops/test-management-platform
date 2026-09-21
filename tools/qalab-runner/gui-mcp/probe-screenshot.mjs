// Electron may stall its first screenshot request while DOM reads still succeed.
// Retry the same full-page capture once, with an explicit budget below probe polling's 60s.
export async function captureProbeScreenshot(page, { timeoutMs = 8000 } = {}) {
  let error;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const buffer = await page.screenshot({ fullPage: true, type: 'png', timeout: timeoutMs });
      if (!buffer?.length) throw new Error('截图返回空数据');
      return { screenshotBuffer: buffer, screenshotError: null };
    } catch (e) { error = e; }
  }
  return { screenshotBuffer: null, screenshotError: String(error?.message || error).slice(0, 500) };
}
