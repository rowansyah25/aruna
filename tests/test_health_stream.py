"""Kesehatan aliran (PASAL 3, 4, 35, 36).

Satu bentuk kegagalan yang tidak dimiliki poll: **tersambung dan senyap**.
Poll yang gagal mengembalikan galat; socket yang menggantung tidak
mengembalikan apa pun dan terlihat persis seperti pasar sepi. Kalau komponen
ini menyimpulkan sehat dari status socket, ia buta tepat pada kegagalan yang
paling mahal - dan itu bukan hipotesis: futures Binance melakukan persis itu
di jaringan ini.
"""

from __future__ import annotations

from typing import Any

from aruna.core.enums import HealthStatus
from aruna.health.stream import STALE_QUOTE_SEC, StreamCheck


class _Stream:
    def __init__(self, **state: Any) -> None:
        self._state = {
            "source": "binance-spot-ws",
            "connected": True,
            "connected_since": None,
            "symbols": ["BTC/USDT", "ETH/USDT"],
            "disconnects": 0,
            "messages": 100,
            "snapshots": 2,
            "snapshot_failures": 0,
            "last_error": None,
            "ages_sec": {"BTC/USDT": 0.2, "ETH/USDT": 0.3},
        } | state
        self.running = state.pop("running", True) if "running" in state else True

    @property
    def connected(self) -> bool:
        return bool(self._state["connected"])

    def state(self) -> dict[str, Any]:
        return dict(self._state)


