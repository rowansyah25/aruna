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

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from aruna.core.enums import Horizon, Market
from aruna.core.logging import get_logger
from aruna.governance.proposal import MIN_VALIDATION_SAMPLE
from aruna.learning.strategies import StrategyStatus
from aruna.router.invalidasi import PilihanSebelumnya, kenapa_berganti
from aruna.router.kecocokan import Kecocokan, nilai
from aruna.router.label import performa_rezim
from aruna.router.peringkat import kandidat_layak
from aruna.router.putusan import (
    AlasanKosong,
    PutusanRouter,
    VonisTingkat,
    lolos_gerbang,
    pilih,
)
from aruna.router.rezim import BacaanRezim, PetaRezim, stabilitas, susun_peta
from aruna.upkeep.candles import bar_start

log = get_logger(__name__)

__all__ = ["INTERVAL_ROUTER", "FaseRouter", "HasilRouter"]


#: Bar yang menentukan stempel satu-pilihan-per-bar.
#:
#: Lima belas menit, sama dengan yang dipindai dan yang disimulasikan
#: (:data:`~aruna.upkeep.skenario.INTERVAL_PEMICU`). Bukan pilihan estetis:
#: fase ini dipanggil dari :meth:`~aruna.upkeep.loop.UpkeepLoop._scan`, jadi
#: kadensnya memang kadens pemindaian, dan stempel yang lebih halus daripada
#: itu menyimpan pilihan yang sama berulang-ulang.
INTERVAL_ROUTER = Horizon.M15


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
    #: Berapa aset yang championnya BERGANTI dari siklus sebelumnya.
    #:
    #: Ini angka adaptasi bagian 17.26. Nol terus berarti router memilih hal
    #: yang sama selamanya - yang bisa benar (pasarnya memang diam) atau bisa
    #: berarti pemetaannya melingkar. Tanpa angka ini keduanya tak terbedakan.
    berganti: int = 0
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
        status: Any = None,
        performa: Any = None,
        minimum_sampel: int = MIN_VALIDATION_SAMPLE,
    ) -> None:
        self._repo = repo
        #: Sumber katalog strategi. ``None`` memakai katalog bawaan.
        self._katalog = katalog
        #: Pembaca status dari tabel ``strategies``. ``None`` memakai status
        #: yang tertulis di katalog kode - dan itu **salah di produksi**, lihat
        #: :func:`_dengan_status`.
        self._status = status
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

        # **Stempel BAR, bukan jam sistem**, dan itu perbaikan yang diukur di
        # produksi 2026-08-23. Migrasi 0041 sudah memberi kolomnya komentar
        # "awal bar yang jadi dasar keputusan, bukan jam sistem" dan kunci
        # UNIQUE (asset_id, dipilih_pada) supaya siklus yang berulang di bar
        # yang sama tidak menghasilkan dua baris - lalu `now` yang dioper.
        #
        # Kuncinya karena itu tidak pernah bentrok: resolusinya mikrodetik.
        # Terukur 18 siklus dalam 8,5 menit, 360 baris, proyeksi 60.632 baris
        # per hari - persis pelajaran `market_snapshots` yang komentar migrasi
        # itu sendiri sebut. Dengan stempel bar: 20 aset x 96 bar = 1.920.
        bar = bar_start(now, INTERVAL_ROUTER)

        terpindai = _terpindai(hasil_pindai)
        if not terpindai:
            return keluar

        identitas = await self._repo.identitas()
        peta_semua = await self._repo.peta_rezim(sekarang=now)
        riwayat = await self._repo.riwayat_15m(sekarang=now)
        risiko = await self._repo.risiko_terakhir(sekarang=now)
        sebelumnya = await self._repo.pilihan_terakhir()
        baris_performa = await self._baris_performa()
        katalog = _dengan_status(
            _katalog(self._katalog), await self._status_tersimpan()
        )
        strategi = kandidat_layak(katalog)
        boleh_memimpin = frozenset(s.code for s in strategi.champion)

        for simbol in sorted(terpindai):
            bacaan = peta_semua.get(simbol)
            ident = identitas.get(simbol)
            # Simbol tanpa identitas dilewati, bukan ditebak: `router_pilihan`
            # menyimpan `asset_id` sebagai kunci, dan menebaknya berarti
            # menulis pilihan atas aset yang salah.
            if not bacaan or ident is None:
                continue

            lama = sebelumnya.get(simbol)
            # Bar ini sudah ditulis. Ditahan DI SINI, bukan diserahkan kepada
            # kunci UNIQUE: yang paling murah adalah tidak mengirimnya sama
            # sekali. Empat siklus per bar x dua puluh aset = enam puluh INSERT
            # yang diabaikan tiap bar, masing-masing memuntahkan peringatan
            # `Duplicate entry` ke log - 5.760 baris sehari.
            if lama is not None and lama[2] == bar:
                continue

            keluar.dipertimbangkan += 1
            putusan, peta, stabil = self._putuskan(
                bacaan, riwayat.get(simbol, ()), strategi, baris_performa
            )
            # Gerbang risiko, sesudah peringkat dan sebelum penyimpanan
            # (diagram operator 2026-08-23). Kedua kalinya risiko masuk, dan
            # pertanyaannya berbeda dari yang di `kecocokan.nilai`: yang itu
            # sejarah drawdown strateginya, yang ini keadaan pasar sekarang.
            putusan = lolos_gerbang(
                putusan, vonis=VonisTingkat.dari_tersimpan(risiko.get(simbol))
            )
            # Bagian 17.26: peralihannya dicatat, bukan disimpulkan. Sebuah
            # baris dengan champion baru tidak menyebutkan siapa yang ia
            # gantikan, apalagi kenapa - dan adaptasi yang tidak bisa dilihat
            # tidak bisa dibuktikan terjadi.
            peralihan = kenapa_berganti(
                None if lama is None else PilihanSebelumnya(lama[0], lama[1]),
                putusan=putusan,
                peta=peta,
                boleh_memimpin=boleh_memimpin,
            )
            if peralihan:
                putusan = replace(putusan, alasan=(*putusan.alasan, *peralihan))
                keluar.berganti += 1

            if putusan.champion is None:
                keluar.catat_tolak(putusan.kode_kosong)
            else:
                keluar.terpilih += 1
            keluar.disimpan += await self._simpan(
                putusan, ident=ident, simbol=simbol, peta=peta,
                stabil=stabil, now=bar,
            )

        log.info(
            "router.selesai",
            dipertimbangkan=keluar.dipertimbangkan,
            terpilih=keluar.terpilih,
            berganti=keluar.berganti,
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

        # **Yang dinilai `challenger`, bukan `champion`** - ia himpunan yang
        # lebih besar dan memuat keduanya. Versi pertama menilai `champion`
        # saja, dan akibatnya seluruh 680 baris produksi berkolom `challenger`
        # NULL: strategi berstatus `UNDER_REVIEW` tidak pernah muncul di mana
        # pun, padahal seluruh alasan slot challenger ada justru untuk mereka.
        #
        # Yang menahan mereka memimpin sekarang `boleh_memimpin` di tiap
        # `Kecocokan`, bukan penyaringan di hulu - jadi mereka ikut dinilai dan
        # ikut tercatat.
        boleh_memimpin = {s.code for s in strategi.champion}
        kandidat: list[Kecocokan] = []
        for s in strategi.challenger:
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
            kandidat.append(
                nilai(
                    s,
                    peta=peta,
                    performa=performa,
                    stabil=stabil,
                    boleh_memimpin=s.code in boleh_memimpin,
                )
            )

        return pilih(tuple(kandidat), peta=peta), peta, stabil

    async def _status_tersimpan(self) -> dict[str, str]:
        """Status dari tabel ``strategies``, atau kosong kalau tak terbaca.

        Kegagalannya mengembalikan status ke katalog kode - yang berarti
        seluruhnya ``ACTIVE`` - dan itu **dicatat**, tidak didiamkan. Router
        yang menolak berjalan karena tabel status tidak terbaca akan berhenti
        justru pada hari tabel itu paling perlu diperbaiki.
        """
        if self._status is None:
            return {}
        try:
            return dict(await self._status.status())
        except Exception:
            log.exception("router.status_katalog_tak_terbaca")
            return {}

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
        ident: tuple[int, Market],
        simbol: str,
        peta: PetaRezim,
        stabil: float | None,
        now: datetime,
    ) -> int:
        asset_id, market = ident
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


