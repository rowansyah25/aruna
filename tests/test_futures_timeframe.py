"""TIMEFRAME wajib ada di pesan futures, dan harus benar.

**Dua cacat berturut-turut, dan yang kedua lebih instruktif.**

Yang pertama: pesan futures tidak pernah mencetak timeframe sama sekali.
``horizon_hours`` hanya dipakai menghitung cooldown, jadi operator menerima
"LONG AVAXUSDT entry 6.3170 stop 6.1800" tanpa cara apa pun untuk tahu apakah
itu ide lima belas menit atau empat jam - dan stop yang benar untuk keduanya
sangat berbeda.

Yang kedua: perbaikannya MENEBAK nama atributnya. Ia mencoba ``horizon``,
``interval``, lalu ``timeframe``, dan ``FuturesPlan`` menyimpannya sebagai
``horizon_hours``. Ketiganya meleset. Hasilnya kode yang terlihat berhati-hati -
tiga nama, ada fallback - dan salah di setiap cabangnya, sampai operator
mengirim tangkapan layar bertuliskan "TIMEFRAME: TIDAK TERCATAT" pada rencana
yang horizonnya diketahui persis.

Berkas ini menguji terhadap ``FuturesPlan`` yang sungguhan, bukan terhadap
objek tiruan yang bidangnya aku karang sendiri - karena objek tiruan akan lulus
untuk nama apa pun yang kebetulan aku pilih di kedua sisi.
"""

from __future__ import annotations

import dataclasses

import pytest

from aruna.futures.notify import _timeframe_of
from aruna.futures.plan import FuturesPlan


class TestBidangnyaMemangAda:
    """Penjaga terhadap tebakan, bukan terhadap perhitungan."""

    def test_futures_plan_menyimpan_horizon_hours(self) -> None:
        nama = {f.name for f in dataclasses.fields(FuturesPlan)}
        assert "horizon_hours" in nama

    def test_pembacanya_memakai_nama_yang_benar_benar_ada(self) -> None:
        """Kalau seseorang mengganti nama bidangnya, ini yang merah - bukan
        operator yang menemukannya lewat tangkapan layar."""
        import inspect

        sumber = inspect.getsource(_timeframe_of)
        nama = {f.name for f in dataclasses.fields(FuturesPlan)}
        dibaca = {
            n for n in nama if f'"{n}"' in sumber or f"'{n}'" in sumber
        }
        assert dibaca, "pembacanya tidak menyebut satu pun bidang FuturesPlan"
        assert "horizon_hours" in dibaca


class TestTerjemahanJam:
    @pytest.mark.parametrize(
        "jam,harapan",
        [
            (4.0, "4h"),
            (1.0, "1h"),
            (0.25, "15m"),
            (24.0, "1d"),
            (48.0, "2d"),
            (1.5, "1.5h"),
        ],
    )
    def test_satuan_yang_dipakai_operator(self, jam, harapan) -> None:
        """'0.25 jam' adalah cara paling bertele-tele menulis lima belas menit."""
        from types import SimpleNamespace as N

        assert _timeframe_of(N(horizon_hours=jam)) == harapan

    @pytest.mark.parametrize("buruk", [None, 0, -1, "empat"])
    def test_yang_tidak_bisa_dibaca_dikatakan(self, buruk) -> None:
        """Ketiadaannya dinyatakan, tidak didiamkan: baris yang hilang terbaca
        seperti 'terserah timeframe berapa'."""
        from types import SimpleNamespace as N

        assert _timeframe_of(N(horizon_hours=buruk)) == "TIDAK TERCATAT"

    def test_tanpa_bidangnya_tidak_meledak(self) -> None:
        from types import SimpleNamespace as N

        assert _timeframe_of(N()) == "TIDAK TERCATAT"


