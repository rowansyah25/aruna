@echo off
REM ===================================================================
REM  ARUNA - jalankan semuanya, satu klik.
REM
REM  Klik dua kali berkas ini. Jendela ini tetap terbuka; menutupnya
REM  menghentikan ARUNA. Ctrl+C juga menghentikannya dengan rapi.
REM
REM  Yang dijalankan:
REM    aruna run          bot Telegram, ingest, aliran WebSocket,
REM                       loop upkeep (candle, berita, pemindai,
REM                       resolusi, penguncian prediksi)
REM    aruna futures-loop analisis perpetual lewat REST
REM
REM  Penjaganya menyalakan ulang apa yang mati, dengan jeda yang
REM  melebar, dan berteriak CRITICAL kalau sesuatu gagal berulang
REM  tanpa pernah benar-benar hidup.
REM
REM  Cuma satu ARUNA yang boleh hidup. Kalau sudah ada yang jalan
REM  (misalnya dari autostart), berkas ini menolak menyala dan bilang
REM  begitu - bukan diam-diam jalan dobel.
REM
REM  Mau ARUNA menyala sendiri tiap login, tanpa perlu klik?
REM    klik dua kali PASANG-AUTOSTART.bat  (sekali saja)
REM    membatalkannya: COPOT-AUTOSTART.bat
REM
REM  ARUNA MENGANALISIS SAJA. Tidak ada order yang dikirim, tidak ada
REM  leverage yang diubah, tidak ada dana yang berpindah. Semua
REM  keputusan dan eksekusi ada di tangan Anda.
REM ===================================================================

cd /d "%~dp0"
title ARUNA

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   TIDAK KETEMU: .venv\Scripts\python.exe
    echo   Jalankan dulu pemasangan dependensinya di folder ini.
    echo.
    pause
    exit /b 1
)

echo.
echo   ARUNA sedang dinyalakan. Biarkan jendela ini terbuka.
echo.

".venv\Scripts\python.exe" -m aruna.cli supervise

echo.
echo   ARUNA berhenti.
pause
