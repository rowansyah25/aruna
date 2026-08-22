"""Gerbang perubahan bisa diperiksa dari log produksi.

Pencacah `dilewati` dan `sebab_simpan` dibangun justru supaya gerbangnya bisa
diperiksa saat berjalan - dan terukur 2026-08-21 sesudah restart pertama,
keduanya mendarat di `log.debug("ingest.pass", ...)` sementara produksi punya
**nol baris DEBUG**. Pencacah yang tidak terbaca sama saja dengan tidak ada.

Menaikkan baris itu ke INFO bukan jawabannya: ia berbunyi tiap lima detik per
pasar, yang persis kebisingan yang membuatnya diturunkan ke DEBUG. Yang
dikumpulkan di sini adalah ringkasan berkala - satu baris per beberapa menit,
membawa jumlah kumulatifnya.
"""

from __future__ import annotations

from aruna.core.enums import Market
from aruna.data.ingest import JEDA_RINGKASAN_DETIK, IngestResult, RingkasanGerbang


def _hasil(*, simpan: int, lewat: int, sebab: dict[str, int] | None = None):
    r = IngestResult(market=Market.CRYPTO, provider="uji")
    r.snapshots = simpan
    r.dilewati = lewat
    r.sebab_simpan = dict(sebab or {})
    return r


class TestMengumpulkan:
    def test_menjumlahkan_lintas_lintasan(self) -> None:
        r = RingkasanGerbang()
        r.tambah(_hasil(simpan=2, lewat=18))
        r.tambah(_hasil(simpan=1, lewat=19))

        assert r.disimpan == 3
        assert r.dilewati == 37

    def test_sebab_dijumlahkan_per_jenis(self) -> None:
        """Sebab yang dilebur jadi satu angka tidak bisa menjawab apakah baris
        yang tersimpan lahir dari pasar yang bergerak atau dari detak wajib -
        dan itu beda antara gerbang yang bekerja dan gerbang yang cuma
        menunda."""
        r = RingkasanGerbang()
        r.tambah(_hasil(simpan=1, lewat=0, sebab={"HARGA": 1}))
        r.tambah(_hasil(simpan=2, lewat=0, sebab={"HARGA": 1, "WAKTU": 1}))

        assert r.sebab == {"HARGA": 2, "WAKTU": 1}


#: Nilai `monotonic()` yang masuk akal di mesin yang sudah hidup berhari-hari.
#:
#: Angkanya besar dengan sengaja. Versi pertama modul ini menjangkarkan jamnya
#: di nol, lolos seluruh test yang memakai angka kecil, dan melaporkan seketika
#: saat start di produksi - karena `monotonic()` adalah uptime mesin.
UPTIME = 987_654.0


def _siap(r: RingkasanGerbang, *, mulai: float = UPTIME) -> RingkasanGerbang:
    """Pasang jangkar jamnya, seperti lintasan poll pertama di produksi."""
    r.ambil(sekarang=mulai)
    return r


class TestJangkarJam:
    def test_panggilan_pertama_tidak_melaporkan(self) -> None:
        """Panggilan pertama memasang jangkarnya. Dua puluh baris `PERTAMA` dan
        `pct_dilewati=0,0` di detik pertama terbaca persis seperti gerbang yang
        tidak menahan apa-apa."""
        r = RingkasanGerbang()
        r.tambah(_hasil(simpan=20, lewat=0, sebab={"PERTAMA": 20}))

        assert r.ambil(sekarang=UPTIME) is None

    def test_jam_besar_tetap_menunggu_jedanya(self) -> None:
        """Yang membedakan test ini dari yang lain adalah besarnya angka - dan
        itulah satu-satunya yang membuatnya menangkap cacatnya."""
        r = _siap(RingkasanGerbang())

        assert r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK - 1.0) is None
        assert r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK) is not None

    def test_yang_terkumpul_sebelum_jangkar_tidak_hilang(self) -> None:
        """Lintasan pertama tetap dihitung; yang ditunda hanya laporannya."""
        r = RingkasanGerbang()
        r.tambah(_hasil(simpan=20, lewat=0, sebab={"PERTAMA": 20}))
        r.ambil(sekarang=UPTIME)
        r.tambah(_hasil(simpan=1, lewat=19, sebab={"HARGA": 1}))

        muatan = r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK)

        assert muatan["disimpan"] == 21
        assert muatan["sebab"] == {"PERTAMA": 20, "HARGA": 1}


