$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$projectVenvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$env:YOLO_CONFIG_DIR = Join-Path $projectRoot "runtime_data"

if (Test-Path -LiteralPath $projectVenvPython) {
    $pythonExe = $projectVenvPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "Python not found. Create a virtual environment and install the project dependencies first."
    }
    $pythonExe = $pythonCommand.Source
}

& $pythonExe -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) {
    $selectedVersion = & $pythonExe --version 2>&1
    throw "GO2 Inspection requires Python 3.12; selected: $selectedVersion"
}

$hostAddress = if ($env:GO2_HOST) { $env:GO2_HOST } else { "0.0.0.0" }
$port = if ($env:GO2_PORT) { $env:GO2_PORT } else { "8000" }

& $pythonExe -m uvicorn app.api:app --host $hostAddress --port $port
