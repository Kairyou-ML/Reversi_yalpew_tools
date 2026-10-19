$ErrorActionPreference = "Stop"
$env:PYTHONPATH = Join-Path $PSScriptRoot "src"
$imagePath = Join-Path $PSScriptRoot "examples\initial_board.ppm"
$configPath = Join-Path $PSScriptRoot "config\sample_board.json"
python -m reversi_bot.demo $imagePath
python -m reversi_bot $imagePath --config $configPath --player black --depth 4 --time-ms 1500

