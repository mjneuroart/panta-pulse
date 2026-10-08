#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Panta Pulse — formatte le brief quotidien pour livraison Telegram.

Usage: format_daily_brief.py '<briefing json>' ['<signals json>']
Sortie: markdown Telegram sur stdout (livrée verbatim par le cron no_agent).
"""
import json
import sys
from datetime import datetime, timezone


def clean_brief(text: str) -> str:
    out = []
    for line in (text or "").splitlines():
        s = line.rstrip()
        if s.startswith("# "):
            s = "**" + s[2:].strip() + "**"
        out.append(s)
    # collapse 3+ blank lines
    res, blanks = [], 0
    for line in out:
        if not line.strip():
            blanks += 1
            if blanks > 1:
                continue
        else:
            blanks = 0
        res.append(line)
    return "\n".join(res).strip()


def short(t: str, n: int = 80) -> str:
    t = (t or "").strip() or "(untitled market)"
    return t if len(t) <= n else t[: n - 1] + "…"


def fmt_left(sec: int) -> str:
    if sec is None:
        return "?"
    if sec >= 86400:
        return f"{sec // 86400}d {(sec % 86400) // 3600}h"
    if sec >= 3600:
        return f"{sec // 3600}h {(sec % 3600) // 60}m"
    return f"{max(sec, 0) // 60}m"


def main() -> None:
    brief = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    sig = {}
    if len(sys.argv) > 2 and sys.argv[2].strip():
        try:
            sig = json.loads(sys.argv[2])
        except Exception:
            sig = {}

    now = datetime.now(timezone.utc)
    lines = []
    lines.append(f"🛰️ **Panta Pulse — Daily Brief** · {now.strftime('%a %d %b %Y')}, {now.strftime('%H:%M')} UTC")
    meta = f"_AI desk · {brief.get('markets', '—')} markets scanned · {brief.get('model', 'deepseek')}_"
    lines.append(meta)
    lines.append("")
    lines.append(clean_brief(brief.get("brief", "(no brief generated this run)")))
    lines.append("")

    if sig:
        lines.append("**Signals**")
        nm = sig.get("newMarkets") or []
        if nm:
            lines.append(f"• 🆕 New: {short(nm[0].get('title',''))} · {nm[0].get('category','')}")
        mv = sig.get("movers") or []
        if mv:
            m = mv[0]
            vol = m.get("vol24h")
            voltxt = f"{vol:.0f} USDC traded 24h" if isinstance(vol, (int, float)) else "leading activity"
            lines.append(f"• 🔥 Mover: {short(m.get('title',''))} — {voltxt}")
        es = sig.get("endingSoon") or []
        if es:
            e = es[0]
            lines.append(f"• ⏳ Ends soon: {short(e.get('title',''))} — in {fmt_left(e.get('secondsLeft'))}")
        cats = sig.get("categories") or []
        if cats:
            top = ", ".join(f"{c.get('name')} {c.get('live', 0)}/{c.get('total', 0)}" for c in cats[:4])
            lines.append(f"• 📊 Pulse: live per category — {top}")
        lines.append("")

    lines.append(f"_Powered by Panta · local desk · {sig.get('liveCount', '—')} live / {sig.get('catalogSize', '—')} catalog_")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
