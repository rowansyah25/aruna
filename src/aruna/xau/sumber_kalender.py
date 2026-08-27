"""Dua sumber kalender ekonomi, keduanya gratis.

**ForexFactory** - tanpa kunci, terverifikasi 2026-08-28 (HTTP 200, 71
peristiwa). Memberi jadwal, dampak, forecast, previous. **Tidak memberi
``actual`` sama sekali**: bidangnya tidak eksis, dan 50 peristiwa yang sudah
lewat pun tidak memuatnya. Endpoint-nya tidak resmi dan tidak berdokumen -
dipakai luas dan stabil bertahun-tahun, tapi bisa berubah kapan saja. Karena
itu kegagalannya ditelan dan dilaporkan, tidak pernah dilempar ke pemanggil.

**FRED** - kunci gratis dari fred.stlouisfed.org, resmi milik The Fed.
Menerbitkan ``actual`` dan menerbitkannya memang baru sesudah rilis, jadi
syarat spec "actual hanya tersedia setelah release time" dipenuhi SUMBERNYA,
bukan oleh janji kode ini. Ia tidak punya forecast konsensus, jadi ia melengkapi
ForexFactory alih-alih menggantikannya.

**Keduanya boleh gagal tanpa menjatuhkan keputusan XAU.** Kalender adalah
bukti tambahan; ketiadaan bukti tambahan bukan alasan berhenti menilai. Sebuah
`KonteksBerita` dengan `sumber` kosong menyatakan "tidak ada kalender", yang
berbeda dari "tidak ada peristiwa" - dan bedanya tersimpan.
"""

from __future__ import annotations

from typing import Any

from aruna.core.errors import DataSourceUnavailableError
from aruna.core.logging import get_logger
from aruna.data.http import HttpFetcher
from aruna.xau.kalender import Dampak, PeristiwaEkonomi, ke_utc

log = get_logger(__name__)

SUMBER_FF = "forexfactory"
SUMBER_FRED = "fred"

FF_BASE = "https://nfs.faireconomy.media"
#: Hanya berkas ini yang hidup. Diukur 2026-08-28: `nextweek`, `lastweek`,
#: `today`, dan `tomorrow` semuanya 404. Jangkauannya karena itu satu minggu
#: berjalan - cukup untuk "kapan rilis berikutnya", tidak cukup untuk riwayat.
FF_BERKAS = "ff_calendar_thisweek.json"

FRED_BASE = "https://api.stlouisfed.org"

_DAMPAK = {
    "high": Dampak.HIGH,
    "medium": Dampak.MEDIUM,
    "low": Dampak.LOW,
}


def _dampak(nilai: object) -> Dampak:
    """Yang tak dikenali jadi TIDAK_DINYATAKAN, bukan LOW.

    Memetakan yang asing ke LOW akan membuat peristiwa besar yang labelnya
    berubah diam-diam terhitung sepele.
    """
    return _DAMPAK.get(str(nilai or "").strip().lower(), Dampak.TIDAK_DINYATAKAN)


def _bersih(nilai: object) -> str | None:
    teks = str(nilai or "").strip()
    return teks or None


def urai_forexfactory(payload: Any) -> list[PeristiwaEkonomi]:
    """Ubah payload ForexFactory jadi peristiwa.  Baris rusak dilewati.

    Satu baris cacat tidak boleh membuang tujuh puluh yang sehat - itu sumber
    pihak ketiga tanpa kontrak, dan bentuknya bisa berubah sebagian.
    """
    if not isinstance(payload, list):
        return []
    keluar: list[PeristiwaEkonomi] = []
    for baris in payload:
        if not isinstance(baris, dict):
            continue
        try:
            saat = ke_utc(str(baris["date"]))
            judul = str(baris["title"])
        except (KeyError, ValueError):
            continue
        keluar.append(
            PeristiwaEkonomi(
                judul=judul,
                negara=str(baris.get("country") or "").strip(),
                saat=saat,
                dampak=_dampak(baris.get("impact")),
                sumber=SUMBER_FF,
                forecast=_bersih(baris.get("forecast")),
                previous=_bersih(baris.get("previous")),
                # ForexFactory tidak menerbitkannya - diukur, bukan diasumsikan.
                actual=None,
            )
        )
    return keluar


async def tarik_forexfactory(fetcher: HttpFetcher) -> list[PeristiwaEkonomi]:
    """Tarik kalender minggu berjalan.  Daftar kosong kalau gagal."""
    try:
        payload, _latency = await fetcher.get_json(f"/{FF_BERKAS}")
    except DataSourceUnavailableError as exc:
        log.warning("xau.kalender_ff_gagal", sebab=str(exc))
        return []
    peristiwa = urai_forexfactory(payload)
    log.info("xau.kalender_ff", peristiwa=len(peristiwa))
    return peristiwa


def urai_fred(payload: Any, *, judul: str, negara: str = "USD") -> list[PeristiwaEkonomi]:
    """Ubah satu seri FRED jadi peristiwa yang SUDAH rilis.

    FRED memberi observasi, bukan jadwal: tiap barisnya adalah angka yang sudah
    diterbitkan. Karena itu ``actual`` terisi dan ``forecast`` tidak pernah -
    kebalikan dari ForexFactory, dan itulah gunanya memakai keduanya.
    """
    if not isinstance(payload, dict):
        return []
    keluar: list[PeristiwaEkonomi] = []
    for baris in payload.get("observations") or []:
        nilai = _bersih(baris.get("value"))
        # FRED memakai "." untuk observasi yang tidak ada.
        if nilai in (None, "."):
            continue
        try:
            saat = ke_utc(str(baris["date"]))
        except (KeyError, ValueError):
            continue
        keluar.append(
            PeristiwaEkonomi(
                judul=judul,
                negara=negara,
                saat=saat,
                # FRED tidak memberi tingkat dampak; mengarangnya HIGH akan
                # membuat tiap observasi terlihat penting.
                dampak=Dampak.TIDAK_DINYATAKAN,
                sumber=SUMBER_FRED,
                forecast=None,
                previous=None,
                actual=nilai,
            )
        )
    return keluar


async def tarik_fred(
    fetcher: HttpFetcher, *, seri: str, judul: str, api_key: str, limit: int = 12
) -> list[PeristiwaEkonomi]:
    """Tarik observasi terbaru satu seri FRED.  Kosong kalau tak ada kunci."""
    if not api_key.strip():
        log.info("xau.kalender_fred_dilewati", sebab="tidak ada kunci")
        return []
    try:
        payload, _latency = await fetcher.get_json(
            "/fred/series/observations",
            params={
                "series_id": seri,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": str(limit),
            },
        )
    except DataSourceUnavailableError as exc:
        log.warning("xau.kalender_fred_gagal", seri=seri, sebab=str(exc))
        return []
    return urai_fred(payload, judul=judul)


__all__ = [
    "FF_BASE",
    "FF_BERKAS",
    "FRED_BASE",
    "SUMBER_FF",
    "SUMBER_FRED",
    "tarik_forexfactory",
    "tarik_fred",
    "urai_forexfactory",
    "urai_fred",
]
