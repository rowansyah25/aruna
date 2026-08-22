@echo off
REM ===================================================================
REM  ARUNA - pasang autostart (Windows Task Scheduler).
REM
REM  Klik dua kali SEKALI SAJA. Sesudah itu ARUNA menyala sendiri
REM  setiap kali Anda login ke Windows, termasuk sesudah restart dan
REM  sesudah mati listrik.
REM
REM  Yang didaftarkan: satu tugas bernama "ARUNA" yang menjalankan
REM  ARUNA.bat di folder ini, atas nama akun Anda sendiri. Tidak butuh
REM  hak administrator dan tidak mengubah pengaturan sistem yang lain.
REM
REM  Kenapa lewat Task Scheduler dan bukan folder Startup:
REM    - batas waktu jalan dimatikan (bawaan Windows membunuh tugas
REM      setelah 3 hari; ARUNA harus jalan terus)
REM    - dinyalakan lagi kalau tugasnya sendiri mati
REM    - tetap jalan waktu memakai baterai
REM    - tidak jalan dua kali kalau Anda juga klik ARUNA.bat manual
REM
REM  Mau membatalkan? Klik dua kali COPOT-AUTOSTART.bat.
REM
REM  ARUNA MENGANALISIS SAJA. Tidak ada order yang dikirim, tidak ada
REM  leverage yang diubah, tidak ada dana yang berpindah.
REM ===================================================================

cd /d "%~dp0"
title Pasang autostart ARUNA

if not exist "ARUNA.bat" (
    echo.
    echo   TIDAK KETEMU: ARUNA.bat
    echo   Berkas ini harus berada di folder yang sama dengan ARUNA.bat.
    echo.
    pause
    exit /b 1
)

echo.
echo   Mendaftarkan tugas "ARUNA" supaya menyala saat login...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\autostart.ps1"

if errorlevel 1 (
    echo.
    echo   GAGAL mendaftarkan tugas. Pesan errornya di atas.
    echo.
    pause
    exit /b 1
)

echo.
echo   Selesai. ARUNA akan menyala sendiri setiap Anda login.
echo.
echo   Mau menyalakannya SEKARANG juga tanpa logout? Jalankan:
echo       schtasks /run /tn ARUNA
echo.
echo   Melihat statusnya:
echo       schtasks /query /tn ARUNA
echo.
pause
