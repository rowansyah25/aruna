"""Faktor yang dihitung tapi tak pernah diberi bahan (audit Phase 18).

Diukur 2026-08-24 dengan menjalankan aplikasi yang sungguhan atas dua belas aset
dan mencetak nilai SETIAP faktor. Empat faktor bernilai - bobot enam dari dua
puluh lima bobot yang menilai - tidak pernah terukur di jalur mana pun::

    historical           0/12   <-- TAK PERNAH TERUKUR   bobot 3
    funding              0/12   <-- TAK PERNAH TERUKUR   bobot 1
    open_interest        0/12   <-- TAK PERNAH TERUKUR   bobot 1
    liquidation          0/12   <-- TAK PERNAH TERUKUR   bobot 1

Bukan karena datanya tidak ada. ``note.memory`` sudah memuat ringkasan Phase 15
beberapa ratus baris sebelum mutu dihitung; ``plan.buffer`` sudah memuat skor
buffer likuidasi dan sudah dicetak di pesan. Yang tidak ada pemanggilnya - cacat
yang sama untuk kedelapan kalinya di proyek ini.

Berkas ini menjaga sisi **futures**. Rekam jejak jalur spot dirangkai belakangan
lewat korpus ingatan bersama - lihat ``test_phase18_rekam_jejak_spot.py``.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace as NS

from aruna.futures.service import _mutu_futures, _rekam_jejak
from aruna.memory.outcome import SAMPEL_MINIMUM


def _ringkasan(*, cukup: bool = True, **kw: object) -> NS:
    dasar = {
        "cukup": cukup,
        "win_rate": {"LONG": 70, "SHORT": None},
        "dinilai": {"LONG": 40, "SHORT": 0},
    }
    dasar.update(kw)
    return NS(**dasar)


def _note(ringkasan: object) -> NS:
    return NS(memory=NS(ringkasan=ringkasan))


def _verdict(arah: str) -> NS:
    return NS(decision=NS(value=arah))


class TestRekamJejakDariIngatan:
    """Bagian 18.4 - "Historical Similarity"."""

    def test_akurasi_arah_yang_diambil(self) -> None:
        akurasi, sampel = _rekam_jejak(_note(_ringkasan()), _verdict("BUY"))

        assert akurasi == 0.70
        assert sampel == 40

    def test_arah_lain_tidak_dipinjam(self) -> None:
        """**Yang paling mudah salah.** Rekam jejak LONG di kondisi ini tidak
        mengatakan apa pun tentang SHORT, dan memakainya untuk menilai SHORT
        adalah angka yang benar untuk pertanyaan yang tidak diajukan."""
        akurasi, sampel = _rekam_jejak(_note(_ringkasan()), _verdict("SELL"))

        assert akurasi is None
        assert sampel == 0

    def test_sampel_yang_DINILAI_bukan_yang_cocok(self) -> None:
        """``per_arah`` menghitung seluruh kasus di arah itu termasuk yang
        hasilnya NEUTRAL. Memakainya melaporkan rekam jejak lebih tebal
        daripada yang benar-benar ada."""
        r = _ringkasan(per_arah={"LONG": 200}, dinilai={"LONG": 31})

        assert _rekam_jejak(_note(r), _verdict("BUY"))[1] == 31

    def test_gerbang_phase_15_dihormati(self) -> None:
        """Phase 15 menolak mengubah korpus setipis itu menjadi persen (lihat
        ``Ringkasan.kalimat``). Mutu yang memakai angkanya diam-diam akan
        menerbitkan sesuatu yang pemiliknya sendiri tolak cetak."""
        akurasi, sampel = _rekam_jejak(
            _note(_ringkasan(cukup=False)), _verdict("BUY")
        )

        assert akurasi is None
        assert sampel == 0

    def test_arah_tak_berarah_tidak_punya_rekam_jejak(self) -> None:
        """WAIT dan NO_SIGNAL adalah keputusan untuk tidak mengambil posisi,
        dan posisi yang tidak diambil tidak punya menang atau kalah."""
        assert _rekam_jejak(_note(_ringkasan()), _verdict("WAIT")) == (None, 0)

    def test_tanpa_ingatan_tidak_meledak(self) -> None:
        assert _rekam_jejak(None, _verdict("BUY")) == (None, 0)
        assert _rekam_jejak(NS(memory=None), _verdict("BUY")) == (None, 0)


class TestSampelIkutDiringkas:
    def test_ringkas_membawa_penyebutnya(self) -> None:
        """``win_rate`` tanpa penyebutnya adalah persen yang tidak bisa
        dinilai - dan penyebutnya dulu dihitung lalu dibuang di baris terakhir
        ``ringkas()``."""
        from datetime import UTC, datetime

        from aruna.memory.outcome import ringkas
        from aruna.memory.record import Hasil, Ingatan

        saat = datetime(2026, 8, 24, tzinfo=UTC)

        def _ingatan(hasil: Hasil) -> tuple:
            return (
                Ingatan(
                    signal_id="x", sidik=NS(), arah="BUY", hasil=hasil,
                    move_pct=None, locked_at=saat, resolved_at=saat,
                    model_version="uji", cakupan=8, mutu=NS(),
                ),
                NS(skor=90),
            )

        r = ringkas([
            _ingatan(Hasil.WIN), _ingatan(Hasil.LOSS), _ingatan(Hasil.NEUTRAL)
        ])

        assert r.per_arah["LONG"] == 3
        # NEUTRAL bukan kekalahan - ia keluar dari penyebut, bukan masuk.
        assert r.dinilai["LONG"] == 2
        assert r.win_rate["LONG"] == 50


class TestFaktorPerpetual:
    """Bagian 18.16 - tiga faktor yang hanya berlaku untuk perpetual."""

    def test_likuidasi_dari_buffer_yang_sudah_dihitung(self) -> None:
        """Angkanya sudah ada di ``plan.buffer`` dan sudah dicetak di pesan;
        yang belum ada hanyalah mengopernya ke penilai mutu."""
        assert _mutu_futures(NS(buffer=NS(score=80), funding=None))[
            "liquidation"
        ] == 0.8

    def test_tanpa_buffer_tidak_terukur(self) -> None:
        assert _mutu_futures(NS(buffer=None, funding=None))["liquidation"] is None

    def test_funding_memakai_ambang_yang_sudah_ada(self) -> None:
        """Ambangnya **dipinjam** dari Phase 14 - itu yang membuat angka ini
        bukan skala karangan. Tarif ekstrem menghasilkan nol, tarif nol
        menghasilkan satu."""
        from aruna.futures.funding import EXTREME_RATE

        tenang = _mutu_futures(
            NS(buffer=None, funding=NS(current_rate=Decimal("0")))
        )["funding"]
        ekstrem = _mutu_futures(
            NS(buffer=None, funding=NS(current_rate=Decimal(str(EXTREME_RATE))))
        )["funding"]

        assert tenang == 1.0
        assert ekstrem == 0.0

    def test_funding_negatif_sama_beratnya(self) -> None:
        """Ekstrem di dua arah sama-sama ekstrem; yang diukur besarnya, bukan
        tandanya."""
        naik = _mutu_futures(
            NS(buffer=None, funding=NS(current_rate=Decimal("0.001")))
        )["funding"]
        turun = _mutu_futures(
            NS(buffer=None, funding=NS(current_rate=Decimal("-0.001")))
        )["funding"]

        assert naik == turun

    def test_open_interest_sengaja_tidak_dibangun(self) -> None:
        """Nilainya ada di ``futures_metrics`` (1.840 baris terisi penuh), tapi
        tidak ada satu pun aturan di sistem ini yang mengatakan open interest
        berapa itu baik - tidak ada ambang untuk dipinjam. Menerjemahkannya
        menjadi 0-1 berarti mengarang skala, lalu bobot satu penuh bergerak
        menurut skala karangan itu.

        Diuji, bukan didiamkan: keputusan untuk TIDAK membangun sesuatu harus
        berubah dengan sengaja, bukan dengan tidak sengaja.
        """
        assert _mutu_futures(NS(buffer=None, funding=None))[
            "open_interest"
        ] is None

    def test_ketiganya_nama_argumen_score_signal(self) -> None:
        """Dioper lewat ``**``, jadi kunci yang meleset akan menjadi
        ``TypeError`` di produksi - bukan faktor yang diam-diam hilang."""
        import inspect

        from aruna.signals.quality import score_signal

        arg = inspect.signature(score_signal).parameters
        assert set(_mutu_futures(NS(buffer=None, funding=None))) <= set(arg)


class TestTerukurLewatPenilainya:
    """Diuji lewat nilai yang keluar, bukan lewat potongan teks di sumber.

    Penjaga struktural membuktikan tersambung; hanya menjalankan penilainya dan
    melihat faktornya terukur membuktikan bekerja.
    """

    def _mutu(self, **kw: object):
        from datetime import UTC, datetime

        from aruna.futures.service import _mutu_signal

        saat = datetime(2026, 8, 24, 12, tzinfo=UTC)
        konteks = NS(
            state=NS(data_quality="OK", is_realtime=True, declared_delay_sec=0,
                     spread_bps=None, bid_depth=None, ask_depth=None),
            as_of=saat, structure=None,
            reading=lambda n: None, value=lambda n: None,
            regime=None, router=None, scenario=None,
        )
        plan = NS(horizon_hours=1.0, entry=None, stop=None, target=None,
                  buffer=NS(score=80), funding=NS(current_rate=Decimal("0")))
        umum = {"context": konteks, "verdict": _verdict("BUY"), "plan": plan,
                "now": saat, "note": _note(_ringkasan())}
        umum.update(kw)
        return _mutu_signal(**umum)

    def _skor(self, mutu, nama: str):
        return next(f.score for f in mutu.factors if f.name == nama)

    def test_historical_terukur(self) -> None:
        assert self._skor(self._mutu(), "historical") == 0.70

    def test_liquidation_terukur(self) -> None:
        assert self._skor(self._mutu(), "liquidation") == 0.8

    def test_funding_terukur(self) -> None:
        assert self._skor(self._mutu(), "funding") == 1.0

    def test_tanpa_ingatan_historical_tetap_tidak_terukur(self) -> None:
        """Yang dirangkai bukan angka bawaan - ia hilang saat bahannya hilang."""
        assert self._skor(self._mutu(note=None), "historical") is None


class TestTerpasangDiJalurHidup:
    def test_mutu_signal_menanyakan_keempatnya(self) -> None:
        """Ditulis dan diuji tidak sama dengan dipanggil."""
        import inspect

        from aruna.futures import service

        sumber = inspect.getsource(service._mutu_signal)

        assert "_rekam_jejak" in sumber
        assert "_mutu_futures" in sumber
        assert "accuracy=" in sumber

    def test_attach_quality_mengoper_notenya(self) -> None:
        """Ingatan Phase 15 menempel di ``note``; penilai yang tidak menerima
        ``note`` tidak bisa membacanya berapa pun benarnya ``_rekam_jejak``."""
        import inspect

        from aruna.futures import service

        assert "note=note" in inspect.getsource(service.attach_quality)

    def test_rekam_jejak_tidak_memakai_katalog_pola(self) -> None:
        """**Jalan pintas yang salahnya halus, dan tetap dilarang.**

        Rekam jejak futures dibaca dari korpus ingatan Phase 15. Yang dijaga di
        sini alternatif yang terlihat jauh lebih murah dan **bias**:
        ``memory.pola.cocokkan`` hanya memulangkan pola yang ``beats_baseline``
        (57 dari 368) dengan sampel di atas ``SAMPEL_POLA``. Merangkainya
        membuat ``historical`` terukur justru ketika rekam jejaknya bagus dan
        tidak terukur ketika buruk - pengukuran satu arah yang hanya bisa
        menaikkan skor.

        Korpus ingatan tidak menyaring apa pun, dan itu yang membuatnya boleh
        dipakai menilai.
        """
        import inspect

        from aruna.memory import korpus

        sumber = inspect.getsource(korpus)

        assert "memory.pola" not in sumber
        assert "cocokkan(" not in sumber

    def test_katalog_pola_memang_menyaring_baseline(self) -> None:
        """Larangan di atas hanya berlaku selama penyaringnya memang ada. Kalau
        `cocokkan` berhenti menyaring, jalan pintas itu berhenti bias - dan
        larangannya jadi usang tanpa ada yang tahu."""
        import inspect

        from aruna.memory import pola

        assert "beats_baseline" in inspect.getsource(pola.cocokkan)

    def test_ambang_sampel_phase_15_masih_ada(self) -> None:
        """Gerbangnya dipinjam, bukan disalin - kalau konstanta ini hilang,
        ``_rekam_jejak`` kehilangan aturannya tanpa satu pun test merah."""
        assert SAMPEL_MINIMUM > 0
