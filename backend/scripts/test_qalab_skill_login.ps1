$ErrorActionPreference='Stop'
$qalabTestRequests=[System.Collections.Generic.List[string]]::new()
$scriptPath=Join-Path $PSScriptRoot '../../tools/skills/qalab-requirement-test/scripts/qalab.ps1'
function Read-Host { param($Prompt,[switch]$AsSecureString) if($AsSecureString){return (ConvertTo-SecureString 'fake-password' -AsPlainText -Force)};return 'test-user' }
function Invoke-RestMethod {
 param($Uri,$Method,$Headers,$TimeoutSec,$MaximumRedirection,$Body,$ContentType)
 if(!$Uri.StartsWith('https://qalab-skill-test.invalid/')){throw 'Unexpected network origin'}
 if($MaximumRedirection -ne 0){throw 'Redirect protection missing'}
 $path=([Uri]$Uri).AbsolutePath
 $qalabTestRequests.Add($path)
 switch($path){
  '/api/auth/login' {return @{code=0;data=@{access_token='fake-access-token';refresh_token='fake-refresh-token'}}}
  '/api/auth/refresh' {return @{code=0;data=@{access_token='fake-refreshed-token'}}}
  '/api/auth/me' {return @{code=0;data=@{user=@{username='test-user'}}}}
  '/api/projects' {return @{code=0;data=@(@{id=1;name='中文项目'})}}
  '/api/devices' {return @{code=0;data=@(@{id=1;runner_id='test';token='must-not-print'})}}
  '/openapi.json' {return @{paths=@{'/api/verified-imports'=@{post=@{}};'/api/verified-imports/preview'=@{post=@{}};'/api/verified-imports/jobs'=@{post=@{}}}}}
  '/api/verified-imports/jobs' {
    $payload=[Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json
    return @{code=0;data=@{accepted=$true;project_id=1;job_id=7;external_id=$payload.external_id;case_count=1;status='pending'}}
  }
  '/api/verified-imports/jobs/7' {return @{code=0;data=@{job_id=7;status='needs_confirmation'}}}
  default {throw ('Unexpected path '+$path)}
 }
}
function Invoke-WebRequest {
 param($Uri,$Method,$Headers,$TimeoutSec,$MaximumRedirection,$Body,$ContentType,[switch]$UseBasicParsing)
 $value = Invoke-RestMethod -Uri $Uri -Method $Method -Headers $Headers -TimeoutSec $TimeoutSec -MaximumRedirection $MaximumRedirection -Body $Body -ContentType $ContentType
 $bytes=[Text.Encoding]::UTF8.GetBytes(($value | ConvertTo-Json -Depth 20 -Compress))
 return @{RawContentStream=[IO.MemoryStream]::new($bytes)}
}
$base='https://qalab-skill-test.invalid'
try {
 $login = & $scriptPath -Action login -BaseUrl $base | Out-String
 if($login.Contains('fake-access-token') -or $login.Contains('fake-password') -or $login.Contains('must-not-print')){throw 'Secret in output'}
 $status = & $scriptPath -Action status -BaseUrl $base | Out-String | ConvertFrom-Json
 if($status.user -ne 'test-user' -or !$status.verified_import_available -or $status.projects[0].name -ne '中文项目'){throw 'DPAPI session roundtrip failed'}
 $fixture=[IO.Path]::GetTempFileName()
 try {
   @{project_id=1;runner_device_id=1;external_id='stable-test-id';cases=@(@{title='中文主流程';steps='测试步骤';expected='测试预期';verdict='pass';script=@(@{action='assert_visible'});report=@(@{action='assert_visible';ok=$true;check=@{actual=$true;expected=$true}})})} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $fixture -Encoding UTF8
   $qalabTestRequests.Clear()
   $result=& $scriptPath -Action import -BaseUrl $base -PayloadPath $fixture | Out-String
   if(!$result.Contains('job_id=7') -or !$result.Contains('无需等待')){throw 'Async acceptance receipt missing'}
   if($qalabTestRequests[$qalabTestRequests.Count-1] -ne '/api/verified-imports/jobs'){throw 'Import waited for completion'}
   if(@($qalabTestRequests | Where-Object {$_ -match 'preview|testcases|exec-queue'}).Count){throw 'Unexpected blocking check'}
   $state=& $scriptPath -Action job -JobId 7 -BaseUrl $base | Out-String | ConvertFrom-Json
   if($state.status -ne 'needs_confirmation'){throw 'Explicit job query failed'}
 } finally {Remove-Item -LiteralPath $fixture}
 $rejected=$false
 try {& $scriptPath -Action status -BaseUrl 'http://qalab-skill-test.invalid'} catch {$rejected=$true}
 if(!$rejected){throw 'Plain HTTP accepted'}
 'PASS: masked login, DPAPI session roundtrip, refresh, discovery, async acceptance without polling, explicit job query, no secret output, HTTPS origin restriction'
} finally { & $scriptPath -Action logout -BaseUrl $base }
