"""Setiap pustaka pihak ketiga yang diimpor `src/` harus dideklarasikan.

**Bug VPS, 2026-08-23.** `websockets` diimpor `data/crypto/stream.py` sejak lama
dan tidak pernah ada di `pyproject.toml`. Di mesin pengembang ia hadir diam-diam
sebagai dependensi transitif `yfinance` - yang juga tidak dideklarasikan. Suite
hijau, ruff bersih, dan aliran harga real-time mati begitu dipasang di mesin
bersih.

Gejalanya yang membuatnya mahal: bukan "impor gagal" saat start, melainkan
`stream.disconnected` berulang tiap belasan detik - persis seperti ISP yang
memblokir Binance, yang memang pernah terjadi di mesin ini.

Test ini membaca impor lewat **AST**, bukan pencarian teks: `import x` di dalam
docstring atau komentar bukan impor, dan pencarian teks sudah tiga kali
tersandung prosa yang menjelaskan larangan di proyek ini.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

AKAR = Path(__file__).resolve().parent.parent
SUMBER = AKAR / "src" / "aruna"

#: Nama impor yang berbeda dari nama distribusinya di PyPI.
NAMA_DISTRIBUSI = {
    "telegram": "python-telegram-bot",
    "dotenv": "python-dotenv",
    "yaml": "pyyaml",
}


def _normal(nama: str) -> str:
    """PEP 503: `-`, `_`, dan `.` setara, dan huruf besar tidak berarti."""
    keluar = nama.strip().lower()
    for tanda in "_.":
        keluar = keluar.replace(tanda, "-")
    return keluar


def _nama_paket(spesifikasi: str) -> str:
    """``pydantic-settings>=2.6`` menjadi ``pydantic-settings``."""
    for pemisah in (">", "<", "=", "!", "~", "[", ";", " "):
        spesifikasi = spesifikasi.split(pemisah)[0]
    return _normal(spesifikasi)


def _dideklarasikan() -> set[str]:
    tom = tomllib.loads((AKAR / "pyproject.toml").read_text(encoding="utf-8"))
    proyek = tom["project"]
    keluar = {_nama_paket(b) for b in proyek.get("dependencies", [])}
    for grup in proyek.get("optional-dependencies", {}).values():
        keluar.update(_nama_paket(b) for b in grup)
    return keluar


def _diimpor() -> dict[str, set[str]]:
    """Modul teratas pihak ketiga -> berkas yang mengimpornya."""
    keluar: dict[str, set[str]] = {}
    for berkas in SUMBER.rglob("*.py"):
        pohon = ast.parse(berkas.read_text(encoding="utf-8"))
        modul: set[str] = set()
        for n in ast.walk(pohon):
            if isinstance(n, ast.Import):
                modul.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                modul.add(n.module.split(".")[0])
        for m in modul:
            if m in sys.stdlib_module_names or m == "aruna" or m.startswith("_"):
                continue
            keluar.setdefault(m, set()).add(str(berkas.relative_to(AKAR)))
    return keluar


class TestSemuaImporDideklarasikan:
    def test_tidak_ada_yang_hadir_karena_kebetulan(self) -> None:
        """Dependensi transitif adalah kebetulan, bukan janji. `yfinance` boleh
        berhenti menarik `websockets` besok tanpa memberi tahu siapa pun."""
        dideklarasikan = _dideklarasikan()
        hilang = {
            m: berkas
            for m, berkas in _diimpor().items()
            if _normal(NAMA_DISTRIBUSI.get(m, m)) not in dideklarasikan
        }

        assert not hilang, (
            "diimpor `src/aruna` tapi tidak ada di pyproject.toml:\n"
            + "\n".join(
                f"  {m}  <- {', '.join(sorted(b))}" for m, b in sorted(hilang.items())
            )
        )

    def test_websockets_dideklarasikan_bukan_diwarisi(self) -> None:
        """Yang benar-benar mati di VPS. Disebut namanya supaya penghapusan
        tak sengaja gagal keras, bukan gagal enam jam kemudian di produksi."""
        assert "websockets" in _dideklarasikan()

    def test_yfinance_opsional_dengan_sengaja(self) -> None:
        """`fundamental/yahoo.py` mengimpornya di DALAM fungsi, dan pemasangan
        yang hanya menganalisis crypto tidak membutuhkannya - ia menarik pandas
        dan numpy, yang berat untuk VPS kecil. Jadi ia opsional karena
        keputusan, bukan karena lupa."""
        tom = tomllib.loads((AKAR / "pyproject.toml").read_text(encoding="utf-8"))
        wajib = {_nama_paket(b) for b in tom["project"]["dependencies"]}
        opsional = tom["project"]["optional-dependencies"]

        assert "yfinance" not in wajib
        assert any(
            "yfinance" in {_nama_paket(b) for b in grup}
            for grup in opsional.values()
        )
