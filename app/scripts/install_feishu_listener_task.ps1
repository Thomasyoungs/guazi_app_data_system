param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$TaskName = "GuaziFeishuListener"
$ProjectRoot = "C:\Users\lzc93\Desktop\定价\guazi_app_data_system"
$StartScript = Join-Path $ProjectRoot "scripts\start_feishu_listener.ps1"

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existingTask) {
    if (-not $Force) {
        Write-Host "Task $TaskName already exists. Use -Force to replace it."
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`"" `
    -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT30S"

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings

Register-ScheduledTask -TaskName $TaskName -InputObject $task | Out-Null
Write-Host "FEISHU_LISTENER_TASK_INSTALLED"
