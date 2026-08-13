$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
Set-Location -LiteralPath $ProjectRoot

. (Join-Path $PSScriptRoot "feishu_service_single_instance.ps1")

$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir "feishu_dispatcher_autostart.log"
$RuntimeLog = Join-Path $LogDir "feishu_dispatcher_runtime.log"
$RuntimeErrLog = Join-Path $LogDir "feishu_dispatcher_runtime.err.log"
$DispatcherScript = Join-Path $ProjectRoot "scripts\feishu_pricing_dispatcher.py"
$CleanupScriptNames = @("feishu_pricing_dispatcher.py", "start_feishu_dispatcher.ps1")
$ServiceScriptName = "feishu_pricing_dispatcher.py"
$StarterScriptName = "start_feishu_dispatcher.ps1"

function Write-DispatcherLog {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $Message" | Tee-Object -FilePath $LogPath -Append
}

try {
    Write-DispatcherLog "Starting Guazi Feishu Dispatcher single-instance launcher"
    Write-DispatcherLog "ProjectRoot=$ProjectRoot"
    Write-DispatcherLog "Cleaning old dispatcher processes in current project scope"

    $cleanup = Stop-ProjectScopedServiceProcesses `
        -ProjectRoot $ProjectRoot `
        -TargetScriptNames $CleanupScriptNames `
        -StarterScriptName $StarterScriptName `
        -ExcludeProcessIds @($PID)

    Write-DispatcherLog "Old dispatcher processes found=$($cleanup.FoundCount)"
    foreach ($process in $cleanup.Stopped) {
        Write-DispatcherLog "Stopped old dispatcher $(Format-ServiceProcessLine $process)"
    }
    Write-DispatcherLog "Remaining old dispatcher processes=$($cleanup.RemainingCount)"

    while ($true) {
        Write-DispatcherLog "New service command: python `"$DispatcherScript`" --loop --allow-app-run"
        $process = Start-Process `
            -FilePath "python" `
            -ArgumentList @("`"$DispatcherScript`"", "--loop", "--allow-app-run") `
            -PassThru `
            -WindowStyle Hidden `
            -RedirectStandardOutput $RuntimeLog `
            -RedirectStandardError $RuntimeErrLog

        Start-Sleep -Seconds 3
        if ($process.HasExited) {
            throw "Dispatcher process exited during startup validation. ProcessId=$($process.Id) ExitCode=$($process.ExitCode)"
        }

        $instance = Assert-SingleProjectServiceInstance `
            -ProjectRoot $ProjectRoot `
            -ServiceScriptName $ServiceScriptName `
            -StarterScriptName $StarterScriptName `
            -ExcludeProcessIds @($PID)
        Write-DispatcherLog "Single dispatcher instance verified. $(Format-ServiceProcessLine $instance)"

        Wait-Process -Id $process.Id
        $exitCode = $process.ExitCode
        Write-DispatcherLog "Dispatcher exited. ProcessId=$($process.Id) ExitCode=$exitCode. Restart after 10 seconds."
        Start-Sleep -Seconds 10
    }
} catch {
    Write-DispatcherLog "ERROR: $($_.Exception.Message)"
    exit 1
}
