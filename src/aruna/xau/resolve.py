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


#: Untung minimum yang dihitung sebagai kemenangan, dalam satuan R.
#:
#: **R = jarak stop.**  Satu R adalah persis yang dipertaruhkan kalau bacaannya
#: salah, jadi mengukur untung terhadapnya membandingkan hasil dengan risiko
#: yang benar-benar diambil - bukan dengan nol.
#:
#: Setengah R, dan ambang ini yang menjaga angka win rate tetap berarti.
#: Tanpanya, "tutup saat masih untung" akan menghitung untung sepeser pun
#: sebagai kemenangan: harga yang bergerak +0,01% adalah derau satu bar, dan
#: menghitungnya menang membuat win rate naik tanpa satu keputusan pun
#: membaik.  Itu persis yang spec larang sebagai mengubah histori.
MIN_R_UNTUK_WIN = Decimal("0.5")


class LevelTersentuh(StrEnum):
    TARGET = "TARGET"
    STOP = "STOP"
    TIDAK_SATU_PUN = "TIDAK_SATU_PUN"


class HasilAkhir(StrEnum):
    """Apa yang SEBENARNYA terjadi pada gagasan ini, termasuk saran ARUNA.

    Dimensi ketiga, berdampingan dengan ``arah_benar`` (ramalan) dan
    ``level_tersentuh`` (eksekusi).  Ia menjawab pertanyaan yang tidak dijawab
    keduanya: apa yang operator dapat kalau ia mengikuti ARUNA.
    """

    TARGET = "TARGET"
    STOP = "STOP"
    #: Horizon habis, ARUNA menyuruh tutup, dan saat itu untung >= ambang.
    TUTUP_UNTUNG = "TUTUP_UNTUNG"
    #: Horizon habis, ARUNA menyuruh tutup, tapi untungnya di bawah ambang
    #: atau justru rugi.  BUKAN kemenangan.
    TUTUP_RUGI = "TUTUP_RUGI"
    #: ARUNA menyuruh menahan: belum ada yang bisa dihitung menang atau kalah.
    TAHAN = "TAHAN"


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


def _harga_keluar(
    tersentuh: LevelTersentuh, geo: Geometri, penutup: Decimal
) -> Decimal:
    """Harga tempat posisi SEBENARNYA ditutup.

    **Level yang tersentuh adalah tempat keluar, bukan close bar.**  Kalau
    stop atau target Anda terpasang sebagai order, ia terisi saat harga
    menyentuhnya - bukan pada harga penutupan bar itu, yang bisa jauh
    berbeda dan bukan angka yang pernah Anda transaksikan.

    Ditemukan dari pertanyaan operator 2026-08-28 atas sebuah kemenangan yang
    terlihat janggal: pesannya melaporkan ``tutup 4575,09`` padahal targetnya
    4574,87 - close bar itu di ATAS target, dan terbaca seperti target yang
    tak tercapai.  Cacat yang sama membuat tiga kekalahan sebelumnya tercatat
    -1,65 / -1,58 / -1,82 R, padahal stop-out menurut definisinya adalah
    -1,00 R.  Kerugiannya dilebih-lebihkan oleh angka yang tak pernah
    diperdagangkan siapa pun.

    Slippage TIDAK dimodelkan, dan itu dinyatakan alih-alih disembunyikan:
    venue ini tidak menerbitkan bid/ask, jadi tidak ada dasar untuk
    mengukurnya.  Yang dilaporkan adalah harga level - asumsi standar, dan
    optimis pada gap.
    """
    if tersentuh is LevelTersentuh.TARGET:
        return geo.target
    if tersentuh is LevelTersentuh.STOP:
        return geo.stop
    return penutup


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

    dipakai = jalur[:horizon_bar]
    tersentuh = _level_tersentuh(dipakai, geo, arah is Decision.BUY)

    # **Level yang tersentuh mengakhiri gagasannya SEKARANG, bukan saat horizon
    # habis.**  Diukur dari kerugian nyata operator 2026-08-28: tiga sinyal kena
    # stop pukul 19:05 dan resolver menunggu sampai 22:10 - tiga jam sebuah
    # hasil yang sudah pasti menggantung tanpa dicatat, tanpa result terkirim,
    # dan tak terlihat oleh koreksi diri.  Menunggu tidak mengubah apa pun yang
    # sudah terjadi; ia hanya menunda operator mengetahuinya.
    #
    # `arah_benar` tetap `None` di sini, dan itu bukan kelalaian: ia bertanya ke
    # mana harga pergi pada TUTUP HORIZON, dan horizon itu belum tutup.
    # Mengisinya dari harga saat stop tersentuh akan menjawab pertanyaan yang
    # berbeda dengan nama pertanyaan yang sama.  `lengkapi_arah` mengisinya
    # belakangan.
    # Horizon yang SUDAH lengkap selalu lewat jalur biasa di bawah - di sana
    # `arah_benar` bisa diukur pada tutup horizon, dan itu jawaban yang lebih
    # lengkap. Jalur dini hanya untuk yang belum tuntas.
    if len(jalur) < horizon_bar:
        if tersentuh is LevelTersentuh.TIDAK_SATU_PUN:
            return None
        keluar = _harga_keluar(tersentuh, geo, dipakai[-1].close)
        return HasilXau(
            prediction_id=prediction_id,
            arah_benar=None,
            level_tersentuh=tersentuh,
            harga_tutup=keluar,
            gerak_pct=(keluar - geo.entry) / geo.entry * 100,
            bar_dipakai=len(dipakai),
            horizon_bar=horizon_bar,
        )
    naik = arah is Decision.BUY
    penutup = dipakai[-1].close
    keluar = _harga_keluar(tersentuh, geo, penutup)

    return HasilXau(
        prediction_id=prediction_id,
        # Diukur pada tutup horizon dari harga PENUTUP - bukan dari harga
        # keluar. Keduanya menjawab pertanyaan berbeda: yang ini bertanya ke
        # mana pasar akhirnya pergi, dan jawabannya tidak berubah karena
        # posisinya sudah ditutup lebih dulu.
        arah_benar=(penutup > geo.entry) if naik else (penutup < geo.entry),
        level_tersentuh=tersentuh,
        harga_tutup=keluar,
        gerak_pct=(keluar - geo.entry) / geo.entry * 100,
        bar_dipakai=len(dipakai),
        horizon_bar=horizon_bar,
    )


