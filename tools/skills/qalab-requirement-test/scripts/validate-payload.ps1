# All platforms use one contract; do not maintain a weaker PowerShell copy.
function Test-ImportPayload($payload, [switch]$Draft) {
  if (!(Get-Command node -ErrorAction SilentlyContinue)) { throw '离线协议校验需要 Node.js；无需安装 Runner 或配置设备 token' }
  $payloadFile=[System.IO.Path]::GetTempFileName()
  try {
    $options=@(); if($Draft){$options+= '--draft'}
    [System.IO.File]::WriteAllText($payloadFile, ($payload | ConvertTo-Json -Depth 100 -Compress), (New-Object System.Text.UTF8Encoding($false)))
    $result=& node (Join-Path $PSScriptRoot 'validate-contract.mjs') --payload $payloadFile @options 2>&1
    if ($LASTEXITCODE -ne 0) { throw ([string]::Join("`n", @($result))) }
  } finally { Remove-Item -LiteralPath $payloadFile -Force -ErrorAction SilentlyContinue }
}
