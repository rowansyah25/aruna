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
from aruna.db.repositories.xau import VERSI_MODEL_XAU
from aruna.xau.bukti import rakit_bukti
from aruna.xau.cooldown import Cooldown
from aruna.xau.geometri import Geometri
from aruna.xau.kabar import (
    nilai_kabar,
    nilai_penutup,
    susun_kabar,
    susun_penutup,
)
from aruna.xau.kalender import ringkas as ringkas_berita
from aruna.xau.kelayakan import periksa_kelayakan
from aruna.xau.koreksi import (
    KOREKSI_TIAP,
    bobot_yang_berlaku,
    hitung_koreksi,
    perlu_koreksi,
)
from aruna.xau.keputusan import SinyalXau, putuskan_dari_dewan
from aruna.xau.konteks import rakit_konteks
from aruna.xau.notify import kirim_sinyal, susun_pesan, susun_result
from aruna.xau.resolve import (
    HORIZON_BAR,
    LevelTersentuh,
    nilai_hasil,
    nilai_hasil_akhir,
    r_multiple,
)
from aruna.xau.timeframes import TumpukanTimeframe, rakit_tumpukan

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


async def nilai_yang_tertunda(
    repo: object,
    m5: list[Candle],
    *,
    struktur: object | None = None,
    sender: object | None = None,
) -> int:
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
    sebelum = await repo.hitung_hasil()
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
        # Horizon habis tanpa level tersentuh adalah titik keputusan, dan
        # sebelumnya ia berakhir dalam DIAM - justru keadaan tempat kerugian
        # paling sering dibiarkan tumbuh. Putusannya dihitung SEBELUM hasil
        # disimpan, karena hasil akhirnya bergantung padanya: menyuruh tutup
        # saat untung adalah kemenangan yang bisa diatribusikan ke ARUNA.
        penutup = None
        if hasil.level_tersentuh is LevelTersentuh.TIDAK_SATU_PUN and struktur:
            penutup = nilai_penutup(
                arah=arah,
                target=geo.target,
                atr=geo.atr,
                struktur=struktur,
                arah_benar=hasil.arah_benar,
                gerak_pct=hasil.gerak_pct,
            )

        r = r_multiple(geo.entry, geo.stop, hasil.harga_tutup, arah)
        hasil_akhir, menang = nilai_hasil_akhir(
            level=hasil.level_tersentuh,
            disuruh_tutup=None if penutup is None else not penutup.tahan,
            r=r,
        )

        await repo.simpan_hasil(
            hasil,
            baris["keputusan"],
            hasil_akhir=hasil_akhir,
            r=r,
            menang=menang,
        )
        dinilai += 1
        log.info(
            "xau.dinilai",
            prediction_id=baris["id"],
            arah_benar=hasil.arah_benar,
            level=hasil.level_tersentuh.value,
            hasil_akhir=hasil_akhir.value,
            r=None if r is None else float(r),
            menang=menang,
        )

        if sender is not None:
            await kirim_sinyal(
                sender,
                susun_result(
                    arah=arah,
                    entry=geo.entry,
                    target=geo.target,
                    stop=geo.stop,
                    hasil=hasil,
                    hasil_akhir=hasil_akhir,
                    r=r,
                    menang=menang,
                    penutup=penutup,
                ),
            )

        if penutup is not None:
            await repo.simpan_penutup(
                baris["id"],
                baris["keputusan"],
                penutup,
                harga=hasil.harga_tutup,
                terkirim=sender is not None,
            )
            log.info(
                "xau.penutup",
                prediction_id=baris["id"],
                tahan=penutup.tahan,
                alasan=penutup.alasan,
            )

    if dinilai:
        await koreksi_kalau_saatnya(repo, sebelum + dinilai)
    return dinilai


