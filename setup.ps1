$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
if (-not (Test-Path -LiteralPath $venvPath)) {
    python -m venv $venvPath
}
$pythonPath = Join-Path $venvPath "Scripts\python.exe"
& $pythonPath -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    throw "Could not install pip/setuptools/wheel. Check the network and retry."
}
& $pythonPath -m pip install --no-build-isolation -e "$projectRoot[live]"
if ($LASTEXITCODE -ne 0) {
    throw "Could not install Reversi Monitor dependencies."
}
Write-Host "Setup complete. Run .\run_monitor.ps1"
