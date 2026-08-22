## Aturan Kecepatan
- Jangan pernah Start-Sleep > 20 detik. Kalau perlu nunggu, polling tiap 10 detik dengan batas maksimal.
- Jalankan pytest di foreground: 'pytest -q -x --lf'. Full suite hanya sekali di akhir, dan hanya kalau diterima.
- Jangan restart suite setelah sudah hijau.
- Kalau satu langkah butuh > 2 menit, berhenti dan laporkan dulu.
- Jangan verifikasi ulang hal yang sudah terbukti.

## Wajib pakai Superpowers saat aku kirim output
- WAJIB jalankan skill dari Superpowers sebelum menyentuh kode.
- Cari akar masalah dulu, jangan langsung patch gejala.
- Baru setelah akar ketemu, fix + satu test yang membuktikan, lalu lapor.