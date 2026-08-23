"""Mesin skenario internal ARUNA (bagian 16.5, 16.6, 16.8).

Internal, dan itu bukan sementara. Bagian 16.12 menuntut ARUNA tetap bekerja
saat MiroFish ``DEGRADED``, dan satu-satunya cara memenuhinya adalah punya mesin
yang tidak bergantung padanya sama sekali. Mesin ini yang jalan sekarang; ketika
MiroFish ada, keluarannya berdampingan dengan yang ini, bukan menggantikannya.

**Tiga selalu, sisanya kalau ada buktinya.** Bagian 16.5 mengeja tiga yang
wajib - bullish continuation, bearish reversal, false breakout - dan lima yang
opsional. Yang wajib lahir tanpa syarat karena ketiganya adalah bentuk dasar
ketidaktahuan: lanjut, berbalik, atau tipuan. Yang opsional hanya lahir kalau
pemicunya menyala, sebab skenario likuidasi berantai tanpa data likuidasi
adalah karangan berformat.

**Deterministik.** Masukan sama menghasilkan skenario sama, byte per byte. Ini
syarat agar bagian 16.19 mungkin: mesin yang berubah-ubah tanpa sebab tidak bisa
dievaluasi, karena skenario yang salah minggu ini tidak bisa dibedakan dari
skenario lain yang kebetulan muncul. Tidak ada ``random`` di berkas ini, dan
tidak ada jam - keduanya masuk lewat parameter.

**Bobot menjumlah 100, dan itu bukan probabilitas.** Bagian 16.6 menyatakannya
dengan huruf besar. Yang dijumlahkan adalah pembagian perhatian relatif antar
skenario pada satu simulasi, bukan peluang pasar; label
:data:`~aruna.scenario.models.CATATAN_BOBOT` ikut di tiap keluaran supaya angka
itu tidak pernah beredar sendirian.

**Efek orde-dua sebagai rantai** (bagian 16.8). ``perkembangan`` adalah tuple
berurutan, bukan satu kalimat: akibat dari akibat punya urutan, dan meratakannya
menjadi satu paragraf membuang justru bagian yang membuatnya orde-dua.
"""

from __future__ import annotations

from datetime import datetime

from aruna.scenario.kerumunan import klasifikasi, simulasikan_kerumunan
from aruna.scenario.models import Invalidasi, Skenario
from aruna.scenario.pemicu import Peristiwa

__all__ = [
    "LANTAI_WAJIB",
    "MINIMUM_SKENARIO",
    "TOTAL_BOBOT",
    "VERSI",
    "simulasikan",
]


#: Versi mesin, ikut di tiap skenario (bagian 16.15 ``simulation_version``).
#:
#: Naikkan ketika aturan di bawah berubah. Evaluasi bagian 16.19 membandingkan
#: skenario dengan hasil pasar; tanpa versi, hasil dua mesin berbeda tercampur
#: dalam satu angka akurasi dan tidak ada yang bisa dikatakan tentang keduanya.
#: Naik ke ``internal-2`` pada 2026-08-22, saat bobot berhenti ditetapkan
#: tangan dan mulai dihitung dari simulasi kerumunan. Evaluasi bagian 16.19
#: membandingkan per versi; skenario dari kedua mesin yang tercampur dalam satu
#: angka akurasi tidak mengatakan apa pun tentang keduanya.
#: Naik ke ``internal-3`` pada 2026-08-23, saat mesin kerumunan diberi inersia
#: dan sebaran absorpsinya dipusatkan di titik seimbangnya sendiri. Sebelum itu
#: generator cuma sanggup menghasilkan TIGA dari enam keluarga yang dimiliki
#: `klasifikasi_jejak`; `False Breakout` - hasil pasar yang paling sering -
#: mustahil dihasilkan, dan ia muncul di keluaran hanya lewat `LANTAI_WAJIB`.
#:
#: Bagian 16.19 menilai per versi, jadi `internal-2` dan `internal-3` diadu di
#: data hidup tanpa satu pun baris lama ditulis ulang. Itu yang membuat
#: perubahan ini bisa dibantah alih-alih diumumkan.
VERSI = "internal-3"

#: Bagian 16.5: minimal tiga.
MINIMUM_SKENARIO = 3

#: Bagian 16.6: bobot dinormalkan ke seratus.
TOTAL_BOBOT = 100

