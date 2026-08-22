"""Ingatan hanya diberi bobot di timeframe yang terbukti membantu (PASAL 15.44).

Terukur 2026-08-21 atas 9.698 ingatan, dengan disiplin ``as_of`` penuh:

    15m   SUPPORTIVE 54% (186)  CONTRARY 40% (103)  selisih +14  membantu
    1h    SUPPORTIVE 58% (159)  CONTRARY 65% (43)   selisih  -7  TIDAK membantu

Dan 1h adalah yang dipinjam jalur keputusan langsung lewat `horizon_ingatan()`.
Jadi ARUNA memberi bobot pada bukti yang evaluasinya sendiri bilang tidak
menambah apa-apa - persis yang PASAL 15.44 larang: *jangan memaksakan
penggunaan memory*.

**Yang digerbangi bobotnya, bukan tampilannya.** Kasus serupa tetap dicetak ke
operator (PASAL 15.20, 15.38): menyembunyikan bukti yang bertentangan adalah
confirmation bias yang dilakukan sistem atas nama operator. Yang berhenti
adalah pengaruhnya terhadap keputusan.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aruna.memory.evaluasi import SAMPEL_SISI, Evaluasi
from aruna.memory.manfaat import KUNCI_STATE, Manfaat, dari_json, ke_json

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _eval(*, mw: int, ml: int, cw: int, cl: int) -> Evaluasi:
    return Evaluasi(
        mendukung_menang=mw, mendukung_kalah=ml,
        melawan_menang=cw, melawan_kalah=cl,
    )


def _manfaat(tf: str, ev: Evaluasi, *, dari: int = 1000) -> Manfaat:
    return Manfaat(timeframe=tf, evaluasi=ev, dinilai_pada=NOW, dinilai_dari=dari)


class TestKapanDipakai:
    def test_terbukti_membantu_dipakai(self) -> None:
        """15m: SUPPORTIVE 54% dari 186, CONTRARY 40% dari 103, selisih +14."""
        m = _manfaat("15m", _eval(mw=100, ml=86, cw=41, cl=62))

        assert m.evaluasi.selisih == 14
        assert m.dipakai

    def test_tidak_menambah_apa_apa_tidak_dipakai(self) -> None:
        """1h: SUPPORTIVE 58% dari 159, CONTRARY 65% dari 43, selisih -7.

        Bukan `terbalik` - selisihnya belum mencapai -10 - tapi juga bukan
        `membantu`. PASAL 15.44 mengejanya: jangan memaksakan penggunaan
        memory. Yang tidak menambah apa-apa tidak diberi bobot.
        """
        m = _manfaat("1h", _eval(mw=92, ml=67, cw=28, cl=15))

        assert m.evaluasi.selisih == -7
        assert not m.evaluasi.membantu
        assert not m.evaluasi.terbalik
        assert not m.dipakai

    def test_berlawanan_jelas_tidak_dipakai(self) -> None:
        m = _manfaat("4h", _eval(mw=30, ml=70, cw=70, cl=30))

        assert m.evaluasi.terbalik
        assert not m.dipakai

    def test_sampel_kurang_tidak_dipakai(self) -> None:
        """Timeframe baru belum pernah membuktikan apa pun.

        Diam berarti belum terbukti, bukan terbukti baik - kalau sebaliknya,
        setiap timeframe baru mulai hidupnya dengan bobot penuh atas bukti yang
        belum pernah diuji.
        """
        m = _manfaat("1d", _eval(mw=5, ml=2, cw=3, cl=1), dari=11)

        assert not m.evaluasi.cukup
        assert not m.dipakai


class TestAlasannyaBisaDibaca:
    def test_menyebut_angkanya_bukan_cuma_putusannya(self) -> None:
        """Operator yang melihat pengaruh ingatan dimatikan harus bisa tahu
        atas dasar apa, tanpa menjalankan ulang evaluasinya."""
        m = _manfaat("1h", _eval(mw=92, ml=67, cw=28, cl=15))

        alasan = m.alasan()

        assert "1h" in alasan
        assert "-7" in alasan

    def test_sampel_kurang_mengatakan_begitu_bukan_menyalahkan_memory(self) -> None:
        m = _manfaat("1d", _eval(mw=5, ml=2, cw=3, cl=1), dari=11)

        alasan = m.alasan()

        assert "belum" in alasan.lower()
        assert str(SAMPEL_SISI) in alasan


class TestBolakBalikJson:
    def test_pulang_pergi_utuh(self) -> None:
        """Disimpan di `app_state` supaya kedua proses membacanya - dan
        `futures-loop` adalah yang benar-benar memakainya, bukan `aruna run`.
        Itu persis kesalahan yang membuat ingatan sempat tersambung ke proses
        yang salah."""
        asal = {
            "15m": _manfaat("15m", _eval(mw=100, ml=86, cw=41, cl=62)),
            "1h": _manfaat("1h", _eval(mw=92, ml=67, cw=28, cl=15)),
        }

        pulih = dari_json(ke_json(asal))

        assert set(pulih) == {"15m", "1h"}
        assert pulih["15m"].dipakai
        assert not pulih["1h"].dipakai
        assert pulih["1h"].evaluasi.selisih == -7
        assert pulih["1h"].dinilai_pada == NOW

    def test_json_rusak_tidak_meledak(self) -> None:
        """`app_state` yang kosong atau ditulis versi lama tidak boleh
        menjatuhkan tick futures."""
        assert dari_json(None) == {}
        assert dari_json({"15m": {"bukan": "bentuk yang benar"}}) == {}

    def test_kuncinya_stabil(self) -> None:
        """Kunci yang berubah membuat penilaian lama tak terbaca, dan
        gerbangnya diam-diam kembali terbuka."""
        assert KUNCI_STATE == "memory_manfaat"


# ---- gerbangnya di `susun` -------------------------------------------------

MEMBANTU = _manfaat("15m", _eval(mw=100, ml=86, cw=41, cl=62))
TIDAK = _manfaat("1h", _eval(mw=92, ml=67, cw=28, cl=15))


def _konteks(*, manfaat=None, menang: int = 18, kalah: int = 2,
             dasar_menang: int = 0):
    """Konteks dari kasus yang jelas SUPPORTIVE, lewat `susun` yang sungguhan."""
    from decimal import Decimal

    from aruna.memory.context import susun
    from aruna.memory.dimensions import UNKNOWN, Dimensi
    from aruna.memory.fingerprint import Sidik
    from aruna.memory.outcome import ringkas
    from aruna.memory.record import Hasil, Ingatan, Mutu
    from aruna.memory.similarity import bandingkan

    def _satu(i: int, hasil: Hasil) -> Ingatan:
        nilai = dict.fromkeys(Dimensi, UNKNOWN)
        nilai[Dimensi.ASSET] = "BTC/USDT"
        nilai[Dimensi.TIMEFRAME] = "1h"
        nilai[Dimensi.REGIME] = "TRENDING"
        return Ingatan(
            signal_id=f"s{i}", sidik=Sidik(nilai=nilai), arah="BUY", hasil=hasil,
            move_pct=Decimal("1"), locked_at=NOW, resolved_at=NOW,
            model_version="v1", cakupan=13, mutu=Mutu.HIGH,
        )

    ingatan = [_satu(i, Hasil.WIN) for i in range(menang)]
    ingatan += [_satu(100 + i, Hasil.LOSS) for i in range(kalah)]
    cocok = [(i, bandingkan(i.sidik, i.sidik)) for i in ingatan]

    # Dasar bawaannya jelas lebih buruk, supaya pengaruhnya SUPPORTIVE tanpa
    # ragu. `dasar_menang` menaikkannya untuk kasus di mana sejarah memang
    # tidak berpendapat - selisih terhadap dasar yang setara adalah nol.
    dasar_ingatan = [_satu(200 + i, Hasil.WIN) for i in range(dasar_menang)]
    dasar_ingatan += [
        _satu(300 + i, Hasil.LOSS) for i in range(20 - dasar_menang)
    ]
    dasar = ringkas([(i, bandingkan(i.sidik, i.sidik)) for i in dasar_ingatan])

    return susun(
        arah_sekarang="BUY", cocok=cocok, dasar=dasar, as_of=NOW,
        manfaat=manfaat,
    )


class TestGerbangDiSusun:
    def test_tanpa_gerbang_pengaruhnya_hidup(self) -> None:
        """Dasar perbandingan: tanpa putusan manfaat, sejarah berpendapat."""
        from aruna.memory.context import Pengaruh

        k = _konteks()

        assert k.pengaruh is Pengaruh.SUPPORTIVE
        assert not k.digerbangi

    def test_timeframe_yang_membantu_tidak_digerbangi(self) -> None:
        from aruna.memory.context import Pengaruh

        k = _konteks(manfaat=MEMBANTU)

        assert k.pengaruh is Pengaruh.SUPPORTIVE
        assert not k.digerbangi

    def test_timeframe_yang_tidak_membantu_dipaksa_neutral(self) -> None:
        """Inti seluruh perubahan ini: 1h terukur -7, jadi pendapat sejarah di
        sana tidak diberi bobot."""
        from aruna.memory.context import Pengaruh

        k = _konteks(manfaat=TIDAK)

        assert k.pengaruh is Pengaruh.NEUTRAL
        assert k.digerbangi

    def test_buktinya_tetap_utuh_saat_digerbangi(self) -> None:
        """PASAL 15.20 dan 15.38: yang digerbangi bobotnya, bukan haknya
        dilihat. Menyembunyikan kasusnya adalah confirmation bias yang
        dilakukan sistem atas nama operator."""
        k = _konteks(manfaat=TIDAK)

        assert k.ringkasan.total == 20
        assert k.memory_ids
        assert k.kontribusi > 0

    def test_alasannya_ikut_di_catatan(self) -> None:
        """Gerbang yang tidak menerangkan dirinya membuat operator melihat
        NEUTRAL tanpa cara tahu kenapa."""
        k = _konteks(manfaat=TIDAK)

        assert any("1h" in c for c in k.catatan)

    def test_operator_diberi_tahu_saat_bobotnya_dimatikan(self) -> None:
        """Tanpa ini operator melihat NEUTRAL dan menyimpulkan sejarah tidak
        berpendapat - padahal ia berpendapat dan pendapatnya sengaja tidak
        dipakai."""
        from aruna.futures.notify import _konteks_historis

        class _Note:
            memory = _konteks(manfaat=TIDAK)

        teks = "\n".join(_konteks_historis(_Note()))

        assert "bobot dimatikan" in teks

    def test_tidak_diumumkan_kalau_tidak_digerbangi(self) -> None:
        """Kalimat yang selalu muncul berhenti berarti."""
        from aruna.futures.notify import _konteks_historis

        class _Note:
            memory = _konteks(manfaat=MEMBANTU)

        teks = "\n".join(_konteks_historis(_Note()))

        assert "bobot dimatikan" not in teks

    def test_jejak_audit_mencatat_gerbangnya(self) -> None:
        """Jejak yang cuma menulis NEUTRAL tidak bisa menjawab, berbulan-bulan
        kemudian, apakah sejarah memang diam atau bobotnya dimatikan."""
        from aruna.futures.service import _jejak_memory

        jejak = _jejak_memory(_konteks(manfaat=TIDAK))

        assert jejak["memory_pengaruh"] == "NEUTRAL"
        assert jejak["memory_digerbangi"] is True

    def test_jejak_selalu_punya_bidangnya(self) -> None:
        """Bidang yang hilang saat ingatan tidak terbaca membuat "tidak ada
        ingatan" tidak bisa dibedakan dari "fasenya tidak jalan"."""
        from aruna.futures.service import _jejak_memory

        assert _jejak_memory(None)["memory_digerbangi"] is False
        assert _jejak_memory(_konteks())["memory_digerbangi"] is False

    def test_neutral_karena_diam_bukan_digerbangi(self) -> None:
        """Dua NEUTRAL yang berbeda arti. "Sejarah tidak berpendapat" dan
        "pendapat sejarah sengaja tidak dipakai" harus bisa dibedakan, kalau
        tidak gerbang ini tidak terlihat oleh siapa pun yang membaca
        keputusannya."""
        from aruna.memory.context import Pengaruh

        # Kasus 50% melawan dasar 50%: selisihnya nol, sejarah benar-benar
        # tidak berpendapat - tidak ada apa pun untuk digerbangi.
        k = _konteks(manfaat=TIDAK, menang=10, kalah=10, dasar_menang=10)

        assert k.pengaruh is Pengaruh.NEUTRAL
        assert not k.digerbangi


