// Own a CDP emulation session, so viewport setup is replayable and scoped to a case.
export function createViewportSession() {
  let active = null;
  async function restore() {
    if (!active) return null;
    const { page, session, original } = active;
    await session.send('Emulation.clearDeviceMetricsOverride');
    if (original) await page.setViewportSize(original);
    await session.detach();
    active = null;
    return { restored: true, actual: await page.evaluate(() => ({ width: innerWidth, height: innerHeight })) };
  }
  async function set(page, viewport) {
    if (!viewport || !Number.isInteger(viewport.width) || !Number.isInteger(viewport.height)
        || viewport.width < 320 || viewport.height < 240 || viewport.width > 7680 || viewport.height > 4320)
      throw new Error('connect.args.viewport 需要整数 width(320–7680)、height(240–4320)');
    if (!active) active = { page, session: await page.context().newCDPSession(page), original: page.viewportSize() };
    if (active.page !== page) throw new Error('视口所属页面已改变，不能继续执行');
    await active.session.send('Emulation.setDeviceMetricsOverride', { width: viewport.width, height: viewport.height, deviceScaleFactor: 0, mobile: false });
    const actual = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
    if (actual.width !== viewport.width || actual.height !== viewport.height)
      throw new Error(`小屏准备失败：期望 ${viewport.width}×${viewport.height}，实际 ${actual.width}×${actual.height}`);
    return { requested: viewport, actual };
  }
  return { set, restore };
}
