"""Seberapa keras sebuah kegagalan layak diteriakkan (PASAL 17).

Regresi dari layar operator:

    [critical] health.transition component=redis message='not connected'

Redis adalah cache, dan ``app.py`` menyatakannya non-fatal di urutan
startup-nya sendiri. Sistem yang menyatakan sesuatu tidak fatal lalu
meneriakkannya sebagai critical sedang melatih pembacanya mengabaikan
critical.
"""

from __future__ import annotations

import pytest

from aruna.core.enums import EventSeverity, HealthStatus
from aruna.health.severity import (
    RECORD_CRITICAL,
    RECOVERABLE,
    RECOVERABLE_PREFIXES,
    is_record_critical,
    severity_for,
)


class TestGarisPemisah:
    @pytest.mark.parametrize("component", sorted(RECORD_CRITICAL))
    def test_yang_mengancam_catatan_tetap_critical(self, component: str) -> None:
        """Bukti yang berhenti tercatat, atau tercatat salah, adalah keadaan
        darurat. Sisanya tidak."""
        assert severity_for(component, HealthStatus.DOWN) is EventSeverity.CRITICAL

    @pytest.mark.parametrize("component", sorted(RECOVERABLE))
    def test_yang_pulih_sendiri_jadi_warning(self, component: str) -> None:
        assert severity_for(component, HealthStatus.DOWN) is EventSeverity.WARNING

    def test_redis_tidak_lagi_critical(self) -> None:
        """Kasus yang memicu perubahan ini."""
        assert severity_for("redis", HealthStatus.DOWN) is EventSeverity.WARNING

    def test_database_tetap_critical(self) -> None:
        """Prediksi, outcome, dan audit tidak punya tempat lagi. Yang hilang
        selama menit-menit itu hilang selamanya."""
        assert severity_for("database", HealthStatus.DOWN) is EventSeverity.CRITICAL

    def test_jam_tetap_critical(self) -> None:
        """Cap waktu yang salah merusak catatan tanpa terlihat rusak, dan SPEC
        22 melarang memperbaikinya belakangan."""
        assert severity_for("clock", HealthStatus.DOWN) is EventSeverity.CRITICAL

    def test_upkeep_critical_karena_catatan_berhenti_bertambah(self) -> None:
        """Upkeep menskor prediksi dan menulis outcome.

        Bantahannya - "prediksinya masih tersimpan, bisa diskor nanti" - benar
        sampai batas tertentu, dan batas itulah alasannya: skoring membaca bar
        dari riwayat venue, dan venue hanya menyajikannya selama jendela
        tertentu. Lewat itu, prediksi yang belum diskor tidak akan pernah punya
        hasil, dan kehilangannya tidak meninggalkan jejak apa pun.

        Keanggotaannya diperiksa langsung, bukan hanya keparahannya. Default
        modul ini adalah "tidak dikenal berarti kritis", jadi memeriksa
        ``severity_for`` saja tidak bisa membedakan **diklasifikasikan kritis**
        dari **tidak diklasifikasikan sama sekali** - dan yang kedua adalah
        persis kelalaian yang default itu ada untuk menangkap.
        """
        assert "upkeep" in RECORD_CRITICAL
        assert "upkeep" not in RECOVERABLE
        assert severity_for("upkeep", HealthStatus.DOWN) is EventSeverity.CRITICAL


class TestKomponenDinamis:
    @pytest.mark.parametrize(
        "component",
        ["provider:binance-spot", "provider:yahoo",
         "candles:CRYPTO", "candles:IDX", "stream:binance-spot"],
    )
    def test_nama_bernomor_ikut_prefiksnya(self, component: str) -> None:
        """Provider yang tidak terjangkau membuat ARUNA menolak menerbitkan
        signal - persis yang PASAL 5 minta - dan catatannya tetap utuh."""
        assert severity_for(component, HealthStatus.DOWN) is EventSeverity.WARNING

    def test_prefiks_tidak_menelan_nama_lain(self) -> None:
        """"providerX" bukan "provider:X"."""
        assert is_record_critical("providerX") is True


