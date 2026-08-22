"""Penjaga penyambungan: modul yang ditulis, diuji, dan tidak pernah dipanggil.

Keluarga cacat ini sudah berkali-kali muncul di sistem ini - bagian PENILAIAN
pernah hilang dari pesan tanpa error dan tanpa log, dan seluruh unit testnya
tetap hijau. Modul `aruna.decision` punya enam belas anggota, dan pada
pengukuran 2026-08-20 delapan di antaranya tidak pernah diimpor dari luar
paketnya sendiri.

Berkas ini menguji **pemanggilnya**, bukan yang dipanggil.
"""

from __future__ import annotations

import inspect
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.test_futures_notify_pasal1426 import NOW, FakePlan, FakeSide, note


class TestFinalDipakaiPesanFutures:
    def test_notify_mengimpor_final(self) -> None:
        from aruna.futures import notify

        assert "from aruna.decision.final import" in inspect.getsource(notify)

    def test_pesannya_membawa_keputusan_final(self) -> None:
        """PASAL 14.2: keputusannya LONG, SHORT, atau NO SIGNAL.

        ``SIDE`` yang sudah dicetak adalah sisi posisi. Keduanya sama pada
        rencana yang terbit dan berbeda persis pada rencana yang tidak.
        """
        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(), NOW, note=note())

        assert "KEPUTUSAN FINAL:" in teks
        assert "KEPUTUSAN FINAL: LONG" in teks

    def test_barisnya_disusun_dari_arah_yang_sungguhan(self) -> None:
        """Diperiksa lewat fungsinya langsung, bukan lewat "tidak ada di teks".

        Versi pertama test ini cuma memeriksa ``"KEPUTUSAN FINAL: FLAT" not in
        teks`` - dan itu hijau bahkan sebelum barisnya ada sama sekali. Sebuah
        pernyataan negatif tentang teks yang belum pernah dicetak tidak menguji
        apa pun.
        """
        from aruna.futures.notify import _keputusan_final

        assert _keputusan_final(FakePlan()) == ["KEPUTUSAN FINAL: LONG"]
        assert _keputusan_final(FakePlan(side=FakeSide("SHORT"))) == [
            "KEPUTUSAN FINAL: SHORT"
        ]

    def test_flat_tidak_pernah_tercetak_sebagai_keputusan(self) -> None:
        """``side='FLAT'`` ada, truthy, dan artinya persis "tidak berarah"."""
        from aruna.futures.notify import _keputusan_final

        assert _keputusan_final(FakePlan(side=FakeSide("FLAT"))) == []

    def test_kegagalannya_tidak_menghentikan_pesan(self) -> None:
        """Yang hilang saat arahnya tak dikenali adalah satu baris keterangan -
        bukan pesan yang membawa entry dan stop."""
        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(side=FakeSide("MUNGKIN")), NOW, note=note())

        assert "64120" in teks
        assert "KEPUTUSAN FINAL:" not in teks

    def test_penundaan_dicatat_sebagai_peringatan_bukan_pengecualian(
        self, monkeypatch
    ) -> None:
        """``FLAT`` adalah keadaan yang wajar - rencana WAIT memang punya sisi
        itu. Mencatatnya sebagai jejak pengecualian akan mengisi log dengan
        alarm palsu, dan log yang penuh alarm palsu berhenti dibaca."""
        from types import SimpleNamespace

        from aruna.futures import notify as modul

        dicatat: list[str] = []
        monkeypatch.setattr(
            modul,
            "log",
            SimpleNamespace(
                exception=lambda nama, **kw: dicatat.append(f"exception:{nama}"),
                warning=lambda nama, **kw: dicatat.append(f"warning:{nama}"),
            ),
        )

        modul._keputusan_final(FakePlan(side=FakeSide("FLAT")))

        assert not [d for d in dicatat if d.startswith("exception:")]


class TestTimingDiPesan:
    def test_entry_di_bawah_acuan_untuk_long_adalah_pullback(self) -> None:
        from aruna.decision.timing import Timing
        from aruna.futures.notify import _entry_timing

        baris = "\n".join(_entry_timing(FakePlan(
            reference_price=Decimal("64500"), entry=Decimal("64120"),
        )))

        assert Timing.PULLBACK.value in baris

    def test_entry_sama_dengan_acuan_adalah_masuk_sekarang(self) -> None:
        from aruna.decision.timing import Timing
        from aruna.futures.notify import _entry_timing

        baris = "\n".join(_entry_timing(FakePlan(
            reference_price=Decimal("64120"), entry=Decimal("64120"),
        )))

        assert Timing.NOW.value in baris

    def test_short_dibalik(self) -> None:
        """Salah tanda di sini memberi operator waktu masuk yang berlawanan
        dengan posisinya: "tunggu pullback" pada harga yang justru sudah
        lewat."""
        from aruna.decision.timing import Timing
        from aruna.futures.notify import _entry_timing

        baris = "\n".join(_entry_timing(FakePlan(
            side=FakeSide("SHORT"),
            reference_price=Decimal("63800"), entry=Decimal("64120"),
        )))

        assert Timing.PULLBACK.value in baris

    def test_long_yang_mengejar_harga_adalah_breakout(self) -> None:
        from aruna.decision.timing import Timing
        from aruna.futures.notify import _entry_timing

        baris = "\n".join(_entry_timing(FakePlan(
            reference_price=Decimal("63900"), entry=Decimal("64120"),
        )))

        assert Timing.BREAKOUT.value in baris

    def test_tanpa_acuan_tidak_menebak(self) -> None:
        """§13.26: kalau datanya tidak ada, tidak ada barisnya - bukan
        MASUK SEKARANG yang kebetulan terbaca seperti ajakan."""
        from aruna.futures.notify import _entry_timing

        assert _entry_timing(FakePlan(reference_price=None)) == []

    def test_arah_tak_dikenal_tidak_menebak(self) -> None:
        from aruna.futures.notify import _entry_timing

        assert _entry_timing(FakePlan(side=FakeSide("FLAT"))) == []

    def test_pesannya_membawanya(self) -> None:
        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(
            reference_price=Decimal("64500"), entry=Decimal("64120"),
        ), NOW, note=note())

        assert "ENTRY TIMING:" in teks


