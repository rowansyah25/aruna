"""Phase 12: yang menahan ARUNA dari mengarang pola.

Berkas ini menguji satu hal berulang kali dari sudut berbeda: **sistem yang
belajar dari data sedikit harus tahu bahwa datanya sedikit.**

Angka yang memicunya: saat Phase 12 dibangun, seluruh sejarah ARUNA berumur
tiga hari - 225 prediksi terskor, dan irisan terbesar berisi 18. Pada data
sebesar itu, mesin pencari pola yang tidak dijaga akan menemukan puluhan irisan
bermenang 100%, semuanya nyata secara aritmetika dan tidak satu pun berarti.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aruna.learning.evidence import (
    MIN_SAMPLE,
    Evidence,
    EvidenceLevel,
    pooled,
    wilson_interval,
)
from aruna.learning.patterns import Observation, discover
from aruna.learning.specialization import Vote, build_profiles, specialists


class TestSampleKecilTidakPernahMenyimpulkan:
    """PASAL 12.3. Tiga dari tiga bukan 'strategi sangat akurat'."""

    def test_tiga_dari_tiga_belum_cukup(self) -> None:
        e = Evidence(wins=3, losses=0)
        assert e.level is EvidenceLevel.INSUFFICIENT_SAMPLE
        assert not e.conclusive

    def test_tiga_dari_tiga_tidak_mengalahkan_koin(self) -> None:
        """Angkanya 100%; batas bawahnya tidak sampai 50%."""
        assert Evidence(wins=3, losses=0).beats(0.5) is False

    def test_yang_dibandingkan_batas_bawah_bukan_titik_tengah(self) -> None:
        """Inti seluruh gerbang ini.

        Titik tengah 3-dari-3 adalah 100% dan mengalahkan apa pun. Batas
        bawahnya 44%. Sebuah gerbang yang memakai titik tengah akan meloloskan
        setiap kebetulan yang pernah terjadi.
        """
        kecil = Evidence(wins=3, losses=0)
        assert (kecil.win_rate or 0) > 0.5
        assert kecil.interval[0] < 0.5

    def test_sample_cukup_tapi_selangnya_masih_memuat_baseline(self) -> None:
        """Kasus yang sesungguhnya diuji aturan batas-bawah.

        Test 3-dari-3 di atas lolos lewat gerbang sample dan tidak pernah
        menyentuh perbandingannya sama sekali - terbukti saat ``beats``
        diganti memakai titik tengah dan test itu tetap hijau.

        Tiga puluh sample dengan 17 menang: titik tengahnya 57% dan
        mengalahkan koin, batas bawahnya 39% dan tidak. Sample-nya cukup, jadi
        gerbang pertama meloloskannya, dan yang memutuskan adalah aturan yang
        benar-benar sedang diuji.
        """
        e = Evidence(wins=17, losses=13)

        assert e.conclusive, "harus lolos gerbang sample dulu"
        assert (e.win_rate or 0) > 0.5
        assert e.interval[0] < 0.5
        assert e.beats(0.5) is False

    def test_sample_besar_boleh_menyimpulkan(self) -> None:
        e = Evidence(wins=210, losses=40)
        assert e.level is EvidenceLevel.STRONG
        assert e.beats(0.5) is True

    def test_selang_menyempit_saat_sample_bertambah(self) -> None:
        sempit = Evidence(wins=84, losses=16).interval
        lebar = Evidence(wins=8, losses=2).interval
        assert (sempit[1] - sempit[0]) < (lebar[1] - lebar[0])

    def test_pemburukan_juga_bisa_dipastikan(self) -> None:
        """Cermin dari ``beats``, dan ia harus ada.

        Gerbang yang hanya bisa memastikan 'lebih baik' membuat setiap
        pemburukan terlihat belum pasti - dan strategi yang memburuk hidup
        selamanya di bawah keraguan yang menguntungkannya.
        """
        assert Evidence(wins=18, losses=57).worse_than(0.5) is True
        assert Evidence(wins=1, losses=2).worse_than(0.5) is False

    def test_sample_size_selalu_tercetak(self) -> None:
        """PASAL 12.3 meminta setiap analisis menampilkannya."""
        for w, kalah in ((3, 0), (0, 0), (210, 40), (18, 57)):
            teks = Evidence(wins=w, losses=kalah).label()
            assert "sample" in teks or f"/{w + kalah}" in teks, teks

    def test_nol_dari_nol_bukan_nol_persen(self) -> None:
        """Belum diukur berbeda dari terukur nol (PASAL 4)."""
        assert Evidence(wins=0, losses=0).win_rate is None

    def test_selang_kosong_adalah_seluruh_rentang(self) -> None:
        assert wilson_interval(0, 0) == (0.0, 1.0)

    def test_gabungan_dijumlah_bukan_dirata_rata(self) -> None:
        """Merata-ratakan persentase memberi bobot sama kepada irisan berisi
        tiga sample dan irisan berisi tiga ratus."""
        besar = Evidence(wins=10, losses=290)
        kecil = Evidence(wins=3, losses=0)
        gabung = pooled([besar, kecil])

        assert gabung.total == 303
        assert (gabung.win_rate or 0) < 0.10


def _obs(n_win: int, n_loss: int, **kwargs: str) -> list[Observation]:
    dasar = {
        "market": "CRYPTO", "symbol": "BTC/USDT", "horizon": "15m",
        "direction": "BUY", "regime": "TRENDING", "quality_band": "0.8-1.0",
    }
    dasar.update(kwargs)
    return [Observation(**dasar, won=True) for _ in range(n_win)] + [
        Observation(**dasar, won=False) for _ in range(n_loss)
    ]


class TestPenemuanPolaTidakMengarang:
    """PASAL 12.2. Korelasi dilaporkan sebagai korelasi, dan hanya kalau
    sample-nya menopangnya."""

    def test_tidak_ada_irisan_kecil_yang_mengaku_unggul(self) -> None:
        # Satu simbol dengan 3-dari-3 di tengah populasi yang kalah banyak.
        data = _obs(0, 100) + _obs(3, 0, symbol="DOGE/USDT")
        hasil = discover(data)

        nakal = [
            p for p in hasil.patterns
            if p.beats_baseline and p.sample_size < MIN_SAMPLE
        ]
        assert nakal == [], [p.key for p in nakal]

    def test_irisan_kecil_tetap_dilaporkan_angkanya(self) -> None:
        """Diredam kesimpulannya, bukan dihapus barisnya. Menghapusnya akan
        menghapus kekalahan bersamanya (PASAL 11.21)."""
        data = _obs(0, 100) + _obs(3, 0, symbol="DOGE/USDT")
        hasil = discover(data)

        kunci = [p.key for p in hasil.patterns]
        assert any("DOGE/USDT" in k for k in kunci)

    def test_diurutkan_dari_sample_terbesar(self) -> None:
        """Mengurutkan menurut win rate menaruh setiap kebetulan di puncak."""
        data = _obs(20, 80) + _obs(3, 0, symbol="DOGE/USDT")
        hasil = discover(data)

        ukuran = [p.sample_size for p in hasil.patterns]
        assert ukuran == sorted(ukuran, reverse=True)

    def test_yang_memburuk_ikut_dilaporkan(self) -> None:
        """Daftar yang hanya memuat irisan unggul adalah cherry picking."""
        data = _obs(60, 40) + _obs(1, 99, symbol="DOGE/USDT")
        hasil = discover(data)

        assert any(p.worse_than_baseline for p in hasil.notable)

    def test_baseline_adalah_performa_sendiri(self) -> None:
        """Bukan 50%. Pertanyaannya 'apakah mengetahui irisan ini memperbaiki
        tebakan', bukan 'apakah ia mengalahkan koin'."""
        data = _obs(10, 90)
        hasil = discover(data)

        assert hasil.baseline.total == 100
        assert abs((hasil.baseline.win_rate or 0) - 0.10) < 1e-9

    def test_tanpa_data_tidak_ada_yang_mengaku_apa_pun(self) -> None:
        hasil = discover([])
        assert hasil.patterns == ()
        assert hasil.notable == ()

    def test_modul_tidak_menyebut_sebab(self) -> None:
        """PASAL 12.2: korelasi tidak boleh dianggap sebab.

        Pemeriksaan bentuk, dan sengaja kasar: nama seperti ``cause`` atau
        ``because`` yang masuk ke sini adalah tanda seseorang mulai
        memperlakukan irisan sebagai penjelasan.
        """
        import inspect

        from aruna.learning import patterns

        sumber = inspect.getsource(patterns)
        # Hanya bagian kode, bukan docstring yang justru MEMBAHAS larangan ini.
        kode = "\n".join(
            b for b in sumber.splitlines()
            if not b.strip().startswith("#")
        )
        for terlarang in ("def cause", "cause=", ".cause", "causes="):
            assert terlarang not in kode, terlarang


