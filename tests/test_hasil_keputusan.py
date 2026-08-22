"""Nasib satu rencana, dikirim ke pembelajaran (PASAL 14.31, 14.34).

``aruna.decision.outcome`` sudah lama bisa menyusun catatan hasil dan menolak
mengirim signal palsu yang sebabnya belum dicari. Ia tidak pernah dipanggil
sekali pun - modul terakhir dari delapan yang diam.

Yang paling berbahaya di berkas ini adalah **salah tanda**. ``move_pct`` adalah
gerak pasar apa adanya - positif berarti harga naik - dan yang membalikkannya
untuk SHORT adalah modul outcome, bukan pemanggilnya. Salah tanda di sini
mengubah kekalahan besar menjadi kemenangan besar di dalam data yang dipelajari
Phase 12, dan tidak ada yang akan menyadarinya sampai model barunya lebih buruk.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aruna.decision.outcome import Hasil
from aruna.decision.score import Arah
from aruna.futures.learning import PlanOutcome, PlanResult
from aruna.futures.models import PositionSide
from aruna.futures.resolve import catat_hasil

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _hasil(
    outcome: PlanOutcome = PlanOutcome.TARGET_HIT,
    side: PositionSide = PositionSide.LONG,
    entry: str = "100",
    exit_price: str | None = "105",
) -> PlanResult:
    return PlanResult(
        signal_id="abc",
        symbol="BTCUSDT",
        side=side,
        outcome=outcome,
        entry=Decimal(entry),
        exit_price=None if exit_price is None else Decimal(exit_price),
    )


def _tangkap(monkeypatch) -> list[tuple[str, dict]]:
    from aruna.futures import resolve as modul

    keluar: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        modul,
        "log",
        SimpleNamespace(
            info=lambda nama, **kw: keluar.append((nama, kw)),
            warning=lambda nama, **kw: keluar.append((nama, kw)),
            error=lambda nama, **kw: keluar.append((nama, kw)),
            exception=lambda nama, **kw: keluar.append((f"!{nama}", kw)),
        ),
    )
    return keluar


class TestEmpatAkhir:
    """PASAL 14.31 menyebut empat, dan keempatnya dikirim ke pembelajaran."""

    @pytest.mark.parametrize(
        ("outcome", "keluar_price", "hasil"),
        [
            (PlanOutcome.TARGET_HIT, "105", Hasil.WIN),
            # -1%: kalah, tapi belum FALSE SIGNAL. Yang diuji di sini pemetaan
            # hasilnya; ambang PASAL 14.34 diuji terpisah di bawah.
            (PlanOutcome.STOPPED_OUT, "99", Hasil.LOSS),
            (PlanOutcome.EXPIRED, "101", Hasil.EXPIRED),
        ],
    )
    def test_dipetakan(
        self, monkeypatch, outcome: PlanOutcome, keluar_price: str, hasil: Hasil
    ) -> None:
        keluar = _tangkap(monkeypatch)
        catatan = catat_hasil(_hasil(outcome, exit_price=keluar_price))

        assert catatan is not None
        assert catatan.outcome is hasil
        assert [n for n, _ in keluar] == ["decision.outcome"]

    def test_likuidasi_tetap_terbaca_sebagai_kalah(self, monkeypatch) -> None:
        """§11.21 melarang menyembunyikan LOSS, dan likuidasi adalah kekalahan
        yang paling buruk. Memberinya kategori sendiri akan mengeluarkannya dari
        kolom kalah - bentuk penyembunyian yang paling mudah dibela.

        Likuidasi yang sungguhan hampir selalu FALSE SIGNAL juga - pasarnya
        bergerak jauh melawan - jadi jalur pembelajarannya tertahan PASAL 14.34.
        Yang wajib: **peringatannya tetap menyebut LOSS**, supaya penahanan itu
        tidak terbaca seperti kekalahan yang menghilang.
        """
        keluar = _tangkap(monkeypatch)
        catat_hasil(_hasil(PlanOutcome.LIQUIDATED, exit_price="90"))

        nama, isi = keluar[0]
        assert nama == "futures.false_signal_tanpa_sebab"
        assert isi["outcome"] == Hasil.LOSS.value

    def test_likuidasi_kecil_tetap_kalah(self, monkeypatch) -> None:
        """Pemetaannya sendiri, tanpa ambang PASAL 14.34 ikut campur."""
        catatan = catat_hasil(_hasil(PlanOutcome.LIQUIDATED, exit_price="99"))

        assert catatan is not None
        assert catatan.outcome is Hasil.LOSS

    def test_yang_belum_selesai_tidak_dicatat(self, monkeypatch) -> None:
        """OPEN bukan hasil. Mengirimnya ke pembelajaran berarti mengajari
        Phase 12 tentang posisi yang belum berakhir."""
        keluar = _tangkap(monkeypatch)

        assert catat_hasil(_hasil(PlanOutcome.OPEN, exit_price=None)) is None
        assert keluar == []


class TestArahnya:
    def test_gerak_pasar_apa_adanya_bukan_yang_sudah_diorientasikan(
        self, monkeypatch
    ) -> None:
        """SHORT yang menang berarti harga TURUN, jadi ``move_pct`` negatif.

        Yang membalik tandanya adalah modul outcome. Menyerahkan angka yang
        sudah diorientasikan dari sini akan membuat modul itu membaliknya untuk
        kedua kalinya - dan kemenangan tercatat sebagai kekalahan.
        """
        keluar = _tangkap(monkeypatch)
        catat_hasil(
            _hasil(PlanOutcome.TARGET_HIT, PositionSide.SHORT,
                   entry="100", exit_price="95")
        )

        assert keluar[0][1]["move_pct"] == Decimal("-5")
        assert keluar[0][1]["outcome"] == Hasil.WIN.value

    def test_long_yang_menang_positif(self, monkeypatch) -> None:
        keluar = _tangkap(monkeypatch)
        catat_hasil(_hasil(PlanOutcome.TARGET_HIT, exit_price="105"))

        assert keluar[0][1]["move_pct"] == Decimal("5")

    def test_arahnya_terbawa(self, monkeypatch) -> None:
        # +1%: SHORT yang kalah tipis, belum FALSE SIGNAL - jadi payloadnya
        # benar-benar terkirim dan bisa diperiksa.
        keluar = _tangkap(monkeypatch)
        catat_hasil(
            _hasil(PlanOutcome.STOPPED_OUT, PositionSide.SHORT,
                   entry="100", exit_price="101")
        )

        assert keluar[0][1]["decision"] == Arah.SHORT.value

    def test_tanpa_harga_keluar_geraknya_belum_terukur(self, monkeypatch) -> None:
        """§13.26: kalau tidak ada harga keluar, geraknya tidak ada - bukan
        nol. Nol berarti pasar tidak bergerak, dan itu pernyataan."""
        keluar = _tangkap(monkeypatch)
        catat_hasil(_hasil(PlanOutcome.EXPIRED, exit_price=None))

        assert keluar[0][1]["move_pct"] is None

    def test_geraknya_dibulatkan_ke_skala_yang_dibaca(self, monkeypatch) -> None:
        """Terukur di produksi 2026-08-20: APTUSDT tercatat
        ``-0.5952539839308117342444545285`` - dua puluh delapan angka di
        belakang koma untuk sebuah persentase yang dinilai terhadap ambang dua
        persen.

        Bukan salah hitung, tapi tetap cacat: baris log yang harus dibaca
        manusia, dan angka yang ketelitiannya dua puluh kali lebih halus
        daripada yang menilainya. Kelas yang sama dengan jejak PASAL 14.30 dan
        dengan tiga kolom DECIMAL yang pernah terpotong MySQL.
        """
        keluar = _tangkap(monkeypatch)
        catat_hasil(_hasil(PlanOutcome.EXPIRED, entry="336", exit_price="334"))

        gerak = keluar[0][1]["move_pct"]

        assert -gerak.as_tuple().exponent <= 2, gerak

    def test_pembulatannya_tidak_menggeser_nilainya(self, monkeypatch) -> None:
        """Yang dibuang sisa di bawah 0,01% - bukan gerakannya."""
        keluar = _tangkap(monkeypatch)
        catat_hasil(_hasil(PlanOutcome.EXPIRED, entry="336", exit_price="334"))

        kasar = (Decimal("334") - Decimal("336")) / Decimal("336") * 100

        assert abs(keluar[0][1]["move_pct"] - kasar) < Decimal("0.01")

    def test_entry_nol_tidak_membagi_nol(self, monkeypatch) -> None:
        keluar = _tangkap(monkeypatch)
        catat_hasil(_hasil(PlanOutcome.EXPIRED, entry="0", exit_price="5"))

        assert keluar[0][1]["move_pct"] is None


class TestSignalPalsu:
    """PASAL 14.34: signal palsu tanpa sebab tidak mengajarkan apa pun."""

    def test_tanpa_sebab_tidak_dikirim_ke_pembelajaran(self, monkeypatch) -> None:
        keluar = _tangkap(monkeypatch)
        catat_hasil(
            _hasil(PlanOutcome.STOPPED_OUT, entry="100", exit_price="90")
        )

        assert [n for n, _ in keluar] == ["futures.false_signal_tanpa_sebab"]

    def test_kalah_biasa_tetap_dikirim(self, monkeypatch) -> None:
        """Kalah adalah bagian dari bertaruh. Menuntut penjelasan untuk setiap
        kekalahan akan menghasilkan penjelasan yang dikarang."""
        keluar = _tangkap(monkeypatch)
        catat_hasil(
            _hasil(PlanOutcome.STOPPED_OUT, entry="100", exit_price="99")
        )

        assert [n for n, _ in keluar] == ["decision.outcome"]

    def test_ambangnya_dari_modul_silence(self, monkeypatch) -> None:
        """Gerakan sebesar yang membuat diamnya ARUNA disebut kehilangan adalah
        gerakan sebesar yang membuat pendapatnya disebut salah. Dua arah, satu
        ukuran - dan menyalinnya ke sini akan menghasilkan dua angka yang bisa
        berselisih diam-diam."""
        from aruna.decision.silence import GERAK_BERARTI_PCT

        tepat = Decimal("100") - GERAK_BERARTI_PCT
        kurang = tepat + Decimal("0.01")

        keluar = _tangkap(monkeypatch)
        catat_hasil(_hasil(PlanOutcome.STOPPED_OUT, exit_price=str(kurang)))
        catat_hasil(_hasil(PlanOutcome.STOPPED_OUT, exit_price=str(tepat)))

        assert [n for n, _ in keluar] == [
            "decision.outcome", "futures.false_signal_tanpa_sebab"
        ]

    def test_yang_ditahan_tetap_menyebut_simbolnya(self, monkeypatch) -> None:
        """Sebuah peringatan yang tidak menyebut simbol mana tidak bisa
        ditindaklanjuti - dan yang perlu ditindaklanjuti di sini adalah mencari
        sebabnya."""
        keluar = _tangkap(monkeypatch)
        catat_hasil(
            _hasil(PlanOutcome.STOPPED_OUT, entry="100", exit_price="90")
        )

        assert keluar[0][1].get("symbol") == "BTCUSDT"


class TestTidakMenjatuhkanResolusi:
    def test_bentuk_yang_tak_terduga_dicatat_bukan_meledak(
        self, monkeypatch
    ) -> None:
        """Pencatat hasil yang menjatuhkan resolusi akan menghentikan
        penilaian seluruh rencana lain di batch yang sama."""
        keluar = _tangkap(monkeypatch)

        class Meledak:
            @property
            def outcome(self):
                raise RuntimeError("baris rusak")

        catat_hasil(Meledak())

        assert [n for n, _ in keluar] == ["!decision.outcome_failed"]


class TestDipanggilJalurHidup:
    def test_resolve_memanggilnya(self) -> None:
        """Tanpa ini, seluruh pencatat bisa dihapus dari ``_resolve_one`` dan
        setiap test di atas tetap hijau - keluarga cacat yang sudah berkali-kali
        muncul di sistem ini."""
        from aruna.futures.resolve import FuturesResolver

        sumber = inspect.getsource(FuturesResolver._resolve_one)

        assert "catat_hasil(result)" in sumber

    def test_dicatat_sesudah_hasilnya_tersimpan(self) -> None:
        """PASAL 14.24: yang dikirim ke pembelajaran adalah hasil yang sudah
        jadi catatan. Mencatatnya lebih dulu berarti mengabarkan sesuatu yang
        bisa gagal tersimpan."""
        from aruna.futures.resolve import FuturesResolver

        sumber = inspect.getsource(FuturesResolver._resolve_one)

        assert sumber.index("save_result(") < sumber.index("catat_hasil(")

