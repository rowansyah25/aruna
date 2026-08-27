"""Menilai sinyal XAU yang horizonnya sudah lewat, pada dua sumbu terpisah.

**Dua sumbu, dan itu bukan kemewahan.**  ``migrations/0044_futures_arah.sql``
ditulis setelah 218 hasil futures diukur dan 201 di antaranya - 92,2% -
mendarat di satu ember yang secara eksplisit menyatakan dirinya tidak menjawab
apakah arahnya benar.  Jalur futures karena itu tidak punya akurasi arah sama
sekali selama berbulan-bulan.  Bedanya menentukan apa yang harus diperbaiki:

    arah benar + stop kena   -> stop-nya terlalu ketat untuk jalur ini
    arah salah + target kena -> beruntung, dan bukan bukti apa pun
    arah salah + stop kena   -> agennya yang salah baca

**Stop menang saat keduanya tersentuh di bar yang sama.**  Sebuah bar M5 punya
``high`` dan ``low`` tapi tidak menyimpan urutan di dalamnya.  Menganggap
target duluan berarti mengarang keberuntungan yang tidak ada buktinya;
menganggap stop duluan hanya membuat angkanya pesimis.  Angka pesimis yang
salah lebih aman daripada angka optimis yang salah - yang kedua membuat
strategi terlihat layak dipakai.

**``arah_benar`` diukur pada TUTUP HORIZON**, bukan pada level yang tersentuh.
Itu yang membuatnya pertanyaan ramalan dan bukan pertanyaan eksekusi: sebuah
sinyal yang kena stop lalu berbalik dan tutup jauh di arah yang benar adalah
ramalan yang benar dengan stop yang terlalu ketat, dan keduanya perlu terlihat
terpisah.

**Jalur yang belum cukup panjang menghasilkan ``None``, bukan
``TIDAK_SATU_PUN``.**  Yang kedua adalah hasil; yang pertama adalah ketiadaan
hasil.  Menyamakannya akan menghitung tiap sinyal yang masih berjalan sebagai
sinyal yang gagal mencapai apa pun.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from aruna.core.enums import Decision
from aruna.data.models import Candle
from aruna.xau.geometri import Geometri

#: Bar M5 yang diberikan pada sebuah sinyal untuk mencapai targetnya.
#:
#: 48 bar = 4 jam, sengaja disamakan dengan H4 - timeframe yang spec sebut
#: sebagai konteks besar.  Angka ini DISIMPAN bersama tiap hasil
#: (``xau_results.horizon_bar``) supaya bisa dinilai ulang kalau kelak terbukti
#: salah; sebuah horizon yang hanya hidup di kode membuat hasil lama dan baru
#: tidak bisa dibandingkan setelah diubah.
HORIZON_BAR = 48


class LevelTersentuh(StrEnum):
    TARGET = "TARGET"
    STOP = "STOP"
    TIDAK_SATU_PUN = "TIDAK_SATU_PUN"


@dataclass(frozen=True, slots=True)
class HasilXau:
    """Hasil satu sinyal, pada dua sumbu yang tidak digabung."""

    prediction_id: int
    #: Sumbu RAMALAN.  ``None`` = tidak terukur, bukan salah.
    arah_benar: bool | None
    #: Sumbu EKSEKUSI.
    level_tersentuh: LevelTersentuh
    harga_tutup: Decimal
    gerak_pct: Decimal
    bar_dipakai: int
    horizon_bar: int = HORIZON_BAR


def _level_tersentuh(
    jalur: list[Candle], geo: Geometri, naik: bool
) -> LevelTersentuh:
    for bar in jalur:
        kena_stop = bar.low <= geo.stop if naik else bar.high >= geo.stop
        kena_target = bar.high >= geo.target if naik else bar.low <= geo.target
        # Stop diperiksa lebih dulu - lihat docstring modul.
        if kena_stop:
            return LevelTersentuh.STOP
        if kena_target:
            return LevelTersentuh.TARGET
    return LevelTersentuh.TIDAK_SATU_PUN


def nilai_hasil(
    prediction_id: int,
    geo: Geometri,
    arah: Decision,
    jalur: list[Candle],
    *,
    horizon_bar: int = HORIZON_BAR,
) -> HasilXau | None:
    """Nilai satu sinyal.  ``None`` kalau horizonnya belum tuntas.

    ``jalur`` adalah bar M5 SESUDAH bar keputusan, terlama dulu.
    """
    if not arah.is_directional:
        raise ValueError(
            f"hanya sinyal berarah yang punya hasil, bukan {arah.value}"
        )
    if len(jalur) < horizon_bar:
        return None

    dipakai = jalur[:horizon_bar]
    naik = arah is Decision.BUY
    penutup = dipakai[-1].close

    return HasilXau(
        prediction_id=prediction_id,
        # Diukur pada tutup horizon, bukan pada level yang tersentuh.
        arah_benar=(penutup > geo.entry) if naik else (penutup < geo.entry),
        level_tersentuh=_level_tersentuh(dipakai, geo, naik),
        harga_tutup=penutup,
        gerak_pct=(penutup - geo.entry) / geo.entry * 100,
        bar_dipakai=len(dipakai),
        horizon_bar=horizon_bar,
    )


__all__ = ["HORIZON_BAR", "HasilXau", "LevelTersentuh", "nilai_hasil"]
