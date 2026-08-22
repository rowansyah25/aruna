"""Membaca apa yang benar-benar dikerjakan satu keputusan (PASAL 14.3, 14.25).

Jarak antara bagaimana keputusan *seharusnya* disusun dan bagaimana ia
*sebenarnya* disusun adalah satu-satunya angka yang bisa dipakai memutuskan
gerbang mana yang layak dipasang.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aruna.decision.hierarchy import HierarchyError, Jalur, Pengamat, Tahap
from aruna.decision.observe import amati


def lengkap() -> dict:
    """Keputusan yang seluruh lapisannya meninggalkan jejak."""
    return {
        "context": SimpleNamespace(as_of="2026-08-19T12:00:00Z", regime=object()),
        "verdict": SimpleNamespace(
            opinions=("a",), protest=SimpleNamespace(objections=("x",))
        ),
        "plan": SimpleNamespace(
            integrity=object(),
            evidence_as_of="2026-08-19T12:00:00Z",
            net_rr=2.44,
            stop_detail=SimpleNamespace(invalidation=63780),
            horizon_hours=0.25,
            verdict=object(),
        ),
        "note": SimpleNamespace(
            confidence=0.87, risk_readings={"volatility": 40.0}, lintas=None
        ),
    }


class TestPengamatTidakPernahMenolak:
    def test_langkah_terbalik_jadi_temuan_bukan_pengecualian(self) -> None:
        """Penjaga yang baru dipasang di sistem yang sudah berjalan akan
        menghentikan produksi karena asumsi penulisnya, bukan karena ada yang
        rusak."""
        p = Pengamat().advance(Tahap.COUNCIL).advance(Tahap.DATA_VALIDITY)

        assert p.jalur.done == (Tahap.COUNCIL,)
        assert not p.clean
        assert "mendahuluinya" in p.findings[0]

    def test_aturannya_sama_persis_dengan_penjaganya(self) -> None:
        """Yang dilonggarkan adalah kapan penjaganya menyela, bukan apa yang
        dianggap salah."""
        with pytest.raises(HierarchyError):
            Jalur().advance(Tahap.COUNCIL).advance(Tahap.DATA_VALIDITY)

    def test_jalur_bersih_tidak_menghasilkan_temuan(self) -> None:
        p = Pengamat()
        for t in Tahap:
            p = p.advance(t)

        assert p.clean
        assert len(p.jalur.done) == len(Tahap)

    def test_note_melewati_yang_tidak_dikerjakan(self) -> None:
        p = (
            Pengamat()
            .note(Tahap.DATA_VALIDITY, done=True)
            .note(Tahap.DATA_FRESHNESS, done=False)
            .note(Tahap.MARKET_REGIME, done=True)
        )

        assert p.jalur.done == (Tahap.DATA_VALIDITY, Tahap.MARKET_REGIME)
        assert p.clean


class TestMembacaKeputusan:
    def test_keputusan_lengkap_hampir_semua_langkah(self) -> None:
        a = amati(**lengkap())

        assert a.order_findings == ()
        assert a.pengamat.jalur.missing_mandatory == ()

    def test_peta_satu_timeframe_bukan_analisis_lintas_timeframe(self) -> None:
        """Ia analisis satu timeframe dengan bungkus yang lebih besar. Butuh
        minimal dua untuk ada yang bisa dibandingkan."""
        bahan = lengkap()
        bahan["note"] = SimpleNamespace(
            confidence=0.87, risk_readings={"volatility": 40.0},
            lintas=SimpleNamespace(readings=("15m",)),
        )
        a = amati(**bahan)

        assert Tahap.MTF in a.absent

    def test_peta_dua_timeframe_dihitung_ada(self) -> None:
        bahan = lengkap()
        bahan["note"] = SimpleNamespace(
            confidence=0.87, risk_readings={"volatility": 40.0},
            lintas=SimpleNamespace(readings=("15m", "4h")),
        )
        a = amati(**bahan)

        assert Tahap.MTF not in a.absent

    def test_risk_reward_dibaca_dari_economics_juga(self) -> None:
        """Rencana yang DITOLAK karena imbalannya tidak sepadan jelas sudah
        menghitung rasionya - angkanya ada di ``economics``, dan kalimat
        penolakannya mengutipnya."""
        bahan = lengkap()
        bahan["plan"] = SimpleNamespace(
            integrity=object(), evidence_as_of="x", net_rr=None,
            economics=SimpleNamespace(net_rr=0.42),
            stop_detail=SimpleNamespace(invalidation=63780),
            horizon_hours=0.25, verdict=object(),
            side=SimpleNamespace(value="LONG"),
        )
        a = amati(**bahan)

        assert Tahap.RR not in a.absent

    def test_risiko_nol_tetap_dilaporkan_tidak_terhitung(self) -> None:
        """Di situ rasionya memang tidak bisa dihitung, dan "tidak ada" adalah
        laporan yang benar."""
        bahan = lengkap()
        bahan["plan"] = SimpleNamespace(
            integrity=object(), evidence_as_of="x", net_rr=None,
            economics=SimpleNamespace(net_rr=None),
            stop_detail=SimpleNamespace(invalidation=63780),
            horizon_hours=0.25, verdict=object(),
            side=SimpleNamespace(value="LONG"),
        )

        assert Tahap.RR in amati(**bahan).absent

    def test_keputusan_tanpa_arah_tidak_dituntut_butir_berarah(self) -> None:
        """Menuntutnya berarti menuntut lapisan di atasnya mengarang."""
        from aruna.decision.audit import BERARAH

        bahan = lengkap()
        bahan["verdict"] = SimpleNamespace(
            opinions=("a",), protest=SimpleNamespace(objections=("x",)),
            decision=SimpleNamespace(value="WAIT"),
        )
        bahan["plan"] = SimpleNamespace(
            integrity=object(), evidence_as_of="x", net_rr=None,
            stop_detail=SimpleNamespace(invalidation=None),
            horizon_hours=0.25, verdict=object(),
        )
        a = amati(**bahan)

        assert set(a.checklist.inapplicable) == set(BERARAH)

    def test_sisi_flat_bukan_arah(self) -> None:
        """``FLAT`` adalah objek yang ada, truthy, dan artinya persis "tidak
        berarah". Menilainya lewat kebenarannya membuat tiga belas keputusan
        tanpa arah tercatat berarah."""
        from aruna.decision.audit import BERARAH

        bahan = lengkap()
        bahan["verdict"] = SimpleNamespace(
            opinions=("a",), protest=SimpleNamespace(objections=("x",)),
            decision=SimpleNamespace(value="WAIT"),
        )
        bahan["plan"] = SimpleNamespace(
            integrity=object(), evidence_as_of="x", net_rr=None,
            stop_detail=SimpleNamespace(invalidation=None),
            horizon_hours=0.25, verdict=object(),
            side=SimpleNamespace(value="FLAT"),
        )
        a = amati(**bahan)

        assert set(a.checklist.inapplicable) == set(BERARAH)

    def test_rencana_ditolak_tetap_dihitung_berarah(self) -> None:
        """Ia punya tesis, stop, dan syarat pembatalan; yang tidak lolos
        hanyalah ongkosnya."""
        bahan = lengkap()
        bahan["plan"] = SimpleNamespace(
            integrity=object(), evidence_as_of="x", net_rr=None,
            economics=SimpleNamespace(net_rr=0.42),
            stop_detail=SimpleNamespace(invalidation=63780),
            horizon_hours=0.25, verdict=object(),
            side=SimpleNamespace(value="LONG"),
        )

        assert amati(**bahan).checklist.inapplicable == ()

    def test_tanpa_peta_dilaporkan_tidak_ada(self) -> None:
        a = amati(**lengkap())

        assert Tahap.MTF in a.absent
        assert not a.checklist.may_publish

    def test_confidence_nol_adalah_pengukuran(self) -> None:
        """Council yang melaporkan keyakinan 0% sudah menilai keyakinan.
        Menyamakannya dengan yang tidak pernah dihitung membuat lapisan yang
        berjalan dilaporkan sebagai lapisan yang hilang."""
        bahan = lengkap()
        bahan["note"] = SimpleNamespace(
            confidence=0.0, risk_readings={"volatility": 40.0}, lintas=None
        )
        a = amati(**bahan)

        assert Tahap.QUALITY not in a.absent

    def test_strategi_dibaca_dari_konteks_bukan_catatan(self) -> None:
        """Phase 12 mengalir lewat ``AgentService._build_context`` (PASAL
        12.6). Mencarinya di catatan council menghasilkan kesimpulan yang
        salah tentang lapisan yang sebenarnya berjalan."""
        bahan = lengkap()
        bahan["context"] = SimpleNamespace(
            as_of="x", regime=object(), strategy=object()
        )
        a = amati(**bahan)

        assert Tahap.STRATEGY not in a.absent

    def test_risiko_yang_hilang_terlihat_sebagai_wajib_yang_hilang(self) -> None:
        bahan = lengkap()
        bahan["note"] = SimpleNamespace(confidence=0.8, strategy="", risk_readings={})
        a = amati(**bahan)

        assert Tahap.RISK in a.pengamat.jalur.missing_mandatory

    def test_tanpa_apa_pun_tidak_meledak(self) -> None:
        a = amati()

        assert a.steps == 0
        assert len(a.absent) == len(Tahap)

    def test_objek_rusak_dibaca_sebagai_tidak_ada(self) -> None:
        """Atribut yang hilang tidak boleh menjatuhkan rencana yang membawa
        entry dan stop."""

        class Meledak:
            @property
            def integrity(self):
                raise RuntimeError("bentuknya berubah")

        a = amati(plan=Meledak())

        assert Tahap.DATA_VALIDITY in a.absent

    def test_nilai_nol_bukan_bukti_langkah_dikerjakan(self) -> None:
        """``horizon_hours=0`` adalah horizon yang tidak masuk akal, dan
        membacanya sebagai "horizon sudah ditentukan" menyembunyikannya."""
        bahan = lengkap()
        bahan["plan"] = SimpleNamespace(
            integrity=object(), evidence_as_of="x", net_rr=2.0,
            stop_detail=SimpleNamespace(invalidation=None),
            horizon_hours=0, verdict=object(),
        )
        a = amati(**bahan)

        assert Tahap.HORIZON in a.absent
        assert Tahap.INVALIDATION in a.absent


class TestRingkasan:
    def test_ringkasannya_angka_bukan_kalimat(self) -> None:
        """Yang dicari dari kumpulannya nanti adalah distribusi, dan
        distribusi butuh angka yang bisa dijumlahkan."""
        s = amati(**lengkap()).summary()

        assert isinstance(s["steps"], int)
        assert isinstance(s["audit_pass"], int)
        assert s["may_publish"] is False
        assert "MTF" in s["audit_unknown"]

    def test_wajib_yang_hilang_disebut_namanya(self) -> None:
        s = amati().summary()

        assert set(s["mandatory_missing"]) == {
            "DATA_VALIDITY", "DATA_FRESHNESS", "RISK"
        }

    def test_ringkasan_bisa_diserialisasi(self) -> None:
        """Ia masuk ke log terstruktur; nilai yang tidak bisa di-JSON akan
        menjatuhkan baris lognya, bukan hanya bidangnya."""
        import json

        json.dumps(amati(**lengkap()).summary())
