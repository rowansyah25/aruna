"""Pilihan router menjadi baris performa (bagian 17.36 - 17.37).

**Inilah yang membuat Task 3 berhenti menunggu.** `performa_rezim` menyaring
baris `strategy_performance` berlabel `router-1`, dan sampai modul ini ada
tidak seorang pun menulisnya - jadi ia memulangkan `None` selamanya dan seluruh
separuh performa-dan-risiko `kecocokan.nilai` menganggur.

**Yang diuji paling keras di sini bahwa angkanya TIDAK melingkar**, karena
justru itu satu-satunya alasan baris ini pantas ada di samping `learn-12.0`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from aruna.router.label import VERSI_ROUTER, dilabeli_router, performa_rezim
from aruna.router.pengukuran import REZIM_SEMUA, baris_simpan, susun_slice

SAAT = datetime(2026, 8, 23, 10, 0, tzinfo=UTC)


def _r(
    champion: str | None,
    regime: str,
    result: str,
    *,
    pnl: str = "0",
    menit: int = 0,
) -> dict[str, Any]:
    return {
        "champion": champion,
        "regime": regime,
        "result": result,
        "net_pnl": Decimal(pnl),
        "resolved_at": SAAT + timedelta(minutes=menit),
    }


class TestTidakLagiMelingkar:
    """**Alasan tunggal baris ini pantas ada di samping `learn-12.0`.**"""

    def test_satu_strategi_bisa_punya_beberapa_rezim(self) -> None:
        """Yang lama tidak bisa. `classify()` menurunkan strategi DARI rezim
        lewat peta satu-ke-satu, jadi `regime=X` selalu himpunan baris yang
        sama persis dengan `regime=ALL` - terukur di produksi: STR-005
        188W/726L di keduanya.

        Begitu ROUTER yang melabeli, `STR-001` bisa menjadi champion di
        `TRENDING`, `TRENDING_BULLISH`, dan `TRENDING_BEARISH` - dan tiap
        pasangan membawa keterangan yang berbeda.
        """
        irisan = susun_slice([
            _r("STR-001", "TRENDING", "WIN", menit=1),
            _r("STR-001", "TRENDING_BULLISH", "WIN", menit=2),
            _r("STR-001", "TRENDING_BEARISH", "LOSS", menit=3),
        ])
        per_rezim = {s.dimensions["regime"]: s for s in irisan}

        assert set(per_rezim) == {
            "TRENDING", "TRENDING_BULLISH", "TRENDING_BEARISH", REZIM_SEMUA,
        }

    def test_regime_all_berbeda_dari_tiap_bagiannya(self) -> None:
        """Di baris `learn-12.0` keduanya identik, dan itulah bentuk
        melingkarnya. Di sini `ALL` adalah jumlah yang sungguhan."""
        irisan = susun_slice([
            _r("STR-001", "TRENDING", "WIN", menit=1),
            _r("STR-001", "BREAKOUT", "LOSS", menit=2),
        ])
        per_rezim = {s.dimensions["regime"]: s for s in irisan}

        assert per_rezim[REZIM_SEMUA].evidence.total == 2
        assert per_rezim["TRENDING"].evidence.total == 1
        assert per_rezim[REZIM_SEMUA].evidence.total != per_rezim[
            "TRENDING"
        ].evidence.total

    def test_dua_strategi_bisa_berbagi_satu_rezim(self) -> None:
        """Kebalikannya juga: `TRENDING` bisa dipimpin `STR-001` hari ini dan
        `STR-005` besok, dan keduanya terukur terpisah pada rezim yang sama.
        Peta balik `classify` tidak pernah bisa menghasilkan ini."""
        irisan = susun_slice([
            _r("STR-001", "TRENDING", "WIN", menit=1),
            _r("STR-005", "TRENDING", "LOSS", menit=2),
        ])
        kode = {s.strategy_code for s in irisan}

        assert kode == {"STR-001", "STR-005"}


class TestYangTidakBolehMasuk:
    def test_penolakan_router_bukan_performa_strategi(self) -> None:
        """Baris tanpa champion punya nilainya sendiri - `kode_kosong` yang
        menghitungnya - tapi ia bukan performa sebuah strategi. Memasukkannya
        mengaitkan hasil sinyal kepada strategi yang tidak dipilih."""
        irisan = susun_slice([
            _r(None, "ANOMALY", "LOSS", menit=1),
            _r("STR-001", "TRENDING", "WIN", menit=2),
        ])

        assert {s.strategy_code for s in irisan} == {"STR-001"}

    def test_sinyal_tanpa_hasil_tidak_menghasilkan_baris_kosong(self) -> None:
        """Baris bersampel nol membuat `performa_rezim` membagi dengan nol -
        dan sebelum itu, membuat sebuah strategi terlihat sudah diukur padahal
        belum."""
        irisan = susun_slice([_r("STR-001", "TRENDING", "WAIT", menit=1)])

        assert irisan == ()

    def test_champion_kosong_bukan_kode_kosong(self) -> None:
        irisan = susun_slice([_r("   ", "TRENDING", "WIN")])

        assert irisan == ()


class TestBentuknyaMengikutiPhase12:
    def test_drawdown_dari_puncak_kumulatif_bukan_pnl_terburuk(self) -> None:
        """Rumusnya **dipinjam**, bukan ditulis ulang: dua rumus drawdown
        adalah dua angka yang harus tetap sepakat selamanya."""
        irisan = susun_slice([
            _r("STR-001", "TRENDING", "WIN", pnl="10", menit=1),
            _r("STR-001", "TRENDING", "LOSS", pnl="-4", menit=2),
            _r("STR-001", "TRENDING", "LOSS", pnl="-3", menit=3),
        ])
        satu = next(s for s in irisan if s.dimensions["regime"] == "TRENDING")

        assert satu.max_drawdown == Decimal("7")
        assert satu.net_pnl == Decimal("3")

    def test_urutan_waktu_menentukan_drawdown(self) -> None:
        """Masukan yang diacak harus memberi jawaban yang sama - modul ini
        yang mengurutkannya, bukan pemanggilnya."""
        maju = susun_slice([
            _r("STR-001", "TRENDING", "LOSS", pnl="-5", menit=1),
            _r("STR-001", "TRENDING", "WIN", pnl="9", menit=2),
        ])
        acak = susun_slice([
            _r("STR-001", "TRENDING", "WIN", pnl="9", menit=2),
            _r("STR-001", "TRENDING", "LOSS", pnl="-5", menit=1),
        ])

        assert maju[0].max_drawdown == acak[0].max_drawdown == Decimal("5")

    def test_baris_simpan_berlabel_router(self) -> None:
        """Satu-satunya hal yang memisahkan baris ini dari `learn-12.0` di
        tabel yang sama."""
        baris = baris_simpan(
            susun_slice([_r("STR-001", "TRENDING", "WIN")]), pada=SAAT
        )

        assert baris
        assert all(b["model_version"] == VERSI_ROUTER for b in baris)
        assert all(dilabeli_router(b) for b in baris)

    def test_kolomnya_lengkap_untuk_penyimpan(self) -> None:
        """`save_strategy_performance` membaca kunci per nama; yang hilang
        meledak sebagai KeyError di tengah siklus, bukan saat test."""
        wajib = {
            "strategy_code", "slice_key", "dimensions", "wins", "losses",
            "sample_size", "win_rate", "ci_low", "ci_high", "evidence",
            "net_pnl", "max_drawdown", "model_version", "computed_at",
        }
        baris = baris_simpan(
            susun_slice([_r("STR-001", "TRENDING", "WIN")]), pada=SAAT
        )

        assert set(baris[0]) == wajib

    def test_evidence_adalah_TINGKAT_bukan_jumlah(self) -> None:
        """Phase 12 menulis `evidence` sebagai tingkat bukti, bukan cacahnya.
        Dua penulis ke satu tabel yang mengisi kolom yang sama dengan arti
        berbeda menghasilkan kolom yang tidak bisa dibaca siapa pun."""
        baris = baris_simpan(
            susun_slice([_r("STR-001", "TRENDING", "WIN")]), pada=SAAT
        )

        assert isinstance(baris[0]["evidence"], str)


class TestGerbangnyaBisaTerBUKA:
    """**Penulis yang menulis terlalu sedikit sama saja dengan tidak ada.**

    Ini jebakan yang hampir kumasuki: `BATAS_ATRIBUSI` semula meminjam bawaan
    `LearningRepository.resolved` - lima ratus - yang bukan angka yang dipakai
    siapa pun. Pemanggil sungguhannya mengoper `AppSettings.review_limit`, dan
    komentar setelan itu menjelaskan kenapa dari pengukurannya sendiri:

        "Batas yang terlalu kecil tidak membuat kalibrasi salah - ia
        membuatnya tidak ada, lalu diam."
    """

    def test_batasnya_cukup_untuk_mencapai_ambang_sampel(self) -> None:
        """Aritmetikanya, bukan seleranya. Dengan sepersepuluh sinyal yang bisa
        diatribusikan, lima ratus baris memberi lima puluh atribusi - dibagi
        beberapa pasangan (strategi, rezim), tidak satu pun akan pernah
        mencapai seratus. Gerbangnya tidak akan pernah terbuka."""
        from aruna.db.repositories.router import (
            BATAS_ATRIBUSI,
            TINGKAT_ATRIBUSI,
        )
        from aruna.governance.proposal import MIN_VALIDATION_SAMPLE

        teratribusi = BATAS_ATRIBUSI * TINGKAT_ATRIBUSI

        cukup = teratribusi >= MIN_VALIDATION_SAMPLE

        assert cukup, (
            f"{BATAS_ATRIBUSI} baris x {TINGKAT_ATRIBUSI:.0%} = "
            f"{teratribusi:.0f} atribusi, di bawah ambang "
            f"{MIN_VALIDATION_SAMPLE} - performa_rezim akan memulangkan None "
            "selamanya walau penulisnya ada"
        )

    def test_tidak_meminjam_bawaan_yang_tidak_dipakai_siapa_pun(self) -> None:
        """`review_limit` adalah angka yang benar-benar dipakai produksi;
        bawaan repositorinya bukan. Meminjam yang salah adalah cara paling
        halus untuk membangun sesuatu yang tidak pernah bekerja."""
        from aruna.core.config import UpkeepSettings
        from aruna.db.repositories.router import BATAS_ATRIBUSI

        dipakai = int(UpkeepSettings.model_fields["review_limit"].default)

        assert min(BATAS_ATRIBUSI, dipakai) == dipakai, (
            f"{BATAS_ATRIBUSI} di bawah yang dipakai produksi {dipakai}"
        )


class TestBisaDibacaKembaliOlehTask3:
    def test_baris_yang_ditulis_lolos_performa_rezim(self) -> None:
        """**Ujung ke ujung, dan ini yang membuktikan lingkarannya tertutup.**
        Yang ditulis modul ini harus bisa dibaca `performa_rezim` - kalau
        tidak, Task 3 tetap menunggu selamanya walau tabelnya terisi.
        """
        baris = baris_simpan(
            susun_slice(
                [_r("STR-001", "TRENDING", "WIN", menit=i) for i in range(8)]
                + [_r("STR-001", "TRENDING", "LOSS", menit=8 + i) for i in range(2)]
            ),
            pada=SAAT,
        )
        hasil = performa_rezim(
            baris, kode="STR-001", regime="TRENDING", minimum=10
        )

        assert hasil is not None
        assert hasil.sample_size == 10
        assert hasil.win_rate == 0.8

    def test_baris_turunan_tetap_ditolak(self) -> None:
        """Penjaga Task 3 tidak boleh melunak karena penulisnya sudah ada."""
        turunan = [
            {**b, "model_version": "learn-12.0"}
            for b in baris_simpan(
                susun_slice(
                    [_r("STR-001", "TRENDING", "WIN", menit=i) for i in range(20)]
                ),
                pada=SAAT,
            )
        ]

        assert performa_rezim(
            turunan, kode="STR-001", regime="TRENDING", minimum=10
        ) is None
