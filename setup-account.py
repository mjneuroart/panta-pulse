#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Panta — setup compte dev (studio) + clé API test + smoke test markets.
Idempotent : réutilise /root/.hermes/secrets/panta-account.json si présent.
Aucun secret ne doit être affiché dans la sortie (masques uniquement)."""
import json, os, secrets as pysecrets, sys, urllib.request, urllib.error, time

BASE = "https://live-api.panta.market/api/v1"
EMAIL = "contact@jeasonstudio.com"
NAME = "Jeason Studio"
OUT = "/root/.hermes/secrets/panta-account.json"


def req(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (JeasonStudio build)"}
    h.update(headers or {})
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()[:600]
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}
    except Exception as e:
        return -1, {"err": str(e)[:300]}


def mask(s):
    s = s or ""
    return (s[:14] + "…" + str(len(s)) + "ch") if s else ""


st = {}
if os.path.exists(OUT):
    st = json.load(open(OUT))
    print("== compte déjà présent ==", st.get("userId"), st.get("keyPrefix"))
else:
    pw = pysecrets.token_hex(24)
    code, d = req("POST", "/auth/register/", {"email": EMAIL, "password": pw, "name": NAME})
    print("register:", code, {k: d.get(k) for k in ("userId", "email", "name") if k in d} or d)
    if code in (200, 201) and d.get("access"):
        st = {"email": EMAIL, "password": pw, "userId": d.get("userId"), "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    elif d.get("code") == "EMAIL_TAKEN":
        print("EMAIL_TAKEN — pas de mot de passe local, arrêt.")
        sys.exit(3)
    else:
        sys.exit(4)

    code, k = req("POST", "/account/keys/", {"env": "test", "name": "jeason-dev"}, {"Authorization": "Bearer " + d.get("access", "")})
    print("key:", code, {kk: k.get(kk) for kk in ("id", "prefix", "env", "status")} or k)
    if code in (200, 201) and k.get("secret"):
        st["apiKey"] = k["secret"]
        st["keyId"] = k.get("id")
        st["keyPrefix"] = k.get("prefix")
    else:
        print("Création de clé KO — on garde le compte.", json.dumps(d)[:200])

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    json.dump(st, f, indent=1)
os.chmod(OUT, 0o600)
print("secrets ->", OUT, "chmod 600")

key = st.get("apiKey")
if key:
    code, me = req("GET", "/account/", None, {"X-Api-Key": key})
    print("whoami:", code, {k: me.get(k) for k in ("userId", "email", "name", "status", "canCreateMarkets")})
    code, mk = req("GET", "/markets/?limit=5", None, {"X-Api-Key": key})
    if isinstance(mk, dict) and "results" in mk:
        rs = mk["results"]
        print("markets:", code, "count_5=", len(rs), "|", [(m.get("title") or m.get("question") or m.get("id")) for m in rs][:5])
    else:
        print("markets:", code, json.dumps(mk)[:300])