class TestSpesialisasiAgent:
    """PASAL 12.5."""

    def _votes(self, role: str, regime: str, benar: int, salah: int) -> list[Vote]:
        return [
            Vote(role=role, regime=regime, agreed=True, abstained=False, won=True)
            for _ in range(benar)
        ] + [
            Vote(role=role, regime=regime, agreed=True, abstained=False, won=False)
            for _ in range(salah)
        ]

    def test_abstain_tidak_masuk_penyebut(self) -> None:
        """Tidak menyatakan apa-apa bukan salah. Menghitungnya menghukum agent
        yang jujur mengaku tidak tahu."""
        suara = self._votes("A", "TRENDING", 30, 0) + [
            Vote(role="A", regime="TRENDING", agreed=False,
                 abstained=True, won=False)
            for _ in range(50)
        ]
        profil = build_profiles(suara)[0]
        assert profil.overall.total == 30

    def test_selalu_setuju_bukan_akurasi_sempurna(self) -> None:
        """Kalau 'benar' berarti 'searah council', seorang agent bisa mencapai
        seratus persen dengan tidak pernah berpendapat."""
        suara = self._votes("A", "TRENDING", 0, 40)  # setuju terus, council kalah
        profil = build_profiles(suara)[0]
        assert (profil.overall.win_rate or 0) == 0.0

    def test_menentang_dan_council_kalah_dihitung_benar(self) -> None:
        suara = [
            Vote(role="A", regime="TRENDING", agreed=False,
                 abstained=False, won=False)
            for _ in range(40)
        ]
        profil = build_profiles(suara)[0]
        assert (profil.overall.win_rate or 0) == 1.0

    def test_spesialisasi_butuh_selang_yang_terpisah(self) -> None:
        """Dua rentang yang bertindihan adalah dua angka yang belum bisa
        dibedakan, seberapa jauh pun titik tengahnya."""
        suara = (
            self._votes("A", "TRENDING", 20, 10)   # 67%, selang lebar
            + self._votes("A", "RANGING", 14, 16)  # 47%, selang lebar
        )
        profil = build_profiles(suara)[0]
        assert profil.specialty is None

    def test_spesialisasi_terbukti_saat_jaraknya_jelas(self) -> None:
        suara = (
            self._votes("A", "TRENDING", 95, 5)
            + self._votes("A", "RANGING", 20, 80)
        )
        profil = build_profiles(suara)[0]
        assert profil.specialty == "TRENDING"
        assert specialists([profil]) == {"A": "TRENDING"}

    def test_rezim_bersample_kecil_tidak_jadi_spesialisasi(self) -> None:
        suara = (
            self._votes("A", "TRENDING", 40, 60)
            + self._votes("A", "BREAKOUT", 3, 0)
        )
        profil = build_profiles(suara)[0]
        assert profil.specialty is None


