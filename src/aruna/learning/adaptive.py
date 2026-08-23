"""Satu putaran pembelajaran adaptif (PASAL 12.27).

Menjalankan urutan yang spec-nya minta - sejarah, penemuan pola, performa
strategi, spesialisasi agent - lalu menyimpan hasilnya dan mencatat peristiwa
yang perlu bisa diaudit.

**Yang TIDAK dilakukan modul ini, dan itu seluruh maksudnya:** ia tidak
mengubah satu pun bobot, ambang, atau parameter. PASAL 11.16 dan 12.26
melarang modifikasi model otomatis, dan larangan itu ditegakkan di sini dengan
cara paling sederhana yang ada - tidak ada satu pun jalur tulis dari modul ini
menuju konfigurasi, bobot agent, atau ambang keputusan. Yang bisa ditulisnya
hanya empat tabel hasil analisis.

Sebuah usulan perubahan berjalan lewat proposal dan persetujuan operator
(PASAL 12.19, 12.20), dan jalur itu sudah ada sejak Phase 10.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from aruna.core.clock import now_utc
from aruna.core.logging import get_logger
from aruna.learning.evidence import Evidence
from aruna.learning.patterns import Discovery, Observation, discover, quality_band
from aruna.learning.specialization import (
    AgentProfile,
    Vote,
    build_profiles,
    specialists,
)
from aruna.learning.strategies import ALL as KATALOG
from aruna.learning.strategies import classify
from aruna.learning.wins import WinStudy, study

log = get_logger("aruna.learning.adaptive")

#: Versi mesin pembelajaran. Ikut menjadi kunci tiap baris hasil, supaya hasil
#: dari dua versi tidak pernah tercampur dalam satu rata-rata (PASAL 12.21).
LEARNING_VERSION = "learn-12.0"


@dataclass(frozen=True, slots=True)
class StrategySlice:
    """Performa satu strategi pada satu irisan."""

    strategy_code: str
    slice_key: str
    dimensions: dict[str, str]
    evidence: Evidence
    net_pnl: Decimal = Decimal(0)
    max_drawdown: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class LearningRun:
    """Hasil satu putaran, dalam bentuk yang bisa dilaporkan."""

    discovery: Discovery
    wins: WinStudy
    profiles: tuple[AgentProfile, ...] = field(default_factory=tuple)
    strategies: tuple[StrategySlice, ...] = field(default_factory=tuple)
    lifecycle: Any = None
    observations: int = 0
    stored_patterns: int = 0
    events: int = 0

    @property
    def specialists(self) -> dict[str, str]:
        return specialists(self.profiles)

    def summary(self) -> str:
        return (
            f"{self.observations} prediksi terskor dipelajari; "
            f"{self.discovery.summary()}"
        )


def _observation(row: dict[str, Any]) -> Observation:
    return Observation(
        market=str(row.get("market_code") or "UNKNOWN"),
        symbol=str(row.get("symbol") or "UNKNOWN"),
        horizon=str(row.get("horizon_code") or "UNKNOWN"),
        direction=str(row.get("direction") or "UNKNOWN"),
        regime=str(row.get("regime") or "UNKNOWN"),
        quality_band=quality_band(
            float(row["signal_quality"])
            if row.get("signal_quality") is not None
            else None
        ),
        won=row.get("result") == "WIN",
    )


def drawdown(pnls: list[Decimal]) -> Decimal:
    """Penurunan terdalam dari puncak kumulatif.

    Dihitung dari urutan yang diberikan pemanggil, dan pemanggil bertanggung
    jawab mengurutkannya menurut waktu. Drawdown atas urutan yang salah adalah
    angka yang terlihat benar dan tidak berarti apa-apa.

    **Publik sejak 2026-08-23**, dan bukan demi kerapian:
    :mod:`aruna.router.pengukuran` menulis baris ``strategy_performance`` juga,
    dan drawdown yang dihitung dua kali dengan dua rumus adalah dua angka yang
    harus tetap sepakat selamanya. Satu rumus, satu tempat.
    """
    puncak = Decimal(0)
    kumulatif = Decimal(0)
    terdalam = Decimal(0)
    for p in pnls:
        kumulatif += p
        puncak = max(puncak, kumulatif)
        terdalam = min(terdalam, kumulatif - puncak)
    return -terdalam


def _strategy_slices(rows: list[dict[str, Any]]) -> tuple[StrategySlice, ...]:
    """Performa per strategi, dan per strategi x rezim.

    Diurutkan menurut waktu penyelesaian sebelum drawdown dihitung - lihat
    :func:`drawdown`.
    """
    berurut = sorted(
        rows, key=lambda r: (r.get("resolved_at") or datetime.min)
    )
    ember: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in berurut:
        kode = classify(r.get("regime"), horizon=r.get("horizon_code"))
        ember.setdefault((kode, "ALL"), []).append(r)
        ember.setdefault((kode, str(r.get("regime") or "UNKNOWN")), []).append(r)

    hasil: list[StrategySlice] = []
    for (kode, regime), anggota in ember.items():
        bukti = Evidence(
            wins=sum(1 for a in anggota if a.get("result") == "WIN"),
            losses=sum(1 for a in anggota if a.get("result") == "LOSS"),
        )
        pnls = [Decimal(str(a.get("net_pnl") or 0)) for a in anggota]
        hasil.append(
            StrategySlice(
                strategy_code=kode,
                slice_key=f"{kode}|regime={regime}",
                dimensions={"regime": regime},
                evidence=bukti,
                net_pnl=sum(pnls, Decimal(0)),
                max_drawdown=drawdown(pnls),
            )
        )
    hasil.sort(key=lambda s: (-s.evidence.total, s.slice_key))
    return tuple(hasil)


class AdaptiveLearningService:
    """Menjalankan satu putaran pembelajaran dan menyimpan hasilnya."""

    def __init__(self, store: Any, *, version: str = LEARNING_VERSION) -> None:
        self._store = store
        self._version = version

    async def run(self, *, now: datetime | None = None) -> LearningRun:
        saat = now or now_utc()
        rows = await self._store.scored_observations()
        obs = [_observation(r) for r in rows]

        penemuan = discover(obs)
        pelajaran = study(obs)
        irisan = _strategy_slices(rows)

        suara = await self._store.agent_votes()
        profil = build_profiles(
            Vote(
                role=str(v["role"]),
                regime=str(v.get("regime") or "UNKNOWN"),
                agreed=bool(v.get("agreed_with_council")),
                abstained=bool(v.get("abstained")),
                won=v.get("result") == "WIN",
            )
            for v in suara
        )

        tersimpan = await self._store.save_patterns(
            [
                p.to_row(model_version=self._version, computed_at=saat)
                for p in penemuan.patterns
            ]
        )
        await self._store.upsert_strategies(
            [s.to_row(model_version=self._version, now=saat) for s in KATALOG]
        )
        await self._store.save_strategy_performance(
            [
                {
                    "strategy_code": s.strategy_code,
                    "slice_key": s.slice_key,
                    "dimensions": s.dimensions,
                    "wins": s.evidence.wins,
                    "losses": s.evidence.losses,
                    "sample_size": s.evidence.total,
                    # Skala kolomnya, bukan presisi penuh pembagian - lihat
                    # catatan yang sama di `Pattern.to_row`.
                    "win_rate": (
                        None if s.evidence.win_rate is None
                        else round(s.evidence.win_rate, 5)
                    ),
                    "ci_low": round(s.evidence.interval[0], 5),
                    "ci_high": round(s.evidence.interval[1], 5),
                    "evidence": s.evidence.level.value,
                    "net_pnl": s.net_pnl,
                    "max_drawdown": s.max_drawdown,
                    "model_version": self._version,
                    "computed_at": saat,
                }
                for s in irisan
            ]
        )

        # PASAL 12.15: daur hidup strategi dinilai ulang dari hasil yang baru
        # saja disimpan di atas - bukan dari hasil putaran sebelumnya.
        daur = await self._assess_lifecycle(saat)

        # Satu peristiwa untuk putaran ini, bukan satu per pola. PASAL 12.22
        # meminta yang penting saja; seratus baris "pola ditemukan" per hari
        # adalah log polling dengan nama lain (PASAL 12.23).
        peristiwa = 0
        if penemuan.notable:
            await self._store.record_event(
                event_type="PATTERN_DISCOVERED",
                subject="ringkasan putaran",
                summary=penemuan.summary()[:500],
                evidence={
                    "notable": [p.key for p in penemuan.notable[:20]],
                    "baseline_wins": penemuan.baseline.wins,
                    "baseline_total": penemuan.baseline.total,
                },
                sample_size=penemuan.baseline.total,
                model_version=self._version,
                occurred_at=saat,
            )
            peristiwa += 1

        log.info(
            "learning.run",
            observations=len(obs),
            patterns=len(penemuan.patterns),
            notable=len(penemuan.notable),
            specialists=len(specialists(profil)),
        )
        return LearningRun(
            discovery=penemuan,
            wins=pelajaran,
            profiles=profil,
            strategies=irisan,
            lifecycle=daur,
            observations=len(obs),
            stored_patterns=tersimpan,
            events=peristiwa,
        )

    async def _assess_lifecycle(self, saat: datetime) -> Any:
        """Nilai daur hidup katalog, terapkan yang boleh, catat yang tidak.

        **Yang diterapkan hanya label pengamatan** - ACTIVE, DEGRADED,
        UNDER_REVIEW. SUSPENDED dan RETIRED mengubah apa yang ARUNA
        pertimbangkan, bukan apa yang ia catat, dan PASAL 12.20 menaruhnya di
        tangan operator. Yang seperti itu dicatat sebagai peristiwa yang
        menunggu keputusan, tidak pernah ditulis ke kolom status.
        """
        from aruna.learning.lifecycle import evaluate

        katalog = await self._store.catalog_with_performance()
        baseline = await self._store.overall_win_rate()
        laporan = evaluate(katalog, baseline=baseline)

        for a in laporan.to_apply:
            await self._store.set_strategy_status(
                a.code, a.proposed.value, reason=a.reason, now=saat
            )
            await self._store.record_event(
                event_type="STRATEGY_STATUS_CHANGED",
                subject=a.code,
                summary=f"{a.current.value} -> {a.proposed.value}: {a.reason}",
                evidence={
                    "wins": a.evidence.wins,
                    "losses": a.evidence.losses,
                    "net_pnl": str(a.net_pnl),
                },
                sample_size=a.evidence.total,
                model_version=self._version,
                occurred_at=saat,
            )

        for a in laporan.to_propose:
            await self._store.record_event(
                event_type="PROPOSAL_SUBMITTED",
                subject=a.code,
                summary=(
                    f"usul {a.current.value} -> {a.proposed.value}: {a.reason}"
                    " (menunggu keputusan operator)"
                ),
                evidence={
                    "wins": a.evidence.wins,
                    "losses": a.evidence.losses,
                    "net_pnl": str(a.net_pnl),
                },
                sample_size=a.evidence.total,
                model_version=self._version,
                occurred_at=saat,
            )

        if laporan.to_apply or laporan.to_propose:
            log.info(
                "learning.lifecycle",
                applied=len(laporan.to_apply),
                awaiting_operator=len(laporan.to_propose),
            )
        return laporan


__all__ = [
    "LEARNING_VERSION",
    "AdaptiveLearningService",
    "LearningRun",
    "StrategySlice",
]
