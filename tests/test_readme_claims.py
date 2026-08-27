"""README tidak boleh menjanjikan kode yang tidak ada (SPEC 4, 49).

README.md pernah berbunyi: "Intervals a provider does not publish (Binance spot
has no 10m ...) are built by exact arithmetic from smaller bars ... and are
labelled as derived." Tidak ada satu pun kode yang melakukannya.
:mod:`aruna.data.resample` lengkap, di-test, dan punya NOL pemanggil di
``src/``; permintaan 10m ditolak keras, dan nol baris ``candles`` membawa
source ``:resampled``. Operator yang membaca kalimat itu akan percaya bar 10m
tersedia secara turunan.

Yang dikunci di sini adalah invarian dua arah, bukan sekadar teks:

* selama ``resample_candles`` tidak dipanggil dari ``src/``, README dilarang
  memuat kalimat yang sudah dicabut itu;
* begitu ada yang benar-benar menyambungkannya, test ini merah dan menyuruh
  README diperbarui - karena saat itu klaimnya justru menjadi benar dan wajib
  dinyatakan, termasuk penandaan turunannya.

Ini kelas cacat yang berulang di proyek ini: kode ditulis, di-test unit, tidak
pernah tercapai jalur hidup - lalu teks operator terlanjur bicara seolah sudah.
"""

from __future__ import annotations

import re

from aruna.core.config import PROJECT_ROOT
from aruna.core.enums import Horizon
from aruna.signals.outcome import sampling_intervals

README = PROJECT_ROOT / "README.md"

#: Modul yang mendefinisikan resampling; ia tidak dihitung sebagai pemanggil
#: dirinya sendiri.
_RESAMPLE_MODULE = PROJECT_ROOT / "src" / "aruna" / "data" / "resample.py"

#: Import atau pemanggilan sungguhan - bukan sekadar penyebutan nama modul di
#: dalam komentar, karena beberapa komentar memang menjelaskan ketiadaannya.
_CALL_PATTERN = re.compile(
    r"(from\s+aruna\.data\.resample\s+import"
    r"|from\s+aruna\.data\s+import\s+resample"
    r"|import\s+aruna\.data\.resample"
    r"|resample_candles\s*\()"
)

#: Kalimat yang dicabut, dalam bentuk yang pernah benar-benar tertulis.
_KLAIM_DICABUT = (
    "built by exact arithmetic from smaller bars",
    "is resampled from 1m",
    "di-resample dari 1m",
)


def _readme_satu_baris() -> str:
    """README dengan spasi diratakan.

    Wajib: kalimat yang dicabut dulu terpotong pembungkus baris di tengah frasa
    ("...from smaller\\nbars..."), jadi pencarian substring mentah akan lolos
    persis pada teks yang menjadi temuannya.
    """
    return re.sub(r"\s+", " ", README.read_text(encoding="utf-8"))


def _pemanggil_resample() -> list[str]:
    hits: list[str] = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        if path == _RESAMPLE_MODULE:
            continue
        if _CALL_PATTERN.search(path.read_text(encoding="utf-8")):
            hits.append(str(path.relative_to(PROJECT_ROOT)))
    return hits


class TestKlaimResamplingDiReadme:
    def test_pemanggil_resample_hanya_xau(self) -> None:
        """README menyebut SATU pemanggil; kalau bertambah, README kedaluwarsa.

        Sampai 2026-08-27 tes ini menuntut nol pemanggil dan README berkata
        "nothing calls it". `aruna.xau.timeframes` kini memanggilnya untuk
        merakit M15/H1/H4 dari M5, jadi klaimnya berubah - bukan dilonggarkan.
        Yang dijaga tetap sama: tiap pemanggil harus tertulis di README lengkap
        dengan akibatnya pada penandaan sumber turunan.

        Pemanggil KEDUA yang muncul tanpa memperbarui README akan membuat
        kalimat "every XAU bar above M5 carries a resampled source" jadi
        setengah benar - dan setengah benar di README adalah yang membuat
        paragraf ini ditulis ulang dua kali sebelumnya.
        """
        pemanggil = _pemanggil_resample()
        assert len(pemanggil) == 1, f"README menyebut satu pemanggil, ada: {pemanggil}"
        assert pemanggil[0].replace("\\", "/") == "src/aruna/xau/timeframes.py"

    def test_readme_menyebut_xau_sebagai_pemanggilnya(self) -> None:
        teks = _readme_satu_baris()
        assert "aruna/xau/timeframes.py" in teks
        assert "nothing calls it" not in teks

    def test_readme_tidak_menjanjikan_interval_turunan(self) -> None:
        teks = _readme_satu_baris()
        hidup = _pemanggil_resample()
        for klaim in _KLAIM_DICABUT:
            assert klaim not in teks, (
                f"README mengklaim {klaim!r} sementara pemanggil resample = {hidup}"
            )

    def test_horizon_10m_memang_tidak_butuh_bar_turunan(self) -> None:
        """Alasan kenapa mencabut klaimnya cukup, bukan menyambung kodenya.

        Prediksi 10m dinilai dari seri 1m yang memang tersimpan; tidak ada
        konsumen yang menunggu bar 10m turunan.
        """
        assert sampling_intervals(Horizon.M10) == (Horizon.M1, Horizon.M10)
        assert "`sampling_intervals(M10)` is `(1m, 10m)`" in _readme_satu_baris()

    def test_readme_menyebut_akibat_turunannya_apa_adanya(self) -> None:
        """Punya pemanggil berarti ada AKIBAT, dan akibatnya harus tertulis.

        Sebelum 2026-08-27 kalimatnya "no caller anywhere in `src/`" dan yang
        dijaga adalah kejujuran soal nol. Sekarang ada satu pemanggil, jadi
        yang dijaga adalah kejujuran soal konsekuensinya: bar XAU di atas M5
        BUKAN bar yang diterbitkan venue, dan pembaca README harus tahu itu
        tanpa membuka kodenya.
        """
        teks = _readme_satu_baris()
        assert "every XAU bar above M5 carries a resampled source" in teks
        assert "Crypto and IDX still call it from nowhere" in teks
