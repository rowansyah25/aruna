"""Phase 17 dan 16 akhirnya masuk ke skor mutu (bagian 18.14, 18.15).

**Celah 1, dan satu-satunya yang mengubah arsitektur.** Diukur 2026-08-24:
`score_signal` menyusun delapan belas faktor, dan tidak satu pun berasal dari
Phase 16 atau Phase 17. Keduanya berjalan sebagai pengamat - menulis
`scenario_evidence` dan `router_pilihan` yang tak seorang pun di jalur
keputusan baca.

Diverifikasi dengan impor: `aruna.router` dan `aruna.scenario` hanya diimpor
oleh dirinya sendiri, repositorinya, dan fase upkeep-nya. Tidak ada satu berkas
pun di `signals/`, `council/`, atau `agents/` yang menyentuhnya.

**Yang dijaga paling keras di sini bedanya "menolak" dari "tidak ditanya".**
Router yang MENOLAK memilih sudah mengukur dan sudah menjawab - itu nilai
rendah yang terukur. Router yang tidak pernah dijalankan belum menjawab apa pun -
itu `None`, dan `Factor` mengeluarkannya dari penyebut. Menyamakan keduanya
menghukum aset yang fasenya kebetulan mati.
"""

from __future__ import annotations

from typing import Any

from aruna.signals.quality import scenario_factor, strategy_factor


class _Pilihan:
    """Bentuknya mengikuti `PutusanRouter`, bukan yang mudah ditulis."""

    def __init__(
        self, kode: str | None, skor: int = 0, *, konsensus: float = 100.0
    ) -> None:
        self.champion = _Champ(kode, skor) if kode else None
        self.konsensus = konsensus
        self.kode_kosong = None if kode else "TAK_ADA_YANG_COCOK"


class _Champ:
    def __init__(self, kode: str, skor: int) -> None:
        self.kode = kode
        self.skor = skor


def _Skenario(
    bobot: int, kokoh: bool, *, nama: str = "", risiko: str = "LOW"
) -> Any:
    """Baris yang **sungguhan** dioper produksi, bukan palsu yang mirip.

    Versi pertama file ini memakai kelas palsu bernama ``_Skenario`` dengan dua
    bidang - ``bobot`` dan ``kerapuhan``. Seluruh testnya hijau. Lalu
    ``scenario_factor`` mulai memanggil
    :func:`~aruna.scenario.banding.bandingkan`, yang membaca ``nama`` dan
    ``risiko`` juga, dan palsunya meledak - bukan karena aturannya salah, tapi
    karena bentuknya tidak pernah sama dengan yang dioper produksi.

    Memakai ``_SkenarioUntukMutu`` yang asli menutup celah itu untuk seterusnya:
    bidang yang hilang di sana akan gagal di sini lebih dulu.
    """
    from aruna.db.repositories.scenario import _SkenarioUntukMutu
    from aruna.scenario.models import Kerapuhan

    return _SkenarioUntukMutu(
        nama=nama or f"S{bobot}",
        bobot=bobot,
        keyakinan=bobot / 100,
        kerapuhan=Kerapuhan.KOKOH if kokoh else Kerapuhan.RAPUH,
        risiko=risiko,
    )


class TestFaktorStrategi:
    """Bagian 18.14."""

    def test_champion_berskor_tinggi_menaikkan_faktor(self) -> None:
        kuat = strategy_factor(_Pilihan("STR-001", 92))
        lemah = strategy_factor(_Pilihan("STR-001", 61))

        assert kuat.score > lemah.score

    def test_router_menolak_adalah_nilai_rendah_TERUKUR(self) -> None:
        """**Bedanya menentukan.** Router yang menolak sudah mengukur dan sudah
        menjawab: rezim ini tidak punya strategi yang cocok. Itu keterangan,
        dan keterangan yang menurunkan mutu keputusan.

        Kalau ia dipulangkan `None`, `Factor` mengeluarkannya dari penyebut dan
        penolakan router berhenti berarti apa-apa.
        """
        tolak = strategy_factor(_Pilihan(None))

        assert tolak.measured
        assert tolak.score is not None
        assert tolak.score < 0.5

    def test_router_tidak_dijalankan_adalah_TIDAK_TERUKUR(self) -> None:
        """Dan ini kebalikannya. Fase router yang mati bukan bukti tentang
        pasar - menghukumnya berarti menghukum aset atas kegagalan kita
        sendiri."""
        diam = strategy_factor(None)

        assert not diam.measured
        assert diam.score is None

    def test_konsensus_rendah_menurunkan_faktor(self) -> None:
        """Bagian 18.14 minta stabilitas strategi ikut dinilai. Dua strategi
        yang sama-sama cocok adalah pilihan yang lebih goyah daripada satu yang
        unggul telak - walau championnya berskor sama."""
        bulat = strategy_factor(_Pilihan("STR-001", 80, konsensus=100.0))
        terbelah = strategy_factor(_Pilihan("STR-001", 80, konsensus=50.0))

        assert bulat.score > terbelah.score

    def test_alasannya_menyebut_kodenya(self) -> None:
        """Faktor yang menurunkan skor tanpa menyebut siapa tidak bisa
        dibantah."""
        f = strategy_factor(_Pilihan("STR-004", 77))

        assert "STR-004" in f.detail

    def test_tidak_pernah_memblokir(self) -> None:
        """Strategi yang tidak cocok bukan alasan menolak sinyal - ia satu
        bukti di antara banyak. Yang memblokir hanya faktor yang kegagalannya
        membuat seluruh penilaian tidak berarti, seperti mutu data."""
        assert not strategy_factor(_Pilihan(None)).blocking


