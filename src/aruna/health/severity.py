"""Seberapa keras sebuah kegagalan komponen layak diteriakkan.

Sebelum modul ini ada, setiap komponen berstatus DOWN dicatat sebagai
``CRITICAL`` - satu peta status ke keparahan, tanpa peduli komponen apa yang
jatuh. Hasilnya terlihat di log operator:

    [critical] health.transition component=redis message='not connected'

Redis adalah cache. ``app.py`` menyatakannya sendiri di urutan startup-nya -
"cache - non-fatal" - dan ``cache.unavailable`` mencetak "running without
cache; MySQL remains the store of record". Jadi sistem ini menyatakan sesuatu
tidak fatal, lalu meneriakkannya sebagai critical.

Itu serigala palsu, dan harganya bukan kebisingan saja: sebuah tingkat
keparahan yang menyala untuk hal yang tidak mendesak akan diabaikan tepat pada
kali ketika ia benar.

**Garis pemisahnya: CRITICAL berarti CATATANNYA TERANCAM.**

Bukan "sesuatu rusak", bukan "ARUNA tidak bisa bekerja sekarang" - ARUNA yang
menolak bekerja karena datanya buruk sedang berperilaku benar, dan itu bukan
keadaan darurat. Yang darurat adalah ketika bukti berhenti tercatat, atau
tercatat salah:

* **database** - prediksi, outcome, dan audit tidak punya tempat lagi. Yang
  hilang selama menit-menit itu hilang selamanya.
* **clock** - cap waktu yang salah merusak catatan tanpa terlihat rusak, dan
  SPEC 22 melarang memperbaikinya belakangan.
* **config** - salah konfigurasi berarti yang tersimpan mungkin bukan yang
  dimaksud.

Sisanya WARNING, dan itu bukan pengecilan. Provider yang tidak terjangkau
membuat ARUNA menolak menerbitkan signal - persis yang PASAL 5 minta - dan
catatannya tetap utuh. Telegram yang mati berarti operator tidak diberi tahu,
dan mengirim alert critical lewat kanal yang sedang mati juga tidak menolong.

**Komponen yang tidak dikenal dianggap CRITICAL.** Arah defaultnya sengaja
berbahaya: sebuah pemeriksaan baru yang lupa diklasifikasikan harus berisik,
bukan diam. Ada test yang memaksa setiap pemeriksaan terdaftar punya
klasifikasi, jadi "lupa" muncul sebagai suite merah dan bukan sebagai
peringatan yang tidak pernah berbunyi.
"""

from __future__ import annotations

from aruna.core.enums import EventSeverity, HealthStatus

#: Komponen yang kegagalannya mengancam catatan itu sendiri.
RECORD_CRITICAL: frozenset[str] = frozenset({
    "database",
    "clock",
    "config",
    # Upkeep menskor prediksi dan menulis outcome. Selama ia mati, catatan
    # berhenti bertambah - dan itu definisi modul ini, bukan pengecualian
    # untuknya.
    #
    # Bantahan yang jelas: prediksinya masih di database dan bisa diskor nanti.
    # Itu benar sampai batas tertentu, dan batas itulah alasannya. Skoring
    # membaca bar dari riwayat venue, dan venue hanya menyajikannya selama
    # jendela tertentu; sebuah prediksi yang horizonnya lewat tanpa diskor
    # masih bisa diselamatkan **selama bar-barnya masih disajikan**. Lewat itu,
    # yang hilang hilang untuk selamanya, dan kehilangannya tidak
    # meninggalkan jejak apa pun - hanya prediksi yang tidak pernah punya
    # hasil.
    "upkeep",
})

#: Komponen yang kegagalannya mengganggu, tapi tidak merusak catatan.
#:
#: Dieja satu per satu, bukan "sisanya". Daftar yang eksplisit memaksa
#: pemeriksaan baru diputuskan tempatnya; "sisanya" akan menelan pemeriksaan
#: baru diam-diam ke tingkat yang paling tenang.
RECOVERABLE: frozenset[str] = frozenset({
    "redis",
    "telegram",
    "process",
})

#: Komponen dinamis: satu per pasar, per provider, per aliran. Namanya tidak
#: bisa dieja di muka, jadi prefiksnya yang didaftarkan.
RECOVERABLE_PREFIXES: tuple[str, ...] = (
    "provider:",
    "candles:",
    "stream:",
)


def is_record_critical(component: str) -> bool:
    """Apakah kegagalan komponen ini mengancam catatan?

    Tidak dikenal berarti **ya**. Lihat catatan modul: arah defaultnya sengaja
    berbahaya, supaya pemeriksaan yang lupa diklasifikasikan berisik alih-alih
    diam.
    """
    if component in RECORD_CRITICAL:
        return True
    if component in RECOVERABLE:
        return False
    return not component.startswith(RECOVERABLE_PREFIXES)


def severity_for(component: str, status: HealthStatus) -> EventSeverity:
    """Tingkat keparahan untuk satu peralihan status.

    DEGRADED tetap WARNING untuk semua komponen, termasuk yang kritis: sebuah
    database yang lambat belum kehilangan apa pun, dan menaikkannya ke CRITICAL
    akan mengembalikan persis kebisingan yang modul ini hapus.
    """
    if status is HealthStatus.DOWN:
        return (
            EventSeverity.CRITICAL
            if is_record_critical(component)
            else EventSeverity.WARNING
        )
    if status is HealthStatus.DEGRADED:
        return EventSeverity.WARNING
    return EventSeverity.INFO


__all__ = [
    "RECORD_CRITICAL",
    "RECOVERABLE",
    "RECOVERABLE_PREFIXES",
    "is_record_critical",
    "severity_for",
]
