"""Penyimpanan keputusan XAU - termasuk yang ditolak.

Dua lapis, dan yang pertama berjalan tanpa MySQL:

* :class:`TestBarisYangDitulis` memeriksa BARIS yang disusun repositori dengan
  penulis palsu yang merekam SQL dan parameternya. Ia menangkap kesalahan
  pemetaan - kolom tertukar, NULL yang jadi nol, penolakan yang tidak jadi
  disimpan - tanpa perlu basis data.

* :class:`TestSkema` memeriksa migrasinya sendiri: kosakata yang ditegakkan di
  storage, dan ketiadaan ON DUPLICATE KEY UPDATE.

Yang TIDAK diperiksa di sini adalah apakah MySQL menerima SQL-nya. Itu hanya
bisa dibuktikan dengan menjalankannya, dan MySQL lokal mati - lihat catatan di
akhir berkas.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from aruna.core.enums import AgentRole, Decision
from aruna.db.repositories.xau import VERSI_MODEL_XAU, XauRepository
from aruna.xau.geometri import Geometri
from aruna.xau.keputusan import SinyalXau
from aruna.xau.suara import RekapSuara, Suara, SuaraAgen

MIGRASI = Path(__file__).resolve().parent.parent / "migrations" / "0046_xau_sinyal.sql"
SAAT = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class PenulisPalsu:
    """Merekam apa yang akan ditulis, tanpa menulis."""

    def __init__(self) -> None:
        self.insert_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.next_id = 7

    async def insert(self, sql: str, *params: Any) -> int:
        self.insert_calls.append((sql, params))
        return self.next_id

    async def execute(self, sql: str, *params: Any) -> None:
        self.insert_calls.append((sql, params))

    def kolom(self, indeks: int = 0) -> list[str]:
        """Nama kolom dari INSERT ke-``indeks``, sesuai urutannya."""
        sql = self.insert_calls[indeks][0]
        dalam = re.search(r"\(([^)]*)\)\s*VALUES", sql, re.S)
        assert dalam, f"tidak menemukan daftar kolom di: {sql[:120]}"
        return [k.strip() for k in dalam.group(1).split(",")]

    def nilai(self, indeks: int = 0) -> dict[str, Any]:
        return dict(zip(self.kolom(indeks), self.insert_calls[indeks][1], strict=True))


def _rincian() -> tuple[SuaraAgen, ...]:
    """Tiga agen dengan tiga sikap berbeda terhadap BUY."""
    return (
        SuaraAgen(AgentRole.TECHNICAL, Suara.AGREE, Decision.BUY, 0.8, False),
        SuaraAgen(AgentRole.REVERSAL, Suara.DISAGREE, Decision.SELL, 0.6, False),
        SuaraAgen(AgentRole.NEWS, Suara.NEUTRAL, Decision.WAIT, 0.0, True),
    )


def _geometri() -> Geometri:
    return Geometri(
        entry=Decimal("1000"),
        stop=Decimal("993"),
        target=Decimal("1020"),
        atr=Decimal("4.0"),
        sentuhan_target=5,
    )


def _sinyal(**kw) -> SinyalXau:
    bawaan = dict(
        keputusan=Decision.BUY,
        setup_id="XAU/USD:BUY:1020.00",
        alasan=None,
        rekap=RekapSuara(setuju=4, menentang=0, netral=5, rincian=()),
        geometri=_geometri(),
        confidence=0.72,
        spread_diukur=False,
    )
    return SinyalXau(**{**bawaan, **kw})


class TestBarisYangDitulis:
    @pytest.fixture
    def db(self) -> PenulisPalsu:
        return PenulisPalsu()

    async def test_sinyal_berarah_tersimpan_dengan_geometrinya(self, db) -> None:
        await XauRepository(db).simpan(_sinyal(), as_of=SAAT, decided_at=SAAT)
        baris = db.nilai()
        assert baris["keputusan"] == "BUY"
        assert baris["entry"] == Decimal("1000")
        assert baris["target"] == Decimal("1020")
        assert baris["alasan_kosong"] is None

    async def test_penolakan_juga_tersimpan(self, db) -> None:
        """Spec: simpan seluruh hasil. NO SIGNAL bukan hasil yang dibuang."""
        ditolak = _sinyal(
            keputusan=Decision.NO_SIGNAL,
            alasan="RR 0.29 di bawah 1.5",
            geometri=_geometri(),
        )
        await XauRepository(db).simpan(ditolak, as_of=SAAT, decided_at=SAAT)
        baris = db.nilai()
        assert baris["keputusan"] == "NO_SIGNAL"
        assert baris["alasan_kosong"] == "RR 0.29 di bawah 1.5"

    async def test_penolakan_menyimpan_angka_penyebabnya(self, db) -> None:
        """Penolakan tanpa angkanya tak bisa dipelajari enam bulan kemudian."""
        ditolak = _sinyal(keputusan=Decision.NO_SIGNAL, alasan="RR rendah")
        await XauRepository(db).simpan(ditolak, as_of=SAAT, decided_at=SAAT)
        baris = db.nilai()
        assert baris["rr"] is not None
        assert baris["kontradiksi"] is not None
        assert baris["confidence"] is not None

    async def test_tanpa_geometri_kolomnya_null_bukan_nol(self, db) -> None:
        """Nol adalah harga; tidak diukur adalah ketiadaan harga."""
        await XauRepository(db).simpan(
            _sinyal(keputusan=Decision.NO_SIGNAL, alasan="tak ada level", geometri=None),
            as_of=SAAT,
            decided_at=SAAT,
        )
        baris = db.nilai()
        for kolom in ("entry", "stop", "target", "atr", "rr", "target_atr"):
            assert baris[kolom] is None, f"{kolom} harus NULL, bukan {baris[kolom]!r}"

    async def test_kontradiksi_tak_terukur_jadi_null(self, db) -> None:
        sepi = RekapSuara(setuju=0, menentang=0, netral=9, rincian=())
        await XauRepository(db).simpan(
            _sinyal(keputusan=Decision.NO_SIGNAL, alasan="sepi", rekap=sepi),
            as_of=SAAT,
            decided_at=SAAT,
        )
        baris = db.nilai()
        assert baris["kontradiksi"] is None
        assert baris["netral"] == 9

    async def test_spread_tak_diukur_tercatat_sebagai_tak_diukur(self, db) -> None:
        await XauRepository(db).simpan(_sinyal(), as_of=SAAT, decided_at=SAAT)
        baris = db.nilai()
        assert baris["spread_diukur"] is False
        assert baris["spread_bps"] is None

    async def test_as_of_bukan_jam_sistem(self, db) -> None:
        await XauRepository(db).simpan(_sinyal(), as_of=SAAT, decided_at=SAAT)
        assert "as_of" in db.kolom()

    async def test_versi_model_tercatat(self, db) -> None:
        """Tanpa versi, hasil dari dua model berbeda tak bisa dipisahkan."""
        await XauRepository(db).simpan(_sinyal(), as_of=SAAT, decided_at=SAAT)
        assert db.nilai()["model_version"] == VERSI_MODEL_XAU

    async def test_suara_tersimpan_per_agen(self, db) -> None:
        sinyal = _sinyal(rekap=RekapSuara(setuju=1, menentang=1, netral=1, rincian=_rincian()))
        await XauRepository(db).simpan(sinyal, as_of=SAAT, decided_at=SAAT)
        suara = [c for c in db.insert_calls if "xau_agent_votes" in c[0]]
        assert len(suara) == 3

    async def test_suara_dan_keputusan_asli_dibedakan(self, db) -> None:
        """`suara` adalah SIKAP terhadap arah; `decision` apa yang agen katakan.

        Menyalin sikap ke kedua kolom membuat tabelnya terisi tapi tak berguna:
        seluruh baris NEUTRAL akan terbaca seolah agennya mengembalikan
        'NEUTRAL', yang bukan sebuah Decision - dan penilaian per agen di
        Rencana 3 kehilangan bahannya.
        """
        sinyal = _sinyal(rekap=RekapSuara(setuju=1, menentang=1, netral=1, rincian=_rincian()))
        await XauRepository(db).simpan(sinyal, as_of=SAAT, decided_at=SAAT)

        baris = [
            dict(zip(db.kolom(i), db.insert_calls[i][1], strict=True))
            for i, c in enumerate(db.insert_calls)
            if "xau_agent_votes" in c[0]
        ]
        menentang = next(b for b in baris if b["suara"] == "DISAGREE")
        assert menentang["decision"] == "SELL", (
            "agen yang menentang BUY mengatakan SELL, bukan 'DISAGREE'"
        )

        netral = next(b for b in baris if b["suara"] == "NEUTRAL")
        assert netral["decision"] == "WAIT"
        assert netral["abstained"] is True

    async def test_confidence_agen_tersimpan(self, db) -> None:
        """Tanpa keyakinan tiap agen, bobot per agen di Rencana 3 tak punya bahan."""
        sinyal = _sinyal(rekap=RekapSuara(setuju=1, menentang=1, netral=1, rincian=_rincian()))
        await XauRepository(db).simpan(sinyal, as_of=SAAT, decided_at=SAAT)
        baris = [
            dict(zip(db.kolom(i), db.insert_calls[i][1], strict=True))
            for i, c in enumerate(db.insert_calls)
            if "xau_agent_votes" in c[0]
        ]
        setuju = next(b for b in baris if b["suara"] == "AGREE")
        assert setuju["confidence"] == Decimal("0.8")

    async def test_bukti_tersimpan_per_timeframe(self, db) -> None:
        bacaan = {"5m": {"atr": (4.0, 14, 14)}, "1h": {"rsi": (55.0, 20, 14)}}
        await XauRepository(db).simpan(
            _sinyal(), as_of=SAAT, decided_at=SAAT, bukti=bacaan
        )
        ev = [c for c in db.insert_calls if "xau_evidence" in c[0]]
        assert len(ev) == 2

    async def test_bukti_tak_terhitung_disimpan_null(self, db) -> None:
        bacaan = {"4h": {"vwap": (None, 5, 20)}}
        await XauRepository(db).simpan(
            _sinyal(), as_of=SAAT, decided_at=SAAT, bukti=bacaan
        )
        ev = next(c for c in db.insert_calls if "xau_evidence" in c[0])
        assert None in ev[1], "indikator yang tak terhitung harus NULL, bukan 0"


def _tanpa_komentar(teks: str) -> str:
    """Buang baris komentar SQL.

    Yang dijaga di kelas di bawah adalah SQL yang DIEKSEKUSI. Komentarnya
    justru menjelaskan kenapa frasa-frasa itu tidak boleh ada, jadi memindai
    komentar akan membuat penjelasan yang benar menjatuhkan tesnya sendiri -
    dan mendorong orang berikutnya menghapus penjelasannya, bukan memperbaiki
    kodenya.
    """
    return "\n".join(
        baris for baris in teks.splitlines() if not baris.strip().startswith("--")
    )


class TestSkema:
    @pytest.fixture
    def sql(self) -> str:
        return _tanpa_komentar(MIGRASI.read_text(encoding="utf-8")).upper()

    def test_kosakata_ditegakkan_di_storage(self, sql) -> None:
        """Larangan WAIT berlaku juga bagi penulis SQL langsung."""
        assert "KEPUTUSAN IN ('BUY', 'SELL', 'NO_SIGNAL')" in sql
        assert "'WAIT'" not in sql.split("XAU_AGENT_VOTES")[0]

    def test_suara_ditegakkan_di_storage(self, sql) -> None:
        assert "SUARA IN ('AGREE', 'DISAGREE', 'NEUTRAL')" in sql

    def test_tidak_pernah_ditimpa(self, sql) -> None:
        """Penulisan kedua atas bar yang sama harus GAGAL, bukan menang."""
        assert "ON DUPLICATE KEY UPDATE" not in sql
        assert "UQ_XAU_SETUP_BAR" in sql

    def test_no_signal_wajib_beralasan(self, sql) -> None:
        assert "XAU_KOSONG_WAJIB_BERALASAN" in sql

    def test_sinyal_berarah_wajib_punya_geometri(self, sql) -> None:
        assert "XAU_ARAH_PUNYA_GEOMETRI" in sql

    def test_penghapusan_dipersulit_bukan_dipermudah(self, sql) -> None:
        """Spec: jangan menghapus LOSS.

        CASCADE membuat satu DELETE atas prediksi menyapu bukti dan suaranya
        tanpa perlawanan. RESTRICT membuatnya gagal selama buktinya ada - di
        tabel yang seluruh gunanya adalah menyimpan hasil, itu perilaku yang
        diinginkan.
        """
        assert "ON DELETE CASCADE" not in sql
        assert sql.count("ON DELETE RESTRICT") == 2

    def test_tidak_ada_kolom_yang_membedakan_rugi(self, sql) -> None:
        """Tak boleh ada kolom atau indeks yang memperlakukan LOSS khusus."""
        for kata in ("LOSS", "RUGI"):
            assert kata not in sql, f"{kata} muncul di SQL migrasi XAU"
