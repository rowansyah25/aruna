# XAUUSD M5 — Rencana 1: Fondasi Data

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ARUNA bisa menarik candle XAUUSD M5 yang tepercaya, menurunkan M15/H1/H4 darinya tanpa panggilan API tambahan, dan menyebut alasan persisnya ketika data tidak layak dipakai.

**Architecture:** XAUUSD masuk sebagai market ketiga (`Market.FOREX`) supaya seluruh lapisan data yang sudah ada — `Candle`, `Quote`, `Provenance`, `QualityGate`, `resample_candles`, `HttpFetcher`, `registry` — dipakai apa adanya, bukan diduplikasi. Adapter Twelve Data hanya melayani M5; M15/H1/H4 dirakit lokal lewat `resample_candles()`, sehingga satu simbol memakai 288 dari 800 kredit harian. Modul futures tidak disentuh sama sekali.

**Tech Stack:** Python 3.13, `httpx` (lewat `HttpFetcher` yang ada), Pydantic Settings, MySQL 8, pytest.

## Global Constraints

Disalin dari spec operator, berlaku untuk **setiap** task di rencana ini:

- ARUNA tetap **ANALYST ONLY** — tidak pernah mengeksekusi order broker secara otomatis.
- Kosakata keputusan: **`BUY` / `SELL` / `NO SIGNAL`**. Dilarang memakai `LONG`, `SHORT`, `WAIT` di modul XAU.
- **JANGAN MERUSAK FUTURES.** Futures tetap `LONG`/`SHORT`/`NO SIGNAL`. Dilarang mengubah funding rate, open interest, liquidation, leverage, margin, mark price, agen futures, basis data futures, dan keluaran Telegram futures.
- Jangan menambahkan kembali Spot Crypto. Jangan menambahkan kembali Saham Indonesia. Jangan membuat modul Spot atau IDX. Jangan me-restore modul yang sudah tidak digunakan.
- Jika komponen sudah ada: **gunakan dan extend**. Jangan membuat duplicate system.
- Semua feature harus **timestamp-safe** dan tidak boleh memakai data masa depan.
- Data basi / hilang / invalid / timestamp tidak konsisten → **`NO SIGNAL`**.
- Yang tidak terukur ditulis `None` / NULL, **bukan** ditebak dan bukan `0`.
- Jangan melakukan rewrite seluruh ARUNA.
- Timeframe: **M5 primer**; M15 konfirmasi, H1 tren, H4 konteks besar. Keputusan final tetap berasal dari XAUUSD M5.

---

## Keputusan Arsitektur: kenapa `Market.FOREX` dibuka

Audit 2026-08-27 menemukan Forex **sengaja dicabut**, dijaga empat lapis dan dikunci lima berkas test:

| Lapis | Berkas | Bentuk penjaga |
|---|---|---|
| Enum | `src/aruna/core/enums.py:28-29` | `Market` hanya `CRYPTO`, `IDX` |
| Parser | `src/aruna/core/enums.py:33` | `FORBIDDEN_MARKETS` memuat `"FOREX"` |
| Config | `src/aruna/core/config.py:101-113` | validator memanggil `parse_market()` |
| SQL | `migrations/0001_core.sql:43` | `CHECK (code IN ('CRYPTO','IDX'))` |

Test pengunci: `test_enums.py:32`, `test_config.py:48`, `test_seed.py:139`, `test_db_integration.py:233`, `test_migrations.py:47`.

**Dua jalan, dan kenapa yang kedua ditolak.**

Jalan A — buka `Market.FOREX`. Seluruh lapisan data dipakai ulang: `Candle`, `Quote` (sudah punya `bid`/`ask`/`spread_bps` yang mengembalikan `None` saat venue diam), `Provenance`, `QualityGate` (sudah punya `blocks_signal`, `find_candle_gaps`, deteksi clock-skew), `resample_candles`, `HttpFetcher`, `registry`. Biaya: empat lapis penjaga diperbarui, lima berkas test disesuaikan.

Jalan B — bikin `XauCandle`, `XauQuote`, `XauProvider` sendiri agar penjaga tak tersentuh. Biaya: menduplikasi tujuh komponen matang. Ini persis **"duplicate system"** yang dilarang spec. Dan ada preseden pahitnya di proyek ini — catatan `palsu-berbentuk-salah`: objek tiruan yang bidangnya menyimpang dari objek asli membuat suite hijau di atas bug produksi.

**Dipilih Jalan A.** Penjaga itu ditulis untuk mencegah forex masuk **tanpa sengaja** — docstring-nya sendiri berkata "a stray config value cannot quietly reintroduce it". Operator sekarang memasukkannya **dengan sengaja**. Jadi penjaga tidak dirobohkan, ia **dipersempit**: `FOREX` diterima sebagai satu-satunya ejaan sah, sementara `FX`, `CURRENCY`, dan `FOREIGN_EXCHANGE` tetap ditolak. Salah ketik tetap mati di depan.

Futures tidak tersentuh oleh perubahan ini: tak satu pun berkas di `src/aruna/futures/` membaca `Market`.

---

## Struktur Berkas

**Dibuat:**

| Berkas | Tanggung jawab |
|---|---|
| `migrations/0044_forex_market.sql` | Longgarkan CHECK `markets`, daftarkan baris market `FOREX` + aset `XAU/USD` |
| `src/aruna/data/forex/__init__.py` | Penanda paket |
| `src/aruna/data/forex/twelvedata.py` | Adapter Twelve Data, **M5 saja**, pemilik jatah kredit |
| `src/aruna/data/forex/budget.py` | Penjaga jatah 800/hari + 8/menit, satu-satunya yang tahu angka itu |
| `src/aruna/xau/__init__.py` | Penanda paket modul XAU |
| `src/aruna/xau/timeframes.py` | Rakit M15/H1/H4 dari M5, nol panggilan API |
| `src/aruna/xau/kelayakan.py` | Ubah `QualityVerdict` jadi alasan `NO SIGNAL` yang bisa dibaca manusia |
| `tests/test_forex_market_dibuka.py` | Penjaga: `FOREX` sah, alias tetap ditolak |
| `tests/test_twelvedata_provider.py` | Adapter: urutan, interval, 429, volume |
| `tests/test_xau_timeframes.py` | Turunan timeframe tidak memanggil API |
| `tests/test_xau_kelayakan.py` | Gerbang: data buruk → `NO SIGNAL` bersebab |

**Diubah:**

| Berkas | Perubahan |
|---|---|
| `src/aruna/core/enums.py:20-33` | Tambah `FOREX`, keluarkan `"FOREX"` dari `FORBIDDEN_MARKETS` |
| `src/aruna/core/config.py:806-823` | Tambah `forex_provider`, `forex_provider_api_key` |
| `src/aruna/data/registry.py:36-39,81` | Daftarkan adapter forex |
| `src/aruna/data/quality.py:315-321` | Batas basi untuk `Market.FOREX` |
| `tests/test_enums.py:32`, `test_config.py:48`, `test_seed.py:139`, `test_db_integration.py:233`, `test_migrations.py:47` | Alias tetap ditolak; `FOREX` kini sah |

---

## Task 1: Buka `Market.FOREX` tanpa melemahkan penjaga

**Files:**
- Modify: `src/aruna/core/enums.py:20-43`
- Modify: `tests/test_enums.py:32-34`, `tests/test_config.py:48-51`, `tests/test_seed.py:139-142`, `tests/test_db_integration.py:233-237`, `tests/test_migrations.py:47-51`
- Create: `migrations/0044_forex_market.sql`
- Test: `tests/test_forex_market_dibuka.py`

**Interfaces:**
- Consumes: tidak ada (task pertama).
- Produces: `Market.FOREX` sebagai anggota enum sah; `parse_market("FOREX") -> Market.FOREX`; `parse_market("FX")` tetap `ValueError`.

- [ ] **Step 1: Tulis test yang gagal**

Buat `tests/test_forex_market_dibuka.py`:

