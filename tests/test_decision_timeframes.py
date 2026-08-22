"""Analisis lintas timeframe (PASAL 14.4 - 14.8).

Yang diuji di sini bukan penyaringan daftar - itu sepele - melainkan satu
larangan: arah TIDAK boleh datang dari suara terbanyak. PASAL 14.7 menulisnya
sebagai "jangan mencampurkan semua timeframe menjadi satu tanpa konteks", dan
contohnya sendiri berakhir LONG dengan dua timeframe SHORT.
"""

from __future__ import annotations

import pytest

from aruna.decision import Arah
from aruna.decision.timeframes import (
    Bacaan,
    Kelas,
    Lintas,
    Posisi,
    TimeframeError,
    classify,
    reading_from_structure,
    urutan,
)


def b(interval: str, arah: Arah, *bukti: str) -> Bacaan:
    return Bacaan(interval=interval, decision=arah, evidence=bukti)


#: Contoh PASAL 14.7 dan 14.8, apa adanya: 5m LONG, 10m SHORT, 15m LONG,
#: horizon 15 menit, final LONG.
CONTOH_14_7 = Lintas(
    horizon="15m",
    readings=(
        b("5m", Arah.LONG, "momentum recovery"),
        b("10m", Arah.SHORT, "momentum bearish sementara"),
        b("15m", Arah.LONG, "struktur bullish", "volume konfirmasi"),
    ),
    regime="TRENDING UP",
)

#: Contoh PASAL 14.5: tinggi bullish, rendah sedang pullback.
CONTOH_14_5 = Lintas(
    horizon="15m",
    readings=(
        b("4h", Arah.LONG), b("1h", Arah.LONG), b("15m", Arah.LONG),
        b("10m", Arah.SHORT), b("5m", Arah.SHORT),
    ),
)


class TestBukanSuaraTerbanyak:
    def test_contoh_pasal_147_berakhir_long(self) -> None:
        assert CONTOH_14_7.decision is Arah.LONG

    def test_mayoritas_berlawanan_tidak_membalik_arah(self) -> None:
        """Empat SHORT melawan satu LONG di horizonnya, dan hasilnya tetap
        LONG. Ini seluruh isi PASAL 14.7 dalam satu kasus."""
        peta = Lintas(
            horizon="15m",
            readings=(
                b("5m", Arah.SHORT), b("10m", Arah.SHORT),
                b("15m", Arah.LONG),
                b("1h", Arah.SHORT), b("4h", Arah.SHORT),
            ),
        )

        assert peta.decision is Arah.LONG
        assert len(peta.opposing) == 4

    def test_horizon_no_signal_tetap_no_signal_walau_semua_setuju(self) -> None:
        """Kebalikannya juga berlaku: empat timeframe LONG tidak menciptakan
        arah yang tidak ditemukan timeframe keputusannya."""
        peta = Lintas(
            horizon="15m",
            readings=(
                b("5m", Arah.LONG), b("10m", Arah.LONG),
                b("15m", Arah.NO_SIGNAL),
                b("1h", Arah.LONG), b("4h", Arah.LONG),
            ),
        )

        assert peta.decision is Arah.NO_SIGNAL
        assert "tidak memberi arah" in peta.reason

    def test_horizon_tidak_dianalisis_bukan_diambil_dari_tetangga(self) -> None:
        """Timeframe terdekat tidak menggantikan yang diminta. Keputusan 15
        menit yang diam-diam dijawab oleh 1h menjawab pertanyaan lain."""
        peta = Lintas(
            horizon="15m",
            readings=(b("5m", Arah.LONG), b("1h", Arah.LONG)),
        )

        assert peta.at_horizon is None
        assert peta.decision is Arah.NO_SIGNAL
        assert "tidak dianalisis" in peta.reason


