<#
    ARUNA AI - Windows setup (PHASE 1)

    Creates the virtual environment, installs dependencies, and creates .env
    from the template.  Safe to re-run; it never overwrites an existing .env.

    Usage:
        powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
        powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Dev
#>
[CmdletBinding()]
param(
    # Install pytest / ruff as well.
    [switch]$Dev,
    # Explicit interpreter to build the venv from (must be 3.12+).
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "ARUNA AI setup" -ForegroundColor Cyan
Write-Host "Project root: $root"

# --- locate a Python 3.12+ interpreter ---------------------------------
function Test-PythonVersion([string]$exe) {
    if (-not (Test-Path $exe)) { return $false }
    try {
        $out = & $exe -c "import sys; print(sys.version_info[0], sys.version_info[1])" 2>$null
    } catch { return $false }
    if (-not $out) { return $false }
    $parts = $out.Trim().Split(' ')
    return ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 12)
}

if ($Python) {
    $interpreter = $Python
} else {
    $candidates = @()
    Get-ChildItem 'C:\laragon\bin\python' -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { $candidates += (Join-Path $_.FullName 'python.exe') }
    $onPath = (Get-Command python -ErrorAction SilentlyContinue)
    if ($onPath) { $candidates += $onPath.Source }

    $interpreter = $null
    foreach ($candidate in $candidates) {
        if (Test-PythonVersion $candidate) { $interpreter = $candidate; break }
    }
}

if (-not $interpreter -or -not (Test-PythonVersion $interpreter)) {
    Write-Host "FAILED: no Python 3.12+ interpreter found." -ForegroundColor Red
    Write-Host "Install Python 3.12 or newer, or pass one explicitly:" -ForegroundColor Yellow
    Write-Host "  .\scripts\setup.ps1 -Python 'C:\path\to\python.exe'"
    exit 1
}

$version = (& $interpreter --version).Trim()
Write-Host "Interpreter:  $interpreter ($version)" -ForegroundColor Green

# --- virtual environment -----------------------------------------------
if (-not (Test-Path '.venv')) {
    Write-Host "`nCreating .venv ..."
    & $interpreter -m venv .venv
} else {
    Write-Host "`n.venv already exists - reusing it."
}

$venvPython = Join-Path $root '.venv\Scripts\python.exe'

Write-Host "Upgrading pip ..."
& $venvPython -m pip install --upgrade pip --quiet

$requirements = if ($Dev) { 'requirements-dev.txt' } else { 'requirements.txt' }
Write-Host "Installing $requirements ..."
& $venvPython -m pip install -r $requirements --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: dependency install" -ForegroundColor Red; exit 1 }

Write-Host "Installing aruna in editable mode ..."
& $venvPython -m pip install -e . --quiet --no-deps

# --- .env ---------------------------------------------------------------
if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host "`nCreated .env from .env.example." -ForegroundColor Yellow
    Write-Host "You must set ARUNA_DB_PASSWORD before ARUNA will start."
} else {
    Write-Host "`n.env already exists - left untouched." -ForegroundColor Green
}

# --- next steps ---------------------------------------------------------
Write-Host "`nSetup complete." -ForegroundColor Cyan
Write-Host @"

Next:
  1. Edit .env               (ARUNA_DB_PASSWORD, and ARUNA_TELEGRAM_* if you want the bot)
  2. .\.venv\Scripts\python.exe -m aruna doctor
  3. .\.venv\Scripts\python.exe -m aruna createdb
  4. .\.venv\Scripts\python.exe -m aruna migrate
  5. .\.venv\Scripts\python.exe -m aruna seed
  6. .\.venv\Scripts\python.exe -m aruna run

Redis is optional. To start Laragon's bundled Redis:
  .\scripts\start_redis.ps1
"@
