# XAUUSD M5 — Rencana 2: Mesin Sinyal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dari tumpukan timeframe XAU yang sudah dinyatakan layak, hasilkan `BUY` / `SELL` / `NO SIGNAL` yang setiap suaranya, buktinya, dan alasan penolakannya tersimpan.

**Architecture:** Merangkai, bukan menduplikasi. `AnalysisEngine` dan `DeliberationEngine` keduanya murni dan bebas DB, jadi modul XAU memakainya apa adanya: candle → `TechnicalSnapshot` → `DecisionContext` → `Deliberation`. Yang ditambahkan XAU hanya yang memang belum ada — penerjemahan suara ke AGREE/DISAGREE/NEUTRAL, gerbang khas XAU, cooldown, dan tabel `xau_*`.

**Tech Stack:** Python 3.13, MySQL 8, pytest. Tidak ada dependensi baru.

## Global Constraints

Sama dengan Rencana 1, ditambah yang khusus berlaku di sini:

- Keputusan: **`BUY` / `SELL` / `NO SIGNAL`**. `LONG`, `SHORT`, `WAIT` dilarang **di keluaran XAU**.
- **Keputusan final berasal dari XAUUSD M5.** M15 konfirmasi, H1 tren, H4 konteks besar.
- Setiap fitur **timestamp-safe**; dilarang memakai data masa depan.
- Likuiditas adalah **bukti**, bukan aturan. Dilarang menulis "liquidity sweep = otomatis BUY/SELL".
- Sesi ASIA/LONDON/NEW YORK/OVERLAP adalah bukti. Dilarang "London = BUY, New York = SELL".
- DXY/yield adalah bukti. Dilarang "DXY naik = pasti SELL XAUUSD".
- Suara agen disimpan sebagai **AGREE / DISAGREE / NEUTRAL**.
- Kontradiksi terlalu tinggi → `NO SIGNAL`. RR tidak layak → `NO SIGNAL`.
- **Simpan seluruh hasil. Jangan menghapus LOSS.**
- Satu setup tidak boleh menghasilkan spam sinyal (cooldown).
- **JANGAN MERUSAK FUTURES.** Tidak ada berkas di `src/aruna/futures/` yang disunting.

---

## Yang sudah ada dan dipakai apa adanya

Diverifikasi terhadap `main` di `9ae97f3`, 2026-08-27:

| Komponen | Tanda tangan terverifikasi | Kenapa dipakai |
|---|---|---|
| `CandleSeries.from_candles` | `(candles: list[Candle]) -> CandleSeries` | Membuang bar belum tutup sendiri, melaporkan `excluded_open_bars` |
| `AnalysisEngine().analyse` | `(series: CandleSeries) -> TechnicalSnapshot` | Murni, bebas DB. 16 indikator + struktur + rezim |
| `TechnicalSnapshot.as_of` | `datetime` | **Close** bar tersettle terbaru, bukan open — jangkar SPEC 24 |
| `DeliberationEngine().deliberate` | `(context: DecisionContext) -> Deliberation` | Murni, bebas DB. Sepuluh agen, jaksa, kritik, gerbang |
| `DecisionContext` | `market, symbol, interval, as_of, state, technical, ...` | Kolam bukti beku |
| `MarketState` | `last_price, bid, ask, spread_bps, session, market_open, ...` | Bidangnya persis keluaran `Snapshot` XAU |
| `AgentOpinion` | `role, decision, confidence, reasoning, evidence, abstained` | Sudah memaksa tiap pandangan punya alasan |
| `Deliberation` | `opinions, proposal, prosecutor, critique, risk, outcome, confidence, independence` | Sudah menghitung independensi antar-agen |

**Yang TIDAK dipakai:** `DeliberationService`. Ia menuntut lima repositori DB dan melayani jalur crypto/IDX. Modul XAU memanggil kedua *engine* langsung.

## Keputusan Arsitektur: WAIT di dalam, NO SIGNAL di luar

`AgentOpinion.validate()` **mewajibkan** `Decision.WAIT` saat sebuah agen abstain:

```python
if self.abstained and self.decision is not Decision.WAIT:
    raise ValueError(...)
```

Spec melarang `WAIT` di modul XAU. Keduanya bisa benar sekaligus karena bicara tentang hal berbeda: larangan itu tentang **kosakata keputusan XAU** yang sampai ke operator, sementara `WAIT` di sini adalah penanda abstain milik mesin dewan yang dipakai bersama crypto dan futures.

Jadi ada **satu batas penerjemahan**, di `aruna/xau/suara.py`, dan hanya di sana. Di sebelah dalam batas itu agen tetap bicara `WAIT`; di sebelah luar tidak ada satu pun `WAIT` yang lolos. Alternatifnya — mengubah `AgentOpinion` — akan menyentuh jalur futures, yang dilarang.

