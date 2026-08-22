"""Penyimpanan skenario, berbatas dengan sengaja (bagian 16.14, 16.15).

Berkas ini ditulis di bawah bayangan satu angka: 216 MB - kolom
`market_snapshots.raw` pada audit Phase 15.1, 62% basis data, nol pembaca. Ia
tumbuh sebesar itu karena tiap amatan ditulis apa adanya.

Yang dijaga di sini karena itu bukan "apakah barisnya masuk" melainkan **apa
yang tidak masuk**: batas per simulasi, tidak adanya salinan masukan, dan
adanya aturan retensi. Ketiganya adalah hal yang tidak akan ketahuan salah
sampai basis datanya sudah terlanjur besar.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aruna.db.repositories.scenario import (
    BATAS_PER_SIMULASI,
    LEBAR_PEMICU,
    ScenarioRepository,
)
from aruna.scenario.mesin import simulasikan
from aruna.scenario.models import HasilSkenario, Invalidasi, Skenario
from aruna.scenario.pemicu import Peristiwa
from aruna.upkeep.retensi import DILINDUNGI, RENCANA

NOW = datetime(2026, 8, 22, 10, 30, tzinfo=UTC)


class _DbPalsu:
    """Mencatat SQL dan argumennya. Bidangnya sama dengan `Database` yang
    dipakai repositori - `execute` memulangkan int, `fetch` memulangkan list -
    supaya test double ini tidak berbentuk lebih longgar dari objek aslinya."""

    def __init__(self, hasil_execute: int = 1) -> None:
        self.sql: list[tuple[str, tuple]] = []
        self._hasil = hasil_execute
        self.baris: list[dict] = []

    async def execute(self, sql: str, *args) -> int:
        self.sql.append((sql, args))
        return self._hasil

    async def fetch(self, sql: str, *args) -> list[dict]:
        self.sql.append((sql, args))
        return self.baris


def _skenario(nama: str, bobot: int) -> Skenario:
    return Skenario(
        scenario_id=f"BTC/USDT-x-{nama}",
        market="CRYPTO",
        asset="BTC/USDT",
        timestamp=NOW,
        nama=nama,
        deskripsi="uji",
        kondisi_awal=("k1", "k2"),
        pemicu="BREAKOUT_BESAR",
        perkembangan=("p1", "p2", "p3"),
        invalidasi=Invalidasi(syarat=("s1", "s2")),
        risiko="MEDIUM",
        keyakinan=bobot / 100,
        bobot=bobot,
        bukti=("b1",),
        versi_simulasi="internal-1",
    )


class TestBatasPenyimpanan:
    """Bagian 16.14."""

    @pytest.mark.asyncio
    async def test_di_bawah_batas_semua_masuk(self) -> None:
        db = _DbPalsu()
        n = await ScenarioRepository(db).simpan(
            tuple(_skenario(f"s{i}", 10) for i in range(3))
        )

        assert n == 3

    @pytest.mark.asyncio
    async def test_di_atas_batas_dipotong(self) -> None:
        db = _DbPalsu()
        banyak = tuple(
            _skenario(f"s{i:02d}", 100 - i) for i in range(BATAS_PER_SIMULASI + 5)
        )

        n = await ScenarioRepository(db).simpan(banyak)

        assert n == BATAS_PER_SIMULASI

    @pytest.mark.asyncio
    async def test_yang_dipotong_yang_berbobot_terendah(self) -> None:
        """Bukan yang datang terakhir: urutan datang tidak menyatakan apa pun
        tentang mana yang paling sedikit menarik perhatian."""
        db = _DbPalsu()
        banyak = (
            _skenario("kecil", 1),
            *(_skenario(f"s{i:02d}", 90 - i) for i in range(BATAS_PER_SIMULASI)),
        )

        await ScenarioRepository(db).simpan(banyak)
        tersimpan = {args[4] for _, args in db.sql}

        assert "kecil" not in tersimpan

    @pytest.mark.asyncio
    async def test_pemotongannya_deterministik(self) -> None:
        """Seri bobot dipecah dengan nama. Pemotongan yang berubah-ubah
        membuat dua jalannya menyimpan himpunan berbeda dari masukan sama."""
        banyak = tuple(_skenario(f"s{i:02d}", 50) for i in range(BATAS_PER_SIMULASI + 3))

        db1, db2 = _DbPalsu(), _DbPalsu()
        await ScenarioRepository(db1).simpan(banyak)
        await ScenarioRepository(db2).simpan(tuple(reversed(banyak)))

        assert {a[4] for _, a in db1.sql} == {a[4] for _, a in db2.sql}

    @pytest.mark.asyncio
    async def test_kosong_tidak_menyentuh_database(self) -> None:
        db = _DbPalsu()

        assert await ScenarioRepository(db).simpan(()) == 0
        assert db.sql == []

    @pytest.mark.asyncio
    async def test_memulangkan_yang_benar_benar_masuk(self) -> None:
        """`INSERT IGNORE` yang menabrak baris lama memulangkan nol. Pemanggil
        yang mengira semuanya masuk akan salah menghitung."""
        db = _DbPalsu(hasil_execute=0)

        assert await ScenarioRepository(db).simpan((_skenario("a", 50),)) == 0


class TestTidakMenyalinMasukan:
    """Pelajaran Phase 15.1: yang tumbuh adalah yang ditulis apa adanya."""

    @pytest.mark.asyncio
    async def test_sql_tidak_menyebut_kolom_masukan(self) -> None:
        db = _DbPalsu()
        await ScenarioRepository(db).simpan((_skenario("a", 50),))
        sql = db.sql[0][0].lower()

        for terlarang in ("raw", "masukan", "input", "payload", "candles", "snapshot"):
            assert terlarang not in sql, terlarang

    @pytest.mark.asyncio
    async def test_sebelas_bidang_16_15_semuanya_tersimpan(self) -> None:
        """Cacat yang berulang di proyek ini: nilai dihitung, diekspor ke
        `to_dict`, dan tidak pernah sampai ke INSERT-nya."""
        db = _DbPalsu()
        await ScenarioRepository(db).simpan((_skenario("a", 50),))
        sql = db.sql[0][0].lower()

        for kolom in (
            "scenario_id", "market_code", "asset", "dibuat_pada", "nama",
            "bobot", "pemicu", "invalidasi", "risiko", "bukti",
            "versi_simulasi",
        ):
            assert kolom in sql, kolom

    @pytest.mark.asyncio
    async def test_jumlah_placeholder_sama_dengan_jumlah_kolom(self) -> None:
        """Penjaga yang lahir dari bug nyata di Phase 15: kolom ditambahkan ke
        daftar tanpa `%s` yang menyertainya, dan yang ketahuan cuma saat
        produksi menulis baris pertamanya."""
        db = _DbPalsu()
        await ScenarioRepository(db).simpan((_skenario("a", 50),))
        sql, args = db.sql[0]

        daftar = sql[sql.index("(") + 1 : sql.index(")")]
        kolom = [k.strip() for k in daftar.split(",")]

        assert len(kolom) == sql.count("%s") == len(args)


class TestKerapuhanDanRantaiIkutTersimpan:
    @pytest.mark.asyncio
    async def test_kerapuhan_tersimpan(self) -> None:
        """Dihitung tapi tidak dikeluarkan sama saja dengan tidak dihitung."""
        db = _DbPalsu()
        await ScenarioRepository(db).simpan((_skenario("a", 50),))

        assert "kerapuhan" in db.sql[0][0]

    @pytest.mark.asyncio
    async def test_perkembangan_tersimpan_berurutan(self) -> None:
        """Bagian 16.8: rantainya punya urutan, dan JSON list menjaganya -
        himpunan atau teks gabungan akan membuangnya."""
        db = _DbPalsu()
        await ScenarioRepository(db).simpan((_skenario("a", 50),))
        args = db.sql[0][1]

        assert '["p1", "p2", "p3"]' in args


class TestPenilaianBelakangan:
    """Bagian 16.19."""

    @pytest.mark.asyncio
    async def test_belum_ditulis_ditolak(self) -> None:
        """`BELUM` berarti horizonnya belum lewat. Menuliskannya mengeluarkan
        baris itu dari antrean penilaian selamanya - yang belum bisa dinilai
        berubah jadi yang tidak akan pernah dinilai."""
        db = _DbPalsu()

        assert not await ScenarioRepository(db).catat_hasil(
            "s-1", HasilSkenario.BELUM, pada=NOW
        )
        assert db.sql == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "hasil",
        [HasilSkenario.BENAR, HasilSkenario.SALAH, HasilSkenario.SEBAGIAN],
    )
    async def test_hasil_final_ditulis(self, hasil) -> None:
        db = _DbPalsu()

        assert await ScenarioRepository(db).catat_hasil("s-1", hasil, pada=NOW)
        assert hasil.value in db.sql[0][1]

    @pytest.mark.asyncio
    async def test_hanya_menimpa_yang_masih_null(self) -> None:
        """Penilaian yang menimpa penilaian lama membuat akurasi berubah tanpa
        ada yang tahu baris mana yang berpindah."""
        db = _DbPalsu()
        await ScenarioRepository(db).catat_hasil("s-1", HasilSkenario.BENAR, pada=NOW)

        assert "hasil IS NULL" in db.sql[0][0]

    @pytest.mark.asyncio
    async def test_waktunya_sadar_zona(self) -> None:
        """**Bug produksi, 2026-08-22.** MySQL memulangkan DATETIME telanjang,
        dan yang membacanya membandingkannya dengan `close_time` candle yang
        sudah sadar-zona. Empat puluh dari empat puluh penilaian gagal dengan
        ``can't compare offset-naive and offset-aware datetimes``.

        Dua puluh tujuh test repositori lolos di atas bug ini karena double-nya
        memulangkan daftar kosong - bentuk yang tidak pernah bisa
        memperlihatkannya.
        """
        db = _DbPalsu()
        db.baris = [{
            "scenario_id": "s-1",
            "market_code": "CRYPTO",
            "asset": "BTC/USDT",
            # Tanpa tzinfo, persis bentuk yang MySQL pulangkan.
            "dibuat_pada": datetime(2026, 8, 22, 10, 0),
            "nama": "Sideways",
            "bobot": 25,
            "invalidasi": "[]",
            "versi_simulasi": "internal-2",
            "sumber": "INTERNAL",
        }]

        keluar = await ScenarioRepository(db).belum_dinilai(sampai=NOW)

        assert keluar[0]["dibuat_pada"].tzinfo is not None

    @pytest.mark.asyncio
    async def test_antrean_penilaian_memakai_is_null(self) -> None:
        """`hasil IS NULL` dan bukan `hasil = 'BELUM'`: nilai apa pun berarti
        evaluasi sudah menyentuhnya."""
        db = _DbPalsu()
        await ScenarioRepository(db).belum_dinilai(sampai=NOW)

        assert "hasil IS NULL" in db.sql[0][0]

    @pytest.mark.asyncio
    async def test_akurasi_dipisah_per_versi_dan_sumber(self) -> None:
        """Hasil dua mesin yang dijumlah jadi satu angka tidak mengatakan apa
        pun tentang keduanya."""
        db = _DbPalsu()
        await ScenarioRepository(db).ringkas_akurasi()
        sql = db.sql[0][0]

        assert "versi_simulasi" in sql
        assert "sumber" in sql


class TestLebarKolom:
    """Terukur 2026-08-22 lewat tulisan sungguhan: tiga belas pemicu yang
    menyala bersamaan menghasilkan 245 karakter di kolom yang dulu VARCHAR(255).

    Kelas ini menghitung kasus terburuknya **dari enum-nya sendiri**, jadi
    pemicu keempat belas gagal di sini alih-alih terpotong diam-diam di
    produksi - dan pemotongan diam-diam adalah kerusakan yang paling sulit
    ditemukan, karena barisnya tetap tersimpan dan tetap terbaca rapi.
    """

    def test_seluruh_pemicu_muat_di_kolomnya(self) -> None:
        terburuk = ", ".join(sorted(p.value for p in Peristiwa))

        assert len(terburuk) <= LEBAR_PEMICU, (
            f"{len(terburuk)} karakter melewati {LEBAR_PEMICU}. Perlebar "
            f"kolomnya lewat migrasi baru - jangan potong nilainya."
        )

    def test_margin_kolomnya_lebih_dari_sekadar_muat(self) -> None:
        """Sepuluh karakter sisa bukan margin. Kalau margin ini menipis lagi,
        yang dibutuhkan migrasi, bukan pemotongan."""
        terburuk = ", ".join(sorted(p.value for p in Peristiwa))

        assert LEBAR_PEMICU - len(terburuk) >= 100

    @pytest.mark.asyncio
    async def test_pemotongan_berteriak_bukan_diam(self) -> None:
        """Kalau suatu saat pemotongan benar-benar terjadi, ia harus terlihat."""
        from aruna.db.repositories import scenario as modul

        dicatat = []

        class _LogPalsu:
            def warning(self, event, **kw):
                dicatat.append((event, kw))

        asli, modul.log = modul.log, _LogPalsu()
        try:
            s = _skenario("a", 50)
            panjang = type(s)(**{
                **{f: getattr(s, f) for f in s.__slots__},
                "pemicu": "X" * (LEBAR_PEMICU + 50),
            })
            await ScenarioRepository(_DbPalsu()).simpan((panjang,))
        finally:
            modul.log = asli

        assert dicatat, "pemotongan tidak menghasilkan satu pun peringatan"
        assert dicatat[0][0] == "scenario.nilai_dipotong"
        assert dicatat[0][1]["kolom"] == "pemicu"

    @pytest.mark.asyncio
    async def test_nilai_yang_muat_tidak_berteriak(self) -> None:
        """Peringatan yang menyala pada keadaan normal berhenti dibaca."""
        from aruna.db.repositories import scenario as modul

        dicatat = []

        class _LogPalsu:
            def warning(self, event, **kw):
                dicatat.append(event)

        asli, modul.log = modul.log, _LogPalsu()
        try:
            await ScenarioRepository(_DbPalsu()).simpan((_skenario("a", 50),))
        finally:
            modul.log = asli

        assert dicatat == []


class TestRetensi:
    def test_ada_aturan_untuk_skenario(self) -> None:
        """Tabel tanpa aturan retensi tumbuh selamanya. Itu persis bagaimana
        `market_snapshots` menjadi 62% basis data."""
        assert any(r.tabel == "scenario_evidence" for r in RENCANA)

    def test_bukan_tabel_terlindung(self) -> None:
        """Bagian 31 melindungi keputusan dan buktinya; skenario bukan
        keputusan (bagian 16.18)."""
        assert "scenario_evidence" not in DILINDUNGI

    def test_batasnya_per_batch_bukan_sekaligus(self) -> None:
        """Bagian 26 Phase 15.1: penghapusan sekaligus mengunci tabel."""
        aturan = next(r for r in RENCANA if r.tabel == "scenario_evidence")

        assert 0 < aturan.batas_batch <= 1000

    def test_umurnya_melampaui_horizon_terpanjang(self) -> None:
        """Evaluasi bagian 16.19 harus sempat membandingkan skenario dengan
        hasil pasar; horizon terpanjang ARUNA harian."""
        aturan = next(r for r in RENCANA if r.tabel == "scenario_evidence")

        assert aturan.hari >= 30


class TestBatasnyaTidakMemotongMesinSekarang:
    @pytest.mark.asyncio
    async def test_seluruh_kosakata_mesin_internal_muat(self) -> None:
        """Bagian 16.5 menyebut tiga wajib dan lima opsional. Batas yang
        memotong keluaran mesin sendiri berarti ARUNA membuang skenario yang
        baru saja diputuskannya layak dibuat."""
        db = _DbPalsu()
        semua = simulasikan(
            market="CRYPTO",
            asset="BTC/USDT",
            pemicu=frozenset(Peristiwa),
            kondisi_awal=("k",),
            bukti=("b",),
            pada=NOW,
        )

        n = await ScenarioRepository(db).simpan(semua)

        assert len(semua) <= BATAS_PER_SIMULASI
        assert n == len(semua)
