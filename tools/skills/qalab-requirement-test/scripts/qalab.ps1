param(
  [ValidateSet('login','status','import','logout')][string]$Action = 'status',
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
  $http = Invoke-WebRequest @params -UseBasicParsing
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
if ($Action -eq 'status') {
  @{platform=$BaseUrl;user=$me.user.username;projects=$projects;devices=@($devices | Select-Object id,runner_id,name,platform,active_kinds,last_seen_at);verified_import_available=$available} | ConvertTo-Json -Depth 8
  exit
}
if (!$available) { throw '线上尚未发布 POST /api/verified-imports。待导入文件保留，不会写到 localhost。' }
if (!$PayloadPath) { throw '缺少 -PayloadPath' }
$payload = Get-Content -LiteralPath $PayloadPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (@($payload.cases.title | Select-Object -Unique).Count -ne @($payload.cases).Count) { throw "同批用例标题须唯一" }
$receipt = Call-Api 'POST' '/api/verified-imports' $payload $session.access_token
foreach ($record in $receipt.records) {
  $saved = Call-Api 'GET' ('/api/ai/testcases/' + $record.case_id) $null $session.access_token
  $source = @($payload.cases | Where-Object { $_.title -eq $record.title })
  if ($source.Count -ne 1 -or $saved.title -ne $source[0].title -or $saved.project_id -ne $payload.project_id -or $saved.steps -ne $source[0].steps -or $saved.expected -ne $source[0].expected -or @($saved.script).Count -ne @($source[0].script).Count) {
    throw '已提交，但线上读回复核不一致；保留相同 external_id，先检查现有记录，勿盲目重复创建'
  }
}
$receipt | ConvertTo-Json -Depth 8
Write-Output ($BaseUrl + '/case-library?project_id=' + $receipt.project_id)
Write-Output ($BaseUrl + '/exec-results?project_id=' + $receipt.project_id + '&batch_id=' + $receipt.batch_id)
