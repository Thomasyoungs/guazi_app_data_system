$ErrorActionPreference = "Stop"

$TaskName = "GuaziFeishuListener"
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($null -eq $existingTask) {
    Write-Host "Task $TaskName does not exist."
    Write-Host "FEISHU_LISTENER_TASK_UNINSTALLED"
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "FEISHU_LISTENER_TASK_UNINSTALLED"
