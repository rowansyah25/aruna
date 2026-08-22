"""Duplikat dan cooldown (PASAL 11.5, 11.6).

Yang diuji bukan "apakah ia menindas", tapi apakah ia menindas hal yang benar.
Penjaga yang menelan signal yang sebenarnya baru lebih berbahaya daripada
kebisingan yang dicegahnya: yang hilang tidak meninggalkan jejak, dan tidak
ada yang bisa menemukan bahwa ia hilang.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from aruna.core.enums import Market
from aruna.signals.repetition import (
    BASE_COOLDOWN_HORIZONS,
    MATERIAL_MOVE_PCT,
    MAX_COOLDOWN_HORIZONS,
    Cooldown,
    cooldown_after_loss,
    cooldown_overridden,
    is_duplicate,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
JAM = 3600.0


def _sig(direction="BUY", entry=100.0, target=110.0, stop=95.0):
    return NS(
        direction=direction, reference_price=entry,
        target_price=target, stop_price=stop,
    )


class TestDuplikat:
    def test_setup_yang_sama_ditindas(self) -> None:
        """Satu setup yang bertahan empat jam pada bar 15 menit lolos enam
        belas kali."""
        hasil = is_duplicate(_sig(), _sig())
        assert hasil.duplicate is True

    def test_arah_berbalik_bukan_duplikat(self) -> None:
        hasil = is_duplicate(_sig("BUY"), _sig("SELL"))
        assert hasil.duplicate is False
        assert any("arah berubah" in r for r in hasil.reasons)

    def test_entry_bergeser_jauh_bukan_duplikat(self) -> None:
        hasil = is_duplicate(_sig(entry=100.0), _sig(entry=101.0))
        assert hasil.duplicate is False

    def test_entry_bergeser_sedikit_tetap_duplikat(self) -> None:
        hasil = is_duplicate(_sig(entry=100.0), _sig(entry=100.1))
        assert hasil.duplicate is True

    def test_ambang_relatif_bukan_mutlak(self) -> None:
        """Selisih lima puluh dolar besar untuk XRP dan tidak terlihat untuk
        BTC. Ambang mutlak salah di salah satu ujungnya."""
        murah = is_duplicate(
            _sig(entry=1.0, target=1.1, stop=0.9),
            _sig(entry=1.05, target=1.1, stop=0.9),
        )
        mahal = is_duplicate(
            _sig(entry=60000.0, target=66000.0, stop=57000.0),
            _sig(entry=60050.0, target=66000.0, stop=57000.0),
        )
        assert murah.duplicate is False   # bergerak 5%
        assert mahal.duplicate is True    # bergerak 0.08%

    def test_target_bergeser_juga_dihitung(self) -> None:
        hasil = is_duplicate(_sig(target=110.0), _sig(target=125.0))
        assert hasil.duplicate is False

    def test_stop_bergeser_juga_dihitung(self) -> None:
        hasil = is_duplicate(_sig(stop=95.0), _sig(stop=90.0))
        assert hasil.duplicate is False

    def test_struktur_berubah_bukan_duplikat(self) -> None:
        hasil = is_duplicate(_sig(), _sig(), structure_changed=True)
        assert hasil.duplicate is False

    def test_tanpa_prediksi_terbuka_tidak_pernah_duplikat(self) -> None:
        """Penjaga yang menganggap ketiadaan sebagai "sama" akan membungkam
        simbol itu selamanya."""
        assert is_duplicate(None, _sig()).duplicate is False

    def test_level_tak_terbandingkan_bukan_bukti_kesamaan(self) -> None:
        """Memperlakukannya begitu menindas signal yang mungkin sangat berbeda
        hanya karena harganya tidak tersimpan."""
        kosong = NS(direction="BUY", reference_price=None,
                    target_price=None, stop_price=None)
        assert is_duplicate(kosong, kosong).duplicate is False

    def test_alasan_selalu_ada(self) -> None:
        """Penindasan tanpa alasan tertulis tidak bisa ditemukan siapa pun."""
        assert is_duplicate(_sig(), _sig()).reasons
        assert is_duplicate(_sig("BUY"), _sig("SELL")).reasons
        assert is_duplicate(None, _sig()).reasons


class TestCooldown:
    def _cd(self, **kw) -> Cooldown:
        base = {"lost_at": NOW, "horizon_sec": JAM}
        base.update(kw)
        return cooldown_after_loss(**base)

    def test_kalah_memulai_jeda(self) -> None:
        cd = self._cd()
        assert cd.active(NOW + timedelta(minutes=30)) is True

    def test_jeda_berakhir(self) -> None:
        cd = self._cd()
        assert cd.active(NOW + timedelta(hours=5)) is False

    def test_diukur_dalam_horizon_bukan_jam(self) -> None:
        """Prediksi 15 menit yang dibungkam empat jam kehilangan enam belas
        peluang karena satu kekalahan."""
        pendek = cooldown_after_loss(lost_at=NOW, horizon_sec=15 * 60)
        panjang = cooldown_after_loss(lost_at=NOW, horizon_sec=24 * 3600)

        assert pendek.until < panjang.until
        assert pendek.until == NOW + timedelta(
            seconds=15 * 60 * BASE_COOLDOWN_HORIZONS
        )

    def test_rugi_besar_memperpanjang(self) -> None:
        kecil = self._cd(loss_pct=0.2)
        besar = self._cd(loss_pct=5.0)
        assert besar.until > kecil.until

    def test_volatilitas_memperpanjang(self) -> None:
        tenang = self._cd(volatility=1.0)
        liar = self._cd(volatility=1.8)
        assert liar.until > tenang.until

    def test_ada_batas_atas(self) -> None:
        """Kehati-hatian yang tidak punya ujung adalah kelumpuhan."""
        ekstrem = self._cd(loss_pct=99.0, volatility=99.0)
        assert ekstrem.horizons <= MAX_COOLDOWN_HORIZONS
        assert ekstrem.until <= NOW + timedelta(
            seconds=JAM * MAX_COOLDOWN_HORIZONS
        )

    def test_tanpa_kekalahan_tidak_ada_jeda(self) -> None:
        cd = cooldown_after_loss(lost_at=None, horizon_sec=JAM)
        assert cd.until is None
        assert cd.active(NOW) is False

    def test_horizon_tak_diketahui_tidak_membungkam(self) -> None:
        """Jeda yang tidak bisa dihitung tidak boleh berubah jadi jeda tanpa
        batas."""
        cd = cooldown_after_loss(lost_at=NOW, horizon_sec=0)
        assert cd.until is None

    def test_alasan_tercatat(self) -> None:
        cd = self._cd(loss_pct=2.0, volatility=1.5)
        assert "rugi" in cd.reason
        assert "volatilitas" in cd.reason


class TestPelangkahan:
    def test_arah_berbalik_melangkahi(self) -> None:
        """Pasar baru saja membuktikan pandangan lama salah; pandangan
        sebaliknya adalah informasi baru, bukan pengulangan."""
        boleh, alasan = cooldown_overridden(
            lost_direction="BUY", candidate_direction="SELL"
        )
        assert boleh is True
        assert "arah berbalik" in alasan

    def test_rezim_berganti_melangkahi(self) -> None:
        boleh, alasan = cooldown_overridden(
            lost_direction="BUY", candidate_direction="BUY",
            lost_regime="SIDEWAYS", candidate_regime="TRENDING_UP",
        )
        assert boleh is True
        assert "rezim berganti" in alasan

    def test_arah_dan_rezim_sama_tidak_melangkahi(self) -> None:
        """Itu analisis yang sama yang baru saja terbukti salah, diterbitkan
        ulang karena harga bergerak sedikit."""
        boleh, _ = cooldown_overridden(
            lost_direction="BUY", candidate_direction="BUY",
            lost_regime="SIDEWAYS", candidate_regime="SIDEWAYS",
        )
        assert boleh is False

    def test_rezim_tak_diketahui_tidak_melangkahi(self) -> None:
        """Ketidaktahuan bukan perubahan."""
        boleh, _ = cooldown_overridden(
            lost_direction="BUY", candidate_direction="BUY",
            lost_regime=None, candidate_regime="TRENDING_UP",
        )
        assert boleh is False


class _Store:
    """Kembalikan apa yang diminta, dan catat bahwa ia ditanya."""

    def __init__(self, *, loss=None, open_row=None, boom=False) -> None:
        self.loss = loss
        self.open_row = open_row
        self.boom = boom
        self.asked: list[str] = []

    async def latest_loss(self, **kw):
        self.asked.append("loss")
        if self.boom:
            raise RuntimeError("database berkedip")
        return self.loss

    async def latest_open(self, **kw):
        self.asked.append("open")
        if self.boom:
            raise RuntimeError("database berkedip")
        return self.open_row


def _service(store):
    """SignalService yang hanya punya bagian yang metode ini pakai.

    Dibangun lewat ``__new__`` dengan sengaja: konstruktornya menuntut selusin
    kolaborator yang tidak satu pun disentuh ``_repetition_reason``, dan
    menyediakan semuanya hanya untuk menguji satu metode akan membuat testnya
    gagal setiap kali konstruktor berubah - karena alasan yang tidak ada
    hubungannya dengan yang diuji.
    """
    from aruna.signals.service import SignalService

    svc = object.__new__(SignalService)
    svc._store = store
    return svc


def _candidate(direction="BUY", entry=100.0, target=110.0, regime="SIDEWAYS",
               locked_at=NOW):
    return NS(
        direction=NS(value=direction), reference_price=entry,
        target_price=target, regime=regime, locked_at=locked_at,
        is_directional=True,
    )


class _Horizon:
    value = "1h"
    duration = timedelta(hours=1)


class TestPenjagaDiJalurHidup:
    """Diuji lewat perilaku, bukan lewat mencari potongan teks di sumber.

    Versi pertama test ini mencocokkan substring - dan ketiganya tetap hijau
    saat penjaganya dicabut, karena nama metodenya tetap muncul di definisi
    walau panggilannya hilang.
    """

    async def _alasan(self, store, **kw) -> str | None:
        return await _service(store)._repetition_reason(
            NS(symbol="BTC/USDT", id=1), Market.CRYPTO, _Horizon(),
            _candidate(**kw), None,
        )

    async def test_duplikat_ditahan(self) -> None:
        store = _Store(open_row={
            "direction": "BUY", "reference_price": 100.0,
            "target_price": 110.0, "regime": "SIDEWAYS",
        })
        alasan = await self._alasan(store)
        assert alasan is not None
        assert "duplikat" in alasan

    async def test_setup_berbeda_lolos(self) -> None:
        store = _Store(open_row={
            "direction": "SELL", "reference_price": 100.0,
            "target_price": 90.0, "regime": "SIDEWAYS",
        })
        assert await self._alasan(store) is None

    async def test_tanpa_apa_pun_tersimpan_lolos(self) -> None:
        assert await self._alasan(_Store()) is None

    async def test_cooldown_menahan_sesudah_kalah(self) -> None:
        store = _Store(loss={
            "exit_at": NOW - timedelta(minutes=10), "net_pnl_pct": -1.5,
            "direction": "BUY", "regime": "SIDEWAYS",
        })
        alasan = await self._alasan(store)
        assert alasan is not None
        assert "cooldown" in alasan

    async def test_cooldown_berakhir(self) -> None:
        store = _Store(loss={
            "exit_at": NOW - timedelta(hours=6), "net_pnl_pct": -1.5,
            "direction": "BUY", "regime": "SIDEWAYS",
        })
        assert await self._alasan(store) is None

    async def test_arah_berbalik_melangkahi_cooldown(self) -> None:
        store = _Store(loss={
            "exit_at": NOW - timedelta(minutes=10), "net_pnl_pct": -1.5,
            "direction": "BUY", "regime": "SIDEWAYS",
        })
        assert await self._alasan(store, direction="SELL") is None

    async def test_cooldown_diperiksa_sebelum_duplikat(self) -> None:
        """Sesudah kalah, prediksi berikutnya biasanya TIDAK identik - harga
        sudah bergerak, jadi penjaga duplikat meloloskannya. Justru itu yang
        PASAL 11.5 cegah.
        """
        store = _Store(
            loss={
                "exit_at": NOW - timedelta(minutes=10), "net_pnl_pct": -1.5,
                "direction": "BUY", "regime": "SIDEWAYS",
            },
            open_row={
                "direction": "BUY", "reference_price": 90.0,
                "target_price": 99.0, "regime": "SIDEWAYS",
            },
        )
        alasan = await self._alasan(store)
        assert alasan is not None and "cooldown" in alasan

    async def test_database_berkedip_tidak_membungkam(self) -> None:
        """Penjaga yang jadi pembungkam saat database gagal dibaca menghapus
        prediksi tanpa jejak, dan ketiadaannya tidak bisa ditemukan siapa pun.
        """
        assert await self._alasan(_Store(boom=True)) is None

    def test_repository_punya_kedua_kuerinya(self) -> None:
        from aruna.db.repositories.signals import SignalRepository

        assert hasattr(SignalRepository, "latest_open")
        assert hasattr(SignalRepository, "latest_loss")

    def test_hanya_yang_terpublikasi_dihitung_terbuka(self) -> None:
        """Verdict yang ARUNA sendiri tolak publikasikan bukan prediksi yang
        sedang berjalan; memperlakukannya begitu membungkam simbol itu karena
        catatan yang tidak pernah dikirim ke siapa pun."""
        import inspect

        from aruna.db.repositories.signals import SignalRepository

        source = inspect.getsource(SignalRepository.latest_open)
        assert "published = TRUE" in source

    def test_cooldown_dihitung_dari_saat_kalah_diketahui(self) -> None:
        """`exit_at`, bukan `locked_at`: keduanya bisa terpisah berjam-jam
        pada horizon panjang."""
        import inspect

        from aruna.db.repositories.signals import SignalRepository

        source = inspect.getsource(SignalRepository.latest_loss)
        assert "ORDER BY t.exit_at DESC" in source


def test_ambang_masuk_akal() -> None:
    assert 0 < MATERIAL_MOVE_PCT < 10
    assert 0 < BASE_COOLDOWN_HORIZONS <= MAX_COOLDOWN_HORIZONS
