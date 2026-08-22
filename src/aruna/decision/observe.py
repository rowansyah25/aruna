"""Membaca apa yang BENAR-BENAR dikerjakan satu keputusan (PASAL 14.3, 14.25).

Modul-modul lain di paket ini mendefinisikan bagaimana sebuah keputusan
*seharusnya* disusun. Modul ini membaca keputusan yang sudah jadi dan
melaporkan bagaimana ia *sebenarnya* disusun - dan jarak antara keduanya
adalah satu-satunya angka yang bisa dipakai memutuskan gerbang mana yang layak
dipasang.

**Mengamati, bukan menahan.** Tidak ada satu pun jalur dari modul ini yang bisa
membatalkan rencana, menahan pesan, atau mengubah arah. Alasannya bukan
kehati-hatian umum melainkan pengukuran: gerbang risiko Phase 13 dan daftar
periksa empat belas butir sudah dua kali terukur akan membungkam ARUNA hampir
sepenuhnya kalau dipasang apa adanya. Memasang gerbang ketiga sebelum yang
pertama diukur akan menghasilkan sistem yang diam karena tiga sebab sekaligus,
dan tidak satu pun bisa dibedakan dari pasar yang sepi.

**Ketiadaan dibaca sebagai ketiadaan, bukan sebagai kegagalan.** Sebagian
langkah PASAL 14.3 memang belum ada di jalur futures - analisis multi-timeframe
serentak salah satunya, karena jalur ini merencanakan satu horizon. Yang
dilaporkan adalah "tidak dikerjakan", dan itu fakta tentang sistemnya, bukan
tuduhan terhadap satu keputusan.

**Semua pembacaannya defensif.** Objek yang dibacanya datang dari lima lapisan
berbeda dan bentuknya bisa berubah; sebuah atribut yang hilang di sini tidak
boleh menjatuhkan rencana yang membawa entry dan stop. Karena itu tiap
pembacaan lewat :func:`_ada`, dan hasil "tidak tahu" diperlakukan sama dengan
"tidak dikerjakan" - lebih baik melaporkan langkah yang sebenarnya ada sebagai
hilang daripada sebaliknya.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aruna.decision.audit import Butir, audit
from aruna.decision.hierarchy import Pengamat, Tahap


def _ada(obj: Any, *jalan: str) -> bool:
    """Apakah rantai atribut ini ada dan tidak kosong.

    ``_ada(plan, "stop_detail", "invalidation")`` benar hanya kalau keduanya
    ada dan yang terakhir bukan ``None``, kosong, atau nol. Pengecualian apa
    pun dibaca sebagai "tidak ada" - lihat catatan modul.
    """
    try:
        nilai: Any = obj
        for nama in jalan:
            if nilai is None:
                return False
            nilai = getattr(nilai, nama, None)
        return bool(nilai)
    except Exception:  # noqa: BLE001 - lihat catatan modul
        # Sengaja selebar mungkin. Objek yang dibaca datang dari lima lapisan
        # berbeda, dan sebuah properti yang melempar - bukan sekadar hilang -
        # tidak boleh menjatuhkan rencana yang membawa entry dan stop. Yang
        # hilang saat ini terjadi hanyalah satu bidang pengukuran.
        return False


def _terukur(obj: Any, *jalan: str) -> bool:
    """Apakah rantai atribut ini **ada**, tanpa menilai isinya.

    Beda dari :func:`_ada` tepat pada satu hal, dan hal itu penting: nilai nol
    dihitung ada. Keyakinan 0% adalah hasil pengukuran - council menilai dan
    menyimpulkan nol - sedangkan keyakinan yang tidak pernah dihitung tidak
    punya nilai sama sekali. Menyamakan keduanya membuat lapisan yang berjalan
    dilaporkan sebagai lapisan yang hilang.
    """
    try:
        nilai: Any = obj
        for nama in jalan:
            if nilai is None:
                return False
            nilai = getattr(nilai, nama, None)
        return nilai is not None
    except Exception:  # noqa: BLE001 - lihat catatan modul
        return False


def _berarah(plan: Any, verdict: Any) -> bool:
    """Apakah keputusan ini punya arah sama sekali.

    Dibaca dari **sisi posisi** rencananya, bukan dari vonisnya: sebuah
    rencana bisa DITOLAK dan tetap punya arah - ia punya tesis, punya stop,
    dan punya syarat pembatalan; yang tidak lolos hanyalah ongkosnya. Yang
    benar-benar tidak berarah adalah WAIT, dan di situ tidak ada sisi sama
    sekali.

    Terukur: dari dua puluh simbol, tujuh REFUSED semuanya punya sisi dan
    syarat pembatalan, dan tiga belas WAIT tidak satu pun punya keduanya.

    **Sisinya dieja, bukan dinilai keberadaannya.** Versi pertama memakai
    :func:`_ada`, dan rencana WAIT menyimpan ``side='FLAT'`` - sebuah objek
    yang ada, truthy, dan artinya persis "tidak berarah". Tiga belas keputusan
    tanpa arah tercatat berarah, dan pelonggaran yang dibuat untuk mereka tidak
    pernah aktif. Ini kelas kesalahan yang sama dengan ``confidence=0``: nilai
    yang sah dan bermakna "tidak ada", dinilai lewat kebenarannya.
    """
    sisi = str(getattr(getattr(plan, "side", None), "value", "") or "")
    if sisi in {"LONG", "SHORT"}:
        return True
    arah = str(getattr(getattr(verdict, "decision", None), "value", "") or "")
    return arah in {"BUY", "SELL", "LONG", "SHORT"}


@dataclass(frozen=True, slots=True)
class Amatan:
    """Apa yang dikerjakan satu keputusan, dan apa yang tidak."""

    pengamat: Pengamat
    checklist: Any
    #: Langkah PASAL 14.3 yang tidak dikerjakan sama sekali.
    absent: tuple[Tahap, ...] = field(default_factory=tuple)
    #: Vonis rencananya - PLAN, REFUSED, atau apa pun yang dilaporkan.
    #:
    #: Ada di sini karena tanpanya angka pengamatan tidak bisa ditafsirkan:
    #: "risk/reward selalu kosong" berarti hal yang sangat berbeda pada dua
    #: puluh rencana yang ditolak dibanding pada dua puluh rencana yang terbit.
    verdict: str = ""

    @property
    def steps(self) -> int:
        return len(self.pengamat.jalur.done)

    @property
    def order_findings(self) -> tuple[str, ...]:
        return self.pengamat.findings

    def summary(self) -> dict[str, object]:
        """Bentuk yang masuk ke log - angka, bukan kalimat.

        Satu baris per simbol per tick. Yang dicari dari kumpulannya nanti
        adalah distribusi, dan distribusi butuh angka yang bisa dijumlahkan.
        """
        a = self.checklist
        return {
            "verdict": self.verdict,
            "steps": self.steps,
            "absent": [t.name for t in self.absent],
            "order_findings": list(self.order_findings),
            "mandatory_missing": [
                t.name for t in self.pengamat.jalur.missing_mandatory
            ],
            "audit_pass": len(a.values) - len(a.failures) - len(a.unknowns),
            "audit_fail": [b.name for b in a.failures],
            "audit_unknown": [b.name for b in a.unknowns],
            "may_publish": a.may_publish,
        }


def amati(
    *,
    context: Any = None,
    verdict: Any = None,
    plan: Any = None,
    note: Any = None,
) -> Amatan:
    """Baca satu keputusan yang sudah jadi.

    Tiap langkah dinilai dari bukti yang **ada di objeknya**, bukan dari
    keyakinan bahwa lapisannya dipanggil. Sebuah lapisan yang berjalan dan
    tidak meninggalkan jejak apa pun di keluarannya tidak bisa dibedakan dari
    lapisan yang tidak berjalan - dan untuk tujuan pengukuran ini, keduanya
    memang sama.
    """
    dikerjakan: dict[Tahap, bool] = {
        # Gerbang integritas SPEC 46 meninggalkan hasilnya di rencana.
        Tahap.DATA_VALIDITY: _ada(plan, "integrity"),
        # Waktu bukti - bar tertutup terbaru di balik keputusannya.
        Tahap.DATA_FRESHNESS: _ada(plan, "evidence_as_of") or _ada(context, "as_of"),
        Tahap.MARKET_REGIME: _ada(context, "regime"),
        # Peta lintas timeframe (PASAL 14.4), dititipkan di catatan council.
        # Dinilai dari **jumlah barisnya**, bukan dari keberadaan objeknya:
        # sebuah peta yang berisi satu timeframe bukan analisis lintas
        # timeframe, ia analisis satu timeframe dengan bungkus yang lebih
        # besar. Butuh minimal dua untuk ada yang bisa dibandingkan.
        Tahap.MTF: len(getattr(getattr(note, "lintas", None), "readings", ()) or ()) >= 2,
        Tahap.AGENTS: _ada(verdict, "opinions"),
        Tahap.PROTEST: _ada(verdict, "protest", "objections"),
        Tahap.COUNCIL: verdict is not None,
        # **Confidence nol adalah pengukuran, bukan ketiadaan.** Versi pertama
        # baris ini memakai :func:`_ada`, yang menilai kebenaran nilainya -
        # jadi council yang melaporkan keyakinan 0% tercatat sebagai council
        # yang tidak pernah menilai keyakinan. Terukur: 13 dari 20 simbol
        # "kehilangan" langkah ini pada pengukuran pertama, dan tidak satu pun
        # benar-benar hilang.
        Tahap.QUALITY: _terukur(note, "confidence"),
        # **Strategi ada di KONTEKS, bukan di catatan council.** Phase 12
        # sudah mengalir ke jalur ini lewat `AgentService._build_context`
        # (PASAL 12.6), dan pengukuran pertama melaporkannya "selalu kosong"
        # karena mencarinya di tempat yang salah - kesimpulan yang salah
        # tentang lapisan yang sebenarnya berjalan.
        Tahap.STRATEGY: _ada(context, "strategy"),
        Tahap.RISK: _ada(note, "risk_readings"),
        # **Dua tempat, dan keduanya sah.** ``plan.net_rr`` hanya terisi pada
        # rencana yang lolos seluruh gerbang; sebuah rencana yang DITOLAK
        # karena imbalannya tidak sepadan jelas sudah menghitung rasionya -
        # angkanya ada di ``economics``, dan kalimat penolakannya mengutipnya.
        #
        # Terukur: tujuh rencana REFUSED, tujuh punya alasan berbasis rasio,
        # dan nol yang ``plan.net_rr``-nya terisi. Membaca satu tempat saja
        # melaporkan langkah yang jelas dikerjakan sebagai langkah yang hilang.
        #
        # ``economics.net_rr`` sendiri bisa ``None`` ketika risiko yang
        # direncanakan nol - di situ rasionya memang tidak bisa dihitung, dan
        # "tidak ada" adalah laporan yang benar.
        Tahap.RR: _ada(plan, "net_rr") or _ada(plan, "economics", "net_rr"),
        Tahap.INVALIDATION: _ada(plan, "stop_detail", "invalidation"),
        Tahap.HORIZON: _ada(plan, "horizon_hours"),
        Tahap.FINAL: _ada(plan, "verdict"),
    }

    p = Pengamat()
    for tahap in Tahap:
        p = p.note(tahap, done=dikerjakan[tahap])

    hasil = audit({
        Butir.DATA: dikerjakan[Tahap.DATA_VALIDITY] or None,
        Butir.FRESHNESS: dikerjakan[Tahap.DATA_FRESHNESS] or None,
        Butir.REGIME: dikerjakan[Tahap.MARKET_REGIME] or None,
        Butir.MTF: dikerjakan[Tahap.MTF] or None,
        Butir.AGENTS: dikerjakan[Tahap.AGENTS] or None,
        Butir.PROTEST: dikerjakan[Tahap.PROTEST] or None,
        Butir.COUNCIL: dikerjakan[Tahap.COUNCIL] or None,
        Butir.QUALITY: dikerjakan[Tahap.QUALITY] or None,
        Butir.STRATEGY: dikerjakan[Tahap.STRATEGY] or None,
        Butir.RISK: dikerjakan[Tahap.RISK] or None,
        Butir.RR: dikerjakan[Tahap.RR] or None,
        Butir.INVALIDATION: dikerjakan[Tahap.INVALIDATION] or None,
        Butir.EXPIRATION: dikerjakan[Tahap.HORIZON] or None,
        Butir.HORIZON: dikerjakan[Tahap.HORIZON] or None,
    }, directional=_berarah(plan, verdict))

    return Amatan(
        pengamat=p,
        checklist=hasil,
        absent=tuple(t for t in Tahap if not dikerjakan[t]),
        verdict=str(
            getattr(getattr(plan, "verdict", None), "value", "") or ""
        ),
    )


__all__ = ["Amatan", "amati"]