class TestCadence:
    def test_belum_jatuh_tempo_tidak_melaporkan(self) -> None:
        r = _siap(RingkasanGerbang())
        r.tambah(_hasil(simpan=1, lewat=19))

        assert r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK - 1.0) is None

    def test_jatuh_tempo_melaporkan_lalu_mengosongkan(self) -> None:
        r = _siap(RingkasanGerbang())
        r.tambah(_hasil(simpan=1, lewat=19, sebab={"HARGA": 1}))

        muatan = r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK)

        assert muatan is not None
        assert muatan["disimpan"] == 1
        assert muatan["dilewati"] == 19
        assert muatan["sebab"] == {"HARGA": 1}
        # Dikosongkan, kalau tidak angkanya menumpuk selamanya dan "1.200 baris
        # dilewati" tidak lagi punya rentang waktu yang berarti.
        assert r.disimpan == 0
        assert r.dilewati == 0
        assert r.sebab == {}

    def test_melaporkan_nol_juga(self) -> None:
        """Nol yang tidak dilaporkan tidak bisa dibedakan dari fase yang mati -
        aturan yang sama dengan retensi dan korelasi di loop upkeep."""
        r = _siap(RingkasanGerbang())

        muatan = r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK)

        assert muatan is not None
        assert muatan["disimpan"] == 0
        assert muatan["dilewati"] == 0

    def test_menghitung_dari_laporan_terakhir_bukan_dari_awal(self) -> None:
        """Kalau jamnya dihitung dari awal proses, laporan kedua menyusul satu
        lintasan sesudah yang pertama dan cadence-nya runtuh."""
        r = _siap(RingkasanGerbang())
        r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK)

        assert r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK + 1.0) is None
        assert r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK * 2) is not None

    def test_persen_dilewati_ikut_dilaporkan(self) -> None:
        """Angka yang menjawab pertanyaannya langsung: berapa yang ditahan."""
        r = _siap(RingkasanGerbang())
        r.tambah(_hasil(simpan=1, lewat=19))

        muatan = r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK)

        assert muatan["pct_dilewati"] == 95.0

    def test_persen_tanpa_amatan_tidak_membagi_dengan_nol(self) -> None:
        r = _siap(RingkasanGerbang())

        assert r.ambil(sekarang=UPTIME + JEDA_RINGKASAN_DETIK)["pct_dilewati"] == 0.0


class TestTerpasangDiLoop:
    """Ringkasan yang benar dan tidak dipanggil persis mengulangi kegagalan
    yang membuatnya perlu ditulis."""

    def test_loop_mengumpulkan_dan_melaporkan(self) -> None:
        import ast
        import inspect
        from textwrap import dedent

        from aruna.data.ingest import IngestService

        pohon = ast.parse(dedent(inspect.getsource(IngestService._loop)))
        dipanggil = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

        assert "tambah" in dipanggil
        assert "ambil" in dipanggil

    def test_dilaporkan_di_info_bukan_debug(self) -> None:
        """Seluruh gunanya. Di DEBUG ia tidak akan pernah terbaca: produksi
        terukur punya nol baris DEBUG."""
        import ast
        import inspect
        from textwrap import dedent

        from aruna.data.ingest import IngestService

        pohon = ast.parse(dedent(inspect.getsource(IngestService._loop)))
        tingkat = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and any(
                isinstance(a, ast.Constant) and a.value == "ingest.gerbang"
                for a in n.args
            )
        }

        assert tingkat == {"info"}
