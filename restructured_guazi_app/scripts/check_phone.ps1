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
# The device gate performs one approved transient ADB server recovery before
# reporting DEVICE_NOT_FOUND; it does not launch the APP in phone-check mode.
& $Python -m guazi_app_data_system.main --mode device --phone-check-only