class TestSehatHanyaKalauDataMengalir:
    async def test_mengalir_itu_up(self) -> None:
        health = await StreamCheck(_Stream()).check()
        assert health.status is HealthStatus.UP
        assert "2 simbol mengalir" in health.message

    async def test_tersambung_tapi_belum_ada_kutipan_itu_down(self) -> None:
        """Bentuk menggantung. Socket bilang hidup, data tidak ada."""
        health = await StreamCheck(
            _Stream(ages_sec={"BTC/USDT": None, "ETH/USDT": None})
        ).check()
        assert health.status is HealthStatus.DOWN
        assert "menggantung" in health.message
        assert "bukan pasar yang sepi" in health.message

    async def test_semua_simbol_diam_serentak_itu_down(self) -> None:
        """Tidak satu pun simbol mengirim kutipan: socket-nya menggantung.

        Itulah satu-satunya bentuk diam yang tidak bisa dijelaskan pasar sepi -
        pasar tidak membuat BTC dan ETH berhenti berdagang pada detik yang sama.
        """
        health = await StreamCheck(
            _Stream(ages_sec={"BTC/USDT": 120.0, "ETH/USDT": 130.0})
        ).check()
        assert health.status is HealthStatus.DOWN
        assert "menggantung" in health.message

    async def test_satu_koin_sepi_bukan_masalah_aliran(self) -> None:
        """**Perubahan yang disengaja, dengan angkanya.**

        Versi lama menyatakan DEGRADED begitu SATU simbol melewati sepuluh
        detik. Itu benar selama daftarnya berisi lima pasangan teramai; ia
        salah begitu daftarnya menjadi dua puluh.

        Terukur dari 1000 perdagangan terakhir tiap simbol: APT berjeda di atas
        sepuluh detik selama 78% waktu dan pernah 293 detik, OP 73%, DOT 68% -
        sementara BTC, ETH dan SOL tidak pernah sama sekali. Rata-rata 5,6 dari
        20 simbol terlihat basi pada saat acak. Aturan lama karena itu
        melaporkan DEGRADED hampir sepanjang waktu, dan alert aliran SELALU
        didorong ke Telegram.

        Yang lebih buruk dari berisiknya: pada malam sepi bagian yang basi bisa
        mendekati ambang DOWN 50%, dan ARUNA akan menyatakan aliran mati
        padahal ia mengalir.
        """
        health = await StreamCheck(
            _Stream(
                ages_sec={
                    "BTC/USDT": 0.2,
                    "ETH/USDT": 0.3,
                    "SOL/USDT": 0.4,
                    # Jeda perdagangan yang sungguh-sungguh biasa untuk koin
                    # sepi: terukur 293 detik pada APT.
                    "APT/USDT": 250.0,
                }
            )
        ).check()
        assert health.status is HealthStatus.UP, health.message

    async def test_langganan_yang_mati_tetap_ketahuan(self) -> None:
        """Sifat yang dijaga test lama, sekarang dijaga pada ambang yang benar.

        Aliran bisa kehilangan sebagian langganannya sementara socket-nya tetap
        sehat dan simbol lain tetap mengalir. Diam selama itu tidak bisa
        dijelaskan pasar sepi - jeda terpanjang yang pernah terukur di seluruh
        daftar adalah 293 detik.

        Umurnya ditulis sebagai angka, **bukan** sebagai
        ``DEAD_SUBSCRIPTION_SEC + 60``. Bentuk kedua itu lulus untuk ambang
        berapa pun, termasuk ambang yang praktis tak terhingga - ia menguji
        mekanismenya sambil membiarkan angkanya bebas. Satu jam diam adalah
        langganan mati menurut ukuran mana pun.
        """
        health = await StreamCheck(
            _Stream(
                ages_sec={
                    "BTC/USDT": 0.2,
                    "ETH/USDT": 0.3,
                    "SOL/USDT": 0.4,
                    "XRP/USDT": 3600.0,
                }
            )
        ).check()
        assert health.status is HealthStatus.DEGRADED, health.message
        assert "1 dari 4" in health.message
        assert "XRP/USDT" in health.message

    async def test_gantungnya_socket_tetap_ketahuan_secepat_dulu(self) -> None:
        """Yang TIDAK boleh ikut melonggar.

        Melonggarkan ambang basi per simbol adalah cara termudah menghapus
        kebisingan di atas, dan ia menukar kebisingan dengan kebutaan: socket
        yang menggantung baru ketahuan bermenit-menit kemudian.

        Karena itu ambangnya tidak disentuh - yang berubah adalah kepada siapa
        ia dibandingkan. Kutipan termuda, bukan tiap simbol. BTC mencetak
        beberapa kali per detik, jadi kecepatan mendeteksinya tetap sama.
        """
        health = await StreamCheck(
            _Stream(
                ages_sec={
                    "BTC/USDT": STALE_QUOTE_SEC + 1,
                    "ETH/USDT": STALE_QUOTE_SEC + 2,
                    "APT/USDT": 400.0,
                }
            )
        ).check()
        assert health.status is HealthStatus.DOWN, health.message

    async def test_ambang_kehidupan_tetap_ketat(self) -> None:
        """Penjaga untuk sisi yang berlawanan dari test di bawah."""
        from aruna.health.stream import STALE_QUOTE_SEC as ambang

        assert ambang <= 15.0, (
            "ambang kehidupan dilonggarkan; socket yang menggantung akan "
            "ketahuan terlambat"
        )

    async def test_ambang_langganan_mati_di_atas_jeda_pasar_terukur(self) -> None:
        """Penjaga untuk angkanya sendiri.

        Kalau seseorang kelak memperketatnya kembali mendekati jeda pasar yang
        nyata, kebisingan yang baru saja dihapus akan kembali - dan test ini
        yang merah, bukan operator yang menemukannya lewat Telegram.
        """
        from aruna.health.stream import DEAD_SUBSCRIPTION_SEC

        JEDA_PASAR_TERPANJANG_TERUKUR = 293.0
        assert DEAD_SUBSCRIPTION_SEC >= JEDA_PASAR_TERPANJANG_TERUKUR * 2

        # Batas atasnya juga, dan alasannya berlawanan arah: ambang yang
        # dilonggarkan sampai praktis tak terhingga menghapus kebisingan dengan
        # menghapus deteksinya. Langganan yang mati harus tetap ketahuan dalam
        # waktu yang masih berguna bagi operator.
        assert DEAD_SUBSCRIPTION_SEC <= 3600.0, (
            "langganan yang putus tidak akan ketahuan dalam satu jam"
        )

    async def test_terputus_itu_down_dan_menyebut_cadangannya(self) -> None:
        health = await StreamCheck(_Stream(connected=False, disconnects=3)).check()
        assert health.status is HealthStatus.DOWN
        assert "terputus" in health.message
        assert "poll REST" in health.message or "backoff" in health.message

    async def test_snapshot_gagal_disebut(self) -> None:
        """PASAL 9: lubang sesudah sambung ulang yang tidak tertutup adalah
        fakta yang harus terucap, bukan angka yang tinggal di details."""
        health = await StreamCheck(_Stream(snapshot_failures=4)).check()
        assert health.status is HealthStatus.DEGRADED
        assert "4 snapshot REST gagal" in health.message