class TestFaktorSkenario:
    """Bagian 18.15."""

    def test_skenario_kokoh_lebih_kuat_daripada_yang_rapuh(self) -> None:
        """`RAPUH` berarti seluruh skenario runtuh oleh satu syarat yang
        hilang (bagian 16.10). Sekumpulan skenario yang seluruhnya rapuh adalah
        bukti yang jauh lebih tipis daripada yang bersyarat banyak."""
        kokoh = scenario_factor([_Skenario(60, True), _Skenario(40, True)])
        rapuh = scenario_factor([_Skenario(60, False), _Skenario(40, False)])

        assert kokoh.score > rapuh.score

    def test_ditimbang_bobotnya_bukan_dihitung_kepalanya(self) -> None:
        """Skenario berbobot 80 yang kokoh dan satu berbobot 5 yang rapuh
        bukan "setengah kokoh" - yang menentukan skenario yang benar-benar
        dipertimbangkan."""
        berat_kokoh = scenario_factor([_Skenario(90, True), _Skenario(10, False)])
        berat_rapuh = scenario_factor([_Skenario(10, True), _Skenario(90, False)])

        assert berat_kokoh.score > berat_rapuh.score

    def test_tanpa_skenario_adalah_TIDAK_TERUKUR(self) -> None:
        """Fase skenario hanya berjalan ketika pemicunya menyala (bagian
        16.2). Aset yang pemicunya diam tidak punya skenario, dan itu bukan
        kelemahan bukti - itu ketiadaan pertanyaan."""
        assert not scenario_factor(None).measured
        assert not scenario_factor([]).measured

    def test_alasannya_menyebut_jumlah_dan_kekokohannya(self) -> None:
        f = scenario_factor([_Skenario(60, True), _Skenario(40, False)])

        assert "2" in f.detail

    def test_tidak_pernah_memblokir(self) -> None:
        assert not scenario_factor([_Skenario(100, False)]).blocking

    def test_konflik_adalah_TIDAK_TERUKUR_bukan_nol(self) -> None:
        """**Yang paling penting di kelas ini.**

        `bandingkan` menyebut selisih di bawah AMBANG_DOMINAN sebagai konflik,
        dan docstring-nya mengeja artinya: mesin skenario tidak sedang menunjuk
        apa pun. Memberinya nol mengubah kelemahan Phase 16 menjadi tuduhan
        terhadap setupnya - skor mutu turun karena mesinnya, bukan pasarnya.

        Terukur 2026-08-24: 1.256 dari 1.569 simulasi seri persis di puncak.
        Nol di sini berarti empat dari lima keputusan dihukum atas cacat yang
        bukan miliknya.
        """
        seri = scenario_factor([_Skenario(50, True), _Skenario(50, True)])

        assert seri.score is None
        assert "konflik" in seri.detail

    def test_tepat_di_ambang_dominan_terukur(self) -> None:
        from aruna.scenario.banding import AMBANG_DOMINAN

        f = scenario_factor([
            _Skenario(50 + AMBANG_DOMINAN, True), _Skenario(50, True)
        ])

        assert f.score is not None

    def test_ambangnya_dipinjam_dari_phase_16(self) -> None:
        """Ambang kedua akan membuat "dominan" punya dua arti - satu di Phase
        16 yang melaporkannya, satu di Phase 18 yang menilainya."""
        import inspect

        from aruna.signals import quality

        assert "bandingkan" in inspect.getsource(quality.scenario_factor)

    def test_bukan_konstanta(self) -> None:
        """Terukur 2026-08-24: `kerapuhan` bernilai KOKOH pada SELURUH 6.561
        baris `scenario_evidence`, karena tiap skenario punya dua atau tiga
        syarat invalidasi - tidak pernah satu. Faktor yang hanya membaca
        kekokohan memulangkan 1.0 setiap kali, bobot dua penuh, tanpa menilai
        apa pun.
        """
        semua_kokoh = [
            scenario_factor([_Skenario(a, True), _Skenario(b, True)]).score
            for a, b in ((50, 50), (70, 30), (95, 5))
        ]

        assert len(set(map(str, semua_kokoh))) > 1


class TestKeduanyaMasukKeSkor:
    def test_score_signal_menyusun_keduanya(self) -> None:
        """**Ini yang membuat celah 1 tertutup.** Faktor yang benar tapi tidak
        pernah disusun ke dalam skor adalah cacat yang sudah enam kali muncul
        di proyek ini.
        """
        import inspect

        from aruna.signals.quality import score_signal

        sumber = inspect.getsource(score_signal)

        assert "strategy_factor" in sumber
        assert "scenario_factor" in sumber

    def test_keduanya_dibaca_dari_konteks(self) -> None:
        """`DecisionContext` menyebut dirinya "kolam bukti yang beku dan
        lengkap untuk satu keputusan". Bukti yang dioper lewat jalur samping
        membuat klaim itu tidak benar, dan membuat replay Phase 9 menilai
        keputusan dengan bahan yang tidak tercatat di konteksnya."""
        import dataclasses

        from aruna.agents.context import DecisionContext

        bidang = {f.name for f in dataclasses.fields(DecisionContext)}

        assert "router" in bidang
        assert "scenario" in bidang


def _tanpa(x: Any) -> Any:
    return x