class TestJejakTercatat:
    """PASAL 14.30: keputusan harus bisa disusun ulang.

    Jejak yang bolong adalah keputusan yang tidak bisa diperiksa ulang - dan
    §11.21 melarang mengubah signal lama, jadi kesempatan mencatatnya cuma
    sekali, saat itu juga.
    """

    def test_service_mengimpor_trail(self) -> None:
        from aruna.futures import service

        assert "from aruna.decision.trail import" in inspect.getsource(service)

    def _tangkap(self, monkeypatch) -> list[tuple[str, dict]]:
        from types import SimpleNamespace

        from aruna.futures import service as modul

        keluar: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            modul,
            "log",
            SimpleNamespace(
                info=lambda nama, **kw: keluar.append((nama, kw)),
                warning=lambda nama, **kw: keluar.append((nama, kw)),
                exception=lambda nama, **kw: keluar.append((f"!{nama}", kw)),
            ),
        )
        return keluar

    def _bahan(self):
        """Bahan yang bentuknya meniru objek sungguhan.

        Versi pertama memakai ``opinions=("a",)`` - tuple string. Pencatat yang
        membaca ``o.role.value`` meledak di situ, tertangkap penjaga luar, dan
        seluruh test lulus sambil menguji jalur kegagalan. Palsu yang bentuknya
        salah adalah cara paling andal membuat test hijau atas kode yang rusak,
        dan itu sudah terjadi dua kali di sesi ini.
        """
        from types import SimpleNamespace

        def _peran(nama: str, keputusan: str):
            return SimpleNamespace(
                role=SimpleNamespace(value=nama),
                decision=SimpleNamespace(value=keputusan),
            )

        def _keberatan(penuduh: str, sasaran: str, dasar: str):
            return SimpleNamespace(
                accuser=SimpleNamespace(value=penuduh),
                target=SimpleNamespace(value=sasaran),
                ground=dasar,
            )

        from aruna.notify.verdict import VoteSplit

        return {
            "plan": FakePlan(),
            "context": SimpleNamespace(strategy="trend-continuation"),
            "verdict": SimpleNamespace(
                decision=SimpleNamespace(value="BUY"),
                opinions=(_peran("TECHNICAL", "BUY"), _peran("VOLUME", "BUY")),
                protest=SimpleNamespace(
                    objections=(
                        _keberatan("TECHNICAL", "REVERSAL", "opposite_direction"),
                    )
                ),
                veto=SimpleNamespace(vetoes=()),
            ),
            "note": note(
                regime="TRENDING UP",
                decision_readings={"trend": 1.0},
                split=VoteSplit(setuju=("TECHNICAL",), kontra=(), abstain=()),
            ),
        }

    def test_satu_baris_per_rencana_berarah(self, monkeypatch) -> None:
        from aruna.futures.service import catat_jejak

        keluar = self._tangkap(monkeypatch)
        catat_jejak(**self._bahan())

        assert [n for n, _ in keluar] == ["decision.trail"]

    def test_seluruh_bidang_wajibnya_terisi(self, monkeypatch) -> None:
        from aruna.decision.score import Arah
        from aruna.decision.trail import required_fields
        from aruna.futures.service import catat_jejak

        keluar = self._tangkap(monkeypatch)
        catat_jejak(**self._bahan())
        _, bidang = keluar[0]

        for j in required_fields(Arah.LONG):
            assert j.name.lower() in bidang, j

    def test_yang_tidak_tersedia_jadi_unknown_bukan_karangan(
        self, monkeypatch
    ) -> None:
        """§13.26: kalau datanya tidak ada, UNKNOWN. Mengisinya dengan nol atau
        kalimat kosong membuat jejaknya terlihat lengkap sambil bohong."""
        from types import SimpleNamespace

        from aruna.futures.service import catat_jejak

        keluar = self._tangkap(monkeypatch)
        bahan = self._bahan()
        bahan["context"] = SimpleNamespace(strategy=None)
        bahan["note"] = note(regime="", split=bahan["note"].split)
        catat_jejak(**bahan)
        _, bidang = keluar[0]

        # Dua jalur pembacaan, dan keduanya harus mengaku tidak tahu:
        # `_ringkas` untuk yang bisa berupa kumpulan, `_teks` untuk yang tunggal.
        assert bidang["strategy"] == "UNKNOWN"
        assert bidang["regime"] == "UNKNOWN"

    def test_rencana_tanpa_arah_tidak_dicatat(self, monkeypatch) -> None:
        """Jejak PASAL 14.30 adalah jejak sebuah keputusan. Rencana WAIT tidak
        punya keputusan untuk dijejaki, dan mencatatnya akan mengisi log dengan
        baris yang seluruh bidang berarahnya UNKNOWN."""
        from aruna.futures.service import catat_jejak

        keluar = self._tangkap(monkeypatch)
        bahan = self._bahan()
        bahan["plan"] = FakePlan(side=FakeSide("FLAT"))
        catat_jejak(**bahan)

        assert keluar == []

    def test_kegagalannya_tidak_menjatuhkan_rencana(self, monkeypatch) -> None:
        class Meledak:
            @property
            def side(self):
                raise RuntimeError("rencana rusak")

        from aruna.futures.service import catat_jejak

        keluar = self._tangkap(monkeypatch)
        bahan = self._bahan()
        bahan["plan"] = Meledak()
        catat_jejak(**bahan)

        assert [n for n, _ in keluar] == ["!decision.trail_failed"]

    def test_barisnya_muat_di_log(self, monkeypatch) -> None:
        """Terukur di produksi 2026-08-20: versi pertama menulis ``repr`` penuh
        sembilan ``AgentOpinion`` beserta seluruh ``EvidenceRef``-nya - satu
        baris lebih dari enam ribu karakter, sebelas kali per tick, sembilan
        puluh enam tick sehari.

        Itu bukan jejak yang bisa dibaca; itu berkas log yang tidak bisa
        dibuka. Yang dipotong panjangnya, bukan keberadaannya - jumlah
        anggotanya tetap dilaporkan.
        """
        from types import SimpleNamespace

        from aruna.futures.service import catat_jejak

        # Sepanjang yang sungguhan: sembilan agent dengan alasan dan bukti
        # penuh. Bahan yang pendek membuat test ini hijau atas pemotong yang
        # dicabut - dan itu terjadi pada percobaan pertama.
        panjang = SimpleNamespace(
            role=SimpleNamespace(value="TECHNICAL" * 40),
            decision=SimpleNamespace(value="BUY" * 40),
        )
        bahan = self._bahan()
        bahan["verdict"] = SimpleNamespace(
            decision=SimpleNamespace(value="BUY"),
            opinions=tuple(panjang for _ in range(9)),
            protest=SimpleNamespace(objections=()),
            veto=SimpleNamespace(vetoes=()),
        )
        bahan["context"] = SimpleNamespace(strategy="x" * 900)

        keluar = self._tangkap(monkeypatch)
        catat_jejak(**bahan)
        _, bidang = keluar[0]

        for nama, nilai in bidang.items():
            assert len(str(nilai)) <= 200, (nama, len(str(nilai)))

    def test_angka_rencana_benar_benar_terbawa(self, monkeypatch) -> None:
        """Penjaga terhadap kelas kesalahan ``model_version``.

        Bidang itu selalu UNKNOWN di produksi karena dibaca dari objek yang
        tidak punya bidangnya - dan tidak ada yang tahu selama berjam-jam,
        karena UNKNOWN terbaca seperti "datanya memang tidak ada". Entry, SL,
        dan TP bisa gagal dengan cara yang sama, dan di sana taruhannya lebih
        besar: PASAL 14.30 menuntut keputusan bisa disusun ulang, dan tanpa
        ketiganya tidak ada yang bisa disusun.

        Pengukuran produksi tidak bisa menutup lubang ini sendiri - seluruh
        rencana pada tick pengukuran berhenti sebelum sizing, jadi ketiganya
        memang kosong di sana.
        """
        from aruna.futures.service import catat_jejak

        keluar = self._tangkap(monkeypatch)
        catat_jejak(**self._bahan())
        _, bidang = keluar[0]

        assert bidang["entry"] == "64120"
        assert bidang["sl"] == "63780"
        assert bidang["tp"] == "64950"
        assert bidang["invalidation"].startswith("1.5 ATR")

    def test_suara_agent_dibaca_dari_catatan_council(self, monkeypatch) -> None:
        """``split`` ada di ``CouncilNote``, bukan di vonisnya.

        Versi pertama membacanya dari ``verdict.split``, dan seluruh sebelas
        jejak pada pengukuran produksi pertama melaporkan UNKNOWN - lapisan yang
        jelas berjalan tercatat sebagai lapisan yang hilang. Kelas kesalahan
        yang sama dengan ``note.strategy`` yang ternyata ada di ``context``.
        """
        from aruna.futures.service import catat_jejak

        keluar = self._tangkap(monkeypatch)
        catat_jejak(**self._bahan())
        _, bidang = keluar[0]

        assert bidang["agent_votes"] != "UNKNOWN"

    def test_versi_model_bukan_dari_rencana(self, monkeypatch) -> None:
        """``FuturesPlan`` tidak punya bidang ``model_version`` - versinya
        dipegang service dan diberikan ke ``save()`` sebagai argumen terpisah.
        Membacanya dari rencana menghasilkan UNKNOWN selamanya, dan PASAL 14.30
        menuntutnya untuk bisa menyusun ulang keputusan."""
        from aruna.futures.service import catat_jejak

        keluar = self._tangkap(monkeypatch)
        catat_jejak(**self._bahan(), model_version="futures-f6")
        _, bidang = keluar[0]

        assert bidang["model_version"] == "futures-f6"

    def test_versi_model_ikut_dari_jalur_hidup(self) -> None:
        """Dicari di dalam panggilan ``catat_jejak``, bukan di seluruh fungsi.

        ``_plan_one`` sudah memuat ``model_version=self._model_version`` di
        panggilan ``save()``-nya, jadi pencarian seluruh fungsi tetap hijau
        walau argumen ini dicabut dari pencatat - persis yang terjadi pada
        cabut-uji pertama.
        """
        from aruna.futures.service import FuturesPlanService

        sumber = inspect.getsource(FuturesPlanService._plan_one)
        mulai = sumber.index("catat_jejak(")
        panggilan = sumber[mulai : sumber.index(")", sumber.index("note=note", mulai))]

        assert "model_version=self._model_version" in panggilan

    def test_dipanggil_dari_jalur_hidup(self) -> None:
        """Tanpa ini, seluruh pencatat bisa dihapus dari ``_plan_one`` dan
        setiap test di atas tetap hijau - persis bagaimana bagian PENILAIAN
        pernah hilang dari pesan tanpa error dan tanpa log.

        **Batasnya disebut:** ini pemeriksaan sumber, bukan pemanggilan. Ia
        merah kalau barisnya dihapus, dan tidak tahu apa-apa kalau barisnya ada
        tapi tak pernah tereksekusi. Pola berhenti-awal yang dipakai
        ``TestPenyambungDipanggilJalurHidup`` tidak berlaku di sini karena
        pencatat ini duduk di baris terakhir ``_plan_one``, sesudah seluruh
        rencana selesai disusun.
        """
        from aruna.futures.service import FuturesPlanService

        sumber = inspect.getsource(FuturesPlanService._plan_one)

        assert "catat_jejak(" in sumber
        assert sumber.index("observe_decision(") < sumber.index("catat_jejak(")


