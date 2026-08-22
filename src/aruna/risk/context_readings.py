"""Pembacaan faktor risiko dari konteks keputusan (PASAL 13.2, 13.31).

Pelengkap :mod:`aruna.risk.futures_readings`, dan pembagiannya bukan
kebetulan: keduanya mengukur faktor yang **berbeda**.

Rencana futures tahu tentang uang - likuidasi, leverage, slippage, funding,
spread. Konteks council tahu tentang pasar - volatilitas, rezim, berita,
korelasi, mutu data. Delapan faktor yang tidak pernah bisa diukur dari sisi
futures hampir seluruhnya ada di sini.

Digabungkan, cakupannya naik dari 62% - yang tepat di ambang dan membuat
gerbang risiko sering menjawab "tidak bisa dinilai" - ke wilayah di mana skor
itu benar-benar menilai sesuatu.

**Arah tiap faktor ditulis di sini, sekali.** Dan yang tidak terukur
dikembalikan tanpa kunci sama sekali, bukan dengan angka netral: PASAL 13.26
melarang mengarang, dan nilai bawaan yang ramah adalah karangan yang paling
sulit terlihat.
"""

from __future__ import annotations

from typing import Any

from aruna.risk.futures_readings import _skala

#: Rezim yang mendukung sebuah arah, dan yang melawannya. Rezim yang tidak
#: terbaca - UNCERTAIN - bukan netral: ia berarti indikator-indikatornya saling
#: bertentangan, dan bertaruh di atas pertentangan itu lebih berisiko daripada
#: bertaruh di atas kesepakatan.
_REGIME_RISIKO: dict[str, float] = {
    "TRENDING": 25.0,
    "BREAKOUT": 40.0,
    "RANGING": 45.0,
    "LOW_VOLATILITY": 40.0,
    "REVERSAL": 65.0,
    "UNCERTAIN": 75.0,
    "ANOMALY": 90.0,
}

#: Mutu data yang dilaporkan snapshot pasar, diterjemahkan.
_MUTU_RISIKO: dict[str, float] = {
    "OK": 10.0,
    "GOOD": 10.0,
    "DEGRADED": 55.0,
    "STALE": 75.0,
    "MISSING": 90.0,
    "INVALID": 95.0,
}


def _aman(ambil: Any, *args: Any) -> Any:
    """Panggil sesuatu pada objek asing, kembalikan None kalau ia meledak.

    **Tangkapannya lebar dengan sengaja.** Modul ini membaca bentuk objek yang
    dimiliki lapisan lain - konteks council, snapshot pasar, matriks korelasi -
    dan bentuk itu bisa berubah tanpa modul ini tahu. Mempersempitnya ke daftar
    pengecualian tertentu berarti menebak cara apa saja sebuah objek asing bisa
    gagal, dan tebakan itu meleset: versi pertama menyaring
    ``(AttributeError, KeyError, TypeError)`` dan tetap jatuh pada konteks yang
    melempar ``RuntimeError`` - ditemukan test, bukan operator.

    Yang hilang saat ia meledak adalah satu faktor risiko, dan faktor yang
    hilang dilaporkan "tidak terukur" - yang benar. Yang tidak boleh hilang
    adalah seluruh penilaiannya.
    """
    try:
        return ambil(*args)
    except Exception:  # noqa: BLE001 - lihat docstring
        return None