class TestRegressionGuard:
    """PASAL 12.11."""

    def _dasar(self) -> dict[str, float]:
        return {
            "win_rate": 0.82, "net_pnl": 100.0, "max_drawdown": 0.10,
            "calibration_error": 0.05, "out_of_sample_win_rate": 0.80,
            "sample_size": 2840,
        }

    def test_contoh_spec_gagal(self) -> None:
        """+4% win rate dibayar dengan +20% drawdown."""
        from aruna.governance.regression import check

        baru = self._dasar() | {"win_rate": 0.86, "max_drawdown": 0.30}
        r = check(self._dasar(), baru)

        assert not r.passed
        assert r.verdict == "FAILED"
        assert r.recommendation == "DO NOT PROMOTE"
        assert [c.name for c in r.broken] == ["max_drawdown"]

    def test_model_yang_benar_benar_lebih_baik_lulus(self) -> None:
        from aruna.governance.regression import check

        baru = self._dasar() | {
            "win_rate": 0.87, "net_pnl": 140.0, "max_drawdown": 0.09,
            "calibration_error": 0.04, "out_of_sample_win_rate": 0.86,
        }
        assert check(self._dasar(), baru).passed

    def test_metrik_yang_tidak_diukur_menggagalkan(self) -> None:
        """Tidak diukur bukan tidak memburuk (PASAL 4)."""
        from aruna.governance.regression import check

        r = check({"win_rate": 0.82}, {"win_rate": 0.95})
        assert not r.passed
        assert r.missing

    def test_kalibrasi_yang_memburuk_menggagalkan(self) -> None:
        """Keyakinan yang tidak ditopang hasil adalah pemburukan (PASAL 12.18)."""
        from aruna.governance.regression import check

        baru = self._dasar() | {"win_rate": 0.90, "calibration_error": 0.25}
        assert not check(self._dasar(), baru).passed