Penegakannya bukan janji: `test_kosakata_xau` memindai seluruh `src/aruna/xau/` dan menolak `Decision.WAIT`, `LONG`, dan `SHORT` di luar berkas penerjemah itu.

---

## Struktur Berkas

**Dibuat:**

| Berkas | Tanggung jawab |
|---|---|
| `src/aruna/xau/bukti.py` | Tumpukan timeframe → `TechnicalSnapshot` per timeframe |
| `src/aruna/xau/konteks.py` | Bukti + snapshot pasar → `DecisionContext` M5 |
| `src/aruna/xau/suara.py` | **Satu-satunya** batas terjemahan `Decision` → AGREE/DISAGREE/NEUTRAL |
| `src/aruna/xau/geometri.py` | Entry/stop/target dari ATR M5, dan RR-nya |
| `src/aruna/xau/keputusan.py` | Gerbang XAU → `BUY`/`SELL`/`NO_SIGNAL` bersebab |
| `src/aruna/xau/cooldown.py` | Satu setup, satu sinyal |
| `migrations/0045_xau_sinyal.sql` | `xau_predictions`, `xau_evidence`, `xau_agent_votes` |
| `src/aruna/db/repositories/xau.py` | Penyimpanan, termasuk yang ditolak |

**Diubah:** tidak ada berkas di luar `src/aruna/xau/`, `migrations/`, dan `tests/` — kecuali satu baris di `tests/test_permukaan_publik.py` untuk mendaftarkan nama publik baru.

---

## Task 1: Bukti per timeframe

**Files:**
- Create: `src/aruna/xau/bukti.py`
- Test: `tests/test_xau_bukti.py`

**Interfaces:**
- Consumes: `TumpukanTimeframe` (Rencana 1), `CandleSeries.from_candles`, `AnalysisEngine().analyse`.
- Produces:
  - `@dataclass(frozen=True, slots=True) class BuktiXau` — `m5: TechnicalSnapshot`, `m15: TechnicalSnapshot | None`, `h1: TechnicalSnapshot | None`, `h4: TechnicalSnapshot | None`; properti `as_of: datetime`; metode `tersedia() -> tuple[Horizon, ...]`
  - `def rakit_bukti(tumpukan: TumpukanTimeframe) -> BuktiXau | None`

- [ ] **Step 1: Tulis test yang gagal**

Buat `tests/test_xau_bukti.py`:

```python
"""Bukti per timeframe, dan jangkar waktunya.

`as_of` diambil dari M5 dan HANYA dari M5. Timeframe besar tersettle lebih
jarang, jadi mengambil yang termuda di antara keempatnya akan melaporkan bukti
lebih tua daripada yang sebenarnya ada - dan gerbang kesegaran yang berdiri di
atasnya akan menolak analisis yang sebetulnya mutakhir.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle, Provenance
from aruna.xau.bukti import rakit_bukti
from aruna.xau.timeframes import rakit_tumpukan

AWAL = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)


def _m5(jumlah: int) -> list[Candle]:
    prov = Provenance(source="twelvedata")
    keluar: list[Candle] = []
    for i in range(jumlah):
        buka = AWAL + timedelta(minutes=5 * i)
        # Deret naik lalu turun supaya indikator arah punya sesuatu untuk dibaca.
        harga = Decimal(1000 + (i if i < jumlah // 2 else jumlah - i))
        keluar.append(
            Candle(
                market=Market.FOREX,
                symbol="XAU/USD",
                interval=Horizon.M5,
                open_time=buka,
                close_time=buka + timedelta(minutes=5),
                open=harga,
                high=harga + 2,
                low=harga - 2,
                close=harga + 1,
                volume=Decimal(0),
                provenance=prov,
                is_closed=True,
            )
        )
    return keluar


class TestRakitBukti:
    def test_m5_selalu_ada_saat_bahannya_cukup(self) -> None:
        bukti = rakit_bukti(rakit_tumpukan(_m5(240)))
        assert bukti is not None
        assert bukti.m5.interval is Horizon.M5

    def test_as_of_diambil_dari_m5(self) -> None:
        """Bukan yang tertua di antara empat timeframe."""
        tumpukan = rakit_tumpukan(_m5(240))
        bukti = rakit_bukti(tumpukan)
        assert bukti.as_of == bukti.m5.as_of
        assert bukti.as_of > bukti.h4.as_of, (
            "H4 tersettle lebih jarang; kalau as_of ikut yang tertua, bukti "
            "M5 yang segar akan dilaporkan basi"
        )

    def test_timeframe_besar_none_saat_bahannya_kurang(self) -> None:
        """None berarti BELUM CUKUP BAHAN, bukan nol dan bukan kerusakan."""
        bukti = rakit_bukti(rakit_tumpukan(_m5(30)))
        assert bukti is not None
        assert bukti.m5 is not None
        assert bukti.h4 is None
        assert Horizon.H4 not in bukti.tersedia()

    def test_tanpa_m5_tidak_ada_bukti(self) -> None:
        """Keputusan final berasal dari M5; tanpa M5 tidak ada apa pun."""
        assert rakit_bukti(rakit_tumpukan([])) is None

    def test_tidak_ada_bukti_yang_mendahului_as_of(self) -> None:
        """Inti SPEC 24: tak satu pun timeframe boleh tahu lebih dulu."""
        bukti = rakit_bukti(rakit_tumpukan(_m5(240)))
        for snap in (bukti.m15, bukti.h1, bukti.h4):
            if snap is not None:
                assert snap.as_of <= bukti.as_of
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
.venv\Scripts\python.exe -m pytest tests/test_xau_bukti.py -q
```

