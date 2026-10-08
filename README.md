# Panta Pulse

**An AI market-intelligence desk for Panta prediction markets.**

Live catalog, trade-tape signals, LLM briefings, and a full market-creation
assistant — built on the Panta API for the [Panta API Sidetrack](https://superteam.fun/earn/listing/panta-api-side-track)
(Colosseum Crypto World's Fair).

*Powered by Panta.*

---

## What it does

- **Live catalog** — the full Panta USDC market catalog: search, category
  filters, four sort orders, live countdowns, YES prices, and volume.
- **Signals** — new markets (72 h), movers from the on-chain trade tape
  (24 h / 6 h / 1 h windows), markets ending soon, and per-category activity.
  A local state file keeps first-seen timestamps and volume deltas.
- **AI briefing** — a server-side LLM reads the live catalog and signals, then
  writes a market brief for the day.
- **Daily digest** — a scheduled job pushes the brief to Telegram.
- **Market-creation assistant** — the real Panta create flow, in the browser:
  **quote → build → sign → register**.

## The market-creation assistant (quote → build → register)

1. **Quote** — `POST /markets/create/quote/`. The desk validates the form and
   shows the live creation fee (USDC), the derived event PDA, and the session
   expiry.
2. **Build** — `POST /markets/create/build/`. Returns the unsigned versioned
   transaction, the recent blockhash, and a build fingerprint.
3. **Sign & broadcast** — done by the user's wallet. The desk never signs and
   never holds keys.
4. **Register** — `POST /markets/register/`. After the broadcast, the
   transaction signature completes the market on Panta.

## Panta API surface used

- `GET /markets/` — paginated catalog with category and phase filters.
- `GET /markets/{id}` and per-market trade tape.
- `POST /markets/create/quote/` · `POST /markets/create/build/` ·
  `POST /markets/register/` — the create flow.
- `GET /account/` — key status and `canCreateMarkets` check.
- Auth: `X-Api-Key`. The key stays server-side and never reaches the browser.

Design notes:

- The front renders public catalog data only. Every keyed call goes through
  the backend.
- Trade-tape deltas use a local snapshot state (first-seen + hourly buckets).
- The create proxy retries once on Panta's sporadic generic 400s
  ("unexpected create … failure") and surfaces every upstream error as-is.

## Stack

- **Backend** — Python + FastAPI, single service (`uvicorn`).
  See `app/backend/` (`main.py`, `create_flow.py`).
- **Frontend** — one static HTML file, vanilla JS, no build step.
  See `app/frontend/index.html`.
- **Ops** — systemd user service; Caddy TLS reverse proxy for the hosted
  deploy (`deploy/`).

## Run it locally

```bash
cd app
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PANTA_API_KEY=pk_live_… .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8090
```

Endpoints: `/` (desk) · `/api/markets` · `/api/signals` · `/api/briefing` ·
`/api/create/config|quote|build|register` · `/healthz`.

## Demo

- Video (headless capture): `docs/demo/panta-pulse-demo.mp4`
- Stills: `docs/evidence/`

## Disclaimer

Panta Pulse is an independent community project. It is not affiliated with
Panta. Market creation spends the user's own USDC and is signed by the user's
own wallet — the desk only quotes and builds unsigned transactions.
