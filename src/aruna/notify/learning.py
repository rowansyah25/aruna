"""Ringkasan pembelajaran untuk operator (PASAL 12.24, 12.25).

**Yang paling penting di modul ini adalah apa yang TIDAK dikirim.**

PASAL 12.25 menyebutnya tanpa ambiguitas: Telegram tidak menerima raw learning
log, tidak menerima tiap pola, tidak menerima tiap backtest, tidak menerima
tiap perhitungan agent, tidak menerima tiap perdebatan internal. Satu putaran
pembelajaran memeriksa ratusan irisan; mengirimkannya akan mengubur enam jenis
pesan yang benar-benar penting - signal, hasil, laporan harian, alert
kesehatan, proposal model, pemulihan - di bawah kebisingan yang tidak
ditindaklanjuti siapa pun.

Jadi yang keluar dari sini hanya satu blok pendek di dalam laporan harian yang
sudah ada, dan hanya proposal yang benar-benar menunggu keputusan operator yang
boleh menjadi pesan tersendiri.

**Sample size selalu ikut.** PASAL 12.3 memintanya pada setiap analisis, dan
laporan harian adalah tempat angka paling mungkin dibaca tanpa konteksnya.
"""

from __future__ import annotations

from typing import Any

#: Paling banyak sekian pola yang disebut di laporan harian.
#:
#: Lima. Bukan karena sisanya tidak menarik, tapi karena daftar sepanjang
#: layar dibaca sebagai satu blok yang dilewati - dan pola keenam sampai
#: keseratus tetap tersimpan di `discovered_patterns` untuk siapa pun yang
#: mencarinya.
MAX_PATTERNS = 5

#: Paling banyak sekian agent yang disebut. Alasannya sama.
MAX_AGENTS = 3


def render_learning(
    *,
    observations: int,
    baseline_label: str,
    patterns: list[Any] | None = None,
    specialists: dict[str, str] | None = None,
    strategies: list[Any] | None = None,
    drift: str | None = None,
    proposal: str | None = None,
) -> list[str]:
    """Blok LEARNING SUMMARY, sebagai baris-baris untuk laporan harian.

    Mengembalikan daftar baris dan bukan satu string supaya pemanggil bisa
    menyisipkannya ke laporan yang sudah ada tanpa menebak pemisahnya.

    Daftar kosong dikembalikan ketika belum ada apa pun yang dipelajari - blok
    berjudul "LEARNING SUMMARY" yang isinya kosong terbaca seperti kerusakan,
    sementara ketiadaannya terbaca seperti apa adanya.
    """
    if observations <= 0:
        return []

    baris: list[str] = ["", "🧠 LEARNING SUMMARY", ""]
    baris.append(f"Prediksi dipelajari: {observations}")
    baris.append(f"Rata-rata: {baseline_label}")

    pola = list(patterns or [])
    if pola:
        baris += ["", "Pola yang berbeda dari rata-rata:"]
        for p in pola[:MAX_PATTERNS]:
            baris.append(f"  {p}")
        sisa = len(pola) - MAX_PATTERNS
        if sisa > 0:
            # Disebut, bukan dibuang diam-diam. Batas yang tak terlihat
            # membuat sepuluh temuan terbaca sebagai lima.
            baris.append(f"  (+{sisa} lagi, tersimpan tapi tidak dicetak)")
    else:
        baris += [
            "",
            "Belum ada pola yang bedanya melampaui ketidakpastian sample.",
        ]

    strat = list(strategies or [])
    if strat:
        baris += ["", "Strategi:"]
        for s in strat[:MAX_PATTERNS]:
            baris.append(f"  {s}")

    ahli = specialists or {}
    if ahli:
        baris += ["", "Spesialisasi agent yang terbukti:"]
        for role, regime in list(ahli.items())[:MAX_AGENTS]:
            baris.append(f"  {role} lebih kuat di {regime}")
        sisa = len(ahli) - MAX_AGENTS
        if sisa > 0:
            baris.append(f"  (+{sisa} lagi)")
    else:
        baris += ["", "Belum ada spesialisasi agent yang terbukti."]

    baris += ["", f"Performance drift: {drift or 'TIDAK TERDETEKSI'}"]
    baris.append(f"Proposal model: {proposal or 'TIDAK ADA'}")

    if proposal:
        # Satu-satunya kalimat di blok ini yang meminta operator berbuat
        # sesuatu. Ditaruh terakhir supaya ia yang terbaca terakhir.
        baris += ["", "Proposal menunggu keputusan Anda: APPROVE atau REJECT."]

    baris += [
        "",
        "ARUNA tidak mengubah modelnya sendiri. Setiap perubahan penting",
        "menunggu persetujuan Anda (PASAL 12.26).",
    ]
    return baris


#: Jenis pesan yang boleh masuk Telegram (PASAL 12.25).
#:
#: Ditulis sebagai data dan bukan sebagai prosa di docstring karena ada test
#: yang membacanya: sebuah jenis pesan baru yang ditambahkan tanpa masuk daftar
#: ini akan tertangkap di sana, bukan ditemukan operator sebagai banjir.
TELEGRAM_ALLOWED = (
    "SIGNAL",
    "RESULT",
    "DAILY_REPORT",
    "HEALTH_ALERT",
    "MODEL_PROPOSAL",
    "RECOVERY",
)

#: Yang dilarang, dinyatakan eksplisit. PASAL 12.25 menyebut kelimanya.
TELEGRAM_FORBIDDEN = (
    "RAW_LEARNING_LOG",
    "EVERY_PATTERN",
    "EVERY_BACKTEST",
    "EVERY_AGENT_CALCULATION",
    "EVERY_POLL",
    "EVERY_INTERNAL_DEBATE",
)


def telegram_allows(kind: str) -> bool:
    """Boleh dikirim ke Telegram?

    Daftar putih, bukan daftar hitam. Daftar hitam melewatkan setiap jenis
    pesan baru secara bawaan - dan jenis pesan baru justru yang paling mungkin
    membanjiri, karena tidak ada yang memikirkan volumenya saat menambahkannya.
    """
    return kind.upper() in TELEGRAM_ALLOWED


__all__ = [
    "MAX_AGENTS",
    "MAX_PATTERNS",
    "TELEGRAM_ALLOWED",
    "TELEGRAM_FORBIDDEN",
    "render_learning",
    "telegram_allows",
]
