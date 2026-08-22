@echo off
REM ===================================================================
REM  ARUNA - copot autostart.
REM
REM  Menghapus tugas terjadwal "ARUNA" saja. Tidak menghapus data,
REM  tidak menghapus database, tidak menghapus berkas apa pun di folder
REM  ini. ARUNA masih bisa dijalankan manual lewat ARUNA.bat.
REM
REM  Kalau ARUNA sedang jalan, jendelanya tetap jalan sampai ditutup.
REM ===================================================================

cd /d "%~dp0"
title Copot autostart ARUNA

echo.
echo   Menghapus tugas terjadwal "ARUNA"...
echo.

schtasks /delete /tn ARUNA /f

if errorlevel 1 (
    echo.
    echo   Tugas "ARUNA" tidak ada atau gagal dihapus.
    echo   Kalau memang belum pernah dipasang, ini normal.
    echo.
    pause
    exit /b 1
)

echo.
echo   Selesai. ARUNA tidak lagi menyala sendiri saat login.
echo   Menjalankannya manual: klik dua kali ARUNA.bat
echo.
pause
