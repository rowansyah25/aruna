"""Mengubah keyakinan mentah menjadi keyakinan yang sesuai kenyataan.

`calibration.py` sudah MENGUKUR kalibrasi dengan benar sejak lama: pita,
akurasi terealisasi, selisih, dan ambang sampel. Yang tidak pernah ada adalah
**pemetanya** - dan tanpa peta, pengukuran itu hanya menghasilkan kalimat
peringatan yang tidak mengubah satu pun angka yang sampai ke operator.

Terukur 2026-08-21, dan inilah yang membuat modul ini perlu ada:

===== ================= ============
pita  keyakinan dinyatakan  menang
===== ================= ============
<50%  654 keputusan     55,2%
≥90%  903 keputusan     47,7%
===== ================= ============

Makin yakin ARUNA, makin sering ia salah. Bagian 9 spec mengejanya sebagai
syarat: keyakinan 80% harus berarti keberhasilan mendekati 80%.

**Tiga hal yang modul ini sengaja TIDAK lakukan.**

Ia tidak menyentuh arah keputusan. LONG/SHORT/NO SIGNAL milik Phase 14; yang
di sini hanya angkanya. Kalibrator yang bisa membalik arah bukan kalibrator
melainkan mesin keputusan kedua, dan tidak seorang pun memintanya.

Ia tidak meratakan peta yang terbalik. Regresi isotonik akan membuat petanya
naik rapi dan menyembunyikan justru temuan yang paling perlu diketahui - bahwa
keyakinan sistem berkorelasi terbalik dengan kebenaran. Yang dilakukan
:attr:`Kalibrator.monoton` adalah menandainya.

Ia tidak menyesuaikan apa pun yang sampelnya tipis (bagian 10). Pita dengan
sembilan belas pengamatan tidak tahu apa-apa tentang akurasinya, dan
menyesuaikan berdasarkan itu adalah mengarang.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from aruna.learning.calibration import MIN_BUCKET_SAMPLE, Bucket, CalibrationReport

__all__ = ["Kalibrator", "Terkalibrasi"]


@dataclass(frozen=True, slots=True)
class Terkalibrasi:
    """Satu keyakinan sesudah dipetakan, berikut alasannya.

    ``mentah`` dibawa serta, bukan dibuang: pengukuran berikutnya harus
    mengukur keluaran model, bukan keluaran kalibrator. Kalibrasi yang menimpa
    nilai mentahnya akan mengukur dirinya sendiri pada putaran berikut dan
    melaporkan bahwa semuanya baik-baik saja.
    """

    mentah: float
    nilai: float
    disesuaikan: bool
    alasan: str
    pita: str | None = None


class Kalibrator:
    """Peta dari keyakinan yang dinyatakan ke keyakinan yang terbukti."""

    def __init__(self, laporan: CalibrationReport | None) -> None:
        self._laporan = laporan
        self._pita: tuple[Bucket, ...] = (
            tuple(laporan.buckets) if laporan is not None else ()
        )

    @property
    def monoton(self) -> bool:
        """Apakah akurasi terukur naik seiring keyakinan yang dinyatakan.

        ``False`` adalah temuan, bukan kegagalan modul ini: ia berarti pita
        yang lebih yakin justru lebih sering salah. Pita yang sampelnya belum
        cukup dilewati - ketiadaan angka bukan penurunan.
        """
        terukur = [
            b.accuracy for b in sorted(self._pita, key=lambda b: b.low)
            if b.accuracy is not None
        ]
        return all(a <= b for a, b in pairwise(terukur))

    def kalibrasi(self, mentah: float) -> Terkalibrasi:
        """Keyakinan yang layak diterbitkan untuk `mentah`.

        Memulangkan nilai apa adanya - dengan alasannya - setiap kali tidak ada
        dasar untuk mengubahnya. Diam berarti belum diukur, bukan sudah benar.
        """
        if self._laporan is None or not self._pita:
            return Terkalibrasi(
                mentah=mentah, nilai=mentah, disesuaikan=False,
                alasan="kalibrasi belum pernah diukur",
            )

        pita = self._pita_untuk(mentah)
        if pita is None:
            return Terkalibrasi(
                mentah=mentah, nilai=mentah, disesuaikan=False,
                alasan=f"tidak ada pita kalibrasi yang mencakup {mentah:.0%}",
            )

        akurasi = pita.accuracy
        if akurasi is None:
            kurang = max(0, MIN_BUCKET_SAMPLE - pita.predictions)
            return Terkalibrasi(
                mentah=mentah, nilai=mentah, disesuaikan=False,
                pita=pita.label,
                alasan=(
                    f"sampel pita {pita.label} belum cukup "
                    f"({pita.predictions}, kurang {kurang})"
                ),
            )

        nilai = min(1.0, max(0.0, float(akurasi)))
        return Terkalibrasi(
            mentah=mentah,
            nilai=nilai,
            disesuaikan=True,
            pita=pita.label,
            alasan=(
                f"pita {pita.label}: {pita.correct} benar dari "
                f"{pita.predictions} = {akurasi:.1%}"
            ),
        )

    def _pita_untuk(self, nilai: float) -> Bucket | None:
        """Pita tempat `nilai` jatuh.

        Batas atas inklusif hanya pada pita terakhir, mengikuti
        ``BUCKET_EDGES``: tanpa itu keyakinan tepat di plafon tidak punya pita
        sama sekali, dan plafon justru nilai yang paling sering muncul.
        """
        urut = sorted(self._pita, key=lambda b: b.low)
        for i, b in enumerate(urut):
            terakhir = i == len(urut) - 1
            if b.low <= nilai < b.high or (terakhir and nilai == b.high):
                return b
        return None