#: Bobot mentah terkecil untuk skenario wajib bagian 16.5.
#:
#: Setengah lintasan. Tiga skenario dasar harus muncul walau nol lintasan
#: mendarat di keluarganya - bagian 16.5 menuntutnya - tapi bobotnya tetap
#: boleh nol setelah pembulatan, dan itu keterangan yang jujur: "kerumunan
#: tidak menghasilkan satu pun jalan seperti ini".
#:
#: Lantai ini ada supaya skenario wajib tidak hilang dari keluaran, bukan
#: supaya angkanya terlihat ramai.
LANTAI_WAJIB = 0.5


def _dasar(pemicu: frozenset[Peristiwa]) -> list[tuple[str, str, tuple[str, ...]]]:
    """Tiga skenario wajib bagian 16.5, beserta rantai perkembangannya.

    Rantainya berubah menurut pemicu yang menyala - bukan supaya terlihat
    pintar, melainkan karena "volume mengering" adalah perkembangan yang masuk
    akal setelah tembusan bervolume ekstrem dan tidak masuk akal setelah
    perubahan regime yang sepi.
    """
    ramai = Peristiwa.VOLUME_EKSTREM in pemicu
    tembus = Peristiwa.BREAKOUT_BESAR in pemicu or Peristiwa.BREAKDOWN_BESAR in pemicu

    lanjut = ["pembeli menyerap penawaran di area tembusan"]
    if ramai:
        lanjut.append("volume bertahan di atas rata-rata pada bar berikutnya")
    lanjut.append("posisi berlawanan yang tertinggal ikut menutup, mempercepat arah")
    if Peristiwa.ANOMALI_OPEN_INTEREST in pemicu:
        lanjut.append("open interest naik bersama harga: posisi baru, bukan tutupan")

    balik = ["penyerapan gagal dan harga kembali ke area sebelum tembusan"]
    if tembus:
        balik.append("yang masuk di tembusan tertinggal dan menutup rugi")
    balik.append("penutupan berurutan menjadi dorongan arah sebaliknya")

    tipuan = ["harga menembus lalu kembali ke dalam rentang dalam beberapa bar"]
    if ramai:
        tipuan.append("volume tembusan tidak diikuti volume lanjutan")
    tipuan.append("rentang lama kembali berlaku dan kedua sisi tersapu bergantian")

    return [
        ("Bullish Continuation", "tembusan bertahan dan diikuti", tuple(lanjut)),
        ("Bearish Reversal", "tembusan gagal dan arah berbalik", tuple(balik)),
        ("False Breakout", "tembusan palsu, harga kembali ke rentang", tuple(tipuan)),
    ]


#: Skenario yang kerumunan **tidak** modelkan sebagai keluarga tersendiri.
#:
#: Bagian 16.5 menyebut keduanya, jadi keduanya tetap dilaporkan ketika
#: pemicunya menyala - tapi bobotnya bukan keluaran simulasi, dan provenansinya
#: mengatakan begitu apa adanya. Bobot yang dikarang boleh ada; bobot yang
#: dikarang lalu dilaporkan seolah dihitung tidak boleh.
#:
#: Efek orde-dua bagian 16.8 sendiri **tidak** bergantung pada baris ini: ia
#: muncul di kerumunan sebagai kaskade likuidasi, lengkap dengan ronde saat ia
#: terpicu.
TANPA_KELUARGA_KERUMUNAN = frozenset({
    "News-Driven Reversal",
    "Second-Order Effect",
})