```python
"""FOREX dibuka dengan sengaja; ejaan lain tetap mati di depan."""

from __future__ import annotations

import pytest

from aruna.core.enums import FORBIDDEN_MARKETS, Market, parse_market


class TestForexDibuka:
    def test_forex_adalah_anggota_market(self) -> None:
        assert Market.FOREX.value == "FOREX"

    @pytest.mark.parametrize("ejaan", ["FOREX", "forex", "  Forex  "])
    def test_ejaan_kanonik_diterima(self, ejaan: str) -> None:
        assert parse_market(ejaan) is Market.FOREX

    @pytest.mark.parametrize("alias", ["FX", "CURRENCY", "FOREIGN_EXCHANGE", "fx"])
    def test_alias_tetap_ditolak(self, alias: str) -> None:
        """Penjaga dipersempit, bukan dirobohkan: salah ketik tetap mati."""
        with pytest.raises(ValueError, match="FOREX"):
            parse_market(alias)

    def test_forex_bukan_lagi_kata_terlarang(self) -> None:
        assert "FOREX" not in FORBIDDEN_MARKETS

    def test_alias_masih_terdaftar_terlarang(self) -> None:
        assert {"FX", "CURRENCY", "FOREIGN_EXCHANGE"} <= FORBIDDEN_MARKETS
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
pytest tests/test_forex_market_dibuka.py -q
```

Diharapkan: GAGAL — `AttributeError: FOREX` pada `Market.FOREX`.

- [ ] **Step 3: Ubah enum**

Di `src/aruna/core/enums.py`, ganti blok `Market` dan `FORBIDDEN_MARKETS`:

```python
class Market(StrEnum):
    """Market yang dicakup ARUNA.

    ``FOREX`` dibuka 2026-08-27 untuk modul XAUUSD M5, dan hanya dalam ejaan
    kanonik itu.  Alias lamanya tetap ditolak oleh :data:`FORBIDDEN_MARKETS`,
    jadi penjaga aslinya dipersempit - bukan dicabut: nilai config yang salah
    ketik tetap gagal di startup, bukan diam-diam jadi market lain.
    """

    CRYPTO = "CRYPTO"
    IDX = "IDX"
    FOREX = "FOREX"


#: Ejaan yang ditolak, eksplisit supaya pesan errornya bisa menyebut sebabnya.
#: ``FOREX`` sengaja TIDAK di sini sejak 2026-08-27; tiga sisanya bertahan
#: karena ambigu - ``FX`` dan ``CURRENCY`` pernah dipakai untuk hal berbeda di
#: catatan lama, dan satu ejaan sah lebih mudah dicari di log daripada empat.
FORBIDDEN_MARKETS: frozenset[str] = frozenset({"FX", "CURRENCY", "FOREIGN_EXCHANGE"})


def parse_market(raw: str) -> Market:
    """Parse nama market, menolak alias yang ambigu."""
    value = raw.strip().upper()
    if value in FORBIDDEN_MARKETS:
        raise ValueError(
            f"market {value!r} tidak dipakai: tulis FOREX untuk pasar valas. "
            f"Market yang sah: {', '.join(m.value for m in Market)}"
        )
    try:
        return Market(value)
    except ValueError:
        raise ValueError(
            f"market {raw!r} tidak dikenal. Market yang sah: "
            f"{', '.join(m.value for m in Market)}"
        ) from None
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

```bash
pytest tests/test_forex_market_dibuka.py -q
```

Diharapkan: LULUS, 11 test.

- [ ] **Step 5: Perbaiki lima test pengunci yang kini keliru**

Kelimanya menegaskan hal yang sudah tidak benar. Ubah masing-masing supaya menguji **penjaga yang dipersempit**, bukan larangan total.

`tests/test_enums.py:32-34` — buang `"FOREX"` dari daftar:

```python
    @pytest.mark.parametrize("value", ["fx", "Currency", "FOREIGN_EXCHANGE"])
    def test_forex_aliases_are_rejected_with_an_explanation(self, value: str) -> None:
        with pytest.raises(ValueError, match="FOREX"):
            parse_market(value)
```

`tests/test_config.py:48-51`:

```python
    @pytest.mark.parametrize("value", ["FX", "CRYPTO,FX", "foreign_exchange"])
    def test_forex_aliases_are_refused(self, value: str) -> None:
        """Ejaan ambigu ditolak; ``FOREX`` kanonik diterima sejak 2026-08-27."""
        with pytest.raises(ValidationError, match="FOREX"):
            AppSettings(enabled_markets=value)

    def test_canonical_forex_is_accepted(self) -> None:
        settings = AppSettings(enabled_markets="CRYPTO,FOREX")
        assert Market.FOREX in settings.enabled_markets
```

`tests/test_seed.py:139-142` — ganti simbol uji ke alias:

```python
    def test_forex_alias_in_the_override_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "override.json"
        path.write_text(
            json.dumps([{"market": "FX", "symbol": "EURUSD", "asset_class": "FX"}]),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="FOREX"):
            load_override(path)
```

`tests/test_db_integration.py:233-237` — market sah sekarang; yang harus mustahil adalah kode **selain** ketiganya:

```python
    def test_unknown_market_is_impossible_for_a_direct_sql_writer(self, cursor) -> None:
        """CHECK di storage, bukan cuma di Python."""
        with pytest.raises(Exception, match="markets_code_allowed|CONSTRAINT"):
            cursor.execute(
                "INSERT INTO markets (code, name, timezone, is_active, "
                "quote_currency) VALUES ('SAHAM_US', 'US Equities', 'UTC', TRUE, 'USD')"
            )
```

`tests/test_migrations.py:47-51`:

```python
    def test_storage_admits_exactly_three_markets(self) -> None:
        """Penjaga storage dipersempit, bukan dibuka lebar."""
        sql = _strip_comments(_read_all_migrations()).upper()
        assert "MARKETS_CODE_ALLOWED" in sql
        for alias in ("'FX'", "'CURRENCY'", "'FOREIGN_EXCHANGE'"):
            assert alias not in sql
```

- [ ] **Step 6: Tulis migrasi**

Buat `migrations/0044_forex_market.sql`:

```sql
-- =====================================================================
-- ARUNA AI - Buka market FOREX untuk modul XAUUSD M5
--
-- Audit 2026-08-27 menemukan forex dijaga empat lapis, dan lapisan
-- storage inilah yang paling keras: CHECK menolak baris FOREX bahkan
-- dari penulis SQL langsung. Itu memang tujuannya - mencegah forex
-- masuk TANPA SENGAJA. Operator sekarang memasukkannya DENGAN SENGAJA,
-- jadi penjaganya dipersempit, bukan dicabut: tiga kode sah, sisanya
-- tetap mati di storage.
--
-- Alias `FX`, `CURRENCY`, `FOREIGN_EXCHANGE` sengaja TIDAK ditambahkan.
-- Satu ejaan sah berarti `WHERE market_code = 'FOREX'` tidak pernah
-- diam-diam melewatkan baris.
--
-- XAU/USD didaftarkan sebagai satu-satunya aset forex. `lot_size` NULL
-- karena ARUNA analyst-only dan tidak pernah menghitung ukuran lot
-- broker; NULL di sini berarti "tidak berlaku", bukan "belum diukur".
-- =====================================================================

ALTER TABLE markets DROP CHECK markets_code_allowed;

ALTER TABLE markets
    ADD CONSTRAINT markets_code_allowed
    CHECK (code IN ('CRYPTO', 'IDX', 'FOREX'));

INSERT INTO markets (code, name, timezone, is_active, quote_currency)
VALUES ('FOREX', 'Foreign Exchange', 'UTC', TRUE, 'USD')
ON DUPLICATE KEY UPDATE name = VALUES(name);

