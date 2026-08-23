"""Rezim pasar dibaca dari beberapa timeframe sekaligus (bagian 17.3 - 17.8).

**Modul ini tidak menghitung rezim.** Ia membaca yang sudah dihitung, lalu
menjawab satu pertanyaan yang penyimpanannya tidak jawab: ketika 15m berkata
RANGING dan 1d berkata TRENDING, mana yang primary.

**Kenapa pertanyaan itu penting.** Bagian 17.8 memisahkan PRIMARY REGIME dari
SECONDARY CONDITION supaya pullback kecil tidak terbaca sebagai perubahan tren
besar. Tanpa pemisahan itu, router akan berganti strategi tiap kali harga
mundur satu bar - dan strategi yang berganti tiap lima belas menit bukan
strategi melainkan derau yang diberi nama.

Sumbernya
=========

**Bukan tabel ``regimes``, walau rencana Phase 17 menyebutnya begitu.** Diukur
2026-08-23: tabel itu memuat **tiga baris**, semuanya ``1d``, terakhir ditulis
2026-08-14. Sebabnya bukan tabelnya melainkan pemanggilnya -
``AnalysisService``, satu-satunya yang mengisinya, hanya berjalan dari perintah
``aruna analyze`` dan tidak pernah dari :class:`~aruna.upkeep.loop.UpkeepLoop`.

Yang hidup ``signal_snapshots.regime``: 9.437 baris 15m, 4.057 baris 1h, dan
2.407 baris 1d dalam tujuh hari, kedua puluh aset terpindai, terbaru
2026-08-23. Phase 16 sudah memilih sumber yang sama untuk alasan yang sama;
lihat :mod:`aruna.db.repositories.konteks_pemicu`.

Modul ini karena itu **tidak menuntut keyakinan classifier**: sumber yang hidup
tidak menyimpannya, dan kolom ``confidence`` di sana milik SINYAL bukan
classifier. Lihat :data:`BacaanRezim.keyakinan_persen` dan
:attr:`PetaRezim.primary_confidence`.

**Interval yang tidak ada dilaporkan, bukan didiamkan.** Rezim yang disimpulkan
dari satu interval sementara tiga tersedia bukan kesimpulan yang sama kuatnya,
dan ``PetaRezim.interval_hilang`` yang memberitahunya.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

from aruna.core.enums import Horizon
from aruna.signals.outcome import STORED_INTERVALS

__all__ = [
    "BOBOT_INTERVAL",
    "MINIMUM_RIWAYAT",
    "BacaanRezim",
    "PetaRezim",
    "stabilitas",
    "susun_peta",
]


#: Interval yang punya bacaan rezim sama sekali.
#:
#: Diturunkan dari :data:`~aruna.signals.outcome.STORED_INTERVALS`, yang sengaja
#: publik supaya tidak ada yang menulis daftar kedua - dokumentasinya sendiri
#: menyatakan bahwa daftar tandingan bebas jatuh keluar barisan dan gejalanya
#: bukan galat melainkan diam.
#:
#: ``1m`` dibuang, dan sebabnya diukur bukan diduga: ``signal_snapshots`` tidak
#: memuat satu pun baris rezim pada 1m. Bar satu menit dipakai menilai HASIL
#: sebuah sinyal, bukan menggolongkan rezim.
_INTERVAL_BERREZIM: tuple[str, ...] = tuple(
    h.value for h in STORED_INTERVALS if h is not Horizon.M1
)

#: Bobot tiap interval saat menentukan rezim primary.
#:
#: **Kebijakan, bukan pengukuran**, dan ditulis begitu supaya tidak ada yang
#: mengutipnya sebagai temuan. Yang bisa dipertahankan bukan angkanya melainkan
#: **urutannya**: horizon panjang lebih berat.
#:
#: Alasannya bukan bahwa horizon panjang lebih benar, melainkan bahwa bagian
#: 17.8 justru menuntut pullback pendek TIDAK terbaca sebagai perubahan tren.
#: Tanpa pembobotan, primary akan diserahkan kepada rezim yang bacaannya
#: terbanyak - dan interval pendek selalu terbanyak, karena ia yang paling
#: sering dipindai. Terukur 2026-08-23: 9.437 bacaan 15m melawan 2.407 bacaan
#: 1d dalam tujuh hari yang sama. Tanpa bobot, jawabannya ditentukan jadwal
#: pemindaian alih-alih pasar.
#:
#: **Kuncinya cuma tiga**, dan versi pertama memuat enam - 5m, 30m, dan 4h ikut
#: di dalamnya. Ketiganya tidak pernah dipindai, jadi ``interval_hilang`` akan
#: menyebutnya di tiap laporan seolah ada yang rusak. Yang menjaganya sekarang
#: `test_router_sumber_rezim`, dari kedua arah.
BOBOT_INTERVAL: dict[str, float] = {
    "15m": 1.0,
    "1h": 1.6,
    "1d": 2.4,
}

#: Berapa bacaan minimum sebelum stabilitas bisa dihitung sama sekali.
#:
#: Dua, dan angkanya bukan pilihan: stabilitas mengukur berapa PASANG bacaan
#: berurutan yang tidak berpindah, dan satu bacaan tidak punya pasangan.
MINIMUM_RIWAYAT = 2

#: Bobot untuk interval yang tidak ada di :data:`BOBOT_INTERVAL`.
#:
#: Netral, bukan nol. Bacaan yang benar-benar ada tidak boleh hilang tanpa
#: jejak hanya karena intervalnya belum pernah dipikirkan - yang benar adalah
#: ia dipakai tanpa diistimewakan.
_BOBOT_NETRAL = 1.0

#: Pengali untuk bacaan yang sumbernya tidak mengukur keyakinan.
#:
#: Satu, artinya bobot intervalnya dipakai utuh. Nol akan membuang bacaannya,
#: dan itu jawaban yang salah: sumber yang hidup memang tidak menyimpan
#: keyakinan classifier, jadi memperlakukan ketiadaannya sebagai keraguan
#: berarti membuang seluruh bukti yang ARUNA benar-benar punya.
_TANPA_KEYAKINAN = 1.0


@dataclass(frozen=True, slots=True)
class BacaanRezim:
    """Satu bacaan rezim pada satu interval.

    ``alasan`` ikut karena bagian 17.6 melarang rezim tanpa alasan. Peta yang
    membawa kesimpulan tanpa buktinya tidak bisa dibantah, dan yang tidak bisa
    dibantah bukan bukti melainkan pendapat berformat.
    """

    interval: str
    regime: str
    #: Keyakinan classifier dalam **persen**, atau ``None`` kalau sumbernya
    #: tidak mengukurnya.
    #:
    #: Satuannya ada di namanya dengan sengaja. ``regimes.confidence``
    #: disimpan 0..1 - terukur 0,653 sampai 1,000 - sementara peta ini memakai
    #: 0..100, dan angka mentah yang dioper apa adanya membuat penskalaan di
    #: :func:`~aruna.router.kecocokan.nilai` menjadi 0,0065 alih-alih 0,65.
    #: Setiap strategi akan runtuh ke NETRAL **tanpa satu pun galat**. Pintu
    #: konversinya satu: :meth:`dari_pecahan`.
    #:
    #: ``None`` bukan nol. Sumber yang hidup - ``signal_snapshots`` - tidak
    #: punya kolomnya sama sekali, dan menyamakan "tidak diukur" dengan "tidak
    #: yakin" akan membuang seluruh bukti yang ada.
    keyakinan_persen: float | None = None
    alasan: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        p = self.keyakinan_persen
        if p is not None and not 0.0 <= p <= 100.0:
            raise ValueError(
                f"keyakinan_persen di luar 0..100: {p!r}. Kalau angkanya "
                "pecahan 0..1, pakai BacaanRezim.dari_pecahan()."
            )

    @classmethod
    def dari_pecahan(
        cls,
        interval: str,
        regime: str,
        pecahan: float,
        alasan: tuple[str, ...] = (),
    ) -> BacaanRezim:
        """Bacaan dari sumber yang menyimpan keyakinan sebagai 0..1.

        Satu-satunya tempat konversi satuan terjadi. Pemanggil yang mengira
        satuannya persen ditolak, bukan dikalikan diam-diam menjadi delapan
        ribu lima ratus.
        """
        if not 0.0 <= pecahan <= 1.0:
            raise ValueError(
                f"pecahan di luar 0..1: {pecahan!r}. Kalau angkanya sudah "
                "persen, isi keyakinan_persen langsung."
            )
        return cls(
            interval=interval,
            regime=regime,
            keyakinan_persen=round(pecahan * 100, 1),
            alasan=alasan,
        )


@dataclass(frozen=True, slots=True)
class PetaRezim:
    """Kesimpulan lintas timeframe, berikut apa yang tidak terbaca."""

    #: ``None`` berarti TIDAK TERBACA - bukan "tidak ada rezim". Pemanggil yang
    #: menyamakan keduanya akan memilih strategi atas pasar yang belum pernah
    #: dilihatnya.
    primary: str | None
    #: Berapa persen dari seluruh bobot yang MENDUKUNG primary.
    #:
    #: **Bukan rata-rata keyakinan bacaan pendukung**, walau versi pertama
    #: begitu. Sumber yang hidup tidak menyimpan keyakinan classifier sama
    #: sekali, jadi rata-rata itu tidak punya isi untuk diambil.
    #:
    #: Yang menggantikannya bisa diukur dari peta itu sendiri: kesepakatan
    #: lintas horizon. Tiga horizon yang sepakat adalah bukti yang lebih kuat
    #: daripada tiga yang berselisih - dan itu pertanyaan yang **berbeda** dari
    #: :func:`stabilitas`, yang mengukur kesepakatan lintas WAKTU pada satu
    #: horizon. Karena berbeda, :func:`~aruna.router.kecocokan.nilai` boleh
    #: mengalikan keduanya tanpa menghitung hal yang sama dua kali.
    primary_confidence: float
    #: Rezim lain yang terbaca di interval lain (bagian 17.8).
    sekunder: tuple[str, ...]
    per_interval: tuple[BacaanRezim, ...]
    #: Interval yang diminta tapi tidak punya bacaan.
    interval_hilang: tuple[str, ...]

    @property
    def terbaca(self) -> bool:
        return self.primary is not None


def susun_peta(bacaan: tuple[BacaanRezim, ...]) -> PetaRezim:
    """Peta rezim dari beberapa bacaan timeframe.

    Tiap bacaan menyumbang ``bobot_interval * keyakinan`` ke rezimnya, dan
    bacaan yang sumbernya tidak mengukur keyakinan menyumbang bobot
    intervalnya utuh. Yang dijumlahkan bobot BERSKALA keyakinan dan bukan
    bobot saja: bacaan 1d yang ragu-ragu tidak boleh mengalahkan dua bacaan 1h
    yang yakin hanya karena intervalnya lebih panjang.

    Seri diputus menurut nama rezim, bukan urutan masuk. Bersandar pada urutan
    berarti jawabannya bergantung pada urutan baris yang kebetulan keluar dari
    database - dan itu jawaban yang berubah tanpa ada yang mengubah apa pun.
    """
    if not bacaan:
        return PetaRezim(None, 0.0, (), (), tuple(BOBOT_INTERVAL))

    skor: dict[str, float] = {}
    for b in bacaan:
        bobot = BOBOT_INTERVAL.get(b.interval, _BOBOT_NETRAL)
        yakin = (
            _TANPA_KEYAKINAN
            if b.keyakinan_persen is None
            else b.keyakinan_persen / 100
        )
        skor[b.regime] = skor.get(b.regime, 0.0) + bobot * yakin

    primary = min(skor, key=lambda r: (-skor[r], r))

    ada = frozenset(b.interval for b in bacaan)
    dukung = tuple(b for b in bacaan if b.regime == primary)
    percaya = _keyakinan(skor, primary, ada, dukung)

    sekunder = tuple(sorted({b.regime for b in bacaan if b.regime != primary}))
    hilang = tuple(i for i in BOBOT_INTERVAL if i not in ada)

    return PetaRezim(primary, percaya, sekunder, tuple(bacaan), hilang)


def _keyakinan(
    skor: dict[str, float],
    primary: str,
    hadir: frozenset[str],
    dukung: tuple[BacaanRezim, ...],
) -> float:
    """Seberapa kuat bukti untuk ``primary``, dalam persen.

    **Tiga pertanyaan yang berbeda dikalikan di sini**, dan tiap versi
    sebelumnya kehilangan salah satunya:

    * *cakupan* - berapa horizon yang punya bacaan sama sekali?
    * *kesepakatan* - dari bukti yang ada, berapa yang mendukung primary?
    * *keyakinan* - di tempat ia mendukung, seberapa yakin classifier-nya?

    *Cakupan* - berapa dari seluruh horizon yang mungkin benar-benar punya
    bacaan. **Tidak bergantung pada rezim mana yang menang.** Versi pertama
    tidak memuatnya sama sekali dan membagi dengan bobot yang KEBETULAN HADIR;
    akibatnya satu bacaan tunggal terbaca 100% - ia sepakat dengan dirinya
    sendiri. Itu justru bukti paling tipis yang mungkin, dan ia kasus yang
    **sering**: 15m diperbarui tiap bar sementara 1h dan 1d tertinggal
    berjam-jam, jadi dengan batas kesegaran seperti Phase 16 yang tersisa
    kerap hanya 15m.

    *Kesepakatan* - berapa bagian dari bukti yang benar-benar masuk mendukung
    primary alih-alih rezim lain. Versi kedua membagi dengan bobot penuh saja,
    dan akibatnya perselisihan berhenti berbiaya sama sekali: menambahkan
    bacaan yang membantah tidak mengubah pembilangnya, jadi angkanya diam.
    Test yang menangkapnya `test_bacaan_yang_berselisih_menurunkan_keyakinan`.

    Versi ketiga memuat keduanya tapi menghitung cakupan sebagai
    ``bobot_primary / bobot_penuh`` - dan itu bukan cakupan, itu pangsa
    primary lagi dengan nama lain. Ketika seluruh interval HADIR tapi satu
    membantah, kedua faktornya menjadi angka yang sama persis dan perselisihan
    dihukum **dua kali**: 0,68 menjadi 46,2 alih-alih 68.

    Ketiganya harus ada, dan harus mengukur hal yang berbeda. Bukti yang
    lengkap tapi terbelah, bukti yang bulat tapi tipis, dan bukti yang lengkap
    dan bulat tapi ragu-ragu sama-sama bukan bukti yang kuat - dan angka yang
    cuma memuat sebagian akan memberi nilai penuh kepada salah satunya.

    Faktor ketiga dihitung dari bacaan yang **MENDUKUNG** saja. Merata-ratakan
    seluruh bacaan akan membuat pembantah yang percaya diri MENAIKKAN keyakinan
    atas rezim yang justru ia bantah - dan `test_bacaan_yang_berselisih_
    menurunkan_keyakinan` sempat terbalik karena itu.

    Sumber yang hidup tidak menyimpan keyakinan classifier, jadi faktor ketiga
    hari ini selalu 1,0. Ia tetap ditulis karena ``regimes`` menyimpannya dan
    akan berarti begitu pengisinya tersambung ke siklus.
    """
    masuk = sum(skor.values())
    if masuk <= 0:
        return 0.0
    penuh = sum(BOBOT_INTERVAL.values())
    cakupan = min(
        1.0, sum(BOBOT_INTERVAL.get(i, _BOBOT_NETRAL) for i in hadir) / penuh
    )
    sepakat = skor[primary] / masuk
    terukur = [b.keyakinan_persen for b in dukung if b.keyakinan_persen is not None]
    yakin = (sum(terukur) / len(terukur) / 100) if terukur else _TANPA_KEYAKINAN
    return round(100.0 * cakupan * sepakat * yakin, 1)


def stabilitas(riwayat: tuple[str, ...]) -> float | None:
    """Berapa persen bacaan berurutan yang TIDAK berpindah rezim.

    ``None`` berarti **belum bisa diukur**, bukan "sangat tidak stabil". Nol
    akan terbaca sebagai rezim yang berkedip terus, dan itu kesimpulan yang
    jauh lebih dramatis daripada "baru satu bacaan" - pemanggil yang
    menyamakannya akan menurunkan keyakinan setiap kali sebuah aset baru mulai
    dipantau.

    **Kenapa angka ini dibutuhkan sama sekali.** Terukur pada Phase 16
    2026-08-22: classifier 15m berpindah rezim pada 30,6% bacaan berurutan,
    dan sebelas dari dua puluh simbol melihat tiga rezim berbeda dalam dua
    jam. Router yang memilih strategi tanpa memeriksa ini akan memilih atas
    rezim yang sudah berganti sebelum sinyalnya sempat terbit - dan bagian
    17.10 menuntut keyakinan strategi diturunkan justru pada keadaan itu.

    Yang dihitung PASANGAN berurutan, bukan berapa rezim berbeda yang muncul.
    Riwayat ``A A A B B B`` berpindah sekali dan sisanya diam; menghitungnya
    sebagai "dua rezim, jadi tidak stabil" akan menghukum tren yang berganti
    sekali sama beratnya dengan yang berkedip lima kali.

    Ini kesepakatan lintas **waktu** pada satu horizon, dan
    :attr:`PetaRezim.primary_confidence` kesepakatan lintas **horizon** pada
    satu waktu. Keduanya pertanyaan yang berbeda, jadi mengalikannya bukan
    menghitung hal yang sama dua kali.
    """
    if len(riwayat) < MINIMUM_RIWAYAT:
        return None
    tetap = sum(1 for a, b in pairwise(riwayat) if a == b)
    return round(100.0 * tetap / (len(riwayat) - 1), 1)