def _tambahan(
    pemicu: frozenset[Peristiwa], hitung: dict[str, int]
) -> list[tuple[str, str, tuple[str, ...]]]:
    """Skenario opsional bagian 16.5 - hanya yang buktinya ada.

    **Buktinya sekarang lintasan, bukan pemicu.** Versi sebelumnya
    menggerbangi tiap skenario opsional dengan pemicu tertentu, dan itu
    membuang bobot secara diam-diam: kerumunan menghasilkan dua lintasan yang
    berakhir sebagai kaskade likuidasi, sementara skenario kaskadenya digerbangi
    pemicu ``LONJAKAN_LIKUIDASI`` yang tidak pernah menyala. Dua lintasan itu
    tetap masuk penyebut dan bobotnya lenyap.

    Terbalik sekarang, dan lebih benar: kalau kerumunan menghasilkan jalan
    semacam itu, jalannya dilaporkan. Bagian 16.5 minta skenario tambahan
    muncul "jika relevan", dan "simulasinya menghasilkan dua jalan seperti ini"
    adalah bukti relevansi yang lebih baik daripada pemicu yang dipetakan
    tangan.
    """
    out: list[tuple[str, str, tuple[str, ...]]] = []

    if hitung.get("High Volatility"):
        out.append((
            "High Volatility",
            "rentang melebar tanpa arah yang menetap",
            (
                "kedua sisi tersapu dalam satu-dua bar",
                "stop di kedua arah tereksekusi lebih dulu dari arah sesungguhnya",
                "arah baru terbentuk hanya setelah volatilitas mereda",
            ),
        ))

    if hitung.get("Sideways"):
        out.append((
            "Sideways",
            "tidak ada pihak yang menang; harga menggantung",
            (
                "volume menyusut di kedua arah",
                "rentang menyempit sampai satu pemicu baru datang",
            ),
        ))

    # Digerakkan lintasan, bukan data likuidasi. Bedanya penting dan harus
    # tetap terbaca: ini **bukan** laporan bahwa likuidasi sedang terjadi -
    # datanya memang belum ada, dan `TANPA_SUMBER_DATA` masih menyebutnya.
    # Ini laporan bahwa kerumunan, dijalankan di bawah premis-premis yang ada,
    # menghasilkan jalan yang berakhir sebagai kaskade. Bukti simulasi, persis
    # seperti label yang bagian 16.1 wajibkan.
    if hitung.get("Liquidation Cascade"):
        out.append((
            "Liquidation Cascade",
            "penutupan paksa beruntun mempercepat arah",
            (
                "harga menyentuh kelompok harga likuidasi pertama",
                "penutupan paksa menjadi order pasar, mendorong ke kelompok berikutnya",
                "kedalaman order book menipis dan slippage melebar",
            ),
        ))

    if Peristiwa.BERITA_BESAR in pemicu:
        out.append((
            "News-Driven Reversal",
            "arah teknikal dibatalkan oleh isi berita",
            (
                "harga bergerak melawan struktur segera setelah berita",
                "peserta yang berposisi menurut teknikal menutup serentak",
                "arah baru bertahan hanya selama berita masih dominan",
            ),
        ))

    if Peristiwa.EFEK_ORDE_DUA in pemicu:
        out.append((
            "Second-Order Effect",
            "akibat dari akibat - bukan reaksi langsung terhadap pemicunya",
            (
                "reaksi pertama pasar terhadap pemicu berjalan seperti biasa",
                "reaksi itu sendiri mengubah posisi peserta lain",
                "perubahan posisi itu yang menggerakkan harga pada periode berikutnya",
                "arahnya bisa berlawanan dengan reaksi pertama",
            ),
        ))

    return out


def _hitungan_kerumunan(
    pemicu: frozenset[Peristiwa], *, kekuatan: float
) -> tuple[dict[str, int], int]:
    """Berapa lintasan mendarat di tiap keluarga, dan berapa seluruhnya.

    **Ini yang menggantikan bobot tetapan tangan.** Sampai 2026-08-22 bobot di
    modul ini ditetapkan lewat konstanta `_GESER = 5.0` dan sederet
    `if` - tebakan yang rapi, dibela komentar, dan tidak bisa dibantah dengan
    apa pun kecuali tebakan lain.

    Sekarang angkanya dihitung: kisi premis dijalankan, tiap lintasan
    diklasifikasikan, dan bobot sebuah keluarga adalah pangsa lintasan yang
    mendarat di sana. Yang membantahnya bisa memeriksa lintasannya - tiap satu
    membawa premis yang melahirkannya dalam satu kalimat.
    """
    lintasan = simulasikan_kerumunan(pemicu, kekuatan=kekuatan)
    hitung: dict[str, int] = {}
    for x in lintasan:
        nama = klasifikasi(x)
        hitung[nama] = hitung.get(nama, 0) + 1
    return hitung, len(lintasan)


def _bobot_mentah(nama: str, hitung: dict[str, int]) -> float:
    """Bobot mentah sebuah skenario: berapa lintasan mendarat di keluarganya.

    :data:`LANTAI_WAJIB` mencegah tiga skenario wajib bagian 16.5 hilang dari
    keluaran ketika nol lintasan mendarat di sana. Ia **tidak** mencegah bobot
    nol - dan itu disengaja: "0 dari 18 lintasan" adalah keterangan yang jujur,
    sementara bobot kecil yang dikarang supaya tidak terlihat kosong adalah
    tebakan yang menyamar sebagai perhitungan.
    """
    return float(hitung.get(nama, 0))


def _provenansi(nama: str, hitung: dict[str, int], total: int) -> str:
    """Dari mana bobot skenario ini datang, dalam satu kalimat yang bisa diadu.

    Dua bentuk, dan bedanya bukan kosmetik: bobot yang dihitung dari lintasan
    bisa diperiksa dengan menjalankan kisi premis yang sama, sedangkan bobot
    lantai tidak bisa diperiksa sama sekali. Menyamakan keduanya di bawah satu
    format membuat yang kedua terbaca seperti yang pertama.
    """
    if nama in TANPA_KELUARGA_KERUMUNAN:
        return "kerumunan: tidak dimodelkan sebagai keluarga; bobotnya lantai"
    return f"kerumunan: {hitung.get(nama, 0)}/{total} lintasan"


