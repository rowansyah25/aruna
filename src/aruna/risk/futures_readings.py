"""Terjemahkan satu rencana futures menjadi pembacaan faktor risiko.

Satu-satunya tempat yang tahu bagaimana ``FuturesPlan`` menyimpan hal-hal, dan
satu-satunya tempat yang menentukan arah tiap faktor. :mod:`aruna.risk.score`
sengaja tidak tahu apa-apa tentang keduanya - ia hanya menggabungkan angka
0-100 di mana tinggi berarti lebih berisiko.

Pemisahan itu bukan kerapian. Aturan penggabungan bisa diuji tanpa membangun
satu pun objek rencana, dan penerjemahan bisa berubah ketika bentuk rencananya
berubah tanpa menyentuh bobot yang sedang dikalibrasi.

**Yang tidak terukur dikembalikan ``None``, bukan ditebak.** PASAL 13.26
melarang mengarang, dan angka bawaan yang ramah adalah karangan yang paling
sulit terlihat: ia selalu masuk akal dan selalu menguntungkan setup-nya.
"""

from __future__ import annotations

from typing import Any


def _pct(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _skala(nilai: float | None, *, aman: float, bahaya: float) -> float | None:
    """Petakan sebuah ukuran ke 0-100 di mana tinggi berarti lebih berisiko.

    ``aman`` adalah nilai yang memberi 0 dan ``bahaya`` memberi 100; keduanya
    boleh terbalik urutannya, dan itulah cara arah faktor dinyatakan - sekali,
    di tempat pemanggilan, alih-alih disimpulkan dari nama kolom.
    """
    if nilai is None:
        return None
    if aman == bahaya:
        return None
    porsi = (nilai - aman) / (bahaya - aman)
    return max(0.0, min(100.0, porsi * 100.0))


def readings_from_plan(plan: Any) -> dict[str, float | None]:
    """Pembacaan faktor untuk :func:`aruna.risk.score.assess`.

    Faktor yang belum punya sumber pada lapisan futures sengaja TIDAK
    disebutkan di sini sama sekali - ketiadaannya kemudian dilaporkan sebagai
    "tidak terukur", yang benar, alih-alih sebagai nilai netral yang bukan
    hasil pengukuran apa pun.
    """
    bacaan: dict[str, float | None] = {}

    # Jarak likuidasi. `BufferScore.score` sudah 0-100 di mana TINGGI berarti
    # AMAN - jadi ia dibalik. Membalik di sini dan bukan di sumbernya menjaga
    # arti aslinya tetap utuh untuk pembaca lain.
    buf = getattr(plan, "buffer", None)
    if buf is not None and getattr(buf, "score", None) is not None:
        bacaan["liquidation_distance"] = max(0.0, 100.0 - float(buf.score))

    # Stop yang hanya lahir dari volatilitas - tanpa struktur pasar -
    # persis yang PASAL 13.4 larang: "jangan menentukan SL hanya berdasarkan
    # persentase tetap tanpa melihat market structure".
    stop = getattr(plan, "stop_detail", None)
    if stop is not None:
        dari_volatilitas = bool(getattr(stop, "from_volatility_only", False))
        bacaan["stop_quality"] = 75.0 if dari_volatilitas else 25.0

    # Risk/reward BERSIH - sesudah fee, funding dan slippage. Kotor akan
    # membuat setiap setup terlihat lebih baik daripada yang bisa dijalankan.
    rr = _pct(getattr(plan, "net_rr", None))
    if rr is not None:
        bacaan["risk_reward"] = _skala(rr, aman=3.0, bahaya=1.0)

    lev = getattr(plan, "leverage", None)
    if lev is not None:
        bacaan["leverage"] = _skala(float(lev), aman=1.0, bahaya=20.0)

    liq = getattr(plan, "liquidity", None)
    if liq is not None:
        spread = _pct(getattr(liq, "spread_bps", None))
        if spread is not None:
            bacaan["spread"] = _skala(spread, aman=1.0, bahaya=50.0)
        sweep = _pct(getattr(liq, "sweep_cost_pct", None))
        if sweep is not None:
            bacaan["liquidity"] = _skala(sweep, aman=0.05, bahaya=1.0)
        elif getattr(liq, "tradeable", None) is False:
            # Tidak bisa diperdagangkan adalah pengukuran, bukan ketiadaan.
            bacaan["liquidity"] = 100.0

    slip = getattr(plan, "slippage", None)
    if slip is not None and getattr(slip, "breaks_at", None) is not None:
        # Ada ukuran di mana rencananya patah karena slippage. Semakin dekat
        # ke ukuran yang direncanakan, semakin berisiko - tapi perbandingan
        # itu butuh keduanya, dan hanya salah satu yang selalu ada.
        bacaan["slippage"] = 60.0
    elif slip is not None:
        bacaan["slippage"] = 20.0

    fund = getattr(plan, "funding", None)
    if fund is not None:
        biaya = _pct(getattr(fund, "projected_cost_pct", None))
        if biaya is not None:
            bacaan["funding"] = _skala(abs(biaya), aman=0.01, bahaya=0.5)

    # Mutu data: gerbang integritas SPEC 46 sudah memutuskannya, dan
    # keputusannya diterjemahkan - bukan dihitung ulang dengan aturan kedua
    # yang bisa berbeda pendapat.
    integ = getattr(plan, "integrity", None)
    vonis = getattr(getattr(integ, "verdict", None), "value", None)
    if vonis is not None:
        bacaan["data_quality"] = {"OK": 10.0, "DEGRADED": 60.0}.get(
            str(vonis).upper(), 90.0
        )

    return bacaan


__all__ = ["readings_from_plan"]