class TestTelegramTidakDibanjiri:
    """PASAL 12.25."""

    def test_log_pembelajaran_tidak_boleh_ke_telegram(self) -> None:
        from aruna.notify.learning import telegram_allows

        for terlarang in (
            "RAW_LEARNING_LOG", "EVERY_PATTERN", "EVERY_BACKTEST",
            "EVERY_AGENT_CALCULATION", "EVERY_INTERNAL_DEBATE",
        ):
            assert telegram_allows(terlarang) is False, terlarang

    def test_enam_jenis_yang_boleh(self) -> None:
        from aruna.notify.learning import telegram_allows

        for boleh in (
            "SIGNAL", "RESULT", "DAILY_REPORT", "HEALTH_ALERT",
            "MODEL_PROPOSAL", "RECOVERY",
        ):
            assert telegram_allows(boleh) is True, boleh

    def test_daftar_putih_bukan_daftar_hitam(self) -> None:
        """Jenis pesan baru harus ditolak secara bawaan: yang baru justru yang
        paling mungkin membanjiri."""
        from aruna.notify.learning import telegram_allows

        assert telegram_allows("JENIS_YANG_BELUM_ADA") is False

    def test_ringkasan_memotong_daftar_panjang_dan_mengatakannya(self) -> None:
        from aruna.notify.learning import MAX_PATTERNS, render_learning

        baris = render_learning(
            observations=200,
            baseline_label="12% menang (24/200)",
            patterns=[f"pola {i}" for i in range(20)],
        )
        teks = "\n".join(baris)
        assert "+15 lagi" in teks
        assert teks.count("pola ") >= MAX_PATTERNS

    def test_tanpa_data_tidak_ada_blok_kosong(self) -> None:
        from aruna.notify.learning import render_learning

        assert render_learning(observations=0, baseline_label="-") == []


class TestTidakMengubahModelSendiri:
    """PASAL 11.16, 12.26. Penjaga paling penting Phase 12."""

    def test_service_pembelajaran_tidak_menulis_konfigurasi(self) -> None:
        """Pemeriksaan bentuk, dan ia berbunyi pada penambahan yang tidak
        berniat apa-apa - yang justru gunanya."""
        import inspect

        from aruna.learning import adaptive

        sumber = inspect.getsource(adaptive)
        for terlarang in (
            "settings.", "set_weight", "update_weight", "apply_model",
            "promote(", "os.environ",
        ):
            assert terlarang not in sumber, terlarang

    def test_penyimpanan_hanya_menyentuh_tabel_hasil(self) -> None:
        """Catatan historis IMMUTABLE (PASAL 12.1)."""
        import inspect

        from aruna.db.repositories import learning12

        sumber = inspect.getsource(learning12).upper()
        for terlarang in (
            "UPDATE SIGNALS", "DELETE FROM SIGNALS", "UPDATE SIGNAL_SNAPSHOTS",
            "UPDATE PAPER_TRADES", "DELETE FROM PAPER_TRADES",
            "UPDATE COUNCIL_VOTES",
        ):
            assert terlarang not in sumber, terlarang

    def test_strategi_tidak_pernah_dihapus(self) -> None:
        """PASAL 12.15: status berubah, barisnya tinggal."""
        import inspect

        from aruna.db.repositories import learning12
        from aruna.learning.strategies import StrategyStatus

        assert "DELETE FROM strategies" not in inspect.getsource(learning12)
        assert not any(
            "DELET" in s.value or "REMOV" in s.value for s in StrategyStatus
        )

    def test_katalog_punya_status_pensiun_tanpa_penghapusan(self) -> None:
        from aruna.learning.strategies import StrategyStatus

        assert StrategyStatus.RETIRED.value == "RETIRED"
        assert {s.value for s in StrategyStatus} == {
            "ACTIVE", "DEGRADED", "UNDER_REVIEW", "SUSPENDED", "RETIRED"
        }


