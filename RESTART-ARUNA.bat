@echo off
REM ===================================================================
REM  ARUNA - restart.
REM
REM  Klik dua kali untuk menghentikan ARUNA yang sedang jalan lalu
REM  menyalakannya lagi. Dipakai sesudah kodenya berubah: proses yang
REM  sudah hidup tetap memakai kode lama sampai dinyalakan ulang.
REM
REM  Yang dilakukan, berurutan:
REM    1. tugas terjadwal ARUNA dihentikan (kalau ada), supaya ia tidak
REM       menyalakan ulang di tengah proses ini
REM    2. Ctrl+C dikirim ke supervisor - berhenti dengan rapi: anak-anak
REM       dihentikan, koneksi database ditutup, pesan penutup terkirim
REM    3. kalau dalam 25 detik belum juga berhenti, dipaksa - dan itu
REM       dikatakan, bukan disembunyikan
REM    4. menunggu kunci logs\aruna.lock benar-benar lepas
REM    5. ARUNA.bat dinyalakan di jendela baru
REM
REM  Langkah 4 bukan basa-basi. Cuma satu ARUNA yang boleh hidup, dan
REM  yang baru akan MENOLAK menyala selama kunci lama masih dipegang.
REM  Tanpa menunggu, restart terlihat gagal padahal justru kuncinya
REM  yang bekerja.
REM
REM  Mau berhenti saja tanpa dinyalakan lagi? Jalankan:
REM      powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restart.ps1 -StopOnly
REM
REM  ARUNA MENGANALISIS SAJA. Tidak ada order yang dikirim, tidak ada
REM  leverage yang diubah, tidak ada dana yang berpindah.
REM ===================================================================

cd /d "%~dp0"
title Restart ARUNA

if not exist "scripts\restart.ps1" (
    echo.
    echo   TIDAK KETEMU: scripts\restart.ps1
    echo.
    pause
    exit /b 1
)

echo.
echo   Merestart ARUNA...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\restart.ps1"

if errorlevel 1 (
    echo.
    echo   RESTART TIDAK SELESAI. Pesannya di atas.
    echo.
    pause
    exit /b 1
)

echo.
echo   Selesai. ARUNA berjalan di jendela terpisah.
echo   Menutup jendela ini tidak menghentikan ARUNA.
echo.
pause
