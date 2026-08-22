# Hentikan ARUNA yang sedang jalan, lalu nyalakan lagi.
#
# Dipisah dari berkas .bat supaya bisa diuji: jalankan dengan -WhatIf untuk
# melihat apa yang akan dihentikan tanpa menghentikan apa pun.
#
# Tiga hal yang mudah salah, dan ketiganya ditangani di sini.
#
# 1. BERHENTI DENGAN RAPI, BUKAN DIBUNUH.
#    Supervisor memasang penanganan Ctrl+C: ia menghentikan anak-anaknya,
#    menutup koneksi database, dan mengirim pesan penutup. Membunuh paksa
#    melewati semua itu. Jadi Ctrl+C dikirim dulu; paksa hanya kalau menolak
#    mati - dan kalau sampai dipaksa, itu dikatakan.
#
# 2. MENUNGGU KUNCI LEPAS.
#    ARUNA memegang kunci instans-tunggal di logs\aruna.lock. Selama proses
#    lama masih hidup, ARUNA baru akan MENOLAK menyala dan bilang "sudah
#    jalan" - dan operator akan mengira restart-nya gagal padahal justru
#    kuncinya bekerja. Jadi skrip ini menunggu kuncinya benar-benar bebas.
#
# 3. TUGAS TERJADWAL IKUT DIHENTIKAN.
#    Kalau ARUNA dijalankan lewat Task Scheduler, menghentikan prosesnya saja
#    tidak cukup: penjadwal bisa menyalakannya lagi. Tugasnya di-end dulu.
#
# ARUNA MENGANALISIS SAJA. Tidak ada order, tidak ada dana yang berpindah.

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    # Berapa lama menunggu berhenti dengan rapi sebelum dipaksa.
    [int] $GracefulSeconds = 25,
    # Berapa lama menunggu kunci lepas sesudah proses hilang.
    [int] $LockSeconds = 20,
    # Hentikan saja, jangan nyalakan lagi.
    [switch] $StopOnly
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$LockPath = Join-Path $Root 'logs\aruna.lock'

# CimCmdlets dimuat sekarang, bukan nanti saat dipakai. Pemuatan otomatis
# membuat alias, dan pembuatan alias itu ikut dilaporkan oleh -WhatIf - dua
# belas baris "Set Alias" yang menenggelamkan satu-satunya baris yang
# sebenarnya ingin dibaca operator.
$simpanWhatIf = $WhatIfPreference
$WhatIfPreference = $false
Import-Module CimCmdlets -ErrorAction SilentlyContinue | Out-Null
$WhatIfPreference = $simpanWhatIf

# --- menemukan apa yang jalan ------------------------------------------------

function Get-ArunaProcesses {
    # Dicocokkan pada command line, bukan pada nama proses: nama prosesnya
    # "python.exe", sama seperti setiap skrip Python lain di mesin ini -
    # termasuk yang sedang dipakai untuk hal lain.
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='cmd.exe'" |
        Where-Object { $_.CommandLine -match 'aruna\.cli|ARUNA\.bat' }
}

function Get-Supervisors {
    Get-ArunaProcesses | Where-Object { $_.CommandLine -match 'aruna\.cli\s+supervise' }
}

# --- Ctrl+C ke proses lain ---------------------------------------------------

$signature = @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool AttachConsole(uint dwProcessId);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool FreeConsole();
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool SetConsoleCtrlHandler(IntPtr handler, bool add);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool GenerateConsoleCtrlEvent(uint dwCtrlEvent, uint dwProcessGroupId);
'@

if (-not ('ArunaCtrl' -as [type])) {
    Add-Type -MemberDefinition $signature -Name 'ArunaCtrl' -Namespace '' | Out-Null
}

function Send-CtrlC {
    param([int] $ProcessId)

    # Ctrl+C dikirim ke seluruh grup konsol, jadi skrip ini harus menempel ke
    # konsol milik proses itu dulu - dan mematikan penanganan Ctrl+C untuk
    # dirinya sendiri, kalau tidak ia ikut mati bersama yang mau dihentikan.
    try {
        [ArunaCtrl]::FreeConsole() | Out-Null
        if (-not [ArunaCtrl]::AttachConsole([uint32] $ProcessId)) { return $false }
        [ArunaCtrl]::SetConsoleCtrlHandler([IntPtr]::Zero, $true) | Out-Null
        $ok = [ArunaCtrl]::GenerateConsoleCtrlEvent(0, 0)   # 0 = CTRL_C_EVENT
        Start-Sleep -Milliseconds 300
        return $ok
    } catch {
        return $false
    } finally {
        [ArunaCtrl]::FreeConsole() | Out-Null
        # Menempel kembali ke konsol induk. Tanpa baris ini skrip berjalan
        # tanpa konsol sama sekali sesudah langkah ini, dan setiap Write-Host
        # berikutnya gagal dengan "The handle is invalid" - termasuk pesan
        # "KUNCI MASIH DIPEGANG", yaitu satu-satunya pesan yang benar-benar
        # harus terbaca operator. Terukur: pesan "Berhenti dengan rapi" gagal
        # dicetak persis karena ini.
        [ArunaCtrl]::AttachConsole([uint32]"0xFFFFFFFF") | Out-Null
        [ArunaCtrl]::SetConsoleCtrlHandler([IntPtr]::Zero, $false) | Out-Null
    }
}

# --- kunci -------------------------------------------------------------------

function Test-LockFree {
    if (-not (Test-Path $LockPath)) { return $true }
    try {
        # FileShare::None gagal selama ADA handle lain yang membuka berkas ini.
        # Itu justru yang mau diketahui: bukan "apakah kuncinya dilepas" tapi
        # "apakah masih ada yang memegangnya".
        $stream = [System.IO.File]::Open(
            $LockPath, 'Open', 'ReadWrite', 'None')
        $stream.Close()
        return $true
    } catch {
        return $false
    }
}