class TestPenjelasanBerlapis:
    """PASAL 14.29: dua sumber BERBEDA, bukan dua kalimat."""

    def _opini(self, peran: str, keputusan: str, *alasan: str):
        from types import SimpleNamespace

        return SimpleNamespace(
            role=SimpleNamespace(value=peran),
            decision=SimpleNamespace(value=keputusan),
            reasoning=alasan,
        )

    def _verdict(self, *opini):
        from types import SimpleNamespace

        return SimpleNamespace(opinions=opini)

    def _susun(self, *opini, strategy=None):
        from types import SimpleNamespace

        from aruna.decision.score import Arah
        from aruna.futures.service import _penjelasan

        return _penjelasan(
            self._verdict(*opini), Arah.LONG,
            SimpleNamespace(strategy=strategy),
        )

    def test_dua_sumber_berbeda_tersusun(self) -> None:
        p = self._susun(
            self._opini("STRUCTURE", "BUY", "higher high dan higher low"),
            self._opini("VOLUME", "BUY", "volume 2.1x rata-rata"),
        )

        assert p is not None
        assert len(p.sources) == 2

    def test_satu_sumber_tidak_cukup(self) -> None:
        """Dua kalimat dari sumber yang sama adalah satu alasan yang diulang -
        dan keputusan yang berdiri di atasnya runtuh bersamanya."""
        p = self._susun(
            self._opini("VOLUME", "BUY", "volume 2.1x", "volume naik terus"),
        )

        assert p is None

    def test_peran_yang_dipetakan_punya_sumbernya_sendiri(self) -> None:
        """Kebalikan dari test di bawahnya, dan pasangannya wajib.

        Kalau seluruh peran runtuh ke satu sumber, PASAL 14.29 tidak pernah
        bisa dipenuhi dan blok KENAPA tidak pernah dicetak - kegagalan yang
        diam sempurna.
        """
        from aruna.decision.explanation import Sumber

        p = self._susun(
            self._opini("STRUCTURE", "BUY", "higher high dan higher low"),
            self._opini("VOLUME", "BUY", "volume 2.1x rata-rata"),
        )

        assert p is not None
        assert set(p.sources) == {Sumber.STRUKTUR, Sumber.VOLUME}

    def test_sembilan_agent_tak_terpetakan_tetap_satu_sumber(self) -> None:
        """Peran yang tidak punya jenis buktinya sendiri jatuh ke AGENT.
        Kalau masing-masing diberi sumber sendiri, sembilan agent akan selalu
        memenuhi syarat dua sumber - dan PASAL 14.29 jadi formulir."""
        p = self._susun(
            self._opini("TECHNICAL", "BUY", "ema_9 di atas ema_21"),
            self._opini("NEWS", "BUY", "sentimen positif"),
            self._opini("FUNDAMENTAL", "BUY", "rasio membaik"),
        )

        assert p is None

    def test_yang_melawan_ikut_dicatat(self) -> None:
        """Bukti yang melawan dan harus dicari sendiri sama saja dengan bukti
        yang tidak disebutkan."""
        p = self._susun(
            self._opini("STRUCTURE", "BUY", "higher high dan higher low"),
            self._opini("VOLUME", "BUY", "volume 2.1x rata-rata"),
            self._opini("MOMENTUM", "SELL", "RSI 84 overbought"),
        )

        assert p is not None
        assert [a.text for a in p.against] == ["RSI 84 overbought"]

    def test_agent_yang_menunggu_tidak_masuk_sisi_mana_pun(self) -> None:
        p = self._susun(
            self._opini("STRUCTURE", "BUY", "higher high dan higher low"),
            self._opini("VOLUME", "BUY", "volume 2.1x rata-rata"),
            self._opini("RISK", "WAIT", "risiko MODERATE"),
        )

        assert p is not None
        assert all("MODERATE" not in a.text for a in (*p.reasons, *p.against))

    def test_kalimat_terlarang_dilewati_bukan_menjatuhkan_blok(self) -> None:
        """PASAL 51. Satu agent yang menulis "pasti profit" tidak boleh
        menghapus dua alasan yang sungguhan."""
        p = self._susun(
            self._opini("STRUCTURE", "BUY", "pasti profit", "higher high"),
            self._opini("VOLUME", "BUY", "volume 2.1x rata-rata"),
        )

        assert p is not None
        assert all("pasti profit" not in a.text for a in p.reasons)
        assert any("higher high" in a.text for a in p.reasons)

    def test_kalimat_kosong_dilewati(self) -> None:
        p = self._susun(
            self._opini("STRUCTURE", "BUY", "terlihat bagus", "higher high"),
            self._opini("VOLUME", "BUY", "volume 2.1x rata-rata"),
        )

        assert p is not None
        assert all("terlihat bagus" not in a.text for a in p.reasons)

    def test_pesannya_mencetaknya(self) -> None:
        from aruna.futures.notify import _alert

        p = self._susun(
            self._opini("STRUCTURE", "BUY", "higher high dan higher low"),
            self._opini("VOLUME", "BUY", "volume 2.1x rata-rata"),
        )
        teks = _alert(FakePlan(), NOW, note=note(explanation=p))

        assert "KENAPA LONG" in teks
        assert "higher high" in teks

    def test_tanpa_penjelasan_bloknya_tidak_dicetak(self) -> None:
        from aruna.futures.notify import _alert

        teks = _alert(FakePlan(), NOW, note=note())

        assert "KENAPA" not in teks

    def test_strategi_ikut_menumpang_ke_catatan(self) -> None:
        """PASAL 14.37 membandingkan strategi pendapat baru dengan yang lama.
        Tanpa penumpang ini, keduanya selalu kalimat kosong dan perbandingannya
        selalu menjawab "sama" - penjaga yang mati diam-diam, bukan penjaga
        yang melonggar."""
        from aruna.futures.service import attach_explanation

        konteks = SimpleNamespace(
            strategy=SimpleNamespace(
                strategy=SimpleNamespace(code="STR-005")
            )
        )
        hasil = attach_explanation(
            note(), self._verdict(), konteks, FakePlan()
        )

        assert hasil.strategy == "STR-005"

    def test_strategi_yang_ditolak_jadi_kalimat_kosong(self) -> None:
        """Phase 12 memulangkan ``Selection`` dengan ``strategy=None`` ketika
        tidak ada yang terbukti lebih baik dari rata-rata. Itu keadaan wajar,
        dan kalimat kosong adalah laporan yang benar untuknya."""
        from aruna.futures.service import attach_explanation

        konteks = SimpleNamespace(strategy=SimpleNamespace(strategy=None))
        hasil = attach_explanation(
            note(), self._verdict(), konteks, FakePlan()
        )

        assert hasil.strategy == ""

    def test_dipanggil_dari_jalur_hidup(self) -> None:
        from aruna.futures.service import FuturesPlanService

        sumber = inspect.getsource(FuturesPlanService._plan_one)

        assert "attach_explanation(note, verdict, context, plan)" in sumber

    def test_disusun_sesudah_rencananya_jadi(self) -> None:
        """Penjelasan untuk arah yang belum diputuskan adalah penjelasan atas
        keputusan yang belum ada."""
        from aruna.futures.service import FuturesPlanService

        sumber = inspect.getsource(FuturesPlanService._plan_one)

        assert sumber.index("observe_decision(") < sumber.index(
            "attach_explanation("
        )


