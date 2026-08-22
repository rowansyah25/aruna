"""Menjembatani jalur keputusan dengan ingatan yang benar-benar ada.

Dua ketidakcocokan terukur pada 2026-08-21, dan keduanya akan membuat ingatan
diam selamanya kalau tidak dijembatani di satu tempat:

**Ejaan simbol.** Ingatan lahir dari ``signal_snapshots`` yang mengeja
``BTC/USDT``; jalur futures merencanakan ``BTCUSDT``. Keduanya menyebut aset
yang sama - jebakan yang persis sama sudah pernah membuat pencarian catatan
council tidak pernah cocok, dan sudah dieja di ``debate.note_of``. Nol ingatan
bersimbol ``BTCUSDT``.

**Timeframe.** Ingatan ada di 15m (5.377), 1h (2.189), dan 1d (800). Keputusan
futures dibuat di **4h**, dan ingatan pada 4h berjumlah **nol**.

Keputusan operator 2026-08-21: pakai tetangga terdekat yang punya data, dan
**sebutkan** - PASAL 15.14 menuntut timeframe yang relevan diprioritaskan,
bukan melarang tetangganya. Yang dilarang adalah menyamar: sebuah konteks 1h
yang dicetak seolah-olah 4h membuat operator menimbang bukti yang bukan
miliknya.

Begitu ingatan 4h melewati ambang kecukupan, :func:`horizon_ingatan` memilihnya
sendiri - tidak ada yang perlu diubah.
"""

from __future__ import annotations

#: Urutan tetangga per horizon keputusan. Yang pertama selalu horizon itu
#: sendiri: ingatan dari timeframe yang sama selalu menang kalau ada.
TETANGGA: dict[str, tuple[str, ...]] = {
    "4h": ("4h", "1h", "1d"),
    "1h": ("1h", "15m", "4h"),
    "15m": ("15m", "1h"),
    "1d": ("1d", "4h", "1h"),
}


def simbol_pasar(symbol: object) -> str:
    """Ejaan simbol yang dipakai ingatan.

    ``BTCUSDT`` -> ``BTC/USDT``. Yang sudah bergaris miring dibiarkan, dan yang
    tidak berakhiran ``USDT`` dipulangkan apa adanya - §33 menyatakan CRYPTO
    hanya pasangan USDT, jadi apa pun di luar itu bukan simbol perpetual yang
    perlu dijembatani.
    """
    teks = str(symbol or "").strip().upper()
    if not teks or "/" in teks:
        return teks
    if teks.endswith("USDT") and len(teks) > 4:
        return f"{teks[:-4]}/USDT"
    return teks


def horizon_ingatan(
    horizon: object, *, tersedia: dict[str, int], minimum: int
) -> tuple[str | None, bool]:
    """Timeframe ingatan mana yang dipakai untuk keputusan di ``horizon``.

    ``tersedia`` adalah jumlah ingatan per timeframe - dihitung pemanggil,
    karena hanya dia yang boleh menyentuh database.

    Memulangkan ``(timeframe, dipinjam)``. ``dipinjam`` benar ketika yang
    dipakai bukan horizon keputusannya sendiri, dan itulah yang **wajib**
    disebut di pesan: konteks 1h yang dicetak seolah-olah 4h membuat operator
    menimbang bukti yang bukan miliknya.

    ``(None, False)`` berarti tidak ada satu pun tetangga yang punya cukup
    ingatan - dan itu jawaban yang sah (PASAL 15.37), bukan kegagalan.
    """
    nama = str(getattr(horizon, "value", horizon) or "").strip()
    for kandidat in TETANGGA.get(nama, (nama,)):
        if tersedia.get(kandidat, 0) >= minimum:
            return kandidat, kandidat != nama
    return None, False


__all__ = ["TETANGGA", "horizon_ingatan", "simbol_pasar"]
