$ErrorActionPreference='Stop'
$scriptPath=Join-Path $PSScriptRoot '../../tools/skills/qalab-requirement-test/scripts/qalab.ps1'
function Read-Host { param($Prompt,[switch]$AsSecureString) if($AsSecureString){return (ConvertTo-SecureString 'fake-password' -AsPlainText -Force)};return 'test-user' }
function Invoke-RestMethod {
 param($Uri,$Method,$Headers,$TimeoutSec,$MaximumRedirection,$Body,$ContentType)
 if(!$Uri.StartsWith('https://qalab-skill-test.invalid/')){throw 'Unexpected network origin'}
 if($MaximumRedirection -ne 0){throw 'Redirect protection missing'}
 $path=([Uri]$Uri).AbsolutePath
 switch($path){
  '/api/auth/login' {return @{code=0;data=@{access_token='fake-access-token';refresh_token='fake-refresh-token'}}}
  '/api/auth/refresh' {return @{code=0;data=@{access_token='fake-refreshed-token'}}}
  '/api/auth/me' {return @{code=0;data=@{user=@{username='test-user'}}}}
  '/api/projects' {return @{code=0;data=@(@{id=1;name='中文项目'})}}
  '/api/devices' {return @{code=0;data=@(@{id=1;runner_id='test';token='must-not-print'})}}
  '/openapi.json' {return @{paths=@{'/api/verified-imports'=@{post=@{}};'/api/verified-imports/preview'=@{post=@{}}}}}
  '/api/verified-imports' {return @{code=0;data=@{project_id=1;batch_id='fake-batch';reused=$true;records=@(@{case_id=123;run_id=456;title='中文主流程';disposition=$(if($script:v2){'reused'}else{$null})})}}}
  '/api/verified-imports/preview' {return @{code=0;data=@{ready=$false;cases=@(@{match='ambiguous';case_id=123})}}}
  '/api/exec-queue/456' {return @{code=0;data=@{project_id=1;test_case_id=123;verdict='pass';payload=@{title='中文主流程';steps='测试步骤';expected='测试预期';script=@(@{action='connect'},@{action='assert_visible'})};report=$null}}}
  '/api/ai/testcases/123' {
    $data=@{id=123;project_id=1;title='中文主流程';steps='测试步骤';expected='测试预期';script=@(@{action='connect'},@{action='assert_visible'})}
    if($script:scriptAsString){$data.script=ConvertTo-Json -InputObject $data.script -Compress}
    if($script:v2){$data.title='原用例标题不同'}
    return @{code=0;data=$data}
  }
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
   @{project_id=1;cases=@(@{title='中文主流程';steps='测试步骤';expected='测试预期';script=@(@{action='connect'},@{action='assert_visible'})})} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $fixture -Encoding UTF8
   foreach($representation in @($true,$false)){
     $script:v2=$representation
     $script:scriptAsString=$representation
     $result=& $scriptPath -Action import -BaseUrl $base -PayloadPath $fixture | Out-String
     if(!$result.Contains('fake-batch')){throw 'Import readback did not complete'}
   }
   $preview=& $scriptPath -Action preview -BaseUrl $base -PayloadPath $fixture | Out-String | ConvertFrom-Json
   if($preview.ready -or $preview.cases[0].match -ne 'ambiguous'){throw 'Preview missing candidates'}
 } finally {Remove-Item -LiteralPath $fixture}
 $rejected=$false
 try {& $scriptPath -Action status -BaseUrl 'http://qalab-skill-test.invalid'} catch {$rejected=$true}
 if(!$rejected){throw 'Plain HTTP accepted'}
 'PASS: masked login, DPAPI session roundtrip, refresh, discovery, JSON-string/array script readback, no secret output, HTTPS origin restriction'
} finally { & $scriptPath -Action logout -BaseUrl $base }