class TestPesanAnalysisSpot:
    """Cermin sisi spot, diuji pada renderernya langsung.

    Gerbang di ``SignalNotifier`` menahan signal tanpa timeframe, jadi keadaan
    ini tidak bisa dicapai lewat jalur kirim - dan justru karena itu ia harus
    diuji di sini. Renderer yang diam soal timeframe akan tetap salah pada
    setiap pemanggil lain: CLI, laporan, dan apa pun yang ditambahkan nanti.
    """

    def _render(self, **ganti):
        from aruna.core.enums import Decision
        from aruna.notify.verdict import VoteSplit, render_analysis

        dasar = {
            "symbol": "BTC/USDT",
            "decision": Decision.BUY,
            "split": VoteSplit(setuju=("TECHNICAL",), kontra=()),
            "entry": "63000",
            "stop": "61500",
            "target": "66000",
            "timeframe": "4h",
        }
        dasar.update(ganti)
        return render_analysis(**dasar)

    def test_timeframe_tercetak_saat_ada(self) -> None:
        assert "TIMEFRAME:\n4h" in self._render()

    def test_ketiadaannya_dikatakan_bukan_dikosongkan(self) -> None:
        """Baris kosong terbaca seperti 'terserah timeframe berapa'."""
        teks = self._render(timeframe=None)

        assert "TIMEFRAME:\nTIDAK TERCATAT" in teks

    def test_timeframe_tetap_ada_walau_level_tidak_ada(self) -> None:
        """Cacat aslinya: barisnya berada DI DALAM cabang entry/stop/target,
        jadi pesan yang kehilangan level kehilangan timeframe juga - dua kali
        tidak berguna sekaligus."""
        teks = self._render(entry=None, stop=None, target=None)

        assert "TIMEFRAME:" in teks

    def test_suara_kosong_disebut_tidak_tercatat(self) -> None:
        """``TOTAL: 0 VS 0`` pada keputusan LONG berarti sebelas agent
        memutuskan tanpa satu pun berpendapat - mustahil, dan ia membuat
        seluruh pesan tidak bisa dipercaya.

        Yang sebenarnya terjadi: barisnya tidak membawa suaranya. "Tidak
        diukur" dicetak sebagai "nol" adalah karangan yang paling halus
        (PASAL 4).
        """
        from aruna.notify.verdict import VoteSplit

        teks = self._render(split=VoteSplit(setuju=(), kontra=()))

        assert "TIDAK TERCATAT untuk keputusan ini" in teks
        assert "0 VS 0" not in teks

    def test_suara_yang_ada_tetap_ditampilkan_utuh(self) -> None:
        """Peredamnya hanya untuk yang benar-benar kosong."""
        from aruna.notify.verdict import VoteSplit

        teks = self._render(
            split=VoteSplit(setuju=("TECHNICAL",), kontra=("RISK",))
        )

        assert "TECHNICAL" in teks
        assert "RISK" in teks
        assert "1 VS 1" in teks


class TestBarisnyaSampaiKePesan:
    def test_pesan_futures_mencetak_timeframe(self) -> None:
        """Pemeriksaan bentuk pada jalur yang sungguhan: baris itu ada di
        badan pesan, bukan hanya fungsinya ada."""
        import inspect

        from aruna.futures import notify

        sumber = inspect.getsource(notify)
        assert "TIMEFRAME:" in sumber
        assert "_timeframe_of(plan)" in sumber

    def test_timeframe_tidak_hilang_saat_blok_risiko_ditambahkan(self) -> None:
        """**Diminta operator secara eksplisit: "timeframe jangan dihilangkan".**

        Blok RISIKO (PHASE 13) disisipkan ke bagian PENILAIAN pada pesan yang
        sama. Dua baris yang ditambahkan ke satu pesan adalah tempat paling
        mudah salah satunya tergeser keluar tanpa ada yang menyadarinya -
        apalagi keduanya baru, dan keduanya di blok yang berbeda.

        Jadi keduanya dipakukan bersama, di satu test, bukan di dua test yang
        bisa hijau bergantian.
        """
        import inspect

        from aruna.futures import notify

        sumber = inspect.getsource(notify)
        assert "TIMEFRAME:   " in sumber, "baris TIMEFRAME hilang"
        assert "RISIKO:           " in sumber, "baris RISIKO hilang"

    def test_risiko_gagal_tidak_menjatuhkan_pesan(self) -> None:
        """Pesan yang membawa entry, stop dan target tidak boleh hilang karena
        satu baris keterangan gagal dihitung."""
        from types import SimpleNamespace as N

        from aruna.futures.notify import _risiko

        # Objek yang setiap atributnya meledak saat dibaca.
        class _Ledakan:
            def __getattr__(self, nama):
                raise RuntimeError("bentuk tak terduga")

        assert isinstance(_risiko(_Ledakan()), str)
        assert isinstance(_risiko(N()), str)

    def test_timeframe_di_atas_bersama_side(self) -> None:
        """Ia bagian dari APA keputusannya, bukan keterangan tambahan di bawah
        angka - jadi ia dibaca sebelum operator sampai ke entry.

        **Dibaca dari ``_alert`` saja, bukan dari seluruh modul.** Versi pertama
        memindai berkasnya utuh, dan ia pecah begitu pesan hasil futures
        ditambahkan - penyusun kedua itu juga mencetak ``SIDE:`` dan ``ENTRY:``,
        jadi ``index`` menemukan kemunculan dari fungsi yang sama sekali tidak
        diuji di sini. Yang salah bukan tata letaknya melainkan cakupan
        pencariannya.
        """
        import inspect

        from aruna.futures.notify import _alert

        sumber = inspect.getsource(_alert)
        posisi_side = sumber.index("SIDE:        ")
        posisi_tf = sumber.index("TIMEFRAME:   ")
        posisi_entry = sumber.index("ENTRY:       ")
        assert posisi_side < posisi_tf < posisi_entry
