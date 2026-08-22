"""Keputusan final hanya LONG / SHORT / NO SIGNAL (bagian 8, 25, 27).

Terukur 2026-08-21: `WAIT` tersimpan sebagai keputusan pada **3.871 dari 6.441**
sesi council dan **5.981 dari 10.494** baris `signal_snapshots`.

Yang TIDAK berubah karena modul ini: apa yang operator lihat. `PUBLIC_DECISION`
sudah memetakan `WAIT -> "NO SIGNAL"` di lapisan pesan sejak lama, jadi kata
"WAIT" tidak pernah sampai ke Telegram. Yang berubah adalah catatan yang
tersimpan berhenti membawa keputusan keempat.

Dan yang juga tidak berubah: `WAIT` tetap sah sebagai **suara agent**
(`UNRESTRICTED_AGENT_DECISIONS`). Bagian 25 mengizinkannya - *"internal
analysis boleh memiliki uncertainty"* - yang dilarang hanya keputusan finalnya.
"""

from __future__ import annotations

import pytest

from aruna.core.enums import Decision
from aruna.decision.finalizer import FINAL, SebabDiam, finalkan


class TestKosakataFinal:
    @pytest.mark.parametrize(
        "masuk", [Decision.WAIT, Decision.NO_SIGNAL, Decision.UNKNOWN_MARKET]
    )
    def test_yang_tidak_berarah_menjadi_no_signal(self, masuk) -> None:
        assert finalkan(masuk).keputusan is Decision.NO_SIGNAL

    @pytest.mark.parametrize("masuk", [Decision.BUY, Decision.SELL])
    def test_yang_berarah_lewat_apa_adanya(self, masuk) -> None:
        """Finalizer menjamin kosakata, bukan mengubah arah. Yang bisa membalik
        arah bukan finalizer melainkan mesin keputusan kedua."""
        assert finalkan(masuk).keputusan is masuk

    def test_wait_tidak_pernah_keluar(self) -> None:
        for d in Decision:
            assert finalkan(d).keputusan is not Decision.WAIT

    def test_keluarannya_selalu_salah_satu_dari_tiga(self) -> None:
        assert set(FINAL) == {Decision.BUY, Decision.SELL, Decision.NO_SIGNAL}
        for d in Decision:
            assert finalkan(d).keputusan in FINAL


class TestSebabnyaTidakHilang:
    """Meruntuhkan dua keputusan menjadi satu menghapus keterangan, kecuali
    keterangannya dipindahkan. `veto.py` mengeja bedanya: NO_SIGNAL berarti
    input tidak bisa dipercaya, WAIT berarti tidak ada setup sekarang."""

    def test_tanpa_setup_punya_sebabnya_sendiri(self) -> None:
        hasil = finalkan(Decision.WAIT)

        assert hasil.sebab is SebabDiam.TIDAK_ADA_SETUP

    def test_diblokir_veto_dibedakan(self) -> None:
        hasil = finalkan(Decision.NO_SIGNAL, diblokir_veto=True)

        assert hasil.sebab is SebabDiam.DIBLOKIR_VETO

    def test_diblokir_no_trade_dibedakan(self) -> None:
        hasil = finalkan(Decision.NO_SIGNAL, diblokir_no_trade=True)

        assert hasil.sebab is SebabDiam.DIBLOKIR_NO_TRADE

    def test_veto_menang_atas_no_trade(self) -> None:
        """Keduanya bisa menyala bersamaan; yang dilaporkan adalah yang
        menghentikan keputusan lebih dulu (SPEC 19 sebelum SPEC 33)."""
        hasil = finalkan(
            Decision.NO_SIGNAL, diblokir_veto=True, diblokir_no_trade=True
        )

        assert hasil.sebab is SebabDiam.DIBLOKIR_VETO

    def test_input_tak_terpercaya_tanpa_gerbang_apa_pun(self) -> None:
        """NO_SIGNAL yang datang bukan dari veto atau no-trade tetap berarti
        sesuatu - dan menyebutnya TIDAK_ADA_SETUP akan berbohong."""
        hasil = finalkan(Decision.NO_SIGNAL)

        assert hasil.sebab is SebabDiam.INPUT_TAK_TERPERCAYA

    def test_yang_berarah_tidak_punya_sebab_diam(self) -> None:
        assert finalkan(Decision.BUY).sebab is None


class TestTidakMengubahYangLain:
    def test_wait_tetap_sah_sebagai_suara_agent(self) -> None:
        """Bagian 25 mengizinkan uncertainty internal. Membuang `WAIT` dari
        kosakata agent akan memaksa tiap agent berpihak pada tiap tick - dan
        itu menghasilkan sistem yang lebih percaya diri, bukan lebih pintar."""
        from aruna.core.enums import UNRESTRICTED_AGENT_DECISIONS

        assert Decision.WAIT in UNRESTRICTED_AGENT_DECISIONS

    def test_finalizer_tidak_menyentuh_confidence(self) -> None:
        """Angka keyakinan milik kalibrator (bagian 9). Dua modul yang
        menyentuh angka yang sama adalah dua yang suatu saat tidak sepakat."""
        import ast
        import inspect

        from aruna.decision import finalizer

        pohon = ast.parse(inspect.getsource(finalizer))
        nama = {n.id for n in ast.walk(pohon) if isinstance(n, ast.Name)}
        nama |= {n.attr for n in ast.walk(pohon) if isinstance(n, ast.Attribute)}

        assert "confidence" not in nama
        assert "keyakinan" not in nama