class TestTerangkaiDiProduksi:
    """Kegagalan yang paling sering terjadi di repo ini, dan yang PASAL 15.32
    sendiri pernah kena: putusannya dihitung di `aruna run`, dipakai di
    `futures-loop`. Salah proses berarti gerbangnya tidak pernah menutup di
    satu pun keputusan hidup."""

    def _pohon(self, fn):
        import ast
        import inspect
        from textwrap import dedent

        return ast.parse(dedent(inspect.getsource(fn)))

    def _kata_kunci(self, pohon, nama: str) -> set[str]:
        import ast

        return {
            k.arg
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == nama
            for k in n.keywords
        }

    def test_penilainya_dirangkai_ke_loop_upkeep(self) -> None:
        from aruna import app as modul

        kata = self._kata_kunci(
            self._pohon(modul.ArunaApplication._start_upkeep), "UpkeepLoop"
        )

        assert "manfaat" in kata

    def test_pembacanya_dirangkai_ke_proses_futures(self) -> None:
        """Bukan ke `aruna run`. Yang mengambil keputusan futures adalah
        proses ini, dan gerbangnya harus menutup di sana."""
        import ast
        import inspect

        from aruna import cli as modul

        # Seluruh modul dipindai, bukan fungsi yang ditebak dari namanya:
        # penebakan yang meleset menghasilkan test yang lolos tanpa memeriksa
        # apa pun.
        pohon = ast.parse(inspect.getsource(modul))
        panggilan = [
            n for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "FuturesPlanService"
        ]

        assert panggilan, "cli tidak pernah membangun FuturesPlanService"
        assert any(
            "app_state" in {k.arg for k in p.keywords} for p in panggilan
        )

    def test_fase_penilaian_dipanggil_dari_cycle(self) -> None:
        import ast

        from aruna.upkeep.loop import UpkeepLoop

        pohon = self._pohon(UpkeepLoop.cycle)
        dipanggil = {
            n.func.attr
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

        assert "_manfaat_due_now" in dipanggil
        assert "_nilai_manfaat" in dipanggil

    def test_dinilai_sesudah_proyeksi_ingatan(self) -> None:
        """Ingatan yang lahir siklus ini adalah bahan penilaiannya."""
        import ast

        from aruna.upkeep.loop import UpkeepLoop

        pohon = self._pohon(UpkeepLoop.cycle)
        posisi = {
            n.func.attr: n.lineno
            for n in ast.walk(pohon)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("_proyeksikan_memory", "_nilai_manfaat")
        }

        assert posisi["_proyeksikan_memory"] < posisi["_nilai_manfaat"]
