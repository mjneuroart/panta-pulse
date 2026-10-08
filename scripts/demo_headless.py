#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Panta Pulse — headless demo & E2E (Chrome --headless=new).

Modes:
  --check            functional E2E: load page, verify create-assistant card,
                     drive quote -> build through the real UI, assert results,
                     save screenshots. Exit 0 = pass.
  --video OUT.mp4    scripted ~40s screencast tour (CDP Page.startScreencast),
                     ends with quote -> build through the real UI, encodes to mp4
                     with ffmpeg.

Run with the app venv: app/.venv/bin/python scripts/demo_headless.py --check
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

APP = Path(__file__).resolve().parent.parent
SCRATCH = Path("/root/.hermes/cache/scratch/panta-s3")
BASE = "http://127.0.0.1:8090"
FFMPEG = shutil.which("ffmpeg") or "/root/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg"

# The recorded take uses a fresh (wallet, question) pair: Panta allows only one
# active creation per pair, so the question rotates between takes.
VIDEO_FIELDS = {
    "cfQuestion": os.environ.get("PANTA_DEMO_QUESTION", "Will ETH close above $5,000 on 2026-12-31?"),
    "cfTitle": os.environ.get("PANTA_DEMO_TITLE", "ETH above $5k on 2026-12-31"),
    "cfRule": "Resolves YES if the Ethereum USD price on CoinGecko is above $5,000 at 2026-12-31 23:59 UTC. Otherwise NO.",
    "cfSources": "https://www.coingecko.com/en/coins/ethereum",
    "cfImage": os.environ.get(
        "PANTA_DEMO_IMAGE",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/0/05/Ethereum_logo_2014.svg/1024px-Ethereum_logo_2014.svg.png",
    ),
}


def log(*a: object) -> None:
    print("[demo]", *a, flush=True)


