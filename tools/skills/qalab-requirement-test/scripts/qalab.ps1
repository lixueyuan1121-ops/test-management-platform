param(
  [ValidateSet('login','status','import','logout','preview')][string]$Action = 'status',
  [string]$BaseUrl = 'https://qalab.claw.qihoo.net',
  [string]$PayloadPath
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
$dedupAvailable = $null -ne $schema.paths.'/api/verified-imports/preview'.post
if ($Action -eq 'status') {
  @{platform=$BaseUrl;user=$me.user.username;projects=$projects;devices=@($devices | Select-Object id,runner_id,name,platform,active_kinds,last_seen_at);verified_import_available=$available;dedup_available=$dedupAvailable} | ConvertTo-Json -Depth 8
  exit
}
if (!$available) { throw '线上尚未发布 POST /api/verified-imports。待导入文件保留，不会写到 localhost。' }
if (!$dedupAvailable) { throw '线上尚未发布平台查重接口；请先更新服务器，保留报告不盲目新增' }
if (!$PayloadPath) { throw '缺少 -PayloadPath' }
$payload = Get-Content -LiteralPath $PayloadPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (@($payload.cases.title | Select-Object -Unique).Count -ne @($payload.cases).Count) { throw "同批用例标题须唯一" }
if ($Action -eq 'preview') {
  Call-Api 'POST' '/api/verified-imports/preview' $payload $session.access_token | ConvertTo-Json -Depth 100
  exit
}
function Canonical($Value) {
  if ($null -eq $Value) { return 'null' }
  if ($Value -is [string] -or $Value.GetType().IsPrimitive) { return (ConvertTo-Json -InputObject $Value -Compress) }
  if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [System.Collections.IDictionary]) {
    $parts = @(); foreach($item in $Value) { $parts += Canonical $item }; return ('[' + ($parts -join ',') + ']')
  }
  $parts = @()
  foreach($name in @($Value.PSObject.Properties.Name | Sort-Object)) {
    $parts += ((ConvertTo-Json -InputObject $name -Compress) + ':' + (Canonical $Value.$name))
  }
  return ('{' + ($parts -join ',') + '}')
}
function Script-Canonical($Script) {
  if ($Script -is [string]) { $Script = ConvertFrom-Json -InputObject $Script }
  foreach($step in @($Script)) {
    if (!$step.target) { $step | Add-Member -NotePropertyName target -NotePropertyValue ([pscustomobject]@{}) -Force }
    if (!$step.args) { $step | Add-Member -NotePropertyName args -NotePropertyValue ([pscustomobject]@{}) -Force }
  }
  return (Canonical @($Script))
}
$receipt = Call-Api 'POST' '/api/verified-imports' $payload $session.access_token
foreach ($record in $receipt.records) {
  $saved = Call-Api 'GET' ('/api/ai/testcases/' + $record.case_id) $null $session.access_token
  $source = @($payload.cases | Where-Object { $_.title -eq $record.title })
  if ($source.Count -ne 1 -or $saved.project_id -ne $payload.project_id) { throw '导入后用例归属不一致' }
  $actual = $saved
  if ($record.disposition -in @('created','reused')) {
    $run = Call-Api 'GET' ('/api/exec-queue/' + $record.run_id) $null $session.access_token
    if ($run.project_id -ne $payload.project_id -or $run.test_case_id -ne $record.case_id -or $run.verdict -ne 'pass' -or (Canonical $run.report) -cne (Canonical $source[0].report)) {
      throw '已提交，但执行报告读回复核不一致；保留原 external_id，请检查线上记录'
    }
    $actual = $run.payload
    if ($actual -is [string]) { $actual = ConvertFrom-Json -InputObject $actual }
  }
  if ($actual.title -cne $source[0].title -or $actual.steps -cne $source[0].steps -or $actual.expected -cne $source[0].expected -or (Script-Canonical $actual.script) -cne (Script-Canonical $source[0].script)) {
    throw '已提交，但线上读回复核不一致；保留相同 external_id，先检查现有记录，勿盲目重复创建'
  }
}
$receipt | ConvertTo-Json -Depth 8
Write-Output ($BaseUrl + '/case-library?project_id=' + $receipt.project_id)
Write-Output ($BaseUrl + '/exec-results?project_id=' + $receipt.project_id + '&batch_id=' + $receipt.batch_id)
