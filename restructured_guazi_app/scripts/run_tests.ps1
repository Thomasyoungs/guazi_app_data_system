$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$TempRoot = Join-Path $Root "artifacts\test_runtime"
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
$env:TMP = $TempRoot
$env:TEMP = $TempRoot
$env:GUAZI_TEST_MODE = "1"
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if ($env:GUAZI_PYTHON) {
  $Python = $env:GUAZI_PYTHON
} elseif (Test-Path $BundledPython) {
  $Python = $BundledPython
} else {
  $Python = "python"
}
$env:PYTHONPATH = Join-Path $Root "src"
& $Python -m unittest discover -s (Join-Path $Root "tests") -p "test_*.py" -v
