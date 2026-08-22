"""Apa yang Phase 16 serahkan ke Phase 14 (bagian 16.1, 16.18).

Kelas kedua di bawah adalah yang paling penting di seluruh Phase 16. Ia
memeriksa **setiap modul** di paket `aruna.scenario`, bukan hanya berkas
keluarannya: pintu yang dijaga tidak berarti kalau dindingnya berlubang, dan
satu bidang `direction` di modul mana pun akan membuat bagian 16.18 tidak
berlaku tanpa satu test pun merah.

Penjaganya berbasis AST dan bukan pencarian teks - alasannya sudah terbukti dua
kali di proyek ini: modul-modul ini MENJELASKAN dalam docstring-nya bahwa mereka
tidak boleh vote LONG, dan pencarian teks gagal justru karena penjelasannya
benar.
"""

from __future__ import annotations

import ast
import inspect
import pkgutil
from datetime import UTC, datetime

import pytest

import aruna.scenario
from aruna.scenario.adapter import HasilAdapter, StatusSimulasi
from aruna.scenario.bukti import BuktiSkenario, susun_bukti
from aruna.scenario.mesin import simulasikan
from aruna.scenario.models import LABEL_BUKTI, Invalidasi, Skenario
from aruna.scenario.pemicu import Peristiwa

NOW = datetime(2026, 8, 22, tzinfo=UTC)
PEMICU = frozenset({Peristiwa.BREAKOUT_BESAR})


def _internal() -> tuple[Skenario, ...]:
    return simulasikan(
        market="CRYPTO",
        asset="BTC/USDT",
        pemicu=PEMICU,
        kondisi_awal=("harga > resistance",),
        bukti=("struktur: higher high",),
        pada=NOW,
    )


def _eksternal() -> Skenario:
    return Skenario(
        scenario_id="ext-1",
        market="CRYPTO",
        asset="BTC/USDT",
        timestamp=NOW,
        nama="MiroFish Alternative",
        deskripsi="dari mesin eksternal",
        kondisi_awal=("k",),
        pemicu="BREAKOUT_BESAR",
        perkembangan=("a", "b"),
        invalidasi=Invalidasi(syarat=("s1", "s2")),
        risiko="HIGH",
        keyakinan=0.4,
        bobot=40,
        bukti=("b",),
        versi_simulasi="mirofish-0",
    )


def _bukti(eksternal: HasilAdapter | None = None) -> BuktiSkenario:
    return susun_bukti(
        market="CRYPTO",
        asset="BTC/USDT",
        pada=NOW,
        pemicu=PEMICU,
        internal=_internal(),
        eksternal=eksternal
        or HasilAdapter(status=StatusSimulasi.DEGRADED, catatan="belum dipasang"),
    )


class TestTidakAdaArahDiKeluaran:
    """Bagian 16.18."""

    def test_tidak_ada_bidang_arah(self) -> None:
        punya = {n for n in dir(_bukti()) if not n.startswith("_")}

        assert not (punya & {
            "direction", "decision", "arah", "keputusan", "rekomendasi",
            "aksi", "sinyal", "signal",
        })

    def test_tidak_ada_harga_masuk_target_atau_stop(self) -> None:
        """Harga target adalah keputusan yang menyamar sebagai angka: yang
        menerimanya tinggal membandingkannya dengan harga sekarang."""
        punya = {n for n in dir(_bukti()) if not n.startswith("_")}

        assert not (punya & {
            "target", "target_price", "entry", "harga_masuk", "stop",
            "stop_loss", "take_profit", "ukuran", "size", "leverage",
        })

    def test_keluaran_json_pun_tidak_membawa_arah(self) -> None:
        """Bidangnya bisa bersih sementara `to_dict` menambahkan sendiri."""
        d = _bukti().to_dict()

        assert not (set(d) & {"direction", "decision", "arah", "signal"})


