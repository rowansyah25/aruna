"""Mengubah pilihan router menjadi baris performa (bagian 17.36 - 17.37).

**Inilah yang membuat Task 3 berhenti menunggu.** `performa_rezim` menyaring
baris ``strategy_performance`` yang berlabel ``router-1``, dan sampai modul ini
ada tidak seorang pun menulisnya - jadi ia memulangkan ``None`` selamanya dan
seluruh separuh performa-dan-risiko `kecocokan.nilai` menganggur.

Apa yang angka ini SUNGGUH ukur
================================

**Ia bukan edge sebuah strategi, dan mengiranya begitu akan menyesatkan.**
ARUNA menganalisis saja; tidak ada order yang dikirim, jadi tidak ada perdagangan
yang benar-benar dihasilkan oleh sebuah strategi. Jalur sinyal pun tidak
membaca pilihan router - router memberi rekomendasi, bukan perintah.

Yang diukur karena itu **observasional**: ketika router merekomendasikan STR-001
untuk sebuah aset pada sebuah bar, bagaimana sinyal ARUNA di jendela itu
akhirnya berakhir. Pertanyaan yang sah, terjawab, dan justru yang dibutuhkan
router untuk tahu apakah rekomendasinya berkorelasi dengan hasil yang baik.

**Tapi ia sudah TIDAK melingkar, dan di situ bedanya dengan baris
``learn-12.0``.** Yang lama memakai ``classify()``, yang menurunkan strategi
DARI rezim lewat peta satu-ke-satu - sehingga ``regime=X`` dan ``regime=ALL``
adalah himpunan baris yang sama persis (terukur: STR-005 188W/726L di keduanya).
Di sini kodenya datang dari ``router_pilihan.champion``, yang dipilih memakai
keyakinan, stabilitas, dan pelipatan keluarga - jadi STR-001 bisa menjadi
champion di ``TRENDING``, ``TRENDING_BULLISH``, dan ``TRENDING_BEARISH``, dan
pasangan (strategi, rezim) akhirnya membawa keterangan yang berbeda-beda.

Attribusinya
============

Sebuah sinyal dihubungkan ke pilihan router untuk aset yang sama pada bar yang
sama, dan **hanya yang terkunci SESUDAH pilihannya tercatat**. Urutan itu bukan
kerapian: pilihan yang dicatat sesudah sinyalnya terkunci tidak menjelaskan
apa pun tentang sinyal itu, dan memakainya adalah look-ahead yang bagian 17.43
larang.

Drawdown-nya memakai :func:`~aruna.learning.adaptive.drawdown` yang sama dengan
Phase 12 - dua rumus drawdown adalah dua angka yang harus tetap sepakat
selamanya.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from aruna.learning.adaptive import StrategySlice, drawdown
from aruna.learning.evidence import Evidence
from aruna.router.label import VERSI_ROUTER

__all__ = ["REZIM_SEMUA", "susun_slice"]


#: Nama irisan yang menampung seluruh rezim sebuah strategi.
#:
#: Ikut ditulis - dan **di sini ia berarti**, tidak seperti pada baris
#: ``learn-12.0``. Di sana ``regime=ALL`` selalu identik dengan ``regime=X``
#: karena tiap strategi hanya pernah dilabeli pada satu rezim. Begitu router
#: yang memilih, sebuah strategi benar-benar terpakai di beberapa rezim, dan
#: ``ALL`` menjadi jumlah yang berbeda dari tiap bagiannya.
REZIM_SEMUA = "ALL"


def susun_slice(baris: Any) -> tuple[StrategySlice, ...]:
    """Baris performa per (strategi, rezim), dari pilihan router yang tuntas.

    Tiap baris masukan mewakili satu sinyal yang sudah selesai, berikut
    champion dan rezim yang berlaku saat ia terkunci.

    Diurutkan menurut waktu penyelesaian **sebelum** drawdown dihitung.
    Drawdown atas urutan yang salah adalah angka yang terlihat benar dan tidak
    berarti apa-apa - lihat :func:`~aruna.learning.adaptive.drawdown`.
    """
    berurut = sorted(baris, key=_kunci_waktu)

    ember: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in berurut:
        kode = str(r.get("champion") or "").strip()
        if not kode:
            # Baris tanpa champion adalah penolakan router. Ia punya nilainya
            # sendiri - `router_pilihan.kode_kosong` menghitungnya - tapi ia
            # bukan performa sebuah strategi, dan memasukkannya ke sini akan
            # mengaitkan hasil sinyal kepada strategi yang tidak dipilih.
            continue
        regime = str(r.get("regime") or "UNKNOWN").strip().upper()
        ember.setdefault((kode, REZIM_SEMUA), []).append(r)
        ember.setdefault((kode, regime), []).append(r)

    hasil: list[StrategySlice] = []
    for (kode, regime), anggota in ember.items():
        bukti = Evidence(
            wins=sum(1 for a in anggota if a.get("result") == "WIN"),
            losses=sum(1 for a in anggota if a.get("result") == "LOSS"),
        )
        if not bukti.total:
            # Sinyal yang belum punya hasil menang/kalah - WAIT yang tidak
            # pernah terselesaikan, atau paper trade yang belum ditutup.
            # Menuliskannya sebagai baris bersampel nol membuat `performa_rezim`
            # membagi dengan nol.
            continue
        pnls = [_desimal(a.get("net_pnl")) for a in anggota]
        hasil.append(
            StrategySlice(
                strategy_code=kode,
                slice_key=f"{kode}|regime={regime}",
                dimensions={"regime": regime},
                evidence=bukti,
                net_pnl=sum(pnls, Decimal(0)),
                max_drawdown=drawdown(pnls),
            )
        )

    hasil.sort(key=lambda s: (-s.evidence.total, s.slice_key))
    return tuple(hasil)


def baris_simpan(
    irisan: tuple[StrategySlice, ...], *, pada: datetime
) -> list[dict[str, Any]]:
    """Bentuk yang diterima ``save_strategy_performance``.

    ``model_version`` selalu :data:`~aruna.router.label.VERSI_ROUTER`, dan
    itulah satu-satunya hal yang memisahkan baris ini dari baris ``learn-12.0``
    di tabel yang sama. Kunci uniknya ``(strategy_code, slice_key,
    model_version)``, jadi keduanya berdiri berdampingan tanpa saling menimpa -
    dan `dilabeli_router` yang memilih mana yang router pakai.
    """
    return [
        {
            "strategy_code": s.strategy_code,
            "slice_key": s.slice_key,
            "dimensions": s.dimensions,
            "wins": s.evidence.wins,
            "losses": s.evidence.losses,
            "sample_size": s.evidence.total,
            # Bentuknya mengikuti Phase 12 persis - pembulatan, `evidence`
            # sebagai TINGKAT bukan jumlah, dan `win_rate` yang boleh `None`.
            # Dua penulis ke satu tabel yang mengisi kolomnya dengan arti
            # berbeda menghasilkan kolom yang tidak bisa dibaca siapa pun.
            "win_rate": (
                None if s.evidence.win_rate is None
                else round(s.evidence.win_rate, 5)
            ),
            "ci_low": round(s.evidence.interval[0], 5),
            "ci_high": round(s.evidence.interval[1], 5),
            "evidence": s.evidence.level.value,
            "net_pnl": s.net_pnl,
            "max_drawdown": s.max_drawdown,
            "model_version": VERSI_ROUTER,
            "computed_at": pada,
        }
        for s in irisan
    ]


def _kunci_waktu(r: Any) -> datetime:
    saat = r.get("resolved_at")
    return saat if isinstance(saat, datetime) else datetime.min


def _desimal(nilai: Any) -> Decimal:
    if nilai is None:
        return Decimal(0)
    try:
        return Decimal(str(nilai))
    except (InvalidOperation, ValueError):
        return Decimal(0)