class TestKonsistensiDanUmur:
    """PASAL 14.35-14.37 dan 14.23, di jalur kirim yang sungguhan."""

    class _Sender:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, teks: str) -> bool:
            self.sent.append(teks)
            return True

    def _notifier(self, sender, *, hours: float = 0.25):
        from aruna.futures.notify import PlanNotifier

        return PlanNotifier(sender=sender, horizon_hours=hours)

    def _plan(self, **kw):
        """Rencana bervonis PLAN dengan horizon 15 menit yang dikenal.

        ``FakePlan`` bawaan tidak punya ``verdict``, dan ``announce`` hanya
        melewatkan ``PlanVerdict.PLAN`` - tanpa ini seluruh test di kelas ini
        hijau atas nol pengiriman.
        """
        from dataclasses import replace as _replace

        from aruna.futures.plan import PlanVerdict

        dasar = _replace(FakePlan(), **kw)
        return SimpleNamespace(
            **{
                f.name: getattr(dasar, f.name)
                for f in dasar.__dataclass_fields__.values()
            },
            verdict=PlanVerdict.PLAN,
        )

    def _note(self, **kw):
        return note(**kw)

    @pytest.mark.asyncio
    async def test_pengulangan_persis_ditahan(self) -> None:
        """PASAL 14.37: LONG LONG LONG untuk setup yang sama, sementara yang
        pertama masih berlaku."""
        sender = self._Sender()
        n = self._notifier(sender, hours=0.0)

        await n.announce([self._plan()], now=NOW,
                         notes={"BTCUSDT": self._note()})
        await n.announce([self._plan()], now=NOW + timedelta(minutes=1),
                         notes={"BTCUSDT": self._note()})

        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_horizon_habis_boleh_bicara_lagi(self) -> None:
        """PASAL 14.23: signal yang sudah EXPIRED tidak membungkam apa pun -
        pendapat berikutnya bukan pengulangan, ia pendapat berikutnya."""
        sender = self._Sender()
        n = self._notifier(sender, hours=0.0)

        await n.announce([self._plan()], now=NOW,
                         notes={"BTCUSDT": self._note()})
        await n.announce([self._plan()], now=NOW + timedelta(minutes=16),
                         notes={"BTCUSDT": self._note()})

        assert len(sender.sent) == 2

    @pytest.mark.asyncio
    async def test_horizon_belum_habis_masih_menahan(self) -> None:
        """Kebalikan test di atas. Tanpa pasangan ini, masa berlaku yang
        dipendekkan jadi nol akan lolos - dan penjaganya tidak menjaga apa
        pun."""
        sender = self._Sender()
        n = self._notifier(sender, hours=0.0)

        await n.announce([self._plan()], now=NOW,
                         notes={"BTCUSDT": self._note()})
        await n.announce([self._plan()], now=NOW + timedelta(minutes=14),
                         notes={"BTCUSDT": self._note()})

        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_arah_berubah_tetap_dikirim(self) -> None:
        """Operator yang memegang LONG sementara ARUNA diam-diam berpikir SHORT
        berada dalam keadaan jauh lebih berbahaya daripada yang menerima satu
        pesan pembalikan."""
        from aruna.decision.explanation import Alasan, Penjelasan, Sumber
        from aruna.decision.score import Arah

        sender = self._Sender()
        n = self._notifier(sender, hours=0.0)
        penjelasan = Penjelasan(
            decision=Arah.SHORT,
            reasons=(
                Alasan(Sumber.STRUKTUR, "lower high pertama sejak dua hari"),
                Alasan(Sumber.VOLUME, "volume jual 2.4x rata-rata"),
            ),
        )

        await n.announce([self._plan()], now=NOW,
                         notes={"BTCUSDT": self._note()})
        await n.announce(
            [self._plan(side=FakeSide("SHORT"))],
            now=NOW + timedelta(minutes=1),
            notes={"BTCUSDT": self._note(explanation=penjelasan)},
        )

        assert len(sender.sent) == 2
        assert "PEMBALIKAN ARAH" in sender.sent[1]
        assert "tidak disunting" in sender.sent[1]

    @pytest.mark.asyncio
    async def test_pembalikan_tanpa_bukti_tetap_dikirim(self) -> None:
        """Tanpa bukti baru, PASAL 14.36 tidak bisa dipenuhi - dan pesannya
        berangkat tanpa blok pembalikan, bukan tidak berangkat.

        **Nama peristiwanya ikut diperiksa**, dan itu bukan kerewelan: ada dua
        penjaga bertumpuk di jalur ini, dan keduanya menghasilkan "pesan tetap
        terkirim". Yang membedakan hanya apa yang tercatat -
        ``pembalikan_tanpa_bukti`` menunjuk sebab yang persis, sementara
        ``konsistensi_ditolak`` cuma bilang ada yang salah. Tanpa memeriksa
        namanya, penjaga yang lebih tepat bisa dicabut tanpa satu test pun
        merah.
        """
        from aruna.futures import notify as modul

        dicatat: list[str] = []
        sender = self._Sender()
        n = self._notifier(sender, hours=0.0)

        await n.announce([self._plan()], now=NOW,
                         notes={"BTCUSDT": self._note()})

        asli = modul.log
        modul.log = SimpleNamespace(
            info=lambda e, **k: None,
            warning=lambda e, **k: dicatat.append(e),
            exception=lambda e, **k: dicatat.append(f"!{e}"),
        )
        try:
            await n.announce(
                [self._plan(side=FakeSide("SHORT"))],
                now=NOW + timedelta(minutes=1),
                notes={"BTCUSDT": self._note()},
            )
        finally:
            modul.log = asli

        assert len(sender.sent) == 2
        assert "PEMBALIKAN ARAH" not in sender.sent[1]
        assert "futures.pembalikan_tanpa_bukti" in dicatat

    @pytest.mark.asyncio
    async def test_horizon_tak_dikenal_tidak_membungkam(self) -> None:
        """``Umur`` benar menolak menebak masa berlaku horizon asing. Memakai
        ketidaktahuan itu sebagai alasan diam akan membungkam simbol itu
        selamanya."""
        from types import SimpleNamespace

        from aruna.futures import notify as modul

        dicatat: list[str] = []
        sender = self._Sender()
        n = self._notifier(sender, hours=0.0)

        await n.announce([self._plan(horizon_hours=None)], now=NOW,
                         notes={"BTCUSDT": self._note()})

        asli = modul.log
        modul.log = SimpleNamespace(
            info=lambda e, **k: None,
            warning=lambda e, **k: dicatat.append(e),
            exception=lambda e, **k: dicatat.append(f"!{e}"),
        )
        try:
            await n.announce([self._plan(horizon_hours=None)],
                             now=NOW + timedelta(days=3),
                             notes={"BTCUSDT": self._note()})
        finally:
            modul.log = asli

        assert len(sender.sent) == 2
        assert "futures.horizon_tak_dikenal" in dicatat

    @pytest.mark.asyncio
    async def test_simbol_lain_tidak_saling_membungkam(self) -> None:
        """Mencampur dua aset di sini akan membuat BTC membungkam ETH."""
        sender = self._Sender()
        n = self._notifier(sender, hours=0.0)

        await n.announce([self._plan()], now=NOW,
                         notes={"BTCUSDT": self._note()})
        await n.announce([self._plan(symbol="ETHUSDT")],
                         now=NOW + timedelta(minutes=1),
                         notes={"ETHUSDT": self._note()})

        assert len(sender.sent) == 2

    @pytest.mark.asyncio
    async def test_yang_ditahan_tidak_dihitung_terkirim(self) -> None:
        """``announce`` memulangkan jumlah yang benar-benar berangkat. Angka
        yang menghitung yang ditahan akan membuat penghitung notifikasi di log
        berbohong."""
        sender = self._Sender()
        n = self._notifier(sender, hours=0.0)

        await n.announce([self._plan()], now=NOW,
                         notes={"BTCUSDT": self._note()})
        jumlah = await n.announce([self._plan()], now=NOW + timedelta(minutes=1),
                                  notes={"BTCUSDT": self._note()})

        assert jumlah == 0

    def test_notify_mengimpor_consistency_dan_lifecycle(self) -> None:
        from aruna.futures import notify

        sumber = inspect.getsource(notify)

        assert "from aruna.decision.consistency import" in sumber
        assert "from aruna.decision.lifecycle import" in sumber


