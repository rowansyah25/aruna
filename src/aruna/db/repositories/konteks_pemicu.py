"""Keadaan jalur keputusan yang dibutuhkan deteksi pemicu (bagian 16.2).

**Tiga pemicu yang sebelumnya kusebut "butuh data dari jalur lain" ternyata
datanya sudah tersimpan.** Jalur keputusan menghitung regime, skor mutu, dan
selisih pendapat council setiap kali ia berjalan, lalu menuliskannya ke
``signal_snapshots`` dan ``council_sessions``. Yang tidak ada cuma pembacanya -
dan tanpa pembaca itu tiga dari tiga belas pemicu bagian 16.2 tidak pernah bisa
menyala.

Yang **tetap** tanpa sumber, dan diperiksa langsung 2026-08-22: funding rate dan
open interest tidak disimpan di tabel mana pun. ``futures_plans.funding_cost_pct``
adalah biaya turunan atas horizon sebuah rencana, bukan rate-nya; proses
``futures-loop`` mengambil keduanya dari Binance REST dan memakainya di memori
tanpa pernah menuliskannya. Likuidasi dan konflik lintas-pasar sama.

**Semuanya dibatasi umur.** Regime dari enam jam lalu bukan regime sekarang, dan
memakainya membuat pemicu menyala atas keadaan yang sudah lewat. Yang lebih tua
dari :data:`UMUR_MAKSIMUM` diperlakukan tidak terbaca - bukan diperlakukan
sebagai nol.

**Satu kueri per keperluan, bukan satu per simbol.** Fase skenario berjalan tiap
siklus atas dua puluh aset; tiga kueri per siklus bisa diterima, enam puluh
tidak.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from aruna.core.logging import get_logger
from aruna.data.crypto.symbols import to_canonical_symbol
from aruna.db.pool import Database
from aruna.db.types import to_mysql_datetime

log = get_logger(__name__)

__all__ = [
    "UMUR_MAKSIMUM",
    "KonteksKeputusan",
    "KonteksPemicuRepository",
]

#: Seberapa tua sebuah bacaan masih dianggap menggambarkan keadaan sekarang.
#:
#: Satu jam - empat bar 15m. Regime dan mutu bergerak dalam hitungan bar, bukan
#: detik, jadi bacaan satu bar lalu masih sah. Enam jam lalu tidak, dan pemicu
#: yang menyala atas keadaan yang sudah lewat lebih buruk daripada pemicu yang
#: diam.
UMUR_MAKSIMUM = timedelta(hours=1)

#: Horizon yang bacaan regime-nya dibandingkan.
#:
#: ``signal_snapshots`` memuat 15m, 1h, dan 1d bercampur. Membandingkan bacaan
#: dari timeframe berbeda menghasilkan "perubahan" yang sebenarnya cuma
#: perbedaan timeframe - dan itu menyala hampir selalu.
#:
#: Lima belas menit, sama dengan yang dipindai dan yang disimulasikan.
HORIZON_REGIME = "15m"

#: Berapa bacaan regime yang dibaca untuk memutuskan ada perubahan atau tidak.
#:
#: **Tiga, dan yang ketiga yang membuat pemicunya berarti.** Bagian 16.2 minta
#: *major* market regime change. Dengan dua bacaan, satu bar yang kebetulan
#: berbeda dari tetangganya sudah dihitung sebagai perubahan - dan classifier
#: 15m memang berkedip. Terukur di produksi 2026-08-22: `PERUBAHAN_REGIME`
#: menyala pada **empat puluh dari empat puluh sembilan** simulasi.
#:
#: Dengan tiga, perubahan hanya dilaporkan ketika keadaan SEBELUMNYA mapan -
#: dua bacaan berturut-turut yang sepakat - lalu berganti. Kedipan satu bar
#: tidak lagi lolos, dan yang lolos memang menetap.
BACAAN_REGIME = 3

#: Bacaan yang bukan regime, melainkan classifier yang mengaku tidak tahu.
#:
#: Diperlakukan **tidak terbaca**, sejajar dengan ``NULL`` - bukan sebagai
#: regime tersendiri. "RANGING -> tidak tahu" bukan pasar yang berpindah rezim;
#: itu alat ukurnya yang kehilangan pijakan, dan bagian 16.2 minta *major
#: market regime change*. Prinsipnya sudah berlaku di tempat lain: aset
#: ``scanned=False`` dilewati karena buktinya tidak cukup, bukan karena
#: pasarnya tenang.
#:
#: **Dibuang dari riwayat, bukan menekan pemicunya.** Bedanya menentukan.
#: Terukur di produksi 2026-08-22 pada 500 titik aset-bar: menekan setiap
#: peralihan yang menyentuh UNCERTAIN ikut membunuh perpindahan yang sungguhan
#: - ``BREAKOUT`` sesudah ``REVERSAL`` yang mapan, dengan satu bacaan bingung
#: di tengahnya. Dengan dibuang, riwayatnya merapat dan perpindahan itu tetap
#: terbaca. UNCERTAIN adalah 15,1% dari seluruh bacaan 15m.
#:
#: Harganya jujur: pada 11,8% titik, yang tersisa kurang dari
#: :data:`BACAAN_REGIME` dan pemicunya diam. Diam karena tidak terbaca, bukan
#: diam karena tidak ada perubahan.
TIDAK_TERBACA = "UNCERTAIN"


def _mapan(riwayat: list[str]) -> bool:
    """Apakah keadaan sebelum bacaan terbaru sudah mapan.

    Butuh tiga bacaan: yang terbaru, dan dua sebelumnya yang harus sepakat.
    Kurang dari itu berarti belum bisa dibedakan antara perubahan dan kedipan,
    dan yang tidak bisa dibedakan tidak dilaporkan.
    """
    return len(riwayat) >= BACAAN_REGIME and riwayat[1] == riwayat[2]


@dataclass(frozen=True, slots=True)
class KonteksKeputusan:
    """Bacaan terbaru untuk satu aset. ``None`` berarti tidak terbaca."""

    regime_sekarang: str | None = None
    regime_sebelumnya: str | None = None
    mutu: int | None = None
    disagreement: float | None = None
    funding_rate: Any = None
    perubahan_oi_pct: Any = None


#: Penerjemah simbol. **Satu, bukan dua.**
#:
#: Modul ini sempat punya `_kanonik` sendiri dengan empat quote hardcoded, dan
#: itu bug diam yang ditemukan 2026-08-23 lewat audit: dua tempat menerjemahkan
#: ``BTCUSDT`` menjadi ``BTC/USDT`` dengan **aturan gagal yang berbeda**.
#:
#: :func:`~aruna.data.crypto.symbols.to_canonical_symbol` memakai daftar quote
#: lengkap venue dan MELEMPAR ketika quote-nya tidak dikenal - persis supaya
#: simbol yang salah potong tidak melanjutkan perjalanannya. Salinan di sini
#: memulangkan simbol apa adanya, yang berarti simbol dengan quote di luar
#: keempatnya diam-diam tidak diterjemahkan, tidak cocok dengan baris mana pun,
#: dan pemicunya diam tanpa satu pun galat.
#:
#: Salinan itu dibuang. Yang tersisa satu sumber kebenaran, dan lemparannya
#: ditangani PER SIMBOL di :meth:`KonteksPemicuRepository._futures` - satu
#: simbol yang tidak bisa diterjemahkan tidak boleh mematikan sembilan belas
#: lainnya, dan tidak boleh lewat diam-diam.
def _kanonik(venue: str) -> str | None:
    """Bentuk kanonik, atau ``None`` kalau simbolnya tidak bisa diterjemahkan.

    ``None`` dan bukan simbol asli: simbol yang tidak diterjemahkan tidak akan
    cocok dengan baris mana pun, jadi memulangkannya apa adanya hanya menunda
    kegagalan sampai ke tempat yang jauh dari sebabnya.
    """
    try:
        return to_canonical_symbol(venue)
    except ValueError:
        return None


class KonteksPemicuRepository:
    def __init__(self, db: Database, *, metrik: Any = None) -> None:
        self._db = db
        #: Pembaca ``futures_metrics``. ``None`` mengembalikan deteksi ke tujuh
        #: pemicu tanpa funding dan open interest.
        self._metrik = metrik

    async def terbaru(self, *, sekarang: datetime) -> dict[str, KonteksKeputusan]:
        """Bacaan terbaru per simbol, dari seluruh pasar sekaligus."""
        batas = to_mysql_datetime(sekarang - UMUR_MAKSIMUM)

        regime = await self._regime(batas)
        mutu = await self._mutu(batas)
        beda = await self._disagreement(batas)
        futures = await self._futures(sekarang)

        simbol = set(regime) | set(mutu) | set(beda) | set(futures)
        keluar: dict[str, KonteksKeputusan] = {}
        for s in simbol:
            dua = regime.get(s, ())
            f = futures.get(s)
            keluar[s] = KonteksKeputusan(
                regime_sekarang=dua[0] if dua else None,
                regime_sebelumnya=dua[1] if len(dua) > 1 else None,
                mutu=mutu.get(s),
                disagreement=beda.get(s),
                funding_rate=f.funding_rate if f else None,
                perubahan_oi_pct=f.perubahan_oi_pct if f else None,
            )
        return keluar

    async def _futures(self, sekarang: datetime) -> dict[str, Any]:
        """Funding dan perubahan open interest, kalau tabelnya terbaca.

        **Simbolnya diterjemahkan.** ``futures_metrics`` memakai bentuk venue
        (``BTCUSDT``) sementara pemindai dan seluruh sisi keputusan memakai
        bentuk kanonik (``BTC/USDT``). Tanpa penerjemahan ini kedua sisi tidak
        pernah bertemu, dan pemicunya diam tanpa satu pun galat - persis bentuk
        kegagalan yang paling sulit ditemukan.
        """
        if self._metrik is None:
            return {}
        try:
            mentah = await self._metrik.terbaru(
                sekarang=sekarang, umur_maksimum=UMUR_MAKSIMUM
            )
        except Exception:
            log.warning("konteks.futures_tak_terbaca", exc_info=True)
            return {}
        keluar: dict[str, Any] = {}
        tak_terbaca: list[str] = []
        for s, v in mentah.items():
            kanonik = _kanonik(s)
            if kanonik is None:
                tak_terbaca.append(s)
                continue
            keluar[kanonik] = v

        if tak_terbaca:
            # Dicatat, bukan didiamkan. Simbol yang tidak diterjemahkan berarti
            # funding dan open interest-nya tidak pernah sampai ke pemicu, dan
            # itu terlihat persis seperti pasar yang tenang.
            log.warning(
                "konteks.simbol_tak_terterjemahkan",
                simbol=sorted(tak_terbaca)[:10],
                jumlah=len(tak_terbaca),
            )
        return keluar

    async def _regime(self, batas: Any) -> dict[str, tuple[str, ...]]:
        """Dua bacaan regime **terakhir berurutan** per simbol, pada satu horizon.

        **Dua, bukan satu.** Yang ditanyakan pemicu bagian 16.2 adalah
        *perubahan* regime, dan perubahan butuh pembanding.

        **Berurutan, bukan dua yang berbeda.** Versi pertama mengumpulkan dua
        regime BERBEDA terakhir dalam jendela satu jam, tanpa peduli kapan
        peralihannya terjadi - sehingga regime yang berubah lima puluh menit
        lalu terus menyalakan pemicu tiap siklus sampai bacaan lamanya
        kedaluwarsa. Terukur di produksi 2026-08-22: `PERUBAHAN_REGIME` menyala
        pada **lima belas dari lima belas** simulasi, dan pemicu yang menyala
        untuk seluruh aset membatalkan bagian 16.2 alih-alih memenuhinya.

        **Satu horizon.** ``signal_snapshots`` memuat 15m, 1h, dan 1d bercampur;
        membandingkan regime 15m dengan regime 1d dari baris berikutnya
        menghasilkan "perubahan" yang cuma perbedaan timeframe.

        **Yang tidak terbaca dibuang di bawah**, sebaris dengan ``NULL``, supaya
        riwayatnya merapat sebelum :func:`_mapan` melihatnya - lihat
        :data:`TIDAK_TERBACA`. Terukur di produksi 2026-08-22: 15,4% titik
        aset-bar menyala menjadi 9,4%, dan yang hilang memang bukan perpindahan
        pasar.
        """
        baris = await self._db.fetch(
            "SELECT symbol, regime, locked_at FROM signal_snapshots "
            "WHERE locked_at >= %s AND regime IS NOT NULL "
            "AND horizon_code = %s "
            "ORDER BY symbol, locked_at DESC",
            batas,
            HORIZON_REGIME,
        )
        riwayat: dict[str, list[str]] = {}
        for r in baris:
            # Dinormalkan LEBIH DULU, lalu disaring. Menyaring di SQL akan
            # memutuskan sebelum `.strip().upper()` berjalan, dan dua tempat
            # yang memutuskan hal yang sama dengan aturan berbeda adalah bug
            # yang menunggu giliran.
            regime = str(r["regime"]).strip().upper()
            if regime == TIDAK_TERBACA:
                continue
            daftar = riwayat.setdefault(r["symbol"], [])
            if len(daftar) < BACAAN_REGIME:
                daftar.append(regime)

        keluar: dict[str, tuple[str, ...]] = {}
        for simbol, v in riwayat.items():
            keluar[simbol] = (v[0], v[1]) if _mapan(v) else (v[0],)
        return keluar

    async def _mutu(self, batas: Any) -> dict[str, int]:
        baris = await self._db.fetch(
            "SELECT s.symbol, s.signal_quality FROM signal_snapshots s "
            "JOIN (SELECT symbol, MAX(locked_at) t FROM signal_snapshots "
            "      WHERE locked_at >= %s GROUP BY symbol) k "
            "  ON s.symbol = k.symbol AND s.locked_at = k.t "
            "WHERE s.signal_quality IS NOT NULL",
            batas,
        )
        return {r["symbol"]: int(r["signal_quality"]) for r in baris}

    async def _disagreement(self, batas: Any) -> dict[str, float]:
        baris = await self._db.fetch(
            "SELECT c.symbol, c.disagreement FROM council_sessions c "
            "JOIN (SELECT symbol, MAX(decided_at) t FROM council_sessions "
            "      WHERE decided_at >= %s GROUP BY symbol) k "
            "  ON c.symbol = k.symbol AND c.decided_at = k.t "
            "WHERE c.disagreement IS NOT NULL",
            batas,
        )
        return {r["symbol"]: float(r["disagreement"]) for r in baris}