INSERT INTO assets (market_code, symbol, name, asset_class, is_active, lot_size)
SELECT 'FOREX', 'XAU/USD', 'Gold Spot vs US Dollar', 'COMMODITY', TRUE, NULL
WHERE NOT EXISTS (
    SELECT 1 FROM assets WHERE market_code = 'FOREX' AND symbol = 'XAU/USD'
);
```

- [ ] **Step 7: Terapkan migrasi dan jalankan seluruh test yang tersentuh**

```bash
aruna db migrate
```

```bash
pytest tests/test_forex_market_dibuka.py tests/test_enums.py tests/test_config.py tests/test_seed.py tests/test_migrations.py -q
```

Diharapkan: seluruhnya LULUS.

- [ ] **Step 8: Buktikan dengan mencabut perbaikan**

Kembalikan `FOREX` ke `FORBIDDEN_MARKETS` untuk sesaat, jalankan `pytest tests/test_forex_market_dibuka.py -q`, dan pastikan MERAH. Kalau tetap hijau, testnya tidak menguji apa pun — perbaiki testnya lebih dulu. Kembalikan kodenya setelah terbukti merah.

- [ ] **Step 9: Commit**

```bash
git add src/aruna/core/enums.py migrations/0044_forex_market.sql tests/
git commit -m "feat(forex): buka Market.FOREX untuk XAUUSD, alias ambigu tetap ditolak"
```

---

## Task 2: Konfigurasi provider forex

**Files:**
- Modify: `src/aruna/core/config.py:806-823` (`ProviderSettings`)
- Modify: `src/aruna/data/quality.py:315-321` (`_staleness_limit`)
- Test: `tests/test_config_forex.py`

**Interfaces:**
- Consumes: `Market.FOREX` dari Task 1.
- Produces: `ProviderSettings.forex_provider: str`, `ProviderSettings.forex_provider_api_key: SecretStr`; `QualityGate.staleness_limit(market: Market) -> float` (publik, membungkus `_staleness_limit` yang kini mengenal `Market.FOREX` = 660.0).

- [ ] **Step 1: Tulis test yang gagal**

Buat `tests/test_config_forex.py`:

```python
"""Konfigurasi provider forex, dan batas basi yang masuk akal untuk M5."""

from __future__ import annotations

from aruna.core.config import ProviderSettings
from aruna.core.enums import Market


class TestProviderForex:
    def test_forex_provider_punya_default(self) -> None:
        assert ProviderSettings().forex_provider == "twelvedata"

    def test_api_key_disembunyikan_dari_repr(self) -> None:
        """Kunci API tidak boleh bocor ke log lewat repr."""
        settings = ProviderSettings(forex_provider_api_key="rahasia123")
        assert "rahasia123" not in repr(settings)
        assert settings.forex_provider_api_key.get_secret_value() == "rahasia123"

    def test_forex_kosong_menonaktifkan(self) -> None:
        assert ProviderSettings(forex_provider="").configured()["forex"] is False

    def test_configured_menyebut_forex(self) -> None:
        assert ProviderSettings().configured()["forex"] is True
```

Tambahkan ke `tests/test_data_quality.py`:

```python
    def test_batas_basi_forex_lebih_ketat_dari_idx(self) -> None:
        """M5 valas bergerak jauh lebih cepat dari bar harian saham."""
        gate = QualityGate()
        assert gate._staleness_limit(Market.FOREX) < gate._staleness_limit(Market.IDX)
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
pytest tests/test_config_forex.py -q
```

Diharapkan: GAGAL — `ProviderSettings` tidak punya `forex_provider`.

- [ ] **Step 3: Tambahkan bidang konfigurasi**

Di `src/aruna/core/config.py`, dalam `ProviderSettings`, setelah baris `idx_provider_api_key`:

```python
    #: Valas hanya melayani XAU/USD (PHASE XAU). Satu adapter saja, dengan
    #: alasan yang sama seperti crypto: adapter kedua yang terdaftar *adalah*
    #: jalur substitusi diam-diam yang aturan ini ada untuk menutupnya.
    forex_provider: str = "twelvedata"
    forex_provider_api_key: SecretStr = SecretStr("")
```

Dan di `configured()`, tambahkan satu entri:

```python
            "forex": bool(self.forex_provider),
```

- [ ] **Step 4: Tambahkan batas basi forex**

Di `src/aruna/data/quality.py`, dalam `_staleness_limit`, tambahkan cabang `Market.FOREX`. Bar M5 tertutup tiap 300 detik; ambil dua bar plus kelonggaran jaringan:

```python
        if market is Market.FOREX:
            # Dua bar M5 penuh (600 detik) plus kelonggaran jaringan. Lebih
            # longgar dari ini berarti keputusan boleh berdiri di atas harga
            # dari tiga bar lalu, yang di M5 sudah beda dunia.
            return 660.0
```

Lalu buka aksesnya untuk pemakai di luar modul — `aruna.xau.kelayakan` perlu angka
ini untuk menyusun kalimat sebab, dan memanggil metode bergaris bawah milik modul
lain adalah utang yang menagih diam-diam saat privatnya berubah. Tambahkan tepat
di atas `_staleness_limit`:

```python
    def staleness_limit(self, market: Market) -> float:
        """Batas basi untuk ``market``, dalam detik.

        Publik karena bukan cuma gerbang ini yang membutuhkannya: pelapor yang
        menulis "basi 900 detik, batas 660" harus menyebut angka yang SAMA
        dengan yang dipakai menolak, bukan salinannya yang bisa menyimpang.
        """
        return self._staleness_limit(market)
```

Ubah juga assertion di test agar memakai yang publik:

```python
    def test_batas_basi_forex_lebih_ketat_dari_idx(self) -> None:
        """M5 valas bergerak jauh lebih cepat dari bar harian saham."""
        gate = QualityGate()
        assert gate.staleness_limit(Market.FOREX) < gate.staleness_limit(Market.IDX)
