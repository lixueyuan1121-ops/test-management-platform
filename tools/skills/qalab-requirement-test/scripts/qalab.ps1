param(
  [ValidateSet('login','status','import','logout','preview','job')][string]$Action = 'status',
  [string]$BaseUrl = 'https://qalab.claw.qihoo.net',
  [string]$PayloadPath,
  [int]$JobId
)
$ErrorActionPreference = 'Stop'
$origin = [Uri]$BaseUrl
if ($origin.Scheme -ne 'https' -or $origin.UserInfo -or $origin.AbsolutePath -ne '/' -or $origin.Query -or $origin.Fragment) {
  throw 'BaseUrl 必须是 HTTPS 平台源地址（不带路径、凭据或查询参数）'
}
$BaseUrl = $origin.GetLeftPart([System.UriPartial]::Authority)
$sha = [Security.Cryptography.SHA256]::Create()
$key = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($BaseUrl)))).Replace('-','').Substring(0,24)
$authDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'QALabSkill'
$authFile = Join-Path $authDir ($key + '.credential')
function Call-Api([string]$Method, [string]$Path, $Body, [string]$Token) {
  $headers = @{}
  if ($Token) { $headers.Authorization = 'Bearer ' + $Token }
  $params = @{Uri=($BaseUrl+$Path);Method=$Method;Headers=$headers;TimeoutSec=30;MaximumRedirection=0}
  if ($null -ne $Body) { $params.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 100 -Compress)); $params.ContentType='application/json; charset=utf-8' }
  try { $http = Invoke-WebRequest @params -UseBasicParsing }
  catch {
    $conflict = $null
    try { $conflict = $_.ErrorDetails.Message | ConvertFrom-Json } catch {}
    if ($conflict.data.reason -eq 'duplicate_confirmation_required') {
      throw ('需确认疑似重复用例；未写入本批数据：' + ($conflict.data | ConvertTo-Json -Depth 100))
    }
    throw
  }
  $response = [Text.Encoding]::UTF8.GetString($http.RawContentStream.ToArray()) | ConvertFrom-Json
  if ($null -ne $response.code -and $response.code -ne 0) { throw ('平台拒绝请求: ' + $response.msg) }
  if ($null -ne $response.data) { return $response.data }
  return $response
}
function Save-Session($Session) {
  [void][IO.Directory]::CreateDirectory($authDir)
  $protected = ConvertTo-SecureString ($Session | ConvertTo-Json -Compress) -AsPlainText -Force
  ConvertFrom-SecureString $protected | Set-Content -LiteralPath $authFile -Encoding ASCII
}
if ($Action -eq 'logout') {
  if (Test-Path -LiteralPath $authFile) { Remove-Item -LiteralPath $authFile }
  Write-Output '已清除本机加密登录信息'; exit
}
if ($Action -eq 'login') {
  $username = Read-Host '线上用户名'
  $secret = Read-Host '线上密码（隐藏输入）' -AsSecureString
  $password = [Net.NetworkCredential]::new('', $secret).Password
  try { $session = Call-Api 'POST' '/api/auth/login' @{username=$username;password=$password} '' }
  finally { $password=$null; $secret=$null }
  Save-Session $session
  Write-Output ('已安全登录: ' + $BaseUrl)
  $Action = 'status'
} else {
  if (!(Test-Path -LiteralPath $authFile)) { throw '请先运行 -Action login；不需要把密码发给 AI' }
  $encrypted = Get-Content -LiteralPath $authFile -Raw
  $secure = ConvertTo-SecureString $encrypted.Trim()
  $session = [Net.NetworkCredential]::new('', $secure).Password | ConvertFrom-Json
  if ($session.refresh_token) {
    $fresh = Call-Api 'POST' '/api/auth/refresh' @{refresh_token=$session.refresh_token} ''
    $session.access_token = $fresh.access_token
    Save-Session $session
  }
}
$me = Call-Api 'GET' '/api/auth/me' $null $session.access_token
$projects = Call-Api 'GET' '/api/projects' $null $session.access_token
$devices = Call-Api 'GET' '/api/devices' $null $session.access_token
$schema = Call-Api 'GET' '/openapi.json' $null ''
$available = $null -ne $schema.paths.'/api/verified-imports'.post
$asyncAvailable = $null -ne $schema.paths.'/api/verified-imports/jobs'.post
$dedupAvailable = $null -ne $schema.paths.'/api/verified-imports/preview'.post
if ($Action -eq 'status') {
  @{platform=$BaseUrl;user=$me.user.username;projects=$projects;devices=@($devices | Select-Object id,runner_id,name,platform,active_kinds,last_seen_at);verified_import_available=$available;dedup_available=$dedupAvailable;async_import_available=$asyncAvailable} | ConvertTo-Json -Depth 8
  exit
}
if ($Action -eq 'job') {
  if ($JobId -le 0) { throw 'job 需要正整数 -JobId' }
  Call-Api 'GET' ('/api/verified-imports/jobs/' + $JobId) $null $session.access_token | ConvertTo-Json -Depth 100
  exit
}
if ($Action -eq 'import' -and !$asyncAvailable) { throw '线上尚未发布异步导入接口；保留报告，更新服务器后再提交，不退回同步导入' }
if (!$PayloadPath) { throw '缺少 -PayloadPath' }
$payload = Get-Content -LiteralPath $PayloadPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (@($payload.cases.title | Select-Object -Unique).Count -ne @($payload.cases).Count) { throw "同批用例标题须唯一" }
if ($Action -eq 'preview') {
  Call-Api 'POST' '/api/verified-imports/preview' $payload $session.access_token | ConvertTo-Json -Depth 100
  exit
}
if (@($payload.cases).Count -lt 1 -or @($payload.cases).Count -gt 20) { throw '每批提交 1 至 20 条用例' }
foreach ($case in $payload.cases) {
  if ($case.resolution) { throw '疑似重复由平台导入任务页集中确认，不在当前任务处理' }
  if ($case.verdict -ne 'pass' -or @($case.script).Count -eq 0 -or @($case.script).Count -ne @($case.report).Count) { throw '只提交完整实测通过的用例' }
  if (!@($case.script | Where-Object { $_.action -like 'assert*' -or $_.action -eq 'judge' }).Count) { throw '用例必须包含业务断言' }
  for ($i=0; $i -lt @($case.script).Count; $i++) {
    $step=$case.script[$i]; $result=$case.report[$i]
    if ($step.action -ne $result.action -or $result.ok -ne $true -or $result.check.pass -eq $false) { throw '脚本与实测报告不一致或存在失败' }
    if ($step.action -like 'assert*' -and ($null -eq $result.check -or 'actual' -notin @($result.check.PSObject.Properties.Name) -or 'expected' -notin @($result.check.PSObject.Properties.Name))) { throw '断言缺少实际值或预期值' }
  }
}
$receipt = Call-Api 'POST' '/api/verified-imports/jobs' $payload $session.access_token
if ($receipt.accepted -ne $true -or $receipt.job_id -le 0 -or $receipt.project_id -ne $payload.project_id -or $receipt.external_id -cne $payload.external_id -or $receipt.case_count -ne @($payload.cases).Count) {
  throw '提交回执无法确认；保留原 external_id 和内容重试，勿生成新 ID；不宣称已经入库'
}
$receipt | ConvertTo-Json -Depth 12
Write-Output '测试结果已提交，平台正在后台整理；无需等待。此回执不代表用例已入库。'
Write-Output ($BaseUrl + '/verified-imports?project_id=' + $receipt.project_id + '&job_id=' + $receipt.job_id)
