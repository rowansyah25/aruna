"""Penyimpanan skenario, berbatas dengan sengaja (bagian 16.14, 16.15).

Modul ini ditulis di bawah bayangan satu angka: 216 MB. Itu ukuran kolom
`market_snapshots.raw` pada audit Phase 15.1 - satu kolom, 62% basis data, nol
pembaca. Ia tumbuh sebesar itu karena tiap amatan ditulis apa adanya, tanpa
pertanyaan apakah ada yang akan membacanya.

Simulasi menghasilkan lebih banyak keadaan antara daripada apa pun di ARUNA.
Karena itu yang disimpan di sini **hanya skenarionya**, dan dua batas ditegakkan
di kode alih-alih diserahkan ke niat pemanggil:

* :data:`BATAS_PER_SIMULASI` - berapa skenario yang boleh masuk dari satu
  simulasi. Yang melewatinya dibuang menurut bobot, dan yang dibuang
  **dicatat**, bukan dihilangkan diam-diam.
* :data:`HARI_SKENARIO` - umur baris sebelum retensi boleh membuangnya. Angkanya
  hidup di :mod:`aruna.upkeep.retensi` bersama seluruh rencana lain; yang ada di
  sini hanya rujukannya, supaya tidak ada dua angka yang bisa melenceng.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from aruna.core.logging import get_logger
from aruna.db.pool import Database
from aruna.db.types import as_utc, load_json, to_mysql_datetime
from aruna.scenario.models import (
    HasilSkenario,
    Invalidasi,
    Kerapuhan,
    Skenario,
)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _SkenarioUntukMutu:
    """Skenario seperlunya untuk :func:`~aruna.signals.quality.scenario_factor`.

    Bukan :class:`~aruna.scenario.models.Skenario` yang penuh: yang itu memuat
    perkembangan, bukti, pemicu, dan kondisi awal - puluhan kalimat yang tidak
    seorang pun di jalur keputusan baca, disalin ke tiap konteks tiap bar.

    **Empat bidang ini persis yang dibaca**
    :func:`~aruna.scenario.banding.bandingkan`, dan itu bukan kebetulan:
    ``scenario_factor`` memanggil fungsi Phase 16 itu apa adanya alih-alih
    menulis ulang aturan dominansinya. Menambah bidang di sini boleh; membuang
    salah satu dari keempatnya akan mematahkan panggilan itu.
    """

    nama: str
    bobot: int
    keyakinan: float
    kerapuhan: Kerapuhan
    #: Dibaca ``bandingkan`` untuk mengambil risiko TERTINGGI di antara seluruh
    #: skenario - bukan risiko yang teratas.
    risiko: str = "UNKNOWN"


__all__ = [
    "BATAS_PER_SIMULASI",
    "LEBAR_DESKRIPSI",
    "LEBAR_PEMICU",
    "ScenarioRepository",
]


#: Lebar kolom ``pemicu``, sesudah migrasi 0038.
#:
#: Terukur 2026-08-22: tiga belas pemicu bagian 16.2 yang menyala bersamaan
#: menghasilkan 245 karakter. Kolom lamanya VARCHAR(255) - muat, dengan sisa
#: sepuluh karakter, yang bukan margin.
LEBAR_PEMICU = 512

#: Lebar kolom ``deskripsi``.
LEBAR_DESKRIPSI = 255


def _muat(nilai: str, lebar: int, kolom: str, scenario_id: str) -> str:
    """Potong kalau perlu, tapi **berteriak** saat memotong.

    Versi pertama menulis ``s.pemicu[:255]`` begitu saja. Pemotongan diam-diam
    adalah bentuk kerusakan yang paling sulit ditemukan: barisnya tetap
    tersimpan, tetap terbaca rapi, dan yang hilang tidak meninggalkan jejak apa
    pun. Skenario yang kehilangan sebagian daftar pemicunya tidak bisa diperiksa
    ulang terhadap apa yang sebenarnya membangunkannya.
    """
    if len(nilai) <= lebar:
        return nilai

    log.warning(
        "scenario.nilai_dipotong",
        kolom=kolom,
        scenario_id=scenario_id,
        panjang=len(nilai),
        lebar=lebar,
        detail="kolomnya perlu diperlebar; yang terpotong tidak bisa dipulihkan",
    )
    return nilai[:lebar]


#: Berapa skenario yang boleh tersimpan dari satu simulasi (bagian 16.14).
#:
#: Delapan: bagian 16.5 menyebut tiga wajib dan lima opsional, jadi delapan
#: adalah **seluruh** kosakata mesin internal - batas ini tidak memotong apa pun
#: yang mesin sekarang hasilkan. Ia berdiri untuk mesin eksternal, yang belum
#: ada dan yang tidak ada alasan mempercayai akan menahan diri.
#:
#: Batas yang hanya menggigit pada masukan yang belum pernah dilihat justru
#: bentuk yang benar: ia tidak mengubah apa pun hari ini, dan ia yang menahan
#: ketika MiroFish kelak memulangkan dua ratus skenario dalam satu jawaban.
BATAS_PER_SIMULASI = 8


class ScenarioRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def simpan(
        self, skenario: tuple[Skenario, ...], *, sumber: str = "INTERNAL"
    ) -> int:
        """Sisipkan skenario, paling banyak :data:`BATAS_PER_SIMULASI`.

        ``INSERT IGNORE``: mesinnya deterministik, jadi simulasi yang diulang
        atas masukan yang sama menghasilkan ``scenario_id`` yang sama. Yang
        menahan baris ganda adalah kunci UNIQUE di database, bukan pemeriksaan
        di sini yang bisa kalah balapan.

        Memulangkan berapa baris yang benar-benar masuk - bukan berapa yang
        dikirim. Keduanya berbeda ketika baris sudah ada, dan pemanggil yang
        mengira semuanya masuk akan salah menghitung.
        """
        if not skenario:
            return 0

        # Dipotong menurut bobot, bukan menurut urutan datang: yang terpotong
        # harus yang paling sedikit menarik perhatian, dan urutan datang tidak
        # menyatakan apa pun tentang itu. Nama sebagai pemecah seri supaya
        # pemotongannya deterministik seperti mesinnya.
        urut = sorted(skenario, key=lambda s: (-s.bobot, s.nama))
        masuk, dibuang = urut[:BATAS_PER_SIMULASI], urut[BATAS_PER_SIMULASI:]

        if dibuang:
            # Bagian 16.14 membatasi; ia tidak membolehkan pembatasannya
            # disembunyikan. Baris yang hilang tanpa jejak terbaca sebagai
            # simulasi yang memang menghasilkan sedikit.
            log.warning(
                "scenario.batas_penyimpanan",
                dikirim=len(skenario),
                disimpan=len(masuk),
                dibuang=[s.nama for s in dibuang],
                batas=BATAS_PER_SIMULASI,
            )

        total = 0
        for s in masuk:
            total += await self._db.execute(
                "INSERT IGNORE INTO scenario_evidence "
                "(scenario_id, market_code, asset, dibuat_pada, nama, "
                " deskripsi, bobot, keyakinan, pemicu, risiko, kondisi_awal, "
                " perkembangan, invalidasi, bukti, kerapuhan, versi_simulasi, "
                " sumber) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s)",
                s.scenario_id,
                s.market,
                s.asset,
                to_mysql_datetime(s.timestamp),
                s.nama,
                _muat(s.deskripsi, LEBAR_DESKRIPSI, "deskripsi", s.scenario_id),
                s.bobot,
                round(s.keyakinan, 4),
                _muat(s.pemicu, LEBAR_PEMICU, "pemicu", s.scenario_id),
                s.risiko,
                json.dumps(list(s.kondisi_awal), ensure_ascii=False),
                json.dumps(list(s.perkembangan), ensure_ascii=False),
                json.dumps(list(s.invalidasi.syarat), ensure_ascii=False),
                json.dumps(list(s.bukti), ensure_ascii=False),
                s.kerapuhan.value,
                s.versi_simulasi,
                sumber,
            )

        return total

    async def belum_dinilai(
        self, *, sampai: Any, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Skenario yang horizonnya sudah lewat tapi belum dinilai (16.19).

        ``hasil IS NULL`` dan bukan ``hasil = 'BELUM'``: kolomnya NULL berarti
        evaluasi belum menyentuhnya sama sekali, sedangkan nilai apa pun berarti
        sudah - termasuk penilaian yang menyimpulkan "belum bisa dinilai".
        Menyatukan keduanya membuat baris yang sudah diperiksa diperiksa lagi
        tiap siklus.
        """
        baris = await self._db.fetch(
            "SELECT id, scenario_id, market_code, asset, dibuat_pada, nama, "
            "       bobot, invalidasi, versi_simulasi, sumber "
            "FROM scenario_evidence "
            "WHERE hasil IS NULL AND dibuat_pada < %s "
            "ORDER BY dibuat_pada ASC LIMIT %s",
            to_mysql_datetime(sampai),
            limit,
        )
        # MySQL memulangkan DATETIME tanpa zona. Yang membacanya membandingkan
        # `dibuat_pada` dengan `close_time` candle, dan itu sudah sadar-zona -
        # lihat `MarketDataRepository.candles`. Terukur di produksi 2026-08-22:
        # empat puluh dari empat puluh penilaian gagal dengan
        # `can't compare offset-naive and offset-aware datetimes`.
        #
        # Dinormalkan di sini, bukan di pemanggil: satu repositori yang
        # memulangkan waktu telanjang memaksa tiap pembacanya mengingat, dan
        # yang lupa akan meledak jauh dari sini.
        for r in baris:
            r["dibuat_pada"] = as_utc(r["dibuat_pada"])
        return baris

    # ---- untuk skor mutu (bagian 18.15) ---------------------------------

    async def untuk_keputusan(
        self, *, market: Any, symbol: str, as_of: Any, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Skenario yang berlaku saat sebuah keputusan dibuat (bagian 18.15).

        Untuk :attr:`~aruna.agents.context.DecisionContext.scenario`. Yang
        dipulangkan skenario dari **simulasi terakhir sebelum ``as_of``** -
        bukan gabungan beberapa simulasi, karena bobot skenario bersifat
        relatif terhadap simulasi yang sama (lihat ``CATATAN_BOBOT``) dan
        mencampur dua simulasi menghasilkan bobot yang tidak berarti apa-apa.

        **Hanya yang dibuat SEBELUM ``as_of``.** Skenario yang lahir sesudah
        keputusannya tidak menjelaskan keputusan itu, dan memakainya adalah
        look-ahead yang bagian 18.40 larang keras.

        Daftar kosong ketika pemicunya tidak pernah menyala untuk aset ini -
        dan `scenario_factor` menerjemahkannya menjadi "tidak terukur", bukan
        skenario yang lemah.
        """
        pasar = getattr(market, "value", market)
        terakhir = await self._db.fetchrow(
            "SELECT MAX(dibuat_pada) AS pada FROM scenario_evidence "
            "WHERE asset = %s AND market_code = %s AND dibuat_pada <= %s",
            symbol,
            pasar,
            to_mysql_datetime(as_of),
        )
        if not terakhir or terakhir["pada"] is None:
            return []
        baris = await self._db.fetch(
            "SELECT scenario_id, nama, bobot, keyakinan, invalidasi, risiko "
            "FROM scenario_evidence "
            "WHERE asset = %s AND market_code = %s AND dibuat_pada = %s "
            "ORDER BY bobot DESC LIMIT %s",
            symbol,
            pasar,
            terakhir["pada"],
            limit,
        )
        return [
            _SkenarioUntukMutu(
                nama=str(r["nama"]),
                bobot=int(r["bobot"] or 0),
                keyakinan=float(r["keyakinan"] or 0.0),
                # Aturan kerapuhannya DIPINJAM, bukan ditulis ulang. `RAPUH`
                # berarti seluruh skenario runtuh oleh satu syarat yang hilang
                # (bagian 16.10), dan menyalin ambangnya ke sini berarti dua
                # tempat yang harus tetap sepakat selamanya.
                kerapuhan=Invalidasi(
                    syarat=tuple(load_json(r["invalidasi"]) or ())
                ).kerapuhan,
                risiko=str(r["risiko"] or "UNKNOWN"),
            )
            for r in baris
        ]

    async def catat_hasil(
        self,
        scenario_id: str,
        hasil: HasilSkenario,
        *,
        pada: Any,
        diinvalidasi: bool | None = None,
    ) -> bool:
        """Isi hasil evaluasi satu skenario.

        ``BELUM`` tidak pernah ditulis: ia berarti horizonnya belum lewat, dan
        menuliskannya akan mengeluarkan baris itu dari :meth:`belum_dinilai`
        selamanya - skenario yang belum bisa dinilai berubah menjadi skenario
        yang tidak akan pernah dinilai.

        ``diinvalidasi`` **wajib ikut**, dan bukan pelengkap. Bagian 16.19
        menuntut skenario yang salah SESUDAH memperingatkan lewat invalidasinya
        dinilai terpisah dari yang salah tanpa peringatan. Sebelum kolomnya ada,
        bendera itu dihitung `Putusan` lalu dibuang di sini - jadi 928 baris
        SALAH tersimpan tanpa satu pun bisa dipisahkan.

        ``None`` disimpan apa adanya: ia berarti syarat batalnya tidak bisa
        diperiksa dari jejak harga, bukan berarti tidak terpicu.
        """
        if hasil is HasilSkenario.BELUM:
            return False

        n = await self._db.execute(
            "UPDATE scenario_evidence "
            "SET hasil = %s, diinvalidasi = %s, dinilai_pada = %s "
            "WHERE scenario_id = %s AND hasil IS NULL",
            hasil.value,
            None if diinvalidasi is None else int(diinvalidasi),
            to_mysql_datetime(pada),
            scenario_id,
        )
        return bool(n)

    async def ringkas_per_simulasi(self) -> list[dict[str, Any]]:
        """Ukuran yang **berarti**, per versi mesin.

        **Kenapa yang per-skenario tidak cukup.** Tiap simulasi menghasilkan
        beberapa skenario dan hanya satu keluarga yang benar-benar terjadi, jadi
        "berapa persen skenario yang BENAR" dibatasi dari atas oleh ``1/N`` -
        oleh berapa banyak skenario per simulasi, bukan oleh mutu mesinnya.
        Terukur 2026-08-22: `internal-1` melaporkan 22,9% dengan batas atas
        struktural 33,3%. Angka itu terlihat seperti mutu dan bukan mutu.

        Dua yang berarti:

        * **cakupan** - apakah keluarga yang benar-benar terjadi ada di antara
          skenario yang dihasilkan sama sekali. Ini menguji kosakata mesin.
        * **teratas** - apakah skenario BERBOBOT TERTINGGI yang ternyata benar.
          Ini menguji pembobotannya, dan pembandingnya tebakan acak di antara
          jumlah skenarionya.

        Terukur pada data yang sama: `internal-1` teratas **0 dari 163** -
        pembobotan tangan memberi bobot tertinggi ke "False Breakout" di seluruh
        simulasi, kalah satu poin dari "Bullish Continuation" yang ternyata
        benar 112 kali.
        """
        return await self._db.fetch(
            "SELECT versi_simulasi, COUNT(*) AS simulasi, "
            "       SUM(ada_benar) AS cakupan, SUM(teratas_benar) AS teratas "
            "FROM ( "
            "  SELECT versi_simulasi, asset, dibuat_pada, "
            "    MAX(hasil = 'BENAR') AS ada_benar, "
            "    MAX(CASE WHEN peringkat = 1 AND hasil = 'BENAR' THEN 1 ELSE 0 END) "
            "      AS teratas_benar, "
            "    COUNT(*) AS n "
            "  FROM ( "
            "    SELECT versi_simulasi, asset, dibuat_pada, nama, bobot, hasil, "
            "      ROW_NUMBER() OVER ( "
            "        PARTITION BY versi_simulasi, asset, dibuat_pada "
            "        ORDER BY bobot DESC, nama ASC) AS peringkat "
            "    FROM scenario_evidence WHERE hasil IS NOT NULL "
            "  ) r GROUP BY versi_simulasi, asset, dibuat_pada "
            ") s GROUP BY versi_simulasi"
        )

    async def ringkas_peringatan(self) -> list[dict[str, Any]]:
        """Dari yang SALAH, berapa yang sempat memperingatkan (bagian 16.19).

        **Ini pertanyaan yang berbeda dari akurasi, dan sengaja dijawab
        terpisah.** Skenario yang salah SESUDAH syarat batalnya terpicu adalah
        mesin yang bekerja: ia menyebutkan syarat batalnya, syarat itu terjadi,
        dan pembacanya sudah diperingatkan. Skenario yang salah tanpa satu pun
        syarat batalnya terpicu adalah mesin yang meleset SEKALIGUS invalidasi
        yang tidak berguna.

        Menjumlahkan keduanya menjadi satu angka "salah" menghasilkan ukuran
        yang MEMBAIK ketika skenario berhenti menyebutkan syarat batalnya.

        ``tak_terperiksa`` berdiri sendiri di penyebutnya: baris yang keluarganya
        tidak bisa diperiksa dari jejak harga - dan baris lama, yang dinilai oleh
        kode yang memang belum memeriksanya sama sekali.
        """
        return await self._db.fetch(
            "SELECT versi_simulasi, "
            "  COUNT(*) AS salah, "
            "  SUM(diinvalidasi = 1) AS memperingatkan, "
            "  SUM(diinvalidasi = 0) AS diam, "
            "  SUM(diinvalidasi IS NULL) AS tak_terperiksa "
            "FROM scenario_evidence WHERE hasil = 'SALAH' "
            "GROUP BY versi_simulasi"
        )

    async def ringkas_akurasi(
        self, *, versi: str | None = None
    ) -> list[dict[str, Any]]:
        """Berapa yang BENAR, SALAH, SEBAGIAN - dipisah per versi mesin.

        Dipisah, dan itu bukan kerapian: hasil dua mesin berbeda yang dijumlah
        menjadi satu angka akurasi tidak mengatakan apa pun tentang keduanya,
        dan justru menyembunyikan perbaikan atau kemunduran yang baru terjadi.
        """
        if versi is None:
            return await self._db.fetch(
                "SELECT versi_simulasi, sumber, hasil, COUNT(*) AS jumlah "
                "FROM scenario_evidence WHERE hasil IS NOT NULL "
                "GROUP BY versi_simulasi, sumber, hasil"
            )
        return await self._db.fetch(
            "SELECT versi_simulasi, sumber, hasil, COUNT(*) AS jumlah "
            "FROM scenario_evidence "
            "WHERE hasil IS NOT NULL AND versi_simulasi = %s "
            "GROUP BY versi_simulasi, sumber, hasil",
            versi,
        )
