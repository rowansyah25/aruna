"""Telegram message builders.

Plain text, no ``parse_mode``.  MarkdownV2 requires escaping eighteen
characters, and a single missed escape turns a health alert into a 400 from the
API - exactly when you need the alert to arrive.  The block layout in SPEC 21
does not need markup to be readable.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from aruna.core.clock import isoformat, now_jakarta, now_utc, wib
from aruna.core.config import Settings
from aruna.core.enums import HealthStatus
from aruna.core.runtime_state import RuntimeState
from aruna.health.models import ComponentHealth, HealthReport
from aruna.notify.telegram.registry import Command, commands_by_phase

#: Telegram rejects messages over 4096 characters.
MAX_MESSAGE_LEN = 4096

_STATUS_MARK: dict[HealthStatus, str] = {
    HealthStatus.UP: "[ OK ]",
    HealthStatus.DEGRADED: "[WARN]",
    HealthStatus.DOWN: "[DOWN]",
    HealthStatus.DISABLED: "[ -- ]",
    HealthStatus.UNKNOWN: "[ ?? ]",
}


def truncate(text: str, limit: int = MAX_MESSAGE_LEN) -> str:
    if len(text) <= limit:
        return text
    marker = "\n... (dipotong)"
    return text[: limit - len(marker)] + marker


def header(title: str) -> str:
    return f"ARUNA - {title}\n" + "=" * min(len(f"ARUNA - {title}"), 32)


def timestamps() -> str:
    """Both zones, each labelled with the zone it actually is.

    The WIB rollout replaced every ``isoformat`` call in this file with
    ``wib``, including the one behind the ``UTC:`` label - so this line printed
    Jakarta time twice and called the first one UTC. A timestamp labelled with
    the wrong zone is worse than one with no label: the reader has no way to
    notice, and a seven-hour error looks exactly like a correct answer.
    """
    return (
        f"UTC: {isoformat(now_utc())}\n"
        f"WIB: {now_jakarta():%Y-%m-%d %H:%M:%S}"
    )


def welcome(settings: Settings, phase: int, *, authorized: bool) -> str:
    lines = [
        header("AI"),
        "",
        "Riset Market & Intelijen Paper Trading",
        f"Market: {', '.join(m.value for m in settings.app.enabled_markets)}",
        f"Instance: {settings.app.instance_name} ({settings.app.env})",
        f"Build: PHASE {phase}",
        "",
        "MODE: PAPER TRADING SAJA",
        "Eksekusi order sungguhan tidak dibangun dan dimatikan lewat",
        "konfigurasi (SPEC 46).",
        "",
        f"Chat ini: {'DIIZINKAN' if authorized else 'TIDAK DIIZINKAN'}",
    ]
    if not authorized:
        lines += [
            "",
            "Perintah dari chat ini akan ditolak. Tambahkan chat id ini ke",
            "ARUNA_TELEGRAM_CHAT_ID atau ARUNA_TELEGRAM_ALLOWED_CHAT_IDS.",
        ]
    lines += ["", "Kirim /help untuk daftar perintah, /status untuk kondisi sistem."]
    return truncate("\n".join(lines))


def help_text(registry: dict[str, Command], current_phase: int) -> str:
    lines = [header("PERINTAH"), "", f"Build: PHASE {current_phase}", ""]
    for phase, commands in commands_by_phase(registry).items():
        marker = "TERSEDIA" if phase <= current_phase else f"PHASE {phase}"
        lines.append(f"--- {marker} ---")
        for command in commands:
            state = "" if command.implemented else "  [belum dibangun]"
            privileged = "  [khusus admin]" if command.privileged else ""
            lines.append(f"/{command.name}{state}{privileged}")
            lines.append(f"    {command.summary}")
        lines.append("")
    return truncate("\n".join(lines))


def status_summary(
    report: HealthReport | None, settings: Settings, state: RuntimeState, phase: int
) -> str:
    lines = [header("STATUS"), "", timestamps(), ""]

    kill = state.kill_switch
    lines += [
        f"INSTANCE:  {settings.app.instance_name} ({settings.app.env})",
        f"BUILD:     PHASE {phase}",
        f"MARKET:    {', '.join(m.value for m in settings.app.enabled_markets)}",
        "MODE:      PAPER TRADING",
        f"UPTIME:    {_duration(state.uptime_seconds)}",
        f"TRADING:   {'DIIZINKAN' if state.trading_allowed else 'DIBLOKIR (kill switch)'}",
    ]
    if kill.active:
        lines += [
            "",
            "KILL SWITCH: AKTIF",
            f"  alasan: {kill.reason or '(tidak disebutkan)'}",
            f"  oleh:   {kill.actor or 'tidak diketahui'}",
            f"  sejak:  {wib(kill.changed_at) if kill.changed_at else 'tidak diketahui'}",
        ]

    lines += ["", "KOMPONEN"]
    if report is None:
        lines.append("  belum ada health sweep yang selesai")
    else:
        lines.append(f"  KESELURUHAN: {report.status.value}")
        for component in report.components:
            mark = _STATUS_MARK[component.status]
            latency = f" {component.latency_ms:.0f}ms" if component.latency_ms else ""
            lines.append(f"  {mark} {component.name}{latency}")
            if component.message:
                lines.append(f"         {component.message}")

    warnings = settings.startup_warnings()
    if warnings:
        lines += ["", "PERINGATAN KONFIGURASI"]
        lines += [f"  ! {w}" for w in warnings]

    notices = settings.phase_notices()
    if notices:
        lines += ["", f"KEKURANGAN YANG DIKETAHUI DI PHASE {phase}"]
        lines += [f"  - {n}" for n in notices]

    return truncate("\n".join(lines))


def health_detail(report: HealthReport | None) -> str:
    if report is None:
        return truncate(
            header("HEALTH") + "\n\nBelum ada health sweep yang selesai.\n"
            "STATUS: UNKNOWN"
        )

    lines = [
        header("HEALTH"),
        "",
        f"KESELURUHAN: {report.status.value}",
        f"DICEK:       {wib(report.checked_at)}",
        f"DURASI:      {report.duration_ms:.0f} ms",
        "",
    ]
    for component in report.components:
        lines.append(f"{_STATUS_MARK[component.status]} {component.name.upper()}")
        lines.append(f"    status:  {component.status.value}")
        if component.latency_ms is not None:
            lines.append(f"    latency: {component.latency_ms:.1f} ms")
        if component.message:
            lines.append(f"    detail:  {component.message}")
        if component.consecutive_failures:
            lines.append(f"    gagal: {component.consecutive_failures} kali berturut-turut")
        for key, value in _flatten(component.details):
            lines.append(f"    {key}: {value}")
        lines.append("")
    return truncate("\n".join(lines))


def health_alert(report: HealthReport, changed: Iterable[ComponentHealth]) -> str:
    changed = tuple(changed)
    worst = max((c.status for c in changed), key=lambda s: s.severity)
    # Recoveries reach this function now that they are no longer swallowed
    # upstream, and delivering "HEALTH ALERT: database UP" would be its own
    # small dishonesty - the reader scans the header, not the body.
    recovered = all(c.status.is_operational for c in changed)
    lines = [
        header("HEALTH PULIH" if recovered else "PERINGATAN HEALTH"),
        "",
        f"KESELURUHAN: {report.status.value}",
        f"WAKTU:       {wib(report.checked_at)}",
        "",
        "BERUBAH",
    ]
    for component in changed:
        lines.append(f"  {_STATUS_MARK[component.status]} {component.name}")
        lines.append(f"        {component.status.value}: {component.message}")

    if worst is HealthStatus.DOWN:
        lines += [
            "",
            "ARUNA tidak menghasilkan signal selama komponen inti berstatus DOWN",
            "(SPEC 5: data tidak valid menghasilkan NO SIGNAL).",
        ]
    return truncate("\n".join(lines))


def kill_switch_engaged(reason: str, actor: str) -> str:
    return truncate(
        "\n".join(
            [
                header("KILL SWITCH"),
                "",
                "KONDISI: AKTIF",
                f"OLEH:    {actor}",
                f"ALASAN:  {reason}",
                f"WAKTU:   {wib(now_utc())}",
                "",
                "ARUNA tidak akan menghasilkan atau mengunci signal baru sampai",
                "/resume. Posisi paper yang terbuka tetap dilacak - pencatatan",
                "outcome adalah bukti, dan menghentikannya akan merusak catatan",
                "(SPEC 22).",
            ]
        )
    )


def kill_switch_released(actor: str) -> str:
    return truncate(
        "\n".join(
            [
                header("KILL SWITCH"),
                "",
                "KONDISI: DILEPAS",
                f"OLEH:    {actor}",
                f"WAKTU:   {wib(now_utc())}",
                "",
                "Pembuatan signal diizinkan lagi, tetap tunduk pada pengecekan",
                "kualitas data dan no-trade seperti biasa.",
            ]
        )
    )


def _money(value: object, places: int = 0) -> str:
    """Rupiah-style grouping.  Returns '-' rather than guessing at a missing value.

    Indonesian convention is the reverse of the C locale: '.' groups thousands
    and ',' marks the decimal.  Swapping only the separator would render 4200.00
    as '4.200.00', which reads as neither.
    """
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    whole, _, fraction = f"{number:,.{places}f}".partition(".")
    whole = whole.replace(",", ".")
    return f"{whole},{fraction}" if fraction else whole


#: Widest price ARUNA will print.  Binance spot's finest tick on the seeded
#: universe is 0.0001, and the column is DECIMAL(30,12) - rendering all twelve
#: stored decimals would be noise, not precision.
_MAX_PRICE_PLACES = 8


def _price(value: object) -> str:
    """A price at the precision it was actually quoted in.

    ``_money(value, 0)`` was correct while every price ARUNA held was a whole
    rupiah.  It is destructive now that crypto is quoted in USDT: XRP at
    1.0015 renders as ``1``, SOL at 75.81 as ``76``, and a bid/ask pair
    collapses to two identical numbers with the spread rendered invisible. The
    operator reads a wrong price on a phone while every formatting test stays
    green, because those tests pass fabricated rows with IDX-sized figures.

    The precision is **recovered from the value, not chosen**.  A stored
    ``1.001500000000`` is normalised to ``1.0015``: those are the digits the
    venue published, and the trailing zeros are the column's padding.  Nothing
    is rounded up, no precision is invented for a value that lacks it, and a
    whole number still prints whole.

    This is a stopgap and worth naming as one.  The right source is
    ``assets.price_precision``, which is still NULL for every row;
    ``BinanceSpotProvider.fetch_metadata`` can fill it from ``tickSize``, but
    nothing calls it during seed yet.  Until then, reading the digits back off
    the value is the only option that does not guess.
    """
    if value is None:
        return "-"
    if isinstance(value, Decimal):
        # normalize() drops padding; the quantize guard stops an exponent form
        # like 6.3E+4 reaching the operator instead of 63000.
        trimmed = value.normalize()
        exponent = trimmed.as_tuple().exponent
        places = 0
        if isinstance(exponent, int) and exponent < 0:
            places = min(-exponent, _MAX_PRICE_PLACES)
        return _money(trimmed, places)
    return _money(value, 0)


def _freshness_line(row: dict) -> str:
    """One line that never overstates the feed (SPEC 3, 5, 49)."""
    if not row.get("is_realtime"):
        delay = int(row.get("declared_delay_sec") or 0)
        label = f"TERTUNDA ~{delay // 60}m" if delay else "TERTUNDA"
    elif row.get("market_open") is False:
        label = "MARKET TUTUP"
    else:
        label = "REALTIME"

    quality = str(row.get("quality") or "OK")
    if quality != "OK":
        label += f" | DATA {quality}"
    return label


def market_overview(market_name: str, rows: list[dict], *, phase: int) -> str:
    """Latest stored snapshot per asset in one market."""
    if not rows:
        return truncate(
            "\n".join(
                [
                    header(market_name),
                    "",
                    "Belum ada data market yang tercatat.",
                    "",
                    "STATUS: SUMBER DATA TIDAK TERSEDIA",
                    "Jalankan:  python -m aruna fetch",
                ]
            )
        )

    lines = [header(market_name), "", timestamps(), ""]
    for row in rows:
        change = row.get("change_24h_pct")
        change_text = f"{float(change):+.2f}%" if change is not None else "n/a"
        lines.append(f"{row['symbol']:<10} {_price(row['last_price']):>18}  {change_text:>8}")

        detail = [_freshness_line(row)]
        if row.get("spread_bps") is not None:
            detail.append(f"spread {float(row['spread_bps']):.1f} bps")
        if row.get("session_code"):
            detail.append(str(row["session_code"]))
        lines.append(f"           {' | '.join(detail)}")
        captured = row.get("captured_at")
        if captured:
            lines.append(f"           per {wib(captured)}  via {row.get('source', '?')}")
        lines.append("")

    lines += [
        f"PHASE {phase}: data market saja.",
        "Belum ada analisis, belum ada council, belum ada signal.",
    ]
    return truncate("\n".join(lines))


def asset_detail(
    symbol: str, snapshot: dict | None, candles: list[dict], *, phase: int
) -> str:
    """One asset: latest snapshot plus a short recent-bar table."""
    if snapshot is None:
        return truncate(
            "\n".join(
                [
                    header(symbol),
                    "",
                    f"Tidak ada data tercatat untuk {symbol}.",
                    "",
                    "STATUS: SUMBER DATA TIDAK TERSEDIA",
                    f"Jalankan:  python -m aruna fetch --symbols {symbol}",
                ]
            )
        )

    lines = [header(symbol), "", timestamps(), ""]
    lines.append(f"TERAKHIR:  {_price(snapshot['last_price'])}")

    change = snapshot.get("change_24h_pct")
    if change is not None:
        lines.append(f"PERUBAHAN: {float(change):+.2f}%")
    if snapshot.get("bid") is not None and snapshot.get("ask") is not None:
        lines.append(
            f"BID/ASK:   {_price(snapshot['bid'])} / {_price(snapshot['ask'])}"
        )
    if snapshot.get("spread_bps") is not None:
        lines.append(f"SPREAD:    {float(snapshot['spread_bps']):.2f} bps")
    if snapshot.get("high_24h") is not None:
        lines.append(
            f"RANGE 24H: {_price(snapshot['low_24h'])} - {_price(snapshot['high_24h'])}"
        )
    if snapshot.get("volume_24h") is not None:
        lines.append(f"VOLUME:    {_money(snapshot['volume_24h'], 4)}")

    lines += [
        "",
        f"FEED:      {_freshness_line(snapshot)}",
        f"SUMBER:    {snapshot.get('source', '?')}",
        f"DIREKAM:   {wib(snapshot['captured_at'])}",
    ]
    if snapshot.get("session_code"):
        lines.append(f"SESI:      {snapshot['session_code']}")
    if snapshot.get("quality_detail"):
        lines.append(f"CATATAN:   {snapshot['quality_detail']}")

    if candles:
        lines += ["", "BAR TERAKHIR"]
        for row in candles[-8:]:
            lines.append(
                f"  {row['open_time']:%m-%d %H:%M}  "
                f"O {_price(row['open']):>14}  C {_price(row['close']):>14}"
            )

    lines += [
        "",
        f"PHASE {phase}: ini data market yang tercatat, bukan rekomendasi.",
        "Tidak ada analisis atau signal di build ini.",
    ]
    return truncate("\n".join(lines))


def council_report(rows: list[dict], *, phase: int) -> str:
    """Latest council verdict per asset (SPEC 14, 16, 18).

    Deliberately unlike the SPEC 21 signal block: no entry, no target, no
    predicted move. Those belong to a locked prediction, which does not exist
    until PHASE 7, and borrowing the signal layout here would imply one.
    """
    if not rows:
        return truncate(
            "\n".join(
                [
                    header("COUNCIL"),
                    "",
                    "Belum ada sesi council yang tercatat.",
                    "",
                    "Jalankan:  python -m aruna council",
                ]
            )
        )

    lines = [header("COUNCIL"), "", timestamps(), ""]
    for row in rows:
        lines.append(f"{row['symbol']}  {row['interval_code']}")
        lines.append(
            f"  KEPUTUSAN: {row['decision']}  "
            f"{float(row['confidence']) * 100:.0f}%"
        )
        lines.append(
            f"  AGEN:      {row['participating_agents']}/{row['total_agents']}"
            f"   ronde {row['rounds_run']}"
        )
        lines.append(
            f"  DEBAT:     {row['objection_count']} keberatan, "
            f"{row['correction_count']} diterima"
        )
        if row.get("minority_prevailed"):
            lines.append("  JUDGE:     MINORITAS MENANG atas dasar bobot bukti")
        lines.append(f"  RISIKO:    {row['risk_level']}")

        raised = int(row.get("veto_raised") or 0)
        upheld = int(row.get("veto_upheld") or 0)
        if raised:
            verdict = f"{upheld} dikuatkan" if upheld else "semua ditolak setelah ditinjau"
            lines.append(f"  VETO:      {raised} diajukan, {verdict}")

        reasons = row.get("no_trade_reasons") or []
        if reasons:
            lines.append(f"  DIBLOKIR:  {', '.join(reasons)}")
        lines.append(f"  PER:       {wib(row['as_of'])}")
        lines.append("")

    lines += [
        f"PHASE {phase}: putusan council bukan signal.",
        "Tidak ada entry, tidak ada target, tidak ada prediksi terkunci di sini -",
        "penguncian adalah langkah terpisah, dan langkah itu bisa menolak. Kirim",
        "/signals untuk melihat apa yang benar-benar terkunci.",
        "Judge menimbang bukti, tidak pernah menghitung kepala.",
    ]

    # Which judge factors lacked a measurement is decided per session and stored
    # per session, so it is read per session. This footer used to assert that
    # reliability and calibration were both unavailable no matter what the rows
    # said - true on day one, and quietly false from the first session that
    # cleared a sample threshold. A claim that cannot change cannot be right
    # twice.
    missing = _unavailable_factors(rows)
    if missing is None:
        lines.append(
            "Faktor judge mana yang tidak punya pengukuran di belakangnya tidak",
        )
        lines.append("dicatat untuk sesi-sesi ini.")
    elif missing:
        lines.append(
            f"Dipakai sebagai netral karena tidak ada pengukuran: {', '.join(missing)}."
        )
    else:
        lines.append("Setiap faktor judge punya pengukuran di belakangnya.")

    return truncate("\n".join(lines))


def futures_plans_report(
    rows: list[dict],
    counts: dict[str, int],
    outcomes: dict[str, int] | None = None,
    ghosts: dict[str, int] | None = None,
) -> str:
    """Recent perpetual plans (FUTURES SPEC 37-39, 48).

    The tally comes first and counts every verdict. A day of two plans and
    forty refusals is a day ARUNA mostly said no, and a report that opened with
    the two would describe a different system from the one that ran.
    """
    considered = sum(counts.values())
    lines = [
        header("FUTURES PLANS"),
        "",
        f"24 JAM TERAKHIR:  {considered} dipertimbangkan",
        f"  plan:      {counts.get('PLAN', 0)}",
        f"  ditolak:   {counts.get('REFUSED', 0)}",
        f"  menunggu:  {counts.get('WAIT', 0)}",
        f"  no signal: {counts.get('NO_SIGNAL', 0)}",
        "",
    ]

    if considered and not counts.get("PLAN"):
        lines += [
            "Tidak ada plan yang diterbitkan. Itu sebuah output, bukan",
            "kegagalan menghasilkan plan.",
            "",
        ]

    # Apa yang terjadi dengan plan-plan itu, bukan cuma bahwa plan-nya dibuat.
    # Melaporkan tally tanpa hasilnya menggambarkan sistem yang memutuskan dan
    # tidak pernah mencari tahu.
    resolved = sum((outcomes or {}).values())
    ghosted = sum((ghosts or {}).values())
    if resolved or ghosted:
        lines.append("SUDAH DINILAI:")
        if resolved:
            detail = ", ".join(f"{k} {v}" for k, v in sorted((outcomes or {}).items()))
            lines.append(f"  plan:  {resolved}  ({detail})")
        if ghosted:
            detail = ", ".join(f"{k} {v}" for k, v in sorted((ghosts or {}).items()))
            lines.append(f"  WAIT:  {ghosted}  ({detail})")
        if not resolved:
            lines.append("  belum ada plan yang resolve - tidak ada win rate")
        lines.append("")

    if not rows:
        lines.append("Belum ada yang direncanakan.")
        return truncate("\n".join(lines))

    for row in rows:
        verdict = str(row.get("verdict"))
        lines.append(f"{row.get('symbol')}  {row.get('side')}  {verdict}")
        if verdict == "PLAN":
            lines.append(
                f"  entry {row.get('entry')}  stop {row.get('stop')}  "
                f"target {row.get('target')}"
            )
            lines.append(
                f"  {row.get('leverage')}x {row.get('margin_mode')}  "
                f"liq {row.get('liquidation_price')}  "
                f"buffer {row.get('buffer_band')}"
            )
        else:
            # A refusal shows its reason, not just its label. "REFUSED" alone
            # is the part of the record a reader most needs explained.
            for reason in (row.get("refusals") or [])[:2]:
                lines.append(f"  - {reason}")
        lines.append(f"  {wib(row['created_at'])}")
        lines.append("")

    lines += [
        "Ini analisis, bukan instruksi. ARUNA tidak menempatkan order,",
        "tidak mengubah setting leverage atau margin, dan tidak memindahkan dana.",
    ]
    return truncate("\n".join(lines))


def _unavailable_factors(rows: list[dict]) -> list[str] | None:
    """Factors named unavailable by any session shown, or ``None`` if unknown.

    ``None`` and ``[]`` are different answers - "nobody recorded it" is not
    "nothing was missing" - so the absence of a judge row is never rendered as
    a clean bill of health.
    """
    seen: set[str] = set()
    known = False
    for row in rows:
        factors = row.get("unavailable_factors")
        if factors is None:
            continue
        known = True
        seen.update(factors)
    return sorted(seen) if known else None


def signals_report(rows: list[dict], *, phase: int) -> str:
    """Open locked predictions (SPEC 20, 21).

    Every line is a prediction made *before* its outcome was known. A signal
    with no target says so: the target comes from measured ATR, and when ATR was
    unavailable no number was invented (see ``aruna.signals.lock``).
    """
    if not rows:
        return truncate(
            "\n".join(
                [
                    header("SIGNALS"),
                    "",
                    "Tidak ada prediksi terpublikasi yang masih terbuka.",
                    "",
                    "ARUNA mungkin tetap mencatat verdict WAIT, dan mungkin",
                    "menolak mempublikasikan call yang buktinya terlalu tua atau",
                    "confidence-nya terlalu rendah. Kirim /today untuk melihatnya.",
                    "",
                    "Jalankan:  python -m aruna signal",
                ]
            )
        )

    lines = [header("SIGNALS"), "", timestamps(), ""]
    for row in rows:
        lines.append(
            f"{row['symbol']}  {row['horizon_code']}  "
            f"{row['direction']}  {float(row['confidence']) * 100:.0f}%"
        )
        lines.append(f"  ENTRY:    {_price(row['reference_price'])}")
        if row.get("target_price") is not None:
            move = row.get("expected_move_pct")
            move_text = f"  ({float(move):+.2f}%)" if move is not None else ""
            lines.append(f"  TARGET:   {_price(row['target_price'])}{move_text}")
        else:
            lines.append("  TARGET:   TIDAK TERSEDIA - ATR tidak bisa diukur")
        lines.append(f"  RESOLVE:  {wib(row['resolves_at'])}")
        lines.append(f"  ID:       {row['signal_id']}")
        lines.append("")

    lines += [
        f"PHASE {phase}: ini prediksi terkunci, bukan saran.",
        "PAPER TRADING SAJA - ARUNA tidak menempatkan order (SPEC 46).",
        "Prediksi terkunci tidak pernah diedit; pandangan yang berubah",
        "menggantikannya.",
    ]
    return truncate("\n".join(lines))


def today_report(rows: list[dict], performance: dict, *, phase: int) -> str:
    """Everything signalled in the window, with outcomes where they exist.

    Unresolved calls are listed as PENDING rather than omitted. A report that
    showed only scored signals would let the unscored ones disappear, which is
    exactly how a track record flatters itself.
    """
    if not rows:
        return truncate(
            "\n".join(
                [
                    header("HARI INI"),
                    "",
                    "Tidak ada prediksi yang terkunci dalam 24 jam terakhir.",
                    "",
                    "Jalankan:  python -m aruna signal",
                ]
            )
        )

    resolved = [r for r in rows if r.get("outcome_class")]
    # Only published calls can be right or wrong. Counting WAITs as failures
    # would punish the system for standing aside, and counting withheld calls
    # would score it on claims it refused to make.
    scored = [
        r
        for r in resolved
        if r["direction"] in ("BUY", "SELL") and r.get("published", True)
    ]
    correct = sum(1 for r in scored if r.get("direction_correct"))
    withheld = [r for r in rows if not r.get("published", True)
                and r["direction"] in ("BUY", "SELL")]

    lines = [header("HARI INI"), "", timestamps(), ""]
    for row in rows:
        lines.append(
            f"{row['symbol']}  {row['horizon_code']}  "
            f"{row['direction']}  {float(row['confidence']) * 100:.0f}%"
        )
        if not row.get("published", True) and row["direction"] in ("BUY", "SELL"):
            reason = row.get("withheld_reason") or "tidak dipublikasikan"
            lines.append(f"  DITAHAN:  {reason}")
        if row.get("outcome_class"):
            if row["direction"] in ("BUY", "SELL"):
                verdict = "BENAR" if row.get("direction_correct") else "SALAH"
            else:
                verdict = "TIDAK ADA POSISI"
            lines.append(
                f"  AKTUAL:   {float(row['actual_move_pct']):+.2f}%  {verdict}"
            )
            lines.append(f"  HASIL:    {row['outcome_class']}")
            if row.get("net_pnl") is not None:
                lines.append(
                    f"  NET PnL:  {_money(row['net_pnl'], 2)}  ({row['result']})"
                )
        else:
            lines.append(f"  PENDING - resolve {wib(row['resolves_at'])}")
        lines.append("")

    lines.append(f"TERCATAT: {len(rows)}")
    if withheld:
        lines.append(
            f"DITAHAN:  {len(withheld)} call berarah tidak dipublikasikan"
        )
    lines.append(f"RESOLVE:  {len(resolved)}  ({len(scored)} posisi terpublikasi)")
    if scored:
        lines.append(
            f"BENAR:    {correct}/{len(scored)} ({correct / len(scored) * 100:.0f}%)"
        )
    else:
        lines.append("BENAR:    n/a - belum ada call berarah yang resolve")

    trades = int(performance.get("trades") or 0)
    if trades:
        net = performance.get("net") or 0
        costs = performance.get("costs") or 0
        lines += [
            "",
            f"PAPER TRADES: {trades} ditutup",
            f"NET PnL:      {_money(net, 2)}  (setelah biaya {_money(costs, 2)})",
        ]

    lines += [
        "",
        f"PHASE {phase}: akurasi di atas adalah hitungan mentah, bukan",
        "probabilitas terkalibrasi. Kirim /performance untuk hasil calibration.",
        "PAPER TRADING SAJA - tidak pernah ada order yang ditempatkan (SPEC 46).",
    ]
    return truncate("\n".join(lines))


def performance_report(
    performance: dict,
    accuracy: dict,
    calibration: dict | None,
    *,
    phase: int,
    period: str = "sepanjang waktu",
    reliability: list[dict] | None = None,
) -> str:
    """Net performance and calibration (SPEC 29, 41).

    Leads with the sample size. Every figure below it is meaningless without
    one, and putting the win rate first invites a reader to stop there.
    """
    trades = int(performance.get("trades") or 0)
    resolved = int(accuracy.get("resolved") or 0)

    lines = [header(f"PERFORMANCE - {period.upper()}"), "", timestamps(), ""]

    if not resolved and not trades:
        lines += [
            "Belum ada yang resolve di periode ini.",
            "",
            "Tidak ada win rate, tidak ada akurasi dan tidak ada calibration",
            "yang ditampilkan, karena masing-masing akan dihitung dari nol",
            "observasi.",
            "",
            "Jalankan:  python -m aruna signal --resolve",
        ]
        return truncate("\n".join(lines))

    correct = int(accuracy.get("correct") or 0)
    lines.append(f"RESOLVE:    {resolved} prediksi berarah")
    if resolved:
        lines.append(
            f"BENAR:      {correct}/{resolved} "
            f"({correct / resolved * 100:.0f}%)  <- hitungan mentah"
        )

    if trades:
        wins = int(performance.get("wins") or 0)
        net = performance.get("net") or 0
        gross = performance.get("gross") or 0
        costs = performance.get("costs") or 0
        lines += [
            "",
            f"PAPER TRADES: {trades} ditutup, {wins} menang",
            f"NET PnL:      {_money(net, 2)}",
            f"GROSS:        {_money(gross, 2)}",
            f"BIAYA:        {_money(costs, 2)}",
        ]

    if calibration:
        lines += ["", "CALIBRATION (SPEC 29)", f"  {calibration.get('verdict', '')}"]
        for bucket in calibration.get("buckets", []):
            if bucket.get("accuracy") is not None:
                lines.append(
                    f"  {bucket['bucket']:<10} bilang "
                    f"{bucket['mean_confidence'] * 100:.0f}%, ternyata "
                    f"{bucket['accuracy'] * 100:.0f}%  (n={bucket['predictions']})"
                )
            else:
                lines.append(
                    f"  {bucket['bucket']:<10} n={bucket['predictions']}, "
                    f"butuh {bucket['needs']} lagi"
                )

    measured = [r for r in (reliability or []) if r.get("accuracy") is not None]
    if measured:
        lines += ["", "KEANDALAN AGEN (SPEC 30)"]
        for record in measured:
            lines.append(
                f"  {record['agent']:<12} {float(record['accuracy']) * 100:.0f}% "
                f"dari {record['scored_opinions']}  x{record['multiplier']}"
            )
    elif reliability:
        # Present but unmeasured is a different state from absent, and the
        # difference is the whole SPEC 30 discipline.
        lines += [
            "",
            f"KEANDALAN AGEN: {len(reliability)} agen dilacak, belum ada yang",
            "punya cukup opini terskor untuk mengubah bobotnya.",
        ]

    lines += [
        "",
        f"PHASE {phase}: akurasi di atas adalah hitungan, bukan probabilitas",
        "terkalibrasi. Sebuah band confidence baru melaporkan akurasinya",
        "setelah punya cukup prediksi untuk berarti.",
        "PAPER TRADING SAJA - tidak pernah ada order yang ditempatkan (SPEC 46).",
    ]
    return truncate("\n".join(lines))


def autopsy_report(
    autopsies: list[dict], ghosts: list[dict], *, phase: int
) -> str:
    """Losses and missed moves (SPEC 25, 28)."""
    if not autopsies and not ghosts:
        return truncate(
            "\n".join(
                [
                    header("AUTOPSY"),
                    "",
                    "Tidak ada prediksi rugi dan tidak ada move terlewat yang",
                    "tercatat.",
                    "",
                    "Itu bukan klaim akurasi - biasanya artinya belum ada yang",
                    "resolve.",
                    "",
                    "Jalankan:  python -m aruna autopsy",
                ]
            )
        )

    lines = [header("AUTOPSY"), "", timestamps(), ""]

    for row in autopsies[:5]:
        lines.append(
            f"{row['symbol']}  {row['horizon_code']}  {row['direction']}  "
            f"{float(row['confidence']) * 100:.0f}%"
        )
        lines.append(f"  HASIL:   {row['outcome_class']}")
        lines.append(
            f"  GERAK:   {float(row['actual_move_pct']):+.2f}% "
            f"(terburuk {float(row['max_adverse_pct']):+.2f}%)"
        )
        for finding in (row.get("findings") or [])[:3]:
            lines.append(f"  - {finding}")
        lines.append("")

    if ghosts:
        lines.append("GHOST SIGNAL - move yang kami lewatkan (SPEC 28)")
        for row in ghosts[:5]:
            lines.append(
                f"  {row['symbol']:<10} {row['horizon_code']:<4} "
                f"{float(row['missed_move_pct']):+.2f}%  "
                f"{row['direction']} akan menangkapnya"
            )
        lines.append("")

    lines += [
        f"PHASE {phase}: autopsy menjelaskan satu kerugian. Autopsy tidak",
        "mengubah bobot apa pun - itu butuh sampel, dan keandalan agen yang",
        "menanganinya.",
        "Move yang terlewat tidak otomatis kesalahan: bukti pada saat itu bisa",
        "saja tetap membenarkan keputusan untuk diam.",
    ]
    return truncate("\n".join(lines))


def research_report(questions: list[dict], drift: dict | None, *, phase: int) -> str:
    """Questions the record raises (SPEC 31)."""
    lines = [header("RESEARCH"), "", timestamps(), ""]

    if not questions:
        lines += [
            "Belum ada apa pun di catatan yang memunculkan pertanyaan.",
            "",
            "Itu biasanya karena kurangnya prediksi yang sudah resolve, bukan",
            "tanda semuanya sehat.",
        ]
    else:
        for row in questions[:6]:
            lines.append(f"[{float(row['severity']):.2f}] {row['question']}")
            for item in (row.get("evidence") or [])[:2]:
                lines.append(f"    - {item}")
            lines.append("")

    if drift:
        lines += ["DRIFT", f"  {drift.get('verdict', '')}", ""]

    lines += [
        f"PHASE {phase}: ini pertanyaan, bukan temuan.",
        "Tidak ada di sini yang mengubah cara ARUNA memutuskan. Perubahan butuh",
        "proposal tertulis, perbandingan tervalidasi, dan persetujuan manusia",
        "yang disebut namanya.",
    ]
    return truncate("\n".join(lines))


def proposals_report(
    proposals: list[dict], decisions: list[dict], *, phase: int
) -> str:
    """Model change proposals and who decided them (SPEC 44)."""
    if not proposals:
        return truncate(
            "\n".join(
                [
                    header("PROPOSALS"),
                    "",
                    "Tidak ada perubahan model yang diusulkan.",
                    "",
                    "Proposal ditulis oleh manusia berdasarkan sebuah pertanyaan",
                    "riset. ARUNA tidak menulis perubahan untuk dirinya sendiri.",
                ]
            )
        )

    lines = [header("PROPOSALS"), "", timestamps(), ""]
    for row in proposals[:6]:
        lines.append(f"{row['proposal_key']}  [{row['status']}]")
        lines.append(f"  {row['title']}")
        validation = row.get("validation")
        if validation:
            lines.append(f"  PUTUSAN: {validation['verdict']}")
            if not validation.get("supports_approval"):
                for reason in (validation.get("reasons") or [])[:1]:
                    lines.append(f"    {reason}")
        else:
            lines.append("  PUTUSAN: BELUM TERVALIDASI - tidak ada perbandingan tercatat")
        lines.append("")

    if decisions:
        lines.append("DIPUTUSKAN")
        for row in decisions[:4]:
            lines.append(
                f"  {row['decision']:<9} {row['proposal_key']} "
                f"oleh {row['decided_by']}"
            )
        lines.append("")

    lines += [
        f"PHASE {phase}: sebuah proposal baru aktif kalau ada orang yang namanya",
        "tercatat menyetujuinya dengan /approve <key>. Tidak ada threshold, skor",
        "atau setting yang bisa menyetujui atas nama ARUNA (SPEC 44).",
    ]
    return truncate("\n".join(lines))


def decision_recorded(key: str, decision: str, actor: str) -> str:
    return truncate(
        "\n".join(
            [
                f"ARUNA - PROPOSAL {decision}",
                "",
                f"{key} telah {decision.lower()} oleh {actor}.",
                "",
                "Keputusan ini masuk catatan dan tidak bisa diedit atau dihapus.",
                "Membatalkannya butuh proposal baru, jadi pembatalan itu ditinjau",
                "seperti perubahan lain.",
            ]
        )
    )


def unauthorized(chat_id: str) -> str:
    return truncate(
        "\n".join(
            [
                "ARUNA - TIDAK DIIZINKAN",
                "",
                f"Chat id {chat_id} tidak ada di allowlist.",
                "Percobaan ini sudah dicatat di audit log.",
                "",
                "Tambahkan id itu ke ARUNA_TELEGRAM_CHAT_ID atau",
                "ARUNA_TELEGRAM_ALLOWED_CHAT_IDS untuk memberi akses.",
            ]
        )
    )


def rate_limited(retry_after_sec: int) -> str:
    return (
        "ARUNA - RATE LIMITED\n\n"
        f"Terlalu banyak perintah. Coba lagi dalam {retry_after_sec}s."
    )


def usage_error(command: str, expected: str) -> str:
    return f"ARUNA - CARA PAKAI\n\n/{command} {expected}"


def _duration(seconds: float) -> str:
    total = int(seconds)
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _flatten(details: dict[str, object], prefix: str = "") -> list[tuple[str, str]]:
    """One level of nesting is enough for health details; deeper is noise."""
    out: list[tuple[str, str]] = []
    for key, value in details.items():
        label = f"{prefix}{key}"
        if isinstance(value, dict):
            inner = ", ".join(f"{k}={v}" for k, v in value.items())
            out.append((label, inner or "(kosong)"))
        elif isinstance(value, (list, tuple)):
            if not value:
                continue
            out.append((label, "; ".join(str(v) for v in value)))
        else:
            out.append((label, str(value)))
    return out


__all__ = [
    "MAX_MESSAGE_LEN",
    "header",
    "health_alert",
    "health_detail",
    "help_text",
    "kill_switch_engaged",
    "kill_switch_released",
    "rate_limited",
    "status_summary",
    "timestamps",
    "truncate",
    "unauthorized",
    "usage_error",
    "welcome",
]
