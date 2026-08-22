"""Daur hidup keputusan (PASAL 14.22, 14.23, 14.24).

Sebuah signal berhorizon lima belas menit yang masih ditampilkan aktif tiga jam
kemudian bukan signal - ia sisa. Operator yang membacanya melihat entry dan
stop yang dihitung untuk pasar yang sudah tidak ada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.decision import HORIZON, State, TransitionError, Umur, can_move, move

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


class TestStateMachine:
    def test_wait_bukan_keadaan(self) -> None:
        """PASAL 14.23 menyebut daftarnya, dan WAIT tidak ada di sana - ia
        penundaan yang menyamar sebagai jawaban."""
        assert "WAIT" not in {s.value for s in State}

    def test_jalur_normal_sampai_terbit(self) -> None:
        s = State.ANALYZING
        for berikut in (
            State.CANDIDATE, State.DEBATING, State.VALIDATED,
            State.PUBLISHED, State.ACTIVE, State.HIT,
        ):
            s = move(s, berikut)
        assert s is State.HIT

    def test_tidak_bisa_melompati_council(self) -> None:
        """Terbit tanpa melewati perdebatan dan gerbangnya adalah keputusan
        yang tidak pernah diuji siapa pun."""
        with pytest.raises(TransitionError):
            move(State.ANALYZING, State.PUBLISHED)

    def test_keadaan_akhir_tidak_bisa_hidup_lagi(self) -> None:
        """PASAL 14.24: kalau pasar berubah, buat keputusan BARU."""
        for akhir in (State.HIT, State.INVALIDATED, State.EXPIRED):
            with pytest.raises(TransitionError, match="keadaan akhir"):
                move(akhir, State.ACTIVE)

    def test_validated_boleh_berakhir_tanpa_terbit(self) -> None:
        """Gerbang risiko menahannya, atau keputusannya NO SIGNAL. Keduanya
        bukan kegagalan."""
        assert can_move(State.VALIDATED, State.EXPIRED)
        assert can_move(State.VALIDATED, State.INVALIDATED)

    def test_bisa_dibatalkan_dari_tahap_mana_pun_sebelum_akhir(self) -> None:
        for s in (
            State.ANALYZING, State.CANDIDATE, State.DEBATING,
            State.VALIDATED, State.PUBLISHED, State.ACTIVE,
        ):
            assert can_move(s, State.INVALIDATED), s

    def test_gagal_berisik_bukan_diam(self) -> None:
        """Perpindahan yang gagal diam-diam meninggalkan pemanggil yang mengira
        ia berhasil - dan status yang tidak sesuai kenyataan lebih berbahaya
        daripada kegagalan yang berisik."""
        with pytest.raises(TransitionError):
            move(State.ACTIVE, State.CANDIDATE)

    def test_yang_sudah_terbit_ditandai(self) -> None:
        assert State.PUBLISHED.published
        assert State.ACTIVE.published
        assert not State.DEBATING.published
        assert not State.VALIDATED.published


class TestKedaluwarsa:
    def test_masa_berlaku_sama_dengan_horizonnya(self) -> None:
        """Bukan kelipatannya: keputusan lima belas menit yang masih berlaku
        satu jam kemudian sedang menilai pasar yang berbeda."""
        u = Umur(published_at=NOW, horizon="15m")
        assert u.expires_at == NOW + timedelta(minutes=15)

    def test_belum_lewat_belum_kedaluwarsa(self) -> None:
        u = Umur(published_at=NOW, horizon="15m")
        assert not u.expired(NOW + timedelta(minutes=14))

    def test_tepat_di_batas_sudah_kedaluwarsa(self) -> None:
        u = Umur(published_at=NOW, horizon="15m")
        assert u.expired(NOW + timedelta(minutes=15))

    def test_horizon_tak_dikenal_tidak_ditebak(self) -> None:
        """Masa berlaku yang dikarang membuat keputusan mati pada waktu yang
        tidak pernah diputuskan siapa pun."""
        u = Umur(published_at=NOW, horizon="7 menit")

        assert u.expires_at is None
        assert not u.expired(NOW + timedelta(days=9999))
        assert "tidak dikenal" in u.line(NOW)

    def test_kalimatnya_menyebut_sisa_waktu(self) -> None:
        u = Umur(published_at=NOW, horizon="1h")
        assert "sisa 45 menit" in u.line(NOW + timedelta(minutes=15))

    def test_kalimatnya_menyebut_sudah_kedaluwarsa(self) -> None:
        u = Umur(published_at=NOW, horizon="15m")
        assert "KEDALUWARSA" in u.line(NOW + timedelta(hours=3))

    @pytest.mark.parametrize("h", ["1m", "5m", "15m", "1h", "4h", "1d"])
    def test_horizon_yang_dipakai_aruna_semuanya_dikenal(self, h) -> None:
        """Horizon yang dipakai jalur hidup tapi tidak ada di peta akan
        menghasilkan keputusan yang tidak pernah kedaluwarsa."""
        assert h in HORIZON
