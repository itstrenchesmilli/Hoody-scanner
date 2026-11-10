"""Hoody setup scanner — FastAPI app, scheduled Bitunix scans, static frontend."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .analysis import analyze_symbol, apply_fundamentals
from .bitunix import Bitunix, BitunixError
from .discovery import attach_discovery, extract_features, score_discovery
from .fundamentals import Fundamentals
from .universe import classify_symbols, fetch_coingecko_coins, is_context_major

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
CACHE_FILE = DATA / "last_scan.json"

MIN_QUOTE_VOL = 1_500_000  # USDT
MAX_PAIRS = 140
SCAN_EVERY_MIN = 10
QATAR = dt.timezone(dt.timedelta(hours=3))

app = FastAPI(title="Hoody Setup Scanner", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = asyncio.Lock()
_state: dict[str, Any] = {
    "status": "starting",
    "last_scan_utc": None,
    "last_scan_qatar": None,
    "next_scan_utc": None,
    "error": None,
    "pairs_considered": 0,
    "pairs_scanned": 0,
    "setups": [],
    "btc": None,
    "macro": {},
    "scan_seconds": None,
    "excluded": [],
    "context_majors": [],
    "changes": {},
    "crypto_only": True,
    "pairs_excluded": 0,
}
_tickers: dict[str, dict] = {}
_klines: dict[str, dict] = {}  # symbol -> {d1,h4,h1,fetched}
_bitunix: Bitunix | None = None
_fund: Fundamentals | None = None
_scheduler: AsyncIOScheduler | None = None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _qatar(ts: dt.datetime | None) -> str | None:
    if not ts:
        return None
    return ts.astimezone(QATAR).strftime("%d %b %Y, %H:%M:%S") + " Asia/Qatar"


async def _client() -> Bitunix:
    global _bitunix
    if _bitunix is None:
        _bitunix = Bitunix()
    return _bitunix


async def _funds() -> Fundamentals:
    global _fund
    if _fund is None:
        _fund = Fundamentals()
    return _fund


def _load_cache() -> None:
    if not CACHE_FILE.exists():
        return
    try:
        js = json.loads(CACHE_FILE.read_text())
        _state["setups"] = js.get("setups") or []
        _state["btc"] = js.get("btc")
        _state["macro"] = js.get("macro") or {}
        _state["last_scan_utc"] = js.get("last_scan_utc")
        _state["last_scan_qatar"] = js.get("last_scan_qatar")
        _state["pairs_scanned"] = js.get("pairs_scanned") or 0
        _state["pairs_considered"] = js.get("pairs_considered") or 0
        _state["excluded"] = js.get("excluded") or []
        _state["context_majors"] = js.get("context_majors") or []
        _state["changes"] = js.get("changes") or {}
        _state["pairs_excluded"] = js.get("pairs_excluded") or 0
        _state["status"] = "cached"
    except Exception:
        pass


def _save_cache() -> None:
    payload = {
        "setups": _state["setups"],
        "btc": _state["btc"],
        "macro": _state["macro"],
        "last_scan_utc": _state["last_scan_utc"],
        "last_scan_qatar": _state["last_scan_qatar"],
        "pairs_scanned": _state["pairs_scanned"],
        "pairs_considered": _state["pairs_considered"],
        "pairs_excluded": _state.get("pairs_excluded") or 0,
        "excluded": _state.get("excluded") or [],
        "context_majors": _state.get("context_majors") or [],
        "changes": _state.get("changes") or {},
        "crypto_only": True,
    }
    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, default=str))


async def run_scan() -> None:
    async with _lock:
        t0 = time.time()
        _state["status"] = "scanning"
        _state["error"] = None
        bx = await _client()
        fund = await _funds()
        try:
            tickers, funding_rows, pairs = await asyncio.gather(
                bx.tickers(),
                bx.funding_batch(),
                bx.trading_pairs(),
            )
        except BitunixError as e:
            _state["status"] = "error"
            _state["error"] = str(e)
            return

        funding_map = {f.get("symbol"): f for f in funding_rows or []}
        previous_setups = {r.get("symbol"): r for r in (_state.get("setups") or []) if r.get("symbol")}
        pair_by_sym = {p.get("symbol"): p for p in (pairs or []) if p.get("symbol")}
        open_usdt = [
            p.get("symbol")
            for p in (pairs or [])
            if (p.get("symbolStatus") or "OPEN") == "OPEN" and (p.get("quote") or "USDT") == "USDT" and p.get("symbol")
        ]
        try:
            cg_coins = await fetch_coingecko_coins(fund._http)
        except Exception:
            cg_coins = []
        classified = classify_symbols(open_usdt, cg_coins)
        open_syms = set(classified["kept"])
        _state["excluded"] = classified["excluded"]
        _state["pairs_excluded"] = len(classified["excluded"])
        universe = []
        by_sym = {}
        for t in tickers or []:
            sym = t.get("symbol") or ""
            if not sym.endswith("USDT"):
                continue
            if open_syms and sym not in open_syms:
                continue
            q = float(t.get("quoteVol") or 0)
            by_sym[sym] = t
            universe.append((q, sym, t))
        universe.sort(reverse=True)
        _tickers.update({s: t for _, s, t in universe})

        # Always include BTC/ETH; then liquid names
        chosen: list[str] = []
        for must in ("BTCUSDT", "ETHUSDT"):
            if must in by_sym and must not in chosen:
                chosen.append(must)
        for q, sym, t in universe:
            if sym in chosen:
                continue
            if q < MIN_QUOTE_VOL:
                continue
            chosen.append(sym)
            if len(chosen) >= MAX_PAIRS:
                break

        _state["pairs_considered"] = len(universe)
        _state["pairs_scanned"] = len(chosen)

        # klines
        async def fetch_tf(sym: str) -> tuple[str, dict | str]:
            try:
                d1, h4, h1 = await asyncio.gather(
                    bx.klines(sym, "1d", 200),
                    bx.klines(sym, "4h", 200),
                    bx.klines(sym, "1h", 200),
                )
                pack = {"d1": d1, "h4": h4, "h1": h1, "fetched": time.time()}
                _klines[sym] = pack
                return sym, pack
            except Exception as e:
                return sym, f"err:{e}"

        packs: dict[str, dict] = {}
        # modest batches so we stay polite
        for i in range(0, len(chosen), 12):
            chunk = chosen[i : i + 12]
            results = await asyncio.gather(*[fetch_tf(s) for s in chunk])
            for sym, pack in results:
                if isinstance(pack, dict):
                    packs[sym] = pack

        btc_pack = packs.get("BTCUSDT") or {}
        from .analysis import structure_bias, swings, pct_range

        btc_bias = "unclear"
        btc_ch = None
        if btc_pack.get("d1"):
            sh, sl = swings(btc_pack["d1"], 2, 2)
            btc_bias = structure_bias(sh, sl)
        bt = by_sym.get("BTCUSDT") or {}
        try:
            last = float(bt.get("lastPrice") or bt.get("last") or 0)
            o = float(bt.get("open") or 0)
            if o:
                btc_ch = (last - o) / o * 100
        except (TypeError, ValueError):
            pass
        if btc_bias == "unclear" and btc_pack.get("h4"):
            sh, sl = swings(btc_pack["h4"], 3, 3)
            btc_bias = structure_bias(sh, sl)

        _state["btc"] = {
            "symbol": "BTCUSDT",
            "price": _f(bt.get("lastPrice") or bt.get("last")),
            "change_24h_pct": btc_ch,
            "bias": btc_bias,
            "range_7d_pct": pct_range(btc_pack.get("d1") or [], 7) if btc_pack else None,
            "funding_rate": _f((funding_map.get("BTCUSDT") or {}).get("fundingRate")),
        }

        # fundamentals preload in parallel with analysis
        async def preload():
            try:
                await fund.preload_markets()
            except Exception:
                pass
            try:
                await fund.load_macro()
            except Exception:
                pass

        preload_task = asyncio.create_task(preload())

        setups = []
        for sym in chosen:
            pack = packs.get(sym)
            if not pack:
                continue
            try:
                s = analyze_symbol(
                    symbol=sym,
                    ticker=by_sym[sym],
                    funding=funding_map.get(sym),
                    d1=pack.get("d1") or [],
                    h4=pack.get("h4") or [],
                    h1=pack.get("h1") or [],
                    btc_bias=btc_bias,
                    btc_change_24h=btc_ch,
                )
            except Exception:
                continue
            if s:
                setups.append(s)

        await preload_task
        macro = fund.macro()
        _state["macro"] = macro

        # Enrich top names (by preliminary score) with FA
        setups.sort(key=lambda s: s.score, reverse=True)
        top = setups[:48]
        # categories only for the very top to respect CoinGecko rate limits
        for i, s in enumerate(top):
            try:
                fa = await fund.enrich(s.coin, macro)
                if i < 18:
                    fa = await fund.attach_categories(fa)
                    await asyncio.sleep(0.25)
                apply_fundamentals(s, fa)
            except Exception:
                apply_fundamentals(s, {"found": False, "news": [], "macro": {"stance": macro.get("stance"), "next_high": macro.get("next_high")}})

        # Hoody discovery rank — this drives the main board, not raw setup score.
        btc_d1 = (btc_pack or {}).get("d1") or []
        rows = []
        for s in setups:
            pack = packs.get(s.symbol) or {}
            fa = s.fa if hasattr(s, "fa") and s.fa else {}
            feat = extract_features(
                coin=s.coin,
                d1=pack.get("d1") or [],
                h4=pack.get("h4") or [],
                ticker=by_sym.get(s.symbol) or {},
                btc_d1=btc_d1,
                launch_ms=_f((pair_by_sym.get(s.symbol) or {}).get("launchTime")),
                mcap_rank=(fa or {}).get("market_cap_rank"),
                market_cap=(fa or {}).get("market_cap"),
                categories=(fa or {}).get("categories") or [],
                has_unlock_headline=bool((fa or {}).get("unlock")),
                has_catalyst_headline=bool((fa or {}).get("news")),
                quote_vol_24h=s.quote_vol_24h,
            )
            dscore, buckets, dnotes = score_discovery(feat)
            row = s.to_dict()
            if fa:
                row.setdefault("fa", fa)
            attach_discovery(row, feat, dscore, buckets, dnotes)
            rows.append(row)

        def _flags(row, prev):
            flags = []
            if (row.get("discovery_score") or 0) >= 68 and row.get("state") in ("confirmed", "trigger"):
                flags.append({"id": "alert", "label": "Alert-worthy: high discovery + trigger/confirmed"})
            if row.get("state") == "stretched":
                flags.append({"id": "chase", "label": "Stretched — do not chase"})
            fr = row.get("funding_rate")
            if fr is not None and abs(fr) >= 0.2:
                flags.append({"id": "funding", "label": "Extreme funding (cap / dislocation)"})
            if row.get("btc_aligned") is False:
                flags.append({"id": "btc", "label": "BTC context fights this direction"})
            if row.get("reward_risk") is not None and row["reward_risk"] < 1.2:
                flags.append({"id": "rr", "label": "Tight R:R to TP1"})
            if prev is None:
                flags.append({"id": "new", "label": "New on the board this scan"})
            else:
                ddisc = (row.get("discovery_score") or 0) - (prev.get("discovery_score") or 0)
                dsetup = (row.get("score") or 0) - (prev.get("score") or 0)
                row["discovery_delta"] = ddisc
                row["score_delta"] = dsetup
                if prev.get("state") and prev.get("state") != row.get("state"):
                    flags.append({"id": "state", "label": f"State changed {prev.get('state')} → {row.get('state')}"})
                if ddisc >= 8 or dsetup >= 8:
                    flags.append({"id": "rising", "label": "Rank improved since last scan"})
            row["flags"] = flags
            row["is_new"] = prev is None
            return row

        context = []
        board = []
        for row in rows:
            prev = previous_setups.get(row["symbol"])
            _flags(row, prev)
            base = row.get("coin") or ""
            if is_context_major(base):
                context.append(row)
            else:
                board.append(row)

        board.sort(key=lambda r: (r.get("board_rank") or 0, r.get("discovery_score") or 0, r.get("score") or 0), reverse=True)
        context.sort(key=lambda r: (r.get("score") or 0), reverse=True)

        kept = [r for r in board if (r.get("discovery_score") or 0) >= 40 or r.get("state") in ("confirmed", "trigger") or (r.get("score") or 0) >= 50]
        if len(kept) < 18:
            kept = board[:30]
        prev_syms = set(previous_setups)
        now_syms = {r["symbol"] for r in kept}
        dropped = []
        for sym, old in previous_setups.items():
            if sym not in now_syms and not is_context_major(old.get("coin") or ""):
                dropped.append({"symbol": sym, "coin": old.get("coin"), "score": old.get("score"), "discovery_score": old.get("discovery_score"), "state": old.get("state"), "direction": old.get("direction")})
        _state["changes"] = {
            "new": [r["symbol"] for r in kept if r.get("is_new")],
            "dropped": dropped[:40],
            "state_changes": [
                {"symbol": r["symbol"], "from": (previous_setups.get(r["symbol"]) or {}).get("state"), "to": r.get("state")}
                for r in kept
                if previous_setups.get(r["symbol"]) and previous_setups[r["symbol"]].get("state") != r.get("state")
            ],
        }
        _state["context_majors"] = context
        _state["setups"] = kept
        now = _now()
        _state["last_scan_utc"] = now.isoformat()
        _state["last_scan_qatar"] = _qatar(now)
        _state["scan_seconds"] = round(time.time() - t0, 1)
        _state["status"] = "ready"
        _save_cache()


def _f(x):
    try:
        return float(x) if x is not None and x != "" else None
    except (TypeError, ValueError):
        return None


@app.on_event("startup")
async def startup():
    _load_cache()
    global _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(run_scan, "interval", minutes=SCAN_EVERY_MIN, id="scan", max_instances=1, coalesce=True)
    _scheduler.start()
    asyncio.create_task(_first_scan())


async def _first_scan():
    # If cache is fresh (< 8 min), skip immediate full scan but still mark ready
    if _state.get("last_scan_utc") and _state.get("setups"):
        try:
            ts = dt.datetime.fromisoformat(_state["last_scan_utc"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.timezone.utc)
            if (_now() - ts).total_seconds() < 8 * 60:
                _state["status"] = "ready"
                return
        except Exception:
            pass
    try:
        await run_scan()
    except Exception as e:
        _state["status"] = "error"
        _state["error"] = str(e)


@app.on_event("shutdown")
async def shutdown():
    if _scheduler:
        _scheduler.shutdown(wait=False)
    if _bitunix:
        await _bitunix.aclose()
    if _fund:
        await _fund.aclose()


@app.get("/api/health")
async def health():
    return {"ok": True, "status": _state["status"], "last_scan_qatar": _state["last_scan_qatar"]}


@app.get("/api/status")
async def status():
    return {
        "status": _state["status"],
        "error": _state["error"],
        "last_scan_utc": _state["last_scan_utc"],
        "last_scan_qatar": _state["last_scan_qatar"],
        "scan_seconds": _state["scan_seconds"],
        "pairs_considered": _state["pairs_considered"],
        "pairs_scanned": _state["pairs_scanned"],
        "setup_count": len(_state["setups"]),
        "pairs_excluded": _state.get("pairs_excluded") or 0,
        "excluded_count": len(_state.get("excluded") or []),
        "crypto_only": True,
        "btc": _state["btc"],
        "context_majors": _state.get("context_majors") or [],
        "changes": _state.get("changes") or {},
        "board_sort": "70% Hoody discovery + 30% rule-based setup score",
        "macro": {
            "stance": (_state.get("macro") or {}).get("stance"),
            "next_high": (_state.get("macro") or {}).get("next_high"),
            "upcoming": (_state.get("macro") or {}).get("upcoming") or [],
            "recent": (_state.get("macro") or {}).get("recent") or [],
            "source": (_state.get("macro") or {}).get("source"),
        },
        "disclaimer": "Not financial advice. This is a rules-based journal scanner, not a signal service. Leverage trading can wipe an account. DYOR.",
        "refresh_minutes": SCAN_EVERY_MIN,
    }


@app.get("/api/setups")
async def setups(
    direction: str | None = Query(None),
    state: str | None = Query(None),
    min_score: int = Query(0),
    min_discovery: int = Query(0),
    q: str | None = Query(None),
    limit: int = Query(80, le=200),
    include_majors: bool = Query(False),
    watchlist: str | None = Query(None),
):
    rows = list(_state["setups"])
    if include_majors:
        rows = rows + list(_state.get("context_majors") or [])
    if direction and direction != "all":
        rows = [r for r in rows if r.get("direction") == direction]
    if state and state != "all":
        rows = [r for r in rows if r.get("state") == state]
    if min_score:
        rows = [r for r in rows if (r.get("score") or 0) >= min_score]
    if min_discovery:
        rows = [r for r in rows if (r.get("discovery_score") or 0) >= min_discovery]
    if watchlist:
        wanted = {x.strip().upper().lstrip("$") for x in watchlist.split(",") if x.strip()}
        rows = [r for r in rows if r.get("symbol") in wanted or r.get("coin") in wanted]
    if q:
        needle = q.strip().upper().lstrip("$")
        rows = [r for r in rows if needle in (r.get("symbol") or "") or needle in (r.get("coin") or "")]
    # live tick overlay
    for r in rows:
        t = _tickers.get(r["symbol"])
        if t:
            try:
                last = float(t.get("lastPrice") or t.get("last"))
                r["last_price"] = last
                o = float(t.get("open") or 0)
                if o:
                    r["change_24h_pct"] = (last - o) / o * 100
            except (TypeError, ValueError):
                pass
    rows.sort(key=lambda r: (r.get("board_rank") or 0, r.get("discovery_score") or 0, r.get("score") or 0), reverse=True)
    return {
        "status": _state["status"],
        "last_scan_qatar": _state["last_scan_qatar"],
        "count": len(rows),
        "setups": rows[:limit],
        "changes": _state.get("changes") or {},
        "sort": "board_rank",
    }


@app.get("/api/setups/{symbol}")
async def setup_detail(symbol: str):
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    for r in list(_state["setups"]) + list(_state.get("context_majors") or []):
        if r.get("symbol") == symbol:
            t = _tickers.get(symbol)
            if t:
                try:
                    r = dict(r)
                    r["last_price"] = float(t.get("lastPrice") or t.get("last"))
                except (TypeError, ValueError):
                    pass
            return r
    raise HTTPException(404, f"No setup cached for {symbol}. It may have been below the score cutoff or not in the liquid universe.")


@app.get("/api/klines/{symbol}")
async def klines(symbol: str, interval: str = "1h", limit: int = 200):
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    if interval not in {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"}:
        raise HTTPException(400, "Unsupported interval")
    cached = _klines.get(symbol) or {}
    key = {"1d": "d1", "4h": "h4", "1h": "h1"}.get(interval)
    if key and cached.get(key):
        rows = cached[key][-limit:]
        return {"symbol": symbol, "interval": interval, "source": "cache", "rows": rows}
    bx = await _client()
    try:
        rows = await bx.klines(symbol, interval, limit)
    except BitunixError as e:
        raise HTTPException(502, str(e))
    return {"symbol": symbol, "interval": interval, "source": "live", "rows": rows}


@app.get("/api/ticker/{symbol}")
async def ticker(symbol: str):
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    t = _tickers.get(symbol)
    if not t:
        bx = await _client()
        try:
            all_t = await bx.tickers()
        except BitunixError as e:
            raise HTTPException(502, str(e))
        for row in all_t:
            if row.get("symbol") == symbol:
                _tickers[symbol] = row
                t = row
                break
    if not t:
        raise HTTPException(404, "Unknown symbol")
    return t


@app.post("/api/rescan")
async def rescan():
    if _state["status"] == "scanning":
        return {"ok": False, "message": "A scan is already running"}
    asyncio.create_task(run_scan())
    return {"ok": True, "message": "Scan started"}


@app.get("/api/tickers-live")
async def tickers_live():
    """Refresh last prices for the board without a full rescan."""
    bx = await _client()
    try:
        rows = await bx.tickers()
        funds = await bx.funding_batch()
    except BitunixError as e:
        raise HTTPException(502, str(e))
    for t in rows:
        if t.get("symbol"):
            _tickers[t["symbol"]] = t
    fmap = {f.get("symbol"): f for f in funds or []}
    slim = {}
    for r in _state["setups"]:
        sym = r["symbol"]
        t = _tickers.get(sym) or {}
        f = fmap.get(sym) or {}
        try:
            last = float(t.get("lastPrice") or t.get("last") or r.get("last_price") or 0)
            o = float(t.get("open") or 0)
            ch = (last - o) / o * 100 if o else r.get("change_24h_pct")
        except (TypeError, ValueError):
            last, ch = r.get("last_price"), r.get("change_24h_pct")
        slim[sym] = {
            "last_price": last,
            "change_24h_pct": ch,
            "funding_rate": _f(f.get("fundingRate")),
            "mark_price": _f(t.get("markPrice")),
        }
        r["last_price"] = last
        r["change_24h_pct"] = ch
        if slim[sym]["funding_rate"] is not None:
            r["funding_rate"] = slim[sym]["funding_rate"]
    btc = _tickers.get("BTCUSDT") or {}
    if _state.get("btc") and btc:
        try:
            last = float(btc.get("lastPrice") or btc.get("last"))
            o = float(btc.get("open") or 0)
            _state["btc"]["price"] = last
            if o:
                _state["btc"]["change_24h_pct"] = (last - o) / o * 100
        except (TypeError, ValueError):
            pass
    return {"tickers": slim, "btc": _state.get("btc")}


@app.get("/api/excluded")
async def excluded():
    return {
        "count": len(_state.get("excluded") or []),
        "excluded": _state.get("excluded") or [],
        "method": "Bitunix has no pair-category field. Filter = maintained crypto allow-list + TradFi exclusion list + CoinGecko name/id tokenized-stock/commodity detection + ticker heuristics. New unlisted alts are kept unless they look like stocks/ETFs/commodities.",
    }


@app.get("/api/context")
async def context():
    return {"majors": _state.get("context_majors") or [], "btc": _state.get("btc")}


# Frontend / PWA
@app.get("/")
async def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(FRONTEND / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    return FileResponse(FRONTEND / "sw.js", media_type="application/javascript")


@app.get("/icons/{name}")
async def icons(name: str):
    path = FRONTEND / "icons" / name
    if not path.exists():
        raise HTTPException(404, "icon not found")
    return FileResponse(path)


app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")
