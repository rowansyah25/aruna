"""Menjawab satu pertanyaan saat keputusan dibuat (PASAL 12.6).

    "Untuk rezim ini, aset ini, timeframe ini - apa yang sejarah katakan?"

Dan hampir selalu, hari ini, jawabannya adalah "belum cukup untuk mengatakan
apa pun". Itu bukan kegagalan modul ini; itu keadaan datanya, dinyatakan
dengan jujur alih-alih ditutupi dengan tebakan yang terdengar percaya diri.

**Kenapa modul ini terpisah dari :mod:`aruna.learning.selection`.** Yang di
sana adalah aturan - tujuh pertimbangan dan kapan harus diam - dan ia murni:
tidak menyentuh database, bisa diuji sepenuhnya dengan angka di tangan. Yang
di sini adalah pengambilan datanya. Menggabungkan keduanya berarti setiap uji
tentang "kapan boleh memilih" menuntut MySQL, dan uji yang mahal adalah uji
yang lama-lama tidak dijalankan.

**Cache, dan kenapa boleh.** Performa strategi dihitung dari sejarah yang
sudah selesai; ia tidak berubah antar tick. Membacanya ulang untuk setiap
simbol di setiap tick adalah puluhan query per menit untuk jawaban yang sama.
Yang di-cache di sini BUKAN data pasar - PASAL 4 melarang menyajikan data lama
sebagai realtime, dan tidak satu pun angka di sini adalah harga.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any

from aruna.core.logging import get_logger
from aruna.learning.evidence import Evidence
from aruna.learning.selection import Candidate, Refusal, Selection, select
from aruna.learning.strategies import by_code

log = get_logger("aruna.learning.strategist")

#: Umur maksimum performa strategi yang di-cache, dalam detik.
#:
#: Lima menit. Sejarah bertambah pelan - satu prediksi terskor tiap beberapa
#: menit - jadi jawabannya tidak akan berubah lebih cepat dari itu, dan sebuah
#: strategi yang statusnya baru diubah operator harus terlihat tanpa menunggu
#: restart.
CACHE_TTL_SEC = 300.0


@dataclass(slots=True)
class Strategist:
    """Membaca performa strategi dan menjawab pertanyaan council."""

    store: Any
    ttl_sec: float = CACHE_TTL_SEC
    _cache: tuple[float, list[dict[str, Any]], float | None] | None = None

    async def _performance(self) -> tuple[list[dict[str, Any]], float | None]:
        sekarang = monotonic()
        if self._cache is not None and sekarang - self._cache[0] < self.ttl_sec:
            return self._cache[1], self._cache[2]

        baris = await self.store.strategy_slices()
        baseline = await self.store.overall_win_rate()
        self._cache = (sekarang, baris, baseline)
        return baris, baseline

    async def suggest(
        self,
        *,
        market: Any,
        symbol: str,
        interval: Any,
        regime: Any,
    ) -> Selection:
        """Strategi yang sejarah sarankan, atau alasan kenapa tidak ada.

        Selalu mengembalikan :class:`Selection`, tidak pernah ``None``. Sebuah
        abstain yang dikembalikan sebagai None tidak bisa dibedakan dari
        "pemilihnya tidak dirangkai", dan pembedaan itu yang menentukan apakah
        diamnya berarti "belum tahu" atau "tidak ditanya".
        """
        nama_regime = regime_name(regime)
        if nama_regime is None:
            return Selection(refusal=Refusal.REGIME_UNKNOWN)

        try:
            baris, baseline = await self._performance()
        except Exception:
            log.exception("strategist.performance_unavailable", symbol=symbol)
            return Selection(refusal=Refusal.INSUFFICIENT_SAMPLE)

        kandidat = [
            Candidate(
                code=str(r["strategy_code"]),
                evidence=Evidence(
                    wins=int(r.get("wins") or 0),
                    losses=int(r.get("losses") or 0),
                ),
                # Tiga angka di bawah belum diukur per strategi, dan
                # dibiarkan kosong dengan sengaja. `select` memperlakukan
                # kosong sebagai "tidak lolos", bukan sebagai "aman" - jadi
                # selama ketiganya belum ada, tidak ada strategi yang akan
                # pernah terpilih. Itu perilaku yang benar: memilih dari angka
                # yang tidak ada adalah memilih dari karangan.
                per_period=(),
                net_pnl=r.get("net_pnl") or 0,
                max_drawdown=r.get("max_drawdown") or 0,
                calibration_error=None,
                out_of_sample=None,
                regimes=_regimes_of(str(r["strategy_code"])),
            )
            for r in baris
            if r.get("strategy_code")
        ]

        pilihan = select(kandidat, regime=nama_regime, baseline=baseline)
        if not pilihan.abstained:
            log.info(
                "strategist.selected",
                symbol=symbol,
                regime=nama_regime,
                strategy=pilihan.strategy,
                sample=pilihan.evidence.total if pilihan.evidence else 0,
            )
        return pilihan


def regime_name(regime: Any) -> str | None:
    """Nama rezim sebagai string, dari bentuk apa pun yang dioper pemanggil.

    **Ada karena bentuk pertamanya diam-diam salah, dan probe yang
    menemukannya.** ``DecisionContext.regime`` mengembalikan ``RegimeVerdict``
    - sebuah objek berisi rezim, keyakinannya, dan alasannya - bukan enum
    ``Regime``. Versi pertama fungsi ini memanggil ``getattr(regime, "value")``
    lalu jatuh ke ``str(regime)``, dan pada RegimeVerdict itu menghasilkan
    seluruh reprnya:

        "RegimeVerdict(regime=<Regime.BREAKOUT: 'BREAKOUT'>, confidence=..."

    String itu tidak pernah cocok dengan satu pun rezim di katalog, jadi
    pemilihnya menjawab "tidak ada strategi yang cocok" untuk SETIAP aset,
    selamanya, tanpa satu pun error. Kegagalan yang paling mahal bukan yang
    berisik - ia yang terlihat seperti jawaban.

    Tiga bentuk diterima dengan sengaja: verdict, enum, dan string. Pemanggil
    di jalur berbeda memegang bentuk berbeda, dan memaksa mereka menyeragamkan
    di tempat panggilan berarti bug yang sama menunggu di tiap tempat panggil
    yang baru.
    """
    if regime is None:
        return None
    dalam = getattr(regime, "regime", None)
    if dalam is not None:
        regime = dalam
    nilai = getattr(regime, "value", None)
    if isinstance(nilai, str):
        return nilai
    return str(regime) if isinstance(regime, str) else None


def _regimes_of(code: str) -> tuple[str, ...]:
    s = by_code(code)
    return tuple(s.preferred_regimes) if s else ()


__all__ = ["CACHE_TTL_SEC", "Strategist", "regime_name"]
