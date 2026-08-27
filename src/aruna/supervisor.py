"""Penjaga proses: satu perintah, terus hidup (PASAL 37).

Loop di dalam ARUNA sudah tahan terhadap tick yang gagal - itu sifat yang
dibangun sejak awal. Yang tidak bisa dijaga dari dalam adalah proses yang
benar-benar mati: OOM, koneksi database yang putus saat startup, Python yang
dimatikan Windows, atau bug yang lolos ke luar ``main``. Dari dalam proses,
kematian itu tidak bisa dilaporkan - tidak ada yang tersisa untuk melapor.

Karena itu penjaganya di luar. Ia tidak menganalisis apa pun; ia menyalakan
kembali apa yang mati, dan menghitung berapa kali.

**Ini tetap ANALIS, bukan eksekutor.** Yang dijaga hidup adalah proses
pembacaan dan analisis. Tidak ada order yang dikirim, tidak ada leverage yang
diubah, tidak ada dana yang berpindah (PASAL 41).

Tiga sifat yang membentuknya:

**Restart yang melambat.** Proses yang mati dalam dua detik dan dinyalakan
ulang seketika adalah hot loop yang membakar CPU sambil terlihat seperti
sistem yang hidup. Jedanya naik dua kali lipat sampai batas, dan hanya
di-reset kalau prosesnya benar-benar sempat hidup lama.

**Gagal berulang itu diteriakkan, bukan disembunyikan.** PASAL 37 menuntut
CRITICAL ALERT kalau sebuah service gagal berkali-kali. Penjaga yang diam-diam
menyalakan ulang seribu kali membuat kerusakan permanen terlihat seperti
uptime.

**Berhenti kalau diminta.** Ctrl-C menghentikan penjaga DAN anak-anaknya. Satu
penjaga yang selamat dari Ctrl-C lalu menyalakan ulang apa yang baru saja
dimatikan operator adalah hal yang paling menjengkelkan yang bisa dilakukan
sebuah supervisor.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from aruna.core.clock import isoformat, now_utc
from aruna.core.logging import get_logger

log = get_logger("aruna.supervisor")

#: Jeda sebelum menyalakan ulang, detik. Naik dua kali lipat sampai batas.
RESTART_MIN_SEC = 2.0
RESTART_MAX_SEC = 120.0

#: Hidup selama ini dianggap "benar-benar jalan", jadi jedanya boleh di-reset.
#: Tanpa syarat ini, proses yang mati tiap tiga menit akan selamanya dianggap
#: baru saja pulih dan tidak pernah melambat.
HEALTHY_UPTIME_SEC = 300.0

#: Kematian beruntun sebanyak ini tanpa pernah sehat = CRITICAL (PASAL 37).
CRITICAL_RESTARTS = 5


@dataclass(slots=True)
class ChildSpec:
    """Satu proses yang dijaga hidup."""

    name: str
    args: list[str]


@dataclass(slots=True)
class ChildState:
    spec: ChildSpec
    restarts: int = 0
    #: Kematian beruntun tanpa pernah mencapai ``HEALTHY_UPTIME_SEC``.
    consecutive: int = 0
    started_at: datetime | None = None
    last_exit_code: int | None = None
    critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "restarts": self.restarts,
            "consecutive": self.consecutive,
            "started_at": isoformat(self.started_at) if self.started_at else None,
            "last_exit_code": self.last_exit_code,
            "critical": self.critical,
        }


@dataclass(slots=True)
class Supervisor:
    """Menjaga sekumpulan proses tetap hidup sampai diminta berhenti."""

    children: list[ChildSpec]
    python: str = sys.executable
    cwd: str | None = None
    #: Menjalankan satu anak sampai selesai, mengembalikan exit code.
    #:
    #: Seam yang dirancang, bukan celah untuk ditambal: tanpa ini satu-satunya
    #: cara menguji restart, backoff, dan vonis CRITICAL adalah menyalakan
    #: proses sungguhan dan membunuhinya - lambat, rapuh, dan perilaku yang
    #: hanya bisa diuji begitu adalah perilaku yang tidak pernah diuji.
    runner: Any = None
    state: dict[str, ChildState] = field(default_factory=dict)
    _stopping: asyncio.Event | None = None

    def __post_init__(self) -> None:
        self.state = {c.name: ChildState(spec=c) for c in self.children}

    async def run(self, *, sleep: Any = None) -> None:
        rest = sleep or asyncio.sleep
        self._stopping = asyncio.Event()
        _install_signal_handlers(self._stopping)

        log.info(
            "supervisor.started",
            children=[c.name for c in self.children],
            detail="analysis only; no order is placed and no funds move",
        )
        try:
            await asyncio.gather(
                *(self._keep_alive(child, rest) for child in self.children)
            )
        finally:
            log.info(
                "supervisor.stopped",
                state=[s.to_dict() for s in self.state.values()],
            )

    async def _keep_alive(self, spec: ChildSpec, rest: Any) -> None:
        state = self.state[spec.name]
        delay = RESTART_MIN_SEC

        while not self._stopping.is_set():
            state.started_at = now_utc()
            log.info("supervisor.child_started", child=spec.name)
            run = self.runner or self._run_once
            code = await run(spec)
            uptime = (now_utc() - state.started_at).total_seconds()
            state.last_exit_code = code

            if self._stopping.is_set():
                return

            state.restarts += 1
            if uptime >= HEALTHY_UPTIME_SEC:
                # Sempat hidup lama, jadi kematian ini berdiri sendiri - bukan
                # bagian dari rentetan.
                state.consecutive = 0
                delay = RESTART_MIN_SEC
            else:
                state.consecutive += 1

            if state.consecutive >= CRITICAL_RESTARTS and not state.critical:
                state.critical = True
                log.critical(
                    "supervisor.child_failing",
                    child=spec.name,
                    consecutive=state.consecutive,
                    exit_code=code,
                    uptime_sec=round(uptime, 1),
                    detail=(
                        "restarted repeatedly without ever staying up; this is "
                        "a fault that restarting will not fix"
                    ),
                )
            elif state.consecutive == 0:
                state.critical = False

            log.warning(
                "supervisor.child_exited",
                child=spec.name,
                exit_code=code,
                uptime_sec=round(uptime, 1),
                restarts=state.restarts,
                consecutive=state.consecutive,
                retry_in_sec=delay,
            )
            await rest(delay)
            delay = min(delay * 2, RESTART_MAX_SEC)

    async def _run_once(self, spec: ChildSpec) -> int | None:
        proc = await asyncio.create_subprocess_exec(
            self.python,
            *spec.args,
            cwd=self.cwd,
            # Diwarisi, tidak ditangkap: anak menulis ke konsol yang sama, jadi
            # operator melihat apa yang terjadi tanpa harus membuka berkas log.
            # Menangkapnya lalu tidak membacanya akan mengisi pipe sampai penuh
            # dan menggantung anaknya - kegagalan yang terlihat seperti hang.
            stdout=None,
            stderr=None,
        )
        try:
            return await proc.wait()
        except asyncio.CancelledError:
            _terminate(proc)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=15)
            raise
        finally:
            if proc.returncode is None:
                _terminate(proc)


class AlreadyRunning(RuntimeError):
    """ARUNA sudah jalan di proses lain."""


@contextlib.contextmanager
def single_instance(path: Path) -> Iterator[None]:
    """Pastikan cuma ada satu ARUNA hidup, atau menolak menyala.

    Sesudah autostart terpasang, ada dua jalan menuju proses yang sama: tugas
    terjadwal saat login, dan klik dua kali ``ARUNA.bat``. Kalau keduanya
    jalan, dua bot Telegram menarik update dari antrean yang sama - Telegram
    menjawab 409 dan pesan hilang bergantian - dan ingest berjalan dobel.
    Kerusakannya diam: sistemnya terlihat hidup, cuma separuh perintah yang
    sampai.

    Kuncinya kunci OS, bukan berkas PID. Berkas PID bertahan sesudah crash dan
    memblokir proses berikutnya sampai seseorang menghapusnya manual - jadi
    mekanisme yang dipasang untuk menjaga uptime malah menjadi sebab downtime.
    Kunci OS dilepas kernel begitu prosesnya mati, apa pun sebabnya.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        try:
            _lock_exclusive(handle)
        except OSError as exc:
            raise AlreadyRunning(
                f"ARUNA sudah jalan (kunci dipegang: {path}). "
                "Tutup jendela ARUNA yang lain, atau hentikan tugas terjadwal "
                "dengan: schtasks /end /tn ARUNA"
            ) from exc
        yield
    finally:
        handle.close()


