"""Bentuk akhir yang sampai ke operator (PASAL 14.26, 14.27, 14.28).

Tiga bentuk: signal berarah, NO SIGNAL, dan konflik antar timeframe. Ketiganya
disusun dari blok yang sudah dihasilkan modul-modul lain di paket ini, bukan
ditulis ulang di sini - sebuah pesan yang mengeja ulang angka yang dihitung di
tempat lain adalah tempat kedua angka itu bisa salah.

**Pesan berarah tanpa entry, stop, target, horizon, atau syarat pembatalan
ditolak - bukan dikirim dengan bagian yang kosong.** Operator: *"kalau ga di
dorong sebagai sinyal gausah di kirim, hanya sinyal valid yang di kirim dan
timeframe wajib."* Sebuah signal yang kehilangan salah satunya tidak bisa
ditindaklanjuti: entry tanpa stop tidak bisa diukur risikonya, dan arah tanpa
horizon tidak bisa dijawab dengan timeframe mana pun.

**Dua penjaga terakhir dijalankan pada teks jadi, bukan pada templatnya.**
Kosakata internal (PASAL 1) dan klaim terlarang (PASAL 51) bisa masuk lewat
kalimat yang disisipkan lapisan mana pun - alasan council, catatan strategi,
kutipan berita. Yang diperiksa adalah apa yang benar-benar akan terkirim.

**Kaki ANALYST ONLY wajib, dan bukan hiasan.** PASAL 14.26 menutup pesannya
dengan *"ARUNA: ANALYST ONLY / EXECUTION: USER"*, dan PASAL 14.44 menyatakan
kenapa: ARUNA bukan execution bot. Sebuah pesan berisi entry, stop, dan
leverage yang sampai tanpa kalimat itu terbaca sebagai perintah.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from aruna.core.claims import find_forbidden
from aruna.decision.explanation import Penjelasan
from aruna.decision.invalidation import Invalidasi
from aruna.decision.score import Arah, Skor
from aruna.decision.timeframes import Lintas
from aruna.decision.timing import Rencana
from aruna.notify.verdict import guard_public

GARIS = "━" * 20

#: Kaki wajib PASAL 14.26. Dua baris, bukan satu paragraf: kalimat panjang di
#: kaki pesan dilewati mata, dan yang harus tersisa dari pembacaan sekilas
#: adalah siapa yang menganalisis dan siapa yang mengeksekusi.
KAKI: tuple[str, ...] = ("ARUNA: ANALYST ONLY", "EKSEKUSI: PENGGUNA")


class OutputError(ValueError):
    """Pesan yang tidak layak dikirim."""


@dataclass(frozen=True, slots=True)
class Berkas:
    """Semua bahan satu pesan akhir.

    Sebagian besar bidangnya boleh kosong: sebuah pesan yang menahan angka
    keputusannya karena satu blok analisis tidak sampai akan menghilangkan
    justru bagian yang diminta operator. Yang **tidak** boleh kosong ada di
    :meth:`_wajib` - dan daftarnya pendek dengan sengaja.
    """

    symbol: str
    market: str
    decision: Arah
    horizon: str

    # ---- hanya untuk keputusan berarah ----
    entry: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    invalidation: Invalidasi | None = None
    timing: Rencana | None = None

    # ---- analisis ----
    quality: int | None = None
    confidence: float | None = None
    regime: str | None = None
    score: Skor | None = None
    lintas: Lintas | None = None
    explanation: Penjelasan | None = None

    # ---- council (PASAL 14.26, 14.28) ----
    setuju: tuple[str, ...] = field(default_factory=tuple)
    kontra: tuple[str, ...] = field(default_factory=tuple)
    #: Penentang beserta alasannya, dan sanggahannya (PASAL 14.28).
    oposisi: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    sanggahan: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    # ---- risiko ----
    risk_line: str | None = None
    rr: str | None = None
    leverage: str | None = None

    #: Kenapa NO SIGNAL (PASAL 14.27). Wajib kalau keputusannya NO SIGNAL.
    reason: str = ""

    # ------------------------------------------------------------------

    def _wajib(self) -> None:
        if not self.horizon.strip():
            raise OutputError(
                f"{self.symbol}: horizon wajib - arah tanpa horizon tidak bisa "
                f"dijawab dengan timeframe mana pun"
            )
        if self.decision is Arah.NO_SIGNAL:
            if not self.reason.strip():
                raise OutputError(
                    f"{self.symbol}: NO SIGNAL wajib menyebut sebabnya "
                    f"(PASAL 14.27)"
                )
            return
        kurang = [
            nama
            for nama, nilai in (
                ("entry", self.entry),
                ("stop loss", self.stop),
                ("take profit", self.target),
                ("syarat pembatalan", self.invalidation),
            )
            if nilai is None
        ]
        if kurang:
            raise OutputError(
                f"{self.symbol}: {self.decision.value} tanpa "
                f"{', '.join(kurang)} - tidak bisa ditindaklanjuti, jadi tidak "
                f"dikirim"
            )

    def render(self) -> str:
        """Pesan jadi, sudah lewat kedua penjaga."""
        self._wajib()
        baris = (
            self._no_signal()
            if self.decision is Arah.NO_SIGNAL
            else self._signal()
        )
        baris += ["", GARIS, "", *KAKI]
        teks = "\n".join(baris)

        terlarang = find_forbidden(teks)
        if terlarang:
            raise OutputError(
                f"menolak mengirim pesan yang memuat {terlarang[0]!r} "
                f"(PASAL 51)"
            )
        return guard_public(teks)

    # ---- PASAL 14.26 --------------------------------------------------

    def _signal(self) -> list[str]:
        baris = [
            "🔮 KEPUTUSAN AKHIR ARUNA",
            GARIS,
            "",
            f"ASET:     {self.symbol}",
            f"PASAR:    {self.market}",
            "",
            f"🎯 KEPUTUSAN: {self.decision.mark} {self.decision.value}",
            f"⏱ HORIZON:   {self.horizon}",
            "",
            f"💰 ENTRY:       {self.entry}",
            f"🛑 STOP LOSS:   {self.stop}",
            f"🎯 TAKE PROFIT: {self.target}",
        ]
        if self.timing is not None and self.timing.waiting:
            # Ditaruh tepat di bawah harganya. Sebuah catatan waktu masuk yang
            # terpisah dari angkanya akan terbaca sesudah operator selesai
            # membaca angka - yaitu sesudah ia memutuskan.
            #
            # Barisnya disusun di sini alih-alih memotong ``Rencana.report()``:
            # versi sebelumnya membuang dua baris pertamanya dengan potongan
            # ``[2:]``, dan potongan sebesar itu akan mengiris kalimat yang
            # salah begitu blok asalnya bertambah satu baris.
            baris += ["", f"⏳ WAKTU MASUK: {self.timing.timing.value}"]
            baris.append("   Arahnya tidak berubah; yang belum pas waktunya.")
            if self.timing.condition is not None:
                baris.append(f"   Syarat: {self.timing.condition.line()}")
                baris.append("   Kalau tidak terjadi, signal ini kedaluwarsa.")
        baris += self._analisis() + self._council() + self._risiko()
        if self.invalidation is not None:
            baris += ["", GARIS, ""]
            baris += self.invalidation.report()
        return baris

    # ---- PASAL 14.27 --------------------------------------------------

    def _no_signal(self) -> list[str]:
        """Tanpa harga, tanpa leverage, dan tanpa kata ``WAIT``.

        PASAL 14.27 menutupnya dengan *"Jangan mengirim WAIT sebagai final
        decision."* Penjaganya bukan di sini melainkan di
        :func:`guard_public`, yang memeriksa teks jadi - jadi kata itu tidak
        bisa masuk lewat kalimat sebab yang disusun lapisan lain.
        """
        baris = [
            "⚪ KEPUTUSAN AKHIR ARUNA",
            GARIS,
            "",
            f"ASET:      {self.symbol}",
            f"PASAR:     {self.market}",
            f"⏱ HORIZON: {self.horizon}",
            "",
            f"KEPUTUSAN: {Arah.NO_SIGNAL.mark} {Arah.NO_SIGNAL.value}",
            "",
            f"SEBAB: {self.reason.strip()}",
        ]
        return baris + self._analisis() + self._council()

    # ---- blok bersama --------------------------------------------------

    def _analisis(self) -> list[str]:
        isi: list[str] = []
        if self.quality is not None:
            isi.append(f"Signal Quality: {self.quality}/100")
        if self.confidence is not None:
            isi.append(f"Confidence:     {self.confidence:.0%}")
        if self.regime:
            isi.append(f"Rezim Pasar:    {self.regime}")
        if self.score is not None and self.score.usable:
            # Angkanya tidak pernah tanpa keterangannya - lihat PASAL 14.16.
            isi.append(f"Decision Score: {self.score.value:+.0f} (bukan peluang profit)")
        if self.explanation is not None:
            isi += ["", *self.explanation.report()]
        if self.lintas is not None:
            isi += ["", *self.lintas.report()]
        if not isi:
            return []
        return ["", GARIS, "", "🧠 ANALISIS", "", *isi]

    def _council(self) -> list[str]:
        if not (self.setuju or self.kontra or self.oposisi):
            return []
        isi: list[str] = []
        if self.setuju:
            isi += ["SETUJU:", *(f"  {a}" for a in self.setuju)]
        if self.kontra:
            if isi:
                isi.append("")
            isi += ["KONTRA:", *(f"  {a}" for a in self.kontra)]
        for siapa, kenapa in self.oposisi:
            isi += ["", f"OPOSISI: {siapa}", f"  {kenapa}"]
        for siapa, kenapa in self.sanggahan:
            isi += ["", f"SANGGAHAN: {siapa}", f"  {kenapa}"]
        return ["", GARIS, "", "🗳 COUNCIL", "", *isi]

    def _risiko(self) -> list[str]:
        isi = [
            f"{label} {nilai}"
            for label, nilai in (
                ("Risiko:", self.risk_line),
                ("Risk/Reward:", self.rr),
                ("Leverage:", self.leverage),
            )
            if nilai
        ]
        if not isi:
            return []
        return ["", GARIS, "", "🛡 RISIKO", "", *isi]


__all__ = ["GARIS", "KAKI", "Berkas", "OutputError"]