class TestPerformaStrategi:
    """PASAL 12.4, 12.7."""

    def test_drawdown_dihitung_dari_puncak_kumulatif(self) -> None:
        from aruna.learning.adaptive import _drawdown

        # naik 10, turun 30, naik 5: puncak 10, terendah -20, dalam 30.
        assert _drawdown(
            [Decimal("10"), Decimal("-30"), Decimal("5")]
        ) == Decimal("30")

    def test_deret_yang_selalu_naik_tidak_punya_drawdown(self) -> None:
        from aruna.learning.adaptive import _drawdown

        assert _drawdown([Decimal("5"), Decimal("5")]) == Decimal("0")

    def test_rezim_dipetakan_ke_strategi_yang_masuk_akal(self) -> None:
        """Setiap rezim dipakukan ke pemiliknya.

        Test ini menemukan bug sungguhan saat ditulis: BREAKOUT jatuh ke
        Momentum, bukan Breakout, karena pemetaannya dibangun dengan dict
        comprehension - dan di sana yang TERAKHIR menang. Tidak ada yang
        memutuskan itu; performa Breakout akan tercatat sebagai performa
        Momentum tanpa satu pun tanda.
        """
        from aruna.learning.strategies import classify

        assert classify("REVERSAL") == "STR-003"
        assert classify("RANGING") == "STR-004"
        assert classify("BREAKOUT") == "STR-002"
        assert classify("LOW_VOLATILITY") == "STR-004"
        # TRENDING dimiliki Trend Continuation; horizon pendek yang
        # memindahkannya ke Momentum, dan itu aturan yang ditulis, bukan
        # akibat urutan katalog.
        assert classify("TRENDING") == "STR-001"
        assert classify("TRENDING", horizon="15m") == "STR-005"

    def test_rezim_tak_dikenal_masuk_penampung_bukan_dibuang(self) -> None:
        """Besarnya penampung mengukur kelengkapan katalog; membuangnya
        menyembunyikan ukuran itu."""
        from aruna.learning.strategies import UNMAPPED, classify

        assert classify(None) == UNMAPPED.code
        assert classify("SESUATU_YANG_BARU") == UNMAPPED.code

    def test_pemetaan_memakai_rezim_tersimpan_bukan_pembacaan_baru(self) -> None:
        """Membaca ulang pasar berarti look-ahead: performa masa lalu diukur
        dengan pengetahuan yang belum ada saat keputusannya diambil (SPEC 24).
        """
        import inspect

        from aruna.learning import strategies

        sumber = inspect.getsource(strategies.classify)
        for terlarang in ("fetch", "await", "candles", "provider"):
            assert terlarang not in sumber, terlarang


