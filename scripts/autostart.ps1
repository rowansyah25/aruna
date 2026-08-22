# Mendaftarkan ARUNA ke Windows Task Scheduler supaya menyala saat login.
#
# Dipanggil oleh PASANG-AUTOSTART.bat. Dipisah dari berkas .bat karena
# PowerShell yang ditulis lewat baris sambung "^" dengan kutip bersarang
# tidak bisa diuji dan salah kutip satu saja membuat tugasnya terdaftar
# dengan perintah yang tidak jalan - kegagalan yang baru ketahuan berminggu
# kemudian, saat ARUNA ternyata tidak pernah menyala sesudah restart.
#
# -WhatIf mencetak apa yang akan didaftarkan tanpa mendaftarkannya.

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $TaskName = 'ARUNA',
    # Kosong = folder induk skrip ini. Diisi di badan, bukan di sini:
    # PowerShell 5.1 mengevaluasi nilai bawaan param sebelum $PSScriptRoot ada.
    [string] $Root
)

$ErrorActionPreference = 'Stop'

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }

$bat = Join-Path $Root 'ARUNA.bat'
if (-not (Test-Path $bat)) {
    throw "Tidak ketemu: $bat"
}

$action = New-ScheduledTaskAction `
    -Execute 'cmd.exe' `
    -Argument ('/c "' + $bat + '"') `
    -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

# Bawaan Windows membunuh tugas sesudah 3 hari. ARUNA harus jalan terus, jadi
# batasnya dihapus. Nol berarti tanpa batas; properti ini tidak punya parameter
# di New-ScheduledTaskSettingsSet, jadi diset langsung.
$settings.ExecutionTimeLimit = 'PT0S'

$desc = @'
ARUNA - riset pasar dan paper trading.
Menganalisis saja: tidak mengirim order, tidak mengubah leverage,
tidak memindahkan dana. Semua eksekusi ada di tangan operator.
'@

if ($PSCmdlet.ShouldProcess($TaskName, 'Register-ScheduledTask')) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description $desc `
        -Force | Out-Null
    Write-Host "  Terdaftar: $TaskName" -ForegroundColor Green
} else {
    Write-Host "  (uji coba) akan mendaftarkan: $TaskName"
    Write-Host "    perintah  : cmd.exe /c `"$bat`""
    Write-Host "    folder    : $Root"
    Write-Host "    pemicu    : saat $env:USERDOMAIN\$env:USERNAME login"
    Write-Host "    batas jalan: $($settings.ExecutionTimeLimit) (PT0S = tanpa batas)"
}