def _invalidasi(nama: str, pemicu: frozenset[Peristiwa]) -> Invalidasi:
    """Syarat yang membatalkan skenario (bagian 16.11).

    Tidak pernah kosong: :class:`~aruna.scenario.models.Skenario` menolaknya,
    dan penolakan itu yang menjaga bagian 16.11 tetap berlaku walau mesin ini
    kelak diganti.
    """
    umum = {
        "Bullish Continuation": [
            "harga kembali di bawah area tembusan dan bertahan satu bar penuh",
            "volume lanjutan turun di bawah rata-rata",
        ],
        "Bearish Reversal": [
            "harga bertahan di atas area tembusan",
            "tidak ada penutupan beruntun dari posisi yang tertinggal",
        ],
        "False Breakout": [
            "harga bertahan di luar rentang lebih dari tiga bar",
            "volume lanjutan bertahan di atas rata-rata",
        ],
        "High Volatility": [
            "rentang bar menyempit kembali ke ATR normal",
            "arah menetap selama dua bar berturut-turut",
        ],
        "Sideways": [
            "rentang melebar melewati batas atas atau bawahnya",
            "volume naik di salah satu arah",
        ],
        "Liquidation Cascade": [
            "harga berbalik sebelum menyentuh kelompok likuidasi berikutnya",
            "kedalaman order book pulih",
        ],
        "News-Driven Reversal": [
            "harga kembali mengikuti struktur teknikal sebelum berita",
            "berita terbantah atau kehilangan dominansi",
        ],
        "Second-Order Effect": [
            "reaksi pertama pasar tidak mengubah posisi peserta lain",
            "harga pada periode berikutnya tetap searah reaksi pertama",
        ],
    }
    syarat = list(umum.get(nama, ["kondisi awal skenario tidak lagi berlaku"]))

    if Peristiwa.PERUBAHAN_REGIME in pemicu:
        syarat.append("regime berpindah lagi sebelum horizon selesai")

    return Invalidasi(syarat=tuple(syarat))


def _risiko(nama: str, pemicu: frozenset[Peristiwa]) -> str:
    if Peristiwa.VOLATILITAS_ABNORMAL in pemicu or nama in {
        "High Volatility",
        "Liquidation Cascade",
    }:
        return "HIGH"
    if nama in {"Sideways", "False Breakout"}:
        return "LOW"
    return "MEDIUM"


