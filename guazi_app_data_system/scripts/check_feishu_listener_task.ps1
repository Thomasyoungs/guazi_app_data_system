$ErrorActionPreference = "Stop"

$TaskName = "GuaziFeishuListener"
$ProjectRoot = "C:\Users\lzc93\Desktop\定价\guazi_app_data_system"
$StartupLog = Join-Path $ProjectRoot "logs\feishu_listener_startup.log"
$RuntimeLog = Join-Path $ProjectRoot "logs\feishu_listener_runtime.log"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "TaskName: $TaskName"
    Write-Host "Exists: false"
} else {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "TaskName: $TaskName"
    Write-Host "Exists: true"
    Write-Host "State: $($task.State)"
    Write-Host "LastRunTime: $($info.LastRunTime)"
    Write-Host "LastTaskResult: $($info.LastTaskResult)"
}

Write-Host "StartupLogExists: $(Test-Path -LiteralPath $StartupLog)"
Write-Host "RuntimeLogExists: $(Test-Path -LiteralPath $RuntimeLog)"
