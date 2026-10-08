# Panta Pulse — submission text (EN)

Ready to paste into Colosseum + Superteam Earn. Replace `[DEMO_URL]`, `[REPO_URL]`, `[VIDEO_URL]` before sending.

---

## Name

Panta Pulse

## Tagline (one line)

An AI market-intelligence desk for Panta prediction markets — live signals, LLM briefings, and a real quote→build→register creation assistant.

## Short description (~50 words)

Panta Pulse turns the Panta catalog into a working desk: live discovery with search and filters, on-chain trade-tape signals, daily AI briefings pushed to Telegram, and a market-creation assistant that runs the real Panta create flow (quote → build → sign → register) from the browser. The API key stays server-side; the user's wallet signs.

## Long description (~300 words)

Prediction markets are usually experienced as a destination website. Panta Pulse explores what they look like as infrastructure. It is an AI market-intelligence desk that sits on top of the Panta API:

- **Discovery and monitoring.** The desk renders the full USDC catalog with search, category filters, four sort orders, and live countdowns. A signal engine reads the per-market trade tape to surface movers (24 h / 6 h / 1 h), new markets, and markets ending soon, and keeps a local state file for first-seen timestamps and volume deltas.
- **AI briefing and delivery.** A server-side LLM reads the live catalog and signals and writes a daily market brief. A scheduled job pushes the brief to Telegram, so the desk reaches users instead of waiting for them to visit.
- **Creation assistant.** The full Panta create flow runs in the browser: **quote** (live USDC fee, derived event PDA, session expiry), **build** (unsigned versioned transaction + blockhash + fingerprint), **sign and broadcast in the user's wallet**, then **register**. The desk never holds keys and never signs; every write step belongs to the user's wallet.

Under the hood: a FastAPI backend, one static front (no build step), catalog caching, trade-tape delta state, and a resilient proxy that absorbs Panta's sporadic transient 400s while surfacing every upstream error as-is. All keyed calls stay server-side; the browser never sees the API key.

The project is built to keep going after the hackathon: a desk that watches Panta markets daily is useful on its own, and the creation assistant is the wedge for creators and communities who want to launch their own markets without touching the low-level transaction plumbing.

## How the Panta API is integrated (for judges)

- `GET /markets/` (paginated, filtered) + per-market data + trade tape → the catalog, the signal engine, and volume deltas.
- `POST /markets/create/quote/` + `POST /markets/create/build/` + `POST /markets/register/` → the creation assistant (full flow, verified end-to-end by execution).
- `GET /account/` → key status and `canCreateMarkets`, shown in the assistant.
- Auth via `X-Api-Key`, server-side only.
- "Powered by Panta" on the product surface (footer + assistant).

## Links

- Demo: [DEMO_URL]
- Repo: [REPO_URL]
- Video: [VIDEO_URL]

## Team

Mike-Jeason Leca — solo builder. Jeason Studio, Paris (AI-native production studio).

## Traction

[À compléter avant envoi : visiteurs de la démo, retours, partages — toute preuve d'usage.]

---

## Video script (for reference, ~70 s)

1. "Panta Pulse — AI market desk for Panta prediction markets" (live catalog).
2. "Live signals: new markets, movers from the trade tape, ending soon."
3. "Market creation assistant — the real Panta create flow."
4. Fill the market form (wallet, question, category, rule, sources).
5. "1 · Quote — real USDC fee from the live Panta API."
6. "2 · Build — unsigned transaction; the wallet signs, then register verifies on-chain."
7. "Built for the Panta API Sidetrack · Powered by Panta."


## Liens publics (08/10/2026)
- App live : https://panta.168-231-106-101.sslip.io
- Vidéo démo (33 s) : https://panta.168-231-106-101.sslip.io/demo/panta-pulse-demo.mp4
- Code : https://github.com/mjneuroart/panta-pulse