class TestSignalQualityDihitungUntukFutures:
    """PASAL 14.39 minta signal quality, dan futures **punya** bahannya.

    Sepanjang sesi ini ia dilaporkan hilang, dan aku sempat menyebutnya "tidak
    berlaku untuk futures" - kesimpulan yang salah. ``score_signal`` menerima
    konteks, opini agent, entry, stop, target, dan horizon; jalur futures
    memegang kelimanya di tempat yang sama. Yang tidak ada bukan datanya,
    melainkan pemanggilnya.

    Membiarkannya "tidak berlaku" akan menutup pertanyaannya untuk selamanya -
    dan itu bentuk paling halus dari menyembunyikan lapisan yang tidak
    dirangkai.
    """

    def _konteks(self):
        """Bentuknya meniru ``DecisionContext`` yang sungguhan.

        ``value()`` dan ``reading()`` keduanya ada di sana dan keduanya dipakai
        ``score_signal``. Palsu yang cuma punya salah satunya meledak di dalam
        penilai, tertangkap penjaga luar, dan memulangkan ``None`` - mutunya
        hilang tanpa satu pun test merah.
        """
        from datetime import datetime as _dt

        return SimpleNamespace(
            state=SimpleNamespace(
                data_quality="OK", is_realtime=True, declared_delay_sec=0,
                spread_bps=None, bid_depth=None, ask_depth=None,
            ),
            as_of=_dt(2026, 8, 20, 11, 55, tzinfo=NOW.tzinfo),
            structure=None,
            reading=lambda nama: None,
            value=lambda nama: None,
        )

    def test_nilainya_terhitung(self) -> None:
        from aruna.futures.service import _mutu_signal

        mutu = _mutu_signal(
            context=self._konteks(), verdict=None, plan=FakePlan(), now=NOW
        )

        assert mutu is not None
        assert 0 <= mutu <= 100

    def test_konteks_kosong_tidak_mengarang_nilai(self, monkeypatch) -> None:
        """§13.26: mutu yang dihitung dari ketiadaan bukti adalah angka yang
        dikarang, dan ia akan tercetak seolah-olah ARUNA mengukurnya.

        **Nama peristiwanya ikut diperiksa.** Penjaga luar juga memulangkan
        ``None`` untuk konteks kosong - lewat ``AttributeError`` yang
        tertangkap - jadi memeriksa nilainya saja tidak membedakan keduanya.
        Yang membedakan: konteks kosong adalah keadaan **wajar**, dan
        mencatatnya sebagai jejak pengecualian mengisi log dengan alarm palsu.
        """
        from aruna.futures import service as modul

        dicatat: list[str] = []
        monkeypatch.setattr(
            modul, "log",
            SimpleNamespace(
                info=lambda n, **k: None,
                warning=lambda n, **k: dicatat.append(n),
                exception=lambda n, **k: dicatat.append(f"!{n}"),
            ),
        )

        assert modul._mutu_signal(
            context=None, verdict=None, plan=FakePlan(), now=NOW
        ) is None
        assert dicatat == []

    def test_kegagalannya_tidak_menjatuhkan_rencana(self) -> None:
        from aruna.futures.service import _mutu_signal

        class Meledak:
            @property
            def state(self):
                raise RuntimeError("konteks rusak")

        assert _mutu_signal(
            context=Meledak(), verdict=None, plan=FakePlan(), now=NOW
        ) is None

    def test_menempel_ke_catatan_council(self) -> None:
        from aruna.futures.service import attach_quality

        hasil = attach_quality(
            note(), context=self._konteks(), verdict=None,
            plan=FakePlan(), now=NOW,
        )

        assert hasil.quality is not None

    def test_dipanggil_dari_jalur_hidup(self) -> None:
        from aruna.futures.service import FuturesPlanService

        sumber = inspect.getsource(FuturesPlanService._plan_one)

        assert "attach_quality(" in sumber

    def test_dihitung_sebelum_pembacanya_berjalan(self) -> None:
        """Terukur di produksi 2026-08-21: mutunya dihitung dan **tetap**
        dilaporkan hilang, dan jejak PASAL 14.30 menulis ``UNKNOWN``.

        Sebabnya urutan. ``observe_decision`` dan ``catat_jejak`` keduanya
        membaca ``note.quality``, dan keduanya berjalan sebelum
        ``attach_quality`` menempelkannya. Kode yang benar, dipanggil dari jalur
        hidup, dan hasilnya tetap nol - keluarga cacat yang sama dengan delapan
        modul yang diam, hanya bergeser satu langkah: bukan "tidak dipanggil",
        melainkan "dipanggil terlambat".
        """
        from aruna.futures.service import FuturesPlanService

        sumber = inspect.getsource(FuturesPlanService._plan_one)
        mutu = sumber.index("attach_quality(")

        assert mutu < sumber.index("observe_decision(")
        assert mutu < sumber.index("catat_jejak(")

    def test_pengukurannya_membacanya(self, monkeypatch) -> None:
        """Tanpa ini, mutunya dihitung dan tetap dilaporkan hilang."""
        from aruna.decision.integration import Masukan
        from aruna.futures import service as modul

        keluar: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            modul, "log",
            SimpleNamespace(
                info=lambda n, **k: keluar.append((n, k)),
                warning=lambda n, **k: None,
                exception=lambda n, **k: keluar.append((f"!{n}", k)),
            ),
        )
        modul.observe_decision(
            context=None, verdict=None, plan=None,
            note=SimpleNamespace(quality=72.0), symbol="BTCUSDT",
        )

        assert Masukan.SIGNAL_QUALITY.value not in keluar[0][1]["integrasi_hilang"]


