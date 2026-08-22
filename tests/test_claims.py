"""Frasa terlarang, dan literal yang tidak pernah bisa cocok (SPEC 51, 26).

Empat cacat dikunci di sini. Ketiganya sejenis dan itulah pokoknya: kode yang
ditulis, di-export, di-test, lalu tidak pernah tercapai oleh jalur hidup - atau
lebih buruk, tercapai tapi membandingkan sesuatu dengan nilai yang mustahil ada.
Tidak ada satu pun yang menimbulkan error. Semuanya melapor "aman".

* sebuah judul RSS bisa membungkam SELURUH log perdebatan;
* ``stance = 'OPPOSE'`` menyaring anggota enum yang tidak pernah ada;
* ``outcome == 'REJECTED'`` membandingkan dengan nilai yang bukan nilainya;
* cooldown notifier distempel sebelum kirim, jadi kegagalan sekali membungkam
  empat jam.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aruna.core.claims import FORBIDDEN_CLAIMS, MASK, find_forbidden, mask_forbidden
from aruna.core.enums import Stance, VetoReviewOutcome

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class TestKutipanLuarDisensorBukanDitolak:
    """Teks yang ditulis ARUNA ditolak. Teks yang dikutip ARUNA disensor.

    Bedanya penting: menolak kalimat ARUNA sendiri membongkar bug, sedangkan
    menolak kutipan berita menyerahkan tombol bisu ke penulis headline mana pun
    di internet.
    """

    @pytest.mark.parametrize("claim", FORBIDDEN_CLAIMS)
    def test_setiap_frasa_terlarang_bisa_disensor(self, claim: str) -> None:
        judul = f"Breaking: analyst says this is {claim} for holders"
        assert find_forbidden(judul), "fixture-nya sendiri tidak memuat frasanya"
        assert not find_forbidden(mask_forbidden(judul))

    def test_sensornya_kelihatan(self) -> None:
        """Menghapus diam-diam berarti memalsukan kutipan."""
        assert MASK in mask_forbidden("this trade is risk-free")

    def test_huruf_besar_kecil_tidak_meloloskan(self) -> None:
        assert not find_forbidden(mask_forbidden("GUARANTEED PROFIT ahead"))
        assert not find_forbidden(mask_forbidden("Pasti Naik minggu ini"))

    def test_teks_bersih_tidak_disentuh(self) -> None:
        bersih = "BTC menembus resistance mingguan"
        assert mask_forbidden(bersih) == bersih

    def test_beberapa_frasa_sekaligus_tersensor_semua(self) -> None:
        assert not find_forbidden(
            mask_forbidden("no risk, guaranteed profit, sure thing")
        )


class TestJudulBeritaTidakMembungkamLogPerdebatan:
    """Jalur lengkapnya: judul RSS -> reasoning NewsAgent -> detail objection
    -> _guard() di push. Sebelum diperbaiki, ujungnya adalah ForbiddenClaim dan
    log perdebatan tidak terkirim sama sekali."""

    @staticmethod
    def _context_dengan_judul(judul: str):
        """Context nyata dengan satu berita CRITICAL berjudul `judul`."""
        import sys
        from dataclasses import replace

        sys.path.insert(0, "tests")
        from aruna.news.models import Importance, NewsCategory, NewsItem, Sentiment
        from test_agents import RISING, context_from

        context = context_from(RISING)
        item = NewsItem(
            title=judul,
            url="https://contoh.invalid/1",
            source="uji",
            published_at=context.as_of,
            category=NewsCategory.REGULATION,
            importance=Importance.CRITICAL,
            sentiment=Sentiment.POSITIVE,
            sentiment_confidence=0.9,
        )
        return replace(context, news=(item,))

    def test_news_agent_menyensor_judul_sebelum_jadi_reasoning(self) -> None:
        """Memanggil agent-nya sungguhan. Menyusun ulang f-string-nya di sini
        akan menguji test-nya sendiri, bukan kodenya."""
        from aruna.agents.context_agents import NewsAgent

        opinion = NewsAgent().safe_evaluate(
            self._context_dengan_judul("BTC rally is a guaranteed profit, no risk")
        )
        gabungan = " ".join(opinion.reasoning)
        assert MASK in gabungan, f"judulnya tidak lewat penyensor: {gabungan}"
        assert not find_forbidden(gabungan)

    def test_judul_beracun_tidak_lagi_membatalkan_pengiriman(self) -> None:
        """Ujung jalurnya: _guard() harus meloloskan teks yang memuat judul itu.

        Guard-nya sekarang yang ada di jalur push plan. Jalur log perdebatan
        punya guard-nya sendiri sampai pesan itu dihapus; propertinya tidak ikut
        terhapus, karena reasoning agent tetap bisa sampai ke pesan plan lewat
        caveat.
        """
        from aruna.agents.context_agents import NewsAgent
        from aruna.futures.notify import _guard

        opinion = NewsAgent().safe_evaluate(
            self._context_dengan_judul("analyst: this is a sure thing, 100% win")
        )
        # Persis seperti protest._examine menyalinnya ke detail objection.
        assert _guard(f"YANG MELEMAHKAN INI:\n  - {opinion.reasoning[0]}")

    def test_guard_tetap_menolak_kalimat_tulisan_aruna(self) -> None:
        """Sensornya untuk kutipan. Untuk kalimat ARUNA sendiri, guard harus
        tetap galak - kalau tidak, bug-nya justru tersembunyi."""
        from aruna.futures.notify import _guard
        from aruna.futures.plan import ForbiddenClaim

        with pytest.raises(ForbiddenClaim):
            _guard("ARUNA FUTURES - BTCUSDT\nini pasti naik")

    def test_daftarnya_satu_sumber(self) -> None:
        """Dua salinan akan berpisah diam-diam, dan yang satu jadi lebih
        longgar tanpa ada yang tahu."""
        from aruna.futures.plan import FORBIDDEN_CLAIMS as dari_futures

        assert dari_futures is FORBIDDEN_CLAIMS


class TestLiteralYangDibandingkanHarusAdaDiEnum:
    """Penjaga terhadap keluarga cacat yang sama, bukan cuma dua kejadiannya.

    ``stance = 'OPPOSE'`` melewati review karena string tidak punya pengecekan
    ejaan. Test ini memberikannya.
    """

    def test_objecting_stances_semuanya_anggota_enum(self) -> None:
        from aruna.db.repositories.learning import OBJECTING_STANCES

        anggota = {s.value for s in Stance}
        for nilai in OBJECTING_STANCES:
            assert nilai in anggota, f"{nilai!r} bukan anggota Stance"

    def test_autopsy_memakai_daftar_yang_sama(self) -> None:
        from aruna.db.repositories.learning import OBJECTING_STANCES
        from aruna.learning.autopsy import _OBJECTING_STANCES

        assert set(OBJECTING_STANCES) == set(_OBJECTING_STANCES)

    def test_support_bukan_objection(self) -> None:
        """Kalau SUPPORT ikut terhitung, tingkat vindikasi jadi omong kosong:
        370 dari 875 objection tersimpan justru berarti persetujuan."""
        from aruna.db.repositories.learning import OBJECTING_STANCES

        assert Stance.SUPPORT.value not in OBJECTING_STANCES

    def test_oppose_bukan_anggota_stance(self) -> None:
        """Ini yang disaring query selama ini. Test ini gagal kalau seseorang
        'memperbaiki' dengan menambahkan OPPOSE ke enum, yang akan memecah
        arsip menjadi dua ejaan."""
        assert "OPPOSE" not in {s.value for s in Stance}

    def test_veto_rejected_adalah_nilai_yang_sebenarnya(self) -> None:
        assert VetoReviewOutcome.VETO_REJECTED.value == "VETO_REJECTED"
        assert "REJECTED" not in {v.value for v in VetoReviewOutcome}


class TestPerdebatanTidakLagiJadiPesanSendiri:
    """Operator: "perdebatan council masih muncul di chat telegram".

    Log perdebatan dulu didorong sebagai notifikasi terpisah dari alert plan -
    dua pesan tentang satu peristiwa, dari satu tick yang sama. Yang dihapus
    adalah pesan keduanya; penilaiannya dipindahkan ke dalam pesan plan.
    """

    @staticmethod
    def _verdict():
        """Council yang benar-benar berdebat: satu agent mengubah pendapat."""
        from types import SimpleNamespace as NS

        rebuttal = NS(
            target=NS(value="STRUCTURE"),
            accuser=NS(value="TECHNICAL"),
            ground="overconfident",
            detail="mengakui: confidence melebihi apa yang didukung bukti",
            conceded=True,
        )
        from aruna.core.enums import Decision

        return NS(
            symbol="BTC/USDT",
            interval="4h",
            decision=NS(value="SELL"),
            confidence=0.52,
            # `opinions` wajib ada. `CouncilVerdict` selalu memilikinya, dan
            # log perdebatan sekarang mencetak siapa yang setuju dan siapa yang
            # menentang - bukan lagi sekadar jumlah objection. Stub tanpa
            # `opinions` di sini akan menguji jalur yang tidak ada di produksi.
            opinions=(
                NS(role=NS(value="TECHNICAL"), decision=Decision.SELL,
                   abstained=False),
                NS(role=NS(value="STRUCTURE"), decision=Decision.BUY,
                   abstained=False),
            ),
            protest=NS(objections=(), rebuttals=(rebuttal,), disagreement=0.0),
            veto=NS(vetoes=(), upheld=(), rejected=(), reviews=()),
            judgement=NS(minority_prevailed=False),
        )

    def test_tidak_ada_lagi_pengirim_perdebatan(self) -> None:
        """Bukan sekadar tidak dipasang - tidak ada lagi yang bisa dipasang.

        Membiarkan kelasnya tetap ada sementara pemanggilnya dicabut akan
        menyisakan jalur yang bisa dipasang lagi tanpa sengaja, dan kode yang
        tidak terjangkau tidak pernah ikut diuji.
        """
        import aruna.futures.notify as notify

        assert not hasattr(notify, "DebateNotifier")

    def test_service_tidak_punya_lagi_seam_debat(self) -> None:
        import inspect

        from aruna.futures.service import FuturesPlanService

        params = inspect.signature(FuturesPlanService.__init__).parameters
        assert "debates" not in params

    def test_penilaiannya_pindah_bukan_hilang(self) -> None:
        """Yang berhenti adalah pesannya, bukan pencatatannya."""
        from aruna.futures.debate import note_of

        note = note_of(self._verdict(), symbol="BTCUSDT")
        assert note.symbol == "BTCUSDT"
        assert note.confidence == 0.52
        assert note.debated is True
        assert any("mengubah pendapat" in r for r in note.reasons)

    def test_simbol_perpetual_bukan_simbol_spot(self) -> None:
        """Council membaca BTC/USDT, plan bernama BTCUSDT.

        Penilaian dicari berdasarkan nama plan. Memakai ejaan council akan
        membuat pencariannya tidak pernah cocok - tanpa error, tanpa log,
        hanya bagian PENILAIAN yang tidak pernah muncul.
        """
        from aruna.futures.debate import note_of

        assert note_of(self._verdict()).symbol == "BTC/USDT"
        assert note_of(self._verdict(), symbol="BTCUSDT").symbol == "BTCUSDT"
