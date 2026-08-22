"""Kondisi pasar yang membuat analisis tidak bisa dipercaya (PASAL 11.8).

Modul ini mengukur hal-hal yang mirip dengan pemindai cepat, dan pertanyaannya
berlawanan. Pemindai bertanya **"ada yang bergerak, layak dilihat?"** - itu
undangan untuk menganalisis. Di sini pertanyaannya **"kondisinya begitu tidak
normal sampai analisis di atasnya tidak berarti?"** - itu alasan untuk menolak.

Konsekuensinya langsung: **ambang di sini harus lebih tinggi daripada ambang
pemindai.** Kalau sama, setiap peristiwa yang layak dilihat sekaligus menjadi
alasan untuk tidak melihatnya, dan ARUNA diam selamanya sambil terlihat
bekerja. Hubungan itu diuji, bukan sekadar ditulis di komentar.

Yang dicari bukan "pergerakan besar". Pergerakan besar adalah pasar yang
sedang bekerja. Yang dicari adalah tanda bahwa **datanya tidak lagi
menggambarkan pasar yang sama**: volume lima belas kali garis dasarnya
biasanya berarti listing, peretasan, atau penghentian perdagangan - dan setiap
indikator yang dihitung dari garis dasar sebelumnya kehilangan artinya, bukan
menjadi lebih tajam.

**Ketiadaan pengukuran BUKAN anomali.** Ini berbeda dari PASAL 11.7 dengan
sengaja, dan perbedaannya prinsipil:

* 11.7 bertanya *"buktikan datanya segar"* - yang tidak bisa dibuktikan
  ditolak, karena menerbitkan signal di atas data yang tidak diketahui
  umurnya persis yang pasal itu larang;
* 11.8 bertanya *"apakah kami mendeteksi sesuatu yang salah"* - dan tidak
  mendeteksi apa pun karena tidak ada yang diukur bukan deteksi.

Menyamakan keduanya akan membuat pasar spot yang tidak menyediakan kedalaman
buku dianggap anomali selamanya.

**ARUNA MENGANALISIS SAJA.** Yang ditahan di sini adalah penerbitan analisis,
bukan order - tidak ada order yang pernah dikirim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Ambang anomali, semuanya kelipatan terhadap garis dasar aset itu sendiri.
#:
#: Angka-angka ini sengaja jauh di atas ambang pemindai (volume 3,0x,
#: volatilitas 2,5x). Pemindai menandai "layak dilihat"; di sini yang ditandai
#: adalah "garis dasarnya sudah tidak berlaku".
VOLUME_ANOMALY = 10.0
RANGE_ANOMALY = 6.0
GAP_ANOMALY = 3.0

#: Spread selebar ini (basis point) berarti buku pesanan sedang bolong. Angka
#: mutlak, bukan kelipatan: dua puluh basis point adalah dua puluh basis point
#: di aset mana pun, dan garis dasar spread tidak tersimpan per aset.
SPREAD_ANOMALY_BPS = 50.0


class AnomalyKind(StrEnum):
    """Nilainya data - jangan diterjemahkan."""

    VOLUME_SPIKE = "VOLUME_SPIKE"
    RANGE_SPIKE = "RANGE_SPIKE"
    PRICE_GAP = "PRICE_GAP"
    SPREAD_BLOWOUT = "SPREAD_BLOWOUT"
    DATA_QUALITY = "DATA_QUALITY"


@dataclass(frozen=True, slots=True)
class Anomaly:
    """Satu kondisi abnormal, beserta angka yang melahirkannya.

    ``measured`` dan ``threshold`` disimpan berpasangan supaya klaimnya bisa
    dibantah. "Volume 14,2x garis dasar, ambang 10,0x" adalah pernyataan yang
    bisa diperiksa; "anomali terdeteksi" tidak.
    """

    kind: AnomalyKind
    measured: float
    threshold: float
    detail: str = ""

    @property
    def severity(self) -> float:
        """Berapa kali melewati ambangnya sendiri."""
        return self.measured / self.threshold if self.threshold else self.measured

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "measured": round(self.measured, 4),
            "threshold": self.threshold,
            "severity": round(self.severity, 3),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class AnomalyReport:
    """Apa yang terdeteksi, dan apa yang memang tidak bisa diperiksa."""

    anomalies: tuple[Anomaly, ...] = field(default_factory=tuple)
    #: Pemeriksaan yang tidak bisa dijalankan karena datanya tidak ada.
    #:
    #: Dicatat, dan sengaja TIDAK dihitung sebagai anomali. Yang tidak diukur
    #: bukan bukti bahwa ada yang salah - ia hanya berarti tidak ada yang tahu,
    #: dan pasal ini bertanya "apakah kami mendeteksi sesuatu", bukan
    #: "buktikan tidak ada apa-apa".
    unchecked: tuple[str, ...] = field(default_factory=tuple)

    @property
    def detected(self) -> bool:
        return bool(self.anomalies)

    @property
    def worst(self) -> Anomaly | None:
        return max(self.anomalies, key=lambda a: a.severity, default=None)

    def summary(self) -> str:
        if not self.anomalies:
            return "tidak ada anomali terdeteksi"
        return "; ".join(
            f"{a.kind.value} {a.measured:.2f} (ambang {a.threshold:.2f})"
            for a in self.anomalies
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "unchecked": list(self.unchecked),
            "summary": self.summary(),
        }


def _field(bar: Any, name: str) -> Any:
    """Baca satu kolom bar, baik ia objek maupun baris database.

    Repository mengembalikan ``dict``; analisis mengembalikan dataclass. Fungsi
    ini menerima keduanya supaya pemanggil tidak perlu membungkus - dan yang
    lebih penting, supaya pemeriksaan ini tidak diam-diam mati saat dipasang
    ke sumber yang bentuknya berbeda.
    """
    if isinstance(bar, dict):
        return bar.get(name)
    return getattr(bar, name, None)


def _mean(values: Any) -> float | None:
    seq = [float(v) for v in values if v is not None]
    return sum(seq) / len(seq) if seq else None


def detect(
    *,
    bars: Any = None,
    state: Any = None,
    atr: float | None = None,
    volume_threshold: float = VOLUME_ANOMALY,
    range_threshold: float = RANGE_ANOMALY,
    gap_threshold: float = GAP_ANOMALY,
    spread_bps_threshold: float = SPREAD_ANOMALY_BPS,
) -> AnomalyReport:
    """Periksa kondisi abnormal dari bar dan keadaan venue.

    ``bars`` diurutkan lama ke baru; yang terakhir adalah bar yang sedang
    dinilai, dan sisanya garis dasarnya. Garis dasar dihitung **tanpa** bar
    terakhir - memasukkannya membuat lonjakan mengangkat garis dasarnya
    sendiri, dan lonjakan terbesar justru paling banyak menyamarkan dirinya.
    """
    anomalies: list[Anomaly] = []
    unchecked: list[str] = []

    rows = list(bars or ())
    if len(rows) >= 3:
        terakhir = rows[-1]
        dasar = rows[:-1]

        volumes = [_field(b, "volume") for b in dasar]
        rata = _mean(volumes)
        vol = _field(terakhir, "volume")
        if rata and rata > 0 and vol is not None:
            kelipatan = float(vol) / rata
            if kelipatan >= volume_threshold:
                anomalies.append(Anomaly(
                    AnomalyKind.VOLUME_SPIKE, kelipatan, volume_threshold,
                    detail=f"volume {kelipatan:.1f}x rata-rata {len(dasar)} bar",
                ))
        else:
            unchecked.append("volume")

        tinggi = _field(terakhir, "high")
        rendah = _field(terakhir, "low")
        if atr and atr > 0 and tinggi is not None and rendah is not None:
            rentang = abs(float(tinggi) - float(rendah))
            kelipatan = rentang / atr
            if kelipatan >= range_threshold:
                anomalies.append(Anomaly(
                    AnomalyKind.RANGE_SPIKE, kelipatan, range_threshold,
                    detail=f"rentang bar {kelipatan:.1f}x ATR",
                ))
        else:
            unchecked.append("range")

        tutup_lalu = _field(rows[-2], "close")
        buka = _field(terakhir, "open")
        if atr and atr > 0 and tutup_lalu is not None and buka is not None:
            celah = abs(float(buka) - float(tutup_lalu))
            kelipatan = celah / atr
            if kelipatan >= gap_threshold:
                anomalies.append(Anomaly(
                    AnomalyKind.PRICE_GAP, kelipatan, gap_threshold,
                    detail=f"celah antar-bar {kelipatan:.1f}x ATR",
                ))
        else:
            unchecked.append("gap")
    else:
        unchecked.extend(("volume", "range", "gap"))

    if state is not None:
        spread = getattr(state, "spread_bps", None)
        if spread is None:
            unchecked.append("spread")
        elif float(spread) >= spread_bps_threshold:
            anomalies.append(Anomaly(
                AnomalyKind.SPREAD_BLOWOUT, float(spread), spread_bps_threshold,
                detail=f"spread {float(spread):.1f} bps",
            ))

        quality = str(getattr(state, "data_quality", "OK") or "OK")
        if quality != "OK":
            # Dicatat sebagai anomali DAN sudah memblokir lewat PASAL 11.7.
            # Dua lapis yang sengaja: yang satu menolak menerbitkan, yang lain
            # menjelaskan kenapa saat autopsi membaca catatannya nanti.
            anomalies.append(Anomaly(
                AnomalyKind.DATA_QUALITY, 1.0, 1.0,
                detail=f"kualitas data {quality}",
            ))
    else:
        unchecked.extend(("spread", "data_quality"))

    return AnomalyReport(tuple(anomalies), tuple(unchecked))


__all__ = [
    "GAP_ANOMALY",
    "RANGE_ANOMALY",
    "SPREAD_ANOMALY_BPS",
    "VOLUME_ANOMALY",
    "Anomaly",
    "AnomalyKind",
    "AnomalyReport",
    "detect",
]
