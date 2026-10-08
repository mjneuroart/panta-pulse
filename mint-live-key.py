#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Panta — mint clé pk_live (catalogue réel) + smoke test. Met à jour le coffre."""
import json, os, urllib.request, urllib.error

BASE = "https://live-api.panta.market/api/v1"
OUT = "/root/.hermes/secrets/panta-account.json"
st = json.load(open(OUT))


def req(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (JeasonStudio build)"}
    h.update(headers or {})
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()[:600]
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


if st.get("apiKeyLive"):
    print("clé live déjà présente:", st.get("keyLivePrefix"))
    key = st["apiKeyLive"]
else:
    code, tok = req("POST", "/auth/token/", {"email": st["email"], "password": st["password"]})
    print("login:", code, list(tok)[:6])
    access = tok.get("access") or tok.get("token") or ""
    code, k = req("POST", "/account/keys/", {"env": "live", "name": "jeason-live"}, {"Authorization": "Bearer " + access})
    print("key live:", code, {kk: k.get(kk) for kk in ("id", "prefix", "env", "status")} or k)
    if k.get("secret"):
        st["apiKeyLive"] = k["secret"]
        st["keyLivePrefix"] = k.get("prefix")
        st["keyLiveId"] = k.get("id")
        json.dump(st, open(OUT, "w"), indent=1)
        os.chmod(OUT, 0o600)
        print("coffre MAJ")
    key = st.get("apiKeyLive", "")

if key:
    code, mk = req("GET", "/markets/?limit=8", None, {"X-Api-Key": key})
    if isinstance(mk, dict):
        items = mk.get("items") or mk.get("results") or []
        print("markets live:", code, "n(first8)=", len(items))
        for m in items[:8]:
            print(" -", m.get("marketId", "?")[:18], "|", (m.get("title") or "")[:70], "|", m.get("category"), "|", m.get("phase"))
        if not items:
            print("(réponse brute:", json.dumps(mk)[:400], ")")
    else:
        print("markets:", code, str(mk)[:300])