```

- [ ] **Step 5: Jalankan, pastikan HIJAU**

```bash
pytest tests/test_config_forex.py tests/test_data_quality.py -q
```

Diharapkan: LULUS.

- [ ] **Step 6: Commit**

```bash
git add src/aruna/core/config.py src/aruna/data/quality.py tests/
git commit -m "feat(forex): konfigurasi provider dan batas basi 660s untuk M5"
```

---

## Task 3: Adapter Twelve Data — M5 saja, pemilik jatah kredit

**Files:**
- Create: `src/aruna/data/forex/__init__.py`, `src/aruna/data/forex/budget.py`, `src/aruna/data/forex/twelvedata.py`
- Modify: `src/aruna/data/registry.py:16-18,36-39,81`
- Test: `tests/test_twelvedata_provider.py`

**Interfaces:**
- Consumes: `Market.FOREX` (Task 1), `ProviderSettings.forex_provider_api_key` (Task 2), `MarketDataProvider`, `ProviderCapabilities`, `ProviderStatus`, `Transport` dari `aruna.data.provider`; `HttpFetcher` dari `aruna.data.http`; `Candle`, `Quote`, `Provenance` dari `aruna.data.models`.
- Produces:
  - `class KreditHarian` — `__init__(self, *, per_hari: int = 800, per_menit: int = 8)`; `def minta(self, saat: datetime) -> bool`; `def sisa(self, saat: datetime) -> int`
  - `class TwelveDataForexProvider(MarketDataProvider)` — `__init__(self, settings: DataSettings, *, api_key: str = "", base_url: str = BASE_URL)`
  - `BASE_URL = "https://api.twelvedata.com"`

- [ ] **Step 1: Tulis test yang gagal**

Buat `tests/test_twelvedata_provider.py`:

```python
"""Adapter Twelve Data: urutan bar, interval yang ditolak, dan jatah kredit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.config import DataSettings
from aruna.core.enums import Horizon, Market
from aruna.data.forex.budget import KreditHarian
from aruna.data.forex.twelvedata import TwelveDataForexProvider

SAAT = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)


class TestKreditHarian:
    def test_jatah_menit_membatasi(self) -> None:
        budget = KreditHarian(per_hari=800, per_menit=8)
        for _ in range(8):
            assert budget.minta(SAAT) is True
        assert budget.minta(SAAT) is False, "kredit ke-9 dalam menit sama harus ditolak"

    def test_menit_berikutnya_memulihkan(self) -> None:
        budget = KreditHarian(per_hari=800, per_menit=8)
        for _ in range(8):
            budget.minta(SAAT)
        assert budget.minta(SAAT + timedelta(minutes=1)) is True

    def test_jatah_harian_membatasi(self) -> None:
        budget = KreditHarian(per_hari=10, per_menit=8)
        saat = SAAT
        diterima = 0
        for i in range(20):
            saat = SAAT + timedelta(minutes=i)
            if budget.minta(saat):
                diterima += 1
        assert diterima == 10

    def test_hari_berikutnya_memulihkan(self) -> None:
        budget = KreditHarian(per_hari=2, per_menit=8)
        budget.minta(SAAT)
        budget.minta(SAAT)
        assert budget.minta(SAAT) is False
        assert budget.minta(SAAT + timedelta(days=1)) is True

    def test_sisa_dilaporkan(self) -> None:
        budget = KreditHarian(per_hari=800, per_menit=8)
        budget.minta(SAAT)
        assert budget.sisa(SAAT) == 799


class TestKemampuan:
    def test_hanya_m5_yang_didukung(self) -> None:
        """M15/H1/H4 dirakit lokal; adapter tidak boleh diam-diam meresample."""
        provider = TwelveDataForexProvider(DataSettings(), api_key="k")
        assert provider.capabilities.supported_intervals == (Horizon.M5,)

    def test_market_adalah_forex(self) -> None:
        provider = TwelveDataForexProvider(DataSettings(), api_key="k")
        assert provider.market is Market.FOREX

    def test_simbol_kanonik_jadi_bentuk_venue(self) -> None:
        provider = TwelveDataForexProvider(DataSettings(), api_key="k")
        assert provider.provider_symbol("XAU/USD") == "XAU/USD"

    def test_keterbatasan_volume_dinyatakan(self) -> None:
        """Volume valas spot tidak ada; itu harus tertulis, bukan tersirat."""
        provider = TwelveDataForexProvider(DataSettings(), api_key="k")
        gabung = " ".join(provider.capabilities.limitations).lower()
        assert "volume" in gabung


class TestFetchCandles:
    @pytest.mark.asyncio
    async def test_interval_selain_m5_ditolak(self) -> None:
        provider = TwelveDataForexProvider(DataSettings(), api_key="k")
        with pytest.raises(ValueError, match="5m"):
            await provider.fetch_candles("XAU/USD", Horizon.H1)

    @pytest.mark.asyncio
    async def test_bar_dikembalikan_terlama_dulu(self, monkeypatch) -> None:
        """Twelve Data mengirim terbaru dulu; kontrak ABC minta sebaliknya."""
        payload = {
            "status": "ok",
            "values": [
                {"datetime": "2026-08-27 10:05:00", "open": "2", "high": "2",
                 "low": "2", "close": "2"},
                {"datetime": "2026-08-27 10:00:00", "open": "1", "high": "1",
                 "low": "1", "close": "1"},
            ],
        }
        provider = TwelveDataForexProvider(DataSettings(), api_key="k")
        monkeypatch.setattr(provider, "_get_json", _payload_tetap(payload))

        candles = await provider.fetch_candles("XAU/USD", Horizon.M5)

        assert [c.open for c in candles] == [Decimal("1"), Decimal("2")]
        assert candles[0].open_time < candles[1].open_time

    @pytest.mark.asyncio
    async def test_volume_nol_dan_turunannya_none(self, monkeypatch) -> None:
        """Valas spot tak punya volume: 0 yang dinyatakan, bukan angka karangan."""
        payload = {
            "status": "ok",
            "values": [
                {"datetime": "2026-08-27 10:00:00", "open": "1", "high": "1",
                 "low": "1", "close": "1"},
            ],
        }
        provider = TwelveDataForexProvider(DataSettings(), api_key="k")
        monkeypatch.setattr(provider, "_get_json", _payload_tetap(payload))

        candle = (await provider.fetch_candles("XAU/USD", Horizon.M5))[0]

        assert candle.volume == Decimal(0)
        assert candle.quote_volume is None
        assert candle.trade_count is None

    @pytest.mark.asyncio
    async def test_galat_venue_jadi_data_source_unavailable(self, monkeypatch) -> None:
        from aruna.core.errors import DataSourceUnavailableError

        payload = {"status": "error", "code": 429, "message": "API credits exceeded"}
        provider = TwelveDataForexProvider(DataSettings(), api_key="k")
        monkeypatch.setattr(provider, "_get_json", _payload_tetap(payload))

        with pytest.raises(DataSourceUnavailableError, match="429"):
            await provider.fetch_candles("XAU/USD", Horizon.M5)

    @pytest.mark.asyncio
    async def test_kredit_habis_menolak_sebelum_menembak(self, monkeypatch) -> None:
        """Jatah dijaga di sisi kita, bukan ditunggu sampai venue marah."""
        from aruna.core.errors import DataSourceUnavailableError

        provider = TwelveDataForexProvider(DataSettings(), api_key="k")
        provider._budget = KreditHarian(per_hari=0, per_menit=8)
        ditembak = False

        async def _jangan_sampai_kesini(*args, **kwargs):
            nonlocal ditembak
            ditembak = True
            return {}

        monkeypatch.setattr(provider, "_get_json", _jangan_sampai_kesini)

        with pytest.raises(DataSourceUnavailableError, match="kredit"):
            await provider.fetch_candles("XAU/USD", Horizon.M5)
        assert ditembak is False


class TestGetJson:
    """`_get_json` sendiri, bukan yang di-monkeypatch tes lain.

    `get_response` mengembalikan respons apa pun statusnya - 4xx tidak
    melempar. Kalau pemeriksaan status di `_get_json` hilang, 429 akan lolos
    jadi `.json()` yang gagal dengan pesan yang menyesatkan.
    """

    @pytest.mark.asyncio
    async def test_429_jadi_data_source_unavailable(self, monkeypatch) -> None:
        import httpx

        from aruna.core.errors import DataSourceUnavailableError

        provider = TwelveDataForexProvider(DataSettings(), api_key="k")

        async def _balas_429(*args, **kwargs):
            return httpx.Response(429, text="API credits exceeded"), 12.0

        monkeypatch.setattr(provider._fetcher, "get_response", _balas_429)

        with pytest.raises(DataSourceUnavailableError, match="429"):
            await provider._get_json("/time_series", {})

    @pytest.mark.asyncio
    async def test_429_tidak_diulang_oleh_fetcher(self, monkeypatch) -> None:
        """Mengulang 429 menghabiskan kredit yang venue keluhkan."""
        import httpx

        terlihat: dict[str, object] = {}

        async def _rekam(*args, **kwargs):
            terlihat.update(kwargs)
            return httpx.Response(200, text="{}"), 1.0

        provider = TwelveDataForexProvider(DataSettings(), api_key="k")
        monkeypatch.setattr(provider._fetcher, "get_response", _rekam)

        await provider._get_json("/time_series", {})

        assert 429 not in terlihat["retry_statuses"]

    @pytest.mark.asyncio
    async def test_non_json_jadi_pesan_yang_menyebut_sumbernya(self, monkeypatch) -> None:
        import httpx

        from aruna.core.errors import DataSourceUnavailableError

        provider = TwelveDataForexProvider(DataSettings(), api_key="k")

        async def _balas_html(*args, **kwargs):
            return httpx.Response(200, text="<html>maintenance</html>"), 1.0

        monkeypatch.setattr(provider._fetcher, "get_response", _balas_html)

        with pytest.raises(DataSourceUnavailableError, match="non-JSON"):
            await provider._get_json("/time_series", {})


def _payload_tetap(payload: dict):
    async def _get_json(*args, **kwargs):
        return payload
    return _get_json
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
pytest tests/test_twelvedata_provider.py -q
```

Diharapkan: GAGAL — `ModuleNotFoundError: aruna.data.forex`.

- [ ] **Step 3: Tulis penjaga kredit**

Buat `src/aruna/data/forex/__init__.py` (kosong) dan `src/aruna/data/forex/budget.py`:

```python
"""Jatah kredit Twelve Data, di satu tempat.

Paket gratis memberi 800 kredit per hari dan 8 per menit.  Angka itu hidup
hanya di sini: adapter yang menebar konstanta jatah ke beberapa berkas akan
kehilangan salah satunya saat pakainya berubah.

Ditolak di sisi kita, bukan ditunggu sampai venue menjawab 429.  Menunggu 429
berarti kredit itu sudah terpakai untuk diberitahu bahwa kredit habis - dan
`HttpFetcher` sengaja tidak mengulang 429 supaya yang pertama sampai utuh ke
sini.
"""

from __future__ import annotations

from datetime import date, datetime


class KreditHarian:
    """Penghitung dua lapis: per menit dan per hari.

    Waktu dioper masuk, tidak dibaca dari jam sistem, supaya perilakunya bisa
    diuji tanpa menunggu satu menit berlalu.
    """

    def __init__(self, *, per_hari: int = 800, per_menit: int = 8) -> None:
        self._per_hari = per_hari
        self._per_menit = per_menit
        self._hari: date | None = None
        self._terpakai_hari = 0
        self._menit: datetime | None = None
        self._terpakai_menit = 0

    def _gulung(self, saat: datetime) -> None:
        hari = saat.date()
        if hari != self._hari:
            self._hari = hari
            self._terpakai_hari = 0
        menit = saat.replace(second=0, microsecond=0)
        if menit != self._menit:
            self._menit = menit
            self._terpakai_menit = 0

    def minta(self, saat: datetime) -> bool:
        """Ambil satu kredit.  ``False`` berarti jatah habis, bukan galat."""
        self._gulung(saat)
        if self._terpakai_hari >= self._per_hari:
            return False
        if self._terpakai_menit >= self._per_menit:
            return False
        self._terpakai_hari += 1
        self._terpakai_menit += 1
        return True

    def sisa(self, saat: datetime) -> int:
        self._gulung(saat)
        return max(0, self._per_hari - self._terpakai_hari)


__all__ = ["KreditHarian"]
```

- [ ] **Step 4: Tulis adapter**

Buat `src/aruna/data/forex/twelvedata.py`:

```python
"""Twelve Data untuk XAU/USD, M5 saja.