class CDP:
    def __init__(self, ws) -> None:
        self.ws = ws
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self.recording = False
        self.frames = 0
        self.frames_dir: Path | None = None
        self.frame_bytes = 0
        self.capture = False
        self.net_bodies: list[dict] = []

    async def start(self) -> None:
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                if "id" in m and m["id"] in self._pending:
                    fut = self._pending.pop(m["id"])
                    if not fut.done():
                        fut.set_result(m)
                    continue
                if m.get("method") == "Page.screencastFrame":
                    p = m["params"]
                    if self.recording and self.frames_dir is not None:
                        data = base64.b64decode(p["data"])
                        (self.frames_dir / f"f{self.frames:05d}.jpg").write_bytes(data)
                        self.frames += 1
                        self.frame_bytes += len(data)
                    sid = p.get("sessionId")
                    if sid is not None:
                        asyncio.create_task(
                            self._fire("Page.screencastFrameAck", {"sessionId": sid})
                        )
                elif m.get("method") == "Network.responseReceived":
                    p = m["params"]
                    url = p.get("response", {}).get("url", "")
                    if self.capture and "/api/create/" in url:
                        asyncio.create_task(
                            self._grab_body(p["requestId"], url, p["response"].get("status"))
                        )
        except Exception:
            pass

    async def _grab_body(self, request_id: str, url: str, status) -> None:
        try:
            r = await self.call("Network.getResponseBody", {"requestId": request_id})
            self.net_bodies.append({"url": url, "status": status, "body": (r.get("body") or "")[:2500]})
        except Exception:
            self.net_bodies.append({"url": url, "status": status, "body": "(body unavailable)"})

    async def _fire(self, method: str, params: dict) -> None:
        try:
            await self.call(method, params)
        except Exception:
            pass

    async def call(self, method: str, params: dict | None = None, timeout: float = 30.0):
        self._id += 1
        i = self._id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[i] = fut
        await self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        m = await asyncio.wait_for(fut, timeout)
        if "error" in m:
            raise RuntimeError(f"{method}: {m['error'].get('message')}")
        return m.get("result", {})

    async def js(self, expr: str, await_promise: bool = False):
        r = await self.call(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": await_promise},
        )
        res = r.get("result", {})
        if res.get("subtype") == "error":
            raise RuntimeError(res.get("description", "js error"))
        return res.get("value")

    async def shot(self, path: Path) -> None:
        r = await self.call("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(r["data"]))


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    for x in b:
        if x == 0:
            s = "1" + s
        else:
            break
    return s or "1"


def demo_throwaway_wallet() -> str:
    """Random valid-looking Solana address for the off-camera warm-up."""
    while True:
        a = _b58(os.urandom(32))
        if 32 <= len(a) <= 44 and a[0] != "1":
            return a


def launch_chrome(user_data_dir: Path, w: int = 1280, h: int = 900):
    exe = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if not exe:
        raise RuntimeError("google-chrome not found")
    user_data_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe,
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--remote-debugging-port=0",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--window-size={w},{h}",
        "about:blank",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port_file = user_data_dir / "DevToolsActivePort"
    for _ in range(150):
        if port_file.exists():
            break
        time.sleep(0.1)
    if not port_file.exists():
        proc.kill()
        raise RuntimeError("chrome DevToolsActivePort not found")
    port = int(port_file.read_text().splitlines()[0])
    ws_url = None
    for _ in range(150):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                targets = json.load(r)
            for t in targets:
                if t.get("type") == "page":
                    ws_url = t["webSocketDebuggerUrl"]
                    break
        except Exception:
            pass
        if ws_url:
            break
        time.sleep(0.1)
    if not ws_url:
        proc.kill()
        raise RuntimeError("no page target")
    return proc, ws_url


async def wait_ready(c: CDP, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok = await c.js(
            "(document.readyState==='complete') && !!document.getElementById('grid') && "
            "(document.getElementById('status')?.textContent||'').indexOf('live')>=0"
        )
        if ok:
            # create card config loaded too
            cfg = await c.js("(document.getElementById('cfStatus')?.textContent||'').length>0")
            if cfg:
                return
        await asyncio.sleep(0.4)
    raise RuntimeError("page not ready in time")


async def fill_form(
    c: CDP,
    wallet: str,
    slow: bool = False,
    overrides: dict | None = None,
) -> None:
    demo = {
        "cfWallet": wallet,
        "cfQuestion": "Will BTC close above $150,000 on 2026-12-31?",
        "cfImage": "https://res.cloudinary.com/dyvupboym/image/upload/v1791389320/balr-market/events/usr_aXaCT3HLqoIed1mHO1_2lQ/09447a294b2e445693685b67d39e6c09.jpg",
        "cfRule": "Resolves YES if the Bitcoin USD price on CoinGecko is above $150,000 at 2026-12-31 23:59 UTC. Otherwise NO.",
        "cfSources": "https://www.coingecko.com/en/coins/bitcoin",
        "cfTitle": "BTC above $150k on 2026-12-31",
        "cfDesc": "Demo market quoted through the Panta Pulse assistant.",
    }
    if overrides:
        demo.update({k: v for k, v in overrides.items() if k in demo})
    for k, v in demo.items():
        await c.js(f"(()=>{{const e=document.getElementById('{k}'); if(e){{e.value={json.dumps(v)}; e.dispatchEvent(new Event('input',{{bubbles:true}}));}}}})()")
        if slow:
            await asyncio.sleep(0.35)


async def wait_for(c: CDP, cond_js: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if await c.js(cond_js):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.35)
    return False


async def wait_quote(c: CDP, timeout: float = 25.0) -> str:
    """Wait for the quote outcome: 'ok' | 'fail' | 'timeout' (fail-fast)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = await c.js("document.getElementById('cfReport').innerText||''")
        st = await c.js("document.getElementById('cfStatus').textContent||''")
        if "USDC" in t and "createId" in t:
            return "ok"
        if "failed" in st:
            return "fail"
        await asyncio.sleep(0.3)
    return "timeout"


async def wait_build(c: CDP, timeout: float = 25.0) -> str:
    """Wait for the build outcome: 'ok' | 'fail' | 'timeout' (fail-fast)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = (await c.js("document.getElementById('cfReport').innerText||''")).lower()
        st = await c.js("document.getElementById('cfStatus').textContent||''")
        if "fingerprint" in t and "transaction" in t:
            return "ok"
        if "failed" in st:
            return "fail"
        await asyncio.sleep(0.3)
    return "timeout"


async def drive_create_flow(c: CDP, wallet: str, fast: bool = True) -> dict:
    """Fill the assistant, click quote, click build. Returns a result dict."""
    await fill_form(c, wallet, slow=not fast)
    # scroll to card
    await c.js("document.getElementById('createCard').scrollIntoView({behavior:'instant',block:'start'})")
    await asyncio.sleep(0.4)

    out: dict = {"quote": None, "build": None}
    await c.js("document.getElementById('cfQuote').click()")
    for _ in range(50):
        txt = await c.js("document.getElementById('cfReport').innerText||''")
        st = await c.js("document.getElementById('cfStatus').textContent||''")
        if "USDC" in txt and "createId" in txt:
            out["quote"] = "ok"
            break
        if "failed" in st or "Error" in txt:
            out["quote"] = st or txt[:200]
            break
        await asyncio.sleep(0.4)
    if not fast:
        await asyncio.sleep(2.0)
    if out["quote"] == "ok":
        await c.js("document.getElementById('cfBuild').click()")
        for _ in range(50):
            txt = await c.js("document.getElementById('cfReport').innerText||''")
            st = await c.js("document.getElementById('cfStatus').textContent||''")
            if "Transaction" in txt and "fingerprint" in txt.lower():
                out["build"] = "ok"
                break
            if "failed" in st:
                out["build"] = st
                break
            await asyncio.sleep(0.4)
    out["report_text"] = (await c.js("document.getElementById('cfReport').innerText||''"))[:1200]
    out["status_text"] = await c.js("document.getElementById('cfStatus').textContent||''")
    return out


async def mode_check(wallet: str) -> int:
    udd = SCRATCH / "chrome-profile-check"
    shots = APP / "docs" / "evidence"
    shots.mkdir(parents=True, exist_ok=True)
    proc, ws_url = launch_chrome(udd)
    try:
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            c = CDP(ws)
            await c.start()
            await c.call("Page.enable")
            await c.call("Runtime.enable")
            await c.call("Page.navigate", {"url": BASE + "/"})
            await wait_ready(c)

            checks: dict = {}
            checks["title"] = await c.js("document.title")
            checks["cards"] = await c.js("document.querySelectorAll('.m').length")
            checks["signals_updated"] = await c.js("document.getElementById('sigHint').textContent")
            checks["create_status"] = await c.js("document.getElementById('cfStatus').textContent")
            checks["config_ok"] = "enabled" in (checks["create_status"] or "")
            await c.shot(shots / "s3-desk-top.png")

            res = await drive_create_flow(c, wallet)
            checks["quote"] = res["quote"]
            checks["build"] = res["build"]
            checks["status_after"] = res["status_text"]
            await c.js("document.getElementById('createCard').scrollIntoView({behavior:'instant',block:'start'})")
            await asyncio.sleep(0.5)
            await c.shot(shots / "s3-create-quote-build.png")
            await c.js("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)
            await c.shot(shots / "s3-desk-bottom.png")

            # password / key must never appear in the served page
            html = await c.js("document.documentElement.outerHTML.slice(0,200000)")
            checks["no_key_in_dom"] = ("pk_live" not in html) and ("pk_test" not in html)

            ok = (
                checks.get("quote") == "ok"
                and checks.get("build") == "ok"
                and checks.get("config_ok")
                and (checks.get("cards") or 0) > 10
                and checks.get("no_key_in_dom")
            )
            checks["PASS"] = bool(ok)
            log(json.dumps(checks, indent=1, ensure_ascii=False))
            return 0 if ok else 1
    finally:
        proc.kill()


async def caption(c: CDP, text: str) -> None:
    await c.js(
        "(()=>{const d=document.getElementById('demoCap'); if(!d) return;"
        "d.innerHTML='';"
        "const t=document.createElement('span'); t.textContent="
        + json.dumps(text)
        + ";"
        "const cur=document.createElement('span'); cur.textContent=' \\u258d';"
        "cur.style.animation='demoPulse 1.1s steps(2,start) infinite'; cur.style.color='#8b5cf6';"
        "d.appendChild(t); d.appendChild(cur);})()"
    )


async def smooth_scroll_to(c: CDP, y: int, seconds: float = 1.2) -> None:
    steps = max(6, int(seconds / 0.05))
    cur = await c.js("window.scrollY")
    target = y
    for i in range(1, steps + 1):
        yy = cur + (target - cur) * i / steps
        await c.js(f"window.scrollTo(0,{yy})")
        await asyncio.sleep(seconds / steps)


async def mode_video(wallet: str, out_path: Path) -> int:
    udd = SCRATCH / "chrome-profile-video"
    frames_dir = SCRATCH / "demo-frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    proc, ws_url = launch_chrome(udd)
    try:
        async with websockets.connect(ws_url, max_size=128 * 1024 * 1024) as ws:
            c = CDP(ws)
            await c.start()
            await c.call("Page.enable")
            await c.call("Runtime.enable")
            await c.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1280, "height": 860, "deviceScaleFactor": 1, "mobile": False},
            )
            await c.call("Page.navigate", {"url": BASE + "/"})
            await wait_ready(c)

            # --- warm-up off camera: absorb server-side flakes before recording ---
            # Uses a throwaway wallet + a different question so the recorded take
            # (Mike's wallet, BTC question) never collides with warm-up sessions.
            taw = demo_throwaway_wallet()
            await fill_form(c, taw, slow=False)
            q2 = "Will SOL close above $500 on 2026-12-31?"
            await c.js(
                f"(()=>{{const q=document.getElementById('cfQuestion'),t=document.getElementById('cfTitle');"
                f"if(q)q.value={json.dumps(q2)};if(t)t.value='SOL above 500 — warm-up';}})()"
            )
            warm = None
            for attempt in (1, 2, 3):
                await c.js("document.getElementById('cfQuote').click()")
                warm = await wait_quote(c, 25)
                if warm == "ok":
                    break
                log(f"warm-up quote {attempt}: {warm}")
                await asyncio.sleep(2.0)
            if warm != "ok":
                log("warm-up: quote never succeeded — aborting video")
                return 2
            log("warm-up quote ok (throwaway wallet)")

            # fresh page for the recorded take
            await c.call("Page.navigate", {"url": BASE + "/"})
            await wait_ready(c)

            # caption overlay + pulse animation (keeps the screencast streaming)
            await c.js(
                "(()=>{const s=document.createElement('style');s.id='demoAnim';"
                "s.textContent='@keyframes demoPulse{0%,49%{opacity:1}50%,100%{opacity:.12}}';"
                "document.head.appendChild(s);})()"
            )
            await c.js(
                "(()=>{const d=document.createElement('div');d.id='demoCap';"
                "d.style.cssText='position:fixed;left:50%;bottom:28px;transform:translateX(-50%);"
                "background:rgba(10,12,17,.88);border:1px solid #2c3350;color:#e8ecf4;padding:10px 20px;"
                "border-radius:12px;font:600 15px Inter,system-ui,sans-serif;z-index:99;max-width:82%;"
                "text-align:center;box-shadow:0 8px 30px rgba(0,0,0,.55)';document.body.appendChild(d);})()"
            )

            # start recording
            c.frames_dir = frames_dir
            c.recording = True
            t_start = time.time()
            await c.call(
                "Page.startScreencast",
                {"format": "jpeg", "quality": 70, "maxWidth": 1280, "maxHeight": 860, "everyNthFrame": 1},
            )

            await caption(c, "Panta Pulse — AI market desk for Panta prediction markets (live API)")
            await asyncio.sleep(4.0)
            await caption(c, "Live signals: new markets, movers from the trade tape, ending soon")
            await smooth_scroll_to(c, 620, 1.6)
            await asyncio.sleep(3.0)
            await caption(c, "Market creation assistant — the real Panta create flow")
            await smooth_scroll_to(c, 1180, 1.4)
            await asyncio.sleep(2.0)

            await caption(c, "Fill the market: wallet, question, category, rule, sources…")
            await fill_form(
                c,
                wallet,
                slow=True,
                overrides=VIDEO_FIELDS,
            )
            await asyncio.sleep(1.2)

            await caption(c, "1 · Quote — real USDC fee from the live Panta API…")
            rq = "fail"
            for attempt in (1, 2):
                await c.js("document.getElementById('cfQuote').click()")
                rq = await wait_quote(c, 25)
                if rq == "ok":
                    if attempt > 1:
                        c.recording = True
                    break
                log(f"on-camera quote {attempt}: {rq} | "
                    + (await c.js("document.getElementById('cfStatus').textContent||''")) + " | "
                    + (await c.js("document.getElementById('cfReport').innerText||''"))[:140].replace("\n", " "))
                c.recording = False  # pause capture during the retry (jump cut)
                await asyncio.sleep(2.5)
            if rq != "ok":
                c.recording = True
                log("video aborted: quote failed on camera")
                return 2
            # short glimpse of the fee, then build immediately: Panta's build is
            # most reliable right after the quote (short gap).
            await smooth_scroll_to(
                c,
                await c.js("document.getElementById('cfReport').getBoundingClientRect().top + window.scrollY - 140"),
                0.9,
            )
            await caption(c, "Quote → live USDC fee, session reserved…")
            await asyncio.sleep(1.6)

            await caption(c, "2 · Build — unsigned transaction…")
            rb2 = "fail"
            for attempt in (1, 2):
                if attempt > 1:
                    # fresh session for the retry, then build right away
                    await c.js("document.getElementById('cfQuote').click()")
                    await wait_quote(c, 25)
                await c.js("document.getElementById('cfBuild').click()")
                rb2 = await wait_build(c, 25)
                if rb2 == "ok":
                    if attempt > 1:
                        c.recording = True
                    break
                stb = await c.js("document.getElementById('cfStatus').textContent||''")
                rt = (await c.js("document.getElementById('cfReport').innerText||''"))[:180].replace("\n", " ")
                log(f"on-camera build {attempt}: {rb2} | {stb} | {rt}")
                c.recording = False
                await asyncio.sleep(2.0)
            if rb2 != "ok":
                c.recording = True
                log("video aborted: build failed on camera")
                return 2
            await smooth_scroll_to(
                c,
                await c.js("document.getElementById('cfReport').getBoundingClientRect().top + window.scrollY - 140"),
                1.0,
            )
            await caption(c, "Unsigned tx + fee + fingerprint — the wallet signs, then register verifies on-chain")
            await asyncio.sleep(4.5)

            await caption(c, "And the full live catalog stays one scroll away")
            await smooth_scroll_to(c, 99999, 1.8)
            await asyncio.sleep(2.2)
            await caption(c, "Panta Pulse — built for the Panta API Sidetrack · Powered by Panta")
            await asyncio.sleep(3.0)

            c.recording = False
            elapsed = max(1.0, time.time() - t_start)
            await c.call("Page.stopScreencast")
        log(f"frames: {c.frames} ({c.frame_bytes/1e6:.1f} MB) over {elapsed:.1f}s wall")
        if c.frames < 60:
            log("too few frames — abort")
            return 3
        fps = max(4.0, min(30.0, c.frames / elapsed))
        fps = round(fps * 4) / 4  # 0.25 step
        cmd = [
            FFMPEG, "-y", "-framerate", str(fps), "-i", str(frames_dir / "f%05d.jpg"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-crf", "23", str(out_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            log("ffmpeg failed:", r.stderr[-800:])
            return 4
        dur = c.frames / fps
        log(f"video: {out_path} · {dur:.1f}s @ {fps}fps")
        return 0
    finally:
        proc.kill()


async def mode_diag() -> int:
    """Browser-driven create flow with network capture; throwaway wallet.

    Prints every state + the raw /api/create/* responses. Exit 0 when the
    flow completes (quote ok + build ok).
    """
    udd = SCRATCH / "chrome-profile-diag"
    proc, ws_url = launch_chrome(udd)
    try:
        async with websockets.connect(ws_url, max_size=128 * 1024 * 1024) as ws:
            c = CDP(ws)
            await c.start()
            await c.call("Page.enable")
            await c.call("Runtime.enable")
            await c.call("Network.enable")
            c.capture = True
            await c.call("Page.navigate", {"url": BASE + "/"})
            await wait_ready(c)

            taw = demo_throwaway_wallet()
            overrides = {
                "cfQuestion": os.environ.get("PANTA_DIAG_QUESTION", "Will DOGE be above $0.50 on 2026-12-31?"),
                "cfTitle": "DOGE above $0.50 — diag",
                "cfRule": "Resolves YES if the Dogecoin USD price on CoinGecko is above $0.50 at 2026-12-31 23:59 UTC. Otherwise NO.",
                "cfSources": "https://www.coingecko.com/en/coins/dogecoin",
            }
            await fill_form(c, taw, slow=False, overrides=overrides)
            await c.js("document.getElementById('cfQuote').click()")
            rq = await wait_quote(c, 30)
            print("DIAG quote:", rq, flush=True)
            print("  status:", await c.js("document.getElementById('cfStatus').textContent"), flush=True)
            print("  report:", (await c.js("document.getElementById('cfReport').innerText") or "")[:400].replace("\n", " | "), flush=True)
            rb = None
            if rq == "ok":
                await asyncio.sleep(3)  # mimic the video quote->build gap
                await c.js("document.getElementById('cfBuild').click()")
                rb = await wait_build(c, 30)
                print("DIAG build:", rb, flush=True)
                print("  status:", await c.js("document.getElementById('cfStatus').textContent"), flush=True)
                print("  report:", (await c.js("document.getElementById('cfReport').innerText") or "")[:600].replace("\n", " | "), flush=True)
            await asyncio.sleep(1.0)
            print("DIAG network:", flush=True)
            for nb in c.net_bodies:
                path = nb["url"].split("/api/create/", 1)[-1]
                print(f"  [{nb['status']}] /api/create/{path} -> {nb['body'][:280]}", flush=True)
            return 0 if (rq == "ok" and rb == "ok") else 1
    finally:
        proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--diag", action="store_true")
    ap.add_argument("--video", metavar="OUT_MP4")
    args = ap.parse_args()
    wallet = json.load(open("/root/.hermes/secrets/mike-solana-wallet.json"))["address"]
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if args.check:
        return asyncio.run(mode_check(wallet))
    if args.diag:
        return asyncio.run(mode_diag())
    if args.video:
        return asyncio.run(mode_video(wallet, Path(args.video)))
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
