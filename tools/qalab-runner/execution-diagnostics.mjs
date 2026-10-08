// Passive, bounded diagnostics: never select a different target or change a verdict.
export function safeUrl(value) {
  try { const u = new URL(value); u.username = ''; u.password = ''; u.search = ''; u.hash = ''; return u.href; }
  catch { return value ? '[invalid URL]' : ''; }
}

export async function probeCdp(port, request = fetch) {
  const endpoint = `http://127.0.0.1:${port}/json/version`;
  const start = Date.now();
  try {
    const response = await request(endpoint, { signal: AbortSignal.timeout(3000) });
    if (!response.ok) return { ok: false, endpoint, status: response.status, elapsed_ms: Date.now() - start };
    const data = await response.json();
    return { ok: !!data.webSocketDebuggerUrl, endpoint, browser: data.Browser,
      error: data.webSocketDebuggerUrl ? undefined : 'CDP response has no websocket endpoint', elapsed_ms: Date.now() - start };
  } catch (e) {
    return { ok: false, endpoint, error: e.cause?.code || e.code || e.name, elapsed_ms: Date.now() - start };
  }
}

export function windowsClientStartScript(executable, port) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid CDP port');
  const quote = value => "'" + String(value).replaceAll("'", "''") + "'";
  return `$ErrorActionPreference='Stop'\n` +
    `$clientPath=(Get-Item -LiteralPath ${quote(executable)}).FullName\n` +
    `$clientName=[IO.Path]::GetFileName($clientPath)\n` +
    // Exact path prevents terminating another installation or similarly named app.
    `$existing=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq $clientName -and $_.ExecutablePath -eq $clientPath })\n` +
    `foreach($p in $existing){ Write-Output ('stopping client pid='+$p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }\n` +
    `Start-Sleep -Seconds 2\n` +
    `$child=Start-Process -FilePath $clientPath -ArgumentList '--remote-debugging-port=${port}' -WindowStyle Hidden -PassThru\n` +
    `Write-Output ('started client pid='+$child.Id+' port=${port}')`;
}

export async function boundedDiagnostic(read, timeout = 2000) {
  let timer;
  try {
    return await Promise.race([Promise.resolve().then(read), new Promise(resolve => {
      timer = setTimeout(() => resolve({ unavailable: 'diagnostic timeout' }), timeout);
    })]);
  } catch { return { unavailable: 'diagnostic read failed' }; }
  finally { clearTimeout(timer); }
}

export async function pageDiagnostics(page, { cdpUrl, vmIframe, target = {} } = {}) {
  const result = { cdp_url: safeUrl(cdpUrl), requested_frame: target.frame || 'auto', vm_iframe: vmIframe || '', diagnostic_only: true };
  if (!page || page.isClosed()) return { ...result, connected: false };
  result.page_url = safeUrl(page.url());
  // Raw selector counts deliberately ignore nth and visibility. They are clues,
  // not a substitute for the runtime's scoped/unique/visible target resolution.
  result.frames = await Promise.all(page.frames().slice(0, 12).map(async frame => ({
    scope: frame === page.mainFrame() ? 'shell' : 'child', url: safeUrl(frame.url()),
    ...await boundedDiagnostic(async () => ({
      ...await frame.evaluate(() => ({ ready_state: document.readyState, testid_count: document.querySelectorAll('[data-testid]').length })),
      ...(typeof target.selector === 'string' ? { raw_selector_count: await frame.locator(target.selector).count() } : {}),
    })),
  })));
  return result;
}
