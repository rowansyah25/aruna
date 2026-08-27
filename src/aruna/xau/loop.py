"""Satu siklus keputusan XAU, dari tarikan candle sampai baris tersimpan.

**Harga diambil dari bar, bukan dari quote.**  Cara paling jelas mendapatkan
``Snapshot`` adalah memanggil ``/quote``, dan rencana ini sengaja tidak
melakukannya.  Sebuah quote diambil *sesudah* bar terakhir tutup, jadi harganya
lebih baru daripada seluruh bukti yang mendasari keputusan - keputusan akan
berdiri di atas harga yang tidak pernah dilihat indikator mana pun.  Bukan
kebocoran masa depan dalam arti biasa, tapi tetap ketidakcocokan antara harga
keputusan dan bukti keputusan, dan di Rencana 3 ia akan muncul sebagai selisih
yang tak seorang pun bisa jelaskan.

Harganya karena itu adalah ``close`` bar M5 tersettle terbaru - bar yang sama
yang melahirkan :attr:`BuktiXau.as_of`.  Efek sampingnya menghemat separuh
jatah kredit: 288 per hari, bukan 576.

**Kegagalan menarik data TIDAK disimpan sebagai NO SIGNAL.**  Sebuah baris
``NO_SIGNAL`` menyatakan ARUNA menilai lalu memutuskan untuk diam.  Venue yang
tidak menjawab bukan penilaian; menyimpannya sebagai keputusan akan mencemari
statistik "seberapa sering XAU diam" dengan menit-menit ketika ARUNA tidak
sempat bertanya sama sekali.  Itu dilaporkan lewat ``alasan_lewat``, bukan
lewat sebuah baris keputusan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from datetime import UTC

from aruna.agents.deliberation import DeliberationEngine
from aruna.core.clock import FOREX_CALENDAR
from aruna.core.enums import DataQuality, Decision, Horizon, Market
from aruna.core.errors import DataSourceUnavailableError
from aruna.core.logging import get_logger
from aruna.data.models import Candle, Snapshot
from aruna.data.provider import MarketDataProvider
from aruna.data.quality import QualityGate
from aruna.xau.bukti import rakit_bukti
from aruna.xau.cooldown import Cooldown
from aruna.xau.geometri import Geometri
from aruna.xau.kelayakan import periksa_kelayakan
from aruna.xau.keputusan import SinyalXau, putuskan_dari_dewan
from aruna.xau.konteks import rakit_konteks
from aruna.xau.resolve import nilai_hasil
from aruna.xau.timeframes import rakit_tumpukan

log = get_logger(__name__)

#: Bar M5 yang ditarik tiap tick.
#:
#: 250 bar = 20 jam 50 menit, cukup untuk lima ember H4 penuh plus sisa - jadi
#: H4 benar-benar terbentuk, dan `as_of` M5 tetap lebih maju daripada H4.
#: Satu permintaan tetap satu kredit berapa pun isinya, jadi menarik lebih
#: sedikit tidak menghemat apa pun.
BAR_DIBUTUHKAN = 250

SIMBOL = "XAU/USD"


@dataclass(frozen=True, slots=True)
class HasilTick:
    """Apa yang terjadi pada satu siklus."""

    #: Terisi kalau ARUNA sempat menilai - termasuk saat hasilnya NO SIGNAL.
    sinyal: SinyalXau | None = None
    #: Terisi kalau siklusnya dilewati tanpa penilaian sama sekali.
    alasan_lewat: str | None = None
    bar: int = 0
    prediction_id: int | None = None
    #: Close bar M5 terbaru yang terlihat siklus ini.  Dioper balik ke tick
    #: berikutnya supaya satu bar tidak dinilai dua kali.
    as_of: datetime | None = None

    @property
    def menilai(self) -> bool:
        return self.sinyal is not None


def _snapshot_dari_bar(candles: list, quality: QualityGate) -> Snapshot:
    """Snapshot dari bar tersettle terbaru - lihat docstring modul.

    ``bid``/``ask``/``spread_bps`` sengaja ``None``: Twelve Data tidak
    menerbitkannya, dan sebuah bar tidak punya dua sisi harga.

    ``session`` dan ``market_open`` diukur dari kalender, bukan ditanyakan ke
    venue - keduanya fungsi dari waktu bar itu sendiri.  Diambil pada
    ``close_time`` bar, bukan pada jam sistem: sesi yang melekat pada sebuah
    keputusan adalah sesi saat barnya tutup, dan keduanya berbeda tiap kali
    tick terlambat.
    """
    terakhir = candles[-1]
    verdict = quality.evaluate_candle(terakhir)
    saat_bar = terakhir.close_time
    return Snapshot(
        market=Market.FOREX,
        symbol=terakhir.symbol,
        captured_at=terakhir.close_time,
        last_price=terakhir.close,
        provenance=terakhir.provenance,
        quality=verdict.quality if not verdict.ok else DataQuality.OK,
        quality_detail=str(verdict) if not verdict.ok else None,
        bid=None,
        ask=None,
        spread_bps=None,
        session=FOREX_CALENDAR.session(saat_bar),
        market_open=FOREX_CALENDAR.is_open(saat_bar),
    )


async def nilai_yang_tertunda(repo: object, m5: list[Candle]) -> int:
    """Nilai sinyal berarah yang horizonnya sudah lewat.  Kembalikan jumlahnya.

    Memakai ``m5`` yang sudah ditarik untuk keputusan tick ini - jendela 250
    bar adalah sekitar dua puluh jam, jadi horizon empat jam milik prediksi
    mana pun di dalamnya sudah lengkap.  Menariknya lagi per prediksi akan
    menghabiskan jatah kredit yang tidak dianggarkan siapa pun.

    Sebuah prediksi yang jalur setelahnya belum cukup panjang **dilewati, bukan
    dinilai** - ``nilai_hasil`` memulangkan ``None`` untuknya, dan ia akan
    terambil lagi di tick berikutnya sampai horizonnya benar-benar tuntas.
    """
    if not m5:
        return 0

    tertunda = await repo.perlu_dinilai(sejak=m5[0].open_time)
    dinilai = 0
    for baris in tertunda:
        arah = Decision(baris["keputusan"])
        geo = Geometri(
            entry=baris["entry"],
            stop=baris["stop"],
            target=baris["target"],
            atr=baris["atr"],
            sentuhan_target=baris["sentuhan_target"] or 0,
        )
        as_of = baris["as_of"]
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)
        # Bar SESUDAH bar keputusan. Memasukkan bar keputusannya sendiri akan
        # menilai sinyal dengan harga yang sudah diketahui saat ia dibuat.
        jalur = [c for c in m5 if c.open_time >= as_of]
        hasil = nilai_hasil(baris["id"], geo, arah, jalur)
        if hasil is None:
            continue
        await repo.simpan_hasil(hasil, baris["keputusan"])
        dinilai += 1
        log.info(
            "xau.dinilai",
            prediction_id=baris["id"],
            arah_benar=hasil.arah_benar,
            level=hasil.level_tersentuh.value,
            gerak_pct=float(hasil.gerak_pct),
        )
    return dinilai


async def satu_tick(
    provider: MarketDataProvider,
    gate: QualityGate,
    *,
    sekarang: datetime,
    repo: object | None = None,
    cooldown: Cooldown | None = None,
    engine: DeliberationEngine | None = None,
    symbol: str = SIMBOL,
    as_of_terakhir: datetime | None = None,
) -> HasilTick:
    """Satu siklus keputusan.  Berhenti di penolakan pertama, tapi menyimpannya.

    ``as_of_terakhir`` adalah bar yang sudah dinilai siklus sebelumnya.  Tanpa
    ini, dua tick dalam satu jendela 300 detik - yang terjadi tiap kali
    supervisor menyalakan ulang di tengah bar - menulis dua baris dengan
    ``(setup_id, as_of)`` yang sama dan melanggar kunci uniknya.  Galat basis
    data itu mematikan loop, supervisor menyalakannya lagi, dan hasilnya crash
    loop yang menyalakan dirinya sendiri tiap lima menit.
    """
    try:
        m5 = await provider.fetch_candles(symbol, Horizon.M5, limit=BAR_DIBUTUHKAN)
    except DataSourceUnavailableError as exc:
        # Bukan penilaian - lihat docstring modul.
        log.warning("xau.tarik_gagal", sebab=str(exc))
        return HasilTick(alasan_lewat=f"tarikan gagal: {exc}")

    if not m5:
        return HasilTick(alasan_lewat="venue menjawab tanpa satu bar pun")

    as_of_bar = m5[-1].close_time
    if as_of_terakhir is not None and as_of_bar <= as_of_terakhir:
        # Bukan kegagalan: tidak ada bar baru berarti tidak ada yang baru untuk
        # dinilai. Menilainya lagi akan menulis baris kedua untuk bar yang sama.
        return HasilTick(
            alasan_lewat=f"bar belum berganti sejak {as_of_terakhir:%H:%M}",
            bar=len(m5),
            as_of=as_of_bar,
        )

    tumpukan = rakit_tumpukan(m5)

    async def simpan(
        sinyal: SinyalXau, as_of: datetime, bacaan: dict | None = None
    ) -> HasilTick:
        prediction_id = None
        if repo is not None:
            prediction_id = await repo.simpan(
                sinyal,
                as_of=as_of,
                decided_at=sekarang,
                symbol=symbol,
                # Bukti ikut disimpan supaya keputusan bisa DIPUTAR ULANG.
                # Sebuah prediksi yang salah tanpa buktinya cuma memberi tahu
                # bahwa ia salah; dengan buktinya, ia memberi tahu kenapa.
                bukti=bacaan,
            )
        log.info(
            "xau.keputusan",
            keputusan=sinyal.keputusan.value,
            alasan=sinyal.alasan,
            setup_id=sinyal.setup_id,
        )
        return HasilTick(
            sinyal=sinyal,
            bar=len(m5),
            prediction_id=prediction_id,
            as_of=as_of_bar,
        )

    kelayakan = periksa_kelayakan(tumpukan, gate, sekarang=sekarang)
    as_of = m5[-1].close_time
    if not kelayakan.layak:
        return await simpan(
            SinyalXau(
                keputusan=Decision.NO_SIGNAL,
                setup_id=f"{symbol}:-:-",
                alasan=kelayakan.alasan,
            ),
            as_of,
        )

    bukti = rakit_bukti(tumpukan)
    if bukti is None:
        return await simpan(
            SinyalXau(
                keputusan=Decision.NO_SIGNAL,
                setup_id=f"{symbol}:-:-",
                alasan="bukti teknikal tidak terhitung dari bar yang ada",
            ),
            as_of,
        )

    # Nilai sinyal lama SEBELUM membuat yang baru, memakai bar yang sudah di
    # tangan - nol panggilan API tambahan. Jendela 250 bar M5 adalah ~20 jam,
    # jadi horizon 4 jam milik prediksi mana pun di dalamnya sudah lengkap.
    if repo is not None:
        await nilai_yang_tertunda(repo, m5)

    konteks = rakit_konteks(bukti, _snapshot_dari_bar(m5, gate))
    deliberation = (engine or DeliberationEngine()).deliberate(konteks)
    sinyal = putuskan_dari_dewan(
        deliberation,
        bukti,
        m5[-1].close,
        symbol=symbol,
        cooldown=cooldown,
    )
    return await simpan(sinyal, bukti.as_of, bukti.bacaan())


__all__ = [
    "BAR_DIBUTUHKAN",
    "SIMBOL",
    "HasilTick",
    "nilai_yang_tertunda",
    "satu_tick",
]
