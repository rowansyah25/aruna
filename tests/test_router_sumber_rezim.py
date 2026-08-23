"""Dari mana router membaca rezim, dan dalam satuan apa.

**Rencana Phase 17 menyebut sumbernya tabel ``regimes``. Itu salah, dan
diukur 2026-08-23:**

    regimes:             3 baris, semuanya 1d, terakhir 2026-08-14
    technical_snapshots: 3 baris, semuanya 1d, terakhir 2026-08-14

Akarnya bukan tabelnya melainkan pemanggilnya: ``AnalysisService`` - satu-
satunya yang mengisi keduanya - hanya dipanggil dari perintah ``aruna
analyze``, tidak pernah dari :class:`~aruna.upkeep.loop.UpkeepLoop`. Tiga
baris itu sisa satu kali jalan manual sembilan hari sebelumnya.

Yang hidup ``signal_snapshots.regime``, ditulis tiap siklus untuk kedua puluh
aset yang dipindai::

    15m  9.437 baris  20 aset  terakhir 2026-08-23 00:30
    1h   4.057 baris  20 aset  terakhir 2026-08-22 19:00
    1d   2.407 baris  20 aset  terakhir 2026-08-22 00:00

Phase 16 sudah memilih sumber yang sama untuk alasan yang sama; lihat
:mod:`aruna.db.repositories.konteks_pemicu`.

**Konsekuensinya dua, dan keduanya dijaga di berkas ini.**

Pertama, intervalnya cuma tiga. ``BOBOT_INTERVAL`` versi pertama memuat enam -
5m, 30m, dan 4h termasuk - dan ketiganya tidak akan pernah ada, jadi
``interval_hilang`` akan menyebutnya tiap kali seolah ada yang rusak.

Kedua, ``signal_snapshots`` **tidak punya kolom keyakinan rezim sama sekali**.
Kolom ``confidence`` di sana milik SINYAL, bukan classifier - memakainya
sebagai keyakinan rezim persis pelanggaran "ambang dipinjam dari pertanyaan
yang sama" yang sudah tiga kali jadi bug di proyek ini.
"""

from __future__ import annotations

import pytest

from aruna.core.enums import Horizon
from aruna.router.rezim import BOBOT_INTERVAL, BacaanRezim, susun_peta
from aruna.signals.outcome import STORED_INTERVALS


def _b(interval: str, regime: str, persen: float | None = 80.0) -> BacaanRezim:
    return BacaanRezim(interval=interval, regime=regime, keyakinan_persen=persen)


class TestIntervalYangBenarBenarAda:
    def test_bobot_hanya_untuk_interval_yang_disimpan(self) -> None:
        """``STORED_INTERVALS`` sengaja publik supaya tidak ada yang menulis
        daftar kedua yang bebas jatuh keluar barisan - dokumentasinya sendiri
        yang menyatakannya. Daftar bobot yang memuat interval di luarnya akan
        melaporkan ``interval_hilang`` selamanya."""
        tersimpan = {h.value for h in STORED_INTERVALS}

        asing = set(BOBOT_INTERVAL) - tersimpan
        assert not asing, (
            f"bobot menyebut interval yang tidak pernah disimpan: {sorted(asing)}"
        )

    def test_setiap_interval_berrezim_punya_bobot(self) -> None:
        """Kebalikannya juga dijaga. Interval yang punya bacaan rezim tapi
        tidak punya bobot akan diam-diam jatuh ke bobot netral, dan urutan
        "horizon panjang lebih berat" berhenti berlaku tanpa ada yang tahu.

        ``1m`` dikecualikan dan sebabnya diukur: ``signal_snapshots`` tidak
        memuat satu pun baris rezim pada 1m. Bar satu menit dipakai menilai
        hasil, bukan menggolongkan rezim.
        """
        berrezim = {h.value for h in STORED_INTERVALS} - {Horizon.M1.value}

        assert set(BOBOT_INTERVAL) == berrezim

    def test_urutannya_yang_dipertahankan(self) -> None:
        urut = [BOBOT_INTERVAL[i] for i in ("15m", "1h", "1d")]

        assert urut == sorted(urut)
        assert BOBOT_INTERVAL["1d"] > BOBOT_INTERVAL["15m"]


