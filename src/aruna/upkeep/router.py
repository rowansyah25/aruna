"""Fase router: pilih strategi per aset menurut rezim (bagian 17.19, 17.53).

**Di sinilah Phase 17 berhenti menjadi kode dan mulai menjadi keputusan.**
Task 1-7 semuanya lulus test unitnya sendiri dan tidak satu pun dipanggil; ini
modul yang memanggil mereka. Cacat yang sudah lima kali muncul di proyek ini -
kode ditulis, diuji, diekspor, lalu tidak pernah dipanggil - dijaga di sini oleh
`test_router_terpasang`, penjaga AST yang menuntut fase ini benar-benar sampai
ke :class:`~aruna.upkeep.loop.UpkeepLoop`.

Alurnya, sesuai diagram operator 2026-08-23::

    signal_snapshots       ->  peta rezim multi-timeframe   (Task 1)
    riwayat 15m            ->  stabilitas                   (Task 2)
    strategy_performance   ->  slice per rezim              (Task 3)
    katalog strategies     ->  kandidat menurut status      (Task 5)
                           ->  skor kecocokan tiap kandidat (Task 4)
                           ->  champion / challenger / NONE (Task 5)
                           ->  router_pilihan               (Task 7)

**Hanya aset yang benar-benar dipindai.** Batas umur bacaan dihitung dalam bar
horizon itu sendiri, jadi jendela 1d membentang delapan HARI - cukup lama untuk
menghidupkan kembali aset yang sudah lama berhenti dipindai. Terukur 2026-08-23:
31 simbol punya bacaan "segar" sementara yang dipindai cuma 20. Sebelas sisanya
akan menghasilkan sebelas baris NONE tiap siklus, dan NONE yang tidak berarti
apa-apa mengencerkan NONE yang berarti.

**Kegagalannya tidak pernah menjatuhkan siklus.** Fase ini menghasilkan bukti,
bukan keputusan, dan siklus yang sama juga menghasilkan keputusan sungguhan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aruna.core.enums import Market
from aruna.core.logging import get_logger
from aruna.governance.proposal import MIN_VALIDATION_SAMPLE
from aruna.router.kecocokan import Kecocokan, nilai
from aruna.router.label import performa_rezim
from aruna.router.peringkat import kandidat_layak
from aruna.router.putusan import AlasanKosong, PutusanRouter, pilih
from aruna.router.rezim import BacaanRezim, PetaRezim, stabilitas, susun_peta

log = get_logger(__name__)

__all__ = ["FaseRouter", "HasilRouter"]


@dataclass(slots=True)
class HasilRouter:
    """Apa yang terjadi pada satu fase router.

    Nol champion karena tidak ada yang cocok dan nol champion karena fasenya
    mati terlihat sama dari luar; bidang-bidang di bawah yang membedakannya.
    """

    #: Berapa aset yang petanya benar-benar dibaca.
    dipertimbangkan: int = 0
    #: Berapa yang dapat champion.
    terpilih: int = 0
    #: Berapa yang ditolak, dipecah menurut sebabnya. Bagian yang paling
    #: berguna dari seluruh laporan ini - NONE tanpa sebab tidak bisa dibantah.
    #:
    #: Dikelompokkan menurut :class:`~aruna.router.putusan.AlasanKosong`, bukan
    #: menurut kalimatnya. Kalimatnya menyebut angka - "keyakinan rezim 20%",
    #: "keyakinan rezim 32%" - jadi mengelompokkan darinya membuat tiap
    #: penolakan menjadi kelompoknya sendiri, dan laporannya sama tak
    #: bergunanya dengan daftar mentah.
    ditolak: dict[str, int] = field(default_factory=dict)
    disimpan: int = 0

    def catat_tolak(self, kode: AlasanKosong | None) -> None:
        kunci = str(kode or "TIDAK_DISEBUT")
        self.ditolak[kunci] = self.ditolak.get(kunci, 0) + 1


class FaseRouter:
    """Menjalankan router untuk aset yang baru saja dipindai.

    ``repo`` dan ``performa`` keduanya opsional, dan keduanya kosong adalah
    keadaan yang sah: tanpa ``repo`` tidak ada yang bisa dibaca maupun disimpan
    dan fase ini diam; tanpa ``performa``, peringkatnya berjalan **tanpa bukti
    performa** - hanya kecocokan rezim, keyakinan, dan stabilitas.

    Yang kedua bukan kerusakan. Sesudah Task 3, seluruh slice per-rezim memang
    memulangkan ``None`` sampai baris berlabel ``router-1`` cukup banyak, dan
    router memang harus berjalan selama itu supaya baris-baris itu lahir.
    """

    def __init__(
        self,
        *,
        repo: Any = None,
        katalog: Any = None,
        performa: Any = None,
        minimum_sampel: int = MIN_VALIDATION_SAMPLE,
    ) -> None:
        self._repo = repo
        #: Sumber katalog strategi. ``None`` memakai katalog bawaan.
        self._katalog = katalog
        #: Pembaca ``strategy_performance``. ``None`` menjalankan router tanpa
        #: bukti performa - lihat catatan kelas.
        self._performa = performa
        self._minimum = minimum_sampel

    async def jalankan(
        self, hasil_pindai: list[Any], *, now: datetime
    ) -> HasilRouter:
        keluar = HasilRouter()
        if self._repo is None:
            return keluar

        terpindai = _terpindai(hasil_pindai)
        if not terpindai:
            return keluar

        peta_semua = await self._repo.peta_rezim(sekarang=now)
        riwayat = await self._repo.riwayat_15m(sekarang=now)
        baris_performa = await self._baris_performa()
        strategi = kandidat_layak(_katalog(self._katalog))

        for simbol, ident in sorted(terpindai.items()):
            bacaan = peta_semua.get(simbol)
            if not bacaan:
                continue
            keluar.dipertimbangkan += 1
            putusan, peta, stabil = self._putuskan(
                bacaan, riwayat.get(simbol, ()), strategi, baris_performa
            )
            if putusan.champion is None:
                keluar.catat_tolak(putusan.kode_kosong)
            else:
                keluar.terpilih += 1
            keluar.disimpan += await self._simpan(
                putusan, ident=ident, peta=peta, stabil=stabil, now=now
            )

        log.info(
            "router.selesai",
            dipertimbangkan=keluar.dipertimbangkan,
            terpilih=keluar.terpilih,
            ditolak=keluar.ditolak,
        )
        return keluar

    def _putuskan(
        self,
        bacaan: tuple[BacaanRezim, ...],
        riwayat: tuple[str, ...],
        strategi: Any,
        baris_performa: list[Any],
    ) -> tuple[PutusanRouter, PetaRezim, float | None]:
        peta = susun_peta(bacaan)
        stabil = stabilitas(riwayat)

        kandidat: list[Kecocokan] = []
        for s in strategi.champion:
            performa = (
                performa_rezim(
                    baris_performa,
                    kode=s.code,
                    regime=peta.primary or "",
                    minimum=self._minimum,
                )
                if baris_performa
                else None
            )
            kandidat.append(nilai(s, peta=peta, performa=performa, stabil=stabil))

        return pilih(tuple(kandidat), peta=peta), peta, stabil

    async def _baris_performa(self) -> list[Any]:
        """Baris ``strategy_performance``, atau kosong kalau tak terbaca.

        Kegagalannya menurunkan mutu peringkat, tidak menghentikannya. Router
        yang menolak berjalan karena tabel performa tidak terbaca akan berhenti
        justru pada hari tabel itu paling perlu diisi ulang.
        """
        if self._performa is None:
            return []
        try:
            return list(await self._performa.semua_slice())
        except Exception:
            log.exception("router.performa_tak_terbaca")
            return []

    async def _simpan(
        self,
        putusan: PutusanRouter,
        *,
        ident: tuple[int, Market, str],
        peta: PetaRezim,
        stabil: float | None,
        now: datetime,
    ) -> int:
        asset_id, market, simbol = ident
        try:
            return await self._repo.simpan(
                putusan,
                asset_id=asset_id,
                market=market,
                symbol=simbol,
                peta=peta,
                dipilih_pada=now,
                stabilitas=stabil,
            )
        except Exception:
            # Satu simbol yang gagal disimpan tidak boleh membuang sembilan
            # belas yang berhasil - disiplin yang sama dengan `_potong` di
            # laporan diam harian.
            log.exception("router.simpan_gagal", simbol=simbol)
            return 0


def _terpindai(hasil: list[Any]) -> dict[str, tuple[int, Market, str]]:
    """Simbol yang benar-benar dipindai siklus ini, berikut identitasnya.

    Yang tidak punya ``asset_id`` dilewati: baris `router_pilihan` menyimpannya
    sebagai kunci, dan menebaknya berarti menulis pilihan atas aset yang salah.
    """
    keluar: dict[str, tuple[int, Market, str]] = {}
    for r in hasil:
        simbol = getattr(r, "symbol", None)
        asset_id = getattr(r, "asset_id", None)
        if not simbol or not asset_id:
            continue
        pasar = getattr(r, "market", None)
        keluar[str(simbol)] = (
            int(asset_id),
            pasar if isinstance(pasar, Market) else Market.CRYPTO,
            str(simbol),
        )
    return keluar


def _katalog(sumber: Any) -> tuple[Any, ...]:
    if sumber is not None:
        return tuple(sumber)
    from aruna.learning.strategies import ALL

    # `ALL`, bukan `CATALOG`: ia memuat `UNMAPPED` (STR-000), dan
    # `kandidat_layak` yang membuangnya. Menyaring dua kali di dua tempat
    # berarti dua aturan yang harus tetap sepakat.
    return tuple(ALL)
