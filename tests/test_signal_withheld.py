"""Kenapa sebuah analisis tidak menjadi signal (PASAL 11.12).

Hampir semua yang pasal ini minta sudah tersimpan sebelum modul ini ada. Yang
diuji di sini adalah bagian yang hilang: alasannya bisa **dihitung**, bukan
sekadar dibaca satu per satu.
"""

from __future__ import annotations

import pytest

from aruna.signals.withheld import Withheld, WithheldCode, classify, tally


class TestPengelompokan:
    @pytest.mark.parametrize(
        ("kalimat", "kode"),
        [
            ("verdict is WAIT, not a position", WithheldCode.NON_DIRECTIONAL),
            ("quality gate: quality 41/100 di bawah 60", WithheldCode.QUALITY_GATE),
            ("duplikat prediksi terbuka: arah sama (BUY)", WithheldCode.DUPLICATE),
            ("cooldown sesudah kalah sampai 2026-08-18", WithheldCode.COOLDOWN),
        ],
    )
    def test_kalimat_nyata_terkelompokkan(self, kalimat: str, kode) -> None:
        """Kalimat-kalimat ini yang benar-benar ditulis jalur penguncian."""
        assert classify(kalimat) is kode

    def test_yang_tidak_dikenali_jadi_unknown(self) -> None:
        """Bukan menebak yang paling mirip. Salah kelompok yang diam membuat
        hitungan terlihat lengkap sambil salah; UNKNOWN yang bertumbuh
        terlihat sebagai pertanyaan."""
        assert classify("sesuatu yang belum pernah ditulis") is WithheldCode.UNKNOWN

    def test_kosong_jadi_unknown(self) -> None:
        assert classify(None) is WithheldCode.UNKNOWN
        assert classify("") is WithheldCode.UNKNOWN

    def test_tidak_peduli_huruf_besar_kecil(self) -> None:
        assert classify("QUALITY GATE: gagal") is WithheldCode.QUALITY_GATE

    def test_non_directional_dipisah_dari_penolakan(self) -> None:
        """Council yang tidak memilih arah BUKAN penolakan - analisisnya
        selesai dan kesimpulannya "tidak ada posisi". Menyatukannya membuat
        ARUNA yang sedang menunggu pasar terlihat seperti ARUNA yang rusak."""
        assert classify("verdict is WAIT, not a position") is not (
            WithheldCode.QUALITY_GATE
        )


class TestHitungan:
    def test_menjawab_kenapa_sebanyak_ini(self) -> None:
        """Seratus penahanan karena confidence dan seratus karena data basi
        adalah dua masalah berbeda dengan dua perbaikan berbeda - dan keduanya
        terbaca sama selama alasannya hanya kalimat."""
        counts = tally([
            "verdict is WAIT, not a position",
            "verdict is WAIT, not a position",
            "verdict is WAIT, not a position",
            "quality gate: coverage terlalu tipis",
            "duplikat prediksi terbuka",
        ])
        assert counts["NON_DIRECTIONAL"] == 3
        assert counts["QUALITY_GATE"] == 1
        assert counts["DUPLICATE"] == 1

    def test_terbanyak_lebih_dulu(self) -> None:
        counts = tally([
            "quality gate: x",
            "verdict is WAIT, not a position",
            "verdict is WAIT, not a position",
        ])
        assert next(iter(counts)) == "NON_DIRECTIONAL"

    def test_kosong_bukan_kesalahan(self) -> None:
        assert tally([]) == {}
        assert tally(None) == {}

    def test_yang_tak_terkelompokkan_ikut_terhitung(self) -> None:
        """Yang tidak terkelompokkan adalah bagian dari jawabannya: kalau
        angkanya besar, hitungan di atasnya tidak selengkap penampilannya."""
        counts = tally(["kalimat asing", "kalimat asing lain"])
        assert counts["UNKNOWN"] == 2


class TestNilaiDanAmbangBerpasangan:
    def test_keduanya_disimpan(self) -> None:
        """"Confidence 0,41" tidak berarti apa-apa tanpa lantainya, dan
        "lantai 0,55" tidak berarti apa-apa tanpa nilainya."""
        w = Withheld(
            code=WithheldCode.QUALITY_GATE, reason="quality 41/100 di bawah 60",
            measured=41.0, threshold=60.0,
        )
        d = w.to_dict()
        assert d["measured"] == 41.0
        assert d["threshold"] == 60.0

    def test_prosanya_tidak_dibuang(self) -> None:
        """Kode menjawab "kelompok apa", kalimat menjawab "apa persisnya".
        Mengganti kalimat dengan kode menghapus satu-satunya tempat yang
        menyebut angka yang meleset."""
        w = Withheld(WithheldCode.COOLDOWN, "cooldown sampai 12:30 (rugi 2,1%)")
        assert "2,1%" in w.to_dict()["reason"]

    def test_tambahan_ikut_terbawa(self) -> None:
        w = Withheld(
            WithheldCode.QUALITY_GATE, "x", extra={"coverage": 0.42}
        )
        assert w.to_dict()["coverage"] == 0.42


class TestKabelKeJalurHidup:
    def test_repository_menulis_kode(self) -> None:
        import inspect

        from aruna.db.repositories.signals import SignalRepository

        source = inspect.getsource(SignalRepository.lock)
        assert "withheld_code" in source
        assert "withheld.code.value" in source

    def test_kode_tidak_ditulis_untuk_yang_terbit(self) -> None:
        """Prediksi yang terbit tidak ditahan. Tanpa ini, satu bug di jalur
        penguncian membuat hitungan "kenapa diam" memuat baris yang justru
        tidak diam."""
        import inspect

        from aruna.db.repositories.signals import SignalRepository

        source = inspect.getsource(SignalRepository.lock)
        assert "None if published or withheld is None" in source

    def test_skema_menegakkan_hal_yang_sama(self) -> None:
        """Dijaga dua kali: di aplikasi dan di skema. Yang kedua bertahan walau
        kode aplikasinya salah tulis besok."""
        from pathlib import Path

        sql = Path("migrations/0025_withheld_code.sql").read_text(encoding="utf-8")
        assert "signals_withheld_code_only_when_withheld" in sql

    def test_skema_membatasi_kodenya(self) -> None:
        from pathlib import Path

        sql = Path("migrations/0025_withheld_code.sql").read_text(encoding="utf-8")
        for code in WithheldCode:
            assert f"'{code.value}'" in sql, code

    def test_repository_bisa_menghitung(self) -> None:
        from aruna.db.repositories.signals import SignalRepository

        assert hasattr(SignalRepository, "withheld_tally")

    def test_hitungan_memasukkan_yang_tanpa_kode(self) -> None:
        import inspect

        from aruna.db.repositories.signals import SignalRepository

        source = inspect.getsource(SignalRepository.withheld_tally)
        assert "COALESCE(withheld_code, 'UNKNOWN')" in source

    def test_dikelompokkan_sekali_saat_menulis(self) -> None:
        """Klasifikasi yang berjalan saat MEMBACA akan berubah jawabannya
        setiap kali daftar frasanya diperbaiki, dan hitungan bulan lalu ikut
        berubah bersamanya."""
        import inspect

        from aruna.signals import service as svc

        source = inspect.getsource(svc.SignalService.lock_signals)
        assert "classify_withheld(reason)" in source
