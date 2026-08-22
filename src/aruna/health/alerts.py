"""Kapan sebuah perubahan health layak membangunkan operator (PASAL 17, 18).

Versi pertama mengirim pesan pada **setiap** peralihan status. Terukur di
lapangan, pada jaringan yang sedang lambat:

    05:28:52  provider:binance-spot  DOWN       no response within 15s
    05:30:14  provider:binance-spot  DEGRADED   skew jam venue +15.0s
    05:31:31  provider:binance-spot  DOWN       no response within 15s
    05:32:09  provider:binance-spot  DEGRADED   skew jam venue +15.0s
    05:33:32  provider:binance-spot  DOWN       no response within 15s

Lima pesan dalam lima menit tentang satu keadaan yang tidak berubah: jaringan
sedang buruk. Dan karena DEGRADED terhitung "operasional", separuh di antaranya
terkirim dengan judul **HEALTH PULIH** - memberi tahu operator bahwa sesuatu
telah pulih, dua kali, padahal tidak pernah pulih.

Kebijakan di sini menuruti bunyi spec, bukan menambah ambang baru:

* **Alert hanya saat masuk DOWN.** DEGRADED adalah peringatan, bukan padam.
* **Pulih hanya saat kembali UP.** DOWN yang menjadi DEGRADED belum pulih -
  ia masih rusak, hanya dengan cara yang berbeda.
* **DEGRADED tidak memicu apa pun.** Ia terbaca di ``/status`` kapan saja
  operator ingin melihatnya.
* **Satu komponen tidak bisa membangunkan berkali-kali dalam jendela pendek.**
  Sebuah komponen yang berkedip DOWN-UP-DOWN sepuluh kali dalam semenit adalah
  satu insiden, bukan sepuluh.

Yang sengaja TIDAK dilakukan: menahan alert pertama sampai N sapuan berturut
(debounce). Sebuah database yang benar-benar mati harus diberitahukan sekarang,
bukan tiga menit lagi - dan penundaan itu tidak bisa dibedakan dari sistem yang
tidak memperhatikan. Yang diredam adalah PENGULANGAN, bukan berita pertamanya.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from aruna.core.enums import HealthStatus
from aruna.health.severity import is_record_critical

#: Satu komponen tidak membangunkan operator lebih sering daripada ini.
ALERT_COOLDOWN = timedelta(minutes=15)

#: Komponen yang tetap membangunkan operator meski tidak mengancam catatan.
#:
#: PASAL 36 menyebutnya langsung: **jangan diam kalau feed mati.** Aliran yang
#: putus tidak merusak apa pun yang sudah tersimpan - jadi menurut garis
#: "catatannya terancam" ia mestinya ikut diredam - tapi pasal itu bukan soal
#: catatan, ia soal operator yang mengira ARUNA sedang mengamati pasar padahal
#: tidak ada harga yang masuk.
#:
#: Dua instruksi operator berbenturan di sini: "hanya yang kritis yang
#: mendorong ke HP", dan pasal ini. Yang lebih spesifik menang untuk kasus yang
#: disebutnya, dan yang umum berlaku untuk sisanya. Sebuah test di
#: ``tests/test_health_stream.py`` memang dipasang sebagai kawat pemicu untuk
#: perubahan ini, dan ia berbunyi tepat pada waktunya.
#: ``candles:`` ikut di sini karena ia kegagalan yang sama diucapkan berbeda.
#: Aliran yang putus dan candle yang berhenti masuk sama-sama berarti tidak ada
#: harga baru yang tiba; yang satu diperiksa pada soket, yang lain pada barisan
#: bar. Meredam salah satunya saja akan membuat "feed mati" terdengar atau
#: tidak terdengar tergantung pemeriksaan mana yang kebetulan menangkapnya
#: lebih dulu.
ALWAYS_PUSH_PREFIXES: tuple[str, ...] = ("stream:", "candles:")


@dataclass(frozen=True, slots=True)
class AlertDecision:
    """Apa yang layak dikirim, dan apa yang ditahan beserta alasannya."""

    alerts: tuple[Any, ...] = ()
    recoveries: tuple[Any, ...] = ()
    suppressed: tuple[str, ...] = ()

    @property
    def anything(self) -> bool:
        return bool(self.alerts or self.recoveries)


@dataclass(slots=True)
class HealthAlertPolicy:
    """Menentukan peralihan mana yang dikirim. Tidak mengirim apa pun sendiri."""

    cooldown: timedelta = ALERT_COOLDOWN
    #: Hanya komponen yang kegagalannya mengancam catatan yang membangunkan
    #: operator lewat Telegram.
    #:
    #: Garis pemisahnya sudah ada dan sudah dipakai untuk tingkat keparahan di
    #: log (:mod:`aruna.health.severity`); yang berubah di sini adalah garis
    #: yang sama ikut menentukan apa yang mendorong notifikasi ke ponsel.
    #:
    #: **Harganya dinyatakan, bukan disembunyikan: gangguan provider yang
    #: berlangsung berjam-jam tidak akan sampai ke ponsel.** ARUNA yang menolak
    #: menerbitkan signal karena providernya tak terjangkau sedang berperilaku
    #: benar dan catatannya tetap utuh - tapi operator yang mengira ARUNA
    #: sedang mengamati pasar tidak akan tahu bedanya dari layar kunci.
    #:
    #: Yang tersisa untuk menutupnya: statusnya tetap terbaca kapan saja lewat
    #: ``/status``, tetap tercatat sebagai peristiwa, dan tetap muncul di
    #: laporan harian yang memang memuat baris per komponen.
    push_record_critical_only: bool = True
    #: Komponen yang operatornya sudah diberi tahu sedang DOWN.
    _announced: set[str] = field(default_factory=set)
    #: Kapan terakhir sebuah komponen membangunkan operator.
    _last_alert: dict[str, datetime] = field(default_factory=dict)

    def decide(
        self, changed: Iterable[Any], *, now: datetime
    ) -> AlertDecision:
        alerts: list[Any] = []
        recoveries: list[Any] = []
        ditahan: list[str] = []

        for component in changed:
            nama = component.name
            status = component.status

            # Disaring sebelum cabang mana pun, dan itu penting: komponen yang
            # tidak pernah diumumkan DOWN juga tidak boleh mengirim "PULIH".
            # Menaruh saringan ini di dalam cabang DOWN saja akan menghasilkan
            # pesan pemulihan untuk sesuatu yang kejatuhannya tidak pernah
            # diberitahukan - kabar baik tentang kabar buruk yang tak pernah
            # sampai.
            if (
                self.push_record_critical_only
                and not is_record_critical(nama)
                and not nama.startswith(ALWAYS_PUSH_PREFIXES)
            ):
                ditahan.append(
                    f"{nama}: {status.value}, tidak mengancam catatan"
                )
                continue

            if status is HealthStatus.DOWN:
                if nama in self._announced:
                    # Sudah diberitahukan dan belum pulih. Kedipan kembali ke
                    # DOWN bukan insiden baru.
                    ditahan.append(f"{nama}: sudah diberitahukan DOWN")
                    continue
                terakhir = self._last_alert.get(nama)
                if terakhir is not None and now - terakhir < self.cooldown:
                    # Berkedip DOWN-UP-DOWN adalah satu insiden, bukan dua.
                    ditahan.append(f"{nama}: masih dalam jeda alert")
                    self._announced.add(nama)
                    continue
                self._announced.add(nama)
                self._last_alert[nama] = now
                alerts.append(component)
                continue

            if status is HealthStatus.UP:
                if nama in self._announced:
                    self._announced.discard(nama)
                    recoveries.append(component)
                continue

            # DEGRADED, DISABLED, UNKNOWN. Tidak membangunkan siapa pun, dan
            # yang lebih penting: TIDAK menghapus tanda "sudah diberitahukan".
            # Menghapusnya akan membuat DOWN berikutnya terhitung insiden baru,
            # dan goyangan DOWN-DEGRADED-DOWN kembali menjadi banjir pesan.
            ditahan.append(f"{nama}: {status.value} bukan padam maupun pulih")

        return AlertDecision(
            alerts=tuple(alerts),
            recoveries=tuple(recoveries),
            suppressed=tuple(ditahan),
        )

    def state(self) -> dict[str, Any]:
        return {
            "announced_down": sorted(self._announced),
            "cooldown_sec": self.cooldown.total_seconds(),
            "push_record_critical_only": self.push_record_critical_only,
        }


__all__ = [
    "ALERT_COOLDOWN",
    "ALWAYS_PUSH_PREFIXES",
    "AlertDecision",
    "HealthAlertPolicy",
]