Diharapkan: GAGAL — `ModuleNotFoundError: aruna.xau.bukti`.

- [ ] **Step 3: Tulis modulnya**

Buat `src/aruna/xau/bukti.py`:

```python
"""Bukti teknikal per timeframe, seluruhnya dari satu deret M5.

`AnalysisEngine` dipakai apa adanya - enam belas indikator, laporan struktur,
dan klasifikasi rezim yang sama persis dengan yang dipakai crypto. Tidak ada
indikator versi XAU: dua implementasi RSI yang berbeda akan menghasilkan dua
angka yang tidak bisa dibandingkan, dan yang salah tidak akan pernah ketahuan
karena tak ada yang membandingkannya.

**`as_of` diambil dari M5 dan hanya dari M5.** Timeframe besar tersettle lebih
jarang: pukul 09:05 UTC, M5 sudah punya bar yang tutup 09:05 sementara H4
masih berhenti di 08:00. Mengambil yang tertua akan melaporkan bukti empat jam
lebih tua daripada yang sebenarnya ada, dan gerbang kesegaran yang berdiri di
atas angka itu akan menolak analisis yang mutakhir. Yang benar adalah M5 -
spec menetapkan keputusan final berasal dari M5, jadi kesegaran keputusan itu
adalah kesegaran M5.

`None` untuk timeframe besar berarti **belum cukup bahan**, bukan nol dan bukan
kerusakan: di awal jam, H4 memang belum punya 48 bar M5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aruna.analysis.engine import AnalysisEngine, TechnicalSnapshot
from aruna.analysis.series import CandleSeries, InsufficientData
from aruna.core.enums import Horizon
from aruna.data.models import Candle
from aruna.xau.timeframes import TumpukanTimeframe

_ENGINE = AnalysisEngine()


@dataclass(frozen=True, slots=True)
class BuktiXau:
    """Bukti teknikal empat timeframe pada satu instan."""

    m5: TechnicalSnapshot
    m15: TechnicalSnapshot | None = None
    h1: TechnicalSnapshot | None = None
    h4: TechnicalSnapshot | None = None

    @property
    def as_of(self) -> datetime:
        """Close bar M5 tersettle terbaru.  Lihat docstring modul."""
        return self.m5.as_of

    def tersedia(self) -> tuple[Horizon, ...]:
        peta = {
            Horizon.M5: self.m5,
            Horizon.M15: self.m15,
            Horizon.H1: self.h1,
            Horizon.H4: self.h4,
        }
        return tuple(tf for tf, snap in peta.items() if snap is not None)


def _analisa(candles: list[Candle]) -> TechnicalSnapshot | None:
    """`None` saat bahannya kurang - bukan snapshot setengah jadi."""
    if not candles:
        return None
    try:
        return _ENGINE.analyse(CandleSeries.from_candles(candles))
    except InsufficientData:
        return None


def rakit_bukti(tumpukan: TumpukanTimeframe) -> BuktiXau | None:
    """Hitung bukti tiap timeframe.  ``None`` kalau M5 sendiri tidak ada."""
    m5 = _analisa(tumpukan.m5)
    if m5 is None:
        return None
    return BuktiXau(
        m5=m5,
        m15=_analisa(tumpukan.m15),
        h1=_analisa(tumpukan.h1),
        h4=_analisa(tumpukan.h4),
    )


__all__ = ["BuktiXau", "rakit_bukti"]
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

```bash
.venv\Scripts\python.exe -m pytest tests/test_xau_bukti.py -q
```

- [ ] **Step 5: Buktikan dengan mencabut perbaikan**

Ganti `return self.m5.as_of` menjadi `min` atas keempat snapshot, jalankan lagi, pastikan `test_as_of_diambil_dari_m5` MERAH. Kembalikan setelah terbukti.

- [ ] **Step 6: Commit**

```bash
git add src/aruna/xau/bukti.py tests/test_xau_bukti.py
git commit -m "feat(xau): bukti per timeframe dengan as_of berjangkar di M5"
```

---

## Task 2: `DecisionContext` untuk XAU

**Files:**
- Create: `src/aruna/xau/konteks.py`
- Test: `tests/test_xau_konteks.py`

**Interfaces:**
- Consumes: `BuktiXau` (Task 1), `Snapshot` dari `aruna.data.models`, `MarketState`/`DecisionContext` dari `aruna.agents.context`.
- Produces: `def rakit_konteks(bukti: BuktiXau, snapshot: Snapshot, *, trading_allowed: bool = True) -> DecisionContext`

- [ ] **Step 1: Tulis test yang gagal**

```python
"""Konteks keputusan XAU: M5, dan yang tidak terukur tetap None."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aruna.core.enums import DataQuality, Horizon, Market
from aruna.data.models import Provenance, Snapshot
from aruna.xau.konteks import rakit_konteks


def _snapshot(**kw) -> Snapshot:
    bawaan = dict(
        market=Market.FOREX,
        symbol="XAU/USD",
        captured_at=datetime(2026, 8, 31, 4, 0, tzinfo=UTC),
        last_price=Decimal("4592.34"),
        provenance=Provenance(source="twelvedata"),
        quality=DataQuality.OK,
    )
    return Snapshot(**{**bawaan, **kw})


class TestRakitKonteks:
    def test_interval_keputusan_adalah_m5(self, bukti) -> None:
        """Spec: keputusan final berasal dari XAUUSD M5."""
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.interval is Horizon.M5

    def test_as_of_ikut_bukti_bukan_jam_tarik(self, bukti) -> None:
        """captured_at adalah kapan KITA bertanya; as_of kapan pasar bicara."""
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.as_of == bukti.as_of

    def test_spread_tak_terukur_tetap_none(self, bukti) -> None:
        """Twelve Data tidak menerbitkan bid/ask - diukur 2026-08-27."""
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.state.spread_bps is None
        assert ctx.state.bid is None and ctx.state.ask is None

    def test_spread_diteruskan_kalau_venue_menerbitkannya(self, bukti) -> None:
        """Sumber lain kelak boleh punya; jalurnya harus sudah benar sekarang."""
        ctx = rakit_konteks(
            bukti,
            _snapshot(bid=Decimal("4592"), ask=Decimal("4593"),
                      spread_bps=Decimal("2.2")),
        )
        assert ctx.state.spread_bps == Decimal("2.2")

    def test_market_adalah_forex(self, bukti) -> None:
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.market is Market.FOREX

    def test_technical_yang_dipakai_adalah_m5(self, bukti) -> None:
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.technical is bukti.m5

    def test_volume_nol_tidak_menyamar_jadi_likuiditas(self, bukti) -> None:
        """Valas spot tak punya volume; 0 tidak boleh dibaca sebagai terukur."""
        ctx = rakit_konteks(bukti, _snapshot())
        assert ctx.state.volume_24h is None
```

Tambahkan `conftest` lokal di berkas yang sama:

```python
import pytest

from aruna.xau.bukti import rakit_bukti
from aruna.xau.timeframes import rakit_tumpukan
from tests.test_xau_bukti import _m5


@pytest.fixture
def bukti():
    return rakit_bukti(rakit_tumpukan(_m5(240)))
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
.venv\Scripts\python.exe -m pytest tests/test_xau_konteks.py -q
```

- [ ] **Step 3: Tulis modulnya**

```python
"""Kolam bukti beku untuk satu keputusan XAU.

