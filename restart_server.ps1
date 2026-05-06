param(
    [string]$ListenHost = "127.0.0.1",
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

$processIds = @(
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
) | Where-Object { $_ }

foreach ($processId in $processIds) {
    try {
        Stop-Process -Id $processId -Force -ErrorAction Stop
        Write-Host "Остановлен процесс PID $processId на порту $Port"
    }
    catch {
        Write-Warning ("Не удалось остановить PID {0}: {1}" -f $processId, $_.Exception.Message)
    }
}

Start-Sleep -Seconds 1

$arguments = @(
    "-m"
    "uvicorn"
    "app.main:app"
    "--host"
    $ListenHost
    "--port"
    $Port
)

if ($Reload) {
    $arguments += "--reload"
}

Write-Host "Запуск сервера: http://$ListenHost`:$Port"
& $pythonExe @arguments