# --- jalannya ----------------------------------------------------------------

$berjalan = @(Get-ArunaProcesses)

if (-not $berjalan) {
    Write-Host "  Tidak ada ARUNA yang sedang jalan." -ForegroundColor Yellow
} else {
    Write-Host "  Yang sedang jalan:"
    foreach ($p in $berjalan) {
        $cmd = $p.CommandLine
        if ($cmd.Length -gt 78) { $cmd = $cmd.Substring(0, 78) + '...' }
        Write-Host ("    PID {0,-6} {1}" -f $p.ProcessId, $cmd)
    }
    Write-Host ""
}

if ($PSCmdlet.ShouldProcess('tugas terjadwal ARUNA', 'schtasks /end')) {
    # Kalau tugasnya tidak ada atau tidak sedang jalan, schtasks mengeluh - dan
    # itu bukan masalah, tidak ada yang perlu dihentikan. Dipanggil lewat
    # Stop-ScheduledTask (cmdlet) supaya keluhan itu tidak berubah menjadi
    # NativeCommandError yang terbaca operator sebagai kegagalan restart.
    try {
        Stop-ScheduledTask -TaskName 'ARUNA' -ErrorAction Stop
    } catch {
        # Tugasnya tidak terdaftar, atau memang tidak sedang jalan.
    }
}

if ($berjalan -and $PSCmdlet.ShouldProcess('ARUNA', 'hentikan')) {
    Write-Host "  Meminta berhenti dengan rapi (Ctrl+C)..."
    foreach ($s in @(Get-Supervisors)) { Send-CtrlC -ProcessId $s.ProcessId | Out-Null }

    $batas = (Get-Date).AddSeconds($GracefulSeconds)
    while ((Get-Date) -lt $batas -and @(Get-ArunaProcesses)) {
        Start-Sleep -Milliseconds 500
    }

    $sisa = @(Get-ArunaProcesses)
    if ($sisa) {
        # Dikatakan, bukan dilakukan diam-diam. Paksa berarti shutdown rapi
        # tidak selesai: koneksi database tidak ditutup baik-baik, dan pesan
        # penutup tidak terkirim.
        Write-Host ""
        Write-Host "  $($sisa.Count) proses tidak berhenti dalam $GracefulSeconds detik." -ForegroundColor Yellow
        Write-Host "  DIPAKSA. Shutdown rapi tidak selesai." -ForegroundColor Yellow
        foreach ($p in $sisa) {
            # Stop-Process, bukan taskkill. Dua alasan, keduanya terukur:
            #
            # * taskkill /T sudah menghabisi anak-anaknya, jadi PID anak yang
            #   menyusul di daftar ini sudah tidak ada - dan taskkill menulis
            #   "process not found" ke stderr. Di PowerShell 5.1, stderr dari
            #   exe native yang diredirect berubah jadi NativeCommandError yang
            #   muncul di layar operator sebagai kegagalan, padahal prosesnya
            #   memang sudah mati seperti yang diinginkan.
            # * Stop-Process itu cmdlet: errornya bisa ditangkap dengan rapi.
            #
            # Daftar prosesnya sudah lengkap di $sisa termasuk anak-anaknya,
            # jadi tidak ada yang hilang tanpa /T.
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            } catch {
                # Sudah mati duluan. Itu keadaan yang diinginkan, bukan galat.
            }
        }
        Start-Sleep -Seconds 2
    } else {
        Write-Host "  Berhenti dengan rapi." -ForegroundColor Green
    }
}

if ($StopOnly) {
    Write-Host ""
    Write-Host "  Berhenti saja - tidak dinyalakan lagi (-StopOnly)."
    return
}

if ($PSCmdlet.ShouldProcess('kunci instans-tunggal', 'tunggu sampai bebas')) {
    Write-Host "  Menunggu kunci instans-tunggal lepas..."
    $batas = (Get-Date).AddSeconds($LockSeconds)
    while ((Get-Date) -lt $batas -and -not (Test-LockFree)) {
        Start-Sleep -Milliseconds 400
    }
    if (-not (Test-LockFree)) {
        # Menyalakan sekarang akan membuat ARUNA baru menolak jalan dan
        # mencetak "sudah jalan" - dan operator akan mengira restart-nya gagal
        # padahal justru kuncinya yang bekerja. Lebih baik berhenti di sini
        # dan mengatakan apa yang terjadi.
        Write-Host ""
        Write-Host "  KUNCI MASIH DIPEGANG: $LockPath" -ForegroundColor Red
        Write-Host "  Masih ada proses ARUNA yang hidup. Tidak dinyalakan," -ForegroundColor Red
        Write-Host "  karena yang baru pasti menolak menyala." -ForegroundColor Red
        Write-Host ""
        Write-Host "  Periksa dengan:"
        Write-Host "      Get-CimInstance Win32_Process -Filter `"Name='python.exe'`" | Select ProcessId,CommandLine"
        exit 1
    }
    Write-Host "  Kunci bebas." -ForegroundColor Green
}

$bat = Join-Path $Root 'ARUNA.bat'
if ($PSCmdlet.ShouldProcess($bat, 'nyalakan')) {
    Write-Host ""
    Write-Host "  Menyalakan ARUNA..."
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$bat`"" -WorkingDirectory $Root
    Write-Host "  Jendela ARUNA terbuka di jendela baru." -ForegroundColor Green
} else {
    Write-Host "  (uji coba) akan menyalakan: $bat"
}