**Intervalnya M5, dan itu bukan pilihan gaya.** Spec menetapkan keputusan
final berasal dari XAUUSD M5; M15, H1, dan H4 masuk sebagai konteks lewat
:class:`BuktiXau`, bukan sebagai kandidat keputusan. Satu keputusan, satu
timeframe yang bertanggung jawab atasnya.

**`as_of` datang dari bukti, bukan dari snapshot.** `Snapshot.captured_at`
adalah kapan ARUNA bertanya; `BuktiXau.as_of` adalah kapan pasar terakhir
bicara. Yang menentukan sah tidaknya sebuah keputusan adalah yang kedua -
memakai yang pertama akan membuat keputusan terlihat segar hanya karena
permintaannya baru dikirim.

**Yang tidak diterbitkan venue diteruskan sebagai ``None``.** Diukur
2026-08-27: Twelve Data tidak menerbitkan bid/ask untuk XAU/USD, jadi
``spread_bps`` selalu ``None`` dan gerbang spread tidak menyala. ``None``
diteruskan apa adanya - tidak ditaksir dari range candle, karena range adalah
pergerakan harga sementara spread adalah biaya transaksi.

Volume juga ``None``, bukan ``0``. Valas spot tidak menerbitkannya; sebuah nol
di sini akan terbaca sebagai "likuiditas terukur dan hasilnya kosong".
"""

from __future__ import annotations

from aruna.agents.context import DecisionContext, MarketState
from aruna.core.enums import Horizon, Market
from aruna.data.models import Snapshot
from aruna.xau.bukti import BuktiXau


def rakit_konteks(
    bukti: BuktiXau,
    snapshot: Snapshot,
    *,
    trading_allowed: bool = True,
) -> DecisionContext:
    """Satu keputusan XAU, dengan seluruh buktinya dan tanpa isian karangan."""
    state = MarketState(
        last_price=snapshot.last_price,
        bid=snapshot.bid,
        ask=snapshot.ask,
        spread_bps=snapshot.spread_bps,
        # Valas spot tidak menerbitkan volume: None berarti tidak diukur.
        volume_24h=None,
        session=snapshot.session,
        market_open=snapshot.market_open,
        is_realtime=snapshot.provenance.is_realtime,
        data_quality=snapshot.quality.value,
        quality_detail=snapshot.quality_detail,
        source=snapshot.provenance.source,
    )
    return DecisionContext(
        market=Market.FOREX,
        symbol=snapshot.symbol,
        interval=Horizon.M5,
        as_of=bukti.as_of,
        state=state,
        technical=bukti.m5,
        trading_allowed=trading_allowed,
    )


__all__ = ["rakit_konteks"]
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Buktikan dengan mencabut perbaikan**

Ganti `as_of=bukti.as_of` menjadi `as_of=snapshot.captured_at`; pastikan `test_as_of_ikut_bukti_bukan_jam_tarik` MERAH. Kembalikan.

- [ ] **Step 6: Commit**

```bash
git add src/aruna/xau/konteks.py tests/test_xau_konteks.py
git commit -m "feat(xau): konteks keputusan M5 tanpa isian karangan"
```

---

## Task 3: Suara AGREE / DISAGREE / NEUTRAL

**Files:**
- Create: `src/aruna/xau/suara.py`
- Test: `tests/test_xau_suara.py`

**Interfaces:**
- Consumes: `AgentOpinion`, `Decision`, `Deliberation`.
- Produces:
  - `class Suara(StrEnum)` — `AGREE`, `DISAGREE`, `NEUTRAL`
  - `def suara_terhadap(opinion: AgentOpinion, arah: Decision) -> Suara`
  - `@dataclass(frozen=True, slots=True) class RekapSuara` — `setuju: int`, `menentang: int`, `netral: int`, `rincian: tuple[tuple[AgentRole, Suara], ...]`; properti `kontradiksi: float | None`
  - `def rekap(deliberation: Deliberation, arah: Decision) -> RekapSuara`
  - `def ke_keputusan_xau(decision: Decision) -> Decision` — **satu-satunya** tempat `WAIT` menjadi `NO_SIGNAL`

- [ ] **Step 1: Tulis test yang gagal**

```python
"""Terjemahan suara, dan satu-satunya tempat WAIT boleh disebut."""

from __future__ import annotations

import pytest

from aruna.agents.base import AgentOpinion
from aruna.core.enums import AgentRole, Decision
from aruna.xau.suara import RekapSuara, Suara, ke_keputusan_xau, suara_terhadap


def _opini(decision: Decision, *, abstained: bool = False) -> AgentOpinion:
    return AgentOpinion(
        role=AgentRole.TECHNICAL,
        decision=decision,
        confidence=0.0 if decision is Decision.WAIT else 0.6,
        reasoning=() if abstained else ("alasan uji",),
        abstained=abstained,
    )


class TestSuaraTerhadap:
    def test_arah_sama_adalah_setuju(self) -> None:
        assert suara_terhadap(_opini(Decision.BUY), Decision.BUY) is Suara.AGREE

    def test_arah_berlawanan_adalah_menentang(self) -> None:
        assert suara_terhadap(_opini(Decision.SELL), Decision.BUY) is Suara.DISAGREE

    def test_abstain_adalah_netral(self) -> None:
        opini = _opini(Decision.WAIT, abstained=True)
        assert suara_terhadap(opini, Decision.BUY) is Suara.NEUTRAL

    def test_wait_tanpa_abstain_juga_netral(self) -> None:
        """Menahan diri bukan menentang - dan bukan mendukung."""
        assert suara_terhadap(_opini(Decision.WAIT), Decision.BUY) is Suara.NEUTRAL

    def test_arah_bukan_arah_ditolak(self) -> None:
        """Merekap terhadap NO_SIGNAL tidak punya arti; itu bug pemanggil."""
        with pytest.raises(ValueError, match="arah"):
            suara_terhadap(_opini(Decision.BUY), Decision.NO_SIGNAL)


class TestKontradiksi:
    def test_bulat_setuju_nol_kontradiksi(self) -> None:
        rekap = RekapSuara(setuju=8, menentang=0, netral=2, rincian=())
        assert rekap.kontradiksi == 0.0

    def test_terbelah_rata_kontradiksi_penuh(self) -> None:
        rekap = RekapSuara(setuju=4, menentang=4, netral=2, rincian=())
        assert rekap.kontradiksi == 1.0

    def test_netral_tidak_menghitung_sebagai_kontradiksi(self) -> None:
        """Sepuluh agen diam bukan sepuluh agen bertengkar."""
        rekap = RekapSuara(setuju=2, menentang=0, netral=8, rincian=())
        assert rekap.kontradiksi == 0.0

    def test_semua_netral_tidak_terukur(self) -> None:
        """Nol suara berarti TIDAK DIUKUR, bukan nol kontradiksi."""
        rekap = RekapSuara(setuju=0, menentang=0, netral=10, rincian=())
        assert rekap.kontradiksi is None


class TestKosakata:
    def test_wait_jadi_no_signal(self) -> None:
        assert ke_keputusan_xau(Decision.WAIT) is Decision.NO_SIGNAL

    def test_arah_diteruskan(self) -> None:
        assert ke_keputusan_xau(Decision.BUY) is Decision.BUY
        assert ke_keputusan_xau(Decision.SELL) is Decision.SELL

    def test_no_signal_tetap(self) -> None:
        assert ke_keputusan_xau(Decision.NO_SIGNAL) is Decision.NO_SIGNAL
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

- [ ] **Step 3: Tulis modulnya**

```python
"""Batas terjemahan antara kosakata dewan dan kosakata XAU.

