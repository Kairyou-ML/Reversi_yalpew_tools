$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host "Environment not installed. Run .\setup.ps1 first."
    exit 1
}
& $pythonPath -m reversi_bot.gui --ld-dir "C:\LDPlayer\LDPlayer14"
