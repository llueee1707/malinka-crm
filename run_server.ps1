param(
    [string]$Host = "127.0.0.1",
    [int]$Port = 8099,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    Write-Error "Не найден интерпретатор $pythonExe. Сначала создайте .venv и установите зависимости."
}

Set-Location $projectRoot

$arguments = @(
    "-m"
    "uvicorn"
    "app.main:app"
    "--host"
    $Host
    "--port"
    $Port
)

if ($Reload) {
    $arguments += "--reload"
}

Write-Host "Запуск сервера: http://$Host`:$Port"
& $pythonExe @arguments
