"""Pembersih retensi: yang boleh lupa, dan yang tidak pernah boleh.

Audit 2026-08-21 menemukan **tidak ada satu pun retention di seluruh basis
kode**. Setiap `DELETE` yang ada hanya penggantian per-sesi. Basis data tumbuh
selamanya - 506 MB pada hari audit, dengan `market_snapshots` menyumbang 62%
dan tumbuh 69.048 baris sehari.

Bentuk modul ini ditentukan oleh satu hal: **kerusakan dari pembersih yang
terlalu rajin tidak bisa dibatalkan**. Karena itu daftar `DILINDUNGI` ditulis
lebih dulu daripada `RENCANA`, dan :meth:`PembersihRetensi.sapu` memeriksa
seluruh rencananya terhadap daftar itu **sebelum menjalankan satu kueri pun**.
Pembersih yang menghapus setengah tabel lalu berteriak sudah terlambat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from aruna.core.logging import get_logger

log = get_logger(__name__)

__all__ = [
    "DILINDUNGI",
    "HARI_CANDLE",
    "HARI_SNAPSHOT",
    "RENCANA",
    "PembersihRetensi",
    "Retensi",
]


#: Tabel yang pembersih ini menolak sentuh, apa pun rencananya.
#:
#: Bagian 31 spec: final signal, agent consensus, disagreement, veto, risk
#: decision, judge decision, WIN, LOSS, self-correction, important events, dan
#: apa pun yang audit bersandar padanya.
#:
#: `news_events` ikut di sini karena dua alasan: isinya bukti yang dibaca
#: analisis, dan `news_asset_links` menunjuk kepadanya dengan foreign key -
#: menghapusnya meninggalkan tautan yatim atau menyeret tautannya ikut hilang.
DILINDUNGI = frozenset(
    {
        # Keputusan dan hasilnya.
        "signals",
        "signal_snapshots",
        "outcome_snapshots",
        "futures_plans",
        "futures_plan_results",
        "futures_plan_delivery",
        # Musyawarah dan buktinya.
        "council_sessions",
        "council_votes",
        "judge_decisions",
        "veto_events",
        "agent_objections",
        "agent_rebuttals",
        # Ingatan dan pembelajaran.
        "market_memories",
        "discovered_patterns",
        "learning_events",
        "loss_autopsies",
        "model_proposals",
        "proposal_decisions",
        "backtest_runs",
        # Jejak audit dan bukti luar.
        "audit_logs",
        "news_events",
        "news_asset_links",
        # Keadaan yang bukan sejarah.
        "app_state",
        "assets",
    }
)

#: Umur snapshot yang masih disimpan, dalam hari.
#:
#: Tabel ini punya tepat tiga pembaca dan ketiganya membaca baris terbaru per
#: simbol; tiga puluh hari jauh melampaui apa pun yang benar-benar dibaca, dan
#: dipilih supaya laporan bulanan tetap punya bahan kalau suatu saat ditulis.
HARI_SNAPSHOT = 30

#: Umur candle per timeframe, dalam hari (bagian 9 spec).
#:
#: Angka 1m sengaja sama dengan batas providernya sendiri: `data/idx/yahoo.py`
#: hanya meminta tujuh hari untuk 1m karena itulah yang Yahoo layani. Menyimpan
#: lebih lama menyimpan sesuatu yang tidak bisa dipulihkan **dan** tidak bisa
#: diperpanjang.
#:
#: Timeframe yang tidak disebut di sini tidak pernah dihapus. Diam berarti
#: simpan, bukan buang.
HARI_CANDLE = {
    "1m": 7,
    "3m": 7,
    "5m": 30,
    "15m": 90,
    "30m": 90,
    "1h": 365,
    "4h": 730,
    "1d": 3650,
    "1w": 3650,
}


@dataclass(frozen=True, slots=True)
class Retensi:
    """Satu aturan: baris di `tabel` yang lebih tua dari `hari` boleh hilang."""

    tabel: str
    kolom_waktu: str
    hari: int
    batas_batch: int
    #: Penyaring tambahan, misalnya satu timeframe candle saja.  Kolomnya
    #: dieja di sini, nilainya di `saring_nilai`, supaya tidak ada string
    #: yang dirangkai dari data.
    saring_kolom: str | None = None
    saring_nilai: str | None = None

    @property
    def nama(self) -> str:
        if self.saring_nilai is None:
            return self.tabel
        return f"{self.tabel}:{self.saring_nilai}"


def _rencana_bawaan() -> tuple[Retensi, ...]:
    aturan = [
        Retensi(
            tabel="market_snapshots",
            kolom_waktu="captured_at",
            hari=HARI_SNAPSHOT,
            batas_batch=1000,
        ),
        # Kejadian operasional: berguna saat menyelidiki minggu ini, tidak
        # berguna sebagai sejarah. `provider_events` tumbuh lebih dari 10.000
        # baris dalam satu hari audit.
        Retensi(
            tabel="provider_events",
            kolom_waktu="occurred_at",
            hari=30,
            batas_batch=1000,
        ),
        Retensi(
            tabel="system_events",
            kolom_waktu="occurred_at",
            hari=90,
            batas_batch=1000,
        ),
        # Skenario simulasi (bagian 16.14). **Bukan** tabel terlindung: daftar
        # `DILINDUNGI` berisi keputusan dan buktinya, dan skenario bukan
        # keputusan - bagian 16.18 menaruh keputusan seluruhnya di Phase 14.
        #
        # Sembilan puluh hari, sama dengan `system_events` dan dengan alasan
        # yang sama: skenario berguna selama evaluasi bagian 16.19 masih bisa
        # membandingkannya dengan hasil pasar, dan horizon terpanjang ARUNA
        # adalah harian. Setelah tiga bulan, yang tersisa bukan bukti melainkan
        # sejarah - dan sejarahnya sudah terangkum di `ringkas_akurasi`.
        Retensi(
            tabel="scenario_evidence",
            kolom_waktu="dibuat_pada",
            hari=90,
            batas_batch=1000,
        ),
        # Funding dan open interest (bagian 16.2). Deret, bukan keputusan -
        # jadi bukan tabel terlindung bagian 31.
        #
        # Tiga puluh hari. Yang membacanya cuma deteksi pemicu, dan ia menanyakan
        # satu jam terakhir; sisanya disimpan supaya penyelidikan atas satu
        # peristiwa masih punya bahan. Dua puluh simbol tiap lima belas menit
        # adalah 1.920 baris sehari, jadi tabelnya berhenti tumbuh di sekitar
        # 57.000 baris - dan tabel tanpa aturan retensi tumbuh selamanya, yang
        # persis bagaimana `market_snapshots` menjadi 62% basis data.
        Retensi(
            tabel="futures_metrics",
            kolom_waktu="captured_at",
            hari=30,
            batas_batch=1000,
        ),
    ]
    aturan += [
        Retensi(
            tabel="candles",
            kolom_waktu="open_time",
            hari=hari,
            batas_batch=1000,
            saring_kolom="interval_code",
            saring_nilai=kode,
        )
        for kode, hari in HARI_CANDLE.items()
    ]
    return tuple(aturan)


RENCANA = _rencana_bawaan()


class _Eksekutor(Protocol):
    async def execute(self, sql: str, *args: Any) -> int: ...


class PembersihRetensi:
    """Menghapus dalam batch berbatas, dan berhenti saat jatahnya habis."""

    def __init__(
        self,
        db: _Eksekutor,
        *,
        rencana: tuple[Retensi, ...] = RENCANA,
    ) -> None:
        self._db = db
        self._rencana = rencana

    async def sapu(
        self, *, now: datetime, batas_total: int
    ) -> dict[str, int]:
        """Jalankan seluruh rencana, paling banyak `batas_total` baris.

        `now` dioper, tidak dibaca dari jam sendiri: pembersih yang punya
        jamnya sendiri tidak bisa diuji, dan jam yang dipakai harus sama dengan
        sisa siklus upkeep.

        Memulangkan jumlah baris yang benar-benar terhapus per aturan -
        termasuk nol, karena aturan yang menghapus nol baris dan aturan yang
        tidak pernah dijalankan terlihat sama dari luar kalau yang nol
        dihilangkan.
        """
        self._tolak_yang_dilindungi()

        hasil: dict[str, int] = {}
        terpakai = 0
        for aturan in self._rencana:
            hasil[aturan.nama] = 0
            if terpakai >= batas_total:
                continue
            terpakai += await self._sapu_satu(
                aturan, now=now, sisa=batas_total - terpakai, hasil=hasil
            )
        return hasil

    def _tolak_yang_dilindungi(self) -> None:
        """Diperiksa sebelum satu kueri pun berjalan.

        Penjaga terhadap rencana yang disunting kemudian oleh orang yang tidak
        membaca bagian 31 - dan itu, cepat atau lambat, adalah semua orang.
        """
        terlarang = sorted(
            {a.tabel for a in self._rencana} & DILINDUNGI
        )
        if terlarang:
            raise ValueError(
                "rencana retensi menyentuh tabel yang dilindungi bagian 31: "
                + ", ".join(terlarang)
            )

    async def _sapu_satu(
        self,
        aturan: Retensi,
        *,
        now: datetime,
        sisa: int,
        hasil: dict[str, int],
    ) -> int:
        batas_waktu = (now - timedelta(days=aturan.hari)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        syarat = f"`{aturan.kolom_waktu}` < %s"
        args: list[Any] = [batas_waktu]
        if aturan.saring_kolom is not None:
            syarat += f" AND `{aturan.saring_kolom}` = %s"
            args.append(aturan.saring_nilai)

        terhapus = 0
        while terhapus < sisa:
            batch = min(aturan.batas_batch, sisa - terhapus)
            n = await self._db.execute(
                f"DELETE FROM `{aturan.tabel}` WHERE {syarat} LIMIT {batch}",
                *args,
            )
            n = int(n or 0)
            terhapus += n
            hasil[aturan.nama] = terhapus
            # Batch yang memulangkan kurang dari yang diminta berarti tabelnya
            # habis. Tanpa berhenti di sini, tabel pertama akan terus dikueri
            # sampai jatah seluruh rencana habis, dan tabel berikutnya tidak
            # pernah kebagian.
            if n < batch:
                break

        if terhapus:
            log.info(
                "retensi.disapu",
                tabel=aturan.nama,
                terhapus=terhapus,
                lebih_tua_dari=batas_waktu,
            )
        return terhapus
