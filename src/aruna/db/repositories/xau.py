"""Penyimpanan keputusan XAUUSD M5.

**Penolakan disimpan sama lengkapnya dengan sinyal.**  Spec menuntutnya
("simpan seluruh hasil"), tapi alasannya lebih dalam dari kepatuhan: sebuah
`NO SIGNAL` yang hanya berbunyi "ditolak" tidak bisa dibantah dan tidak bisa
dipelajari.  Enam bulan kemudian, "XAU diam sepanjang Maret" bisa berarti
gerbangnya terlalu ketat atau pasarnya memang sepi, dan tanpa angkanya tidak
ada yang bisa membedakan.  Karena itu ``rr``, ``kontradiksi``, dan
``confidence`` tetap ditulis pada baris yang ditolak - terutama pada baris yang
ditolak.

**``None`` menjadi ``NULL``, tidak pernah ``0``.**  Nol adalah sebuah harga;
tidak diukur adalah ketiadaan harga.  ``kontradiksi`` NULL berarti tak seorang
agen pun mengambil arah, yang berbeda dari perselisihan yang diukur lalu
hasilnya nol - dan menyamakan keduanya akan membuat sinyal yang tak seorang pun
mendukung terbaca sebagai kesepakatan bulat.

**Tidak ada ``ON DUPLICATE KEY UPDATE``**, dengan alasan yang sama seperti
``futures_plans``: penulisan kedua atas setup dan bar yang sama adalah upaya
mengubah keputusan yang sudah diambil, dan itu harus gagal keras alih-alih
diam-diam menang.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from aruna.core.clock import FOREX_CALENDAR
from aruna.core.logging import get_logger
from aruna.db.types import to_mysql_datetime
from aruna.xau.keputusan import SinyalXau

log = get_logger(__name__)

#: Versi model XAU yang menghasilkan sebuah baris.
#:
#: Bukan hiasan: tanpa ini, hasil dari dua model berbeda bercampur dalam satu
#: agregat dan perbandingan apa pun di Rencana 3 kembali melingkar.  Dinaikkan
#: saat gerbang, ambang, atau geometrinya berubah - bukan saat kodenya dirapikan.
VERSI_MODEL_XAU = "xau-m5-1"

#: Bacaan indikator: ``{horizon_code: {nama: (nilai, sample_size, required)}}``.
BacaanBukti = dict[str, dict[str, tuple[float | None, int, int]]]


#: Skala kolom, disalin dari `migrations/0046_xau_sinyal.sql`.
#:
#: Dibulatkan di Python, bukan diserahkan ke MySQL. Diukur di produksi
#: 2026-08-27: ATR datang sebagai 4.647307316048187 - lima belas angka desimal
#: ke kolom berkapasitas delapan - dan tiap baris memicu lima peringatan
#: "Data truncated". Bagian bulatnya tidak pernah terancam (DECIMAL(24,8)
#: memuat enam belas digit di depan koma, emas butuh empat), jadi yang hilang
#: cuma presisi yang tak ada artinya. Yang berbahaya adalah peringatannya:
#: dinding peringatan yang selalu menyala mengajari operator mengabaikan
#: peringatan yang sungguhan.
SKALA_HARGA = Decimal("0.00000001")  # DECIMAL(24,8)
SKALA_RASIO = Decimal("0.0001")  # DECIMAL(10,4) dan DECIMAL(6,4)


def _desimal(
    nilai: float | Decimal | None, skala: Decimal = SKALA_RASIO
) -> Decimal | None:
    """``None`` tetap ``None``; sisanya dibulatkan ke skala kolomnya."""
    if nilai is None:
        return None
    angka = nilai if isinstance(nilai, Decimal) else Decimal(str(nilai))
    return angka.quantize(skala, rounding=ROUND_HALF_EVEN)


class XauRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def simpan(
        self,
        sinyal: SinyalXau,
        *,
        as_of: datetime,
        decided_at: datetime,
        symbol: str = "XAU/USD",
        bukti: BacaanBukti | None = None,
    ) -> int:
        """Tulis satu keputusan beserta suara dan buktinya.  Kembalikan id-nya."""
        geo = sinyal.geometri
        rekap = sinyal.rekap

        prediction_id = await self._db.insert(
            """
            INSERT INTO xau_predictions
                (symbol, setup_id, as_of, sesi, pasar_buka, decided_at,
                 keputusan, alasan_kosong,
                 confidence, setuju, menentang, netral, kontradiksi,
                 entry, stop, target, atr, rr, target_atr, sentuhan_target,
                 spread_bps, spread_diukur, model_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            symbol,
            sinyal.setup_id,
            to_mysql_datetime(as_of),
            # Diukur dari kalender pada close bar keputusan, bukan jam sistem.
            FOREX_CALENDAR.session(as_of),
            FOREX_CALENDAR.is_open(as_of),
            to_mysql_datetime(decided_at),
            sinyal.keputusan.value,
            sinyal.alasan,
            _desimal(sinyal.confidence),
            rekap.setuju if rekap else 0,
            rekap.menentang if rekap else 0,
            rekap.netral if rekap else 0,
            _desimal(rekap.kontradiksi) if rekap else None,
            _desimal(geo.entry, SKALA_HARGA) if geo else None,
            _desimal(geo.stop, SKALA_HARGA) if geo else None,
            _desimal(geo.target, SKALA_HARGA) if geo else None,
            _desimal(geo.atr, SKALA_HARGA) if geo else None,
            _desimal(geo.rr) if geo else None,
            _desimal(geo.target_atr) if geo else None,
            geo.sentuhan_target if geo else None,
            None,
            sinyal.spread_diukur,
            VERSI_MODEL_XAU,
        )

        if rekap is not None:
            for agen in rekap.rincian:
                await self._db.insert(
                    """
                    INSERT INTO xau_agent_votes
                        (prediction_id, role, suara, decision, confidence,
                         abstained)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    prediction_id,
                    agen.role.value,
                    agen.suara.value,
                    # Kosakata dewan apa adanya - termasuk WAIT. Ini BUKAN
                    # keluaran XAU: yang sampai ke operator adalah
                    # `xau_predictions.keputusan`, yang CHECK-nya menolak WAIT.
                    agen.decision.value,
                    _desimal(agen.confidence),
                    agen.abstained,
                )

        for horizon_code, bacaan in (bukti or {}).items():
            for nama, (nilai, sample_size, required) in bacaan.items():
                await self._db.insert(
                    """
                    INSERT INTO xau_evidence
                        (prediction_id, horizon_code, nama, nilai,
                         sample_size, required)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    prediction_id,
                    horizon_code,
                    nama,
                    _desimal(nilai),
                    sample_size,
                    required,
                )

        log.info(
            "xau.tersimpan",
            keputusan=sinyal.keputusan.value,
            setup_id=sinyal.setup_id,
            alasan=sinyal.alasan,
        )
        return prediction_id


    async def perlu_dinilai(self, *, sejak: datetime) -> list[dict[str, Any]]:
        """Sinyal berarah yang belum punya hasil, sejak ``sejak``.

        Dibatasi ``sejak`` karena penilainya memakai bar M5 yang SUDAH ada di
        tangan loop - jendela yang sama yang ditarik tiap tick.  Prediksi yang
        lebih tua dari jendela itu tidak bisa dinilai tanpa menarik ulang jalur
        harganya, dan menariknya diam-diam akan menghabiskan jatah kredit yang
        tidak dianggarkan siapa pun.

        ``LEFT JOIN ... IS NULL`` dan bukan ``NOT IN``: yang kedua memindai
        ulang seluruh tabel hasil untuk tiap baris prediksi.
        """
        return await self._db.fetch(
            """
            SELECT p.id, p.keputusan, p.as_of, p.entry, p.stop, p.target,
                   p.atr, p.sentuhan_target
            FROM xau_predictions p
            LEFT JOIN xau_results r ON r.prediction_id = p.id
            WHERE r.id IS NULL
              AND p.keputusan <> 'NO_SIGNAL'
              AND p.as_of >= %s
            ORDER BY p.as_of
            """,
            to_mysql_datetime(sejak),
        )

    async def simpan_hasil(self, hasil: Any, keputusan: str) -> int:
        """Tulis satu hasil.

        ``keputusan`` disalin ke barisnya bukan karena malas menormalkan: ia
        yang membuat foreign key gabungan bisa menolak hasil untuk prediksi
        ``NO_SIGNAL`` - lihat `migrations/0047_xau_hasil.sql`.
        """
        return await self._db.insert(
            """
            INSERT INTO xau_results
                (prediction_id, keputusan, arah_benar, level_tersentuh,
                 harga_tutup, gerak_pct, bar_dipakai, horizon_bar)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            hasil.prediction_id,
            keputusan,
            hasil.arah_benar,
            hasil.level_tersentuh.value,
            _desimal(hasil.harga_tutup, SKALA_HARGA),
            _desimal(hasil.gerak_pct, Decimal("0.000001")),
            hasil.bar_dipakai,
            hasil.horizon_bar,
        )


__all__ = ["BacaanBukti", "VERSI_MODEL_XAU", "XauRepository"]
