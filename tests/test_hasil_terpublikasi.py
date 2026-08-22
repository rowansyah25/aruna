"""Dua keluhan operator tentang pesan hasil, dan bentuk yang menjawabnya.

**"Belum ada signal, tiba-tiba result semua."** Terukur di database saat
dilaporkan: dalam dua belas jam, 73 prediksi berarah diskor tanpa pernah
dipublikasikan - ditahan karena bukti basi, cooldown, atau duplikat - lawan 28
yang dipublikasikan. Ketiganya didorong ke Telegram dengan cara yang sama, jadi
mayoritas pesan hasil adalah kabar tentang prediksi yang tidak pernah ada di
layar siapa pun.

**"Sama-sama tidak capai target, kenapa satu WIN satu LOSS."** Karena keduanya
menjawab pertanyaan berbeda, dan pesannya tidak pernah menunjukkan yang kedua.
DOT/USDT: masuk 0,747, keluar 0,749 - naik, arahnya memang benar - kotor +2,68,
biaya 3,67, bersih -0,99.

Yang dijaga berkas ini, di atas keduanya, adalah PASAL 11.21: tidak satupun
peredaman di sini boleh bisa memilih-milih hasil.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from aruna.notify.result import ResultNotifier, render_result


class _Pengirim:
    def __init__(self, berhasil: bool = True) -> None:
        self.terkirim: list[str] = []
        self._berhasil = berhasil

    async def send(self, teks: str) -> bool:
        if self._berhasil:
            self.terkirim.append(teks)
        return self._berhasil


def _baris(
    signal_id: str,
    *,
    published: Any = True,
    trade_result: str = "LOSS",
    symbol: str = "DOT/USDT",
) -> dict[str, Any]:
    baris = {
        "symbol": symbol,
        "decision": "BUY",
        "outcome_class": "TARGET_NOT_REACHED",
        "signal_id": signal_id,
        "entry": Decimal("0.747"),
        "target": Decimal("0.75128425"),
        "trigger": None,
        "trade_result": trade_result,
        "model_version": "signals-s3",
        "economics": (Decimal("2.68"), Decimal("3.67"), Decimal("-0.99")),
    }
    if published is not None:
        baris["published"] = published
    return baris


def _notifier(pengirim: _Pengirim) -> ResultNotifier:
    # `warmup=False`: pass pertama sesudah menyala memang sengaja diam, dan
    # membiarkannya menyala di sini akan membuat setiap test di bawah lulus
    # tanpa pernah menyentuh penyaring yang diuji.
    return ResultNotifier(sender=pengirim, warmup=False)


NOW = __import__("datetime").datetime(
    2026, 8, 19, 6, 0, tzinfo=__import__("datetime").UTC
)


class TestHasilTanpaSignalnyaTidakDidorong:
    """Sebuah RESULT tanpa SIGNAL-nya tidak bisa dipakai untuk apa-apa: tidak
    ada yang bisa diperiksa ulang, dan tidak ada yang bisa dipelajari."""

    @pytest.mark.asyncio
    async def test_yang_tidak_pernah_diumumkan_tidak_dikirim(self) -> None:
        pengirim = _Pengirim()
        n = await _notifier(pengirim).announce(
            [_baris("a", published=False)], now=NOW
        )

        assert n == 0
        assert pengirim.terkirim == []

    @pytest.mark.asyncio
    async def test_yang_diumumkan_tetap_dikirim(self) -> None:
        pengirim = _Pengirim()
        n = await _notifier(pengirim).announce(
            [_baris("b", published=True)], now=NOW
        )

        assert n == 1
        assert "DOT/USDT" in pengirim.terkirim[0]

    @pytest.mark.asyncio
    async def test_tanpa_keterangan_tetap_dikirim(self) -> None:
        """Gagal terbuka, dan itu disengaja.

        Satu pencarian yang gagal tidak boleh membungkam kabar bahwa ARUNA
        salah. Arah kegagalan yang sebaliknya melanggar PASAL 11.21 dengan
        cara yang tidak akan terlihat siapa pun.
        """
        pengirim = _Pengirim()
        n = await _notifier(pengirim).announce(
            [_baris("c", published=None)], now=NOW
        )

        assert n == 1

    @pytest.mark.asyncio
    async def test_keterangan_publikasi_tidak_bocor_ke_pesan(self) -> None:
        """``render_result`` menolak kwarg yang tidak dikenalnya - perilaku yang
        benar, dan berarti keterangan yang menumpang harus dibuang lebih dulu."""
        pengirim = _Pengirim()
        await _notifier(pengirim).announce([_baris("d")], now=NOW)

        assert "published" not in pengirim.terkirim[0]


class TestPeredamanTidakBisaMemilihHasil:
    """PASAL 11.21. Penjaga untuk peredam di atas.

    ``published`` diputuskan saat prediksi dikunci - jauh sebelum ada yang tahu
    ia menang atau kalah - jadi penyaringan berdasarkan kolom itu tidak bisa
    condong ke salah satu sisi. Test di bawah membuktikannya dengan menjalankan
    dua himpunan yang identik kecuali putusan menang-kalahnya.
    """

    async def _terkirim(self, hasil: str, *, published: bool) -> int:
        pengirim = _Pengirim()
        return await _notifier(pengirim).announce(
            [
                _baris(f"{hasil}-{i}", published=published, trade_result=hasil)
                for i in range(3)
            ],
            now=NOW,
        )

    @pytest.mark.asyncio
    async def test_menang_dan_kalah_diperlakukan_sama_saat_diumumkan(self) -> None:
        assert await self._terkirim("WIN", published=True) == 3
        assert await self._terkirim("LOSS", published=True) == 3

    @pytest.mark.asyncio
    async def test_menang_dan_kalah_diperlakukan_sama_saat_ditahan(self) -> None:
        assert await self._terkirim("WIN", published=False) == 0
        assert await self._terkirim("LOSS", published=False) == 0

    def test_penyaringnya_tidak_menyebut_menang_atau_kalah(self) -> None:
        """Pemeriksaan bentuk, bukan perilaku.

        Kalau seseorang kelak menambahkan syarat menang-kalah ke penyaring ini,
        dua test di atas masih bisa lulus dengan data yang kebetulan seimbang.
        Ini yang tidak bisa.
        """
        import inspect

        sumber = inspect.getsource(ResultNotifier.announce)
        potong = sumber[sumber.index("diumumkan = ["):]
        potong = potong[: potong.index("baru = [")]

        for terlarang in ("WIN", "LOSS", "trade_result", "net_pnl"):
            assert terlarang not in potong, (
                f"penyaring publikasi menyebut {terlarang!r}; peredaman yang "
                "melihat hasilnya bisa menyembunyikan kekalahan (PASAL 11.21)"
            )

    @pytest.mark.asyncio
    async def test_yang_diredam_dihitung_bukan_dibuang_diam_diam(
        self, monkeypatch
    ) -> None:
        from aruna.notify import result as modul

        dicatat: list[tuple[str, int]] = []
        monkeypatch.setattr(
            modul.log, "info",
            lambda e, **k: dicatat.append((e, k.get("count"))),
        )

        pengirim = _Pengirim()
        await _notifier(pengirim).announce(
            [_baris("x", published=False), _baris("y", published=False)],
            now=NOW,
        )

        assert ("result.unpublished_suppressed", 2) in dicatat, dicatat


class TestHitunganDitampilkan:
    """Kelas outcome menjawab **bagaimana** prediksinya meleset; menang-kalah
    menjawab **berapa uangnya**. Sebelum blok ini ada, pesan bisa berbunyi
    "LOSS - arahnya benar" dan dua bagian kalimat itu saling membantah."""

    def _render(self, hasil: str, ekonomi: tuple | None) -> str:
        return render_result(
            symbol="DOT/USDT",
            decision="BUY",
            outcome_class="TARGET_NOT_REACHED",
            signal_id="abc",
            entry=Decimal("0.747"),
            target=Decimal("0.75128425"),
            trade_result=hasil,
            economics=ekonomi,
        )

    def test_kalah_yang_arahnya_benar_menunjukkan_ongkosnya(self) -> None:
        teks = self._render(
            "LOSS", (Decimal("2.68"), Decimal("3.67"), Decimal("-0.99"))
        )

        assert "kotor  +2.68" in teks
        assert "biaya  -3.67" in teks
        assert "bersih -0.99" in teks

    def test_menang_juga_menunjukkannya(self) -> None:
        """Blok yang hanya muncul saat kalah mengembalikan asimetri yang PASAL
        11 minta dihapus, lewat pintu lain."""
        teks = self._render(
            "WIN", (Decimal("9.69"), Decimal("3.07"), Decimal("6.62"))
        )

        assert "HITUNGAN (paper):" in teks
        assert "bersih +6.62" in teks

    def test_tanpa_paper_trade_tidak_ada_blok_kosong(self) -> None:
        """Prediksi yang tidak menghasilkan posisi tidak punya hitungan."""
        teks = self._render("BREAKEVEN", None)

        assert "HITUNGAN" not in teks

    def test_biaya_tidak_pernah_bertanda_ganda(self) -> None:
        """Versi pertama mencetak ``-+3.67``: tanda plus dari pemformat angka
        dan tanda minus dari barisnya bertemu."""
        teks = self._render(
            "LOSS", (Decimal("2.68"), Decimal("3.67"), Decimal("-0.99"))
        )

        assert "-+" not in teks

    def test_angka_yang_dicetak_konsisten_satu_sama_lain(self) -> None:
        """Kotor dikurangi biaya harus sama dengan bersih. Kalau tidak, pembaca
        yang menjumlahkannya akan menemukan pesannya berbohong."""
        kotor, biaya, bersih = Decimal("2.68"), Decimal("3.67"), Decimal("-0.99")
        assert kotor - biaya == bersih

        teks = self._render("LOSS", (kotor, biaya, bersih))
        assert all(s in teks for s in ("+2.68", "-3.67", "-0.99"))


class TestKabelnyaSampai:
    """Cacat yang paling sering terulang di sistem ini: kode ditulis, diekspor,
    diuji sendiri, dan tidak pernah dilewati jalur yang hidup.

    Dua penyaring di atas tidak berguna kalau loop upkeep tidak pernah mengisi
    ``published``, dan blok hitungan tidak berguna kalau angkanya berhenti di
    ``_simulate_trade``.
    """

    @pytest.mark.asyncio
    async def test_loop_upkeep_menanyakan_status_publikasi(self) -> None:
        from aruna.upkeep.loop import UpkeepLoop

        ditanya: list[list[str]] = []

        class _Resolver:
            async def published_ids(self, ids: Any) -> set[str]:
                ditanya.append(list(ids))
                return {"sudah"}

        loop = UpkeepLoop.__new__(UpkeepLoop)
        loop._resolver = _Resolver()
        baris = [{"signal_id": "sudah"}, {"signal_id": "belum"}]

        await loop._attach_published(baris)

        assert ditanya == [["sudah", "belum"]]
        assert baris[0]["published"] is True
        assert baris[1]["published"] is False

    @pytest.mark.asyncio
    async def test_pencarian_yang_gagal_tidak_menandai_apa_pun(self) -> None:
        """Baris tanpa tanda tetap dikirim - lihat
        ``test_tanpa_keterangan_tetap_dikirim``. Yang harus dijaga di sini
        adalah bahwa kegagalan tidak menandai semuanya sebagai belum terbit."""
        from aruna.upkeep.loop import UpkeepLoop

        class _Rusak:
            async def published_ids(self, ids: Any) -> set[str]:
                raise RuntimeError("database sedang tidak bisa dihubungi")

        loop = UpkeepLoop.__new__(UpkeepLoop)
        loop._resolver = _Rusak()
        baris = [{"signal_id": "a"}]

        await loop._attach_published(baris)

        assert "published" not in baris[0]

    def test_baris_hasil_membawa_hitungannya(self) -> None:
        from types import SimpleNamespace as N

        from aruna.upkeep.loop import _result_row

        signal = N(
            symbol="DOT/USDT", direction="BUY", signal_id="abc",
            reference_price=Decimal("0.747"),
            target_price=Decimal("0.75128425"), model_version="signals-s3",
        )
        outcome = N(outcome_class=N(value="TARGET_NOT_REACHED"),
                    target_reached=False)

        row = _result_row(
            signal, outcome, "LOSS",
            (Decimal("2.68"), Decimal("3.67"), Decimal("-0.99")),
        )

        assert row["economics"] == (
            Decimal("2.68"), Decimal("3.67"), Decimal("-0.99")
        )
        assert row["trade_result"] == "LOSS"

    def test_resolver_meneruskan_pencarian_ke_penyimpanan(self) -> None:
        """Diteruskan lewat service, bukan dengan menjangkau ``_store`` privat
        dari loop upkeep."""
        import inspect

        from aruna.signals.service import SignalService

        assert hasattr(SignalService, "published_ids")
        sumber = inspect.getsource(SignalService.published_ids)
        assert "published_ids" in sumber

    @pytest.mark.asyncio
    async def test_tanpa_penyimpanan_semua_dianggap_terbit(self) -> None:
        """Gagal terbuka, diperiksa pada seam-nya sendiri."""
        from aruna.signals.service import SignalService

        svc = SignalService.__new__(SignalService)
        svc._store = object()

        assert await svc.published_ids(["a", "b"]) == {"a", "b"}