**Kenapa cuma M5.**  `MarketDataProvider.fetch_candles` mewajibkan adapter
menolak interval yang tidak didukung, karena resampling adalah keputusan
pemanggil yang harus eksplisit.  M15, H1, dan H4 dirakit di
`aruna.xau.timeframes` dari bar M5 yang sama - nol kredit tambahan, dan
mustahil ada dua timeframe yang tidak sinkron karena semuanya satu sumber.

**Volume selalu nol.**  Valas spot tidak menerbitkan volume, dan Twelve Data
tidak mengarangnya.  `Candle.volume` wajib terisi, jadi nilainya `0` - dan
keterbatasan itu dinyatakan di `capabilities.limitations` supaya fitur apa pun
yang membacanya ketahuan salah sejak awal.  `quote_volume` dan `trade_count`
opsional, jadi keduanya `None`: tidak diukur, bukan nol.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from aruna.core.clock import now_utc
from aruna.core.config import DataSettings
from aruna.core.enums import DataQuality, Horizon, Market
from aruna.core.errors import DataSourceUnavailableError
from aruna.data.forex.budget import KreditHarian
from aruna.data.http import RETRY_STATUSES, HttpFetcher
from aruna.data.models import Candle, Provenance, Quote, Snapshot
from aruna.data.provider import (
    MarketDataProvider,
    ProviderCapabilities,
    ProviderStatus,
    Transport,
)

BASE_URL = "https://api.twelvedata.com"

#: Satu-satunya interval yang diminta dari venue.
_INTERVAL_VENUE = "5min"

#: Bar per permintaan.  Batas endpoint adalah 5000 dan satu permintaan tetap
#: berharga satu kredit, jadi meminta kurang dari maksimum membuang jatah.
MAX_BAR_PER_PERMINTAAN = 5000

#: 429 dikeluarkan dari daftar ulang: adapter ini yang memiliki kebijakan
#: kredit, jadi 429 pertama harus sampai utuh ke sini alih-alih dihabiskan
#: oleh tiga percobaan ulang.
_RETRY = RETRY_STATUSES - {429}


