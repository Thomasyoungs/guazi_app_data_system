$ErrorActionPreference = "Stop"

# Project root = parent folder of this script's folder.
# This avoids hardcoding Chinese paths such as Desktop\定价.
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
Set-Location -LiteralPath $ProjectRoot

. (Join-Path $PSScriptRoot "feishu_service_single_instance.ps1")

$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$StartupLog = Join-Path $LogDir "feishu_listener_startup.log"
$RuntimeLog = Join-Path $LogDir "feishu_listener_runtime.log"
$RuntimeErrLog = Join-Path $LogDir "feishu_listener_runtime.err.log"
$ReceiverScript = Join-Path $ProjectRoot "scripts\feishu_realtime_receiver.py"
$CleanupScriptNames = @("feishu_realtime_receiver.py", "start_feishu_listener.ps1")
$ServiceScriptName = "feishu_realtime_receiver.py"
$StarterScriptName = "start_feishu_listener.ps1"

function Write-StartupLog {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $Message" | Tee-Object -FilePath $StartupLog -Append
}

try {
    Write-StartupLog "Starting Guazi Feishu Listener single-instance launcher"
    Write-StartupLog "ProjectRoot=$ProjectRoot"
    Write-StartupLog "Cleaning old listener processes in current project scope"

    $cleanup = Stop-ProjectScopedServiceProcesses `
        -ProjectRoot $ProjectRoot `
        -TargetScriptNames $CleanupScriptNames `
        -StarterScriptName $StarterScriptName `
        -ExcludeProcessIds @($PID)

    Write-StartupLog "Old listener processes found=$($cleanup.FoundCount)"
    foreach ($process in $cleanup.Stopped) {
        Write-StartupLog "Stopped old listener $(Format-ServiceProcessLine $process)"
    }
    Write-StartupLog "Remaining old listener processes=$($cleanup.RemainingCount)"

    if (-not $env:FEISHU_APP_ID) {
        Write-StartupLog "FEISHU_APP_ID missing. Set it as a Windows User environment variable."
        exit 10
    }

    if (-not $env:FEISHU_APP_SECRET) {
        Write-StartupLog "FEISHU_APP_SECRET missing. Set it as a Windows User environment variable."
        exit 11
    }

    Write-StartupLog "Self-check command: python scripts/feishu_realtime_receiver.py --self-check"
    & python $ReceiverScript --self-check *>&1 | Tee-Object -FilePath $StartupLog -Append
    if ($LASTEXITCODE -ne 0) {
        Write-StartupLog "Self-check failed. exit=$LASTEXITCODE"
        exit $LASTEXITCODE
    }

    Write-StartupLog "Self-check OK. New service command: python `"$ReceiverScript`" --listen"
    # Service command equivalent: python scripts/feishu_realtime_receiver.py --listen
    $process = Start-Process `
        -FilePath "python" `
        -ArgumentList @("`"$ReceiverScript`"", "--listen") `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $RuntimeLog `
        -RedirectStandardError $RuntimeErrLog

    Start-Sleep -Seconds 3
    if ($process.HasExited) {
        throw "Listener process exited during startup validation. ProcessId=$($process.Id) ExitCode=$($process.ExitCode)"
    }

    $instance = Assert-SingleProjectServiceInstance `
        -ProjectRoot $ProjectRoot `
        -ServiceScriptName $ServiceScriptName `
        -StarterScriptName $StarterScriptName `
        -ExcludeProcessIds @($PID)
    Write-StartupLog "Single listener instance verified. $(Format-ServiceProcessLine $instance)"

    Wait-Process -Id $process.Id
    Write-StartupLog "Listener exited. ProcessId=$($process.Id) ExitCode=$($process.ExitCode)"
    exit $process.ExitCode
} catch {
    Write-StartupLog "ERROR: $($_.Exception.Message)"
    exit 1
}
