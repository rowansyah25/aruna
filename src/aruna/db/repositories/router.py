"""Penyimpanan pilihan router (bagian 17.9, 17.27, 17.44, 17.52).

**Satu baris per PILIHAN, bukan per perhitungan.** Router berjalan tiap siklus
atas dua puluh aset; menyimpan tiap peringkat berarti mengulang pelajaran
``market_snapshots``, yang menjadi 62% basis data ini dengan nol pembaca.

**Penolakan ikut tersimpan, dan itu bukan kelengkapan melainkan keharusan.**
Nol karena tidak ada strategi yang cocok dan nol karena fasenya mati terlihat
sama persis dari luar - yang pertama normal sementara yang kedua bug. Baris
tanpa ``alasan_kosong`` tidak bisa membedakan keduanya.

Dan penolakan akan sering terjadi. Diukur 2026-08-23 sebelum router menyala:
1.860 dari 9.437 bacaan 15m berlabel ``UNCERTAIN``, 453 ``HIGH_VOLATILITY`` dan
49 ``ANOMALY`` tanpa strategi mana pun, ditambah tiap aset yang cuma punya satu
horizon segar - keyakinan tertingginya 48 sementara ambangnya 50.

**Tidak pernah ditulis ulang** (bagian 17.27). Rezim berganti sesudah sebuah
pilihan tercatat adalah hal biasa; mengubah catatannya membuat seluruh evaluasi
Phase 12 mengukur keputusan yang tidak pernah diambil siapa pun. ``INSERT
IGNORE``, bukan ``ON DUPLICATE KEY UPDATE``: siklus yang berjalan dua kali pada
bar yang sama tidak menghasilkan dua baris, dan yang **pertama** yang bertahan.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from aruna.core.enums import Horizon, Market
from aruna.db.types import as_utc, to_mysql_datetime
from aruna.router.label import VERSI_ROUTER
from aruna.router.putusan import PutusanRouter
from aruna.router.rezim import BOBOT_INTERVAL, BacaanRezim, PetaRezim

__all__ = [
    "LEBAR_ALASAN_KOSONG",
    "RIWAYAT_STABILITAS",
    "UMUR_MAKSIMUM_BAR",
    "RouterRepository",
    "umur_maksimum",
]


#: Berapa bar sendiri sebelum sebuah bacaan rezim dianggap basi.
#:
#: **Satu aturan untuk ketiga horizon, dan angkanya diukur bukan dipilih.**
#: Yang menentukan bukan selera melainkan seberapa sering sumbernya benar-benar
#: menulis. ``signal_snapshots`` hanya mendapat baris ketika sebuah sinyal
#: terkunci, jadi kepadatannya berbeda jauh per horizon. Terukur 2026-08-23
#: atas tujuh hari dan dua puluh aset::
#:
#:     15m  9.437 baris  ->  satu bacaan tiap ~21 menit  =  1,4 bar
#:     1h   4.057 baris  ->  satu bacaan tiap ~6 jam     =  6   bar
#:     1d   2.407 baris  ->  satu bacaan tiap ~10 jam    =  0,4 bar
#:
#: Jendela yang lebih pendek daripada kepadatannya sendiri akan **selalu**
#: membuang horizon itu. Dengan 1h dibuang, yang tersisa 15m sendirian - dan
#: keyakinannya 20, jauh di bawah ambang 50, jadi router tidak akan pernah
#: memilih siapa pun. Delapan bar menampung ketiganya dengan margin.
#:
#: Dan ia tetap bermakna: sebuah rezim 1d yang sudah berganti sebelum delapan
#: bar hariannya lewat bukan rezim harian, itu derau yang salah label.
#:
#: **Yang basi tidak dipakai diam-diam.** Ia hilang dari peta, `interval_hilang`
#: menyebutnya, dan faktor cakupan di :func:`~aruna.router.rezim.susun_peta`
#: menurunkan keyakinannya sendiri. Bukti yang tipis terbaca tipis.
UMUR_MAKSIMUM_BAR = 8

#: Berapa bacaan 15m berurutan yang dibaca untuk menghitung stabilitas.
#:
#: Delapan, dan itu **bukan** :data:`~aruna.db.repositories.konteks_pemicu.
#: BACAAN_REGIME` (tiga). Pertanyaannya berbeda: yang tiga menjawab "apakah
#: rezimnya baru saja berganti dari keadaan yang mapan", yang ini menjawab
#: "seberapa sering ia berganti akhir-akhir ini". Tiga bacaan memberi dua
#: pasangan, dan stabilitas dari dua pasangan cuma punya tiga nilai mungkin -
#: 0, 50, 100 - yang terlalu kasar untuk menskalakan apa pun.
#:
#: Delapan memberi tujuh pasangan, dan menutupi dua jam pada 15m - jendela yang
#: sama dengan pengukuran Phase 16 yang melahirkan angka 30,6%.
RIWAYAT_STABILITAS = 8


#: Horizon yang riwayatnya dipakai menghitung stabilitas.
#:
#: Yang dipindai, dan yang paling padat bacaannya. Membandingkan bacaan lintas
#: horizon menghasilkan "perpindahan" yang cuma perbedaan timeframe.
_INTERVAL_STABILITAS = Horizon.M15.value

#: Bacaan yang bukan rezim, melainkan classifier yang mengaku tidak tahu.
#: Sejajar dengan :data:`~aruna.router.putusan._TIDAK_TERBACA`; dibuang di sini
#: supaya riwayat stabilitas merapat dan tidak menghitung "RANGING -> tidak
#: tahu -> RANGING" sebagai dua perpindahan.
_TIDAK_TERBACA = frozenset({"UNCERTAIN"})


def umur_maksimum(interval: str) -> timedelta:
    """Berapa lama sebuah bacaan pada ``interval`` masih dianggap sekarang."""
    return Horizon(interval).duration * UMUR_MAKSIMUM_BAR


def _terlalu_tua(locked_at: Any, interval: str, sekarang: datetime) -> bool:
    saat = as_utc(locked_at)
    return saat is None or (sekarang - saat) > umur_maksimum(interval)


#: Lebar kolom ``alasan_kosong``, disebut supaya pemotongannya disengaja.
#:
#: MySQL dalam mode ketat **menolak seluruh barisnya** ketika sebuah string
#: kepanjangan - jadi pilihan yang alasannya panjang tidak tersimpan sama
#: sekali, dan yang hilang justru baris yang paling perlu dibaca. Dipotong di
#: sini dengan penanda, bukan diserahkan kepada database.
LEBAR_ALASAN_KOSONG = 255


def _potong(teks: str, lebar: int) -> str:
    if len(teks) <= lebar:
        return teks
    return teks[: lebar - 1] + "…"


class RouterRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def peta_rezim(
        self, *, sekarang: datetime
    ) -> dict[str, tuple[BacaanRezim, ...]]:
        """Bacaan rezim terbaru per simbol, satu per horizon.

        **Satu kueri untuk seluruh simbol dan seluruh horizon**, bukan satu per
        simbol. Fase ini berjalan tiap siklus atas dua puluh aset; tiga kueri
        per siklus bisa diterima, enam puluh tidak - disiplin yang sama dengan
        :class:`~aruna.db.repositories.konteks_pemicu.KonteksPemicuRepository`.

        Batas umurnya **per horizon**, bukan satu batas untuk semuanya. Satu
        jam masuk akal bagi bacaan 15m dan mustahil bagi bacaan 1d; memaksakan
        satu angka berarti membuang seluruh horizon panjang, dan dengan itu
        membuang seluruh alasan bagian 17.8 ada.

        ``UNCERTAIN`` dibuang di sini, sejajar dengan ``NULL`` - classifier yang
        mengaku tidak tahu bukan sebuah rezim. Disaring **sesudah**
        dinormalkan, bukan di SQL: dua tempat yang memutuskan hal yang sama
        dengan aturan berbeda adalah bug yang menunggu giliran.
        """
        paling_lama = max(umur_maksimum(i) for i in BOBOT_INTERVAL)
        baris = await self._db.fetch(
            "SELECT symbol, horizon_code, regime, locked_at "
            "FROM signal_snapshots "
            "WHERE locked_at >= %s AND regime IS NOT NULL "
            "AND horizon_code IN (%s, %s, %s) "
            "ORDER BY symbol, horizon_code, locked_at DESC",
            to_mysql_datetime(sekarang - paling_lama),
            *BOBOT_INTERVAL,
        )

        keluar: dict[str, dict[str, BacaanRezim]] = {}
        for r in baris:
            interval = str(r["horizon_code"])
            per_simbol = keluar.setdefault(str(r["symbol"]), {})
            if interval in per_simbol:
                # Barisnya sudah terurut menurun, jadi yang pertama yang
                # terbaru. Sisanya riwayat, dan riwayat urusan `stabilitas`.
                continue
            regime = str(r["regime"]).strip().upper()
            if regime in _TIDAK_TERBACA:
                continue
            if _terlalu_tua(r["locked_at"], interval, sekarang):
                continue
            per_simbol[interval] = BacaanRezim(
                interval=interval,
                regime=regime,
                alasan=(f"{regime} pada {interval}",),
            )
        return {s: tuple(v.values()) for s, v in keluar.items() if v}

    async def riwayat_15m(
        self, *, sekarang: datetime, batas: int = RIWAYAT_STABILITAS
    ) -> dict[str, tuple[str, ...]]:
        """Bacaan 15m berurutan per simbol, terbaru lebih dulu.

        Untuk :func:`~aruna.router.rezim.stabilitas`. Hanya 15m: membandingkan
        bacaan dari horizon berbeda menghasilkan "perpindahan" yang sebenarnya
        cuma perbedaan timeframe, dan itu menyala hampir selalu.
        """
        rows = await self._db.fetch(
            "SELECT symbol, regime FROM signal_snapshots "
            "WHERE locked_at >= %s AND regime IS NOT NULL AND horizon_code = %s "
            "ORDER BY symbol, locked_at DESC",
            to_mysql_datetime(sekarang - umur_maksimum(_INTERVAL_STABILITAS)),
            _INTERVAL_STABILITAS,
        )
        keluar: dict[str, list[str]] = {}
        for r in rows:
            regime = str(r["regime"]).strip().upper()
            if regime in _TIDAK_TERBACA:
                continue
            daftar = keluar.setdefault(str(r["symbol"]), [])
            if len(daftar) < batas:
                daftar.append(regime)
        return {s: tuple(v) for s, v in keluar.items()}

    async def risiko_terakhir(self, *, sekarang: datetime) -> dict[str, str]:
        """Tingkat risiko terakhir per simbol, apa adanya dari penyimpanan.

        **Tidak diterjemahkan di sini.** Kolomnya menyimpan kosakata
        :class:`aruna.agents.risk.RiskLevel` - ``LOW``/``MODERATE``/``HIGH``/
        ``EXTREME`` - dan penerjemahannya milik
        :meth:`~aruna.router.putusan.VonisTingkat.dari_tersimpan`, satu tempat.

        Jendelanya sama dengan bacaan 15m: sebuah tingkat risiko dari kemarin
        bukan risiko sekarang, dan memakainya berarti menahan champion atas
        keadaan yang sudah lewat.
        """
        rows = await self._db.fetch(
            "SELECT symbol, risk_level, locked_at FROM signal_snapshots "
            "WHERE locked_at >= %s AND risk_level IS NOT NULL "
            "ORDER BY symbol, locked_at DESC",
            to_mysql_datetime(sekarang - umur_maksimum(_INTERVAL_STABILITAS)),
        )
        keluar: dict[str, str] = {}
        for r in rows:
            keluar.setdefault(str(r["symbol"]), str(r["risk_level"]))
        return keluar

    async def pilihan_terakhir(self) -> dict[str, tuple[str | None, str | None]]:
        """Champion dan rezim yang terakhir tercatat per simbol.

        Untuk :func:`~aruna.router.invalidasi.kenapa_berganti`. Satu kueri
        untuk seluruh simbol, dan **tanpa batas waktu**: pilihan terakhir tetap
        pilihan terakhir walau ARUNA mati semalam, dan peralihan yang melintasi
        waktu mati justru yang paling perlu terlihat.
        """
        rows = await self._db.fetch(
            "SELECT r.symbol, r.champion, r.regime_primary FROM router_pilihan r "
            "JOIN (SELECT asset_id, MAX(dipilih_pada) t FROM router_pilihan "
            "      GROUP BY asset_id) k "
            "  ON r.asset_id = k.asset_id AND r.dipilih_pada = k.t"
        )
        return {
            str(r["symbol"]): (
                None if r["champion"] is None else str(r["champion"]),
                None if r["regime_primary"] is None else str(r["regime_primary"]),
            )
            for r in rows
        }

    async def simpan(
        self,
        putusan: PutusanRouter,
        *,
        asset_id: int,
        market: Market,
        symbol: str,
        peta: PetaRezim,
        dipilih_pada: datetime,
        stabilitas: float | None,
    ) -> int:
        """Catat satu pilihan router, termasuk ketika tidak ada yang dipilih.

        ``dipilih_pada`` dioper, tidak dibaca dari jam di dalam sini. Yang
        kedua membuat pilihan tidak bisa diuji ulang, dan membuat replay
        Phase 9 menulis stempel hari ini pada keputusan tahun lalu.

        ``stabilitas`` boleh ``None`` - riwayatnya terlalu pendek untuk diukur.
        Disimpan NULL dan bukan nol: nol berarti rezimnya berkedip terus, dan
        itu kesimpulan yang jauh lebih dramatis daripada "baru satu bacaan".
        """
        champion = putusan.champion
        challenger = putusan.challenger
        return await self._db.execute(
            "INSERT IGNORE INTO router_pilihan "
            "(asset_id, market_code, symbol, dipilih_pada, regime_primary, "
            " regime_confidence, regime_stability, interval_hilang, champion, "
            " champion_skor, challenger, challenger_skor, alasan_kosong, "
            " kode_kosong, alasan, versi_router) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s)",
            asset_id,
            market.value,
            symbol,
            to_mysql_datetime(dipilih_pada),
            peta.primary,
            round(peta.primary_confidence, 3),
            None if stabilitas is None else round(stabilitas, 3),
            ",".join(peta.interval_hilang) or None,
            None if champion is None else champion.kode,
            None if champion is None else champion.skor,
            None if challenger is None else challenger.kode,
            None if challenger is None else challenger.skor,
            _potong(putusan.alasan_kosong, LEBAR_ALASAN_KOSONG) or None,
            None if putusan.kode_kosong is None else str(putusan.kode_kosong),
            json.dumps(list(putusan.alasan), ensure_ascii=False)
            if putusan.alasan
            else None,
            VERSI_ROUTER,
        )