**Ini satu-satunya berkas di `aruna/xau/` yang boleh menyebut `Decision.WAIT`.**
`test_kosakata_xau` menegakkannya dengan memindai seluruh paket.

Alasannya ada di dua aturan yang keduanya benar. `AgentOpinion.validate()`
mewajibkan sebuah agen yang abstain mengembalikan `WAIT` - itu penanda milik
mesin dewan, yang dipakai bersama crypto dan futures. Spec XAU melarang
`WAIT` sebagai kosakata keputusan yang sampai ke operator. Keduanya bicara
tentang hal berbeda, jadi yang dibutuhkan bukan mengubah salah satunya
melainkan satu tempat yang menerjemahkan - dan hanya satu.

**Netral bukan setengah menentang.** Seorang agen yang menahan diri karena
buktinya tipis tidak sedang membantah apa pun. Menghitungnya sebagai
kontradiksi akan membuat setiap kondisi sepi terlihat seperti perselisihan,
dan gerbang kontradiksi akan menolak justru saat pasar paling tenang.

**Kontradiksi `None` berarti tidak terukur.** Kalau seluruh agen netral, tidak
ada perselisihan untuk diukur - dan itu berbeda dari perselisihan yang diukur
lalu hasilnya nol. Pemanggil yang menyamakan keduanya akan meloloskan sinyal
yang tidak seorang pun mendukungnya.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aruna.agents.base import AgentOpinion
from aruna.agents.deliberation import Deliberation
from aruna.core.enums import AgentRole, Decision