def r_multiple(
    entry: Decimal, stop: Decimal, harga: Decimal, arah: Decision
) -> Decimal | None:
    """Untung/rugi dalam satuan risiko yang dipertaruhkan.

    ``None`` kalau jarak stop nol - tidak ada risiko untuk dibandingkan, dan
    membagi dengan nol akan mengarang angka tak terhingga.
    """
    risiko = abs(entry - stop)
    if risiko == 0:
        return None
    gerak = (harga - entry) if arah is Decision.BUY else (entry - harga)
    return gerak / risiko


def nilai_hasil_akhir(
    *,
    level: LevelTersentuh,
    disuruh_tutup: bool | None,
    r: Decimal | None,
) -> tuple[HasilAkhir, bool | None]:
    """Hasil akhir dan apakah ia menang.  ``None`` = belum bisa dinilai.

    ``disuruh_tutup`` adalah putusan ARUNA saat horizon habis: ``True`` tutup,
    ``False`` tahan, ``None`` belum ada putusan.

    **Menang butuh dua hal sekaligus**: ARUNA menyuruh menutup, DAN untungnya
    setidaknya :data:`MIN_R_UNTUK_WIN`.  Menyuruh tutup di untung tipis bukan
    kemenangan - operator memang diperingatkan, tapi yang didapatnya sebanding
    dengan derau satu bar.
    """
    if level is LevelTersentuh.TARGET:
        return HasilAkhir.TARGET, True
    if level is LevelTersentuh.STOP:
        return HasilAkhir.STOP, False

    if disuruh_tutup is None:
        return HasilAkhir.TAHAN, None
    if not disuruh_tutup:
        # ARUNA menyuruh menahan: posisinya belum ditutup, jadi belum ada
        # hasil. Menghitungnya kalah akan menghukum kesabaran yang ARUNA
        # sendiri sarankan.
        return HasilAkhir.TAHAN, None

    if r is None:
        return HasilAkhir.TUTUP_RUGI, False
    if r >= MIN_R_UNTUK_WIN:
        return HasilAkhir.TUTUP_UNTUNG, True
    return HasilAkhir.TUTUP_RUGI, False


__all__ = [
    "HORIZON_BAR",
    "MIN_R_UNTUK_WIN",
    "HasilAkhir",
    "HasilXau",
    "LevelTersentuh",
    "nilai_hasil",
    "nilai_hasil_akhir",
    "r_multiple",
]
