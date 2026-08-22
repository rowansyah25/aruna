"""Laporan harian: format persis, dan angka yang tidak boleh berbohong."""

from __future__ import annotations

from datetime import UTC, datetime

from aruna.notify.daily import (
    RULE,
    AgentScore,
    Component,
    CouncilScore,
    DailyReport,
    MarketBlock,
    SelfCorrection,
    Tally,
    render_daily,
)

HARI = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)


def _report(**kwargs) -> DailyReport:
    base = {
        "date": HARI,
        "markets": (
            MarketBlock(
                title="FUTURES / PERPETUAL",
                icon="🔮",
                tally=Tally(total=11, win=10, loss=1, active=0),
                long=Tally(total=6, win=6, loss=0),
                short=Tally(total=5, win=4, loss=1),
            ),
        ),
    }
    base.update(kwargs)
    return DailyReport(**base)  # type: ignore[arg-type]


class TestHitungan:
    def test_win_rate_seperti_contoh_operator(self) -> None:
        """10 / 11 x 100 = 90.91% (PASAL 3)."""
        assert Tally(win=10, loss=1, active=5).win_rate == 10 / 11 * 100
        assert _pctstr(Tally(win=10, loss=1).win_rate) == "90.91%"

    def test_active_tidak_masuk_penyebut(self) -> None:
        """Cara termudah membuat angka terlihat bagus adalah memasukkan yang
        belum selesai ke sisi yang menguntungkan (PASAL 6)."""
        tanpa = Tally(win=10, loss=1, active=0).win_rate
        dengan = Tally(win=10, loss=1, active=99).win_rate
        assert tanpa == dengan

    def test_tanpa_hasil_bukan_nol_persen(self) -> None:
        """0.00% terbaca sebagai nol menang dari sekian percobaan. Tidak ada
        percobaan sama sekali (PASAL 5)."""
        kosong = Tally(total=0, win=0, loss=0, active=0)
        assert kosong.win_rate is None
        assert kosong.loss_rate is None

    def test_active_saja_belum_punya_win_rate(self) -> None:
        assert Tally(total=5, active=5).win_rate is None

    def test_win_dan_loss_rate_berjumlah_seratus(self) -> None:
        t = Tally(win=7, loss=3)
        assert round(t.win_rate + t.loss_rate, 6) == 100.0

    def test_total_keseluruhan_dijumlah_dari_bloknya(self) -> None:
        """Laporan yang bagian bawahnya membantah bagian atasnya tidak bisa
        dipercaya di kedua bagian."""
        report = _report(
            markets=(
                MarketBlock("A", "🔮", Tally(total=11, win=10, loss=1)),
                MarketBlock("B", "💰", Tally(total=8, win=7, loss=1)),
                MarketBlock("C", "📈", Tally(total=10, win=9, loss=1)),
            )
        )
        assert report.overall.total == 29
        assert report.overall.win == 26
        assert report.overall.loss == 3
        assert f"{report.overall.win_rate:.2f}" == "89.66"


class TestFormatPersis:
    def test_judul_dan_penutup(self) -> None:
        teks = render_daily(_report())
        assert teks.startswith("📊 ARUNA DAILY PERFORMANCE\n")
        assert teks.endswith("🤖 ARUNA ANALYST ONLY\n⚡ EXECUTION: USER")

    def test_tanggal_huruf_besar_inggris(self) -> None:
        """Diambil dari tabel, bukan strftime: locale mesin bisa mengubahnya
        jadi "Agustus" dan format yang ikut pengaturan sistem bukan format
        yang ditentukan."""
        assert "18 AUGUST 2026" in render_daily(_report())

    def test_periode_tetap(self) -> None:
        # EN DASH, seperti di template operator - bukan hyphen.
        assert "00:00 – 23:59" in render_daily(_report())  # noqa: RUF001

    def test_pemisah_dua_puluh_karakter(self) -> None:
        assert RULE == "━" * 20
        assert RULE in render_daily(_report())

    def test_urutan_section_sesuai_template(self) -> None:
        teks = render_daily(
            _report(
                agents=(AgentScore("Agent 1", 91.3),),
                components=(Component("Server", "🖥", "HEALTHY"),),
            )
        )
        urutan = [
            "🔮 FUTURES / PERPETUAL",
            "🏆 TOTAL PERFORMANCE",
            "🤖 AGENT PERFORMANCE",
            "🧠 COUNCIL PERFORMANCE",
            "🧠 SELF-CORRECTION",
            "⚙️ SYSTEM STATUS",
        ]
        posisi = [teks.index(bagian) for bagian in urutan]
        assert posisi == sorted(posisi), urutan

    def test_rincian_arah_pakai_cabang_pohon(self) -> None:
        teks = render_daily(_report())
        assert "   ├─ 🟢 WIN:" in teks
        assert "   └─ 🔴 LOSS:" in teks

    def test_overall_dinamai_overall(self) -> None:
        teks = render_daily(_report())
        assert "📈 Overall Win Rate:" in teks
        assert "📉 Overall Loss Rate:" in teks

    def test_short_disembunyikan_kalau_pasar_tak_punya(self) -> None:
        """Spot dan saham di contoh operator hanya punya LONG."""
        teks = render_daily(
            _report(
                markets=(
                    MarketBlock(
                        "SPOT", "💰",
                        Tally(total=8, win=7, loss=1),
                        long=Tally(total=8, win=7, loss=1),
                    ),
                )
            )
        )
        assert "🔴 SHORT:" not in teks
        assert "🟢 LONG:" in teks

    def test_short_ditampilkan_kalau_ada(self) -> None:
        """Menyembunyikannya akan menghapus sebagian catatan dari laporan yang
        gunanya justru mencatat."""
        teks = render_daily(
            _report(
                markets=(
                    MarketBlock(
                        "SPOT", "💰",
                        Tally(total=9, win=7, loss=2),
                        long=Tally(total=8, win=7, loss=1),
                        short=Tally(total=1, win=0, loss=1),
                    ),
                )
            )
        )
        assert "🔴 SHORT:" in teks