class Suara(StrEnum):
    """Sikap satu agen terhadap arah yang diusulkan."""

    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    NEUTRAL = "NEUTRAL"


def suara_terhadap(opinion: AgentOpinion, arah: Decision) -> Suara:
    """Sikap ``opinion`` terhadap ``arah``.

    ``arah`` harus BUY atau SELL: merekap terhadap NO_SIGNAL tidak punya arti,
    dan diam-diam memulangkan NEUTRAL akan menyembunyikan bug pemanggil.
    """
    if not arah.is_directional:
        raise ValueError(
            f"arah harus BUY atau SELL untuk merekap suara, bukan {arah.value}"
        )
    if opinion.abstained or not opinion.decision.is_directional:
        return Suara.NEUTRAL
    return Suara.AGREE if opinion.decision is arah else Suara.DISAGREE


@dataclass(frozen=True, slots=True)
class RekapSuara:
    setuju: int
    menentang: int
    netral: int
    rincian: tuple[tuple[AgentRole, Suara], ...]

    @property
    def bersuara(self) -> int:
        return self.setuju + self.menentang

    @property
    def kontradiksi(self) -> float | None:
        """0 = bulat, 1 = terbelah rata.  ``None`` = tidak ada yang bersuara.

        Diukur hanya di antara yang BERSUARA. Netral tidak masuk penyebut -
        lihat docstring modul.
        """
        if self.bersuara == 0:
            return None
        minoritas = min(self.setuju, self.menentang)
        return 2 * minoritas / self.bersuara


def rekap(deliberation: Deliberation, arah: Decision) -> RekapSuara:
    rincian = tuple(
        (o.role, suara_terhadap(o, arah)) for o in deliberation.opinions
    )
    hitung = [s for _role, s in rincian]
    return RekapSuara(
        setuju=hitung.count(Suara.AGREE),
        menentang=hitung.count(Suara.DISAGREE),
        netral=hitung.count(Suara.NEUTRAL),
        rincian=rincian,
    )


