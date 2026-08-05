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

$requirementsPath = Join-Path $projectRoot "requirements-runtime.txt"
if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "Runtime requirements file not found: $requirementsPath"
}

$runtimeDataPath = Join-Path $projectRoot "runtime_data"
New-Item -ItemType Directory -Path $runtimeDataPath -Force | Out-Null
$dependencyStampPath = Join-Path $runtimeDataPath ".runtime-requirements.sha256"
$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
$dependencyFingerprint = "$requirementsHash|$pythonExe"
$savedFingerprint = if (Test-Path -LiteralPath $dependencyStampPath) {
    (Get-Content -Raw -LiteralPath $dependencyStampPath).Trim()
} else {
    ""
}

$dependencyProbe = @'
import importlib
import sys

modules = ('fastapi', 'uvicorn', 'multipart', 'numpy', 'PIL', 'docx', 'cv2')
failures = []
for name in modules:
    try:
        importlib.import_module(name)
    except Exception as exc:
        failures.append(f'{name}: {exc}')
if failures:
    print('Runtime dependency check failed:', file=sys.stderr)
    for failure in failures:
        print(f'  - {failure}', file=sys.stderr)
    raise SystemExit(1)
'@

& $pythonExe -c $dependencyProbe
$dependenciesAvailable = $LASTEXITCODE -eq 0
$requirementsChanged = $savedFingerprint -ne $dependencyFingerprint
if (-not $dependenciesAvailable -or $requirementsChanged) {
    Write-Host "Synchronizing runtime dependencies..." -ForegroundColor Cyan
    & $pythonExe -m pip install --disable-pip-version-check --requirement $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install runtime dependencies from: $requirementsPath"
    }
    & $pythonExe -c $dependencyProbe
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime dependency check still fails after installation."
    }
    Set-Content -LiteralPath $dependencyStampPath -Value $dependencyFingerprint -Encoding utf8
}

$hostAddress = if ($env:GO2_HOST) { $env:GO2_HOST } else { "0.0.0.0" }
$port = if ($env:GO2_PORT) { $env:GO2_PORT } else { "8000" }

& $pythonExe -m uvicorn app.api:app --host $hostAddress --port $port
