"""Kapan sebuah perubahan health layak membangunkan operator (PASAL 17, 18).

Regresi dari lapangan: pada jaringan yang lambat, lima pesan dalam lima menit
tentang satu keadaan yang tidak berubah - dan separuhnya berjudul HEALTH PULIH
karena DEGRADED terhitung operasional.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from aruna.core.enums import HealthStatus
from aruna.health.alerts import ALERT_COOLDOWN, HealthAlertPolicy

NOW = datetime(2026, 8, 18, 5, 30, tzinfo=UTC)


def _c(name: str, status: HealthStatus):
    return NS(name=name, status=status)


class TestHanyaPadamYangMembangunkan:
    def test_masuk_down_mengirim_alert(self) -> None:
        p = HealthAlertPolicy()
        hasil = p.decide([_c("database", HealthStatus.DOWN)], now=NOW)

        assert len(hasil.alerts) == 1
        assert hasil.recoveries == ()

    def test_degraded_tidak_mengirim_apa_pun(self) -> None:
        """DEGRADED adalah peringatan, bukan padam. Ia terbaca di /status
        kapan saja operator ingin melihatnya."""
        p = HealthAlertPolicy()
        hasil = p.decide([_c("binance", HealthStatus.DEGRADED)], now=NOW)

        assert hasil.anything is False
        assert hasil.suppressed

    def test_kembali_up_mengirim_pulih(self) -> None:
        p = HealthAlertPolicy()
        p.decide([_c("database", HealthStatus.DOWN)], now=NOW)
        hasil = p.decide(
            [_c("database", HealthStatus.UP)], now=NOW + timedelta(minutes=1)
        )
        assert len(hasil.recoveries) == 1

    def test_up_tanpa_pernah_down_tidak_mengirim_pulih(self) -> None:
        """Sapuan yang menaikkan komponen dari DEGRADED ke UP bukan pemulihan
        dari padam - dan operator tidak pernah diberi tahu ia padam."""
        p = HealthAlertPolicy()
        hasil = p.decide([_c("telegram", HealthStatus.UP)], now=NOW)
        assert hasil.anything is False


class TestGoyanganTidakMembanjiri:
    def _goyang(self) -> HealthAlertPolicy:
        return HealthAlertPolicy()

    def test_down_lalu_degraded_lalu_down_hanya_sekali(self) -> None:
        """Persis pola yang membanjiri ponsel operator: binance-spot bolak-balik
        DOWN dan DEGRADED tiap sembilan puluh detik."""
        p = self._goyang()
        terkirim = 0
        waktu = NOW
        for status in (
            HealthStatus.DOWN, HealthStatus.DEGRADED,
            HealthStatus.DOWN, HealthStatus.DEGRADED,
            HealthStatus.DOWN,
        ):
            hasil = p.decide([_c("binance", status)], now=waktu)
            terkirim += len(hasil.alerts) + len(hasil.recoveries)
            waktu += timedelta(seconds=90)

        assert terkirim == 1

    def test_degraded_tidak_menghapus_tanda_sudah_diberitahu(self) -> None:
        """Menghapusnya membuat DOWN berikutnya terhitung insiden baru, dan
        goyangannya kembali menjadi banjir."""
        p = self._goyang()
        p.decide([_c("binance", HealthStatus.DOWN)], now=NOW)
        p.decide([_c("binance", HealthStatus.DEGRADED)], now=NOW)

        assert "binance" in p.state()["announced_down"]

    def test_down_berulang_tidak_mengirim_dua_kali(self) -> None:
        p = self._goyang()
        p.decide([_c("db", HealthStatus.DOWN)], now=NOW)
        hasil = p.decide(
            [_c("db", HealthStatus.DOWN)], now=NOW + timedelta(minutes=1)
        )
        assert hasil.alerts == ()

    def test_kedipan_down_up_down_diredam_jeda(self) -> None:
        """Berkedip DOWN-UP-DOWN sepuluh kali dalam semenit adalah satu
        insiden, bukan sepuluh."""
        p = self._goyang()
        alert = 0
        waktu = NOW
        for _ in range(5):
            alert += len(p.decide([_c("db", HealthStatus.DOWN)], now=waktu).alerts)
            waktu += timedelta(seconds=20)
            p.decide([_c("db", HealthStatus.UP)], now=waktu)
            waktu += timedelta(seconds=20)

        assert alert == 1

    def test_padam_berkepanjangan_tidak_berteriak_lagi(self) -> None:
        """Komponen yang DOWN terus-menerus selama sejam adalah SATU padam.

        Ini yang membedakan tanda "sudah diberitahukan" dari jeda alert: jeda
        habis setelah lima belas menit, dan tanpa tanda itu sebuah padam yang
        belum pernah pulih akan diberitakan ulang setiap lima belas menit -
        empat pesan sejam tentang keadaan yang operatornya sudah tahu.
        """
        p = self._goyang()
        alert = 0
        waktu = NOW
        for _ in range(20):  # 20 sapuan x 5 menit = lebih dari satu jam
            alert += len(p.decide([_c("db", HealthStatus.DOWN)], now=waktu).alerts)
            waktu += timedelta(minutes=5)

        assert alert == 1

    def test_sesudah_jeda_lewat_insiden_baru_diberitahukan(self) -> None:
        """Peredam yang tidak punya ujung akan menyembunyikan padam berikutnya
        selamanya."""
        p = self._goyang()
        p.decide([_c("db", HealthStatus.DOWN)], now=NOW)
        p.decide([_c("db", HealthStatus.UP)], now=NOW + timedelta(minutes=1))

        nanti = NOW + ALERT_COOLDOWN + timedelta(minutes=1)
        hasil = p.decide([_c("db", HealthStatus.DOWN)], now=nanti)
        assert len(hasil.alerts) == 1


class TestBeritaPertamaTidakDitunda:
    def test_padam_sungguhan_diberitahukan_sekarang(self) -> None:
        """Sengaja tidak ada debounce. Database yang benar-benar mati harus
        diberitahukan sekarang, bukan tiga sapuan lagi - dan penundaan itu
        tidak bisa dibedakan dari sistem yang tidak memperhatikan."""
        p = HealthAlertPolicy()
        hasil = p.decide([_c("database", HealthStatus.DOWN)], now=NOW)
        assert len(hasil.alerts) == 1

    def test_beberapa_komponen_padam_sekaligus(self) -> None:
        p = HealthAlertPolicy()
        hasil = p.decide(
            [_c("database", HealthStatus.DOWN), _c("clock", HealthStatus.DOWN)],
            now=NOW,
        )
        assert len(hasil.alerts) == 2

    def test_padam_dan_pulih_dipisah(self) -> None:
        """Satu pesan berisi "database DOWN" dan "clock UP" memaksa pembacanya
        memilah judul yang saling bertentangan."""
        p = HealthAlertPolicy()
        p.decide([_c("clock", HealthStatus.DOWN)], now=NOW)
        hasil = p.decide(
            [_c("database", HealthStatus.DOWN), _c("clock", HealthStatus.UP)],
            now=NOW + timedelta(minutes=20),
        )
        assert len(hasil.alerts) == 1
        assert len(hasil.recoveries) == 1


class TestHanyaYangMengancamCatatanYangMendorongKeHp:
    """Garis yang sama dengan tingkat keparahan di log, dipakai untuk memilih
    apa yang membangunkan operator.

    Harganya dinyatakan di depan, bukan ditemukan belakangan: gangguan provider
    berjam-jam tidak akan sampai ke ponsel.
    """

    def test_database_tetap_membangunkan(self) -> None:
        p = HealthAlertPolicy()
        assert len(p.decide([_c("database", HealthStatus.DOWN)], now=NOW).alerts) == 1

    def test_clock_tetap_membangunkan(self) -> None:
        p = HealthAlertPolicy()
        assert len(p.decide([_c("clock", HealthStatus.DOWN)], now=NOW).alerts) == 1

    def test_redis_tidak_lagi_membangunkan(self) -> None:
        """Cache yang mati bukan catatan yang hilang. MySQL tetap sumber
        kebenarannya, dan app.py sendiri menyebut cache 'non-fatal'."""
        p = HealthAlertPolicy()
        hasil = p.decide([_c("redis", HealthStatus.DOWN)], now=NOW)

        assert hasil.anything is False
        assert any("tidak mengancam catatan" in s for s in hasil.suppressed)

    def test_provider_tidak_lagi_membangunkan(self) -> None:
        p = HealthAlertPolicy()
        hasil = p.decide(
            [_c("provider:binance-spot", HealthStatus.DOWN)], now=NOW
        )
        assert hasil.anything is False

    def test_aliran_putus_tetap_membangunkan(self) -> None:
        """PASAL 36 menyebutnya langsung: jangan diam kalau feed mati.

        Aliran yang putus tidak merusak catatan, jadi garis "catatannya
        terancam" akan meredamnya. Pasal itu bukan soal catatan - ia soal
        operator yang mengira ARUNA sedang mengamati pasar padahal tidak ada
        harga yang masuk. Dua instruksi berbenturan, dan yang lebih spesifik
        menang untuk kasus yang disebutnya.
        """
        p = HealthAlertPolicy()
        hasil = p.decide([_c("stream:binance-spot", HealthStatus.DOWN)], now=NOW)

        assert len(hasil.alerts) == 1

    def test_candle_berhenti_masuk_juga_membangunkan(self) -> None:
        """Kegagalan yang sama diucapkan berbeda.

        Aliran putus dan candle berhenti masuk sama-sama berarti tidak ada
        harga baru yang tiba - yang satu diperiksa pada soket, yang lain pada
        barisan bar. Meredam salah satunya membuat "feed mati" terdengar atau
        tidak tergantung pemeriksaan mana yang kebetulan menangkapnya dulu.
        """
        p = HealthAlertPolicy()
        hasil = p.decide([_c("candles:CRYPTO", HealthStatus.DOWN)], now=NOW)

        assert len(hasil.alerts) == 1

    def test_upkeep_membangunkan_karena_catatan_berhenti_bertambah(self) -> None:
        """Upkeep menskor prediksi dan menulis outcome.

        Bantahannya - "prediksinya masih di database, bisa diskor nanti" -
        benar sampai batas tertentu, dan batas itulah alasannya: skoring
        membaca bar dari riwayat venue, dan venue hanya menyajikannya selama
        jendela tertentu.
        """
        p = HealthAlertPolicy()
        assert len(p.decide([_c("upkeep", HealthStatus.DOWN)], now=NOW).alerts) == 1

    def test_aliran_yang_pulih_juga_dikabarkan(self) -> None:
        p = HealthAlertPolicy()
        p.decide([_c("stream:binance-spot", HealthStatus.DOWN)], now=NOW)
        hasil = p.decide(
            [_c("stream:binance-spot", HealthStatus.UP)],
            now=NOW + timedelta(minutes=1),
        )
        assert len(hasil.recoveries) == 1

    def test_yang_ditahan_tidak_mengirim_pulih_belakangan(self) -> None:
        """Kabar baik tentang kabar buruk yang tidak pernah sampai.

        Kalau saringannya ditaruh di dalam cabang DOWN saja, redis akan tetap
        tercatat 'sudah diberitahukan' lalu mengirim HEALTH PULIH saat kembali -
        pesan pemulihan untuk kejatuhan yang tak pernah diberitahukan.
        """
        p = HealthAlertPolicy()
        p.decide([_c("redis", HealthStatus.DOWN)], now=NOW)
        hasil = p.decide(
            [_c("redis", HealthStatus.UP)], now=NOW + timedelta(minutes=5)
        )
        assert hasil.recoveries == ()

    def test_komponen_tak_dikenal_tetap_membangunkan(self) -> None:
        """Arah defaultnya sengaja berbahaya: pemeriksaan baru yang lupa
        diklasifikasikan harus berisik, bukan diam."""
        p = HealthAlertPolicy()
        hasil = p.decide([_c("pemeriksaan-baru", HealthStatus.DOWN)], now=NOW)
        assert len(hasil.alerts) == 1

    def test_saringannya_bisa_dimatikan(self) -> None:
        """Keputusan ini milik operator, jadi ia punya sakelar - dan sakelar
        yang tidak pernah diuji adalah sakelar yang tidak bekerja."""
        p = HealthAlertPolicy(push_record_critical_only=False)
        hasil = p.decide([_c("redis", HealthStatus.DOWN)], now=NOW)

        assert len(hasil.alerts) == 1
        assert p.state()["push_record_critical_only"] is False


class TestKabelKeApp:
    def test_app_memakai_kebijakan(self) -> None:
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._on_health_change)
        assert "_alert_policy.decide" in source

    def test_alert_dan_pulih_dikirim_terpisah(self) -> None:
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._on_health_change)
        assert "putusan.alerts, putusan.recoveries" in source