def ke_keputusan_xau(decision: Decision) -> Decision:
    """Kosakata dewan → kosakata XAU.  ``WAIT`` tidak pernah lolos dari sini."""
    return Decision.NO_SIGNAL if decision is Decision.WAIT else decision


__all__ = ["RekapSuara", "Suara", "ke_keputusan_xau", "rekap", "suara_terhadap"]
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

- [ ] **Step 5: Tulis penjaga kosakata**

Buat `tests/test_kosakata_xau.py`:

```python
"""Kosakata XAU: BUY / SELL / NO SIGNAL. Tidak ada yang lain.

Spec melarang LONG, SHORT, dan WAIT. `suara.py` dikecualikan karena ia justru
BATAS yang menerjemahkan kosakata dewan - dan pengecualian itu satu berkas,
bukan sebuah kebiasaan.
"""

from __future__ import annotations

from pathlib import Path

XAU = Path(__file__).resolve().parent.parent / "src" / "aruna" / "xau"
PENERJEMAH = "suara.py"
TERLARANG = ("Decision.WAIT", "Decision.LONG", "Decision.SHORT")


class TestKosakata:
    def test_wait_hanya_di_penerjemah(self) -> None:
        pelanggar: dict[str, list[str]] = {}
        for path in XAU.rglob("*.py"):
            if path.name == PENERJEMAH:
                continue
            isi = path.read_text(encoding="utf-8")
            kena = [k for k in TERLARANG if k in isi]
            if kena:
                pelanggar[path.name] = kena
        assert not pelanggar, (
            f"kosakata futures bocor ke modul XAU: {pelanggar}. "
            f"Terjemahkan lewat {PENERJEMAH}."
        )

    def test_penerjemahnya_memang_ada_dan_menyebut_wait(self) -> None:
        """Kalau penerjemahnya hilang, test di atas jadi hijau tanpa arti."""
        isi = (XAU / PENERJEMAH).read_text(encoding="utf-8")
        assert "Decision.WAIT" in isi
        assert "Decision.NO_SIGNAL" in isi
```

- [ ] **Step 6: Commit**

```bash
git add src/aruna/xau/suara.py tests/test_xau_suara.py tests/test_kosakata_xau.py
git commit -m "feat(xau): suara AGREE/DISAGREE/NEUTRAL dengan satu batas terjemahan"
```

---

## Task 4: Geometri dan RR

**Files:**
- Create: `src/aruna/xau/geometri.py`
- Test: `tests/test_xau_geometri.py`

**Interfaces:**
- Consumes: `TechnicalSnapshot.reading("atr")`, `Decision`.
- Produces:
  - `@dataclass(frozen=True, slots=True) class Geometri` — `entry: Decimal`, `stop: Decimal`, `target: Decimal`, `rr: float`, `atr: Decimal`
  - `def rakit_geometri(bukti: BuktiXau, arah: Decision, harga: Decimal) -> Geometri | None`
  - `MIN_RR: float = 1.5`, `STOP_ATR: Decimal`, `TARGET_ATR: Decimal`

**Pelajaran futures yang dipinjam, bukan kodenya.** Modul futures belajar dua hal dengan mahal, dan keduanya berlaku di sini — tapi `src/aruna/futures/` tidak boleh disentuh, jadi yang dipinjam adalah pelajarannya:

1. Target minimum **2 ATR**, karena satu ATR adalah pergerakan khas dan menargetkannya berarti menargetkan hasil imbang yang terukur paling buruk.
2. Stop **dibatasi jangkauan horizon**: level struktural di luar jangkauan yang masuk akal bukan stop, ia hanya jarak.

- [ ] **Step 1: Tulis test yang gagal**

```python
"""Geometri XAU: jarak dalam ATR, dan RR yang jujur."""

from __future__ import annotations

from decimal import Decimal

from aruna.core.enums import Decision
from aruna.xau.geometri import MIN_RR, rakit_geometri


class TestGeometri:
    def test_buy_stop_di_bawah_target_di_atas(self, bukti) -> None:
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("1000"))
        assert geo.stop < geo.entry < geo.target

    def test_sell_stop_di_atas_target_di_bawah(self, bukti) -> None:
        geo = rakit_geometri(bukti, Decision.SELL, Decimal("1000"))
        assert geo.target < geo.entry < geo.stop

    def test_target_minimal_dua_atr(self, bukti) -> None:
        """Satu ATR adalah pergerakan khas; menargetkannya = menargetkan
        hasil imbang yang terukur paling buruk."""
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("1000"))
        assert abs(geo.target - geo.entry) >= 2 * geo.atr

    def test_rr_dihitung_dari_jarak_sebenarnya(self, bukti) -> None:
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("1000"))
        diharapkan = float(abs(geo.target - geo.entry) / abs(geo.entry - geo.stop))
        assert abs(geo.rr - diharapkan) < 1e-9

    def test_rr_memenuhi_ambang(self, bukti) -> None:
        geo = rakit_geometri(bukti, Decision.BUY, Decimal("1000"))
        assert geo.rr >= MIN_RR

    def test_tanpa_atr_tidak_ada_geometri(self, bukti_tanpa_atr) -> None:
        """ATR tidak terukur berarti jaraknya tidak diketahui - bukan nol."""
        assert rakit_geometri(bukti_tanpa_atr, Decision.BUY, Decimal("1000")) is None

    def test_arah_bukan_arah_ditolak(self, bukti) -> None:
        import pytest

        with pytest.raises(ValueError, match="arah"):
            rakit_geometri(bukti, Decision.NO_SIGNAL, Decimal("1000"))
```

