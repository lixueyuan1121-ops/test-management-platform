# qa-hosts-helper.ps1 —— Windows 上的提权助手。
# 由计划任务「qa-hosts-helper」以最高权限、按需运行。
# 任务本身不常驻：watcher 写 job.json → schtasks /run → 本脚本处理 → 写 job.result.json。
#
# 设计要点：
#   * 计划任务以「已登录的管理员用户」运行，不是 SYSTEM，避免落到 session 0。
#   * 只有本脚本提权；watcher 和 codex 都以普通权限运行，codex 改不了 hosts。
#   * 结果通过文件回传，watcher 用 token 匹配，防止读到上一个任务的结果。

$ErrorActionPreference = 'Stop'

$Runtime = if ($env:QA_RUNTIME) { $env:QA_RUNTIME } else { Join-Path $env:USERPROFILE 'qa-workspace\runtime' }

# 运行日志：任务被拉起但没写结果时，靠它区分「没跑」「跑错目录」「脚本报错」。
# 由计划任务运行，所以用绝对路径，不依赖调用方的工作目录。
$LogFile = Join-Path $Runtime 'qa-hosts-helper.log'
function Write-Log([string]$Message) {
  try {
    $line = "{0} pid={1} user={2} runtime={3} {4}" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffZ'), $PID, $env:USERNAME, $Runtime, $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
  } catch { }
}

# job/结果文件默认在 runtime 根（runner 写这里）；兼容早期放在 runtime\hosts 的形态
$JobPath = Join-Path $Runtime 'job.json'
if (-not (Test-Path $JobPath)) {
  $legacy = Join-Path (Join-Path $Runtime 'hosts') 'job.json'
  if (Test-Path $legacy) { $JobPath = $legacy }
}
$ResPath = Join-Path (Split-Path -Parent $JobPath) 'job.result.json'
$Hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'

function Write-Result($token, $ok, $out, $err) {
  $payload = @{ token = $token; ok = $ok; out = $out; err = $err } | ConvertTo-Json -Depth 5
  Set-Content -Path $ResPath -Value $payload -Encoding UTF8
}

Write-Log "start runtime=$Runtime job=$JobPath"
if (-not (Test-Path $JobPath)) { Write-Log "no job file, exit"; exit 1 }

$job = Get-Content -Raw -Path $JobPath | ConvertFrom-Json
$token = $job.token
$action = $job.action
$arg = $job.arg

try {
  if ($arg -and ([IO.Path]::IsPathRooted($arg) -eq $false)) { throw "路径必须是绝对路径: $arg" }

  switch ($action) {
    'snapshot' {
      Copy-Item -LiteralPath $Hosts -Destination $arg -Force
      $acl = Get-Acl $arg
      Write-Log "snapshot ok -> $arg"
      Write-Result $token $true "snapshot -> $arg" ""
    }
    'apply' {
      if (-not (Test-Path -LiteralPath $arg)) { throw "分组文件不存在: $arg" }
      # 只校验生效行（非注释、非空）必须是 ASCII；注释允许中文。
      $effective = Get-Content -LiteralPath $arg -Encoding UTF8 |
        Where-Object { $_ -notmatch '^\s*#' -and $_ -notmatch '^\s*$' }
      foreach ($line in $effective) {
        if ($line -match '[^\x00-\x7F]') { throw "分组文件的有效行含非 ASCII 字符（注释不受限），拒绝写入: $line" }
      }
      $bak = "$Hosts.qa-bak.$(Get-Date -Format 'yyyyMMddHHmmss')"
      Copy-Item -LiteralPath $Hosts -Destination $bak -Force -ErrorAction SilentlyContinue
      $tmp = "$Hosts.qa-tmp"
      Copy-Item -LiteralPath $arg -Destination $tmp -Force
      Move-Item -LiteralPath $tmp -Destination $Hosts -Force
      Write-Result $token $true "applied <- $arg" ""
    }
    'restore' {
      $tmp = "$Hosts.qa-tmp"
      Copy-Item -LiteralPath $arg -Destination $tmp -Force
      Move-Item -LiteralPath $tmp -Destination $Hosts -Force
      Write-Result $token $true "restored <- $arg" ""
    }
    default { throw "未知 action: $action" }
  }
}
catch {
  Write-Log "error: $($_.Exception.Message)"
  Write-Result $token $false "" $_.Exception.Message
  exit 1
}