class TestTidakAdaAngkaRusak:
    def test_laporan_kosong_tidak_menghasilkan_nan(self) -> None:
        """NaN, null, undefined, Infinity - tidak satu pun boleh terbentuk
        (PASAL 5)."""
        teks = render_daily(
            DailyReport(
                date=HARI,
                markets=(
                    MarketBlock("FUTURES / PERPETUAL", "🔮", Tally()),
                    MarketBlock("SPOT", "💰", Tally()),
                    MarketBlock("SAHAM INDONESIA", "📈", Tally()),
                ),
            )
        )
        for busuk in ("nan", "null", "undefined", "infinity", "none", "inf%"):
            assert busuk not in teks.lower(), busuk
        assert "N/A" in teks

    def test_hari_kosong_tetap_mencetak_nol(self) -> None:
        teks = render_daily(DailyReport(date=HARI, markets=(
            MarketBlock("SPOT", "💰", Tally()),
        )))
        assert "📊 Total Signal:\n0" in teks


class TestAgent:
    def test_medali_urut_dari_tertinggi(self) -> None:
        teks = render_daily(
            _report(agents=(
                AgentScore("Agent 1", 91.30),
                AgentScore("Agent 2", 94.12),
                AgentScore("Agent 4", 88.89),
            ))
        )
        assert teks.index("🥇 Agent 2:") < teks.index("🥈 Agent 1:")
        assert teks.index("🥈 Agent 1:") < teks.index("🥉 Agent 4:")

    def test_terbawah_disebut(self) -> None:
        teks = render_daily(
            _report(agents=(
                AgentScore("Agent 1", 91.30),
                AgentScore("Agent 2", 94.12),
                AgentScore("Agent 4", 88.89),
                AgentScore("Agent 5", 81.25),
            ))
        )
        assert "⚠️ Lowest:\nAgent 5 — 81.25%" in teks

    def test_terbawah_tidak_disebut_kalau_ia_juga_pemenang(self) -> None:
        """Dengan dua agent, "terbaik" dan "terburuk" adalah orang yang sama
        disebut dua kali - itu bukan informasi."""
        teks = render_daily(
            _report(agents=(AgentScore("A", 90.0), AgentScore("B", 80.0)))
        )
        assert "⚠️ Lowest:" not in teks

    def test_tanpa_agent_terskor_dikatakan(self) -> None:
        """Section kosong tanpa keterangan terbaca seperti pesan terpotong."""
        teks = render_daily(_report(agents=()))
        assert "🤖 AGENT PERFORMANCE" in teks
        assert "Belum ada agent" in teks


class TestCouncilDanStatus:
    def test_akurasi_council(self) -> None:
        teks = render_daily(_report(council=CouncilScore(correct=26, incorrect=3)))
        assert "📊 Accuracy:\n89.66%" in teks

    def test_council_tanpa_data_tidak_nol_persen(self) -> None:
        assert CouncilScore().accuracy is None
        assert "📊 Accuracy:\nN/A" in render_daily(_report())

    def test_komponen_sehat_hijau(self) -> None:
        teks = render_daily(
            _report(components=(Component("Database", "🗄", "HEALTHY"),))
        )
        assert "🗄 Database:\n🟢 HEALTHY" in teks

    def test_komponen_mati_merah(self) -> None:
        """Komponen mati yang dicetak hijau adalah kebohongan yang paling
        mudah dipercaya, karena pembaca memang berharap hijau."""
        teks = render_daily(
            _report(components=(
                Component("Binance REST", "🔵", "DISCONNECTED", healthy=False),
            ))
        )
        assert "🔵 Binance REST:\n🔴 DISCONNECTED" in teks

    def test_uptime_dan_versi_model(self) -> None:
        teks = render_daily(
            _report(
                uptime="23h 58m",
                correction=SelfCorrection(model_version="ARUNA v1.0"),
            )
        )
        assert "⏱ Uptime:\n23h 58m" in teks
        assert "🤖 Current Model:\nARUNA v1.0" in teks


def _pctstr(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}%"