class TestPemilihanStrategi:
    """PASAL 12.6. 'ARUNA tidak boleh memilih strategy hanya berdasarkan
    win rate' - tujuh pertimbangan, dan hak untuk diam."""

    def _kandidat(self, kode: str = "STR-001", w: int = 210,
                  kalah: int = 90, **ganti):
        from aruna.learning.selection import Candidate

        dasar = {
            "per_period": (0.70, 0.72, 0.69),
            "net_pnl": Decimal("100"),
            "max_drawdown": Decimal("20"),
            "calibration_error": 0.05,
            "out_of_sample": Evidence(wins=70, losses=30),
            "regimes": ("TRENDING",),
        }
        dasar.update(ganti)
        return Candidate(code=kode, evidence=Evidence(wins=w, losses=kalah),
                         **dasar)

    def _pilih(self, kandidat, regime="TRENDING", baseline=0.5):
        from aruna.learning.selection import select

        return select([kandidat], regime=regime, baseline=baseline)

    def test_kandidat_lengkap_dan_kuat_terpilih(self) -> None:
        pilihan = self._pilih(self._kandidat())
        assert pilihan.strategy == "STR-001"
        assert not pilihan.abstained

    @pytest.mark.parametrize(
        "ganti,alasan",
        [
            ({"w": 3, "kalah": 0}, "INSUFFICIENT_SAMPLE"),
            ({"w": 100, "kalah": 200}, "NOT_BETTER_THAN_AVERAGE"),
            ({"per_period": (0.95, 0.30, 0.70)}, "UNSTABLE"),
            ({"per_period": ()}, "UNSTABLE"),
            ({"max_drawdown": Decimal("300")}, "DRAWDOWN_TOO_DEEP"),
            ({"net_pnl": Decimal("-5")}, "DRAWDOWN_TOO_DEEP"),
            ({"calibration_error": 0.40}, "POORLY_CALIBRATED"),
            ({"calibration_error": None}, "POORLY_CALIBRATED"),
            ({"out_of_sample": None}, "NO_OUT_OF_SAMPLE"),
            ({"out_of_sample": Evidence(wins=2, losses=1)}, "NO_OUT_OF_SAMPLE"),
        ],
    )
    def test_tiap_pertimbangan_bisa_menggugurkan(self, ganti, alasan) -> None:
        """Tujuh pertimbangan yang spec sebut, masing-masing diuji.

        Termasuk bentuk 'belum diukur' untuk tiga di antaranya: sebuah pemilih
        yang memperlakukan kekosongan sebagai aman akan memilih justru
        strategi yang paling sedikit diketahui (PASAL 4).
        """
        w = ganti.pop("w", 210)
        kalah = ganti.pop("kalah", 90)
        pilihan = self._pilih(self._kandidat(w=w, kalah=kalah, **ganti))

        assert pilihan.abstained
        assert pilihan.refusal is not None
        assert pilihan.refusal.name == alasan, pilihan.refusal

    def test_rezim_tak_terbaca_tidak_memilih(self) -> None:
        assert self._pilih(self._kandidat(), regime=None).abstained

    def test_rezim_tak_cocok_tidak_memilih(self) -> None:
        pilihan = self._pilih(self._kandidat(), regime="RANGING")
        assert pilihan.refusal is not None
        assert pilihan.refusal.name == "NO_CANDIDATES"

    def test_diurutkan_menurut_batas_bawah_bukan_win_rate(self) -> None:
        """Dua strategi bermenang 70%, satu dari 40 sample dan satu dari 400,
        bukan dua strategi yang sama baiknya."""
        from aruna.learning.selection import select

        tipis = self._kandidat("STR-TIPIS", w=28, kalah=12)
        tebal = self._kandidat("STR-TEBAL", w=280, kalah=120)
        pilihan = select([tipis, tebal], regime="TRENDING", baseline=0.5)

        assert pilihan.strategy == "STR-TEBAL"

    def test_alasan_tiap_yang_gugur_disimpan(self) -> None:
        """'Kenapa bukan yang itu' harus bisa dijawab tanpa menjalankan ulang."""
        from aruna.learning.selection import select

        pilihan = select(
            [self._kandidat("STR-A", w=3, kalah=0),
             self._kandidat("STR-B", w=100, kalah=200)],
            regime="TRENDING", baseline=0.5,
        )
        assert {k for k, _ in pilihan.rejected} == {"STR-A", "STR-B"}

    def test_kalimatnya_menyebut_ini_keterangan_bukan_perintah(self) -> None:
        """PASAL 12.26: ARUNA menganalisis, operator memutuskan."""
        teks = self._pilih(self._kandidat()).line()
        assert "bukan perintah" in teks