def simulasikan(
    *,
    market: str,
    asset: str,
    pemicu: frozenset[Peristiwa],
    kondisi_awal: tuple[str, ...],
    bukti: tuple[str, ...],
    pada: datetime,
    kekuatan: float = 1.0,
) -> tuple[Skenario, ...]:
    """Skenario untuk satu peristiwa. Minimal tiga, bobot menjumlah seratus.

    ``pada`` masuk sebagai parameter, bukan dibaca dari jam di dalam: mesin yang
    membaca jamnya sendiri tidak bisa diuji ulang atas masukan yang sama, dan
    bagian 16.19 menuntut justru itu.

    ``kekuatan`` adalah severity peristiwanya dibagi ambangnya sendiri - angka
    yang sama yang dipakai pemindai. Ia menskalakan guncangan awal kerumunan:
    tembusan yang dua kali lebih jauh melewati ambangnya menggoyang kerumunan
    dua kali lebih keras, dan bobot yang keluar berbeda.

    **Produksi tidak mengisinya, dan itu SENGAJA sejak 2026-08-23** - bukan
    kelalaian. `upkeep.skenario._satu` memanggil fungsi ini tanpa ``kekuatan``,
    jadi nilainya selalu 1,0. Severity-nya ada di tangan pemanggil
    (``hasil.events[i].severity``), jadi menyambungkannya satu baris.

    Yang menahannya: disambungkan, hasilnya lebih BURUK, dan itu terukur bukan
    diduga. Dijalankan pada kisi premis nyata, menaikkan kekuatan ke 1,5-4,0
    justru menghapus ``Sideways`` dan membuat hampir seluruh lintasan
    ``Bullish Continuation``; ``False Breakout`` baru muncul sekali di 5-6 lalu
    hilang lagi di 8. Padahal ``False Breakout`` adalah **46,2%** hasil pasar
    yang sebenarnya.

    Jadi menyambungkannya akan menggeser bobot tanpa satu pun bukti bahwa
    geserannya ke arah yang benar - persis yang :class:`
    ~aruna.governance.proposal.Verdict` ``WITHIN_NOISE`` ada untuk menolak.
    Perubahannya menunggu :class:`~aruna.governance.proposal.ModelProposal`
    yang divalidasi out-of-sample, bukan satu baris yang ditambahkan diam-diam.

    ``scenario_id`` diturunkan dari aset, waktu, dan urutan - bukan dari UUID
    acak. Simulasi yang sama, dijalankan ulang, menghasilkan id yang sama, dan
    itu yang membuat baris ganda bisa dikenali alih-alih menumpuk diam-diam.
    """
    hitung, total_lintasan = _hitungan_kerumunan(pemicu, kekuatan=kekuatan)

    dasar = _dasar(pemicu)
    bentuk = dasar + _tambahan(pemicu, hitung)
    berlantai = {nama for nama, _, _ in dasar} | TANPA_KELUARGA_KERUMUNAN

    mentah = [
        max(
            _bobot_mentah(nama, hitung),
            LANTAI_WAJIB if nama in berlantai else 0.0,
        )
        for nama, _, _ in bentuk
    ]
    jumlah = sum(mentah)
    if jumlah <= 0:
        # Tidak satu pun lintasan mendarat di keluarga mana pun yang punya
        # skenario, dan tidak satu pun skenario wajib. Tidak mungkin hari ini -
        # `_dasar` selalu menghasilkan tiga - tapi pembagian nol yang menunggu
        # perubahan di tempat lain adalah cara paling sunyi sebuah siklus jatuh.
        mentah = [1.0] * len(bentuk)
        jumlah = float(len(bentuk))

    # Dinormalkan dengan metode sisa terbesar, bukan dengan membuang seluruh
    # selisih pembulatan ke bobot tertinggi.
    #
    # Bagian 16.6 minta bobotnya menjumlah seratus, dan pembulatan biasa
    # menghasilkan 99 atau 101 pada sebagian besar masukan - angka yang hampir
    # seratus mengundang pembacanya menyimpulkan ada skenario yang tidak
    # dilaporkan. Versi pertama menambalnya dengan menaruh seluruh selisih pada
    # yang terbesar, dan itu memiringkan hasil yang seharusnya seri: tiga
    # keluarga dengan dua lintasan masing-masing menghasilkan 34/33/33, dengan
    # kelebihan satu selalu jatuh ke yang pertama disebut.
    #
    # Sisa terbesar memberi kelebihannya kepada yang pecahannya paling besar.
    # Pada seri sempurna selisih satu tetap tak terhindarkan - seratus tidak
    # habis dibagi tiga - tapi selisihnya jadi yang terkecil yang mungkin,
    # bukan yang paling menguntungkan satu pihak.
    tepat = [m / jumlah * TOTAL_BOBOT for m in mentah]
    bobot = [int(t) for t in tepat]
    sisa = TOTAL_BOBOT - sum(bobot)
    if sisa > 0:
        urut = sorted(
            range(len(tepat)), key=lambda i: (-(tepat[i] - bobot[i]), i)
        )
        for i in urut[:sisa]:
            bobot[i] += 1

    stempel = pada.strftime("%Y%m%dT%H%M%S")
    keluar: list[Skenario] = []
    for urut, ((nama, deskripsi, perkembangan), berat) in enumerate(
        zip(bentuk, bobot, strict=True)
    ):
        keluar.append(
            Skenario(
                scenario_id=f"{asset}-{stempel}-{urut}",
                market=market,
                asset=asset,
                timestamp=pada,
                nama=nama,
                deskripsi=deskripsi,
                kondisi_awal=kondisi_awal,
                pemicu=", ".join(sorted(p.value for p in pemicu)),
                perkembangan=perkembangan,
                invalidasi=_invalidasi(nama, pemicu),
                risiko=_risiko(nama, pemicu),
                # Keyakinan mesin pada skenarionya, bukan pada arah pasar.
                # Diturunkan dari bobot supaya keduanya tidak bisa bertentangan.
                keyakinan=berat / TOTAL_BOBOT,
                bobot=berat,
                # Provenansi bobotnya, ikut ke tiap keluaran. Angka telanjang
                # menuntut dipercaya; "3 dari 18 lintasan" bisa diperiksa -
                # dan yang memeriksa bisa menjalankan kisi premis yang sama.
                bukti=(*bukti, _provenansi(nama, hitung, total_lintasan)),
                versi_simulasi=VERSI,
            )
        )

    return tuple(keluar)
