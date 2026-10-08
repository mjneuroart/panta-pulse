#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Panta Pulse — market creation assistant (S3).

Server-side proxy for the Panta create flow:
  1. quote    -> POST /markets/create/quote/    (validate + fee, reserves a session)
  2. build    -> POST /markets/create/build/    (unsigned VersionedTransaction)
  3. sign     -> done by the USER's wallet (this app never signs, never holds keys)
  4. register -> POST /markets/register/        (after the wallet broadcasts)

The API key stays server-side. No on-chain write happens server-side.

Built for the Panta API Sidetrack (Colosseum Crypto World's Fair).
"Powered by Panta" required on the product surface.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

HOME = Path("/root")
SECRETS = HOME / ".hermes" / "secrets"
PANTA_BASE = "https://live-api.panta.market/api/v1/"  # trailing slash required
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

CATEGORIES = [
    "sports",
    "crypto",
    "politics",
    "entertainment",
    "finance",
    "science",
    "world",
    "other",
]

_PATHS = {
    "account": ["account/", "whoami/"],
    "quote": "markets/create/quote/",
    "build": "markets/create/build/",
    "register": "markets/register/",
}

_WALLET_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

router = APIRouter(prefix="/api/create", tags=["create"])

# Tiny in-process rate guard (protect the live key from loops / abuse).
_rl: dict[str, list[float]] = {}


def _rate_ok(bucket: str, limit: int, window_s: float) -> bool:
    now = time.time()
    hits = [t for t in _rl.get(bucket, []) if now - t < window_s]
    if len(hits) >= limit:
        _rl[bucket] = hits
        return False
    hits.append(now)
    _rl[bucket] = hits
    return True


def _load_key() -> str | None:
    """Load the server-side Panta API key. Never sent to the client."""
    v = os.environ.get("PANTA_API_KEY")
    if v and v.strip():
        return v.strip()
    try:
        d = json.loads((SECRETS / "panta-account.json").read_text())
    except Exception:
        return None
    for field in ("apiKeyLive", "api_key_live", "liveKey", "apiKey", "api_key"):
        v = d.get(field)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _key_kind(key: str | None) -> str:
    if not key:
        return "missing"
    if key.startswith("pk_live"):
        return "live"
    if key.startswith("pk_test"):
        return "test"
    return "other"


def _dig(d: Any, key: str) -> Any:
    """Find a key in a dict, one nesting level deep (response shapes vary)."""
    if isinstance(d, dict):
        if key in d:
            return d[key]
        for v in d.values():
            if isinstance(v, dict):
                r = _dig(v, key)
                if r is not None:
                    return r
    return None


def _usdc(base: Any) -> str | None:
    """Base units (6 decimals) -> human-readable USDC string."""
    try:
        n = int(str(base))
    except Exception:
        return None
    return f"{n / 1_000_000:.2f}"


async def _panta(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    key = _load_key()
    if not key:
        return 500, {"code": "NO_API_KEY", "message": "server key missing (PANTA_API_KEY / vault)"}
    headers = {"X-Api-Key": key}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            if method == "GET":
                r = await client.get(PANTA_BASE + path, headers=headers)
            else:
                r = await client.post(PANTA_BASE + path, headers=headers, json=body or {})
    except httpx.HTTPError as exc:
        return 502, {"code": "UPSTREAM_UNREACHABLE", "message": str(exc)[:300]}
    try:
        data = r.json()
    except Exception:
        return 502, {"code": "BAD_UPSTREAM", "message": f"non-JSON reply ({r.status_code})"}
    if not isinstance(data, dict):
        data = {"data": data}
    return r.status_code, data


# The Panta create family returns sporadic generic 400s
# ("unexpected create ... failure — check server logs") that succeed on retry.
# One spaced retry absorbs that flake without changing semantics.
_FLAKE_MARK = "unexpected create"


async def _panta_create(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    st, data = await _panta(method, path, body)
    if st == 400 and _FLAKE_MARK in str(data.get("message", "")):
        await asyncio.sleep(1.5)
        st, data = await _panta(method, path, body)
    return st, data


def _validate_quote(p: dict) -> tuple[dict | None, dict | None]:
    """Validate + normalize the client form. Returns (payload, error_fields)."""
    err: dict[str, str] = {}

    wallet = str(p.get("wallet", "")).strip()
    if not _WALLET_RE.fullmatch(wallet):
        err["wallet"] = "base58 Solana address required (32-44 chars)"

    question = str(p.get("question", "")).strip()
    if not question or len(question) > 512:
        err["question"] = "1..512 chars"

    rule = str(p.get("resolutionRule", "")).strip()
    if not rule or len(rule) > 2048:
        err["resolutionRule"] = "1..2048 chars"

    if not (str(p.get("title", "")).strip() or str(p.get("description", "")).strip()):
        err["title"] = "title or description required (server-side requirement)"

    src = p.get("sourcesOfTruth")
    src_clean: list[str] | None = None
    if isinstance(src, str):
        src = [s.strip() for s in src.splitlines() if s.strip()]
    if (
        isinstance(src, list)
        and 1 <= len(src) <= 20
        and all(isinstance(s, str) and s.strip() for s in src)
    ):
        src_clean = [str(s).strip() for s in src]
    else:
        err["sourcesOfTruth"] = "1..20 non-empty strings"

    category = str(p.get("category", "")).strip().lower()
    if category not in CATEGORIES:
        err["category"] = f"one of: {', '.join(CATEGORIES)}"

    start = end = res = 0
    try:
        start, end, res = int(p["startTime"]), int(p["endTime"]), int(p["resolutionTime"])
        if not (start < end <= res):
            err["times"] = "startTime < endTime <= resolutionTime required"
    except Exception:
        err["times"] = "unix seconds required"

    image = str(p.get("imageUrl", "")).strip()
    if not (image.startswith("http://") or image.startswith("https://")) or len(image) > 2048:
        err["imageUrl"] = "public http(s) URL required (1024x1024 recommended)"

    if err:
        return None, err

    out: dict[str, Any] = {
        "wallet": wallet,
        "question": question,
        "resolutionRule": rule,
        "sourcesOfTruth": src_clean or [],
        "category": category,
        "startTime": start,
        "endTime": end,
        "resolutionTime": res,
        "imageUrl": image,
    }
    for k, maxlen in (("title", 200), ("description", 2000), ("region", 64), ("oracle", 256)):
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:maxlen]
    mt = str(p.get("marketType", "standard")).strip().lower()
    if mt in ("standard", "breaking"):
        out["marketType"] = mt
        if mt == "breaking" and p.get("eventInProgress") is True:
            out["eventInProgress"] = True
    return out, None


@router.get("/config")
async def create_config() -> dict:
    """Desk-side facts about the create flow (no secrets, no account dump)."""
    st, acct = 0, {}
    for path in _PATHS["account"]:
        st, acct = await _panta("GET", path)
        if st == 200:
            break
    return {
        "apiEnv": _key_kind(_load_key()),
        "accountStatus": st,
        "canCreateMarkets": _dig(acct, "canCreateMarkets") if st == 200 else None,
        "categories": CATEGORIES,
        "minStartDelaySec": 3600,
        "sessionTtlHintSec": 300,
        "flow": ["quote", "build", "sign + broadcast (wallet)", "register"],
    }


@router.post("/quote")
async def create_quote(payload: dict) -> JSONResponse:
    if not _rate_ok("quote", 20, 600):
        return JSONResponse(
            status_code=429,
            content={"code": "RATE_LIMITED", "message": "too many quotes from this desk, retry in a few minutes"},
        )
    p, err = _validate_quote(payload)
    if err is not None:
        return JSONResponse(status_code=400, content={"code": "INVALID_MARKET_PARAMS", "fields": err})
    assert p is not None  # guarantee: no error <=> payload present

    st, data = await _panta_create("POST", _PATHS["quote"], body=p)
    print(f"[create] quote -> {st} {data.get('code', 'ok')}", flush=True)
    out = dict(data)
    if st == 200:
        out["feeUsdc"] = _usdc(data.get("paymentUsdc"))
        out["liquidityUsdc"] = _usdc(data.get("liquidityInjectionUsdc"))
        out["platformUsdc"] = _usdc(data.get("platformRevenueUsdc"))
        out["wallet"] = p["wallet"]
        out["question"] = p["question"]
    return JSONResponse(status_code=st, content=out)


@router.post("/build")
async def create_build(payload: dict) -> JSONResponse:
    if not _rate_ok("build", 20, 600):
        return JSONResponse(status_code=429, content={"code": "RATE_LIMITED", "message": "too many builds, retry later"})
    create_id = str(payload.get("createId", "")).strip()
    if not create_id:
        return JSONResponse(
            status_code=400,
            content={"code": "INVALID_MARKET_PARAMS", "fields": {"createId": "required"}},
        )
    body: dict[str, Any] = {"createId": create_id}
    w = str(payload.get("wallet", "")).strip()
    if w:
        body["wallet"] = w

    st, data = await _panta_create("POST", _PATHS["build"], body=body)
    print(f"[create] build {create_id[:14]}… -> {st} {data.get('code', 'ok')}", flush=True)
    out = dict(data)
    if st == 200:
        out["feeUsdc"] = _usdc(data.get("paymentUsdc"))
        out["liquidityUsdc"] = _usdc(data.get("liquidityInjectionUsdc"))
        out["platformUsdc"] = _usdc(data.get("platformRevenueUsdc"))
    return JSONResponse(status_code=st, content=out)


@router.post("/register")
async def create_register(payload: dict) -> JSONResponse:
    if not _rate_ok("register", 20, 600):
        return JSONResponse(status_code=429, content={"code": "RATE_LIMITED", "message": "too many registrations, retry later"})
    create_id = str(payload.get("createId", "")).strip()
    signature = str(payload.get("signature", "")).strip()
    fields: dict[str, str] = {}
    if not create_id:
        fields["createId"] = "required"
    if not signature or len(signature) < 40:
        fields["signature"] = "base58 transaction signature required"
    if fields:
        return JSONResponse(status_code=400, content={"code": "INVALID_MARKET_PARAMS", "fields": fields})

    st, data = await _panta_create("POST", _PATHS["register"], body={"createId": create_id, "signature": signature})
    print(f"[create] register {create_id[:14]}… -> {st} {data.get('code', 'ok')}", flush=True)
    return JSONResponse(status_code=st, content=dict(data))