class TestRezimDibacaDariBentukApaPun:
    """Bug yang ditemukan probe, bukan test.

    ``DecisionContext.regime`` mengembalikan ``RegimeVerdict``, bukan enum
    ``Regime``. Versi pertama jatuh ke ``str(regime)`` dan menghasilkan seluruh
    repr objeknya - string yang tidak pernah cocok dengan rezim mana pun.
    Pemilihnya menjawab 'tidak ada strategi yang cocok' untuk SETIAP aset,
    selamanya, tanpa satu pun error.
    """

    def test_dari_regime_verdict(self) -> None:
        from types import SimpleNamespace as N

        from aruna.learning.strategist import regime_name

        verdict = N(regime=N(value="BREAKOUT"), confidence=0.65)
        assert regime_name(verdict) == "BREAKOUT"

    def test_dari_enum_langsung(self) -> None:
        from aruna.core.enums import Regime
        from aruna.learning.strategist import regime_name

        assert regime_name(Regime.TRENDING) == "TRENDING"

    def test_dari_string(self) -> None:
        from aruna.learning.strategist import regime_name

        assert regime_name("RANGING") == "RANGING"

    def test_none_tetap_none(self) -> None:
        from aruna.learning.strategist import regime_name

        assert regime_name(None) is None

    def test_tidak_pernah_mengembalikan_repr_objek(self) -> None:
        """Penjaga untuk bentuk kegagalan aslinya."""
        from types import SimpleNamespace as N

        from aruna.learning.strategist import regime_name

        hasil = regime_name(N(regime=N(value="BREAKOUT"), confidence=0.65))
        assert hasil is not None
        assert "(" not in hasil and "=" not in hasil, hasil


class TestStrategistDirangkaiDanTerisolasi:
    """Cacat yang paling sering terulang: kode benar yang tidak pernah
    dilewati jalur hidup."""

    @pytest.mark.asyncio
    async def test_selalu_mengembalikan_selection_bukan_none(self) -> None:
        """Abstain yang dikembalikan sebagai None tidak bisa dibedakan dari
        'pemilihnya tidak dirangkai'."""
        from aruna.learning.selection import Selection
        from aruna.learning.strategist import Strategist

        class _Store:
            async def strategy_slices(self):
                return []

            async def overall_win_rate(self):
                return None

        hasil = await Strategist(store=_Store()).suggest(
            market="CRYPTO", symbol="BTC/USDT", interval="1h", regime=None
        )
        assert isinstance(hasil, Selection)
        assert hasil.abstained

    @pytest.mark.asyncio
    async def test_penyimpanan_yang_rusak_tidak_melempar(self) -> None:
        """Kegagalan pembelajaran tidak boleh menjadi kegagalan analisis."""
        from aruna.learning.strategist import Strategist

        class _Rusak:
            async def strategy_slices(self):
                raise RuntimeError("tabel belum ada")

            async def overall_win_rate(self):
                return None

        hasil = await Strategist(store=_Rusak()).suggest(
            market="CRYPTO", symbol="BTC/USDT", interval="1h",
            regime="TRENDING",
        )
        assert hasil.abstained

    def test_build_context_memanggil_pemilihnya(self) -> None:
        """Pemeriksaan bentuk pada jalur yang sungguhan."""
        import inspect

        from aruna.agents.service import DeliberationService

        sumber = inspect.getsource(DeliberationService._build_context)
        assert "self._strategist" in sumber
        assert "strategy=" in sumber

    def test_context_punya_tempat_untuk_strateginya(self) -> None:
        from aruna.agents.context import DecisionContext

        assert "strategy" in DecisionContext.__dataclass_fields__

    def test_aplikasi_merangkai_strategist(self) -> None:
        import inspect

        from aruna.app import ArunaApplication

        sumber = inspect.getsource(ArunaApplication)
        assert "Strategist(" in sumber
        assert "strategist=self.strategist" in sumber

    def test_yang_dikeluarkan_operator_tidak_ditawarkan(self) -> None:
        """PASAL 12.15: penangguhan yang tidak mengubah apa pun bukan
        penangguhan.

        Yang disaring hanya SUSPENDED dan RETIRED - status yang operator
        pasang. DEGRADED dan UNDER_REVIEW tetap ditawarkan; keduanya label
        pengamatan yang ARUNA pasang sendiri, dan menyaringnya di sini berarti
        ARUNA menonaktifkan strateginya sendiri diam-diam (PASAL 11.16).
        Gerbang buktinya sudah menolak yang tidak terbukti.
        """
        import inspect

        from aruna.db.repositories import learning12

        sumber = inspect.getsource(learning12.LearningRepository.strategy_slices)
        assert "SUSPENDED" in sumber and "RETIRED" in sumber
        assert "s.status = 'ACTIVE'" not in sumber

    def test_pemilihan_tidak_menyentuh_bobot(self) -> None:
        """PASAL 11.16, 12.26."""
        import inspect

        from aruna.learning import selection, strategist

        for modul in (selection, strategist):
            sumber = inspect.getsource(modul)
            for terlarang in (
                "set_weight", "update_weight", "apply_model", "promote(",
                "settings.", "os.environ",
            ):
                assert terlarang not in sumber, f"{modul.__name__}: {terlarang}"


