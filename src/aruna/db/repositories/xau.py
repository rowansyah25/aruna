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

import json
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
        regime: Any = None,
        dolar: Any = None,
        berita: Any = None,
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
                 spread_bps, spread_diukur, model_version,
                 regime, regime_confidence, bukti_dipakai, bukti_tersedia,
                 proksi_simbol, proksi_korelasi, proksi_sampel, proksi_gerak_pct,
                 sumber_kalender, menit_ke_rilis, rilis_berikutnya,
                 dampak_berikutnya, menit_sejak_rilis, dampak_tinggi_24j)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            # Rezim ikut supaya "akurasi per rezim" bisa dijawab tanpa
            # menghitung ulang dari luar - dan supaya UNCERTAIN, yang memblokir
            # 17,4% keputusan, bisa disandingkan dengan hasilnya.
            regime.regime.value if regime is not None else None,
            _desimal(regime.confidence) if regime is not None else None,
            regime.evidence_used if regime is not None else None,
            regime.evidence_available if regime is not None else None,
            # Proksi dolar: DIREKAM, tidak ada gerbang yang membacanya. Spec
            # melarang "DXY naik = pasti SELL", dan r terukur 0,35 menunjukkan
            # kenapa. Simbolnya ikut supaya tak pernah ada keraguan tentang apa
            # yang diukur - ia EUR/USD, bukan DXY.
            dolar.simbol if dolar is not None else None,
            _desimal(dolar.korelasi) if dolar is not None else None,
            dolar.sampel if dolar is not None else None,
            _desimal(dolar.gerak_pct, Decimal("0.000001"))
            if dolar is not None
            else None,
            # Kalender: DIREKAM, tidak ada gerbang yang membacanya. Sumber
            # kosong berarti "tidak ada kalender", yang berbeda dari "tidak ada
            # peristiwa" - menyamakannya membuat kegagalan jaringan terbaca
            # sebagai pasar yang tenang.
            ",".join(berita.sumber) if berita is not None and berita.sumber else None,
            _desimal(berita.menit_ke_berikutnya, Decimal("0.1"))
            if berita is not None
            else None,
            berita.berikutnya.judul[:128]
            if berita is not None and berita.berikutnya
            else None,
            berita.berikutnya.dampak.value
            if berita is not None and berita.berikutnya
            else None,
            _desimal(berita.menit_sejak_terakhir, Decimal("0.1"))
            if berita is not None
            else None,
            berita.dampak_tinggi_24j if berita is not None else None,
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


    async def as_of_terakhir(self, symbol: str = "XAU/USD") -> datetime | None:
        """Bar terakhir yang sudah punya keputusan, atau ``None``.

        **Dibaca saat loop menyala, dan itu memperbaiki cacat yang sudah
        meledak.**  Penjaga "satu bar dinilai sekali" hidup di memori proses;
        restart menghapusnya, jadi proses baru menilai ulang bar yang sudah
        disimpan proses lama dan menabrak ``uq_xau_setup_bar``.  Diukur di
        produksi 2026-08-27: crash loop tiga kali beruntun, tiap delapan detik,
        karena supervisor menyalakan ulang apa yang baru saja mati.

        Penjaga di memori menutup drift jadwal; ini yang menutup restart.
        Keduanya perlu - dan yang kedua tidak bisa disimpulkan dari yang
        pertama.
        """
        return await self._db.fetchval(
            "SELECT MAX(as_of) FROM xau_predictions WHERE symbol = %s",
            symbol,
        )

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

    async def sinyal_berjalan(self, *, sejak: datetime) -> list[dict[str, Any]]:
        """Sinyal berarah yang belum punya hasil, beserta kabar terakhirnya.

        ``keadaan_terakhir`` dibaca dari basis data, bukan dari memori proses.
        Alasannya sudah dibayar sekali di modul ini: penjaga yang hidup di
        variabel proses hilang saat restart, dan supervisor mengubah akibatnya
        jadi crash loop.  Di sini akibatnya cuma pesan ganda - tapi sebabnya
        sama persis, dan sudah diketahui.
        """
        return await self._db.fetch(
            """
            SELECT p.id, p.keputusan, p.as_of, p.entry, p.stop, p.target, p.atr,
                   (SELECT k.keadaan FROM xau_kabar k
                     WHERE k.prediction_id = p.id
                     ORDER BY k.id DESC LIMIT 1) AS keadaan_terakhir
            FROM xau_predictions p
            LEFT JOIN xau_results r ON r.prediction_id = p.id
            WHERE r.id IS NULL
              AND p.keputusan <> 'NO_SIGNAL'
              AND p.as_of >= %s
            ORDER BY p.as_of
            """,
            to_mysql_datetime(sejak),
        )

    async def simpan_kabar(
        self, prediction_id: int, keputusan: str, kabar: Any, *, terkirim: bool
    ) -> int:
        """Catat satu PERUBAHAN keadaan.  Bukan tiap tick."""
        return await self._db.insert(
            """
            INSERT INTO xau_kabar
                (prediction_id, keputusan, keadaan, alasan, harga, sisa_bar,
                 ke_target_atr, ke_stop_atr, disarankan_tutup, terkirim)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            prediction_id,
            keputusan,
            kabar.keadaan.value,
            kabar.alasan[:255],
            _desimal(kabar.harga, SKALA_HARGA),
            kabar.sisa_bar,
            _desimal(kabar.ke_target_atr),
            _desimal(kabar.ke_stop_atr),
            kabar.menyarankan_tutup,
            terkirim,
        )

    async def simpan_penutup(
        self,
        prediction_id: int,
        keputusan: str,
        penutup: Any,
        *,
        harga: Any,
        terkirim: bool,
    ) -> int:
        """Catat putusan saat horizon habis: tahan atau tutup.

        ``tahan`` disimpan sebagai kolomnya sendiri, bukan dikubur di teks
        alasan: pertanyaan "berapa kali ARUNA menyuruh menahan, dan berapa di
        antaranya benar" harus bisa dijawab SQL.
        """
        return await self._db.insert(
            """
            INSERT INTO xau_kabar
                (prediction_id, keputusan, keadaan, alasan, harga, sisa_bar,
                 disarankan_tutup, tahan, terkirim)
            VALUES (%s, %s, 'HORIZON_HABIS', %s, %s, 0, %s, %s, %s)
            """,
            prediction_id,
            keputusan,
            penutup.alasan[:255],
            _desimal(harga, SKALA_HARGA),
            not penutup.tahan,
            penutup.tahan,
            terkirim,
        )

    async def baris_keandalan(self) -> list[dict[str, Any]]:
        """Suara berarah yang sudah punya hasil, siap untuk ``build_reliability``.

        Bentuknya sengaja persis seperti yang mesin keandalan crypto minta -
        ``agent``, ``agent_decision``, ``council_decision``,
        ``direction_correct`` - supaya XAU memakai pengukur yang sama alih-alih
        yang kedua.  Dua implementasi keandalan menghasilkan dua angka yang
        tidak bisa dibandingkan, dan yang salah tak akan pernah ketahuan.

        Suara yang abstain tidak ikut: menolak membaca bukti yang tipis adalah
        tindakan yang sah, bukan kegagalan yang perlu dihukum.
        """
        return await self._db.fetch(
            """
            SELECT v.role AS agent,
                   v.decision AS agent_decision,
                   p.keputusan AS council_decision,
                   r.arah_benar AS direction_correct
            FROM xau_results r
            JOIN xau_predictions p ON p.id = r.prediction_id
            JOIN xau_agent_votes v ON v.prediction_id = p.id
            WHERE r.arah_benar IS NOT NULL
              AND v.abstained = FALSE
              AND v.decision IN ('BUY', 'SELL')
            """
        )

    async def hitung_hasil(self) -> int:
        """Berapa hasil yang sudah terselesaikan.  Pemicu putaran koreksi."""
        return int(await self._db.fetchval("SELECT COUNT(*) FROM xau_results") or 0)

    async def koreksi_terakhir(self) -> dict[str, Any] | None:
        """Putaran koreksi terakhir, atau ``None`` kalau belum pernah."""
        baris = await self._db.fetch(
            "SELECT versi, dipicu_oleh, bobot, diterapkan FROM xau_model_versions "
            "ORDER BY id DESC LIMIT 1"
        )
        return baris[0] if baris else None

    async def simpan_koreksi(self, hasil: Any) -> int:
        """Tulis satu putaran koreksi - termasuk yang tidak diterapkan.

        Yang gagal karena sampelnya tipis TETAP ditulis: tanpa barisnya,
        "belum cukup bahan" dan "tidak pernah dijalankan" terlihat sama persis.
        """
        return await self._db.insert(
            """
            INSERT INTO xau_model_versions
                (versi, versi_sebelumnya, dipicu_oleh, sampel, garis_dasar,
                 diterapkan, alasan, bobot)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            hasil.versi,
            hasil.versi_sebelumnya,
            hasil.dipicu_oleh,
            hasil.sampel,
            _desimal(hasil.garis_dasar),
            hasil.diterapkan,
            hasil.alasan,
            json.dumps(hasil.bobot) if hasil.bobot else None,
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