class TestKeyakinanBolehTidakTerukur:
    def test_sumber_tanpa_keyakinan_tetap_menghasilkan_peta(self) -> None:
        """``signal_snapshots`` tidak punya kolom keyakinan rezim. Kalau peta
        menuntutnya, sumber yang benar-benar hidup tidak bisa dipakai sama
        sekali - dan router akan berdiri di atas tabel mati."""
        peta = susun_peta((_b("15m", "RANGING", None), _b("1h", "RANGING", None)))

        assert peta.primary == "RANGING"
        assert peta.primary_confidence > 0.0

    def test_keyakinan_yang_ada_tetap_dipakai_membobot(self) -> None:
        """Bacaan yang ragu tidak boleh menang atas bacaan yang yakin di
        interval yang sama."""
        peta = susun_peta((_b("1h", "BREAKOUT", 20.0), _b("1h", "REVERSAL", 95.0)))

        assert peta.primary == "REVERSAL"


class TestKeyakinanPrimaryAdalahKesepakatan:
    def test_sepakat_lintas_horizon_lebih_yakin_daripada_berselisih(self) -> None:
        """**Ini yang berubah dari versi pertama.** Dulu
        ``primary_confidence`` adalah rata-rata keyakinan bacaan pendukung -
        dan dengan sumber yang tidak menyediakan keyakinan sama sekali, angka
        itu tidak punya isi.

        Yang menggantikannya bisa diukur dari peta itu sendiri: berapa bagian
        dari seluruh bobot yang mendukung primary. Tiga horizon yang sepakat
        adalah bukti yang lebih kuat daripada tiga yang berselisih, dan itu
        pertanyaan yang berbeda dari :func:`~aruna.router.rezim.stabilitas`,
        yang mengukur kesepakatan lintas WAKTU pada satu horizon. Keduanya
        boleh dikalikan tanpa menghitung hal yang sama dua kali.
        """
        sepakat = susun_peta((
            _b("15m", "TRENDING"), _b("1h", "TRENDING"), _b("1d", "TRENDING"),
        ))
        berselisih = susun_peta((
            _b("15m", "TRENDING"), _b("1h", "RANGING"), _b("1d", "REVERSAL"),
        ))

        assert sepakat.primary_confidence == 100.0
        assert berselisih.primary_confidence < sepakat.primary_confidence

    def test_keyakinan_tidak_pernah_melebihi_seratus(self) -> None:
        peta = susun_peta((_b("15m", "TRENDING"), _b("1d", "TRENDING")))

        assert 0.0 < peta.primary_confidence <= 100.0


class TestSatuanDiubahDiSatuTempat:
    def test_pecahan_nol_sampai_satu_punya_pintu_sendiri(self) -> None:
        """``regimes.confidence`` disimpan **0..1** - terukur 0,653 sampai
        1,000 - sementara peta ini memakai 0..100.

        Kalau angka mentahnya dioper apa adanya, penskalaan di
        :func:`~aruna.router.kecocokan.nilai` menjadi 0,0065 alih-alih 0,65
        dan **setiap strategi runtuh ke NETRAL tanpa satu pun galat**. Bentuk
        kegagalan yang sama dengan ``dimensions``-sebagai-teks di Task 3:
        senyap, dan terlihat seperti keputusan.

        Karena itu konversinya punya satu pintu, dan nama bidangnya menyebut
        satuannya sendiri di tiap tempat ia dibaca.
        """
        b = BacaanRezim.dari_pecahan("1d", "TRENDING", 0.653)

        assert b.keyakinan_persen == 65.3

    def test_pecahan_di_luar_nol_satu_ditolak(self) -> None:
        """Memanggil ``dari_pecahan`` dengan 85 berarti pemanggilnya mengira
        satuannya persen. Diam-diam mengalikannya menjadi 8.500 jauh lebih
        buruk daripada menolak."""
        with pytest.raises(ValueError):
            BacaanRezim.dari_pecahan("1d", "TRENDING", 85.0)

    def test_persen_di_luar_nol_seratus_ditolak(self) -> None:
        with pytest.raises(ValueError):
            BacaanRezim(interval="1d", regime="TRENDING", keyakinan_persen=150.0)