- [ ] **Step 2–6:** merah → tulis → hijau → cabut-uji `TARGET_ATR` ke `1` dan pastikan `test_target_minimal_dua_atr` merah → commit.

---

## Task 5: Gerbang keputusan XAU

**Files:**
- Create: `src/aruna/xau/keputusan.py`
- Test: `tests/test_xau_keputusan.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True, slots=True) class SinyalXau` — `keputusan: Decision`, `alasan: str | None`, `rekap: RekapSuara | None`, `geometri: Geometri | None`, `confidence: float | None`, `setup_id: str`
  - `def putuskan(...) -> SinyalXau`
  - `MAX_KONTRADIKSI: float = 0.5`

Urutan gerbang, dan tiap penolakan membawa sebabnya:

1. Kelayakan data (Rencana 1) gagal → `NO_SIGNAL`
2. Dewan tidak berarah (`WAIT`) → `NO_SIGNAL`
3. Tidak ada yang bersuara (`kontradiksi is None`) → `NO_SIGNAL`
4. `kontradiksi > MAX_KONTRADIKSI` → `NO_SIGNAL`
5. Geometri tidak terhitung → `NO_SIGNAL`
6. `rr < MIN_RR` → `NO_SIGNAL`
7. Cooldown menahan → `NO_SIGNAL`
8. Selain itu → `BUY` atau `SELL`

**Gerbang spread sengaja TIDAK ada di daftar ini.** Diukur 2026-08-27: Twelve Data tidak menerbitkan bid/ask, jadi `spread_bps` selalu `None`. Sebuah gerbang yang membandingkan `None` dengan ambang akan selalu lolos sambil terlihat aktif — dan itu lebih buruk daripada tidak ada, karena laporan akan menyebutnya "lulus". `SinyalXau` menyertakan `spread_diukur: bool` supaya laporannya menyebut **tidak aktif**, bukan lulus.

---

## Task 6: Tabel `xau_*` dan penyimpanan

**Files:**
- Create: `migrations/0045_xau_sinyal.sql`, `src/aruna/db/repositories/xau.py`
- Test: `tests/test_xau_penyimpanan.py`

Tiga tabel, sesuai spec:

- `xau_predictions` — satu baris per keputusan, **termasuk `NO_SIGNAL` dan alasannya**. Kolom `alasan_kosong` mengikuti pola `router_pilihan`: terisi berarti tidak ada sinyal, dan sebabnya ada di situ.
- `xau_evidence` — bacaan indikator per timeframe pada `as_of`, supaya keputusan bisa diputar ulang.
- `xau_agent_votes` — satu baris per agen per keputusan, menyimpan `AGREE`/`DISAGREE`/`NEUTRAL`.

**Tidak pernah ditimpa, dan LOSS tidak pernah dihapus.** Kunci unik `(setup_id, as_of)` supaya siklus yang berjalan dua kali pada bar yang sama tidak menghasilkan dua baris — baris yang sudah ada ditolak, bukan diubah.

---

## Peta Rencana Berikutnya

| Rencana | Isi | Syarat |
|---|---|---|
| **3 — Hasil & Pembelajaran** | `xau_results`, `xau_training_samples`, belah TRAIN/VALIDATION/OUT-OF-SAMPLE deret waktu, walk-forward, `xau_model_versions` dengan fallback | Rencana 2 |
| **4 — Konteks & Berita** | `xau_market_regimes`, `xau_news_events`, sesi dari `is_market_open`, DXY/yield sebagai bukti | Rencana 2 |
| **5 — Penyampaian** | Telegram XAU terpisah dari futures, penjadwalan, pemantauan | Rencana 3 |

**Yang tidak boleh dinyatakan sebelum Rencana 3 selesai:** target 80–90% tercapai. Butuh bukti out-of-sample **dan** walk-forward. Kalau hasilnya 72%, yang ditampilkan 72%.

**Yang tidak akan pernah bisa dinyatakan dengan sumber sekarang:** gerbang spread lulus. Ia tidak diukur.
