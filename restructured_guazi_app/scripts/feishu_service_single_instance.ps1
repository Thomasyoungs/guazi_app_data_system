$ErrorActionPreference = "Stop"

function Normalize-ServiceProjectRoot {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    return [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
}

function Get-ProjectScopedServiceProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string[]]$TargetScriptNames,
        [string]$StarterScriptName = "",
        [int[]]$ExcludeProcessIds = @()
    )

    $root = Normalize-ServiceProjectRoot $ProjectRoot
    $comparison = [System.StringComparison]::OrdinalIgnoreCase
    $processes = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine })
    $byPid = @{}
    foreach ($process in $processes) {
        $byPid[[int]$process.ProcessId] = $process
    }

    $matches = @()
    foreach ($process in $processes) {
        $processId = [int]$process.ProcessId
        if ($ExcludeProcessIds -contains $processId) {
            continue
        }

        $commandLine = [string]$process.CommandLine
        $scriptMatched = $false
        foreach ($scriptName in $TargetScriptNames) {
            if ($commandLine.IndexOf($scriptName, $comparison) -ge 0) {
                $scriptMatched = $true
                break
            }
        }
        if (-not $scriptMatched) {
            continue
        }

        $projectMatched = $commandLine.IndexOf($root, $comparison) -ge 0
        $parentMatched = $false
        $parentCommandLine = ""
        if ($process.ParentProcessId -and $byPid.ContainsKey([int]$process.ParentProcessId)) {
            $parentCommandLine = [string]$byPid[[int]$process.ParentProcessId].CommandLine
            if ($StarterScriptName -and
                $parentCommandLine.IndexOf($StarterScriptName, $comparison) -ge 0 -and
                $parentCommandLine.IndexOf($root, $comparison) -ge 0) {
                $parentMatched = $true
            }
        }

        if ($projectMatched -or $parentMatched) {
            $matches += [pscustomobject]@{
                ProcessId = $processId
                ParentProcessId = $process.ParentProcessId
                CommandLine = $commandLine
                ParentCommandLine = $parentCommandLine
            }
        }
    }
    return @($matches)
}

function Format-ServiceProcessLine {
    param([Parameter(Mandatory = $true)]$ProcessInfo)
    return "ProcessId=$($ProcessInfo.ProcessId) CommandLine=$($ProcessInfo.CommandLine)"
}

function Stop-ProjectScopedServiceProcesses {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string[]]$TargetScriptNames,
        [string]$StarterScriptName = "",
        [int[]]$ExcludeProcessIds = @()
    )

    $matches = @(Get-ProjectScopedServiceProcess `
        -ProjectRoot $ProjectRoot `
        -TargetScriptNames $TargetScriptNames `
        -StarterScriptName $StarterScriptName `
        -ExcludeProcessIds $ExcludeProcessIds)

    $stopped = @()
    foreach ($process in $matches) {
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
            $stopped += $process
        } catch {
            $stillRunning = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
            if ($stillRunning) {
                throw "Failed to stop old service process. $(Format-ServiceProcessLine $process). Error=$($_.Exception.Message)"
            }
        }
    }

    if ($matches.Count -gt 0) {
        Start-Sleep -Seconds 1
    }

    $remaining = @(Get-ProjectScopedServiceProcess `
        -ProjectRoot $ProjectRoot `
        -TargetScriptNames $TargetScriptNames `
        -StarterScriptName $StarterScriptName `
        -ExcludeProcessIds $ExcludeProcessIds)

    if ($remaining.Count -gt 0) {
        $details = ($remaining | ForEach-Object { Format-ServiceProcessLine $_ }) -join "`n"
        throw "Old service processes remain after cleanup:`n$details"
    }

    return [pscustomobject]@{
        FoundCount = $matches.Count
        Stopped = @($stopped)
        RemainingCount = $remaining.Count
    }
}

function Assert-SingleProjectServiceInstance {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$ServiceScriptName,
        [string]$StarterScriptName = "",
        [int[]]$ExcludeProcessIds = @()
    )

    $matches = @(Get-ProjectScopedServiceProcess `
        -ProjectRoot $ProjectRoot `
        -TargetScriptNames @($ServiceScriptName) `
        -StarterScriptName $StarterScriptName `
        -ExcludeProcessIds $ExcludeProcessIds)

    if ($matches.Count -ne 1) {
        $details = ($matches | ForEach-Object { Format-ServiceProcessLine $_ }) -join "`n"
        throw "Expected exactly one $ServiceScriptName instance in project scope, found $($matches.Count).`n$details"
    }

    return $matches[0]
}
