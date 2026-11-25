# Registers (or with -Uninstall removes) the "SignCheckAgent" scheduled task.
# The task runs service.cmd as SYSTEM at boot: no window, no logged-in user needed,
# no run-time limit, restarted automatically. Output goes to logs\agent.log.
# ASCII only on purpose: Windows PowerShell 5.1 misreads UTF-8 scripts without a BOM.
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$TaskName = 'SignCheckAgent'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AgentPath = Join-Path $Dir 'agent.mjs'

function Say($msg) { Write-Host "[signcheck] $msg" }
function Fail($msg) { Write-Host "[signcheck] ERROR: $msg" -ForegroundColor Red; exit 1 }

function Stop-Agent {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    # Stopping the task may leave node.exe behind; end only the node process running THIS folder's agent.mjs.
    Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($AgentPath.ToLower()) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    return [bool]$existing
}

if ($Uninstall) {
    if (Stop-Agent) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Say "task '$TaskName' removed."
    } else {
        Say "task '$TaskName' is not installed."
    }
    exit 0
}

# ---- preflight ----
if (-not (Test-Path (Join-Path $Dir '.env'))) {
    Fail ".env not found. Copy .env.example to .env, fill BASE_URL and SIGNCHECK_AGENT_TOKEN, then run install.cmd again."
}
$missing = @('360Base_test.exe', '360Base.dll', 'pbusc_d.dll', 'sesafe.dll', 'sesafe64.dll') |
    Where-Object { -not (Test-Path (Join-Path $Dir "verifier\$_")) }
if ($missing) { Fail ("verifier folder is incomplete, missing: " + ($missing -join ', ')) }

$nodeCmd = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $nodeCmd) { Fail "Node.js not found. Install Node.js 18+ (https://nodejs.org) for all users, then run install.cmd again." }
$Node = $nodeCmd.Source
$major = [int](& $Node -p "process.versions.node.split('.')[0]")
if ($major -lt 18) { Fail "Node.js $major is too old; 18 or newer is required." }
Say "node: $Node (v$major)"

# ---- register ----
Stop-Agent | Out-Null
$argument = '/c ""{0}" "{1}""' -f (Join-Path $Dir 'service.cmd'), $Node
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $argument -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
# ExecutionTimeLimit 0 = never stop it (the default would kill the task after 72 hours).
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'Test platform sign-check agent (polls the platform, runs 360Base_test.exe)' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Say "task '$TaskName' installed and started. It will start automatically with Windows."

# ---- show what the agent says ----
Start-Sleep -Seconds 6
$log = Join-Path $Dir 'logs\agent.log'
Say ("task state: " + (Get-ScheduledTask -TaskName $TaskName).State)
if (Test-Path $log) {
    Say "last lines of logs\agent.log:"
    Get-Content $log -Tail 8 -Encoding UTF8 | ForEach-Object { Write-Host "    $_" }
} else {
    Say "logs\agent.log has not been created yet; check it in a few seconds."
}