class TestTidakMengarangKetikaTidakAda:
    async def test_tidak_dirangkai_dikatakan_bukan_dilaporkan_sehat(self) -> None:
        """Nol simbol basi karena tidak ada simbol adalah nol yang berarti
        'tidak ditanya' (SPEC 4, 49)."""
        health = await StreamCheck(None).check()
        assert health.status is HealthStatus.UP
        assert "tidak aktif" in health.message
        assert health.details["wired"] is False

    async def test_proses_sekali_jalan_bukan_kegagalan(self) -> None:
        """Aturan A: perintah CLI pendek tidak menjalankan loop, jadi aliran
        yang mati di situ adalah keadaan yang benar."""
        stream = _Stream()
        stream.running = False
        health = await StreamCheck(stream, background=False).check()
        assert health.status is HealthStatus.UP
        assert "sekali-jalan" in health.message

    async def test_task_mati_pada_proses_panjang_itu_down(self) -> None:
        stream = _Stream()
        stream.running = False
        health = await StreamCheck(stream, background=True).check()
        assert health.status is HealthStatus.DOWN
        assert "tidak berjalan" in health.message


class TestTerpasangDiApp:
    """Kabelnya. Cacat tertua repo ini adalah komponen yang hanya hidup di
    test - dan health yang tidak terdaftar tidak melaporkan apa pun tepat saat
    ada yang perlu dilaporkan."""

    def test_streamcheck_terdaftar_di_startup(self) -> None:
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._start_health_monitor)
        assert "StreamCheck" in source, "StreamCheck tidak terdaftar di health monitor"

    async def test_aliran_putus_sampai_ke_telegram(self) -> None:
        """PASAL 36: jangan diam kalau feed mati.

        Dibuktikan lewat hook yang sungguhan, bukan dengan membangun jalur
        alert kedua. ``_on_health_change`` mengirim untuk komponen mana pun
        yang berubah, dan ``StreamCheck`` sudah terdaftar - jadi mekanisme
        yang ada SUDAH menutup pasal ini. Test ini yang membuktikannya, dan
        yang akan merah kalau seseorang menyempitkan hook itu ke daftar
        komponen tertentu.
        """
        from aruna.app import ArunaApplication
        from aruna.health.models import ComponentHealth, HealthReport

        terkirim: list[str] = []

        class _Bot:
            started = True

            async def send(self, text: str) -> bool:
                terkirim.append(text)
                return True

        class _Cache:
            async def set_json(self, *_a, **_kw) -> None:
                return None

        app = ArunaApplication.__new__(ArunaApplication)
        app.settings = __import__(
            "aruna.core.config", fromlist=["get_settings"]
        ).get_settings()
        from aruna.health.alerts import HealthAlertPolicy

        app.bot = _Bot()
        app.cache = _Cache()
        app._health_alerted = True  # bukan sapuan pertama
        app._alert_policy = HealthAlertPolicy()

        mati = ComponentHealth(
            name="stream:binance-spot",
            status=HealthStatus.DOWN,
            message="terputus, 3 kali sejauh ini - sedang menyambung ulang",
        )
        report = HealthReport(components=(mati,))

        await ArunaApplication._on_health_change(app, report, (mati,))

        assert terkirim, "aliran mati tidak sampai ke operator"
        assert "stream:binance-spot" in terkirim[0]

    def test_aliran_dirangkai_dan_dijaga_background(self) -> None:
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._start_stream)
        assert "BinanceSpotStream(" in source
        assert "self._background" in source, "aturan A: loop harus di balik penjaga"