class TwelveDataForexProvider(MarketDataProvider):
    def __init__(
        self,
        settings: DataSettings,
        *,
        api_key: str = "",
        base_url: str = BASE_URL,
    ) -> None:
        self._settings = settings
        self._api_key = api_key
        self._fetcher = HttpFetcher(
            base_url=base_url,
            timeout_sec=settings.request_timeout_sec,
        )
        self._budget = KreditHarian()

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="twelvedata",
            market=Market.FOREX,
            transport=Transport.POLL,
            is_realtime=False,
            expected_delay_sec=60,
            supports_order_book=False,
            supported_intervals=(Horizon.M5,),
            max_candles_per_request=MAX_BAR_PER_PERMINTAAN,
            requires_credentials=True,
            regulatory_note="Twelve Data Basic plan; data lisensi redistribusi terbatas",
            limitations=(
                "valas spot tidak menerbitkan volume: Candle.volume selalu 0 "
                "dan tidak boleh dipakai sebagai fitur",
                "hanya M5 yang diminta dari venue; M15/H1/H4 dirakit lokal",
                "jatah paket gratis 800 kredit/hari dan 8/menit",
            ),
        )

    async def open(self) -> None:
        await self._fetcher.open()

    async def close(self) -> None:
        await self._fetcher.close()

    def provider_symbol(self, symbol: str) -> str:
        """``XAU/USD`` sudah bentuk yang dipakai Twelve Data."""
        return symbol.strip().upper()

    async def status(self) -> ProviderStatus:
        try:
            payload = await self._get_json(
                "/time_series",
                {"symbol": "XAU/USD", "interval": _INTERVAL_VENUE, "outputsize": "1"},
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderStatus(reachable=False, detail=str(exc))
        if payload.get("status") == "error":
            return ProviderStatus(reachable=False, detail=str(payload.get("message", "")))
        return ProviderStatus(reachable=True, server_time=now_utc())

    async def _get_json(self, path: str, params: dict[str, str]) -> dict:
        """Ambil JSON dengan 429 dibiarkan sampai utuh ke sini.

        Sengaja lewat ``get_response`` dan bukan ``get_json``: yang terakhir
        tidak membuka ``retry_statuses``, jadi 429-nya akan diulang tiga kali -
        menghabiskan tepat kredit yang venue keluhkan.  ``get_response``
        mengembalikan respons apa pun statusnya, jadi statusnya diperiksa di
        sini.
        """
        response, _latency = await self._fetcher.get_response(
            path,
            params={**params, "apikey": self._api_key},
            retry_statuses=_RETRY,
        )
        if response.status_code >= 400:
            raise DataSourceUnavailableError(
                f"twelvedata HTTP {response.status_code} untuk {path}: "
                f"{response.text[:120]!r}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise DataSourceUnavailableError(
                f"twelvedata membalas non-JSON untuk {path}: {response.text[:120]!r}"
            ) from exc

    async def fetch_candles(
        self,
        symbol: str,
        interval: Horizon,
        *,
        limit: int = 500,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        if interval is not Horizon.M5:
            raise ValueError(
                f"twelvedata hanya melayani {Horizon.M5.value}; "
                f"{interval.value} dirakit di aruna.xau.timeframes"
            )

        saat = now_utc()
        if not self._budget.minta(saat):
            raise DataSourceUnavailableError(
                "jatah kredit twelvedata habis "
                f"(sisa hari ini {self._budget.sisa(saat)}); tidak menembak venue"
            )

        params = {
            "symbol": self.provider_symbol(symbol),
            "interval": _INTERVAL_VENUE,
            "outputsize": str(min(limit, MAX_BAR_PER_PERMINTAAN)),
            "timezone": "UTC",
        }
        if start is not None:
            params["start_date"] = start.strftime("%Y-%m-%d %H:%M:%S")
        if end is not None:
            params["end_date"] = end.strftime("%Y-%m-%d %H:%M:%S")

        payload = await self._get_json("/time_series", params)
        if payload.get("status") == "error":
            raise DataSourceUnavailableError(
                f"twelvedata menolak: {payload.get('code')} {payload.get('message')}"
            )

        nilai = payload.get("values") or []
        provenance = Provenance(source="twelvedata", provider_timestamp=saat)
        # Venue mengirim terbaru dulu; kontrak ABC minta terlama dulu.
        return [
            self._ke_candle(row, symbol, provenance) for row in reversed(nilai)
        ]

    def _ke_candle(self, row: dict, symbol: str, provenance: Provenance) -> Candle:
        try:
            open_time = datetime.strptime(
                row["datetime"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=UTC)
            harga = {k: Decimal(row[k]) for k in ("open", "high", "low", "close")}
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise DataSourceUnavailableError(
                f"bar twelvedata tidak terbaca: {row!r}"
            ) from exc

        return Candle(
            market=Market.FOREX,
            symbol=symbol,
            interval=Horizon.M5,
            open_time=open_time,
            close_time=open_time + Horizon.M5.duration,
            open=harga["open"],
            high=harga["high"],
            low=harga["low"],
            close=harga["close"],
            # Valas spot tidak punya volume; keterbatasan ini dinyatakan di
            # capabilities.limitations dan diuji supaya tak dipakai jadi fitur.
            volume=Decimal(0),
            quote_volume=None,
            trade_count=None,
            provenance=provenance,
            is_closed=True,
        )

    async def fetch_quote(self, symbol: str) -> Quote:
        saat = now_utc()
        if not self._budget.minta(saat):
            raise DataSourceUnavailableError("jatah kredit twelvedata habis")

        payload = await self._get_json(
            "/quote", {"symbol": self.provider_symbol(symbol)}
        )
        if payload.get("status") == "error":
            raise DataSourceUnavailableError(
                f"twelvedata menolak: {payload.get('code')} {payload.get('message')}"
            )
        try:
            harga = Decimal(payload["close"])
        except (KeyError, InvalidOperation) as exc:
            raise DataSourceUnavailableError(
                f"quote twelvedata tidak terbaca: {payload!r}"
            ) from exc

        # bid/ask sengaja None kalau venue tidak menerbitkannya: Quote.spread_bps
        # sudah mengembalikan None untuk itu, dan itulah "tidak diukur".
        return Quote(
            market=Market.FOREX,
            symbol=symbol,
            price=harga,
            provenance=Provenance(source="twelvedata", provider_timestamp=saat),
            bid=_desimal_opsional(payload.get("bid")),
            ask=_desimal_opsional(payload.get("ask")),
        )

    async def fetch_snapshot(self, symbol: str) -> Snapshot:
        """Pandangan satu titik waktu.

        ``session`` dan ``market_open`` sengaja ``None``: keduanya baru diisi
        di Rencana 4 (sesi ASIA/LONDON/NEW YORK/OVERLAP).  ``None`` di sini
        berarti BELUM DIUKUR, dan sampai rencana itu selesai gerbang XAU
        memakai ``aruna.xau.kelayakan.periksa_kelayakan`` - bukan
        ``Snapshot.tradeable`` - supaya tak ada keputusan yang berdiri di atas
        bidang yang belum diisi siapa pun.
        """
        quote = await self.fetch_quote(symbol)
        return Snapshot(
            market=Market.FOREX,
            symbol=symbol,
            captured_at=quote.provenance.server_timestamp,
            last_price=quote.price,
            provenance=quote.provenance,
            quality=DataQuality.OK,
            bid=quote.bid,
            ask=quote.ask,
            # Quote.spread_bps sudah mengembalikan None saat bid/ask tak
            # terbit; diteruskan apa adanya, tidak pernah ditaksir dari range.
            spread_bps=quote.spread_bps,
            session=None,
            market_open=None,
        )


def _desimal_opsional(nilai: object) -> Decimal | None:
    if nilai in (None, ""):
        return None
    try:
        return Decimal(str(nilai))
    except InvalidOperation:
        return None


__all__ = ["BASE_URL", "MAX_BAR_PER_PERMINTAAN", "TwelveDataForexProvider"]
```

- [ ] **Step 5: Daftarkan di registry**

Di `src/aruna/data/registry.py`, tambahkan import dan entri:

```python
from aruna.data.forex.twelvedata import TwelveDataForexProvider
```

```python
PROVIDERS: dict[Market, dict[str, ProviderFactory]] = {
    Market.CRYPTO: {"binance-spot": BinanceSpotProvider},
    Market.IDX: {"yahoo": YahooIdxProvider},
    Market.FOREX: {"twelvedata": TwelveDataForexProvider},
}
```

Dan di `build_providers`, tambahkan ke peta nama:

```python
    names = {
        Market.CRYPTO: providers.crypto_provider,
        Market.IDX: providers.idx_provider,
        Market.FOREX: providers.forex_provider,
    }
```

- [ ] **Step 6: Jalankan, pastikan HIJAU**

```bash
pytest tests/test_twelvedata_provider.py -q
```

Diharapkan: LULUS, 18 test.

- [ ] **Step 7: Buktikan dengan mencabut perbaikan**

Balik `reversed(nilai)` menjadi `nilai` di `fetch_candles`, jalankan `pytest tests/test_twelvedata_provider.py -q`, pastikan `test_bar_dikembalikan_terlama_dulu` MERAH. Kembalikan setelah terbukti.

- [ ] **Step 8: Commit**

```bash
git add src/aruna/data/forex/ src/aruna/data/registry.py tests/test_twelvedata_provider.py
git commit -m "feat(forex): adapter twelvedata M5 dengan penjaga kredit 800/hari"
```

---

## Task 4: Rakit M15/H1/H4 dari M5, nol panggilan API

**Files:**
- Create: `src/aruna/xau/__init__.py`, `src/aruna/xau/timeframes.py`
- Test: `tests/test_xau_timeframes.py`

**Interfaces:**
- Consumes: `resample_candles`, `can_resample` dari `aruna.data.resample`; `Candle` dari `aruna.data.models`; `Horizon` dari `aruna.core.enums`.
- Produces:
  - `TIMEFRAME_TURUNAN: tuple[Horizon, ...] = (Horizon.M15, Horizon.H1, Horizon.H4)`
  - `@dataclass(frozen=True, slots=True) class TumpukanTimeframe` — bidang `m5: list[Candle]`, `m15: list[Candle]`, `h1: list[Candle]`, `h4: list[Candle]`; properti `lengkap: bool`; metode `kurang() -> tuple[Horizon, ...]`
  - `def rakit_tumpukan(m5: list[Candle]) -> TumpukanTimeframe`

- [ ] **Step 1: Tulis test yang gagal**

Buat `tests/test_xau_timeframes.py`:

```python
"""M15/H1/H4 dirakit dari M5 yang sama - satu sumber, nol kredit tambahan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle, Provenance
from aruna.xau.timeframes import TIMEFRAME_TURUNAN, rakit_tumpukan

AWAL = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)


def _m5(jumlah: int, *, mulai: datetime = AWAL) -> list[Candle]:
    prov = Provenance(source="twelvedata")
    keluar = []
    for i in range(jumlah):
        buka = mulai + timedelta(minutes=5 * i)
        harga = Decimal(1000 + i)
        keluar.append(
            Candle(
                market=Market.FOREX,
                symbol="XAU/USD",
                interval=Horizon.M5,
                open_time=buka,
                close_time=buka + timedelta(minutes=5),
                open=harga,
                high=harga + 1,
                low=harga - 1,
                close=harga,
                volume=Decimal(0),
                provenance=prov,
                is_closed=True,
            )
        )
    return keluar


class TestRakitTumpukan:
    def test_semua_turunan_terisi_dari_m5_saja(self) -> None:
        """48 bar M5 = 4 jam penuh = satu bar H4."""
        tumpukan = rakit_tumpukan(_m5(48))
        assert len(tumpukan.m15) == 16
        assert len(tumpukan.h1) == 4
        assert len(tumpukan.h4) == 1
        assert tumpukan.lengkap is True

    def test_bar_h4_merangkum_seluruh_jendela(self) -> None:
        tumpukan = rakit_tumpukan(_m5(48))
        h4 = tumpukan.h4[0]
        m5 = _m5(48)
        assert h4.open == m5[0].open
        assert h4.close == m5[-1].close
        assert h4.high == max(c.high for c in m5)
        assert h4.low == min(c.low for c in m5)

    def test_ember_tak_lengkap_dibuang_bukan_dirata_rata(self) -> None:
        """Satu bar H4 butuh 48 bar M5; 47 tidak boleh jadi H4 palsu."""
        tumpukan = rakit_tumpukan(_m5(47))
        assert tumpukan.h4 == []
        assert tumpukan.lengkap is False
        assert Horizon.H4 in tumpukan.kurang()

    def test_bar_terbuka_tidak_ikut(self) -> None:
        """Bar yang belum tutup masih berubah setelah dibaca - itu kebocoran."""
        bars = _m5(48)
        bars[-1] = replace_is_closed(bars[-1], False)
        tumpukan = rakit_tumpukan(bars)
        assert tumpukan.h4 == [], "H4 tidak boleh dirakit dari bar yang belum tutup"

    def test_m5_diteruskan_apa_adanya(self) -> None:
        bars = _m5(12)
        assert rakit_tumpukan(bars).m5 == bars

    def test_kosong_menghasilkan_kosong_bukan_galat(self) -> None:
        tumpukan = rakit_tumpukan([])
        assert tumpukan.m5 == []
        assert tumpukan.lengkap is False
        assert set(tumpukan.kurang()) == set(TIMEFRAME_TURUNAN)


def replace_is_closed(candle: Candle, is_closed: bool) -> Candle:
    from dataclasses import replace

    return replace(candle, is_closed=is_closed)
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
pytest tests/test_xau_timeframes.py -q
```

Diharapkan: GAGAL — `ModuleNotFoundError: aruna.xau`.

- [ ] **Step 3: Tulis perakit**

Buat `src/aruna/xau/__init__.py` (kosong) dan `src/aruna/xau/timeframes.py`:

```python
"""Empat timeframe dari satu sumber.

Spec meminta M5 primer dengan M15 konfirmasi, H1 tren, dan H4 konteks besar.
Meminta keempatnya ke venue berarti empat kali kredit dan - lebih buruk - empat
jawaban yang bisa saja tidak sinkron: bar H1 yang ditarik sedetik lebih lambat
dapat memuat pergerakan yang belum ada di M5 saat keputusan diambil.  Itu
kebocoran masa depan yang tidak terlihat seperti kebocoran.

Merakitnya dari bar M5 yang sama menutup keduanya: nol kredit tambahan, dan
mustahil ada timeframe yang tahu lebih banyak daripada M5 yang melahirkannya.

`resample_candles` sudah membuang ember yang tidak lengkap alih-alih
merata-rata, dan sudah menyaring bar yang belum tutup lewat `require_closed`.
Berkas ini tidak menghitung ulang apa pun - ia hanya menyatakan timeframe mana
yang diminta dan melaporkan mana yang belum cukup bahannya.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.core.enums import Horizon
from aruna.data.models import Candle
from aruna.data.resample import resample_candles

#: Diturunkan dari M5, sesuai spec: konfirmasi, tren, konteks besar.
TIMEFRAME_TURUNAN: tuple[Horizon, ...] = (Horizon.M15, Horizon.H1, Horizon.H4)


@dataclass(frozen=True, slots=True)
class TumpukanTimeframe:
    """Empat timeframe yang seluruhnya lahir dari satu deret M5."""

    m5: list[Candle] = field(default_factory=list)
    m15: list[Candle] = field(default_factory=list)
    h1: list[Candle] = field(default_factory=list)
    h4: list[Candle] = field(default_factory=list)

    def kurang(self) -> tuple[Horizon, ...]:
        """Timeframe turunan yang bahannya belum cukup.

        Kosong bukan berarti rusak: di awal jam, H4 memang belum punya 48 bar.
        Pemanggil yang membedakan keduanya membutuhkan daftar ini, bukan
        sekadar ``lengkap``.
        """
        peta = {Horizon.M15: self.m15, Horizon.H1: self.h1, Horizon.H4: self.h4}
        return tuple(tf for tf in TIMEFRAME_TURUNAN if not peta[tf])

    @property
    def lengkap(self) -> bool:
        return bool(self.m5) and not self.kurang()


def rakit_tumpukan(m5: list[Candle]) -> TumpukanTimeframe:
    """Turunkan M15/H1/H4 dari ``m5``.  Tidak menyentuh jaringan."""
    if not m5:
        return TumpukanTimeframe()
    return TumpukanTimeframe(
        m5=m5,
        m15=resample_candles(m5, Horizon.M15, require_closed=True),
        h1=resample_candles(m5, Horizon.H1, require_closed=True),
        h4=resample_candles(m5, Horizon.H4, require_closed=True),
    )


__all__ = ["TIMEFRAME_TURUNAN", "TumpukanTimeframe", "rakit_tumpukan"]
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

```bash
pytest tests/test_xau_timeframes.py -q
```

Diharapkan: LULUS, 6 test.

- [ ] **Step 5: Commit**

```bash
git add src/aruna/xau/ tests/test_xau_timeframes.py
git commit -m "feat(xau): rakit M15/H1/H4 dari M5, nol panggilan API tambahan"
```

---

## Task 5: Gerbang kelayakan data → alasan `NO SIGNAL`

**Files:**
- Create: `src/aruna/xau/kelayakan.py`
- Test: `tests/test_xau_kelayakan.py`

**Interfaces:**
- Consumes: `QualityGate.evaluate_candle`, `QualityGate.staleness_limit` (Task 2), `QualityVerdict.blocks_signal`, `find_candle_gaps` dari `aruna.data.quality`; `TumpukanTimeframe.kurang()` dari Task 4; `Decision.NO_SIGNAL` dari `aruna.core.enums`.
- Produces:
  - `@dataclass(frozen=True, slots=True) class Kelayakan` — bidang `layak: bool`, `alasan: str | None`; properti `keputusan: Decision`
  - `def periksa_kelayakan(tumpukan: TumpukanTimeframe, gate: QualityGate, *, sekarang: datetime) -> Kelayakan`

- [ ] **Step 1: Tulis test yang gagal**

Buat `tests/test_xau_kelayakan.py`:

```python
"""Data tak layak menghasilkan NO SIGNAL yang menyebutkan sebabnya."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aruna.core.enums import Decision, Horizon, Market
from aruna.data.models import Candle, Provenance
from aruna.data.quality import QualityGate
from aruna.xau.kelayakan import periksa_kelayakan
from aruna.xau.timeframes import rakit_tumpukan

AWAL = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)
SEKARANG = AWAL + timedelta(minutes=5 * 48)


def _m5(jumlah: int, *, lompati: int | None = None) -> list[Candle]:
    prov = Provenance(source="twelvedata")
    keluar = []
    for i in range(jumlah):
        if lompati is not None and i == lompati:
            continue
        buka = AWAL + timedelta(minutes=5 * i)
        harga = Decimal(1000 + i)
        keluar.append(
            Candle(
                market=Market.FOREX,
                symbol="XAU/USD",
                interval=Horizon.M5,
                open_time=buka,
                close_time=buka + timedelta(minutes=5),
                open=harga,
                high=harga + 1,
                low=harga - 1,
                close=harga,
                volume=Decimal(0),
                provenance=prov,
                is_closed=True,
            )
        )
    return keluar


class TestKelayakan:
    def test_data_sehat_layak(self) -> None:
        hasil = periksa_kelayakan(
            rakit_tumpukan(_m5(48)), QualityGate(), sekarang=SEKARANG
        )
        assert hasil.layak is True
        assert hasil.alasan is None

    def test_tanpa_data_tidak_layak(self) -> None:
        hasil = periksa_kelayakan(rakit_tumpukan([]), QualityGate(), sekarang=SEKARANG)
        assert hasil.layak is False
        assert "tidak ada" in hasil.alasan.lower()

    def test_bar_hilang_di_tengah_menolak(self) -> None:
        """Lubang di deret berarti fitur dihitung di atas waktu yang bolong."""
        hasil = periksa_kelayakan(
            rakit_tumpukan(_m5(48, lompati=20)), QualityGate(), sekarang=SEKARANG
        )
        assert hasil.layak is False
        assert "lubang" in hasil.alasan.lower()

    def test_data_basi_menolak(self) -> None:
        """Bar terakhir dari sejam lalu tidak boleh jadi dasar keputusan M5."""
        hasil = periksa_kelayakan(
            rakit_tumpukan(_m5(48)),
            QualityGate(),
            sekarang=SEKARANG + timedelta(hours=1),
        )
        assert hasil.layak is False
        assert "basi" in hasil.alasan.lower()

    def test_timeframe_kurang_menolak_dan_menyebutkannya(self) -> None:
        hasil = periksa_kelayakan(
            rakit_tumpukan(_m5(20)), QualityGate(), sekarang=AWAL + timedelta(minutes=100)
        )
        assert hasil.layak is False
        assert "h4" in hasil.alasan.lower()

    def test_tidak_layak_selalu_no_signal(self) -> None:
        """Kosakata XAU: NO_SIGNAL, tidak pernah WAIT."""
        hasil = periksa_kelayakan(rakit_tumpukan([]), QualityGate(), sekarang=SEKARANG)
        assert hasil.keputusan is Decision.NO_SIGNAL

    def test_layak_tidak_memutuskan_arah(self) -> None:
        """Kelayakan bukan sinyal: layak berarti boleh dinilai, bukan BUY."""
        hasil = periksa_kelayakan(
            rakit_tumpukan(_m5(48)), QualityGate(), sekarang=SEKARANG
        )
        assert hasil.keputusan is Decision.NO_SIGNAL
```

- [ ] **Step 2: Jalankan, pastikan MERAH**

```bash
pytest tests/test_xau_kelayakan.py -q
```

Diharapkan: GAGAL — `ModuleNotFoundError: aruna.xau.kelayakan`.

- [ ] **Step 3: Tulis gerbangnya**

Buat `src/aruna/xau/kelayakan.py`:

```python
"""Boleh tidaknya data XAU dipakai menilai - dan kalau tidak, kenapa.

Spec menetapkan empat keadaan yang wajib menghasilkan `NO SIGNAL`: data basi,
data hilang, data invalid, dan timestamp tidak konsisten.  Ketiganya yang
pertama sudah punya pengukurnya di `aruna.data.quality`; berkas ini tidak
menulis ulang logika itu, ia menerjemahkan hasilnya jadi satu kalimat yang
bisa dibaca operator dan disimpan di kolom alasan.

**Alasannya yang penting, bukan sekadar penolakannya.**  "tidak ada sinyal
karena memang tak ada setup" dan "tidak ada sinyal karena feed mati" terlihat
sama persis dari luar - yang pertama normal, yang kedua kerusakan.  Tanpa
kalimat sebab di sini, laporan "XAU diam hari ini" tidak bisa dibantah.

**`Kelayakan.layak = True` bukan sinyal.**  Ia berarti bahannya cukup untuk
DINILAI.  Arahnya diputuskan dewan di rencana berikutnya, jadi `keputusan` di
sini selalu `NO_SIGNAL` - kelayakan tidak pernah menaikkan apa pun jadi BUY.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aruna.core.enums import Decision
from aruna.data.quality import QualityGate, find_candle_gaps
from aruna.xau.timeframes import TumpukanTimeframe


@dataclass(frozen=True, slots=True)
class Kelayakan:
    layak: bool
    alasan: str | None = None

    @property
    def keputusan(self) -> Decision:
        """Selalu ``NO_SIGNAL``.

        Kelayakan hanya bisa MENOLAK.  Yang menaikkan sesuatu jadi BUY atau
        SELL adalah dewan, bukan pemeriksa data.
        """
        return Decision.NO_SIGNAL


def periksa_kelayakan(
    tumpukan: TumpukanTimeframe,
    gate: QualityGate,
    *,
    sekarang: datetime,
) -> Kelayakan:
    """Periksa berurutan, berhenti di penolakan pertama."""
    if not tumpukan.m5:
        return Kelayakan(False, "tidak ada bar M5 sama sekali")

    for candle in tumpukan.m5:
        verdict = gate.evaluate_candle(candle)
        if verdict.blocks_signal:
            return Kelayakan(
                False, f"bar M5 {candle.open_time:%Y-%m-%d %H:%M} invalid: {verdict}"
            )

    lubang = find_candle_gaps(tumpukan.m5)
    if lubang:
        mulai, selesai, jumlah = lubang[0]
        return Kelayakan(
            False,
            f"lubang {jumlah} bar M5 antara {mulai:%H:%M} dan {selesai:%H:%M}",
        )

    terakhir = tumpukan.m5[-1]
    umur = (sekarang - terakhir.close_time).total_seconds()
    batas = gate.staleness_limit(terakhir.market)
    if umur > batas:
        return Kelayakan(
            False,
            f"bar M5 terakhir basi: {umur:.0f} detik, batas {batas:.0f}",
        )

    kurang = tumpukan.kurang()
    if kurang:
        nama = ", ".join(tf.value for tf in kurang)
        return Kelayakan(False, f"timeframe belum cukup bahannya: {nama}")

    return Kelayakan(True)


__all__ = ["Kelayakan", "periksa_kelayakan"]
```

- [ ] **Step 4: Jalankan, pastikan HIJAU**

```bash
pytest tests/test_xau_kelayakan.py -q
```

Diharapkan: LULUS, 7 test.

- [ ] **Step 5: Buktikan dengan mencabut perbaikan**

Hapus blok `lubang = find_candle_gaps(...)`, jalankan `pytest tests/test_xau_kelayakan.py -q`, pastikan `test_bar_hilang_di_tengah_menolak` MERAH. Kembalikan setelah terbukti.

- [ ] **Step 6: Jalankan seluruh suite sekali**

```bash
pytest -q
```

Diharapkan: LULUS seluruhnya. Kalau ada yang merah di luar berkas XAU, itu berarti Task 1 menyentuh sesuatu yang tak terduga — perbaiki sebelum commit.

- [ ] **Step 7: Commit**

```bash
git add src/aruna/xau/kelayakan.py tests/test_xau_kelayakan.py
git commit -m "feat(xau): gerbang kelayakan data dengan alasan NO SIGNAL yang bisa dibaca"
```

---

## Peta Rencana Berikutnya

Rencana 1 berhenti di titik yang bisa berdiri sendiri: **ARUNA punya data XAUUSD M5 tepercaya di empat timeframe, atau tahu persis kenapa tidak.** Belum ada sinyal — itu sengaja.

| Rencana | Isi | Bergantung pada |
|---|---|---|
| **2 — Mesin Sinyal** | Fitur timestamp-safe; suara agen `AGREE`/`DISAGREE`/`NEUTRAL`; gerbang kontradiksi, spread, dan RR; cooldown per `setup_id`/`candle_id`; tabel `xau_predictions`, `xau_evidence`, `xau_agent_votes` | Rencana 1 |
| **3 — Hasil & Pembelajaran** | Resolusi hasil ke `xau_results`; `xau_training_samples` dengan belah TRAIN/VALIDATION/OUT-OF-SAMPLE deret waktu; walk-forward; `xau_model_versions` dengan model lama sebagai fallback | Rencana 2 |
| **4 — Konteks & Berita** | `xau_market_regimes`, `xau_news_events`; sesi ASIA/LONDON/NEW YORK/OVERLAP sebagai bukti bukan aturan; DXY/yield tanpa aturan absolut; actual hanya tersedia setelah release time | Rencana 2 |
| **5 — Penyampaian** | Keluaran Telegram XAU terpisah dari futures; penjadwalan; pemantauan | Rencana 3 |

Dua hal yang **tidak** boleh dikerjakan sampai Rencana 3 selesai dan angkanya keluar:

1. Menyatakan target 80–90% tercapai. Butuh bukti out-of-sample **dan** walk-forward. Kalau hasilnya 72%, yang ditampilkan 72%.
2. Menyalakan gerbang spread sebagai pengukuran. Twelve Data belum tentu menerbitkan bid/ask untuk XAU/USD; kalau `Quote.spread_bps` mengembalikan `None`, gerbang itu dicatat **"tidak diukur"** dan tidak boleh ditebak dari range candle.

---

## Catatan Verifikasi

Audit yang mendasari rencana ini dijalankan 2026-08-27 terhadap `main` di `782567f`:

- `Market` hanya `CRYPTO`/`IDX` — `src/aruna/core/enums.py:28-29`
- `CHECK (code IN ('CRYPTO','IDX'))` — `migrations/0001_core.sql:43`
- Tidak ada provider forex — `src/aruna/data/registry.py:36-39`
- `Decision.BUY/SELL/NO_SIGNAL` sudah ada — `src/aruna/core/enums.py:198-201`
- `Quote.bid/ask/spread_bps` sudah mengembalikan `None` saat tak terbit — `src/aruna/data/models.py:85-124`
- `QualityGate.blocks_signal`, `find_candle_gaps` sudah ada — `src/aruna/data/quality.py:38,435`
- `resample_candles` membuang ember tak lengkap, tidak merata-rata — `src/aruna/data/resample.py:118-120`
- `HttpFetcher` sengaja menyerahkan 429 pertama ke adapter — `src/aruna/data/http.py:143-147`

Tanda tangan yang diperiksa ulang setelah draf pertama rencana ini memuat tiga
tebakan yang salah. Dicatat supaya pelaksana tidak mengulanginya:

| Yang ditulis dari asumsi | Yang sebenarnya |
|---|---|
| `get_json(..., retry_statuses=...)` | `get_json(path, *, params)` saja — hanya `get_response` membuka `retry_statuses` (`http.py:101,124`) |
| `Snapshot(market, symbol, quote=)` | `Snapshot(market, symbol, captured_at, last_price, provenance, ...)` (`models.py:220+`) |
| `from aruna.core.time import now_utc` | `from aruna.core.clock import now_utc` (`clock.py:30`) |

`get_response` mengembalikan respons **apa pun statusnya, 4xx termasuk**
(`http.py:132-134`), jadi adapter wajib memeriksa `status_code` sendiri —
diuji oleh `TestGetJson` di Task 3.

Diukur langsung terhadap Yahoo 2026-08-27, alasan sumber itu ditolak: `XAUUSD=X` menjawab `404` di semua rentang; `GC=F` M5 memberi 7.776 bar (30 hari) lalu `422`; tidak ada bid/ask di interval mana pun.