def _nilai(context: Any, nama: str) -> float | None:
    """Satu bacaan indikator, atau None kalau ia tidak dihitung."""
    v = _aman(lambda: context.value(nama))
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def readings_from_context(
    context: Any, *, signal_quality: float | None = None
) -> dict[str, float | None]:
    """Pembacaan faktor dari :class:`~aruna.agents.context.DecisionContext`.

    ``signal_quality`` dioper terpisah karena ia dihitung di lapisan sinyal,
    bukan disimpan pada konteks - dan mengambilnya dari tempat yang salah akan
    menghasilkan kunci yang selalu hilang tanpa ada yang menyadarinya.

    Skalanya 0-100 di mana **tinggi berarti lebih berisiko**, sama seperti
    adapter futures, supaya keduanya bisa digabung tanpa menerjemahkan dua
    kali.
    """
    bacaan: dict[str, float | None] = {}

    # Volatilitas terwujud, sebagai persen. Dipakai daripada ATR mentah karena
    # ATR berskala harga - 500 pada BTC dan 0,5 pada DOGE menggambarkan
    # volatilitas yang mirip, dan sebuah ambang tetap akan menyebut yang satu
    # ekstrem dan yang lain tenang.
    vol = _nilai(context, "realised_volatility")
    if vol is not None:
        bacaan["volatility"] = _skala(abs(vol), aman=0.5, bahaya=8.0)

    verdict = _aman(getattr, context, "regime", None)
    dalam = getattr(verdict, "regime", None)
    nama = getattr(dalam, "value", None) or getattr(verdict, "value", None)
    if isinstance(nama, str):
        dasar = _REGIME_RISIKO.get(nama.upper())
        if dasar is not None:
            # Keyakinan rezimnya ikut. Rezim TRENDING yang dibaca dengan
            # keyakinan 0,2 bukan pernyataan yang sama kuatnya dengan yang
            # dibaca 0,9, dan memperlakukannya sama membuang informasi yang
            # sudah dihitung.
            yakin = getattr(verdict, "confidence", None)
            if isinstance(yakin, (int, float)) and 0.0 <= yakin <= 1.0:
                # Keyakinan rendah menarik nilainya ke arah UNCERTAIN.
                dasar = dasar + (75.0 - dasar) * (1.0 - float(yakin))
            bacaan["market_regime"] = max(0.0, min(100.0, dasar))

    state = _aman(getattr, context, "state", None)
    mutu = getattr(state, "data_quality", None)
    if isinstance(mutu, str):
        nilai = _MUTU_RISIKO.get(mutu.upper())
        if nilai is not None:
            bacaan["data_quality"] = nilai

    # Berita: yang dihitung adalah keberadaan berita berdampak dalam jendela
    # terakhir, bukan sentimennya. PASAL 13.16 menyebut risiko VOLATILITAS -
    # dan berita bagus menggerakkan harga sama kerasnya dengan berita buruk.
    berita = _aman(getattr, context, "recent_news", None)
    if callable(berita):
        baru = _aman(berita, )
        if baru is not None:
            berdampak = sum(
                1 for n in baru if getattr(n, "high_impact", False)
            )
            bacaan["news_risk"] = _skala(float(berdampak), aman=0.0, bahaya=3.0)

    korelasi = _aman(getattr, context, "correlation", None)
    tertinggi = getattr(korelasi, "max_abs", None)
    if isinstance(tertinggi, (int, float)):
        bacaan["correlation"] = _skala(abs(float(tertinggi)), aman=0.2, bahaya=0.9)

    if signal_quality is not None:
        # Kualitas TINGGI berarti risiko RENDAH pada faktor ini - dan itu satu-
        # satunya tempat kualitas boleh menyentuh risiko. Ia satu dari tujuh
        # belas faktor dengan bobot 1.0, bukan pengurang yang bisa menutupi
        # sisanya; PASAL 13.21 melarang yang kedua.
        bacaan["signal_quality"] = _skala(
            float(signal_quality), aman=100.0, bahaya=0.0
        )

    return bacaan


def merge(*sumber: dict[str, float | None]) -> dict[str, float | None]:
    """Gabungkan beberapa sumber pembacaan.

    Yang lebih dulu menang, dan itu disengaja: pemanggil menaruh sumber yang
    paling dekat dengan keputusan di depan. Adapter futures mengukur mutu data
    dari gerbang integritas SPEC 46 yang sudah memutuskan; konteks mengukurnya
    dari snapshot. Keduanya sah, dan yang harus dipakai adalah yang diputuskan
    pemanggil - bukan yang kebetulan terakhir ditulis.
    """
    hasil: dict[str, float | None] = {}
    for s in sumber:
        for k, v in s.items():
            if v is not None and k not in hasil:
                hasil[k] = v
    return hasil


__all__ = ["merge", "readings_from_context"]