class TestKanalDipakai:
    """PASAL 14.38: tujuh jenis pesan yang boleh berangkat tanpa diminta.

    ``allow`` sengaja menerima :class:`Jenis` dan bukan teks bebas - penjaga
    yang menebak jenis pesan dari isinya akan sesekali salah menebak, dan yang
    salah tebak adalah pesan yang paling tidak biasa, yaitu yang paling mungkin
    penting. Nilai penyambungannya karena itu ada pada **penyebutan jenisnya**
    di tiap jalur kirim: sebuah jalur baru yang lupa menyebutkannya tidak akan
    lolos.
    """

    def test_result_mengimpor_channel(self) -> None:
        from aruna.notify import result

        assert "from aruna.decision.channel import" in inspect.getsource(result)

    def test_tujuh_jenis_dan_tidak_lebih(self) -> None:
        from aruna.decision.channel import Jenis

        assert len(Jenis) == 7

    def test_teks_bebas_ditolak(self) -> None:
        """Sebuah jalur kirim yang menyebut jenisnya sebagai kalimat akan lolos
        diam-diam kalau penjaganya menerima teks."""
        import pytest

        from aruna.decision.channel import ChannelError, allow

        with pytest.raises(ChannelError):
            allow("FINAL SIGNAL")

    def test_jenis_yang_sah_diteruskan(self) -> None:
        from aruna.decision.channel import Jenis, allow

        for jenis in (Jenis.SIGNAL, Jenis.WIN, Jenis.LOSS):
            assert allow(jenis) is jenis

    def test_signal_menyebut_jenisnya(self) -> None:
        from aruna.notify.result import SignalNotifier

        sumber = inspect.getsource(SignalNotifier._kirim)

        assert "allow(Jenis.SIGNAL)" in sumber

    def test_hasil_menyebut_menang_atau_kalah(self) -> None:
        """WIN dan LOSS adalah dua jenis terpisah di PASAL 14.38, dan §11.21
        melarang menyembunyikan LOSS - menyatukan keduanya di bawah satu jenis
        akan membuat pembungkaman salah satunya tidak terlihat."""
        from aruna.notify.result import ResultNotifier

        sumber = inspect.getsource(ResultNotifier._kirim_hasil)

        assert "Jenis.WIN" in sumber
        assert "Jenis.LOSS" in sumber