def _lock_exclusive(handle: Any) -> None:
    """Kunci eksklusif non-blocking; ``OSError`` kalau sudah dipegang."""
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _terminate(proc: Any) -> None:
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.terminate()


def _install_signal_handlers(stopping: asyncio.Event) -> None:
    """Ctrl-C menghentikan penjaga, bukan cuma satu anaknya."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except NotImplementedError:
            # Windows ProactorEventLoop tidak punya add_signal_handler; jalur
            # sinkronnya harus melompat balik ke thread loop.
            signal.signal(
                sig, lambda *_: loop.call_soon_threadsafe(stopping.set)
            )


def default_children(symbols: str, *, hours: float) -> list[ChildSpec]:
    """Dua proses yang membentuk ARUNA yang berjalan.

    ``aruna run`` memegang bot, ingest, aliran, dan loop upkeep. Futures loop
    berdiri sendiri karena ia punya cadence dan universe sendiri - dan karena
    salah satunya mati tidak boleh menyeret yang lain.

    Horizonnya dibaca dari satu tempat, bukan diketik di sini. Yang menghitung
    korelasi untuk keputusan ini adalah proses yang lain
    (:class:`~aruna.upkeep.korelasi.PenyegarKorelasi`), dan dua angka yang
    berdiri sendiri akan berselisih tanpa satu pun error - tabel terisi rapi di
    interval yang tidak pernah ditanyakan siapa pun.
    """
    from aruna.core.config import get_settings
    from aruna.upkeep.korelasi import HORIZON_KEPUTUSAN

    # **Ekuitasnya dibaca, bukan diketik.** Sampai 2026-08-25 baris di bawah
    # berbunyi `"--equity", "10000"`, dan angka itu tidak pernah cocok dengan
    # akun operator. Ukuran posisi dihitung dari persen ekuitas, jadi ekuitas
    # yang salah membuat setiap notional salah dengan rapi - tanpa satu pun
    # error, dan hanya terasa sebagai floating loss yang tidak masuk akal.
    ekuitas = get_settings().upkeep.futures_equity
    # **Risikonya dibaca juga, dengan alasan yang sama.** Sampai 2026-08-25
    # baris ini tidak ada sama sekali, jadi `--risk` tetap None dan yang berlaku
    # `futures.risk.DEFAULT_RISK_PCT` = 0,5% - seperempat dari 2% yang operator
    # tetapkan. Ekuitas yang benar dengan risiko yang salah tetap menghasilkan
    # ukuran posisi yang salah; keduanya harus sampai.
    risiko = get_settings().upkeep.futures_risk_pct

    return [
        ChildSpec(name="aruna-run", args=["-m", "aruna.cli", "run"]),
        ChildSpec(
            name="futures-loop",
            args=[
                "-m", "aruna.cli", "futures-loop", symbols,
                "--horizon", HORIZON_KEPUTUSAN.value,
                "--hours", str(hours),
                "--interval", "900",
                "--equity", f"{ekuitas:g}",
                "--risk", f"{risiko:g}",
            ],
        ),
        # Proses ketiga, sejajar futures dan sama-sama berdiri sendiri.
        #
        # **Tanpa --equity dan --risk, dan itu disengaja.** ARUNA di jalur XAU
        # adalah analis arah; ia tidak menghitung ukuran posisi, notional, atau
        # margin. Menyalin argumen futures ke sini akan membuat dua modul
        # terlihat berbagi model risiko yang sebenarnya tidak ada di salah
        # satunya.
        #
        # Intervalnya 300 detik karena itu satu bar M5 - cadence keputusannya,
        # bukan angka yang dipilih supaya terasa sering.
        ChildSpec(
            name="xau-loop",
            args=[
                "-m", "aruna.cli", "xau-loop",
                "--hours", str(hours),
                "--interval", "300",
            ],
        ),
    ]


#: Kunci instans tunggal. Di ``logs/`` karena folder itu sudah diabaikan git
#: dan sudah pasti bisa ditulis oleh proses yang sama.
LOCK_PATH = Path("logs") / "aruna.lock"


__all__ = [
    "CRITICAL_RESTARTS",
    "HEALTHY_UPTIME_SEC",
    "LOCK_PATH",
    "RESTART_MAX_SEC",
    "RESTART_MIN_SEC",
    "AlreadyRunning",
    "ChildSpec",
    "ChildState",
    "Supervisor",
    "default_children",
    "single_instance",
]