class TestTinggiDanRendah:
    def test_letak_dihitung_dari_panjang_bukan_dari_teks(self) -> None:
        """Urutan yang ditebak dari teks akan menaruh '10m' di bawah '5m'."""
        assert CONTOH_14_5.posisi("5m") is Posisi.LOWER
        assert CONTOH_14_5.posisi("10m") is Posisi.LOWER
        assert CONTOH_14_5.posisi("15m") is Posisi.HORIZON
        assert CONTOH_14_5.posisi("1h") is Posisi.HIGHER
        assert urutan("10m") > urutan("5m")

    def test_pullback_dan_melawan_arus_dipisahkan(self) -> None:
        """PASAL 14.5: perlawanan dari bawah sering wajar, dari atas jarang.
        Melaporkan keduanya sebagai "3 menolak" menghapus perbedaan itu."""
        assert {x.interval for x in CONTOH_14_5.pullbacks} == {"5m", "10m"}
        assert CONTOH_14_5.against_trend == ()

    def test_perlawanan_dari_atas_ditandai_terpisah(self) -> None:
        peta = Lintas(
            horizon="15m",
            readings=(b("15m", Arah.LONG), b("4h", Arah.SHORT), b("5m", Arah.SHORT)),
        )

        assert {x.interval for x in peta.against_trend} == {"4h"}
        assert {x.interval for x in peta.pullbacks} == {"5m"}

    def test_tiap_posisi_punya_pekerjaannya(self) -> None:
        """PASAL 14.5 memberi timeframe tinggi dan rendah pekerjaan berbeda."""
        assert Posisi.HIGHER.job == "konteks tren"
        assert Posisi.LOWER.job == "waktu masuk"

    def test_timeframe_tak_dikenal_ditolak(self) -> None:
        with pytest.raises(TimeframeError, match="tidak dikenal"):
            urutan("7m")
        with pytest.raises(TimeframeError):
            Bacaan(interval="7m", decision=Arah.LONG)
        with pytest.raises(TimeframeError):
            Lintas(horizon="7m", readings=())

    def test_timeframe_ganda_ditolak(self) -> None:
        """Dua keputusan untuk satu timeframe berarti salah satunya akan
        diabaikan diam-diam."""
        with pytest.raises(TimeframeError, match="dua kali"):
            Lintas(
                horizon="15m",
                readings=(b("15m", Arah.LONG), b("15m", Arah.SHORT)),
            )


class TestPenentang:
    def test_no_signal_tidak_dihitung_melawan(self) -> None:
        """Timeframe yang tidak menemukan arah tidak sedang menentang apa pun;
        memasukkannya membuat pasar sepi terbaca sebagai pasar bertengkar."""
        peta = Lintas(
            horizon="15m",
            readings=(
                b("15m", Arah.LONG), b("5m", Arah.NO_SIGNAL), b("1h", Arah.SHORT)
            ),
        )

        assert {x.interval for x in peta.opposing} == {"1h"}
        assert peta.supporting == ()

    def test_pendukung_tidak_termasuk_horizonnya_sendiri(self) -> None:
        assert all(x.interval != "15m" for x in CONTOH_14_7.supporting)
        assert {x.interval for x in CONTOH_14_7.supporting} == {"5m"}

    def test_tanpa_perlawanan_dikatakan_begitu(self) -> None:
        peta = Lintas(
            horizon="15m", readings=(b("15m", Arah.LONG), b("1h", Arah.LONG))
        )

        assert not peta.conflicted
        assert "tanpa perlawanan" in peta.reason


class TestKelasHorizon:
    @pytest.mark.parametrize(
        ("interval", "kelas"),
        [
            ("5m", Kelas.SCALP),
            ("10m", Kelas.SCALP),
            ("15m", Kelas.SHORT_INTRADAY),
            ("30m", Kelas.SHORT_INTRADAY),
            ("1h", Kelas.SHORT_INTRADAY),
            ("4h", Kelas.INTRADAY),
            ("1d", Kelas.SWING),
        ],
    )
    def test_kelas_pasal_146(self, interval: str, kelas: Kelas) -> None:
        assert classify(interval) is kelas

    def test_di_batas_bersama_kelas_lebih_pendek_menang(self) -> None:
        """Spesifikasinya bertumpang tindih di 60 menit dan di 4 jam. Satu
        aturan dipakai di kedua tempat."""
        assert classify("1h") is Kelas.SHORT_INTRADAY
        assert classify("4h") is Kelas.INTRADAY

    def test_di_luar_tabel_tidak_dibulatkan(self) -> None:
        """Menyebut keputusan satu menit sebagai SCALP memberinya nama yang
        tidak pernah ditulis di PASAL 14.6."""
        assert classify("1m") is None
        assert classify("3m") is None
        assert classify("1w") is None