def _terpindai(hasil: list[Any]) -> frozenset[str]:
    """Simbol yang benar-benar dipindai siklus ini.

    **Hanya simbolnya, dan itu koreksi 2026-08-23.** Versi pertama menuntut
    ``asset_id`` dan ``market`` dari tiap hasil - dan
    :class:`~aruna.scanner.events.ScanResult` **tidak punya keduanya**.
    Bidangnya ``symbol``, ``events``, ``usable_bars``, ``scanned``, ``reason``.

    Akibatnya setiap hasil dibuang, fase router diam, dan tidak satu pun galat
    muncul. Terukur sesudah ARUNA dinyalakan: fase pindai berjalan 410 kali,
    baris `router_pilihan` nol. Yang menyembunyikannya adalah test double yang
    bidangnya kupilih sendiri - cacat yang sudah tercatat di proyek ini sebagai
    "palsu berbentuk salah".

    Identitasnya sekarang datang dari :meth:`~aruna.db.repositories.router.
    RouterRepository.identitas`, yang membacanya dari tabel yang memang
    menyimpannya.

    ``scanned=False`` dilewati: aset yang buktinya tidak cukup untuk dipindai
    juga tidak cukup untuk dipilihkan strategi.
    """
    return frozenset(
        str(r.symbol)
        for r in hasil
        if getattr(r, "symbol", None) and getattr(r, "scanned", True)
    )


