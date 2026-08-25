"""Persen risiko yang operator tetapkan benar-benar sampai ke ukuran posisi.

**Cacat yang bentuknya berulang di jalur ini, dan ini kali ketiga.** Sampai
2026-08-25 keputusan operator - risiko 2% per ide - tidak punya jalan sampai ke
kode sama sekali: tidak ada setting untuknya, ``--risk`` di CLI berbawaan
``None``, dan ``supervise`` tidak pernah mengopernya. Yang berlaku
``DEFAULT_RISK_PCT`` = 0,5%, seperempat dari yang diputuskan.

Dua saudaranya di jalur yang sama: ``--equity 10000`` yang tidak pernah cocok
dengan akun $100, dan daftar simbol yang dulu bawaan argumen. Ketiganya gagal
dengan cara yang sama - tanpa satu pun error, hanya angka yang salah dengan
rapi.

Terukur akibatnya: anggaran risiko $0,50 alih-alih $2, jadi notional yang bisa
didukung bermedian $1,25 sementara minimum venue $5. Delapan puluh enam
penolakan "di bawah minimum venue" dalam lima hari, dan setidaknya separuhnya
hilang pada 2%.

Yang dijaga di sini bukan angkanya - itu boleh diubah operator - melainkan
JALURNYA: setting ada, supervisor mengopernya, dan CLI menerimanya.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from aruna.core.config import UpkeepSettings
from aruna.futures.risk import DEFAULT_RISK_PCT, MAX_RISK_PCT


def _upkeep(**overrides) -> UpkeepSettings:
    return UpkeepSettings(_env_file=None, **overrides)


class TestSettingnyaAda:
    def test_risiko_bisa_dikonfigurasi(self) -> None:
        assert _upkeep().futures_risk_pct == 2.0

    def test_tidak_boleh_melewati_plafon_mesin_risiko(self) -> None:
        """``MAX_RISK_PCT`` ada karena satu rentetan buruk di atasnya
        menghabiskan akun. Setting yang bisa melewatinya akan membuat plafon itu
        cuma hiasan."""
        assert _upkeep().futures_risk_pct <= float(MAX_RISK_PCT)

        with pytest.raises(ValidationError):
            _upkeep(futures_risk_pct=5.0)

    def test_nol_ditolak(self) -> None:
        """Risiko nol berarti ukuran posisi nol - ARUNA yang diam tanpa pernah
        mengatakan kenapa."""
        with pytest.raises(ValidationError):
            _upkeep(futures_risk_pct=0.0)


class TestSupervisorMengoperkannya:
    def test_risk_ikut_di_baris_perintah(self) -> None:
        """Gerbang yang tidak pernah dipanggil bukan gerbang, dan setting yang
        tidak pernah dioper bukan konfigurasi."""
        from aruna.supervisor import default_children

        anak_semua = default_children("ETHUSDT,SOLUSDT", hours=24.0)

        args = {c.name: c.args for c in anak_semua}["futures-loop"]

        assert "--risk" in args, (
            "supervisor tidak mengoper risiko - `DEFAULT_RISK_PCT` yang berlaku, "
            "dan keputusan operator tidak pernah sampai"
        )
        nilai = args[args.index("--risk") + 1]
        assert float(nilai) == _upkeep().futures_risk_pct

    def test_equity_dan_risk_dioper_bersama(self) -> None:
        """Ekuitas yang benar dengan risiko yang salah tetap menghasilkan ukuran
        posisi yang salah. Keduanya harus sampai, jadi keduanya dikunci di satu
        test - memisahkannya membiarkan satu diperbaiki dan satu terlupa."""
        from aruna.supervisor import default_children

        anak_semua = default_children("ETHUSDT,SOLUSDT", hours=24.0)

        args = {c.name: c.args for c in anak_semua}["futures-loop"]

        assert "--equity" in args and "--risk" in args


class TestNilainyaBenarBenarBerbeda:
    def test_yang_dioper_bukan_bawaan_mesin_risiko(self) -> None:
        """Kalau setting kebetulan sama dengan ``DEFAULT_RISK_PCT``, seluruh
        perbaikan ini tidak bisa dibedakan dari tidak melakukan apa-apa - dan
        test di atas akan hijau di atas jalur yang masih putus.

        Ini yang membuat perbedaannya terukur: 2% berbanding 0,5%, empat kali
        lipat anggaran risiko dan karena itu empat kali lipat notional yang bisa
        didukung.
        """
        disetel = Decimal(str(_upkeep().futures_risk_pct))

        assert disetel != DEFAULT_RISK_PCT
        assert disetel == DEFAULT_RISK_PCT * 4
