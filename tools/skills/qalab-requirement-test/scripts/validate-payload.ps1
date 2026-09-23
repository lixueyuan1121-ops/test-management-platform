function Test-ScriptTargets($steps, [string]$title) {
  if ($steps -isnot [array] -or $steps.Count -eq 0) { throw ($title + ': script 必须是非空数组') }
  $targetActions=@('click','hover','fill','type','set_checked','select_option','wait_for','get_text','assert_text','assert_visible','assert_absent')
  for ($n=0; $n -lt $steps.Count; $n++) {
    $step=$steps[$n]; $label=('{0}: 第 {1} 步' -f $title,($n+1))
    if ($step -isnot [pscustomobject]) { throw ($label + ' 必须是对象') }
    if ($step.action -isnot [string] -or !$step.action.Trim()) { throw ($label + ' 缺 action') }
    $action=$step.action.Trim(); $target=$step.target
    if ($action -notin ($targetActions + @('connect','press','wait_response','screenshot','mock_route','unmock_route'))) { throw ($label + ' 非平台 GUI/E2E action「' + $action + '」；等待元素使用 wait_for + target') }
    if ($null -ne $target -and $target -isnot [pscustomobject]) { throw ($label + ' target 必须是对象，定位写入 target.selector 或 target.key') }
    if ($null -ne $step.args -and $step.args -isnot [pscustomobject]) { throw ($label + ' args 必须是对象') }
    if ($action -notin $targetActions -and !($action -eq 'press' -and $null -ne $target -and @($target.PSObject.Properties).Count -gt 0)) { continue }
    $current=$target; $depth=0
    while ($true) {
      if ($current -isnot [pscustomobject] -or !(($current.key -is [string] -and $current.key.Trim()) -or ($current.selector -is [string] -and $current.selector.Trim()))) {
        throw ($label + '「' + $action + '」缺 target.key/selector（或 within 定位）；须填写真实 DOM 定位，不能仅写描述或顶层 selector；修复后重跑完整脚本再导入')
      }
      foreach ($name in @('key','selector')) {
        if ($name -in @($current.PSObject.Properties.Name) -and ($current.$name -isnot [string] -or !$current.$name.Trim())) { throw ($label + ' ' + $name + ' 必须为非空字符串') }
      }
      $current=$current.within
      if ($null -eq $current) { break }
      $depth++
      if ($depth -gt 3) { throw ($label + ' within 最多嵌套三层') }
    }
  }
}

function Test-ImportPayload($payload) {
if (@($payload.cases.title | Select-Object -Unique).Count -ne @($payload.cases).Count) { throw "同批用例标题须唯一" }
if (@($payload.cases).Count -lt 1 -or @($payload.cases).Count -gt 20) { throw '每批提交 1 至 20 条用例' }
foreach ($case in $payload.cases) {
  if ($case.resolution) { throw '疑似重复由平台导入任务页集中确认，不在当前任务处理' }
  if (!$case.exec_kind -or $case.exec_kind -in @('gui','e2e')) { Test-ScriptTargets $case.script $case.title }
  if ($case.verdict -ne 'pass' -or @($case.script).Count -eq 0 -or @($case.script).Count -ne @($case.report).Count) { throw '只提交完整实测通过的用例' }
  if (!@($case.script | Where-Object { $_.action -like 'assert*' -or $_.action -eq 'judge' }).Count) { throw '用例必须包含业务断言' }
  for ($i=0; $i -lt @($case.script).Count; $i++) {
    $step=$case.script[$i]; $result=$case.report[$i]
    if ($step.action -ne $result.action -or $result.ok -ne $true -or $result.check.pass -eq $false) { throw '脚本与实测报告不一致或存在失败' }
    if ($step.action -like 'assert*' -and ($null -eq $result.check -or 'actual' -notin @($result.check.PSObject.Properties.Name) -or 'expected' -notin @($result.check.PSObject.Properties.Name))) { throw '断言缺少实际值或预期值' }
  }
}
}