#: Status yang dipakai ketika tabel menyebut nilai yang tidak dikenal enum.
#:
#: ``RETIRED``, dan itu arah kegagalan yang benar: sebuah status baru yang lupa
#: diurus di sini tidak akan memimpin apa pun sampai seseorang mengurusnya.
#: Menebaknya ``ACTIVE`` berarti status yang belum dipahami lolos memimpin
#: diam-diam.
_STATUS_ASING = StrategyStatus.RETIRED


def _dengan_status(
    katalog: tuple[Any, ...], tersimpan: dict[str, str]
) -> tuple[Any, ...]:
    """Katalog kode, dengan status dari TABEL kalau ada.

    **Cacat yang ditemukan saat mengukur Task 11, 2026-08-23**, dan bentuknya
    varian yang berulang di proyek ini: fungsinya dipanggil, tapi masukan yang
    membedakannya tidak pernah sampai.

    Katalog di :mod:`aruna.learning.strategies` menulis setiap strategi
    ``ACTIVE``. Tabel ``strategies`` - yang governance tulis berdasarkan
    pengukuran - mencatat lain::

        KODE:      STR-002 ACTIVE        STR-005 ACTIVE
        DATABASE:  STR-002 UNDER_REVIEW  STR-005 UNDER_REVIEW

    dengan sebab yang tertulis di barisnya sendiri: "lebih buruk dari rata-rata
    pada 1043 sample; cukup diukur untuk pantas dipertimbangkan dihentikan".

    Tanpa fungsi ini, seluruh pembedaan champion/challenger di
    :mod:`aruna.router.peringkat` mati di produksi - dan matinya senyap, karena
    test unitnya mengoper statusnya sendiri dan tetap hijau.

    Yang diambil dari tabel **hanya statusnya**. ``preferred_regimes`` tetap
    dari kode: kolomnya di tabel bertipe JSON dan bisa kosong pada baris lama,
    dan mengambil keduanya dari sumber berbeda berarti strategi yang statusnya
    baru tapi preferensinya lama.
    """
    if not tersimpan:
        return katalog
    return tuple(
        replace(s, status=_status(tersimpan.get(s.code), s.status)) for s in katalog
    )


def _status(nilai: str | None, bawaan: StrategyStatus) -> StrategyStatus:
    if nilai is None:
        return bawaan
    try:
        return StrategyStatus(str(nilai).strip().upper())
    except ValueError:
        log.warning("router.status_tak_dikenal", status=nilai)
        return _STATUS_ASING


def _katalog(sumber: Any) -> tuple[Any, ...]:
    if sumber is not None:
        return tuple(sumber)
    from aruna.learning.strategies import ALL

    # `ALL`, bukan `CATALOG`: ia memuat `UNMAPPED` (STR-000), dan
    # `kandidat_layak` yang membuangnya. Menyaring dua kali di dua tempat
    # berarti dua aturan yang harus tetap sepakat.
    return tuple(ALL)
