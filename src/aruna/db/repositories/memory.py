"""Pembaca dan penulis ingatan pasar (PASAL 15.2, 15.26, 15.39, 15.40).

**Satu aturan mengalahkan semua yang lain di berkas ini: tidak ada pencarian
tanpa ``as_of``.** PASAL 15.39 melarang memakai informasi masa depan saat
menilai keputusan masa lalu, dan pelanggarannya tidak meninggalkan jejak apa
pun - secara teknis tidak ada yang gagal, hasilnya cuma menjadi lebih bagus.
Sebuah memory engine yang bocor melaporkan akurasi tinggi pada backtest mana
pun, dan angkanya naik justru ketika kebocorannya makin parah.

Karena itu ``as_of`` adalah keyword **tanpa nilai bawaan**. Bawaan yang berarti
"sekarang" akan dipakai pemanggil backtest tanpa ia sadari, dan itu persis
bentuk kebocoran yang PASAL 15.40 sebut.

Tabelnya proyeksi, bukan gudang kedua - lihat ``migrations/0031``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from aruna.core.logging import get_logger
from aruna.db.pool import Database
from aruna.db.types import as_utc, to_mysql_datetime
from aruna.memory.dimensions import UNKNOWN, Dimensi
from aruna.memory.fingerprint import Sidik
from aruna.memory.lookup import simbol_pasar
from aruna.memory.record import Hasil, Mutu, mutu_dari
from aruna.memory.similarity import bandingkan
from aruna.memory.teknikal import band_funding, band_open_interest

log = get_logger("aruna.db.memory")

#: Kolom yang dibaca pencarian. Dieja, bukan `SELECT *`: kolom baru yang
#: diam-diam ikut terbawa akan mengubah bentuk dict yang dibaca lapisan murni.
_KOLOM = (
    "signal_id, symbol, timeframe, regime, risk_level, news, "
    "quality_band, liquidity_band, volatility_band, momentum_band, "
    "volume_band, trend_band, structure_band, funding_band, oi_band, "
    "arah, hasil, move_pct, "
    "cakupan, mutu, model_version, locked_at, resolved_at"
)

#: Dimensi -> kolomnya. Satu tempat yang memutuskan, dipakai penulis dan
#: pembaca: dua peta yang harus tetap sepakat adalah dua peta yang suatu saat
#: tidak, dan yang tidak sepakat di sini menghasilkan sidik jari yang ditulis
#: penuh lalu dibaca kosong.
KOLOM_DIMENSI: dict[Dimensi, str] = {
    Dimensi.REGIME: "regime",
    Dimensi.RISK_LEVEL: "risk_level",
    Dimensi.NEWS: "news",
    Dimensi.QUALITY: "quality_band",
    Dimensi.LIQUIDITY: "liquidity_band",
    Dimensi.VOLATILITY: "volatility_band",
    Dimensi.MOMENTUM: "momentum_band",
    Dimensi.VOLUME: "volume_band",
    Dimensi.TREND: "trend_band",
    Dimensi.STRUCTURE: "structure_band",
    Dimensi.FUNDING: "funding_band",
    Dimensi.OPEN_INTEREST: "oi_band",
}


def ingatan_dari_baris(row: dict[str, Any]) -> Any:
    """Satu baris ``market_memories`` menjadi :class:`Ingatan`.

    Di sini, bersebelahan dengan ``KOLOM_DIMENSI`` dan dengan penulisnya:
    salinan kedua pernah hidup di ``futures/service.py``, dan dua pembangun
    yang harus tetap sepakat adalah dua yang suatu saat tidak. Yang tidak
    sepakat di sini menghasilkan sidik jari yang ditulis penuh lalu dibaca
    kosong - tanpa satu pun error.
    """
    from aruna.core.enums import Market
    from aruna.memory.dimensions import UNKNOWN
    from aruna.memory.fingerprint import Sidik
    from aruna.memory.record import Ingatan, Mutu

    nilai = dict.fromkeys(Dimensi, UNKNOWN)
    nilai[Dimensi.ASSET] = row["symbol"]
    nilai[Dimensi.MARKET] = row.get("market_code") or Market.CRYPTO.value
    nilai[Dimensi.TIMEFRAME] = row["timeframe"]
    for d, kolom in KOLOM_DIMENSI.items():
        nilai[d] = row.get(kolom) or UNKNOWN
    return Ingatan(
        signal_id=row["signal_id"],
        sidik=Sidik(nilai=nilai),
        arah=row["arah"],
        hasil=Hasil(row["hasil"]),
        move_pct=row["move_pct"],
        locked_at=as_utc(row["locked_at"]),
        resolved_at=as_utc(row["resolved_at"]),
        model_version=row["model_version"],
        cakupan=row["cakupan"],
        mutu=Mutu(row["mutu"]),
    )


class MemoryRepository:
    def __init__(self, db: Database, *, oi_reader: Any = None) -> None:
        self._db = db
        #: Pembaca open interest (PASAL 15.5). Opsional: tanpa dia dimensinya
        #: ``UNKNOWN``, dan itu keadaan yang sah - bukan proyeksi yang gagal.
        self._oi_reader = oi_reader
        #: Riwayat OI per simbol, satu panggilan venue melayani seluruh
        #: ingatan simbol itu dalam satu lintasan proyeksi.
        self._oi_cache: dict[str, list[tuple[Any, float]]] = {}

    async def cari(
        self,
        *,
        as_of: datetime,
        market: str,
        timeframe: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Mesin kueri konteks (PASAL 15.29): ingatan yang hasilnya sudah final
        **sebelum** ``as_of``.

        ``as_of`` tanpa bawaan dengan sengaja - lihat catatan modul.

        Disaring ``market_code`` dan ``timeframe`` juga: tanpa keduanya,
        ingatan IDX harian akan muncul sebagai "kondisi serupa" untuk rencana
        CRYPTO lima belas menit, dan PASAL 15.14 justru menyatakan timeframe
        punya kepribadian sendiri.
        """
        return await self._db.fetch(
            f"SELECT {_KOLOM} FROM market_memories "
            "WHERE market_code = %s AND timeframe = %s "
            "AND resolved_at IS NOT NULL AND resolved_at < %s "
            "ORDER BY resolved_at DESC LIMIT %s",
            market,
            timeframe,
            to_mysql_datetime(as_of),
            limit,
        )

    async def ingatan_berarah(
        self, *, timeframe: str, as_of: datetime, limit: int = 4000
    ) -> list[Any]:
        """Ingatan berarah yang hasilnya final, urut ``locked_at`` MENAIK.

        Untuk penilaian PASAL 15.44. Tiga syaratnya masing-masing menahan satu
        cara angkanya jadi tidak berarti:

        * ``arah IN ('BUY','SELL')`` - keputusan tanpa arah tidak punya sisi
          untuk didukung atau dilawan, dan memasukkannya mengukur sesuatu yang
          lain lalu menyebutnya kontribusi memory;
        * ``hasil IN ('WIN','LOSS')`` - yang belum final belum mengajari apa pun;
        * ``locked_at < as_of`` - batas atas yang sama seperti :meth:`cari`.

        **Urut MENAIK**, tidak seperti :meth:`cari`: penilaiannya menyapu
        keputusan dari yang paling lama ke yang paling baru sambil menumbuhkan
        kumpulan "yang sudah tersedia". Urutan menurun akan membuat sapuan itu
        melihat seluruh masa depan pada langkah pertama.

        Pemotongan ``limit`` mengambil yang **terbaru** lalu membalik urutannya,
        supaya batas yang berbatas tidak diam-diam menilai korpus paling awal.
        """
        rows = await self._db.fetch(
            "SELECT * FROM ("
            "  SELECT * FROM market_memories "
            "  WHERE timeframe = %s AND arah IN (%s, %s) AND hasil IN (%s, %s) "
            "  AND resolved_at IS NOT NULL AND locked_at < %s "
            "  ORDER BY locked_at DESC LIMIT %s"
            ") t ORDER BY t.locked_at ASC",
            timeframe,
            "BUY",
            "SELL",
            Hasil.WIN.value,
            Hasil.LOSS.value,
            to_mysql_datetime(as_of),
            limit,
        )
        return [ingatan_dari_baris(r) for r in rows]

    async def kandil_sampai(
        self, *, symbol: str, timeframe: str, sampai: datetime, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Bar yang sudah **tutup sebelum** ``sampai``, untuk perkayaan teknikal.

        Pintu kebocoran paling halus di seluruh Phase 15 (PASAL 15.39): bar
        yang tutup **sesudah** keputusan berisi persis jawaban yang sedang
        dicari, dan volatilitas yang dihitung dengan bar itu akan tampak
        menerangkan hasil dengan sangat baik. Tidak ada log yang akan
        menyebutnya.

        ``is_closed`` juga, dan itu SPEC 24: bar yang belum settle adalah harga
        yang belum jadi, dan indikator yang membacanya membaca masa depan yang
        belum ada.
        """
        return await self._db.fetch(
            "SELECT c.open_time, c.close_time, c.open, c.high, c.low, "
            "       c.close, c.volume "
            "FROM candles c JOIN assets a ON a.id = c.asset_id "
            "WHERE a.symbol = %s AND c.interval_code = %s "
            "AND c.is_closed = TRUE AND c.close_time <= %s "
            "ORDER BY c.open_time DESC LIMIT %s",
            symbol,
            timeframe,
            to_mysql_datetime(sampai),
            limit,
        )

    async def hitung_per_timeframe(
        self, *, as_of: datetime, market: str
    ) -> dict[str, int]:
        """Berapa ingatan yang boleh dilihat, per timeframe.

        Dipakai :func:`aruna.memory.lookup.horizon_ingatan` untuk memilih
        timeframe mana yang punya cukup bahan. **Satu kueri per tick**, bukan
        satu per simbol: jawabannya sama untuk seluruh simbol pada tick yang
        sama.

        **Hanya ingatan yang bisa mengajari yang dihitung** - yang hasilnya
        ``WIN`` atau ``LOSS``. Terukur 2026-08-21 saat proyektor futures
        dibangun: dari 182 hasil futures, **165 EXPIRED** - bukan menang, bukan
        kalah. Penghitung yang menghitung seluruh baris akan melihat "182
        ingatan 4h", melewati ambang dua puluh, lalu meninggalkan korpus 1h
        yang punya 2.189 hasil sungguhan: berpindah ke timeframe yang lebih
        tepat dan nyaris tidak bisa mengatakan apa pun.

        Terikat ``as_of`` seperti pencariannya sendiri - hitungan yang
        memasukkan ingatan masa depan akan memilih timeframe yang belum punya
        apa-apa pada saat keputusan itu dibuat, dan itu kebocoran PASAL 15.39
        lewat pintu belakang.
        """
        rows = await self._db.fetch(
            "SELECT timeframe, COUNT(*) AS n FROM market_memories "
            "WHERE market_code = %s AND hasil IN (%s, %s) "
            "AND resolved_at IS NOT NULL AND resolved_at < %s "
            "GROUP BY timeframe",
            market,
            Hasil.WIN.value,
            Hasil.LOSS.value,
            to_mysql_datetime(as_of),
        )
        return {str(r["timeframe"]): int(r["n"]) for r in rows}

    async def ringkas_harian(
        self, *, since: datetime, until: datetime
    ) -> Any:
        """Keadaan ingatan untuk laporan harian (PASAL 15.43).

        ``since``/``until`` membatasi yang **baru**; sisanya keadaan sekarang.
        Yang bisa mengajari dihitung terpisah dari total dengan alasan yang
        sama seperti di :meth:`hitung_per_timeframe`: delapan ribu ingatan yang
        sebagian kedaluwarsa bukan delapan ribu pelajaran.
        """
        from aruna.memory.harian import IngatanHarian

        pokok = await self._db.fetchrow(
            "SELECT "
            "  SUM(created_at >= %s AND created_at < %s) AS baru, "
            "  COUNT(*) AS total, "
            "  SUM(hasil IN (%s, %s)) AS mengajari, "
            "  MIN(locked_at) AS awal, MAX(locked_at) AS akhir "
            "FROM market_memories",
            to_mysql_datetime(since),
            to_mysql_datetime(until),
            Hasil.WIN.value,
            Hasil.LOSS.value,
        ) or {}

        per_tf = {
            str(r["k"]): int(r["n"])
            for r in await self._db.fetch(
                "SELECT timeframe AS k, COUNT(*) AS n FROM market_memories "
                "GROUP BY timeframe"
            )
        }
        per_mutu = {
            str(r["k"]): int(r["n"])
            for r in await self._db.fetch(
                "SELECT mutu AS k, COUNT(*) AS n FROM market_memories "
                "GROUP BY mutu"
            )
        }

        awal, akhir = as_utc(pokok.get("awal")), as_utc(pokok.get("akhir"))
        return IngatanHarian(
            baru=int(pokok.get("baru") or 0),
            total=int(pokok.get("total") or 0),
            per_timeframe=per_tf,
            per_mutu=per_mutu,
            bisa_mengajari=int(pokok.get("mengajari") or 0),
            rentang=(awal, akhir) if awal and akhir else None,
        )

    async def cari_terhitung(
        self,
        *,
        as_of: datetime,
        market: str,
        timeframe: str,
        limit: int = 500,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Sama seperti :meth:`cari`, plus penanda apakah hasilnya terpotong.

        Terukur 2026-08-21: pencarian sungguhan memulangkan **tepat 5.000**
        baris - yaitu batas yang dioper. Batas yang persis tercapai berarti
        ingatan lain terpotong, dan karena urutannya ``resolved_at DESC``, yang
        terpotong selalu yang paling lama.

        Dua kesalahan sekaligus: jumlah sampel yang dilaporkan PASAL 15.9 jadi
        salah, dan pemotongannya menambahkan bias kebaruan yang tidak pernah
        diputuskan siapa pun. Yang menahannya bukan batas yang lebih besar -
        batas berapa pun bisa tercapai - melainkan pemanggil yang **tahu**.
        """
        rows = await self.cari(
            as_of=as_of, market=market, timeframe=timeframe, limit=limit
        )
        terpotong = len(rows) >= limit
        if terpotong:
            log.warning(
                "memory.cari_terpotong",
                market=market,
                timeframe=timeframe,
                limit=limit,
                detail=(
                    "kandidat mencapai batas; yang tertua terpotong dan "
                    "jumlah sampelnya jadi batas bawah, bukan jumlah sebenarnya"
                ),
            )
        return rows, terpotong

    async def simpan(self, baris: dict[str, Any]) -> bool:
        """Sisipkan satu ingatan. ``INSERT IGNORE`` - PASAL 15.26.

        Proyeksi yang dijalankan dua kali tidak boleh melahirkan ingatan kedua
        untuk peristiwa yang sama, dan yang menahannya adalah kunci UNIQUE di
        database - bukan pemeriksaan di sini yang bisa kalah balapan.
        """
        n = await self._db.execute(
            "INSERT IGNORE INTO market_memories "
            "(signal_id, market_code, symbol, timeframe, regime, risk_level, "
            " news, quality_band, liquidity_band, volatility_band, "
            " momentum_band, volume_band, trend_band, structure_band, "
            " funding_band, oi_band, arah, hasil, move_pct, "
            " cakupan, mutu, model_version, locked_at, resolved_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            baris["signal_id"],
            baris["market_code"],
            baris["symbol"],
            baris["timeframe"],
            baris["regime"],
            baris["risk_level"],
            baris["news"],
            baris["quality_band"],
            baris["liquidity_band"],
            baris.get("volatility_band", UNKNOWN),
            baris.get("momentum_band", UNKNOWN),
            baris.get("volume_band", UNKNOWN),
            baris.get("trend_band", UNKNOWN),
            baris.get("structure_band", UNKNOWN),
            baris.get("funding_band", UNKNOWN),
            baris.get("oi_band", UNKNOWN),
            baris["arah"],
            baris["hasil"],
            baris["move_pct"],
            baris["cakupan"],
            baris["mutu"],
            baris["model_version"],
            to_mysql_datetime(baris["locked_at"]),
            to_mysql_datetime(baris["resolved_at"])
            if baris["resolved_at"] is not None
            else None,
        )
        return bool(n)

    async def bahan_proyeksi(
        self, *, sampai: datetime, limit: int
    ) -> list[dict[str, Any]]:
        """Signal yang hasilnya sudah final dan belum punya ingatan.

        **Terikat ``sampai`` juga**, dan bukan karena kehati-hatian umum:
        sebuah backtest yang memproyeksikan seluruh sejarah lebih dulu lalu
        mencari dengan ``as_of`` yang benar tetap bocor lewat pintu ini, karena
        proyeksinya sendiri sudah membaca hasil yang belum terjadi.
        """
        return await self._db.fetch(
            "SELECT s.signal_id, s.symbol, s.market_code, s.horizon_code, "
            "       s.regime, s.risk_level, s.news_state, s.signal_quality, "
            "       s.spread_bps, s.direction, s.model_version, s.locked_at, "
            "       o.move_pct, o.favourable, o.sampled_at AS resolved_at "
            "FROM signal_snapshots s "
            "JOIN outcome_snapshots o ON o.signal_id = s.signal_id "
            "LEFT JOIN market_memories m ON m.signal_id = s.signal_id "
            "WHERE o.is_final = 1 AND o.sampled_at < %s AND m.signal_id IS NULL "
            "ORDER BY o.sampled_at ASC LIMIT %s",
            to_mysql_datetime(sampai),
            limit,
        )

    async def _perkaya(
        self, sidik: Sidik, row: dict[str, Any], dikunci: datetime | None
    ) -> Sidik:
        """Tambahkan lima dimensi teknikal dari candle (PASAL 15.5).

        Dihitung ulang dari bar yang **sudah tutup sebelum** keputusan itu
        dibuat - lihat :meth:`kandil_sampai` untuk kenapa batas itu bukan
        kehati-hatian umum.

        Gagal menjadi sidik jari apa adanya, bukan pengecualian: ingatan yang
        lebih tipis tetap ingatan, dan cakupannya yang akan menyebutkannya.
        """
        if dikunci is None:
            return sidik
        try:
            from aruna.analysis.series import CandleSeries, InsufficientData
            from aruna.core.enums import Horizon, Market
            from aruna.memory.teknikal import dimensi_teknikal

            simbol = str(row.get("symbol") or "")
            tf = str(row.get("horizon_code") or row.get("timeframe") or "")
            baris = await self.kandil_sampai(
                symbol=simbol, timeframe=tf, sampai=dikunci, limit=BAR_TEKNIKAL
            )
            if len(baris) < BAR_MINIMUM:
                return sidik
            try:
                seri = CandleSeries.from_rows(
                    baris, market=Market.CRYPTO, symbol=simbol,
                    interval=Horizon(tf),
                )
            except (InsufficientData, ValueError):
                return sidik
            return sidik.dengan(dimensi_teknikal(seri))
        except Exception:
            log.exception("memory.perkaya_failed")
            return sidik

    async def _arah_oi(self, symbol: str, dikunci: datetime | None) -> str:
        """Arah open interest pada saat keputusan itu (PASAL 15.5).

        Dibaca dari ``open_interest_history`` yang **sudah ada di adapter sejak
        lama** dan tidak pernah disimpan siapa pun. Riwayatnya dicache per
        simbol: satu panggilan venue melayani seluruh ingatan simbol itu.

        Pembacanya opsional. Tanpa dia - dan ketika venue tidak terjangkau -
        hasilnya ``UNKNOWN``, bukan pengecualian: ingatan yang lebih tipis
        tetap ingatan, dan proyeksi tidak boleh berhenti karena satu endpoint.

        **Hanya bacaan yang stempel waktunya SEBELUM keputusan** yang dipakai
        (PASAL 15.39). Open interest sesudahnya berisi jawaban yang sedang
        dicari.
        """
        if self._oi_reader is None or dikunci is None:
            return UNKNOWN
        try:
            riwayat = self._oi_cache.get(symbol)
            if riwayat is None:
                baris = await self._oi_reader.open_interest_history(
                    symbol, period="4h", limit=200
                )
                riwayat = sorted(
                    ((as_utc(b.as_of), float(b.open_interest)) for b in baris),
                    key=lambda x: x[0],
                )
                self._oi_cache[symbol] = riwayat
            lebih_awal = [n for t, n in riwayat if t and t <= dikunci]
            if len(lebih_awal) < 2:
                return UNKNOWN
            return band_open_interest(lebih_awal[-1], lebih_awal[-2])
        except Exception:
            log.exception("memory.oi_unavailable", symbol=symbol)
            self._oi_cache[symbol] = []
            return UNKNOWN

    async def proyeksikan_futures(
        self, *, sampai: datetime, limit: int = 500
    ) -> int:
        """Bangun ingatan dari rencana futures yang sudah diresolusi (PASAL 15.2).

        **Kenapa proyektor kedua, bukan satu yang digabung.** Sumbernya berbeda
        tabel, berbeda ejaan simbol, dan berbeda bentuk hasil - satu kueri yang
        melayani keduanya akan penuh cabang yang hanya benar untuk salah satu.

        **Yang tidak tersimpan tetap UNKNOWN.** Jalur futures tidak menyimpan
        regime, berita, signal quality, maupun spread per rencana; hanya
        ``risk_level`` yang bisa dijoin lewat ``council_sessions``, dan itu pun
        terukur MODERATE untuk 179 dari 179. Mengisi sisanya dengan tebakan
        membuat ingatan futures terlihat lebih lengkap daripada yang
        sungguhnya (§13.26), dan cakupannya - yang dilaporkan terpisah - akan
        berbohong tentang seberapa banyak yang benar-benar dibandingkan.

        Hari ini hasilnya tipis dan itu disebut, bukan disembunyikan: 182 hasil
        futures, **165 kedaluwarsa**, tersisa 17 yang benar-benar mengajari.
        ``hitung_per_timeframe`` hanya menghitung yang mengajari, jadi 4h tidak
        akan mengambil alih dari 1h sebelum ia benar-benar punya isi.
        """
        rows = await self._db.fetch(
            "SELECT p.signal_id, p.symbol, p.side, p.horizon_hours, "
            "       p.model_version, p.created_at, p.funding_cost_pct, "
            "       r.outcome, r.resolved_at, r.entry, r.exit_price, "
            "       c.risk_level "
            "FROM futures_plan_results r "
            "JOIN futures_plans p ON p.signal_id = r.signal_id "
            "LEFT JOIN council_sessions c ON c.id = p.council_session_id "
            "LEFT JOIN market_memories m ON m.signal_id = r.signal_id "
            "WHERE r.resolved_at < %s AND m.signal_id IS NULL "
            "ORDER BY r.resolved_at ASC LIMIT %s",
            to_mysql_datetime(sampai),
            limit,
        )

        tersisip = 0
        for row in rows:
            dikunci = as_utc(row["created_at"])
            selesai = as_utc(row["resolved_at"])
            hasil = hasil_futures(row.get("outcome"))
            sidik = Sidik.dari_konteks(
                symbol=simbol_pasar(row["symbol"]),
                market="CRYPTO",
                timeframe=_timeframe_dari_jam(row.get("horizon_hours")),
                regime=None,
                risk_level=row.get("risk_level"),
                news=None,
                quality=None,
                spread_bps=None,
            )
            # Lima teknikal dari candle, plus dua dimensi venue yang **sudah
            # tersimpan dan tidak pernah dibaca**: funding dari kolomnya
            # sendiri, open interest dari pembaca yang dioper pemanggil.
            sidik = await self._perkaya(
                sidik,
                {"symbol": sidik.nilai[Dimensi.ASSET],
                 "timeframe": sidik.nilai[Dimensi.TIMEFRAME]},
                dikunci,
            )
            sidik = sidik.dengan({
                Dimensi.FUNDING: band_funding(row.get("funding_cost_pct")),
                Dimensi.OPEN_INTEREST: await self._arah_oi(
                    row["symbol"], dikunci
                ),
            })
            cakupan = bandingkan(sidik, sidik).cakupan
            if await self.simpan({
                "signal_id": row["signal_id"],
                "market_code": "CRYPTO",
                "symbol": sidik.nilai[Dimensi.ASSET],
                "timeframe": sidik.nilai[Dimensi.TIMEFRAME],
                **{
                    kolom: sidik.nilai[d]
                    for d, kolom in KOLOM_DIMENSI.items()
                },
                "arah": str(row.get("side") or "UNKNOWN"),
                "hasil": hasil.value,
                "move_pct": _gerak_futures(row),
                "cakupan": cakupan,
                "mutu": mutu_dari(
                    cakupan=cakupan, hasil=hasil,
                    locked_at=dikunci, resolved_at=selesai,
                ).value,
                "model_version": str(row.get("model_version") or "UNKNOWN"),
                "locked_at": dikunci,
                "resolved_at": selesai,
            }):
                tersisip += 1
        if tersisip:
            log.info("memory.proyeksi_futures", bahan=len(rows), tersisip=tersisip)
        return tersisip

    async def proyeksikan(self, *, sampai: datetime, limit: int = 500) -> int:
        """Bangun ingatan baru dari signal yang hasilnya sudah final.

        Memulangkan jumlah ingatan yang benar-benar tersisip. Nol berarti tidak
        ada bahan baru - bukan kegagalan, dan itu keadaan yang normal begitu
        proyeksi pertama selesai.
        """
        bahan = await self.bahan_proyeksi(sampai=sampai, limit=limit)
        tersisip = 0
        for row in bahan:
            # **Zona waktu dipasang di sini, di batas repositori.** Kolom
            # DATETIME MySQL tidak membawanya, jadi driver memulangkan waktu
            # yang naif - dan `to_mysql_datetime` menolak yang naif dengan
            # sengaja supaya urutan antar provider tidak pernah ambigu.
            # Lintasan pertama terhadap data produksi meledak persis di sini.
            dikunci = as_utc(row["locked_at"])
            selesai = as_utc(row["resolved_at"])
            sidik = await self._perkaya(Sidik.dari_snapshot(row), row, dikunci)
            # Cakupan dibaca dari perbandingan sidik jari dengan dirinya
            # sendiri: pembilangnya tidak dipakai, penyebutnya yang dicari -
            # yaitu berapa bobot dimensi yang benar-benar terbaca. Satu tempat
            # yang memutuskan arti "cakupan", bukan dua yang harus sepakat.
            cakupan = bandingkan(sidik, sidik).cakupan
            hasil = _hasil_dari(row)
            mutu = mutu_dari(
                cakupan=cakupan,
                hasil=hasil,
                locked_at=dikunci,
                resolved_at=selesai,
            )
            if await self.simpan({
                "signal_id": row["signal_id"],
                "market_code": row["market_code"],
                "symbol": row["symbol"],
                "timeframe": row["horizon_code"],
                **{
                    kolom: sidik.nilai[d]
                    for d, kolom in KOLOM_DIMENSI.items()
                },
                "arah": str(row.get("direction") or "UNKNOWN"),
                "hasil": hasil.value,
                "move_pct": _skala_gerak(row.get("move_pct")),
                "cakupan": cakupan,
                "mutu": Mutu(mutu).value,
                "model_version": str(row.get("model_version") or "UNKNOWN"),
                "locked_at": dikunci,
                "resolved_at": selesai,
            }):
                tersisip += 1
        if tersisip:
            log.info("memory.proyeksi", bahan=len(bahan), tersisip=tersisip)
        return tersisip


#: Nasib rencana futures -> nasib ingatan.
#:
#: ``LIQUIDATED`` masuk kolom LOSS dan tidak punya kategori sendiri: §11.21
#: melarang menyembunyikan LOSS, dan likuidasi adalah kekalahan yang paling
#: buruk - memberinya kategori sendiri akan mengeluarkannya dari kolom kalah.
#: Keputusan yang sama sudah diambil Phase 14 di ``decision.outcome``.
#:
#: ``EXPIRED`` menjadi ``NEUTRAL``, bukan ``LOSS``. Terukur 2026-08-21: 165
#: dari 182 hasil futures kedaluwarsa. Menghitungnya sebagai kalah membuat win
#: rate futures terlihat 2% - angka yang salah dan meyakinkan.
_HASIL_FUTURES: dict[str, Any] = {
    "TARGET_HIT": Hasil.WIN,
    "STOPPED_OUT": Hasil.LOSS,
    "LIQUIDATED": Hasil.LOSS,
    "EXPIRED": Hasil.NEUTRAL,
}


def hasil_futures(outcome: object) -> Hasil:
    """Nasib satu rencana futures sebagai nasib ingatan."""
    if outcome is None:
        return Hasil.UNKNOWN
    return _HASIL_FUTURES.get(str(outcome).strip().upper(), Hasil.UNKNOWN)


#: Presisi gerak pasar yang disimpan: empat desimal persen, yaitu satu per
#: sepuluh ribu persen. Lebih dari cukup untuk angka yang dinilai terhadap
#: ambang satuan persen.
_SKALA = Decimal("0.0001")

#: Bar yang dibaca untuk perkayaan teknikal. Indikator terpanjang -
#: ``realised_volatility`` dan ``sma(50)`` - butuh sekitar lima puluh; dua
#: ratus memberi ruang tanpa membaca sejarah yang tidak menerangkan kondisi
#: saat itu.
BAR_TEKNIKAL = 200

#: Di bawah ini indikatornya memulangkan `insufficient` dan perkayaannya tidak
#: menambah apa pun - jadi kueri candle-nya pun tidak perlu dijalankan.
BAR_MINIMUM = 60


def _skala_gerak(nilai: object) -> Decimal | None:
    """Bulatkan gerak pasar sebelum disimpan.

    ``outcome_snapshots.move_pct`` menyimpan enam desimal; kolom ingatan empat.
    Tanpa pembulatan di sini MySQL yang memotongnya, dan ia memperingatkan
    ``Data truncated`` tiap baris - terukur pada lintasan pertama 2026-08-21.
    Yang dibulatkan database tidak pernah diketahui siapa pun yang membaca
    kodenya; yang dibulatkan di sini tertulis.
    """
    if nilai is None:
        return None
    try:
        return Decimal(str(nilai)).quantize(_SKALA)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _timeframe_dari_jam(jam: object) -> str:
    """``horizon_hours`` menjadi ejaan timeframe yang dipakai ingatan.

    ``Decimal('4.0000')`` -> ``'4h'``. Dieja lewat :class:`Horizon` supaya
    ejaan yang tidak dikenalnya ketahuan di sini, bukan menjadi timeframe
    karangan yang tidak pernah cocok dengan apa pun.
    """
    from aruna.core.enums import Horizon

    try:
        angka = Decimal(str(jam))
    except (InvalidOperation, TypeError, ValueError):
        return UNKNOWN
    utuh = int(angka)
    if angka != utuh:
        return UNKNOWN
    ejaan = f"{utuh}h" if utuh < 24 else f"{utuh // 24}d"
    try:
        return Horizon(ejaan).value
    except ValueError:
        return UNKNOWN


def _gerak_futures(row: dict[str, Any]) -> Decimal | None:
    """Gerak pasar dari entry ke harga keluar, apa adanya.

    **Positif berarti harga naik**, tanpa pembalikan untuk SHORT - sama seperti
    ``decision.outcome`` di Phase 14. Membaliknya di sini juga akan
    membaliknya dua kali, dan kekalahan besar tercatat sebagai kemenangan
    besar di dalam data yang dipelajari.
    """
    masuk, keluar = row.get("entry"), row.get("exit_price")
    if masuk in (None, 0) or keluar is None:
        return None
    try:
        return _skala_gerak((Decimal(str(keluar)) - Decimal(str(masuk)))
                            / Decimal(str(masuk)) * 100)
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return None


#: Arah yang bisa benar atau salah. Dua ejaan karena ``market_memories`` memuat
#: keduanya: ``BUY``/``SELL`` dari spot, ``LONG``/``SHORT`` dari futures.
_ARAH_BERARAH = frozenset({"BUY", "SELL", "LONG", "SHORT"})


def _hasil_dari(row: dict[str, Any]) -> Hasil:
    """Nasib satu signal dari kolom yang benar-benar ada.

    ``favourable`` adalah satu-satunya penilaian menang/kalah yang tersimpan di
    ``outcome_snapshots``. Yang tidak punya nilainya menjadi ``UNKNOWN``, bukan
    ``LOSS``: menganggap yang tidak terukur sebagai kekalahan akan membuat win
    rate historis terlihat buruk justru pada hari yang datanya paling tipis.

    **Keputusan tanpa arah menjadi ``NEUTRAL``, bukan ``LOSS``**, dan itu bukan
    kehalusan. Terukur 2026-08-21: 5.627 ``WAIT`` dan 176 ``NO_SIGNAL`` tercatat
    kalah - 59% dari seluruh korpus ingatan, cocok tepat dengan 5.803 baris
    ``NO_POSITION`` di ``paper_results``. Akibatnya win rate ingatan terbaca
    **17,9%** sementara akurasi arah sesungguhnya **44,5%**, dan
    ``Ringkasan.win_rate`` yang menggerakkan seluruh pengaruh SUPPORTIVE/CONTRARY
    dihitung di atas 59% label palsu.

    ``favourable=0`` pada ``WAIT`` berarti "harga tidak naik", bukan "ARUNA
    salah" - tidak ada yang diklaim untuk dibantah. Dan ``favourable=1`` juga
    tidak menjadikannya menang: kalau harga yang kebetulan naik sesudah WAIT
    dihitung kemenangan, ARUNA belajar bahwa diam adalah strategi yang menang.

    Ini bug yang **sama** dengan yang sudah diperbaiki di sisi futures beberapa
    baris di atas - ``EXPIRED`` menjadi ``NEUTRAL`` karena menghitungnya kalah
    membuat win rate futures terlihat 2%. Sisi spot tidak pernah ikut.
    """
    arah = str(row.get("direction") or "").strip().upper()
    if arah not in _ARAH_BERARAH:
        return Hasil.NEUTRAL
    nilai = row.get("favourable")
    if nilai is None:
        return Hasil.UNKNOWN
    return Hasil.WIN if int(nilai) else Hasil.LOSS


__all__ = ["MemoryRepository"]