class TestDariStruktur:
    """PASAL 14.4: tiap timeframe punya keputusan internalnya sendiri."""

    def test_uptrend_jadi_long(self) -> None:
        b = reading_from_structure("15m", struktur_uji("UPTREND", "NONE"))

        assert b.decision is Arah.LONG
        assert "uptrend" in b.evidence[0]

    def test_downtrend_jadi_short(self) -> None:
        assert reading_from_structure(
            "1h", struktur_uji("DOWNTREND", "NONE")
        ).decision is Arah.SHORT

    def test_tren_mengalahkan_penembusan(self) -> None:
        """Breakout ke atas di dalam downtrend lebih sering pantulan daripada
        pembalikan, dan memperlakukannya sebagai LONG akan membeli setiap
        pantulan."""
        b = reading_from_structure("15m", struktur_uji("DOWNTREND", "BREAKOUT_UP"))

        assert b.decision is Arah.SHORT

    def test_tanpa_tren_penembusan_boleh_memutuskan(self) -> None:
        """Mengabaikannya berarti diam selama seluruh awal setiap tren."""
        b = reading_from_structure("15m", struktur_uji("RANGE", "BREAKOUT_UP"))

        assert b.decision is Arah.LONG

    def test_penembusan_palsu_dibalik(self) -> None:
        b = reading_from_structure(
            "15m", struktur_uji("UNDETERMINED", "FALSE_BREAKOUT_UP")
        )

        assert b.decision is Arah.SHORT

    def test_tanpa_keduanya_tidak_berarah(self) -> None:
        b = reading_from_structure("15m", struktur_uji("RANGE", "NONE"))

        assert b.decision is Arah.NO_SIGNAL
        assert "tanpa tren" in b.evidence[0]

    def test_struktur_kosong_tidak_meledak(self) -> None:
        assert reading_from_structure("15m", None).decision is Arah.NO_SIGNAL

    def test_intervalnya_ikut_terbawa(self) -> None:
        assert reading_from_structure("4h", None).interval == "4h"


def struktur_uji(tren: str, tembus: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        trend=SimpleNamespace(value=tren), breakout=SimpleNamespace(value=tembus)
    )


class TestPenjelasanKonflik:
    def test_kelima_hal_yang_diminta_pasal_148_tercetak(self) -> None:
        teks = "\n".join(CONTOH_14_7.report())

        assert "DOMINAN: 15m" in teks          # timeframe mana yang dominan
        assert "ALASAN:" in teks               # mengapa
        assert "momentum recovery" in teks     # bukti yang mendukung
        assert "momentum bearish" in teks      # bukti yang melawan
        assert "REZIM: TRENDING UP" in teks    # rezim pasar

    def test_alasannya_menyebut_dominasi(self) -> None:
        assert "mendominasi" in CONTOH_14_7.reason

    def test_perlawanan_dari_atas_diberi_kalimatnya_sendiri(self) -> None:
        peta = Lintas(
            horizon="15m",
            readings=(b("15m", Arah.LONG), b("4h", Arah.SHORT)),
        )
        teks = "\n".join(peta.report())

        assert "bukan pullback" in teks

    def test_pullback_tidak_memicu_kalimat_itu(self) -> None:
        teks = "\n".join(CONTOH_14_5.report())

        assert "pullback" in teks
        assert "bukan pullback" not in teks

    def test_daftarnya_terurut_dari_terpendek(self) -> None:
        """Daftar yang terurut menurut teks akan menaruh 10m sebelum 5m."""
        urut = [x.split(":")[0].strip().lstrip("◀ ") for x in _daftar(CONTOH_14_5)]

        assert urut == ["5m", "10m", "15m", "1h", "4h"]

    def test_horizonnya_ditandai_di_daftar(self) -> None:
        bertanda = [x for x in _daftar(CONTOH_14_7) if "◀" in x]

        assert len(bertanda) == 1
        assert "15m:" in bertanda[0]


def _daftar(peta: Lintas) -> list[str]:
    """Baris pembacaan saja - blok di atas ringkasan dominan.

    Diambil dengan memotong di penanda, bukan dengan mencocokkan pola: pola
    yang mencocokkan "15m:" juga mencocokkan baris bukti di bawahnya, dan test
    urutan yang ikut menyapu blok pendukung akan gagal karena alasan yang salah.
    """
    baris = peta.report()
    batas = next(i for i, x in enumerate(baris) if "DOMINAN" in x)
    return [x for x in baris[:batas] if ":" in x]