class TestDefaultBerbahaya:
    def test_yang_tidak_dikenal_dianggap_critical(self) -> None:
        """Arah defaultnya sengaja berbahaya: pemeriksaan baru yang lupa
        diklasifikasikan harus berisik, bukan diam."""
        assert is_record_critical("sesuatu_yang_baru") is True
        assert severity_for(
            "sesuatu_yang_baru", HealthStatus.DOWN
        ) is EventSeverity.CRITICAL

    def test_dua_daftar_tidak_beririsan(self) -> None:
        assert frozenset() == RECORD_CRITICAL & RECOVERABLE


class TestStatusLain:
    @pytest.mark.parametrize("component", ["database", "redis"])
    def test_degraded_selalu_warning(self, component: str) -> None:
        """Database yang lambat belum kehilangan apa pun, dan menaikkannya ke
        CRITICAL mengembalikan persis kebisingan yang perubahan ini hapus."""
        assert severity_for(component, HealthStatus.DEGRADED) is EventSeverity.WARNING

    @pytest.mark.parametrize(
        "status", [HealthStatus.UP, HealthStatus.DISABLED, HealthStatus.UNKNOWN]
    )
    def test_selain_padam_dan_degraded_itu_info(self, status) -> None:
        assert severity_for("database", status) is EventSeverity.INFO

    def test_disabled_bukan_padam(self) -> None:
        """Redis yang dimatikan operator bukan kegagalan sama sekali."""
        assert severity_for("redis", HealthStatus.DISABLED) is EventSeverity.INFO


class TestSetiapPemeriksaanDiklasifikasikan:
    """Yang memaksa "lupa" muncul sebagai suite merah, bukan sebagai
    peringatan yang tidak pernah berbunyi."""

    def _nama_pemeriksaan(self) -> set[str]:
        from aruna.health import checks as c
        from aruna.health.providers import ProviderCheck
        from aruna.health.stream import StreamCheck
        from aruna.health.upkeep import CandleFreshnessCheck, UpkeepCheck

        tetap = {
            cls.name
            for cls in (
                c.ClockCheck, c.ConfigCheck, c.DatabaseCheck,
                c.ProcessCheck, c.RedisCheck, c.TelegramCheck, UpkeepCheck,
            )
            if isinstance(getattr(cls, "name", None), str)
        }
        # Nama dinamis diwakili prefiksnya.
        assert ProviderCheck and StreamCheck and CandleFreshnessCheck
        return tetap

    def test_semua_pemeriksaan_tetap_punya_klasifikasi(self) -> None:
        belum = self._nama_pemeriksaan() - RECORD_CRITICAL - RECOVERABLE
        assert belum == set(), (
            f"pemeriksaan tanpa klasifikasi keparahan: {sorted(belum)} - "
            "tambahkan ke RECORD_CRITICAL atau RECOVERABLE di "
            "aruna/health/severity.py"
        )

    def test_prefiks_dinamis_terdaftar(self) -> None:
        assert set(RECOVERABLE_PREFIXES) == {"provider:", "candles:", "stream:"}


class TestTerpasangDiMonitor:
    def test_monitor_memakai_nama_komponen(self) -> None:
        """Versi lama hanya menerima status, jadi ia tidak BISA membedakan
        redis dari database."""
        import inspect

        from aruna.health import monitor

        source = inspect.getsource(monitor.HealthMonitor._announce)
        assert "severity_for(component.name, component.status)" in source

    async def test_peralihan_redis_dicatat_sebagai_warning(self) -> None:
        from aruna.health.models import ComponentHealth, HealthReport
        from aruna.health.monitor import HealthMonitor

        tercatat: list[EventSeverity] = []

        async def hook(component, overall, severity) -> None:
            tercatat.append(severity)

        mati = ComponentHealth(
            name="redis", status=HealthStatus.DOWN, message="not connected"
        )
        m = HealthMonitor([], event_hook=hook)
        await m._announce(HealthReport(components=(mati,)), (mati,))

        assert tercatat == [EventSeverity.WARNING]

    async def test_peralihan_database_tetap_critical(self) -> None:
        from aruna.health.models import ComponentHealth, HealthReport
        from aruna.health.monitor import HealthMonitor

        tercatat: list[EventSeverity] = []

        async def hook(component, overall, severity) -> None:
            tercatat.append(severity)

        mati = ComponentHealth(
            name="database", status=HealthStatus.DOWN, message="gone"
        )
        m = HealthMonitor([], event_hook=hook)
        await m._announce(HealthReport(components=(mati,)), (mati,))

        assert tercatat == [EventSeverity.CRITICAL]