async def kabari_yang_berjalan(
    repo: object,
    m5: list[Candle],
    tumpukan: TumpukanTimeframe,
    *,
    sender: object | None = None,
) -> int:
    """Kabari sinyal yang masih berjalan - hanya saat keadaannya BERGANTI.

    Struktur dibaca ulang dari bar terbaru, bukan dari yang tersimpan saat
    sinyal terbit: seluruh gunanya adalah menanyakan "apakah alasannya masih
    ada", dan alasan yang dibaca dari catatan lama akan selalu menjawab ya.
    """
    if not m5:
        return 0

    bukti = rakit_bukti(tumpukan)
    if bukti is None:
        return 0

    harga = m5[-1].close
    sekarang = m5[-1].close_time
    dikabarkan = 0

    for baris in await repo.sinyal_berjalan(sejak=m5[0].open_time):
        as_of = baris["as_of"]
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)
        lewat = max(0, int((sekarang - as_of).total_seconds() // 300))
        sisa = HORIZON_BAR - lewat
        if sisa <= 0:
            # Horizonnya habis: yang menilainya adalah resolver, bukan kabar.
            continue

        kabar = nilai_kabar(
            arah=Decision(baris["keputusan"]),
            entry=baris["entry"],
            stop=baris["stop"],
            target=baris["target"],
            atr=baris["atr"],
            harga=harga,
            struktur=bukti.m5.structure,
            sisa_bar=sisa,
            # Rezim terbaru ikut: pasar yang kini membaca LAWAN arah sinyal
            # membatalkan premisnya, sama seperti level yang hilang.
            regime=bukti.m5.regime,
        )
        # Hanya PERUBAHAN. Tanpa syarat ini, satu gagasan berhorizon empat jam
        # mengirim empat puluh delapan pesan yang sama.
        if not kabar.perlu_dikabarkan:
            continue
        if kabar.keadaan.value == baris.get("keadaan_terakhir"):
            continue

        terkirim = False
        if sender is not None:
            terkirim = await kirim_sinyal(
                sender,
                susun_kabar(
                    kabar,
                    arah=Decision(baris["keputusan"]),
                    as_of=f"{sekarang:%Y-%m-%d %H:%M} UTC",
                ),
            )
        await repo.simpan_kabar(
            baris["id"], baris["keputusan"], kabar, terkirim=terkirim
        )
        dikabarkan += 1
        log.info(
            "xau.kabar",
            prediction_id=baris["id"],
            keadaan=kabar.keadaan.value,
            tutup=kabar.menyarankan_tutup,
            alasan=kabar.alasan,
        )
    return dikabarkan


async def koreksi_kalau_saatnya(repo: object, hasil_terselesaikan: int) -> None:
    """Jalankan koreksi diri kalau hitungan hasil melewati ambangnya.

    **Tanpa persetujuan operator, dan itu keputusan operator sendiri**
    (2026-08-28).  Yang membuatnya aman bukan kehati-hatian melainkan apa yang
    dikoreksi: bobot agen terhadap garis dasar pasar, bukan ambang gerbang
    terhadap hasilnya sendiri.  Yang kedua adalah overfitting yang spec larang.

    Putaran yang sampelnya kurang TETAP ditulis, supaya "belum cukup bahan"
    tidak terlihat sama dengan "tidak pernah dijalankan".
    """
    terakhir = await repo.koreksi_terakhir()
    dipicu_sebelumnya = int(terakhir["dipicu_oleh"]) if terakhir else 0
    if not perlu_koreksi(hasil_terselesaikan, dipicu_sebelumnya):
        return

    baris = await repo.baris_keandalan()
    putaran = dipicu_sebelumnya // KOREKSI_TIAP + 1
    hasil = hitung_koreksi(
        baris,
        putaran=putaran,
        dipicu_oleh=hasil_terselesaikan,
        versi_sebelumnya=terakhir["versi"] if terakhir else None,
    )
    await repo.simpan_koreksi(hasil)
    log.info(
        "xau.koreksi",
        versi=hasil.versi,
        diterapkan=hasil.diterapkan,
        sampel=hasil.sampel,
        ringkas=hasil.ringkas(),
    )


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
    dolar: object | None = None,
    berita: object | None = None,
    sender: object | None = None,
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

    # **Bar yang belum tutup dibuang di sini, satu kali, untuk SELURUH siklus.**
    #
    # Venue mengembalikan bar yang sedang berjalan sebagai nilai terbaru, dan
    # high/low/close-nya masih akan berubah.  Membiarkannya masuk membuat harga
    # keputusan datang dari bar yang belum selesai sementara buktinya datang
    # dari bar yang sudah - dua angka dari dua dunia, dan selisihnya muncul
    # belakangan sebagai hasil yang tak bisa dijelaskan.
    #
    # `CandleSeries` dan `resample_candles` sudah menyaringnya masing-masing,
    # tapi penyaringan yang tersebar berarti tiap pemakai baru harus
    # mengingatnya. Disaring sekali di sini, tidak ada yang perlu ingat.
    terbuka = sum(1 for c in m5 if not c.is_closed)
    m5 = [c for c in m5 if c.is_closed]
    if not m5:
        return HasilTick(
            alasan_lewat=f"seluruh {terbuka} bar masih terbuka; belum ada yang settle"
        )

    tumpukan = rakit_tumpukan(m5)

    # **Urusan lama diselesaikan SEBELUM gerbang keputusan apa pun.**
    #
    # Sebelumnya blok ini duduk di belakang gerbang "bar belum berganti" dan
    # "data tidak layak", jadi tiap tick yang tidak jadi memutuskan ikut
    # melewatkan penilaian sinyal yang sudah kena stop. Diukur dari kerugian
    # nyata operator 2026-08-28: tiga sinyal kena stop dan tetap tak tercatat
    # walau loop berjalan normal - karena barnya belum berganti.
    #
    # Menilai hasil dan mengabari sinyal berjalan tidak ada hubungannya dengan
    # bisa-tidaknya keputusan BARU dibuat. Menggabungkan keduanya membuat
    # keheningan di satu sisi membungkam sisi yang lain.
    if repo is not None:
        segar = rakit_bukti(tumpukan)
        struktur = segar.m5.structure if segar is not None else None
        await nilai_yang_tertunda(repo, m5, struktur=struktur, sender=sender)
        await kabari_yang_berjalan(repo, m5, tumpukan, sender=sender)

    as_of_bar = m5[-1].close_time
    if as_of_terakhir is not None and as_of_bar <= as_of_terakhir:
        # Bukan kegagalan: tidak ada bar baru berarti tidak ada yang baru untuk
        # dinilai. Menilainya lagi akan menulis baris kedua untuk bar yang sama.
        return HasilTick(
            alasan_lewat=f"bar belum berganti sejak {as_of_terakhir:%H:%M}",
            bar=len(m5),
            as_of=as_of_bar,
        )

    async def simpan(
        sinyal: SinyalXau,
        as_of: datetime,
        bacaan: dict | None = None,
        regime: object | None = None,
    ) -> HasilTick:
        prediction_id = None
        if repo is not None:
            prediction_id = await repo.simpan(
                sinyal,
                as_of=as_of,
                decided_at=sekarang,
                symbol=symbol,
                # Rezim M5: gerbang UNKNOWN_REGIME memblokir 17,4% keputusan
                # (diukur atas 17 hari), dan tanpa kolom ini angka itu tak
                # pernah bisa disandingkan dengan hasil keputusannya.
                regime=regime,
                # Proksi dolar, kalau pemanggil menyediakannya. `None` di sini
                # berarti belum ditarik pada siklus ini - proksi ditarik per
                # jam, bukan tiap bar, karena korelasi 250-bar bergerak lambat.
                dolar=dolar,
                # Kalender ekonomi. Diringkas terhadap `as_of` bar keputusan -
                # bukan jam sistem - supaya "menit ke rilis" diukur dari saat
                # keputusan berdiri, dan supaya peristiwa yang belum rilis
                # tidak pernah menyerahkan `actual`-nya.
                berita=(
                    ringkas_berita(berita, sekarang=as_of)
                    if berita is not None
                    else None
                ),
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
        # HANYA sinyal berarah yang dikabarkan. XAU memutuskan 288 kali sehari
        # dan hampir semuanya diam; mengabarkan tiap diam akan mengubur yang
        # satu-satunya berarti. NO SIGNAL tetap tersimpan lengkap dengan
        # sebabnya - baris itu catatannya.
        if sinyal.ada_sinyal and sender is not None:
            await kirim_sinyal(
                sender,
                susun_pesan(
                    sinyal,
                    as_of=f"{as_of:%Y-%m-%d %H:%M} UTC",
                    sesi=FOREX_CALENDAR.session(as_of),
                    regime=regime,
                    dolar=dolar,
                    berita=(
                        ringkas_berita(berita, sekarang=as_of)
                        if berita is not None
                        else None
                    ),
                    versi_model=VERSI_MODEL_XAU,
                ),
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
    konteks = rakit_konteks(bukti, _snapshot_dari_bar(m5, gate))
    deliberation = (engine or DeliberationEngine()).deliberate(konteks)
    # Keandalan terukur dari koreksi diri. Kosong sampai sepuluh hasil pertama
    # terselesaikan - dan saat kosong tiap suara bernilai sama.
    bobot = bobot_yang_berlaku(
        await repo.koreksi_terakhir() if repo is not None else None
    )
    sinyal = putuskan_dari_dewan(
        deliberation,
        bukti,
        m5[-1].close,
        symbol=symbol,
        cooldown=cooldown,
        bobot=bobot,
    )
    return await simpan(sinyal, bukti.as_of, bukti.bacaan(), bukti.m5.regime)


__all__ = [
    "BAR_DIBUTUHKAN",
    "SIMBOL",
    "HasilTick",
    "nilai_yang_tertunda",
    "satu_tick",
]
