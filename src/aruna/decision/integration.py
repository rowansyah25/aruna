"""Apa yang wajib dibaca dari Phase 11, 12, dan 13 (PASAL 14.39-14.41).

PASAL 14.13 (Phase 11), PASAL 14.14 (Phase 12), dan PASAL 14.15 (Phase 13)
menjelaskan *cara* memakai ketiga fase itu; PASAL 14.39-14.41 mendaftar
*apa*-nya. Modul ini memuat daftarnya - dan `_kelengkapan_fase` di
`futures/service.py` yang mengukur berapa banyak dari daftar itu yang
benar-benar sampai ke keputusan. Modul ini memuat daftarnya dan **tidak menghitung apa
pun** - angka-angkanya lahir di fasenya masing-masing, dan menghitung ulang di
sini akan menghasilkan dua sumber yang bisa berselisih.

Gunanya satu: membuat "lapisan ini tidak terbaca" menjadi angka. Sebuah fase
yang tidak pernah sampai ke keputusan tidak meninggalkan jejak apa pun kalau
tidak ada yang mendaftarnya, dan yang tidak terdaftar tidak pernah ditanyakan.
Ini keluarga cacat yang paling sering muncul di sistem ini - kode yang ditulis,
diekspor, diuji, dan tidak pernah dilewati jalur hidup.

**Laporannya dipecah per fase, bukan cuma digabung.** Kelengkapan 70% bisa
berarti Phase 13 hilang seluruhnya atau tiga baris tersebar di tiga fase; itu
dua masalah dengan dua perbaikan yang sangat berbeda, dan angka gabungan
membacanya sama.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class Fase(StrEnum):
    SEBELAS = "PHASE 11"
    DUA_BELAS = "PHASE 12"
    TIGA_BELAS = "PHASE 13"


class Masukan(StrEnum):
    """Nilainya data - jangan diterjemahkan.

    Satu anggota per baris di PASAL 14.39, 14.40, dan 14.41, dieja apa adanya.
    Menambah anggota di sini tanpa menambahnya ke :data:`WAJIB` akan tertangkap
    testnya: anggota yang tidak masuk daftar mana pun tidak pernah diperiksa.
    """

    # PASAL 14.39 - Phase 11
    SIGNAL_QUALITY = "SIGNAL_QUALITY"
    AGENT_RELIABILITY = "AGENT_RELIABILITY"
    MARKET_REGIME = "MARKET_REGIME"
    DATA_FRESHNESS = "DATA_FRESHNESS"
    ANOMALY_DETECTION = "ANOMALY_DETECTION"
    CONFIDENCE_CALIBRATION = "CONFIDENCE_CALIBRATION"
    AGENT_ACCOUNTABILITY = "AGENT_ACCOUNTABILITY"

    # PASAL 14.40 - Phase 12
    PATTERN_DISCOVERY = "PATTERN_DISCOVERY"
    STRATEGY_PERFORMANCE = "STRATEGY_PERFORMANCE"
    AGENT_SPECIALIZATION = "AGENT_SPECIALIZATION"
    CHAMPION = "CHAMPION"
    CHALLENGER = "CHALLENGER"
    WALK_FORWARD = "WALK_FORWARD"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    DRIFT_DETECTION = "DRIFT_DETECTION"
    LEARNING_RESULTS = "LEARNING_RESULTS"

    # PASAL 14.41 - Phase 13
    RISK_SCORE = "RISK_SCORE"
    RISK_REWARD = "RISK_REWARD"
    SL_QUALITY = "SL_QUALITY"
    TP_QUALITY = "TP_QUALITY"
    LEVERAGE_ANALYSIS = "LEVERAGE_ANALYSIS"
    LIQUIDATION_RISK = "LIQUIDATION_RISK"
    CORRELATION_RISK = "CORRELATION_RISK"
    EXPOSURE = "EXPOSURE"
    VOLATILITY = "VOLATILITY"
    NEWS_RISK = "NEWS_RISK"
    DAILY_RISK_BUDGET = "DAILY_RISK_BUDGET"


WAJIB: dict[Fase, tuple[Masukan, ...]] = {
    Fase.SEBELAS: (
        Masukan.SIGNAL_QUALITY,
        Masukan.AGENT_RELIABILITY,
        Masukan.MARKET_REGIME,
        Masukan.DATA_FRESHNESS,
        Masukan.ANOMALY_DETECTION,
        Masukan.CONFIDENCE_CALIBRATION,
        Masukan.AGENT_ACCOUNTABILITY,
    ),
    Fase.DUA_BELAS: (
        Masukan.PATTERN_DISCOVERY,
        Masukan.STRATEGY_PERFORMANCE,
        Masukan.AGENT_SPECIALIZATION,
        Masukan.CHAMPION,
        Masukan.CHALLENGER,
        Masukan.WALK_FORWARD,
        Masukan.OUT_OF_SAMPLE,
        Masukan.DRIFT_DETECTION,
        Masukan.LEARNING_RESULTS,
    ),
    Fase.TIGA_BELAS: (
        Masukan.RISK_SCORE,
        Masukan.RISK_REWARD,
        Masukan.SL_QUALITY,
        Masukan.TP_QUALITY,
        Masukan.LEVERAGE_ANALYSIS,
        Masukan.LIQUIDATION_RISK,
        Masukan.CORRELATION_RISK,
        Masukan.EXPOSURE,
        Masukan.VOLATILITY,
        Masukan.NEWS_RISK,
        Masukan.DAILY_RISK_BUDGET,
    ),
}

#: Urutan tetap untuk laporan - supaya dua tick bisa dibandingkan.
_URUT: tuple[Masukan, ...] = tuple(m for fase in Fase for m in WAJIB[fase])

_FASE_DARI: dict[Masukan, Fase] = {
    m: fase for fase, daftar in WAJIB.items() for m in daftar
}


def _pct(hadir: int, total: int) -> int:
    return round(hadir * 100 / total) if total else 0


@dataclass(frozen=True, slots=True)
class Kelengkapan:
    hadir: tuple[Masukan, ...]
    hilang: tuple[Masukan, ...]
    #: Masukan yang keputusan ini memang belum sampai ke sana.
    #:
    #: Rencana WAIT tidak punya entry, leverage, atau harga likuidasi - dan itu
    #: benar, bukan lapisan yang putus. Terukur pada 2026-08-20: laporan yang
    #: sama menyebut Phase 13 **27%** pada rencana WAIT dan **73%** pada rencana
    #: PLAN, dan selisihnya bukan perbedaan perakitan.
    tak_berlaku: tuple[Masukan, ...] = ()

    @property
    def pct(self) -> int:
        return _pct(len(self.hadir), len(self.hadir) + len(self.hilang))

    @property
    def per_fase(self) -> dict[Fase, int | None]:
        """Kelengkapan tiap fase sendiri-sendiri.

        Inilah bentuk yang menjawab "fase mana yang tidak sampai". Angka
        gabungan hanya memberitahu bahwa ada yang hilang.

        ``None`` untuk fase yang seluruh masukannya tidak berlaku: nol menuduh
        perakitan yang tidak pernah diuji, dan seratus memuji kelengkapan yang
        tidak pernah diperiksa.
        """
        punya = set(self.hadir)
        lewat = set(self.tak_berlaku)
        keluar: dict[Fase, int | None] = {}
        for fase, daftar in WAJIB.items():
            dinilai = [m for m in daftar if m not in lewat]
            keluar[fase] = (
                _pct(sum(m in punya for m in dinilai), len(dinilai))
                if dinilai
                else None
            )
        return keluar


def periksa(
    tersedia: Mapping[Masukan, bool],
    *,
    tak_berlaku: Iterable[Masukan] = (),
) -> Kelengkapan:
    """Mana yang benar-benar sampai ke keputusan, dan mana yang tidak.

    Yang tidak dilaporkan dihitung **hilang**, bukan hadir: pemanggil yang
    paling sedikit melapor justru yang paling perlu terlihat. Bawaan yang
    ramah di sini akan membuat pemanggil yang belum melaporkan apa-apa tercatat
    lengkap sempurna.

    ``tak_berlaku`` menyebut masukan yang keputusan ini memang belum sampai ke
    sana - bukan yang gagal sampai. Keduanya keluar dari penyebut, karena
    menghitungnya sebagai kegagalan akan membuat seseorang menyambungkan
    lapisan yang sudah tersambung.

    **Yang ternyata ADA tidak pernah dianggap tidak berlaku.** Sebuah masukan
    yang benar-benar terbaca jelas berlaku, apa pun klaim pemanggilnya -
    mengeluarkannya dari hitungan akan menyembunyikan lapisan yang bekerja.
    """
    lewat = {m for m in tak_berlaku if not tersedia.get(m, False)}
    hadir = tuple(m for m in _URUT if tersedia.get(m, False))
    hilang = tuple(
        m for m in _URUT if not tersedia.get(m, False) and m not in lewat
    )
    return Kelengkapan(
        hadir=hadir,
        hilang=hilang,
        tak_berlaku=tuple(m for m in _URUT if m in lewat),
    )


def fase_dari(masukan: Masukan) -> Fase:
    return _FASE_DARI[masukan]


__all__ = ["WAJIB", "Fase", "Kelengkapan", "Masukan", "fase_dari", "periksa"]