class TestPaketTidakMenyentuhKeputusan:
    """Penjaga se-paket. Ini yang menjaga bagian 16.18 tetap berlaku.

    Diperiksa lewat AST karena modul-modul ini MENJELASKAN larangannya di
    docstring-nya sendiri; pencarian teks akan tersandung pada penjelasan yang
    benar - persis yang sudah terjadi dua kali di proyek ini.
    """

    @staticmethod
    def _modul():
        for info in pkgutil.iter_modules(aruna.scenario.__path__):
            yield __import__(
                f"aruna.scenario.{info.name}", fromlist=["_"]
            )

    def test_ada_modul_yang_diperiksa(self) -> None:
        """Penjaga yang berjalan atas nol modul lulus tanpa memeriksa apa pun -
        bentuk kegagalan yang paling sulit terlihat."""
        assert len(list(self._modul())) >= 6

    def test_tidak_ada_nama_berarah_di_seluruh_paket(self) -> None:
        for modul in self._modul():
            pohon = ast.parse(inspect.getsource(modul))
            nama = {n.id for n in ast.walk(pohon) if isinstance(n, ast.Name)}
            nama |= {
                n.attr for n in ast.walk(pohon) if isinstance(n, ast.Attribute)
            }
            nama |= {
                a.asname or a.name
                for n in ast.walk(pohon)
                if isinstance(n, ast.ImportFrom | ast.Import)
                for a in n.names
            }

            assert not (nama & {
                "Decision", "Direction", "direction", "decision", "arah",
                "keputusan", "LockedSignal", "CouncilVerdict",
            }), modul.__name__

    def test_tidak_ada_bidang_dataclass_berarah(self) -> None:
        """Bidang dataclass adalah `AnnAssign`, dan namanya tidak muncul
        sebagai `Name` yang dibaca - jadi test di atas tidak melihatnya."""
        for modul in self._modul():
            pohon = ast.parse(inspect.getsource(modul))
            bidang = {
                n.target.id
                for n in ast.walk(pohon)
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            }

            assert not (bidang & {
                "direction", "decision", "arah", "keputusan", "target_price",
                "entry", "stop_loss", "take_profit",
            }), modul.__name__

    def test_tidak_ada_long_atau_short_sebagai_nilai(self) -> None:
        """Teks "LONG" di docstring boleh - itu penjelasan larangannya. Teks
        "LONG" sebagai nilai yang dihitung adalah arah yang lolos lewat string.

        Docstring dikecualikan satu per satu lewat identitas simpulnya, bukan
        lewat pencocokan isi: dua string yang kebetulan sama isinya akan ikut
        terkecualikan kalau dicocokkan begitu.
        """
        for modul in self._modul():
            pohon = ast.parse(inspect.getsource(modul))

            docstring = set()
            for simpul in ast.walk(pohon):
                if isinstance(
                    simpul,
                    ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
                ):
                    badan = getattr(simpul, "body", [])
                    if (
                        badan
                        and isinstance(badan[0], ast.Expr)
                        and isinstance(badan[0].value, ast.Constant)
                        and isinstance(badan[0].value.value, str)
                    ):
                        docstring.add(id(badan[0].value))

            for simpul in ast.walk(pohon):
                if (
                    isinstance(simpul, ast.Constant)
                    and isinstance(simpul.value, str)
                    and id(simpul) not in docstring
                ):
                    assert simpul.value.strip().upper() not in {
                        "LONG", "SHORT", "BUY", "SELL",
                    }, f"{modul.__name__}: {simpul.value!r}"

    def test_penjaganya_benar_benar_menggigit(self) -> None:
        """Penjaga docstring di atas rumit, dan penjaga rumit yang salah lulus
        atas apa pun. Ini membuktikan ia masih menolak kode yang seharusnya
        ditolak - tanpa perlu mengotori paketnya."""
        contoh = ast.parse('"""Docstring menyebut LONG."""\nx = "LONG"\n')

        badan = contoh.body
        docstring = {id(badan[0].value)} if isinstance(badan[0], ast.Expr) else set()
        nilai = [
            n.value
            for n in ast.walk(contoh)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and id(n) not in docstring
        ]

        assert nilai == ["LONG"]


