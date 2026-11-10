# Hoody Setup Scanner

A local (or self-hosted) website that scans **Bitunix USDT perpetual futures** for setups in the style of the public *Hoody Trader* journal. It is a research tool, **not financial advice** and not a copy-trade feed.

The main board is a **Hoody discovery** ranking — names that look like the coins he actually traded (high-beta, usually not mega-majors, often newer or narrative-hot, with volume and a ~15–30% swing band). Rules 1–8 (structure / SMC-style confluence) are scored and shown, but they only count for 30% of the board sort. BTC and other majors sit in a market-context panel, not at the top of the board.

The universe is **crypto only**. Tokenized stocks, pre-IPO names, ETFs, indices, commodities (including XAU/XAG/PAXG/XAUT) and FX-style listings are excluded.

## Open it

```bash
# from the repository root (Dockerfile, backend/, frontend/)
chmod +x run.sh
./run.sh
```

Then open [http://127.0.0.1:8788/](http://127.0.0.1:8788/). The first live scan takes about 1–3 minutes.

On a phone or tablet, open the URL and use **Add to Home Screen** (the app ships a PWA manifest and icons). Last-updated times are in **Asia/Qatar**.

To listen on all interfaces (port-forward or a host):

```bash
HOST=0.0.0.0 PORT=8788 ./run.sh
```

Needs Python 3.10+ and internet access to Bitunix, CoinGecko, Google News, and the Forex Factory calendar JSON. No API keys and no exchange login.

## How discovery and the score work

- **Discovery (0–100, 70% of board rank)** — not a mega-major, volatility in his ~20% move band, tradeable volume and volume spikes, listing freshness, 7-day relative strength vs BTC, prior compression, distance from the 30-day high, and a light narrative/catalyst tag.
- **Setup score (0–100, 30% of board rank)** — the eight numbered rulebook buckets: R1 bias+POI+liquidity+invalidation (28), R2 breakout and retest (16), R3 order-block reaction (10), R4 range/volume (12), R5 BTC/funding/news/macro (12), R6 plan and R:R (8), R7 do not chase (6), R8 MTF agreement (8).
- **State:** confirmed, trigger, watch, or stretched (do not chase).

Levels and scores are automated. Order-block quality, whether a headline matters, and whether you should enter still need judgement.

## Deploying

This package does **not** create cloud accounts in your name. No Bitunix API key is required.

1. **This machine / your Mac or PC:** `./run.sh` then http://127.0.0.1:8788/. Add to Home Screen on iPhone/iPad from Safari.
2. **SSH tunnel:** `ssh -L 8788:127.0.0.1:8788 user@host`.
3. **Temporary public URL without signing up:** `cloudflared tunnel --url http://127.0.0.1:8788` (needs outbound access to Cloudflare’s tunnel ports; this box could not reach them). Treat the `*.trycloudflare.com` link as private — there is no login on the app.
4. **Render:** needs *your* Render account. Use the included `Dockerfile` + `render.yaml` (Blueprint) or a Docker web service, health check `/api/health`. Render’s always-on free web tier is generally not available anymore; budget a starter instance. Set nothing except the injected `PORT`.
5. **Railway:** needs *your* Railway account. `railway.toml` + `Dockerfile`. Railway injects `PORT`.
6. **Fly.io or a $5 VPS:** same Docker image, `PORT` env, outbound HTTPS.
7. **Vercel / serverless** cannot run the 10-minute scheduler as-is.

## Disclaimer

Not financial advice. Crypto perpetual futures are highly risky. Past journal results are not a track record you can expect to repeat.
