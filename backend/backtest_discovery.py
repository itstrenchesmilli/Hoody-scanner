"""Historical discovery backtest: Hoody's real alt picks vs random liquid alts."""
from __future__ import annotations

import asyncio
import csv
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.discovery import extract_features, score_discovery  # noqa: E402
from backend.universe import is_mega_major  # noqa: E402

OUT = ROOT / "data" / "discovery_backtest.json"
TRADES = Path("/workspace/hoody/trades.csv")
CONTROLS = [
    "AVAXUSDT", "LINKUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "TIAUSDT",
    "WLDUSDT", "NEARUSDT", "APTUSDT", "ATOMUSDT", "FILUSDT", "AAVEUSDT",
    "ORDIUSDT", "WIFUSDT", "1000PEPEUSDT", "SEIUSDT", "JUPUSDT", "PYTHUSDT",
    "BIGTIMEUSDT", "POPCATUSDT", "BOMEUSDT", "CFXUSDT", "STXUSDT", "ENAUSDT",
    "ONDOUSDT", "TAOUSDT", "FETUSDT", "APEUSDT", "BLURUSDT",
]


def _to_rows(kl):
    out = []
    for x in kl:
        try:
            out.append({"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5]), "q": float(x[6] if len(x) > 6 else x[5])})
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda r: r["t"])
    return out


async def fetch_bybit(http, symbol, end_ms, sem):
    async with sem:
        r = await http.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": "D", "end": int(end_ms), "limit": 120},
        )
    if r.status_code != 200:
        return []
    js = r.json()
    if js.get("retCode") != 0:
        return []
    return _to_rows((js.get("result") or {}).get("list") or [])


async def klines(http, symbol, end_ms, cache, sem):
    key = (symbol, int(end_ms) // 86400000)
    if key in cache:
        return cache[key]
    rows = await fetch_bybit(http, symbol, end_ms, sem)
    cache[key] = rows
    return rows


def score_rows(coin, d1, btc_d1, end_ms):
    feat = extract_features(coin=coin, d1=d1, btc_d1=btc_d1, launch_ms=None, now_ms=end_ms)
    if d1 and d1[-1].get("q"):
        feat["quote_vol_24h"] = d1[-1]["q"]
    s, b, n = score_discovery(feat)
    return s


async def main():
    rows = list(csv.DictReader(open(TRADES)))
    picks = []
    for r in rows:
        coin = (r.get("coin") or "").upper().replace("$", "").replace("USDT", "")
        if not coin or is_mega_major(coin):
            continue
        src = r.get("source") or ""
        date = r.get("date") or ""
        if src != "formal_call" and not date.startswith("2026"):
            continue
        try:
            ts = datetime.fromisoformat(date.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts.year < 2024:
            continue
        picks.append({"coin": coin, "symbol": coin + "USDT", "date": date, "ts": ts, "end_ms": int(ts.timestamp() * 1000) - 3600_000})
    seen = set()
    uniq = []
    for p in picks:
        k = (p["coin"], p["ts"].date().isoformat())
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    picks = uniq
    print(f"picks {len(picks)}", flush=True)

    cache = {}
    sem = asyncio.Semaphore(8)
    results = []
    skipped = 0
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "HoodySetupScanner/1.0"}) as http:
        for i, p in enumerate(picks, 1):
            btc = await klines(http, "BTCUSDT", p["end_ms"], cache, sem)
            d1 = await klines(http, p["symbol"], p["end_ms"], cache, sem)
            if len(d1) < 15 or len(btc) < 15:
                skipped += 1
                print(f"  skip {p['coin']} {p['date'][:10]} d1={len(d1)}", flush=True)
                continue
            ps = score_rows(p["coin"], d1, btc, p["end_ms"])
            c_scores = []
            # fetch remaining controls concurrently
            missing = [c for c in CONTROLS if c != p["symbol"] and c.replace("USDT", "") != p["coin"]]
            fetched = await asyncio.gather(*[klines(http, c, p["end_ms"], cache, sem) for c in missing])
            for c, cd in zip(missing, fetched):
                if len(cd) < 15:
                    continue
                c_scores.append({"symbol": c, "score": score_rows(c.replace("USDT", ""), cd, btc, p["end_ms"])})
            if len(c_scores) < 8:
                skipped += 1
                print(f"  few controls {p['coin']}", flush=True)
                continue
            universe = c_scores + [{"symbol": p["symbol"], "score": ps}]
            universe.sort(key=lambda x: x["score"], reverse=True)
            rank = next(i0 for i0, x in enumerate(universe, 1) if x["symbol"] == p["symbol"])
            n = len(universe)
            percentile = (n - rank + 1) / n * 100
            rec = {
                "coin": p["coin"],
                "date": p["date"],
                "discovery_score": ps,
                "rank": rank,
                "universe_n": n,
                "percentile": round(percentile, 1),
                "control_median": round(statistics.median(x["score"] for x in c_scores), 1),
                "control_mean": round(statistics.mean(x["score"] for x in c_scores), 1),
            }
            results.append(rec)
            if i % 10 == 0 or i == 1:
                print(f"  {i}/{len(picks)} {p['date'][:10]} {p['coin']:10} score={ps:3} rank={rank}/{n} pct={percentile:.0f}", flush=True)

    if not results:
        OUT.write_text(json.dumps({"error": "no results", "skipped": skipped}, indent=2))
        print("no results", flush=True)
        return
    pcts = [r["percentile"] for r in results]
    scores = [r["discovery_score"] for r in results]
    diffs = [r["discovery_score"] - r["control_median"] for r in results]
    summary = {
        "n_picks_scored": len(results),
        "n_skipped_no_klines": skipped,
        "method": "Discovery features from daily Bybit klines at the ticket date (range, ATR, volume spike, RS vs BTC, compression, distance from 30d high). Mega-majors excluded from the pick set. Controls = other liquid alts with history that day. Market-cap, listing age, and narrative were not historically available, so those buckets are neutral in the backtest (the live scanner uses them).",
        "median_percentile": round(statistics.median(pcts), 1),
        "mean_percentile": round(statistics.mean(pcts), 1),
        "share_top_quartile": round(sum(1 for p in pcts if p >= 75) / len(results), 3),
        "share_top_half": round(sum(1 for p in pcts if p >= 50) / len(results), 3),
        "median_pick_score": round(statistics.median(scores), 1),
        "median_vs_control": round(statistics.median(diffs), 1),
        "mean_vs_control": round(statistics.mean(diffs), 1),
        "by_year": {},
        "results": results,
    }
    by = {}
    for r in results:
        by.setdefault(r["date"][:4], []).append(r)
    for y, rs in by.items():
        summary["by_year"][y] = {
            "n": len(rs),
            "median_percentile": round(statistics.median(x["percentile"] for x in rs), 1),
            "share_top_half": round(sum(1 for x in rs if x["percentile"] >= 50) / len(rs), 3),
            "median_vs_control": round(statistics.median(x["discovery_score"] - x["control_median"] for x in rs), 1),
        }
    OUT.write_text(json.dumps(summary, indent=2))
    print("wrote", OUT, flush=True)
    print("SUMMARY", {k: summary[k] for k in summary if k != "results"}, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
