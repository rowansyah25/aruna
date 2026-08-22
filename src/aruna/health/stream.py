"""Kesehatan aliran WebSocket (PASAL 3, 4, 35, 36).

Aliran punya satu bentuk kegagalan yang tidak dimiliki poll, dan justru itu
yang paling mahal: **tersambung dan senyap**. Sebuah poll yang gagal
mengembalikan galat - ada yang bisa dilihat. Sebuah socket yang menggantung
tidak mengembalikan apa pun, dan dari luar terlihat persis seperti pasar yang
sedang sepi. Diukur pada jaringan ini, futures Binance melakukan tepat itu:
menerima koneksi, menjawab SUBSCRIBE, lalu diam.

Karena itu komponen ini tidak pernah menyimpulkan sehat dari "tersambung".
Ia menanyakan umur kutipan terakhir per simbol, dan umur itulah vonisnya.

PASAL 3 melarang mengklaim nol gap, jadi tidak ada di sini yang menyatakan
aliran selalu tersambung. Yang dinyatakan adalah: putusnya terlihat, umurnya
terukur, dan angkanya apa adanya.
"""

from __future__ import annotations

from typing import Any

from aruna.core.enums import HealthStatus
from aruna.health.models import ComponentHealth

#: Umur kutipan **paling segar** yang masih berarti aliran hidup.
#:
#: Dipakai pada yang termuda, bukan pada tiap simbol - dan itu perbaikan atas
#: bentuk sebelumnya, yang membandingkan ambang ini dengan umur SETIAP simbol.
#: Bentuk lama benar selama daftarnya berisi lima pasangan teramai; ia salah
#: begitu daftarnya menjadi dua puluh.
#:
#: Terukur dari 1000 perdagangan terakhir tiap simbol di daftar sekarang:
#: BTC, ETH dan SOL tidak pernah berjeda di atas sepuluh detik (0,0% waktu),
#: sementara APT berjeda selama itu 78% waktu dan pernah 293 detik. Rata-rata
#: 5,6 dari 20 simbol terlihat basi pada saat acak - 28% - jadi ambang lama
#: melaporkan DEGRADED hampir sepanjang waktu, dan pada malam sepi bisa
#: mendekati ambang DOWN 50% dan menyatakan aliran mati padahal ia mengalir.
#:
#: Yang membuat "paling segar" benar: socket yang menggantung membungkam SEMUA
#: simbol sekaligus, termasuk BTC. Jadi selama ada satu kutipan muda, aliran
#: itu hidup - dan kecepatan mendeteksi gantungnya tidak berkurang sedikit pun,
#: karena BTC mencetak beberapa kali per detik.
STALE_QUOTE_SEC = 10.0

#: Umur yang tidak bisa dijelaskan pasar sepi: langganan simbolnya mati.
#:
#: Jeda terpanjang yang benar-benar terukur di seluruh daftar adalah 293 detik
#: (APT). Ambang ini tiga kali lipatnya. Di bawahnya, diam adalah fakta tentang
#: pasar; di atasnya, ia fakta tentang langganan yang putus - dan itu bentuk
#: kegagalan yang nyata: aliran bisa kehilangan sebagian langganannya sementara
#: socket-nya tetap sehat dan simbol lain tetap mengalir.
DEAD_SUBSCRIPTION_SEC = 900.0

#: Bagian simbol yang boleh basi sebelum vonisnya DOWN, bukan DEGRADED. Satu
#: simbol sepi berbeda dari seluruh aliran mati, dan menyamakan keduanya
#: membuat operator belajar mengabaikan warnanya.
DOWN_STALE_SHARE = 0.5


