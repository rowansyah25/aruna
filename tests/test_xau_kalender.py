"""Kalender ekonomi: keamanan timestamp yang berbentuk, bukan dijanjikan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aruna.xau.kalender import (
    JEDA_SEBELUM_HIGH,
    JEDA_SESUDAH_HIGH,
    Dampak,
    PeristiwaEkonomi,
    gejolak_rilis,
    ke_utc,
    ringkas,
)
from aruna.xau.sumber_kalender import (
    SUMBER_FF,
    SUMBER_FRED,
    urai_forexfactory,
    urai_fred,
)

SEKARANG = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _p(menit: int, *, dampak: Dampak = Dampak.HIGH, negara: str = "USD",
       actual: str | None = None, judul: str = "CPI") -> PeristiwaEkonomi:
    return PeristiwaEkonomi(
        judul=judul,
        negara=negara,
        saat=SEKARANG + timedelta(minutes=menit),
        dampak=dampak,
        sumber="uji",
        actual=actual,
    )


class TestKebocoranMasaDepan:
    def test_actual_peristiwa_belum_rilis_tidak_diserahkan(self) -> None:
        """Penjaga terakhir. Sumber yang keliru memuat actual untuk peristiwa
        yang belum terjadi tidak cukup untuk membocorkannya."""
        nanti = _p(30, actual="3.1%")
        assert nanti.actual_pada(SEKARANG) is None

    def test_actual_peristiwa_sudah_rilis_diserahkan(self) -> None:
        assert _p(-30, actual="3.1%").actual_pada(SEKARANG) == "3.1%"

    def test_tepat_pada_detik_rilis_dianggap_sudah(self) -> None:
        """Batasnya inklusif: pada detik rilis, angkanya sudah publik."""
        assert _p(0, actual="3.1%").actual_pada(SEKARANG) == "3.1%"

    def test_peristiwa_depan_tidak_pernah_jadi_terakhir(self) -> None:
        k = ringkas([_p(30), _p(60)], sekarang=SEKARANG)
        assert k.terakhir is None
        assert k.berikutnya is not None


class TestRingkasan:
    def test_jarak_ke_peristiwa_berikutnya(self) -> None:
        k = ringkas([_p(45), _p(120)], sekarang=SEKARANG)
        assert k.menit_ke_berikutnya == 45.0
        assert k.berikutnya.saat == SEKARANG + timedelta(minutes=45)

    def test_jarak_sejak_peristiwa_terakhir(self) -> None:
        k = ringkas([_p(-20), _p(-90)], sekarang=SEKARANG)
        assert k.menit_sejak_terakhir == 20.0

    def test_yang_terdekat_dipilih_bukan_yang_pertama_di_daftar(self) -> None:
        k = ringkas([_p(200), _p(15), _p(90)], sekarang=SEKARANG)
        assert k.menit_ke_berikutnya == 15.0

    def test_kepadatan_melingkupi_masa_lalu_dan_depan(self) -> None:
        """Rilis besar sejam lalu masih menggerakkan harga."""
        k = ringkas([_p(-60), _p(60), _p(-2000)], sekarang=SEKARANG)
        assert k.dampak_tinggi_24j == 2

    def test_dampak_rendah_tidak_dihitung_padat(self) -> None:
        k = ringkas(
            [_p(-60, dampak=Dampak.LOW), _p(60, dampak=Dampak.MEDIUM)],
            sekarang=SEKARANG,
        )
        assert k.dampak_tinggi_24j == 0


class TestRelevansi:
    def test_negara_lain_diabaikan(self) -> None:
        k = ringkas([_p(30, negara="NZD"), _p(90, negara="USD")], sekarang=SEKARANG)
        assert k.menit_ke_berikutnya == 90.0

    def test_all_ikut_dihitung(self) -> None:
        """ForexFactory memakai `All` untuk peristiwa lintas-negara."""
        k = ringkas([_p(30, negara="All")], sekarang=SEKARANG)
        assert k.berikutnya is not None


class TestTidakTerukur:
    def test_tanpa_peristiwa_sama_sekali_tidak_terukur(self) -> None:
        """Kosong berarti TIDAK ADA KALENDER, beda dari tidak ada peristiwa."""
        k = ringkas([], sekarang=SEKARANG)
        assert k.terukur is False
        assert k.sumber == ()

    def test_ada_kalender_tapi_tak_relevan_tetap_terukur(self) -> None:
        """Sumbernya menjawab; kebetulan tak ada peristiwa USD. Itu keterangan."""
        k = ringkas([_p(30, negara="NZD")], sekarang=SEKARANG)
        assert k.terukur is True
        assert k.berikutnya is None


class TestUraiForexFactory:
    def test_bentuk_sungguhan_terbaca(self) -> None:
        """Payload nyata, disalin dari respons 2026-08-28."""
        payload = [
            {
                "title": "Core PCE Price Index m/m",
                "country": "USD",
                "date": "2026-08-26T08:30:00-04:00",
                "impact": "High",
                "forecast": "0.2%",
                "previous": "0.1%",
            }
        ]
        (p,) = urai_forexfactory(payload)
        assert p.judul == "Core PCE Price Index m/m"
        assert p.dampak is Dampak.HIGH
        assert p.forecast == "0.2%"
        assert p.sumber == SUMBER_FF

    def test_offset_dikonversi_ke_utc(self) -> None:
        """-04:00 disimpan apa adanya akan meleset empat jam dari as_of bar."""
        payload = [{
            "title": "X", "country": "USD",
            "date": "2026-08-26T08:30:00-04:00", "impact": "High",
        }]
        (p,) = urai_forexfactory(payload)
        assert p.saat == datetime(2026, 8, 26, 12, 30, tzinfo=UTC)

    def test_actual_selalu_none(self) -> None:
        """Diukur: nol dari 71 peristiwa memuatnya, termasuk 50 yang lewat."""
        payload = [{
            "title": "X", "country": "USD",
            "date": "2026-08-26T08:30:00-04:00", "impact": "High",
        }]
        assert urai_forexfactory(payload)[0].actual is None

    def test_baris_rusak_dilewati_bukan_menjatuhkan(self) -> None:
        """Satu baris cacat tidak boleh membuang tujuh puluh yang sehat."""
        payload = [
            {"title": "baik", "country": "USD",
             "date": "2026-08-26T08:30:00-04:00", "impact": "High"},
            {"title": "tanpa tanggal", "country": "USD"},
            {"date": "bukan tanggal", "title": "x", "country": "USD"},
            "bukan objek",
        ]
        assert len(urai_forexfactory(payload)) == 1

    def test_dampak_asing_tidak_jadi_low(self) -> None:
        """Memetakannya ke LOW membuat peristiwa besar terhitung sepele."""
        payload = [{
            "title": "X", "country": "USD",
            "date": "2026-08-26T08:30:00-04:00", "impact": "Critical",
        }]
        assert urai_forexfactory(payload)[0].dampak is Dampak.TIDAK_DINYATAKAN

    def test_payload_bukan_daftar_tidak_menjatuhkan(self) -> None:
        assert urai_forexfactory({"error": "x"}) == []


class TestUraiFred:
    def test_observasi_jadi_actual(self) -> None:
        """FRED memberi angka yang SUDAH terbit - kebalikan ForexFactory."""
        payload = {"observations": [
            {"date": "2026-08-26", "value": "3.1"},
            {"date": "2026-07-26", "value": "2.9"},
        ]}
        hasil = urai_fred(payload, judul="CPI")
        assert len(hasil) == 2
        assert hasil[0].actual == "3.1"
        assert hasil[0].forecast is None
        assert hasil[0].sumber == SUMBER_FRED

    def test_titik_berarti_tidak_ada_observasi(self) -> None:
        """FRED memakai '.' untuk yang tak ada; menyimpannya sebagai actual
        akan membuat 'tidak ada data' terbaca sebagai angka."""
        payload = {"observations": [{"date": "2026-08-26", "value": "."}]}
        assert urai_fred(payload, judul="CPI") == []

    def test_dampak_tidak_dikarang(self) -> None:
        """FRED tak memberi tingkat dampak; HIGH akan membuat tiap observasi
        terlihat penting."""
        payload = {"observations": [{"date": "2026-08-26", "value": "3.1"}]}
        assert urai_fred(payload, judul="CPI")[0].dampak is Dampak.TIDAK_DINYATAKAN


class TestGabungDuaSumber:
    def test_keduanya_masuk_satu_ringkasan(self) -> None:
        """Lubang masing-masing ditutup yang lain: FF punya jadwal tanpa
        actual, FRED punya actual tanpa forecast."""
        ff = urai_forexfactory([{
            "title": "Core PCE", "country": "USD",
            "date": "2026-08-28T14:00:00+00:00", "impact": "High",
            "forecast": "0.2%", "previous": "0.1%",
        }])
        fred = urai_fred(
            {"observations": [{"date": "2026-08-26", "value": "3.1"}]},
            judul="CPI",
        )
        k = ringkas(ff + fred, sekarang=SEKARANG)
        assert set(k.sumber) == {SUMBER_FF, SUMBER_FRED}
        assert k.berikutnya.forecast == "0.2%"
        assert k.terakhir.actual == "3.1"


class TestKeUtc:
    def test_tanpa_zona_dianggap_utc(self) -> None:
        assert ke_utc("2026-08-26T08:30:00") == datetime(
            2026, 8, 26, 8, 30, tzinfo=UTC
        )

    def test_tanggal_saja_bisa_diurai(self) -> None:
        """FRED memberi tanggal tanpa jam."""
        assert ke_utc("2026-08-26") == datetime(2026, 8, 26, tzinfo=UTC)


def _ringkas(*peristiwa: PeristiwaEkonomi):
    return ringkas(list(peristiwa), sekarang=SEKARANG)


class TestGerbangRilis:
    """Hanya HIGH yang menutup, dan hanya di dalam jendelanya.

    Terukur 2026-08-28, rentang bar yang memuat rilis dibagi ATR saat itu,
    terhadap 4.974 bar biasa sebagai garis dasar:

        garis dasar 0,95 ATR    LOW 1,02 ATR    MEDIUM 0,91 ATR    HIGH 4,21 ATR

    LOW tidak bisa dibedakan dari bar biasa, jadi menutupnya membuang peluang
    tanpa membeli keamanan apa pun.
    """

    def test_low_tepat_di_depan_hidung_tetap_lolos(self) -> None:
        """Permintaan operator, dan datanya mendukung: kalau news LOW, jalan."""
        assert gejolak_rilis(_ringkas(_p(1, dampak=Dampak.LOW))) is None

    def test_medium_lolos_dan_angkanya_tetap_tercatat(self) -> None:
        assert gejolak_rilis(_ringkas(_p(1, dampak=Dampak.MEDIUM))) is None

    def test_high_menjelang_ditutup(self) -> None:
        alasan = gejolak_rilis(_ringkas(_p(10, judul="Fed Chair Speaks")))
        assert alasan is not None
        assert "Fed Chair Speaks" in alasan

    def test_high_baru_saja_lewat_ditutup(self) -> None:
        alasan = gejolak_rilis(_ringkas(_p(-int(JEDA_SESUDAH_HIGH) + 5)))
        assert alasan is not None
        assert "sesudah" in alasan

    def test_high_sudah_jauh_lewat_dibuka_lagi(self) -> None:
        """Gejolaknya pulih di 30 menit - terukur, bukan ditebak."""
        assert gejolak_rilis(_ringkas(_p(-int(JEDA_SESUDAH_HIGH) - 5))) is None

    def test_high_masih_jauh_di_depan_dibuka(self) -> None:
        assert gejolak_rilis(_ringkas(_p(int(JEDA_SEBELUM_HIGH) + 5))) is None

    def test_high_di_balik_low_tetap_menutup(self) -> None:
        """Kasus produksi 2026-08-28 pukul 13:40.

        `berikutnya` menunjuk Chicago PMI (LOW, 5 menit lagi) sementara pidato
        Ketua Fed (HIGH) menunggu 20 menit kemudian. Gerbang yang membaca
        `berikutnya.dampak` akan meloloskannya - dan sinyal apa pun yang lahir
        di situ akan menahan bar rilis yang hari itu bergerak 11 ATR.
        """
        berita = _ringkas(
            _p(5, dampak=Dampak.LOW, judul="Chicago PMI"),
            _p(20, dampak=Dampak.HIGH, judul="Fed Chairman Speaks"),
        )
        assert berita.berikutnya.dampak is Dampak.LOW, "prasyarat kasusnya"
        alasan = gejolak_rilis(berita)
        assert alasan is not None
        assert "Fed Chairman Speaks" in alasan

    def test_kalender_mati_tidak_menutup(self) -> None:
        """Uptime API gratis tidak boleh jadi sakelar mati.

        ForexFactory membalas 429 pada 2026-08-28. Menutup saat sumbernya diam
        akan membuat ARUNA bisu setiap kali pihak ketiga tersendat.
        """
        assert gejolak_rilis(None) is None
        assert gejolak_rilis(ringkas([], sekarang=SEKARANG)) is None

    def test_peristiwa_negara_lain_tidak_menutup(self) -> None:
        """Rilis Selandia Baru tidak menggerakkan emas lewat dolar."""
        assert gejolak_rilis(_ringkas(_p(10, negara="NZD"))) is None
