"""Bukti seumur satu bar bukan bukti basi (dilaporkan lewat log produksi).

271 penahanan berbunyi persis "evidence is 15 minute(s) old against a 15m
horizon", dan 76 berbunyi "60 minute(s) old against a 1h horizon" - satu angka
yang sama berulang ratusan kali, bukan sebaran. Itu tanda batas yang dilewati
tipis, bukan data yang benar-benar tua.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.core.enums import Horizon
from aruna.signals.lock import (
    EVIDENCE_SETTLE_GRACE_SEC,
    MAX_EVIDENCE_AGE_MULTIPLE,
    evidence_age_note,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def catatan(umur_detik: float, horizon: Horizon = Horizon.M15) -> str | None:
    return evidence_age_note(
        NOW - timedelta(seconds=umur_detik), horizon, NOW
    )


class TestBarTerbaruTidakDitolak:
    @pytest.mark.parametrize(
        ("horizon", "detik"),
        [(Horizon.M15, 900), (Horizon.H1, 3600), (Horizon.D1, 86400)],
    )
    def test_umur_tepat_satu_interval_lolos(self, horizon, detik) -> None:
        """Bar tertutup terbaru berumur antara nol dan satu interval penuh
        pada saat mana pun. Satu interval adalah kasus normal terburuk, bukan
        kesalahan."""
        assert catatan(detik, horizon) is None

    @pytest.mark.parametrize(
        ("horizon", "detik"),
        [(Horizon.M15, 905), (Horizon.H1, 3610), (Horizon.M15, 950)],
    )
    def test_satu_interval_plus_jeda_settle_lolos(self, horizon, detik) -> None:
        """Ini kasus yang terjadi di produksi: tick yang mengunci jatuh di
        dalam jeda settle ingest, dan menemukan bar sebelumnya."""
        assert catatan(detik, horizon) is None

    def test_tepat_di_batas_kelonggaran_lolos(self) -> None:
        assert catatan(900 + EVIDENCE_SETTLE_GRACE_SEC) is None


class TestBuktiBenarBenarBasiTetapDitolak:
    def test_sedikit_di_atas_kelonggaran_ditolak(self) -> None:
        assert catatan(900 + EVIDENCE_SETTLE_GRACE_SEC + 1) is not None

    def test_dua_interval_ditolak(self) -> None:
        """Bukti dua bar terlambat berarti pasar sudah bergerak melewati
        sebagian besar jendela yang diklaim prediksinya."""
        pesan = catatan(1800)

        assert pesan is not None
        assert "stale" in pesan

    def test_enam_jam_pada_horizon_satu_jam_ditolak(self) -> None:
        """Contoh yang ditulis di docstring aslinya."""
        assert catatan(6 * 3600, Horizon.H1) is not None


class TestKelonggarannyaMasukAkal:
    def test_menutupi_jeda_settle_ingest(self) -> None:
        """Ingest menunggu ``candle_settle_sec`` sesudah batas bar sebelum
        menarik bar itu. Kelonggarannya harus melebihi jeda itu, atau kasus
        produksi tetap ditolak."""
        from aruna.core.config import UpkeepSettings

        assert UpkeepSettings().candle_settle_sec < EVIDENCE_SETTLE_GRACE_SEC

    def test_jauh_di_bawah_horizon_terpendek(self) -> None:
        """Kelonggaran yang mendekati satu horizon akan meloloskan bukti yang
        benar-benar satu bar terlambat."""
        assert Horizon.M15.duration.total_seconds() / 4 > EVIDENCE_SETTLE_GRACE_SEC

    def test_pengalinya_tetap_satu_horizon_penuh(self) -> None:
        """Kelonggarannya ditambahkan, bukan mengubah pengalinya - supaya
        "satu horizon" tetap berarti satu horizon."""
        assert MAX_EVIDENCE_AGE_MULTIPLE == 1.0
