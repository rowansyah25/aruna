"""Phase 12 dan Phase 13 yang dibawa ke keputusan (PASAL 14.40, 14.41).

Terukur di produksi 2026-08-20: Phase 12 hanya **22%** sampai ke keputusan.
Pattern discovery, spesialisasi agent, champion, challenger, drift - semuanya
sudah dibangun berbulan-bulan, tersimpan di database, dan tidak satu pun dibaca
oleh lapisan yang memutuskan. Korelasi sama nasibnya: mesinnya ada sejak
Phase 4, tabelnya terisi lewat CLI, dan ``DecisionContext.correlation``
**tidak pernah diisi di mana pun** - termasuk jalur spot.

Ini keluarga cacat yang sama dengan delapan modul ``aruna.decision`` yang
diam - hanya satu tingkat lebih besar: bukan modul yang tidak dipanggil,
melainkan seluruh fase.

**Dibaca sekali per jendela, bukan sekali per simbol.** Dua puluh simbol tiap
lima belas menit dikali enam pertanyaan adalah seratus dua puluh kueri per tick
untuk data yang berubah dalam hitungan jam. Polanya menyalin
:class:`aruna.learning.strategist.Strategist`, yang sudah memecahkan masalah
yang sama - dan cache-nya berkunci pasar **dan** interval, karena korelasi
CRYPTO bukan korelasi IDX.

**Kegagalannya kosong, bukan meledak.** Sebuah lapisan pembelajaran yang
menjadi syarat agar council bisa memutuskan akan mengubah kegagalan
pembelajaran menjadi kegagalan analisis - dan tiap bagian ditangkap
sendiri-sendiri, supaya satu tabel yang bermasalah tidak menghapus lima
lapisan yang sehat.

**Yang tidak ada tetap kosong.** Tidak ada satu pun nilai di sini yang dikarang
kalau sumbernya diam (§13.26): ``challenger`` yang kosong berarti tidak ada
penantang, bukan penantang bernama UNKNOWN.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from aruna.core.logging import get_logger

log = get_logger("aruna.learning.snapshot")

#: Lima menit, sama dengan :data:`aruna.learning.strategist.CACHE_TTL_SEC`.
#:
#: Sejarah bertambah pelan - satu prediksi terskor tiap beberapa menit - jadi
#: jawabannya tidak akan berubah lebih cepat dari itu. Cache tanpa masa berlaku
#: adalah data yang membeku pada saat proses menyala, dan sebuah usulan model
#: yang baru diputuskan operator tidak akan pernah terlihat tanpa restart.
CACHE_TTL_SEC = 300.0

#: Status usulan yang berarti "masih menantang".
#:
#: **Diambil dari :class:`~aruna.governance.proposal.ProposalStatus`, bukan
#: dikarang.** Versi pertama daftar ini berbunyi ``{"PENDING", "APPROVED"}`` -
#: dua nilai yang tidak pernah ada di sistem ini. Akibatnya challenger selalu
#: kosong padahal ada tiga usulan tersimpan, dan diamnya terbaca persis seperti
#: "tidak ada penantang".
#:
#: ``REJECTED`` dan ``ABANDONED`` sudah selesai; ``APPROVED`` sudah menang dan
#: menjadi champion. Ketiganya bukan penantang.
def _menantang() -> frozenset[str]:
    from aruna.governance.proposal import ProposalStatus

    return frozenset({
        ProposalStatus.DRAFT.value,
        ProposalStatus.SHADOWED.value,
        ProposalStatus.VALIDATED.value,
        ProposalStatus.AWAITING_APPROVAL.value,
    })


MENANTANG: frozenset[str] = _menantang()


@dataclass(frozen=True, slots=True)
class Pembelajaran:
    """Apa yang Phase 12 dan Phase 13 punya untuk keputusan ini."""

    #: Kunci pola yang layak disebut (PASAL 14.40).
    patterns: tuple[str, ...] = ()
    #: Rezim -> agent yang paling sering benar di rezim itu.
    specialists: dict[str, str] = field(default_factory=dict)
    #: Versi model yang sedang memutuskan. SPEC 37 punya enum
    #: ``ModelRole.CHAMPION`` dan tidak satu pun baris yang menyimpannya; yang
    #: benar-benar berlaku adalah versi yang sedang jalan.
    champion: str = ""
    #: Usulan yang belum diputuskan atau sudah disetujui tapi belum menggantikan.
    challenger: str = ""
    #: Pemeriksaan drift terakhir, atau ``None`` kalau belum pernah ada.
    drift: dict[str, Any] | None = None
    #: Pasangan berkorelasi terkuat di pasar ini (PASAL 14.41).
    correlation: tuple[dict[str, Any], ...] = ()
    #: Lintasan backtest terakhir (PASAL 14.40) - walk-forward dan holdout.
    #:
    #: Yang dibaca darinya hanya **keberadaan validasinya**. Angkanya tidak
    #: berubah antar tick, dan menyuapkannya per keputusan akan membuat pesan
    #: membawa angka yang terlihat relevan padahal tidak.
    backtest: dict[str, Any] | None = None
    #: Vonis kalibrasi terakhir (bagian 18.45), atau kalimat kosong.
    #:
    #: Kalimat penuh, bukan satu kata. Bagian 18.45 mencontohkan
    #: ``Calibration: GOOD``, dan satu kata itu membuang justru bagian yang
    #: bisa ditindaklanjuti: terukur pada 2026-08-24, vonisnya berbunyi
    #: "OVERCONFIDENT in 50-65%, 65-80%, 80-96%" - tiga pita yang **spesifik**,
    #: dan pembaca yang keyakinannya jatuh di 70% berhak tahu bahwa pitanya
    #: termasuk yang terlalu percaya diri.
    #:
    #: Dibaca sekali per jendela lima menit bersama lapisan lain di atas, bukan
    #: per simbol: kalibrasi adalah sifat sistem, bukan sifat BTCUSDT.
    kalibrasi: str = ""

    @property
    def ada(self) -> bool:
        """Apakah ada satu pun lapisan yang benar-benar terbaca."""
        return bool(
            self.patterns
            or self.specialists
            or self.champion
            or self.challenger
            or self.drift
            or self.correlation
            or self.backtest
            or self.kalibrasi
        )


@dataclass(slots=True)
class PembacaPembelajaran:
    """Membaca Phase 12 dan Phase 13 sekali per jendela, bukan per simbol."""

    learning12: Any = None
    governance: Any = None
    correlation: Any = None
    #: Repositori backtest (PASAL 14.40). Opsional: pemanggil tanpa dia
    #: menghasilkan snapshot tanpa bagian validasi, bukan snapshot yang gagal.
    backtest: Any = None
    model_version: str = ""
    ttl_sec: float = CACHE_TTL_SEC
    _cache: dict[tuple[str, str], tuple[float, Any]] = field(
        default_factory=dict
    )

    async def baca(self, *, market: Any, interval: Any) -> Pembelajaran:
        """Snapshot untuk pasar dan interval ini, dibaca sekali per jendela.

        **Yang disimpan adalah pembacaan yang sedang berjalan, bukan hanya
        hasilnya.** Terukur di produksi 2026-08-20: dua puluh kegagalan identik
        dalam delapan puluh milidetik. Loop futures menjalankan simbolnya
        serentak, jadi kedua puluh pemanggil sampai di cache sebelum ada satu
        pun yang selesai mengisinya - dan cache yang hanya menyimpan hasil
        tidak menahan serbuan itu sama sekali.
        """
        kunci = (str(getattr(market, "value", market)),
                 str(getattr(interval, "value", interval)))
        sekarang = monotonic()
        simpan = self._cache.get(kunci)
        if simpan is not None and sekarang - simpan[0] < self.ttl_sec:
            return await simpan[1]

        tugas = asyncio.ensure_future(self._susun(market, interval))
        self._cache[kunci] = (sekarang, tugas)
        try:
            return await tugas
        except Exception:
            # Pembacaan yang gagal tidak boleh membekukan cache selama lima
            # menit - tick berikutnya harus boleh mencoba lagi.
            self._cache.pop(kunci, None)
            log.exception("snapshot.baca_gagal")
            return Pembelajaran(champion=self.model_version or "")

    async def _susun(self, market: Any, interval: Any) -> Pembelajaran:
        return Pembelajaran(
            patterns=await self._patterns(),
            specialists=await self._specialists(),
            champion=self.model_version or "",
            challenger=await self._challenger(),
            drift=await self._drift(),
            correlation=await self._correlation(market, interval),
            backtest=await self._backtest(),
            kalibrasi=await self._kalibrasi(),
        )

    async def _kalibrasi(self) -> str:
        """Vonis kalibrasi terakhir (bagian 18.45), atau kalimat kosong.

        Kalimat kosong berarti **belum pernah diukur**, dan itu yang dicetak
        pemanggilnya - bukan "GOOD". Sebuah sistem yang belum pernah memeriksa
        kejujurannya sendiri bukan sistem yang terkalibrasi baik.
        """
        if self.learning12 is None:
            return ""
        try:
            baris = await self.learning12.latest_calibration()
        except Exception:
            log.exception("snapshot.kalibrasi_unavailable")
            return ""
        return str((baris or {}).get("verdict") or "")

    async def _backtest(self) -> dict[str, Any] | None:
        """Lintasan backtest terakhir (PASAL 14.40), atau ``None``.

        Sampai 2026-08-21 ini selalu ``None`` di produksi - bukan karena
        mesinnya tidak ada, tapi karena ``aruna backtest`` menghitung fold
        walk-forward lalu membuangnya, dan ``backtest_runs`` berisi nol baris
        sepanjang umur sistem.

        **Lewat ``validasi_terakhir``, bukan ``recent_runs``.** Yang kedua
        menyaring rezim biaya dan kolomnya hanya PnL - ``walk_forward`` dan
        ``holdout_included`` tidak ada di sana sama sekali. Versi pertama
        memakainya, testnya hijau karena palsunya memulangkan apa yang
        diinginkan test, dan di produksi kedua masukan tetap hilang.
        """
        if self.backtest is None:
            return None
        try:
            return await self.backtest.validasi_terakhir()
        except Exception:
            log.exception("snapshot.backtest_unavailable")
            return None

    # -- tiap bagian ditangkap sendiri-sendiri ----------------------------

    async def _patterns(self) -> tuple[str, ...]:
        if self.learning12 is None:
            return ()
        try:
            from aruna.learning.adaptive import LEARNING_VERSION

            # ``model_version`` adalah kata kunci WAJIB di repositori yang
            # sungguhan. Versi pertama baris ini memanggilnya tanpa argumen,
            # lolos seluruh test karena palsunya menerima ``**kw``, lalu gagal
            # dua puluh kali pada tick pertama di produksi.
            #
            # Versinya adalah versi **mesin pembelajaran**, bukan versi
            # aplikasi. Pola tersimpan di bawah `learn-12.0`; mencarinya dengan
            # versi app menghasilkan nol baris dari tabel berisi 365 - kosong
            # yang terbaca seperti "belum ada pola", bukan seperti kunci salah.
            baris = await self.learning12.notable_patterns(
                model_version=LEARNING_VERSION
            )
        except Exception:
            log.exception("snapshot.patterns_unavailable")
            return ()
        return tuple(
            str(r.get("pattern_key"))
            for r in baris or ()
            if r.get("pattern_key")
        )

    async def _specialists(self) -> dict[str, str]:
        if self.learning12 is None:
            return {}
        try:
            from aruna.learning.specialization import (
                Vote,
                build_profiles,
                specialists,
            )

            baris = await self.learning12.agent_votes()
        except Exception:
            log.exception("snapshot.votes_unavailable")
            return {}
        try:
            suara = [
                Vote(
                    role=str(r.get("role") or ""),
                    regime=str(r.get("regime") or ""),
                    agreed=bool(r.get("agreed_with_council")),
                    abstained=bool(r.get("abstained")),
                    won=str(r.get("result") or "") == "WIN",
                )
                for r in baris or ()
            ]
            return specialists(build_profiles(suara))
        except Exception:
            log.exception("snapshot.specialists_failed")
            return {}

    async def _challenger(self) -> str:
        if self.governance is None:
            return ""
        try:
            baris = await self.governance.proposals(limit=20)
        except Exception:
            log.exception("snapshot.proposals_unavailable")
            return ""
        for r in baris or ():
            if str(r.get("status") or "").upper() in MENANTANG:
                return str(r.get("proposal_key") or "")
        return ""

    async def _drift(self) -> dict[str, Any] | None:
        if self.governance is None:
            return None
        try:
            return await self.governance.latest_drift()
        except Exception:
            log.exception("snapshot.drift_unavailable")
            return None

    async def _correlation(
        self, market: Any, interval: Any
    ) -> tuple[dict[str, Any], ...]:
        """Pasangan berkorelasi terkuat di pasar ini.

        ``market`` diteruskan **apa adanya**, bukan sebagai teks: repositori
        yang sungguhan memanggil ``market.value``. Versi pertama meneruskan
        kunci cache yang sudah jadi string, dan gagal dua puluh kali pada tick
        pertama di produksi.
        """
        if self.correlation is None:
            return ()
        try:
            nama = str(getattr(interval, "value", interval))
            baris = await self.correlation.latest(market, nama, limit=10)
        except Exception:
            log.exception("snapshot.correlation_unavailable")
            return ()
        return tuple(dict(r) for r in baris or ())


__all__ = ["CACHE_TTL_SEC", "MENANTANG", "PembacaPembelajaran", "Pembelajaran"]

