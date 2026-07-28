"""
Build a Unicode grid chart for Telegram live tracking messages.
"""
from __future__ import annotations
from datetime import datetime
from trading.grid_engine import GridSession

_BAR = 8          # bar width in characters
_MAX_LEVELS = 12  # max grid levels to display (6 above + 6 below price)


def _bar(filled: bool) -> str:
    return "🟦" * (_BAR // 2) if filled else "⬜" * (_BAR // 2)


def _position_bar(price: float, lower: float, upper: float) -> str:
    """Return a 10-char progress bar + % showing price position in range."""
    if upper <= lower:
        return "⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 50%"
    pct = max(0.0, min(1.0, (price - lower) / (upper - lower)))
    filled = round(pct * 10)
    bar = "🟦" * filled + "⬜" * (10 - filled)
    return f"{bar} {pct * 100:.0f}%"


def build_grid_chart(
    session: GridSession,
    price: float,
    pnl: float,
    pnl_pct: float,
    fills: int,
) -> str:
    cfg    = session.config
    levels = sorted(session.levels, key=lambda l: l.price, reverse=True)

    # ── Limit display to _MAX_LEVELS closest to current price ──────────────
    above = [l for l in levels if l.price > price]
    below = [l for l in levels if l.price <= price]
    half  = _MAX_LEVELS // 2
    # FIX: above مرتبة تنازلياً (الأعلى أولاً) — above[:half] يأخذ الأبعد عن السعر
    # الصحيح: نأخذ الـ half الأخيرة (الأقرب للسعر من فوق) ثم نعكسها للعرض
    display = list(reversed(above[-half:])) + below[:half]
    display = sorted(display, key=lambda l: l.price, reverse=True)

    # ── Header ─────────────────────────────────────────────────────────────
    pnl_icon = "🟢" if pnl >= 0 else "🔴"
    lines = [
        f"💎 <b>مراقبة حية: {cfg.symbol}</b>",
        "<code>─────────────────────────</code>",
    ]

    price_line_added = False
    for lvl in display:
        # Insert price marker line when we pass below it
        if not price_line_added and price >= lvl.price:
            lines.append(
                f"<code>─── ► {price:>10.4f} ◄ 📍 ───</code>"
            )
            price_line_added = True

        if lvl.status == "filled":
            icon = "✅"
            bar  = _bar(True)
        elif lvl.status in ("cancelled", "expired"):
            icon = "⬜"
            bar  = "·" * _BAR
        else:
            icon = "🟢" if lvl.side == "buy" else "🔴"
            bar  = _bar(False)

        side = "BUY " if lvl.side == "buy" else "SELL"
        lines.append(
            f"<code>{lvl.price:>12.4f}  {bar}  {side}</code>  {icon}"
        )

    if not price_line_added:
        lines.append(
            f"<code>─── ► {price:>10.4f} ◄ 📍 ───</code>"
        )

    # ── Position bar ───────────────────────────────────────────────────────
    pos = _position_bar(price, session.lower_price, session.upper_price)
    lines.append("<code>─────────────────────────</code>")
    lines.append(f"📍 <b>الوضع الحالي:</b>\n{pos}")
    lines.append(
        f"<code>🔻 {session.lower_price:.4f}</code>  ◀️  ▶️  <code>{session.upper_price:.4f} 🔺</code>"
    )

    # ── Stats ──────────────────────────────────────────────────────────────
    lines.append("<code>─────────────────────────</code>")
    lines.append(
        f"{pnl_icon} <b>الربح/الخسارة:</b> <code>{pnl:+.4f} USDT</code> "
        f"(<b>{pnl_pct:+.2f}%</b>)"
    )
    lines.append(
        f"🔄 <b>الصفقات:</b> <code>{fills}</code> "
        f"│ 💵 <b>المبلغ:</b> <code>{cfg.entry_amount:.2f}</code>"
    )
    if cfg.trailing_stop:
        lines.append(f"📈 Trailing Stop: <code>{cfg.trailing_pct}%</code>")
    now = datetime.now().strftime("%H:%M:%S")
    lines.append(f"⏱ <code>{now}</code>  •  🔄 يُحدَّث كل 30 ث")

    return "\n".join(lines)
