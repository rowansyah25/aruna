<#
    Starts the Redis bundled with Laragon, if it is not already running.

    Redis is optional for ARUNA: without it the system still runs, PostgreSQL
    remains the store of record, and the health monitor reports redis as DOWN.

    Usage:
        powershell -ExecutionPolicy Bypass -File scripts\start_redis.ps1
        powershell -ExecutionPolicy Bypass -File scripts\start_redis.ps1 -Stop
#>
[CmdletBinding()]
param(
    [int]$Port = 6379,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'

function Test-RedisPort([int]$p) {
    (Test-NetConnection -ComputerName 127.0.0.1 -Port $p -WarningAction SilentlyContinue).TcpTestSucceeded
}

if ($Stop) {
    $running = Get-Process redis-server -ErrorAction SilentlyContinue
    if (-not $running) { Write-Host "Redis is not running."; exit 0 }
    $running | Stop-Process -Force
    Write-Host "Stopped Redis." -ForegroundColor Green
    exit 0
}

if (Test-RedisPort $Port) {
    Write-Host "Redis is already listening on 127.0.0.1:$Port." -ForegroundColor Green
    exit 0
}

$redisDir = Get-ChildItem 'C:\laragon\bin\redis' -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
if (-not $redisDir) {
    Write-Host "No Redis found under C:\laragon\bin\redis." -ForegroundColor Yellow
    Write-Host "Either install Redis, or set ARUNA_REDIS_ENABLED=false in .env."
    exit 1
}

$exe = Join-Path $redisDir.FullName 'redis-server.exe'
if (-not (Test-Path $exe)) {
    Write-Host "redis-server.exe not found in $($redisDir.FullName)." -ForegroundColor Red
    exit 1
}

Write-Host "Starting $exe on port $Port ..."
Start-Process -FilePath $exe -ArgumentList "--port $Port" -WindowStyle Hidden

for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Milliseconds 300
    if (Test-RedisPort $Port) {
        Write-Host "Redis is up on 127.0.0.1:$Port." -ForegroundColor Green
        exit 0
    }
}

Write-Host "Redis did not come up within 3 seconds." -ForegroundColor Red
exit 1
