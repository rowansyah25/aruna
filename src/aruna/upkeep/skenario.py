"""Fase simulasi berpemicu (bagian 16.17).

Bagian 16.17 menggambar alurnya: MARKET DATA -> VALIDATION -> ARUNA AGENTS ->
EVENT DETECTOR -> MIROFISH TRIGGER -> SCENARIO SIMULATION -> SCENARIO EVIDENCE.
Modul ini menempati tiga kotak terakhir, dan ia disambungkan ke pemindai karena
di situlah kotak EVENT DETECTOR sudah berdiri: ``ScanResult`` adalah pendeteksi
peristiwa yang sudah ada, sudah terukur, dan sudah berjalan tiap siklus.

**Apa yang benar-benar tersambung, dan apa yang tidak.** Enam dari tiga belas
pemicu bagian 16.2 punya jalur data di titik ini - yang lahir dari pemindai:
breakout besar, breakdown besar, volume ekstrem, volatilitas abnormal, berita
besar, dan efek orde-dua yang diturunkan dari gabungan mereka. Tujuh sisanya
tidak, dan sebabnya berbeda-beda:

* ``PERUBAHAN_REGIME``, ``KETIDAKPASTIAN_TINGGI`` - regime dan skor mutu
  dihitung di jalur keputusan, sesudah fase ini berjalan.
* ``SELISIH_PENDAPAT_TAJAM`` - lahir di council, yang digelar per bar dan bukan
  per pemindaian.
* ``ANOMALI_FUNDING``, ``ANOMALI_OPEN_INTEREST`` - hidup di proses
  ``futures-loop`` yang terpisah.
* ``LONJAKAN_LIKUIDASI``, ``KONFLIK_LINTAS_PASAR`` - datanya memang belum
  dikumpulkan sama sekali.

Ditulis di sini alih-alih dibiarkan tersirat karena "pemicunya tidak pernah
menyala" dan "pemicunya tidak tersambung" terlihat sama persis dari lognya, dan
yang pertama adalah pasar yang tenang sementara yang kedua adalah pekerjaan yang
belum selesai.

**Batas sumber daya** (bagian 16.14). Satu simulasi pada satu waktu, dan paling
banyak :data:`BATAS_PER_SIKLUS` aset per siklus. Batas kedua yang menggigit:
pada hari pasar bergerak serentak, tiap simbol menyalakan pemicu, dan simulasi
untuk seluruhnya adalah persis ledakan yang bagian 16.14 larang.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aruna.core.enums import Horizon, Regime
from aruna.core.logging import get_logger
from aruna.council.protest import HIGH_DISAGREEMENT
from aruna.scanner.events import EventKind
from aruna.scenario.adapter import HasilAdapter, coba_simulasi
from aruna.scenario.bukti import BuktiSkenario, susun_bukti
from aruna.scenario.masukan import MasukanDitolak, susun_masukan
from aruna.scenario.mesin import simulasikan
from aruna.scenario.pemicu import (
    KonteksPemicu,
    Peristiwa,
    deteksi,
    layak_simulasi,
)
from aruna.scenario.pertanyaan import PertanyaanDitolak, susun_pertanyaan
from aruna.signals.quality import MIN_QUALITY
from aruna.upkeep.candles import bar_start

#: Interval yang menentukan "bar" untuk kunci satu-simulasi-per-bar. Sama
#: dengan yang dipindai (:class:`~aruna.scanner.service.ScannerService`).
INTERVAL_PEMICU = Horizon.M15

log = get_logger(__name__)

__all__ = [
    "BATAS_PER_SIKLUS",
    "HasilSiklus",
    "PenyimulasiSkenario",
]


#: Berapa aset yang boleh disimulasikan dalam satu siklus (bagian 16.14).
#:
#: Lima, dan angkanya punya dasar: universe ARUNA hari ini lima simbol, jadi
#: batas ini tidak memotong apa pun sekarang. Ia berdiri untuk universe yang
#: tumbuh - pada hari pasar bergerak serentak, tiap simbol menyalakan pemicu,
#: dan simulasi untuk seluruhnya adalah persis ledakan yang pasalnya larang.
#:
#: Yang dipotong dipilih menurut peristiwa terkuat, bukan urutan abjad, dan
#: yang tersisih **dicatat**.
BATAS_PER_SIKLUS = 5


class HasilSiklus:
    """Apa yang terjadi pada satu fase simulasi.

    Kelas, bukan tuple: nol simulasi karena tidak ada pemicu dan nol simulasi
    karena fasenya mati terlihat sama dari luar, dan bidang-bidang di bawah yang
    membedakannya.
    """

    __slots__ = (
        "bukti",
        "dipertimbangkan",
        "disimpan",
        "ditunda",
        "menyala",
        "sudah_disimulasikan",
    )

    def __init__(self) -> None:
        #: Berapa aset yang konteksnya diperiksa.
        self.dipertimbangkan = 0
        #: Berapa aset yang pemicunya menyala.
        self.menyala = 0
        #: Berapa aset yang pemicunya menyala tapi barnya sudah disimulasikan.
        self.sudah_disimulasikan = 0
        #: Berapa aset yang tersisih oleh BATAS_PER_SIKLUS.
        self.ditunda = 0
        #: Berapa baris skenario yang benar-benar tersimpan.
        self.disimpan = 0
        self.bukti: list[BuktiSkenario] = []


class PenyimulasiSkenario:
    """Menjalankan mesin skenario untuk aset yang pemicunya menyala.

    ``repo`` dan ``mesin_eksternal`` keduanya opsional, dan keduanya kosong
    adalah keadaan yang sah: tanpa repo, buktinya tetap dihasilkan dan tercatat
    di log tapi tidak tersimpan; tanpa mesin eksternal, statusnya ``DEGRADED``
    dan mesin internal tetap jalan (bagian 16.12).
    """

    def __init__(
        self,
        *,
        repo: Any = None,
        mesin_eksternal: Any = None,
        batas_per_siklus: int = BATAS_PER_SIKLUS,
        konteks: Any = None,
    ) -> None:
        self._repo = repo
        self._mesin = mesin_eksternal
        self._batas = batas_per_siklus
        #: Pembaca keadaan jalur keputusan
        #: (:class:`~aruna.db.repositories.konteks_pemicu.
        #: KonteksPemicuRepository`). ``None`` mengembalikan deteksi ke enam
        #: pemicu yang lahir dari pemindai saja.
        self._konteks = konteks
        #: Bacaan konteks siklus berjalan, per simbol.
        self._terakhir_konteks: dict[str, Any] = {}
        #: Arah kohort siklus berjalan. Milik seluruh pemindaian, bukan satu aset.
        self._terakhir_kohort: int | None = None
        #: Bar terakhir yang sudah disimulasikan, per simbol.
        #:
        #: **Satu simulasi per bar, bukan per siklus.** Pemindai berjalan tiap
        #: siklus dan menilai bar tertutup yang sama sampai bar berikutnya
        #: datang, jadi satu tembusan menyalakan pemicunya berulang kali -
        #: terukur di produksi 2026-08-22: satu tembusan AVAX/USDT tersimpan
        #: **empat kali**, dengan `scenario_id` berbeda karena stempel detiknya
        #: berbeda, sehingga `INSERT IGNORE` tidak menahannya.
        #:
        #: Ini mode kegagalan yang sama persis dengan `market_snapshots`, yang
        #: tumbuh menjadi 62% basis data karena tiap amatan ditulis apa adanya.
        #: Bedanya cuma satu: kali ini ketahuan pada hari pertama.
        #:
        #: Hidup di memori, jadi restart menyimulasikan ulang bar yang sedang
        #: berjalan satu kali. Itu satu baris ganda, bukan baris rusak - dan
        #: alternatifnya, membaca bar terakhir dari basis data tiap siklus,
        #: membeli penghematan langka dengan satu kueri per siklus selamanya.
        #: Trade-off yang sama sudah dipilih `_locked_bar` di `upkeep/loop.py`.
        self._bar_disimulasikan: dict[str, Any] = {}

    async def jalankan(
        self, hasil_pindai: list[Any], *, now: datetime
    ) -> HasilSiklus:
        """Satu fase simulasi atas hasil pemindaian.

        Tidak pernah melempar. Fase ini menghasilkan **bukti**, bukan
        keputusan - kegagalannya tidak boleh menjatuhkan siklus yang
        menghasilkan keputusan sungguhan.
        """
        keluar = HasilSiklus()
        konteks = await self._konteks_keputusan(now)
        # Disimpan supaya `_satu` bisa menyusun kondisi dari apa yang menyala,
        # bukan cuma dari peristiwa pemindai.
        self._terakhir_konteks = konteks
        # Dihitung sekali untuk seluruh siklus: arahnya milik kohort, bukan
        # milik satu aset, dan menghitungnya per aset akan menghasilkan dua
        # puluh jawaban untuk satu pertanyaan.
        kohort = self._terakhir_kohort = _arah_kohort(hasil_pindai)

        menyala: list[tuple[Any, frozenset, float]] = []
        for r in hasil_pindai:
            if not getattr(r, "scanned", False):
                continue
            keluar.dipertimbangkan += 1

            pemicu = deteksi(_konteks_untuk(r, konteks.get(r.symbol), kohort))
            if not layak_simulasi(pemicu):
                continue

            keluar.menyala += 1

            # Satu simulasi per bar, dikunci pada bar SIKLUS INI - bukan pada
            # stempel peristiwanya.
            #
            # Versi sebelumnya menulis `max(e.at for e in r.events)`, yang
            # mengandaikan tiap pemicu lahir dari peristiwa pemindai. Sejak
            # regime, mutu, dan selisih pendapat ikut menyalakan pemicu, aset
            # tanpa satu pun peristiwa pemindai bisa menyala - dan `max()` atas
            # daftar kosong melempar. Terukur di produksi 2026-08-22: delapan
            # `upkeep.scenario_failed` dalam lima menit.
            #
            # Bar siklus ini selalu ada, bergerak bersama bar peristiwanya, dan
            # tidak punya cabang kosong.
            bar = bar_start(now, INTERVAL_PEMICU)
            if self._bar_disimulasikan.get(r.symbol) == bar:
                keluar.sudah_disimulasikan += 1
                continue

            # Diurutkan menurut peristiwa terkuat supaya pemotongan di bawah
            # membuang yang paling sedikit berarti - bukan yang namanya paling
            # belakang di abjad.
            kuat = max((e.severity for e in r.events), default=0.0)
            menyala.append((r, pemicu, kuat))

        menyala.sort(key=lambda x: (-x[2], x[0].symbol))
        dijalankan, tersisih = menyala[: self._batas], menyala[self._batas :]
        keluar.ditunda = len(tersisih)

        if tersisih:
            # Bagian 16.14 membatasi; ia tidak membolehkan pembatasannya
            # disembunyikan. Aset yang hilang tanpa jejak terbaca sebagai aset
            # yang pemicunya tidak menyala.
            log.warning(
                "scenario.batas_siklus",
                menyala=len(menyala),
                dijalankan=len(dijalankan),
                tersisih=[r.symbol for r, _, _ in tersisih],
                batas=self._batas,
            )

        # Berurutan, bukan `gather`. Bagian 16.14 melarang CPU dan SQL overload,
        # dan lima simulasi serentak yang masing-masing menulis delapan baris
        # adalah persis bentuk lonjakan yang dilarangnya. Batasnya kecil, jadi
        # berurutan tidak memperlambat siklus secara berarti.
        for r, pemicu, _ in dijalankan:
            # Dicatat SEBELUM simulasinya, bukan sesudah. Simulasi yang gagal
            # tidak boleh dicoba ulang tiap tiga puluh detik sampai barnya
            # berganti - itu disiplin yang sama dengan fase-fase harian di
            # `upkeep/loop.py`, yang menstempel percobaan dan bukan
            # keberhasilan.
            self._bar_disimulasikan[r.symbol] = bar_start(now, INTERVAL_PEMICU)

            bukti = await self._satu(r, pemicu, now=now)
            if bukti is None:
                continue
            keluar.bukti.append(bukti)
            keluar.disimpan += await self._simpan(bukti)

        # Dicatat setiap siklus, termasuk yang nol. Nol karena tidak ada
        # peristiwa dan nol karena fasenya tidak pernah dipanggil terlihat sama
        # dari luar - dan yang pertama normal sementara yang kedua bug.
        log.info(
            "upkeep.skenario",
            dipertimbangkan=keluar.dipertimbangkan,
            menyala=keluar.menyala,
            sudah_disimulasikan=keluar.sudah_disimulasikan,
            ditunda=keluar.ditunda,
            disimpan=keluar.disimpan,
            # Prinsip yang sama, diterapkan pada `KONFLIK_LINTAS_PASAR`.
            # Terukur 2026-08-23: pemicu itu nol dari 3.048 baris, dan tanpa
            # angka ini "pasarnya tidak pernah berkonflik" tidak bisa dibedakan
            # dari "sambungannya putus". Keduanya terlihat sama dari luar, dan
            # yang pertama normal sementara yang kedua bug.
            #
            # `None` berarti kohortnya tidak punya arah - kurang dari
            # `MINIMUM_KOHORT` aset berarah, atau seri. Kalau angka ini SELALU
            # None, yang perlu diperiksa lantainya; kalau ia berarah dan
            # pemicunya tetap diam, tidak ada aset yang melawan.
            arah_kohort=kohort,
        )
        return keluar

    async def _konteks_keputusan(self, now: datetime) -> dict[str, Any]:
        """Bacaan jalur keputusan per simbol, atau kosong.

        Kegagalannya **mengecilkan** deteksi, tidak menghentikannya: tanpa
        konteks, enam pemicu yang lahir dari pemindai tetap bekerja. Fase yang
        mati total karena satu kueri gagal menukar sebagian bukti dengan tidak
        ada bukti sama sekali.
        """
        if self._konteks is None:
            return {}
        try:
            return await self._konteks.terbaru(sekarang=now)
        except Exception:
            log.exception("scenario.konteks_gagal")
            return {}

    async def _satu(
        self, hasil: Any, pemicu: frozenset, *, now: datetime
    ) -> BuktiSkenario | None:
        """Simulasi untuk satu aset. Kegagalannya berhenti di sini."""
        kondisi = _kondisi(
            hasil,
            pemicu,
            self._terakhir_konteks.get(hasil.symbol),
            arah_kohort=self._terakhir_kohort,
        )

        try:
            pertanyaan = susun_pertanyaan(
                aset=hasil.symbol, pemicu=pemicu, kondisi=kondisi
            )
            masukan = susun_masukan({
                "market_summary": f"{hasil.symbol}: {len(hasil.events)} peristiwa",
                "recent_price_structure": list(kondisi),
                "scenario_question": pertanyaan,
            })
        except (MasukanDitolak, PertanyaanDitolak) as galat:
            # Ditolak, bukan disimulasikan seadanya: bagian 16.3 dan 16.4 ada
            # justru untuk mencegah skenario rapi di atas masukan cacat.
            log.warning(
                "scenario.masukan_ditolak", symbol=hasil.symbol, sebab=str(galat)
            )
            return None

        try:
            internal = simulasikan(
                market=_pasar(hasil.symbol),
                asset=hasil.symbol,
                pemicu=pemicu,
                kondisi_awal=kondisi,
                bukti=tuple(
                    f"{e.kind.value}: {e.detail}" for e in hasil.events
                ),
                pada=now,
            )
        except Exception as galat:
            log.exception("scenario.mesin_gagal", symbol=hasil.symbol)
            _ = galat
            return None

        eksternal: HasilAdapter = await coba_simulasi(
            self._mesin, pertanyaan=pertanyaan, masukan=masukan.bidang
        )

        return susun_bukti(
            market=_pasar(hasil.symbol),
            asset=hasil.symbol,
            pada=now,
            pemicu=pemicu,
            internal=internal,
            eksternal=eksternal,
        )

    async def _simpan(self, bukti: BuktiSkenario) -> int:
        if self._repo is None:
            return 0
        try:
            internal = tuple(
                s for s in bukti.skenario if s.versi_simulasi.startswith("internal")
            )
            eksternal = tuple(s for s in bukti.skenario if s not in internal)
            n = await self._repo.simpan(internal, sumber="INTERNAL")
            if eksternal:
                # Dipisah sumbernya, bukan digabung: hasil dua mesin yang
                # dinilai dalam satu angka akurasi tidak mengatakan apa pun
                # tentang keduanya (bagian 16.19).
                n += await self._repo.simpan(eksternal, sumber="EKSTERNAL")
            return n
        except Exception:
            log.exception("scenario.simpan_gagal", asset=bukti.asset)
            return 0


#: Berapa aset berarah minimal sebelum kohortnya punya "arah".
#:
#: Tiga. Di bawah itu yang ada bukan pasar melainkan beberapa aset, dan sebuah
#: aset yang bergerak berlawanan dengan dua tetangganya belum berkonflik dengan
#: apa pun.
MINIMUM_KOHORT = 3


def _arah_kohort(hasil_pindai: list[Any]) -> int | None:
    """Arah yang sedang ditempuh mayoritas aset, atau ``None``.

    **Fase ini satu-satunya tempat yang memegang seluruh aset sekaligus.**
    Deteksi pemicu bekerja per aset, jadi ia tidak bisa melihat kohortnya
    sendiri - dan tanpa angka ini `KONFLIK_LINTAS_PASAR` tidak punya apa pun
    untuk dilawan.

    ``None`` ketika tidak ada mayoritas yang jelas, dan itu bukan nol: pasar
    yang tidak ke mana-mana tidak bisa dikonfliki siapa pun. Seri juga
    ``None`` - separuh naik separuh turun adalah pasar yang terbelah, dan
    keduanya sama benarnya.
    """
    naik = turun = 0
    for r in hasil_pindai:
        if not getattr(r, "scanned", False):
            continue
        for e in r.events:
            if e.kind is EventKind.BREAKOUT:
                naik += 1
                break
            if e.kind is EventKind.BREAKDOWN:
                turun += 1
                break

    if naik + turun < MINIMUM_KOHORT:
        return None
    if naik == turun:
        return None
    return 1 if naik > turun else -1


def _kondisi(
    hasil: Any,
    pemicu: frozenset,
    keputusan: Any,
    *,
    arah_kohort: int | None = None,
) -> tuple[str, ...]:
    """Kondisi konkret yang menyalakan simulasi ini.

    **Peristiwa pemindai saja tidak cukup lagi.** Sejak regime, mutu, dan
    selisih pendapat ikut menyalakan pemicu, sebuah aset bisa menyala tanpa satu
    pun peristiwa pemindai - dan `susun_pertanyaan` menolak pertanyaan tanpa
    kondisi (bagian 16.4). Tanpa baris-baris di bawah, ketiga pemicu yang baru
    disambungkan akan menyala lalu ditolak di langkah berikutnya, dan yang
    terlihat di log cuma "masukan ditolak".

    Tiap kalimat menyebut ANGKANYA, bukan cuma namanya: "mutu 42 di bawah
    ambang 60" bisa diperiksa, "mutu rendah" tidak.
    """
    keluar = [e.detail for e in hasil.events]

    # Sengaja di atas pintu keluar `keputusan is None`: konflik kohort tidak
    # butuh jalur keputusan sama sekali, dan aset yang menyala karenanya sering
    # justru yang belum punya keputusan cukup baru.
    if Peristiwa.KONFLIK_LINTAS_PASAR in pemicu and arah_kohort:
        kemana = "menembus ke atas" if arah_kohort > 0 else "terjun"
        keluar.append(f"bergerak melawan kohortnya, yang mayoritasnya {kemana}")

    if keputusan is None:
        return tuple(keluar)

    if Peristiwa.PERUBAHAN_REGIME in pemicu:
        keluar.append(
            f"regime berpindah dari {keputusan.regime_sebelumnya} "
            f"ke {keputusan.regime_sekarang}"
        )
    if Peristiwa.KETIDAKPASTIAN_TINGGI in pemicu and keputusan.mutu is not None:
        keluar.append(
            f"mutu sinyal {keputusan.mutu} di bawah ambang {MIN_QUALITY}"
        )
    if (
        Peristiwa.SELISIH_PENDAPAT_TAJAM in pemicu
        and keputusan.disagreement is not None
    ):
        keluar.append(
            f"selisih pendapat antar-agent {keputusan.disagreement:.2f} "
            f"melewati ambang {HIGH_DISAGREEMENT:.2f}"
        )
    return tuple(keluar)


def _regime(nilai: str | None) -> Regime | None:
    """``Regime`` dari teks yang tersimpan, atau ``None``.

    Baris lama memuat nilai yang sudah tidak ada di enum - taksonomi berarah
    masuk 2026-08-21. Yang tidak dikenal diperlakukan tidak terbaca, bukan
    melempar: satu baris lawas tidak boleh menjatuhkan deteksi pemicu.
    """
    if not nilai:
        return None
    try:
        return Regime(str(nilai).strip().upper())
    except ValueError:
        return None


def _konteks_untuk(
    hasil: Any, keputusan: Any, arah_kohort: int | None = None
) -> KonteksPemicu:
    """Gabungkan peristiwa pemindai dengan keadaan jalur keputusan.

    Yang tidak terbaca tetap ``None`` - bukan nol. Bedanya menentukan:
    ``mutu=None`` berarti tidak ada keputusan yang cukup baru untuk aset ini,
    sedangkan ``mutu=0`` berarti keputusannya buruk sekali, dan yang kedua
    menyalakan ``KETIDAKPASTIAN_TINGGI`` sementara yang pertama tidak boleh.
    """
    if keputusan is None:
        return KonteksPemicu(
            peristiwa_pindai=tuple(hasil.events), arah_kohort=arah_kohort
        )

    return KonteksPemicu(
        peristiwa_pindai=tuple(hasil.events),
        arah_kohort=arah_kohort,
        regime_sekarang=_regime(keputusan.regime_sekarang),
        regime_sebelumnya=_regime(keputusan.regime_sebelumnya),
        mutu=keputusan.mutu,
        disagreement=keputusan.disagreement,
        funding_rate=keputusan.funding_rate,
        perubahan_oi_pct=keputusan.perubahan_oi_pct,
    )


def _pasar(symbol: str) -> str:
    """Kode pasar dari simbolnya.

    Kasar dengan sengaja: yang dibutuhkan tabel skenario hanya pemisah CRYPTO
    dari IDX, dan simbol crypto di ARUNA selalu berakhiran quote asing.
    """
    return "CRYPTO" if symbol.upper().endswith(("USDT", "USD", "BUSD")) else "IDX"
