#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Panta Pulse — backend (FastAPI).

Endpoints:
- GET /api/markets  : full live Panta catalog (paginated, cached 60s). Key stays server-side.
- GET /api/signals  : market signals — new markets, movers (trade-tape powered), ending soon,
                      category pulse. Local state keeps first-seen + snapshots for deltas.
- GET /api/briefing : AI briefing (DeepSeek) over live markets + signals (cached 10 min).
- GET /             : static front.

Built for the Panta API Sidetrack (Colosseum Crypto World's Fair).
"Powered by Panta" required on the product surface.
"""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from backend.create_flow import router as create_flow_router

HOME = Path("/root")
SECRETS = HOME / ".hermes" / "secrets"
APP = Path(__file__).resolve().parent.parent
DATA = APP / "data"
DATA.mkdir(exist_ok=True)
STATE_FILE = DATA / "state.json"

PANTA_BASE = "https://live-api.panta.market/api/v1"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

VERSION = "0.3.0"
TAPE_TTL = 300          # per-market trade-tape cache
MARKETS_TTL = 60        # catalog cache
SIGNALS_TTL = 120       # signals cache
BRIEF_TTL = 600         # briefing cache
STALE_MAX = 1800        # serve somewhat-stale + refresh in background up to 30 min; older = blocking refetch
SNAP_MIN_GAP = 1800     # rotate volume snapshot at most every 30 min
SNAP_KEEP = 48          # keep ~24h of snapshots
NEW_WINDOW = 72 * 3600  # "new market" window
LIVE_LIMIT_TAPE = 20    # max trade-tape calls per signals pass


async def _warm_bg() -> None:
    """Pré-chauffe les caches au démarrage (best effort)."""
    try:
        await _fetch_markets(force=True)
    except Exception:
        pass
    try:
        await _compute_signals(force=False)
    except Exception:
        pass


@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    asyncio.create_task(_warm_bg())
    yield


app = FastAPI(title="Panta Pulse", version=VERSION, lifespan=_lifespan)

LIVE_STATUSES = {"primary", "primary_active", "secondary", "secondary_active", "open", "live"}
DEAD_STATUSES = {"resolved", "voided", "cancelled", "canceled"}


# ---------------------------------------------------------------- secrets
def _panta_key() -> str:
    d = json.loads((SECRETS / "panta-account.json").read_text())
    return d.get("apiKeyLive") or d["apiKey"]


def _deepseek_key() -> str:
    p = HOME / ".hermes" / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("DEEPSEEK_API_KEY", "")


# ------------------------------------------------------------------ state
_state: Optional[dict] = None


def _load_state() -> dict:
    global _state
    if _state is None:
        if STATE_FILE.exists():
            try:
                _state = json.loads(STATE_FILE.read_text())
            except Exception:
                _state = None
        if not isinstance(_state, dict):
            _state = {}
    _state.setdefault("firstSeen", {})
    _state.setdefault("snapshots", [])
    _state.setdefault("tapes", {})
    return _state


def _save_state() -> None:
    st = _load_state()
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(STATE_FILE)


# ------------------------------------------------------------ panta helpers
def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return str(v)


def _fnum(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ts(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _norm(m: dict) -> dict:
    """Normalize a raw Panta catalog row. Volume fields are flaky upstream → parse defensively."""
    vol = _fnum(m.get("totalVolumeUsdc"))
    if vol is None:
        vol = _fnum(m.get("volumeUsdc"))
    px = _fnum(m.get("yesPrice"))
    if px is None or not (0.0 < px <= 1.0):
        px = _fnum(m.get("primaryYesPrice"))
        if px is not None and not (0.0 < px <= 1.0):
            px = None
    return {
        "id": m.get("marketId"),
        "title": m.get("title") or "",
        "category": m.get("category") or "other",
        "phase": m.get("phase") or "",
        "status": m.get("status") or m.get("phase") or "",
        "marketType": m.get("marketType") or "",
        "resolved": bool(m.get("resolved")),
        "startTs": _parse_ts(m.get("startTime")),
        "endTs": _parse_ts(m.get("endTime")),
        "startTime": _iso(m.get("startTime")),
        "endTime": _iso(m.get("endTime")),
        "volume": vol,
        "yesPrice": px,
        "image": (m.get("images") or [None])[0],
    }


def _is_live(m: dict) -> bool:
    if m.get("resolved"):
        return False
    s = (m.get("status") or "").lower()
    if s in DEAD_STATUSES:
        return False
    if s in LIVE_STATUSES:
        return True
    return (m.get("phase") or "").lower() in ("primary", "secondary")


_markets_cache: dict[str, Any] = {"at": 0.0, "data": None}
_refreshing: dict[str, bool] = {"markets": False, "signals": False, "brief": False}


async def _bg_refresh(key: str, fn) -> None:
    """Relance un calcul en arrière-plan (dédupliqué par clé) — pattern stale-while-revalidate."""
    if _refreshing.get(key):
        return
    _refreshing[key] = True
    try:
        await fn()
    except Exception:
        pass
    finally:
        _refreshing[key] = False


async def _fetch_markets(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _markets_cache["data"] is not None and now - _markets_cache["at"] < MARKETS_TTL:
        return _markets_cache["data"]
    headers = {"X-Api-Key": _panta_key(), "User-Agent": "PantaPulse/0.2"}
    items: list[dict] = []
    cursor = None
    async with httpx.AsyncClient(timeout=40) as c:
        for _ in range(3):  # up to 3 pages x 100
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            data = None
            last_err: Optional[Exception] = None
            for attempt in range(2):  # une reprise par page (l'API amont est parfois lente/saccadée)
                try:
                    r = await c.get(f"{PANTA_BASE}/markets/", params=params, headers=headers)
                    r.raise_for_status()
                    data = r.json()
                    break
                except Exception as e:
                    last_err = e
                    if attempt == 0:
                        await asyncio.sleep(0.8)
            if data is None:
                raise last_err  # type: ignore[misc]
            items += data.get("items") or []
            cursor = data.get("nextCursor")
            if not cursor:
                break
    out = [_norm(m) for m in items]
    # dedupe by id, keep order
    seen, dedup = set(), []
    for m in out:
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"])
            dedup.append(m)
    _markets_cache.update({"at": now, "data": dedup})
    return dedup


# -------------------------------------------------------------- trade tape
async def _tape_stats(client: httpx.AsyncClient, mid: str) -> Optional[dict]:
    """Aggregate the public trade tape for one market (cached TAPE_TTL)."""
    st = _load_state()
    now = time.time()
    cached = st["tapes"].get(mid)
    if cached and now - cached.get("at", 0) < TAPE_TTL:
        return cached

    headers = {"X-Api-Key": _panta_key(), "User-Agent": "PantaPulse/0.2"}
    try:
        r = await client.get(f"{PANTA_BASE}/markets/{mid}/trades/", params={"limit": 200}, headers=headers)
        r.raise_for_status()
        trades = r.json().get("items") or []
    except Exception:
        return cached  # degrade to stale cache if any

    day_ago, six_ago, hour_ago = now - 86400, now - 21600, now - 3600
    vol24 = vol6 = vol1 = 0.0
    n24 = 0
    wallets: set[str] = set()
    last_ts = None
    for t in trades:
        bt = _parse_ts(t.get("blockTime"))
        amt = _fnum(t.get("amountUsdc")) or 0.0
        if bt is None:
            continue
        if last_ts is None or bt > last_ts:
            last_ts = bt
        if bt >= day_ago:
            vol24 += amt
            n24 += 1
            if t.get("wallet"):
                wallets.add(t["wallet"])
        if bt >= six_ago:
            vol6 += amt
        if bt >= hour_ago:
            vol1 += amt

    rec = {
        "at": now,
        "vol24h": round(vol24, 2),
        "vol6h": round(vol6, 2),
        "vol1h": round(vol1, 2),
        "trades24h": n24,
        "wallets24h": len(wallets),
        "lastTs": last_ts,
    }
    st["tapes"][mid] = rec
    # prune tapes not touched in 7 days
    for k in [k for k, v in st["tapes"].items() if now - v.get("at", 0) > 7 * 86400]:
        st["tapes"].pop(k, None)
    return rec


# ----------------------------------------------------------------- signals
_sig_cache: dict[str, Any] = {"at": 0.0, "data": None}
_brief_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _short(m: dict, limit: int = 90) -> str:
    t = m.get("title") or "(untitled market)"
    return t if len(t) <= limit else t[: limit - 1] + "…"


async def _compute_signals(force: bool = False) -> dict:
    now = time.time()
    if not force and _sig_cache["data"] is not None and now - _sig_cache["at"] < SIGNALS_TTL:
        return _sig_cache["data"]

    items = await _fetch_markets(force=force)
    st = _load_state()
    fs = st["firstSeen"]
    fresh_seed = len(st["snapshots"]) == 0 and len(fs) == 0  # very first pass ever

    for m in items:
        mid = m["id"]
        if mid not in fs:
            if fresh_seed and m.get("startTs") and m["startTs"] <= now:
                fs[mid] = int(m["startTs"])
            else:
                fs[mid] = int(now)

    # snapshot rotation for volume deltas
    snaps = st["snapshots"]
    if not snaps or now - snaps[-1]["at"] >= SNAP_MIN_GAP:
        snaps.append({
            "at": int(now),
            "vol": {m["id"]: m["volume"] for m in items if m["volume"] is not None},
        })
        if len(snaps) > SNAP_KEEP:
            del snaps[:-SNAP_KEEP]
    base = snaps[0] if snaps else None
    deltas: dict[str, float] = {}
    if base and base.get("vol"):
        cut = base["at"]
        for m in items:
            if m["volume"] is None:
                continue
            v0 = base["vol"].get(m["id"])
            if v0 is not None and m["volume"] > v0:
                deltas[m["id"]] = round(m["volume"] - v0, 2)

    live = [m for m in items if _is_live(m)]

    tape: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=25) as c:
        for m in live[:LIVE_LIMIT_TAPE]:
            t = await _tape_stats(c, m["id"])
            if t:
                tape[m["id"]] = t

    # ---- new markets (first seen ≤ 72h, not resolved)
    new_markets = []
    for m in items:
        if m.get("resolved"):
            continue
        seen_at = fs.get(m["id"], now)
        if now - seen_at <= NEW_WINDOW:
            new_markets.append({**m, "firstSeen": seen_at})
    new_markets.sort(key=lambda m: (m.get("startTs") or 0, m["firstSeen"]), reverse=True)
    new_markets = new_markets[:8]

    # ---- movers: tape 24h volume first, catalog volume delta as fallback
    mover_rows = []
    for m in live:
        t = tape.get(m["id"]) or {}
        score = max(t.get("vol24h") or 0.0, deltas.get(m["id"], 0.0))
        mover_rows.append({
            "id": m["id"],
            "title": m["title"],
            "category": m["category"],
            "phase": m["phase"],
            "status": m["status"],
            "volume": m["volume"],
            "vol24h": t.get("vol24h"),
            "vol6h": t.get("vol6h"),
            "vol1h": t.get("vol1h"),
            "trades24h": t.get("trades24h"),
            "wallets24h": t.get("wallets24h"),
            "deltaSinceBoot": deltas.get(m["id"]),
            "score": round(score, 2),
            "endTs": m["endTs"],
        })
    mover_rows.sort(key=lambda r: r["score"], reverse=True)
    movers = [r for r in mover_rows if r["score"] > 0][:8]
    if not movers:
        fallback = sorted((m for m in live if m["volume"]), key=lambda m: m["volume"], reverse=True)[:6]
        movers = [{
            "id": m["id"], "title": m["title"], "category": m["category"], "phase": m["phase"],
            "status": m["status"], "volume": m["volume"], "vol24h": None, "vol6h": None, "vol1h": None,
            "trades24h": None, "wallets24h": None, "deltaSinceBoot": None,
            "score": round(m["volume"] or 0.0, 2), "endTs": m["endTs"], "basis": "volume",
        } for m in fallback]

    # ---- ending soon (live, still open, soonest first)
    ending = [m for m in live if m.get("endTs") and m["endTs"] > now]
    ending.sort(key=lambda m: m["endTs"])
    ending_soon = [{
        "id": m["id"], "title": m["title"], "category": m["category"], "phase": m["phase"],
        "status": m["status"], "endTs": m["endTs"], "endTime": m["endTime"],
        "secondsLeft": int(m["endTs"] - now), "volume": m["volume"], "image": m["image"],
    } for m in ending[:8]]

    # ---- category pulse
    cats: dict[str, dict] = {}
    for m in items:
        cat = cats.setdefault(m["category"], {"name": m["category"], "total": 0, "live": 0, "volume": 0.0, "vol24h": 0.0})
        cat["total"] += 1
        if _is_live(m):
            cat["live"] += 1
        if m["volume"]:
            cat["volume"] += m["volume"]
    for mid_, t in tape.items():
        for m in items:
            if m["id"] == mid_:
                cats[m["category"]]["vol24h"] += t.get("vol24h") or 0.0
                break
    cat_list = sorted(cats.values(), key=lambda c: (c["live"], c["vol24h"], c["volume"]), reverse=True)
    for c in cat_list:
        c["volume"] = round(c["volume"], 2)
        c["vol24h"] = round(c["vol24h"], 2)

    resp = {
        "generatedAt": int(now),
        "catalogSize": len(items),
        "liveCount": len(live),
        "newMarkets": new_markets,
        "movers": movers,
        "endingSoon": ending_soon,
        "categories": cat_list,
        "basis": "trade-tape 24h vol (fallback: catalog volume delta since boot)",
    }
    _sig_cache.update({"at": now, "data": resp})
    _save_state()
    return resp


@app.get("/api/signals")
async def signals(force: int = 0):
    cached = _sig_cache["data"]
    if not force and cached is not None:
        age = time.time() - _sig_cache["at"]
        if age < SIGNALS_TTL:
            return cached
        if age < STALE_MAX:
            asyncio.create_task(_bg_refresh("signals", lambda: _compute_signals(force=False)))
            return cached
    try:
        return await _compute_signals(force=bool(force))
    except Exception as e:
        if cached is not None:
            return cached
        return JSONResponse(status_code=502, content={"error": f"signals unavailable: {e}"})


@app.get("/api/markets")
async def markets(force: int = 0) -> dict:
    cached = _markets_cache["data"]
    if not force and cached is not None:
        age = time.time() - _markets_cache["at"]
        if age < MARKETS_TTL:
            items = cached
        elif age < STALE_MAX:
            asyncio.create_task(_bg_refresh("markets", lambda: _fetch_markets(force=True)))
            items = cached
        else:
            items = await _fetch_markets(force=True)
    else:
        items = await _fetch_markets(force=True)
    return {
        "count": len(items),
        "liveCount": sum(1 for m in items if _is_live(m)),
        "items": items,
        "fetchedAt": int(time.time()),
    }


async def _generate_brief() -> dict:
    now = time.time()
    try:
        sig = await _compute_signals()
    except Exception:
        sig = None
    items = await _fetch_markets()

    lines = []
    for m in items[:40]:
        t = m["title"] or "(untitled — image-only market)"
        lines.append(f'- [{m["category"]} / {m["status"]}] {t[:110]}')
    prompt = (
        "You are the analyst of Panta Pulse, a market-intelligence desk for Panta prediction markets (Solana). "
        "Snapshot of live markets right now:\n" + "\n".join(lines) + "\n"
    )
    if sig:
        s = ["", "SIGNALS (computed from the public trade tape + catalog):"]
        if sig["newMarkets"]:
            s.append("- Newest markets: " + "; ".join(_short(m, 70) for m in sig["newMarkets"][:4]))
        if sig["movers"]:
            s.append("- 24h activity leaders: " + "; ".join(
                f'{_short(m, 60)} ({m["vol24h"] or 0:.0f} USDC / {m["trades24h"] or 0} trades)' for m in sig["movers"][:4]))
        if sig["endingSoon"]:
            s.append("- Ending soonest: " + "; ".join(
                f'{_short(m, 60)} (in {m["secondsLeft"]//3600}h)' for m in sig["endingSoon"][:4]))
        if sig["categories"]:
            s.append("- Category pulse: " + ", ".join(
                f'{c["name"]} live {c["live"]}/{c["total"]}' for c in sig["categories"][:5]))
        prompt += "\n".join(s) + "\n"
    prompt += (
        "\nWrite a punchy intelligence brief in English (markdown, max 6 bullets, 130-190 words):\n"
        "- what stands out across categories (sports, crypto, pop-culture...) and why;\n"
        "- the 2-3 most interesting markets and the question they're really asking;\n"
        "- one trend to watch over the next days.\n"
        "Concrete, no financial advice, no filler."
    )
    body = {"model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 600}
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(DEEPSEEK_URL, json=body, headers={"Authorization": "Bearer " + _deepseek_key()})
        r.raise_for_status()
        d = r.json()
    text = d["choices"][0]["message"]["content"]
    data = {"brief": text, "model": d.get("model"), "markets": len(items), "generatedAt": int(time.time())}
    _brief_cache.update({"at": now, "data": data})
    return data


@app.get("/api/briefing")
async def briefing(force: int = 0) -> dict:
    cached = _brief_cache["data"]
    if not force and cached is not None:
        age = time.time() - _brief_cache["at"]
        if age < BRIEF_TTL:
            return cached
        if age < 6 * 3600:
            asyncio.create_task(_bg_refresh("brief", _generate_brief))
            return cached
    return await _generate_brief()


app.include_router(create_flow_router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "ts": int(time.time()), "version": VERSION}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(APP / "frontend" / "index.html")


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    return FileResponse(APP / "frontend" / "favicon.svg")