class TestLabelMelekat:
    """Bagian 16.1: SIMULATION EVIDENCE, bukan FACT."""

    def test_label_ada_di_keluaran(self) -> None:
        assert _bukti().to_dict()["label"] == LABEL_BUKTI

    def test_label_properti_bukan_bidang(self) -> None:
        """Bidang yang bisa diisi bisa diisi salah.

        Jenis lemparannya tidak diikat: `frozen=True` bersama `slots=True`
        menghasilkan `TypeError` untuk atribut non-bidang, bukan
        `FrozenInstanceError` seperti untuk bidang biasa. Yang dituntut pasal
        ini bukan jenis galatnya melainkan bahwa labelnya tidak bisa berubah -
        jadi itu yang diperiksa, termasuk setelah percobaannya gagal.
        """
        b = _bukti()

        with pytest.raises((AttributeError, TypeError)):
            b.label = "FACT"  # type: ignore[misc]

        assert b.label == LABEL_BUKTI
        assert b.to_dict()["label"] == LABEL_BUKTI

    def test_tiap_skenario_juga_berlabel(self) -> None:
        for s in _bukti().to_dict()["skenario"]:
            assert s["label"] == LABEL_BUKTI

    def test_tidak_pernah_menyebut_dirinya_fakta(self) -> None:
        import json

        teks = json.dumps(_bukti().to_dict()).upper()

        assert "GUARANTEED" not in teks
        assert "SIMULATION EVIDENCE" in teks


class TestGabunganInternalDanEksternal:
    def test_degraded_hanya_menyisakan_internal(self) -> None:
        """Bagian 16.12: mesin internal tetap jalan."""
        b = _bukti()

        assert len(b.skenario) == len(_internal())
        assert b.status_eksternal is StatusSimulasi.DEGRADED

    def test_ok_menambahkan_skenario_eksternal(self) -> None:
        b = _bukti(
            HasilAdapter(status=StatusSimulasi.OK, skenario=(_eksternal(),))
        )

        assert len(b.skenario) == len(_internal()) + 1
        assert any(s.nama == "MiroFish Alternative" for s in b.skenario)

    def test_timeout_membuang_hasilnya(self) -> None:
        """Bagian 16.13: jangan menggunakan hasil yang sudah stale - walau
        adapternya terlanjur membawanya."""
        b = _bukti(
            HasilAdapter(
                status=StatusSimulasi.TIMEOUT, skenario=(_eksternal(),)
            )
        )

        assert len(b.skenario) == len(_internal())

    def test_perbandingan_dihitung_atas_gabungan(self) -> None:
        """Dua himpunan yang dibandingkan terpisah menghasilkan dua "teratas"
        yang bisa bertentangan (bagian 16.9)."""
        b = _bukti(
            HasilAdapter(status=StatusSimulasi.OK, skenario=(_eksternal(),))
        )

        assert b.perbandingan.jumlah == len(b.skenario)

    def test_risiko_eksternal_ikut_terbaca(self) -> None:
        """Skenario eksternal berisiko HIGH tidak boleh hilang dari rangkuman
        hanya karena ia datang dari luar."""
        b = _bukti(
            HasilAdapter(status=StatusSimulasi.OK, skenario=(_eksternal(),))
        )

        assert b.perbandingan.risiko == "HIGH"

    def test_status_eksternal_selalu_dilaporkan(self) -> None:
        """Bukti yang tidak menyebut apakah MiroFish ikut atau tidak membuat
        dua keluaran yang sangat berbeda terlihat identik."""
        for status in StatusSimulasi:
            b = _bukti(HasilAdapter(status=status, catatan="c"))

            assert b.to_dict()["simulasi_eksternal"]["status"] == status.value