@pytest.mark.asyncio
async def test_putaran_penuh_tanpa_database_sungguhan() -> None:
    """Service-nya berjalan utuh di atas penyimpanan palsu.

    Menguji rangkaiannya - bahwa tiap bagian dipanggil dan hasilnya sampai ke
    penyimpanan - tanpa menuntut MySQL. Cacat yang paling sering di sistem ini
    adalah kode yang benar tapi tidak pernah dilewati.
    """
    from aruna.learning.adaptive import AdaptiveLearningService

    class _Store:
        def __init__(self) -> None:
            self.patterns: list = []
            self.strategies: list = []
            self.performance: list = []
            self.events: list = []

        async def scored_observations(self):
            return [
                {
                    "market_code": "CRYPTO", "symbol": "BTC/USDT",
                    "horizon_code": "15m", "direction": "BUY",
                    "regime": "TRENDING", "signal_quality": 0.9,
                    "result": "WIN" if i % 4 == 0 else "LOSS",
                    "net_pnl": Decimal("1") if i % 4 == 0 else Decimal("-1"),
                    "resolved_at": None,
                }
                for i in range(120)
            ]

        async def agent_votes(self):
            return [
                {"role": "VOLUME", "agreed_with_council": True,
                 "abstained": False, "regime": "TRENDING", "result": "WIN"}
                for _ in range(40)
            ]

        async def save_patterns(self, rows):
            self.patterns = list(rows)
            return len(rows)

        async def upsert_strategies(self, rows):
            self.strategies = list(rows)
            return len(rows)

        async def save_strategy_performance(self, rows):
            self.performance = list(rows)
            return len(rows)

        async def record_event(self, **kwargs):
            self.events.append(kwargs)
            return len(self.events)

        # Ditambahkan ketika penilaian daur hidup dirangkai ke `run()`
        # (PASAL 12.15). Kegagalan test ini adalah cara yang benar untuk
        # mengetahuinya: sebuah service yang menumbuhkan kebutuhan baru pada
        # penyimpanannya harus membuat setiap pemanggil lama merah, bukan
        # menemukannya di produksi.
        async def catalog_with_performance(self):
            return [
                {"code": "STR-005", "status": "ACTIVE",
                 "wins": 1, "losses": 61, "net_pnl": -10},
            ]

        async def overall_win_rate(self):
            return 0.13

        async def set_strategy_status(self, code, status, *, reason, now):
            self.status_ditulis.append((code, status))
            return 1

    store = _Store()
    store.status_ditulis = []
    run = await AdaptiveLearningService(store).run()

    assert run.observations == 120
    assert store.patterns, "tidak ada pola yang sampai ke penyimpanan"
    assert store.strategies, "katalog tidak pernah dipasang"
    assert store.performance, "performa strategi tidak pernah disimpan"
    # Tiap baris pola membawa sample size-nya - PASAL 12.3 di jalur simpan.
    assert all("sample_size" in r for r in store.patterns)
    assert all(r["sample_size"] == r["wins"] + r["losses"] for r in store.patterns)
    # Daur hidupnya ikut berjalan dalam putaran yang sama, dan memakai hasil
    # yang baru saja disimpan - bukan hasil putaran sebelumnya (PASAL 12.15).
    assert store.status_ditulis == [("STR-005", "DEGRADED")]
    assert run.lifecycle is not None
