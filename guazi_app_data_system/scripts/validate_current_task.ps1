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
$Code = @'
import json
import sys

from guazi_app_data_system.feishu_sync import validate_current_target_task

result = validate_current_target_task()
print(json.dumps(result, ensure_ascii=False, indent=2))
if result.get("status") in {"TASK_IMPORT_VERIFIED", "CURRENT_TASK_FILE_NOT_FOUND"}:
    raise SystemExit(0)
raise SystemExit(1)
'@

$Code | & $Python -
