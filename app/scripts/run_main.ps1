param(
  [ValidateSet("simulate", "device")]
  [string]$Mode = "simulate"
)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if ($env:GUAZI_PYTHON) {
  $Python = $env:GUAZI_PYTHON
} elseif (Test-Path $BundledPython) {
  $Python = $BundledPython
} else {
  $Python = "python"
}
$env:PYTHONPATH = Join-Path $Root "src"
& $Python -m guazi_app_data_system.main --mode $Mode