class StreamCheck:
    """Aliran spot Binance, dinilai dari umur data - bukan dari status socket."""

    def __init__(self, stream: Any, *, background: bool = True) -> None:
        self._stream = stream
        self._background = background
        self.name = "stream:binance-spot"

    async def check(self) -> ComponentHealth:
        if self._stream is None:
            # Tidak dirangkai sama sekali. Dinyatakan, bukan dilaporkan sehat:
            # nol simbol basi karena tidak ada simbol adalah nol yang berarti
            # "tidak ditanya" (SPEC 4, 49).
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.UP,
                message=(
                    "tidak aktif: tidak ada aset CRYPTO aktif, atau streaming "
                    "belum dirangkai - harga tetap diambil lewat poll REST"
                ),
                details={"wired": False},
            )

        state = self._stream.state()
        details: dict[str, Any] = dict(state)

        if not self._background:
            # Aturan A: perintah CLI pendek tidak menjalankan loop. Aliran yang
            # mati di sini adalah keadaan yang benar, bukan cacat.
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.UP,
                message="tidak dijalankan pada proses sekali-jalan (background=False)",
                details=details,
            )

        if not self._stream.running:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    "task aliran tidak berjalan - harga realtime tidak masuk; "
                    "poll REST masih jalan sebagai cadangan"
                ),
                details=details,
            )

        ages: dict[str, float | None] = state.get("ages_sec") or {}
        if not self._stream.connected:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    f"terputus, {state.get('disconnects', 0)} kali sejauh ini - "
                    "sedang menyambung ulang dengan backoff"
                ),
                details=details,
            )

        never = [symbol for symbol, age in ages.items() if age is None]
        # Basi = jauh lebih lama daripada jeda perdagangan mana pun yang pernah
        # terukur di daftar ini, bukan sekadar lebih lama dari sepuluh detik.
        # Sebuah koin yang sepi sepuluh detik adalah fakta tentang pasar.
        stale = [
            symbol
            for symbol, age in ages.items()
            if age is not None and age > DEAD_SUBSCRIPTION_SEC
        ]
        total = len(ages) or 1

        # Kutipan termuda. Inilah yang menjawab "apakah socket-nya hidup":
        # gantungnya socket membungkam semua simbol serentak, jadi satu kutipan
        # muda saja sudah membuktikan ia mengalir.
        umur_ada = [age for age in ages.values() if age is not None]
        termuda = min(umur_ada) if umur_ada else None
        if termuda is not None and termuda > STALE_QUOTE_SEC:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    f"tidak satu pun dari {total} simbol mengirim kutipan "
                    f"dalam {termuda:.0f} detik terakhir - aliran menggantung, "
                    "bukan pasar yang sepi"
                ),
                details=details,
            )

        if never and len(never) == len(ages):
            # Tersambung dan belum satu pun kutipan tiba. Ini bentuk
            # menggantung yang persis dialami futures di jaringan ini, dan ia
            # terlihat sehat dari sisi socket.
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    "tersambung tapi belum ada satu kutipan pun - aliran "
                    "menggantung, bukan pasar yang sepi"
                ),
                details=details,
            )

        problems: list[str] = []
        if never:
            problems.append(
                f"{len(never)} simbol belum pernah mengirim kutipan: "
                + ", ".join(sorted(never)[:4])
            )
        if stale:
            oldest = max(ages[s] or 0.0 for s in stale)
            problems.append(
                f"{len(stale)} dari {total} langganan simbol diam di atas "
                f"{DEAD_SUBSCRIPTION_SEC / 60:.0f} menit "
                f"(terlama {oldest / 60:.0f} menit): "
                + ", ".join(sorted(stale)[:4])
            )
        if state.get("snapshot_failures"):
            problems.append(
                f"{state['snapshot_failures']} snapshot REST gagal - lubang "
                "sesudah sambung ulang tidak tertutup"
            )

        if not problems:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.UP,
                message=(
                    f"{total} simbol mengalir, {state.get('messages', 0)} pesan"
                ),
                details=details,
            )

        share = (len(never) + len(stale)) / total
        status = (
            HealthStatus.DOWN if share >= DOWN_STALE_SHARE else HealthStatus.DEGRADED
        )
        return ComponentHealth(
            name=self.name, status=status, message="; ".join(problems), details=details
        )


__all__ = [
    "DEAD_SUBSCRIPTION_SEC",
    "DOWN_STALE_SHARE",
    "STALE_QUOTE_SEC",
    "StreamCheck",
]
