param(
    [string]$ListenHost = "0.0.0.0",
    [int]$Port = 8099,
    [switch]$Reload,
    [switch]$Background
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pidFile = Join-Path $projectRoot "server.pid"

function Stop-ServerProcessTree {
    param(
        [int]$RootPid,
        [string]$Label = "process"
    )

    $children = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ParentProcessId -eq $RootPid }
    )

    foreach ($child in $children) {
        Stop-ServerProcessTree -RootPid $child.ProcessId -Label "child process"
    }

    try {
        Stop-Process -Id $RootPid -Force -ErrorAction Stop
        Write-Host "Stopped $Label PID $RootPid"
    }
    catch {
        Write-Warning ("Could not stop {0} PID {1}: {2}" -f $Label, $RootPid, $_.Exception.Message)
    }
}

if (-not (Test-Path $pythonExe)) {
    Write-Error "Не найден интерпретатор $pythonExe. Сначала создайте .venv и установите зависимости."
}

Set-Location $projectRoot

if (Test-Path $pidFile) {
    $previousPid = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($previousPid) {
        Stop-ServerProcessTree -RootPid ([int]$previousPid) -Label "background server"
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

$uvicornProcessIds = @(
    Get-CimInstance Win32_Process -Filter "name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like "*uvicorn*" -and
            $_.CommandLine -like "*app.main:app*" -and
            $_.CommandLine -like "*--port $Port*"
        } |
        Select-Object -ExpandProperty ProcessId -Unique
) | Where-Object { $_ }

foreach ($uvicornProcessId in $uvicornProcessIds) {
    Stop-ServerProcessTree -RootPid $uvicornProcessId -Label "uvicorn server"
}

$processIds = @(
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
) | Where-Object { $_ }

$spawnChildrenForPortOwners = @(
    foreach ($processId in $processIds) {
        Get-CimInstance Win32_Process -Filter "name = 'python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*parent_pid=$processId*" } |
            Select-Object -ExpandProperty ProcessId -Unique
    }
) | Where-Object { $_ }

foreach ($spawnChild in $spawnChildrenForPortOwners) {
    Stop-ServerProcessTree -RootPid $spawnChild -Label "spawned server child"
}

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
if ($Background) {
    $outLog = Join-Path $projectRoot "server.out.log"
    $errLog = Join-Path $projectRoot "server.err.log"
    $process = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList $arguments `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru
    Set-Content -Path $pidFile -Value $process.Id
    Write-Host "Server started in background. PID $($process.Id)."
    Write-Host "Logs: $outLog, $errLog"
    exit 0
}

& $pythonExe @arguments
