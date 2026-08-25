"""Sekali sehari, pukul 00:00 WIB, dan tetap sekali sesudah restart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from aruna.core.enums import HealthStatus
from aruna.notify.daily import CouncilScore, MarketBlock, SelfCorrection, Tally
from aruna.notify.daily_service import (
    DAILY_SENT_KEY,
    WIB,
    DailyReportService,
    day_window,
)

# 00:03 WIB tanggal 18 = 17:03 UTC tanggal 17.
LEWAT_TENGAH_MALAM = datetime(2026, 8, 17, 17, 3, tzinfo=UTC)


class _Repo:
    def __init__(self) -> None:
        self.windows: list[tuple] = []

    async def futures(self, *, start, end):
        self.windows.append((start, end))
        return MarketBlock("FUTURES / PERPETUAL", "🔮", Tally(total=1, win=1))

    async def spot_or_equity(self, *, market_code, title, icon, start, end):
        return MarketBlock(title, icon, Tally())

    async def agents(self):
        return ()

    async def council(self, *, start, end):
        return CouncilScore()

    async def correction(self, *, start, end, model_version):
        return SelfCorrection(model_version=model_version)


class _Sender:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return self.ok


class _State:
    def __init__(self, stored=None) -> None:
        self.store = dict(stored or {})

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, *, actor):
        self.store[key] = value


def _service(**kwargs) -> DailyReportService:
    base = {"repo": _Repo(), "sender": _Sender()}
    base.update(kwargs)
    return DailyReportService(**base)  # type: ignore[arg-type]


class TestJendelaHari:
    def test_batasnya_tengah_malam_wib_bukan_utc(self) -> None:
        """Laporan "18 Agustus" yang dipotong pukul lima sore adalah laporan
        hari yang salah."""
        awal, akhir = day_window(LEWAT_TENGAH_MALAM)

        assert awal.astimezone(WIB).hour == 0
        assert akhir.astimezone(WIB).hour == 0
        assert (akhir - awal) == timedelta(days=1)

    def test_yang_dilaporkan_hari_kemarin(self) -> None:
        """Laporan pukul 00:00 yang berisi hari ini berisi nol menit."""
        awal, akhir = day_window(LEWAT_TENGAH_MALAM)
        assert awal.astimezone(WIB).day == 17
        assert akhir.astimezone(WIB).day == 18

    def test_siang_hari_pun_menunjuk_kemarin(self) -> None:
        siang = datetime(2026, 8, 18, 5, 0, tzinfo=UTC)  # 12:00 WIB
        awal, _ = day_window(siang)
        assert awal.astimezone(WIB).day == 17


class TestSekaliSehari:
    async def test_terkirim_sekali(self) -> None:
        sender = _Sender()
        svc = _service(sender=sender)

        assert await svc.run(LEWAT_TENGAH_MALAM) is True
        assert len(sender.sent) == 1

    async def test_tidak_dikirim_dua_kali_hari_yang_sama(self) -> None:
        sender = _Sender()
        svc = _service(sender=sender)

        await svc.run(LEWAT_TENGAH_MALAM)
        await svc.run(LEWAT_TENGAH_MALAM + timedelta(hours=6))
        assert len(sender.sent) == 1

    async def test_hari_berikutnya_dikirim_lagi(self) -> None:
        sender = _Sender()
        svc = _service(sender=sender)

        await svc.run(LEWAT_TENGAH_MALAM)
        await svc.run(LEWAT_TENGAH_MALAM + timedelta(days=1))
        assert len(sender.sent) == 2

    async def test_restart_tidak_mengirim_ulang(self) -> None:
        """Penjaga proses memang membuat proses ini mati lalu hidup lagi.
        Penanda yang hanya ada di memori akan hilang setiap kali."""
        state = _State({DAILY_SENT_KEY: {"date": "2026-08-18"}})
        sender = _Sender()
        svc = _service(sender=sender, state=state)  # proses baru, cache kosong

        assert await svc.run(LEWAT_TENGAH_MALAM) is False
        assert sender.sent == []

    async def test_tanggal_disimpan_supaya_bertahan(self) -> None:
        state = _State()
        svc = _service(state=state)
        await svc.run(LEWAT_TENGAH_MALAM)

        assert state.store[DAILY_SENT_KEY] == {"date": "2026-08-18"}

    async def test_gagal_kirim_tidak_distempel(self) -> None:
        """Menstempel duluan menghapus laporan hari itu selamanya karena satu
        kegagalan jaringan - dan laporan yang hilang tidak berjejak."""
        state = _State()
        sender = _Sender(ok=False)
        svc = _service(sender=sender, state=state)

        assert await svc.run(LEWAT_TENGAH_MALAM) is False
        assert DAILY_SENT_KEY not in state.store

        sender.ok = True
        assert await svc.run(LEWAT_TENGAH_MALAM) is True

    async def test_terlambat_tetap_terkirim(self) -> None:
        """Tidak ada syarat "tepat pukul 00:00": tick bisa terlewat karena
        restart atau mesin yang tidur."""
        sender = _Sender()
        svc = _service(sender=sender)
        telat = LEWAT_TENGAH_MALAM + timedelta(hours=9)

        assert await svc.run(telat) is True


class TestTanpaTujuanKirim:
    async def test_diam_kalau_belum_ada_bot(self) -> None:
        """Bukan "gagal kirim". Gagal kirim dicatat lalu dicoba lagi; ini akan
        mencetak peringatan itu tiap lima belas detik selamanya."""
        class _Belum:
            def ready(self) -> bool:
                return False

            async def send(self, text: str) -> bool:  # pragma: no cover
                raise AssertionError("tidak boleh dipanggil")

        svc = _service(sender=_Belum())
        assert await svc.run(LEWAT_TENGAH_MALAM) is False

    async def test_bot_yang_datang_belakangan_tetap_dilayani(self) -> None:
        """`_start_upkeep` berjalan sebelum `_start_telegram`, jadi bot memang
        belum ada saat laporan ini dibangun."""
        kotak: dict[str, object] = {"bot": None}

        class _Nanti:
            def ready(self) -> bool:
                return kotak["bot"] is not None

            async def send(self, text: str) -> bool:
                kotak.setdefault("sent", []).append(text)  # type: ignore[union-attr]
                return True

        svc = _service(sender=_Nanti())
        assert await svc.run(LEWAT_TENGAH_MALAM) is False

        kotak["bot"] = object()  # Telegram menyusul
        assert await svc.run(LEWAT_TENGAH_MALAM) is True


class TestAgentDihitungLangsung:
    """Tabel snapshot `agent_reliability` hanya terisi ketika seseorang
    menjalankan `aruna autopsy` dengan persist - dan laporan harian berjalan
    sendiri tiap tengah malam, tanpa ada yang menjalankan apa pun.

    Terukur saat ditemukan: 144 opini terskor tersedia, dan bagian AGENT
    PERFORMANCE tetap kosong karena snapshot-nya belum pernah ditulis.
    """

    def _repo(self, opinions: int, correct: int):
        """Fake yang bentuknya mengikuti sumber SEKARANG: keputusan hidup.

        `agent_outcomes` tidak lagi membaca `direction_correct` dari
        `paper_results` - tabel itu berhenti tumbuh saat spot dicabut. Benar
        atau salahnya sekarang dihitung dari gerak candle sesudah keputusan,
        jadi fake ini memberi tiap sesi simbolnya sendiri: yang benar naik,
        yang salah turun. Palsu yang bidangnya menyimpang dari sumber aslinya
        adalah cara test ini bisa hijau di atas kode yang rusak.
        """
        import json
        from datetime import UTC, datetime, timedelta

        from aruna.db.repositories.daily import DailyRepository

        bobot = json.dumps([{"role": "TECHNICAL", "decision": "BUY"}])
        saat = datetime(2026, 8, 20, 12, 0, tzinfo=UTC).replace(tzinfo=None)

        sesi = [
            {
                "id": i,
                "market_code": "CRYPTO",
                "symbol": f"S{i}/USDT",
                "interval_code": "1d",
                "decided_at": saat,
                "council_decision": "BUY",
                "weights": bobot,
            }
            for i in range(opinions)
        ]
        candles = []
        for i in range(opinions):
            # Council bilang BUY, jadi naik = benar.
            sesudah = 110.0 if i < correct else 90.0
            candles.append({
                "market_code": "CRYPTO", "symbol": f"S{i}/USDT",
                "interval_code": "1d",
                "close_time": saat - timedelta(days=1), "close": 100.0,
            })
            candles.append({
                "market_code": "CRYPTO", "symbol": f"S{i}/USDT",
                "interval_code": "1d",
                "close_time": saat + timedelta(days=1), "close": sesudah,
            })

        class _Db:
            async def fetch(self, sql, *args):
                if "council_sessions" in sql:
                    return list(sesi)
                if "FROM candles" in sql:
                    return list(candles)
                return []

        return DailyRepository(_Db())

    async def test_dibaca_dari_hasil_bukan_dari_snapshot(self) -> None:
        from aruna.learning.reliability import MIN_RELIABILITY_SAMPLE

        agents = await self._repo(MIN_RELIABILITY_SAMPLE, MIN_RELIABILITY_SAMPLE).agents()
        assert [a.name for a in agents] == ["TECHNICAL"]
        assert agents[0].win_rate == 100.0

    async def test_sampel_kurang_tidak_muncul(self) -> None:
        """Disiplin INSUFFICIENT_SAMPLE tetap dipegang mesin keandalan."""
        assert await self._repo(5, 5).agents() == ()


class TestKabelKeApp:
    def test_daily_dioper_ke_upkeep(self) -> None:
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._start_upkeep)
        assert "daily=" in source, "UpkeepLoop dibangun tanpa laporan harian"

    def test_bot_tidak_dibaca_saat_dibangun(self) -> None:
        """Versi pertama memeriksa `self.bot is None` di sini dan mengembalikan
        None - laporan harian tidak akan pernah jalan, tanpa error dan tanpa
        log."""
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._build_daily)
        assert "if self.bot is None" not in source
        assert "_LateSender" in source

    def test_atribut_monitor_ada(self) -> None:
        """`self.health` tidak pernah ada di kelas ini; namanya `self.monitor`,
        dan propertinya `.latest`."""
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._build_daily)
        assert "self.monitor.latest" in source
        assert hasattr(app_module.HealthMonitor, "latest")

    def test_loop_benar_benar_memanggilnya(self) -> None:
        import inspect

        from aruna.upkeep import loop as loop_module

        source = inspect.getsource(loop_module.UpkeepLoop.cycle)
        assert "_send_daily" in source, "daily dibangun tapi tidak pernah dipanggil"


class TestStatusSistem:
    async def test_komponen_sehat_hijau(self) -> None:
        report = SimpleNamespace(components=[
            SimpleNamespace(name="database", status=HealthStatus.UP),
        ])
        sender = _Sender()
        svc = _service(sender=sender, health=lambda: report)
        await svc.run(LEWAT_TENGAH_MALAM)

        assert "🗄 Database:\n🟢 HEALTHY" in sender.sent[0]

    async def test_komponen_mati_merah(self) -> None:
        report = SimpleNamespace(components=[
            SimpleNamespace(name="database", status=HealthStatus.DOWN),
        ])
        sender = _Sender()
        svc = _service(sender=sender, health=lambda: report)
        await svc.run(LEWAT_TENGAH_MALAM)

        assert "🗄 Database:\n🔴 DOWN" in sender.sent[0]

    async def test_tidak_diperiksa_bukan_hijau(self) -> None:
        """Hijau berarti "sudah diperiksa dan sehat". Memberi hijau pada yang
        tidak diperiksa adalah kebohongan yang paling mudah dipercaya."""
        sender = _Sender()
        svc = _service(sender=sender, health=lambda: None)
        await svc.run(LEWAT_TENGAH_MALAM)

        assert "🔴 UNKNOWN" in sender.sent[0]
        assert "🟢 UNKNOWN" not in sender.sent[0]

    async def test_uptime_dicetak(self) -> None:
        sender = _Sender()
        svc = _service(sender=sender, uptime_seconds=lambda: 86_280)
        await svc.run(LEWAT_TENGAH_MALAM)

        assert "⏱ Uptime:\n23h 58m" in sender.sent[0]


class TestIsinya:
    async def test_repo_ditanya_jendela_yang_benar(self) -> None:
        repo = _Repo()
        svc = _service(repo=repo)
        await svc.run(LEWAT_TENGAH_MALAM)

        awal, akhir = repo.windows[0]
        assert awal.astimezone(WIB).day == 17
        assert akhir.astimezone(WIB).day == 18

    async def test_satu_pasar_sejak_spot_dicabut(self) -> None:
        """Dulu tiga pasar; sejak 2026-08-25 hanya futures.

        SPOT dan SAHAM INDONESIA dibaca dari ``paper_trades``, dan tabel itu
        berhenti tumbuh begitu jalur spot dicabut. Blok yang membaca tabel beku
        akan melaporkan angka kemarin sebagai angka hari ini - jadi yang
        dihapus bloknya, bukan cuma isinya.
        """
        sender = _Sender()
        svc = _service(sender=sender)
        await svc.run(LEWAT_TENGAH_MALAM)

        assert "FUTURES / PERPETUAL" in sender.sent[0]
        assert "SAHAM INDONESIA" not in sender.sent[0]

    async def test_penutup_analis_saja(self) -> None:
        sender = _Sender()
        svc = _service(sender=sender)
        await svc.run(LEWAT_TENGAH_MALAM)

        assert sender.sent[0].endswith(
            "🤖 ARUNA ANALYST ONLY\n⚡ EXECUTION: USER"
        )
