"""
Rule-based technical analysis mapped to the Hoody Trader rulebook.

Rules (from /workspace/hoody/rulebook.md):
  1. Bias + POI + liquidity target + invalidation before entry (28 pts)
  2. Breakout + retest (or accumulation/base) (16 pts)
  3. Order block / liquidity proximity; wait for reaction (10 pts)
  4. High-beta / volume; stand down if dead or already extended (12 pts)
  5. FA bias, macro, funding context (12 pts — filled after fundamentals attach)
  6. Defined entry, stop, targets with acceptable R:R (8 pts)
  7. Do not chase; missed trades are allowed (6 pts)
  8. HTF bias, LTF trigger alignment (8 pts)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


def _sma(xs: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _rsi(closes: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_g, avg_l = gains / n, losses / n
    out[n] = 100 - 100 / (1 + (avg_g / avg_l if avg_l else 99))
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = (d if d > 0 else 0.0), (-d if d < 0 else 0.0)
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l) / n
        out[i] = 100 - 100 / (1 + (avg_g / avg_l if avg_l else 99))
    return out


def _atr(rows: list[dict], n: int = 14) -> list[float | None]:
    trs: list[float] = []
    for i, r in enumerate(rows):
        if i == 0:
            trs.append(r["h"] - r["l"])
        else:
            prev = rows[i - 1]["c"]
            trs.append(max(r["h"] - r["l"], abs(r["h"] - prev), abs(r["l"] - prev)))
    return _sma(trs, n)


def swings(rows: list[dict], left: int = 3, right: int = 3) -> tuple[list[dict], list[dict]]:
    highs, lows = [], []
    n = len(rows)
    if n < left + right + 3:
        return highs, lows
    for i in range(left, n - right):
        window_h = [rows[j]["h"] for j in range(i - left, i + right + 1)]
        window_l = [rows[j]["l"] for j in range(i - left, i + right + 1)]
        if rows[i]["h"] >= max(window_h) - 1e-18:
            highs.append({"i": i, "price": rows[i]["h"], "t": rows[i]["t"]})
        if rows[i]["l"] <= min(window_l) + 1e-18:
            lows.append({"i": i, "price": rows[i]["l"], "t": rows[i]["t"]})
    return highs, lows


def structure_bias(highs: list[dict], lows: list[dict]) -> str:
    if len(highs) < 2 or len(lows) < 2:
        return "unclear"
    h1, h2 = highs[-2]["price"], highs[-1]["price"]
    l1, l2 = lows[-2]["price"], lows[-1]["price"]
    hh, hl = h2 > h1 * 1.001, l2 > l1 * 1.001
    lh, ll = h2 < h1 * 0.999, l2 < l1 * 0.999
    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "range"


def _clusters(levels: list[float], tol: float) -> list[dict]:
    if not levels:
        return []
    xs = sorted(levels)
    groups: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if abs(x - groups[-1][-1]) / max(x, 1e-12) <= tol:
            groups[-1].append(x)
        else:
            groups.append([x])
    out = []
    for g in groups:
        if len(g) >= 2:
            out.append({"price": sum(g) / len(g), "touches": len(g), "low": min(g), "high": max(g)})
    out.sort(key=lambda z: -z["touches"])
    return out


def equal_levels(sw: list[dict], last_price: float, side: str, tol: float = 0.004) -> list[dict]:
    prices = [s["price"] for s in sw[-16:]]
    clusters = _clusters(prices, tol)
    if side == "high":
        return [c for c in clusters if c["price"] >= last_price * 0.998]
    return [c for c in clusters if c["price"] <= last_price * 1.002]


def range_box(rows: list[dict], lookback: int = 40, exclude_last: int = 3) -> dict | None:
    if len(rows) < lookback + 5:
        lookback = max(12, len(rows) - exclude_last - 2)
    if lookback < 12:
        return None
    end = len(rows) - exclude_last
    start = max(0, end - lookback)
    window = rows[start:end]
    hi = max(r["h"] for r in window)
    lo = min(r["l"] for r in window)
    mid = (hi + lo) / 2
    width = (hi - lo) / mid if mid else 0
    # Count touches near edges
    t_hi = sum(1 for r in window if abs(r["h"] - hi) / hi <= 0.008)
    t_lo = sum(1 for r in window if abs(r["l"] - lo) / lo <= 0.008)
    return {
        "high": hi,
        "low": lo,
        "width_pct": width * 100,
        "touches_high": t_hi,
        "touches_low": t_lo,
        "start_i": start,
        "end_i": end - 1,
    }


def detect_breakout_retest(rows: list[dict], box: dict | None, atr: float) -> dict:
    result = {
        "breakout": None,  # "up" | "down"
        "breakout_level": None,
        "breakout_index": None,
        "retest": False,
        "retest_held": False,
        "notes": "",
    }
    if not box or not rows or atr <= 0:
        return result
    # Search last 12 bars for a close beyond the box (box was computed excluding last 3,
    # so also recompute a slightly earlier box if needed — use given box).
    level_up, level_dn = box["high"], box["low"]
    buf = max(atr * 0.15, level_up * 0.0015)
    brk_i = None
    direction = None
    for i in range(max(box["end_i"] + 1, 1), len(rows)):
        c, h, l = rows[i]["c"], rows[i]["h"], rows[i]["l"]
        if c > level_up + buf:
            brk_i, direction = i, "up"
            break
        if c < level_dn - buf:
            brk_i, direction = i, "down"
            break
    if not direction:
        # maybe last close is still inside but wick broke — not a close breakout
        result["notes"] = "No confirmed close beyond the recent range."
        return result
    result["breakout"] = direction
    result["breakout_level"] = level_up if direction == "up" else level_dn
    result["breakout_index"] = brk_i
    level = result["breakout_level"]
    # Retest: after breakout, a later bar trades back to the level and then holds
    for i in range(brk_i + 1, len(rows)):
        r = rows[i]
        near = abs(r["l"] - level) <= atr * 1.05 or abs(r["h"] - level) <= atr * 1.05
        if direction == "up":
            near = r["l"] <= level + atr * 0.6
            failed = r["c"] < level - atr * 0.35
        else:
            near = r["h"] >= level - atr * 0.6
            failed = r["c"] > level + atr * 0.35
        if failed:
            result["retest"] = True
            result["retest_held"] = False
            result["notes"] = "Broke the range then closed back through — breakout weakened."
            return result
        if near:
            result["retest"] = True
            # held if the most recent close is still on the breakout side
            last = rows[-1]["c"]
            result["retest_held"] = (last >= level - atr * 0.15) if direction == "up" else (last <= level + atr * 0.15)
            result["notes"] = (
                "Breakout and retest held at the broken level."
                if result["retest_held"]
                else "Price has come back to the broken level; retest not yet confirmed."
            )
            return result
    result["notes"] = "Range breakout without a retest yet — do not chase; wait for the test."
    return result


def last_order_block(rows: list[dict], atr: float, kind: str) -> dict | None:
    """Approximate last opposing candle before an impulse (rulebook-style OB)."""
    if len(rows) < 20 or atr <= 0:
        return None
    # Scan from the end backward for an impulse of 2+ candles
    end = len(rows) - 1
    # ignore a tiny last wick if still forming conceptually — still use last close
    for i in range(end, 8, -1):
        if kind == "bullish":
            # impulse up: body of this or prior 2-3 candles
            run = 0.0
            start = i
            for k in range(i, max(i - 5, 0), -1):
                body = rows[k]["c"] - rows[k]["o"]
                if body > 0:
                    run += body
                    start = k
                else:
                    if run >= atr * 1.15:
                        break
                    run = 0.0
                    start = k
            if run >= atr * 1.2:
                # last down / bearish candle before start
                for j in range(start - 1, max(start - 8, 1), -1):
                    if rows[j]["c"] < rows[j]["o"] or rows[j]["l"] < rows[j - 1]["l"]:
                        lo, hi = min(rows[j]["o"], rows[j]["c"]), max(rows[j]["o"], rows[j]["c"])
                        # include wicks slightly
                        zone_lo, zone_hi = rows[j]["l"], max(hi, rows[j]["o"])
                        return {
                            "kind": "bullish",
                            "low": zone_lo,
                            "high": zone_hi,
                            "index": j,
                            "t": rows[j]["t"],
                        }
        else:
            run = 0.0
            start = i
            for k in range(i, max(i - 5, 0), -1):
                body = rows[k]["o"] - rows[k]["c"]
                if body > 0:
                    run += body
                    start = k
                else:
                    if run >= atr * 1.15:
                        break
                    run = 0.0
                    start = k
            if run >= atr * 1.2:
                for j in range(start - 1, max(start - 8, 1), -1):
                    if rows[j]["c"] > rows[j]["o"] or rows[j]["h"] > rows[j - 1]["h"]:
                        zone_lo, zone_hi = min(rows[j]["o"], rows[j]["c"]), rows[j]["h"]
                        return {
                            "kind": "bearish",
                            "low": zone_lo,
                            "high": zone_hi,
                            "index": j,
                            "t": rows[j]["t"],
                        }
    return None


def pct_range(rows: list[dict], bars: int) -> float | None:
    if not rows:
        return None
    w = rows[-bars:] if len(rows) >= bars else rows
    lo = min(r["l"] for r in w)
    hi = max(r["h"] for r in w)
    if lo <= 0:
        return None
    return (hi - lo) / lo * 100


def volume_ratio(rows: list[dict], n: int = 20) -> float | None:
    qs = [r["q"] for r in rows if r.get("q")]
    if len(qs) < n:
        qs = [r["v"] for r in rows]
    if len(qs) < n:
        return None
    avg = sum(qs[-n - 1 : -1]) / n
    if avg <= 0:
        return None
    return qs[-1] / avg


@dataclass
class Setup:
    symbol: str
    coin: str
    direction: str
    state: str
    score: int
    last_price: float
    mark_price: float | None
    change_24h_pct: float | None
    quote_vol_24h: float | None
    funding_rate: float | None
    entry_zone: str
    entry_low: float | None
    entry_high: float | None
    stop: float | None
    invalidation: str
    tp1: float | None
    tp2: float | None
    reward_risk: float | None
    rule_scores: dict
    rule_notes: dict
    ta: dict
    fa: dict = field(default_factory=dict)
    narrative: str = ""
    automated: list[str] = field(default_factory=list)
    needs_judgement: list[str] = field(default_factory=list)
    btc_aligned: bool | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _rr(direction: str, entry: float, stop: float, tp: float) -> float | None:
    risk = abs(entry - stop)
    reward = (tp - entry) if direction == "long" else (entry - tp)
    if risk <= 0:
        return None
    return reward / risk


def analyze_symbol(
    symbol: str,
    ticker: dict,
    funding: dict | None,
    d1: list[dict],
    h4: list[dict],
    h1: list[dict],
    btc_bias: str,
    btc_change_24h: float | None,
) -> Setup | None:
    if not h4 or not h1 or len(h4) < 30 or len(h1) < 40:
        return None
    last = float(ticker.get("lastPrice") or ticker.get("last") or h1[-1]["c"])
    mark = _f(ticker.get("markPrice"))
    open24 = _f(ticker.get("open"))
    ch24 = ((last - open24) / open24 * 100) if open24 else None
    qvol = _f(ticker.get("quoteVol"))
    fund = _f((funding or {}).get("fundingRate"))

    atr_d = _last(_atr(d1, 14)) if d1 and len(d1) > 20 else None
    atr_4 = _last(_atr(h4, 14)) or 0.0
    atr_1 = _last(_atr(h1, 14)) or atr_4
    rsi_4 = _last(_rsi([r["c"] for r in h4], 14))
    rsi_1 = _last(_rsi([r["c"] for r in h1], 14))

    sh_d, sl_d = swings(d1, 2, 2) if d1 and len(d1) > 20 else ([], [])
    sh_4, sl_4 = swings(h4, 3, 3)
    sh_1, sl_1 = swings(h1, 3, 3)
    bias_d = structure_bias(sh_d, sl_d) if sh_d and sl_d else "unclear"
    bias_4 = structure_bias(sh_4, sl_4)
    bias_1 = structure_bias(sh_1, sl_1)

    box_4 = range_box(h4, 36, 3)
    box_d = range_box(d1, 30, 2) if d1 and len(d1) > 25 else None
    brk = detect_breakout_retest(h4, box_4, atr_4 or last * 0.01)

    bull_ob = last_order_block(h4, atr_4 or last * 0.01, "bullish")
    bear_ob = last_order_block(h4, atr_4 or last * 0.01, "bearish")

    eq_highs = equal_levels(sh_4 + sh_d, last, "high", 0.005)
    eq_lows = equal_levels(sl_4 + sl_d, last, "low", 0.005)
    # also use recent swing high/low as liquidity if no equals
    liq_up = eq_highs[0]["price"] if eq_highs else (sh_4[-1]["price"] if sh_4 else (sh_d[-1]["price"] if sh_d else None))
    liq_dn = eq_lows[0]["price"] if eq_lows else (sl_4[-1]["price"] if sl_4 else (sl_d[-1]["price"] if sl_d else None))

    rng_7d = pct_range(d1, 7) if d1 else pct_range(h4, 42)
    rng_24h = None
    if open24 and ticker.get("high") and ticker.get("low"):
        lo, hi = _f(ticker["low"]), _f(ticker["high"])
        if lo:
            rng_24h = (hi - lo) / lo * 100
    if rng_24h is None:
        rng_24h = pct_range(h1, 24)

    vol_r = volume_ratio(h1, 20)

    # Direction decision — rule-based, not a vibe
    direction = "unclear"
    reasons = []
    if brk["breakout"] == "up" and brk["retest_held"]:
        direction = "long"
        reasons.append("4H close broke the range and the retest held")
    elif brk["breakout"] == "down" and brk["retest_held"]:
        direction = "short"
        reasons.append("4H close broke down and the retest held")
    elif brk["breakout"] == "up" and not brk["retest"]:
        direction = "long"
        reasons.append("4H range breakout up, retest not yet seen (watch, do not chase)")
    elif brk["breakout"] == "down" and not brk["retest"]:
        direction = "short"
        reasons.append("4H range breakdown, retest not yet seen (watch, do not chase)")
    else:
        # POI reaction: price sitting in an OB in the direction of HTF bias
        near_bull = bull_ob and _near(last, bull_ob["low"], bull_ob["high"], atr_4)
        near_bear = bear_ob and _near(last, bear_ob["low"], bear_ob["high"], atr_4)
        htf = bias_d if bias_d != "unclear" else bias_4
        if near_bull and htf in ("bullish", "range") and bias_4 != "bearish":
            direction = "long"
            reasons.append("Price is testing a 4H bullish order block with non-bearish higher-timeframe bias")
        elif near_bear and htf in ("bearish", "range") and bias_4 != "bullish":
            direction = "short"
            reasons.append("Price is testing a 4H bearish order block with non-bullish higher-timeframe bias")
        elif box_4 and box_4["width_pct"] >= 8 and last <= box_4["low"] + (atr_4 or 0) * 0.8 and htf != "bearish":
            direction = "long"
            reasons.append("Price is testing the low of a 4H accumulation/range")
        elif box_4 and box_4["width_pct"] >= 8 and last >= box_4["high"] - (atr_4 or 0) * 0.8 and htf != "bullish":
            direction = "short"
            reasons.append("Price is testing the high of a 4H range / supply")
        elif htf == "bullish" and bias_4 == "bullish":
            direction = "long"
            reasons.append("Daily and 4H structure both bullish (HH/HL), looking for a pullback POI")
        elif htf == "bearish" and bias_4 == "bearish":
            direction = "short"
            reasons.append("Daily and 4H structure both bearish (LH/LL), looking for a rally into supply")

    if direction == "unclear":
        # Still emit a low-score watch card only if the coin is notably volatile
        if (rng_7d or 0) < 12 and (rng_24h or 0) < 8:
            return None
        direction = "watch"
        reasons.append("No clean directional trigger yet — volatility is present, waiting for a POI reaction")

    # Levels
    entry_low = entry_high = stop = tp1 = tp2 = None
    invalidation = "unknown"
    if direction == "long":
        if brk["breakout"] == "up" and brk["breakout_level"]:
            lvl = brk["breakout_level"]
            atr_use = atr_4 or last * 0.01
            entry_low, entry_high = lvl - atr_use * 0.25, lvl + atr_use * 0.35
            # Only merge a nearby bullish OB, not a demand zone far below the retest.
            if bull_ob and abs(bull_ob["high"] - lvl) <= atr_use * 1.4:
                entry_low = min(entry_low, bull_ob["low"])
                entry_high = max(entry_high, min(bull_ob["high"], lvl + atr_use * 0.5))
            stop = entry_low - atr_use * 0.45
        elif bull_ob:
            entry_low, entry_high = bull_ob["low"], bull_ob["high"]
            stop = bull_ob["low"] - (atr_4 or last * 0.01) * 0.4
        elif box_4:
            entry_low, entry_high = box_4["low"], box_4["low"] + (atr_4 or 0) * 0.6
            stop = box_4["low"] - (atr_4 or 0) * 0.4
        else:
            entry_low = entry_high = last
            stop = last - (atr_4 or last * 0.02) * 1.2
        # liquidity targets
        candidates = []
        if liq_up and liq_up > last * 1.004:
            candidates.append(liq_up)
        if box_4 and box_4["high"] > last * 1.004:
            candidates.append(box_4["high"])
        if sh_4:
            candidates += [s["price"] for s in sh_4[-4:] if s["price"] > last * 1.006]
        if sh_d:
            candidates += [s["price"] for s in sh_d[-3:] if s["price"] > last * 1.01]
        candidates = sorted(set(round(c, 10) for c in candidates))
        tp1 = candidates[0] if candidates else last + (atr_4 or last * 0.02) * 2
        tp2 = candidates[1] if len(candidates) > 1 else (candidates[0] * 1.0 + (atr_4 or 0) * 2 if candidates else last + (atr_4 or last * 0.02) * 4)
        if tp2 <= tp1:
            tp2 = tp1 + (atr_4 or last * 0.01) * 2
        invalidation = f"4H close below { _fmt(stop) } (beyond the demand / broken level)"
    elif direction == "short":
        if brk["breakout"] == "down" and brk["breakout_level"]:
            lvl = brk["breakout_level"]
            atr_use = atr_4 or last * 0.01
            entry_low, entry_high = lvl - atr_use * 0.35, lvl + atr_use * 0.25
            if bear_ob and abs(bear_ob["low"] - lvl) <= atr_use * 1.4:
                entry_high = max(entry_high, bear_ob["high"])
                entry_low = min(entry_low, max(bear_ob["low"], lvl - atr_use * 0.5))
            stop = entry_high + atr_use * 0.45
        elif bear_ob:
            entry_low, entry_high = bear_ob["low"], bear_ob["high"]
            stop = bear_ob["high"] + (atr_4 or last * 0.01) * 0.4
        elif box_4:
            entry_low, entry_high = box_4["high"] - (atr_4 or 0) * 0.6, box_4["high"]
            stop = box_4["high"] + (atr_4 or 0) * 0.4
        else:
            entry_low = entry_high = last
            stop = last + (atr_4 or last * 0.02) * 1.2
        candidates = []
        if liq_dn and liq_dn < last * 0.996:
            candidates.append(liq_dn)
        if box_4 and box_4["low"] < last * 0.996:
            candidates.append(box_4["low"])
        if sl_4:
            candidates += [s["price"] for s in sl_4[-4:] if s["price"] < last * 0.994]
        if sl_d:
            candidates += [s["price"] for s in sl_d[-3:] if s["price"] < last * 0.99]
        candidates = sorted(set(round(c, 10) for c in candidates), reverse=True)
        tp1 = candidates[0] if candidates else last - (atr_4 or last * 0.02) * 2
        tp2 = candidates[1] if len(candidates) > 1 else (candidates[0] - (atr_4 or 0) * 2 if candidates else last - (atr_4 or last * 0.02) * 4)
        if tp2 >= tp1:
            tp2 = tp1 - (atr_4 or last * 0.01) * 2
        invalidation = f"4H close above { _fmt(stop) } (beyond the supply / broken level)"
    else:
        entry_low = entry_high = last
        stop = None

    # Tighten stops (SL should survive wicks, not the entire opposite side of a huge range)
    # and make sure targets sit beyond the entry zone.
    atr_use = atr_4 or last * 0.01
    if direction == "long" and entry_low is not None and stop is not None:
        cap = entry_low - atr_use * 3.2
        tight = entry_low - atr_use * 0.45
        # prefer a stop just beyond the POI; never tighter than `tight`
        if stop < cap:
            stop = cap
        if stop > tight:
            stop = tight
    if direction == "short" and entry_high is not None and stop is not None:
        cap = entry_high + atr_use * 3.2
        tight = entry_high + atr_use * 0.45
        if stop > cap:
            stop = cap
        if stop < tight:
            stop = tight

    def _valid_tps(direction, entry_low, entry_high, tps):
        out = []
        for p in tps:
            if p is None:
                continue
            if direction == "long" and entry_high and p > entry_high * 1.008:
                out.append(p)
            elif direction == "short" and entry_low and p < entry_low * 0.992:
                out.append(p)
        return out

    if direction == "long" and entry_high is not None:
        raw = [tp1, tp2] + ([] if not box_4 else [box_4["high"]])
        # measured move: range height projected from breakout/entry
        if box_4:
            raw.append(entry_high + (box_4["high"] - box_4["low"]))
        raw.append(entry_high + atr_use * 2.2)
        raw.append(entry_high + atr_use * 4.0)
        ok = sorted(set(_valid_tps("long", entry_low, entry_high, raw)))
        tp1 = ok[0] if ok else entry_high + atr_use * 2.2
        tp2 = ok[1] if len(ok) > 1 else tp1 + atr_use * 2.0
        invalidation = f"4H close below { _fmt(stop) } (beyond the demand / broken level)"
    elif direction == "short" and entry_low is not None:
        raw = [tp1, tp2] + ([] if not box_4 else [box_4["low"]])
        if box_4:
            raw.append(entry_low - (box_4["high"] - box_4["low"]))
        raw.append(entry_low - atr_use * 2.2)
        raw.append(entry_low - atr_use * 4.0)
        ok = sorted(set(_valid_tps("short", entry_low, entry_high, raw)), reverse=True)
        tp1 = ok[0] if ok else entry_low - atr_use * 2.2
        tp2 = ok[1] if len(ok) > 1 else tp1 - atr_use * 2.0
        invalidation = f"4H close above { _fmt(stop) } (beyond the supply / broken level)"

    mid_entry = None
    if entry_low is not None and entry_high is not None:
        mid_entry = (entry_low + entry_high) / 2
        # If price is already far past the zone, mid_entry for RR should be current or zone
        # R:R is quoted from the planned entry, not from a chased market price.
        use_entry = mid_entry
    else:
        use_entry = last

    rr = None
    if direction in ("long", "short") and stop and tp1 and use_entry:
        rr = _rr(direction, use_entry, stop, tp1)

    # Extension / chase flags
    stretched = False
    stretch_notes = []
    if direction == "long" and rsi_4 is not None and rsi_4 >= 76:
        stretched, stretch_notes = True, stretch_notes + [f"4H RSI is {rsi_4:.0f} (overbought)"]
    if direction == "short" and rsi_4 is not None and rsi_4 <= 24:
        stretched, stretch_notes = True, stretch_notes + [f"4H RSI is {rsi_4:.0f} (oversold)"]
    if direction == "long" and ch24 is not None and ch24 >= 18:
        stretched, stretch_notes = True, stretch_notes + [f"Already up {ch24:.1f}% in 24h"]
    if direction == "short" and ch24 is not None and ch24 <= -18:
        stretched, stretch_notes = True, stretch_notes + [f"Already down {abs(ch24):.1f}% in 24h"]
    if direction == "long" and brk["breakout"] == "up" and not brk["retest"] and not brk["retest_held"]:
        # distance from breakout
        if brk["breakout_level"] and last > brk["breakout_level"] * 1.04:
            stretched, stretch_notes = True, stretch_notes + ["Broke out and has already extended without a retest"]
    if direction == "short" and brk["breakout"] == "down" and not brk["retest"] and brk["breakout_level"] and last < brk["breakout_level"] * 0.96:
        stretched, stretch_notes = True, stretch_notes + ["Broke down and has already extended without a retest"]
    if fund is not None and direction == "long" and fund >= 0.001:  # 0.1%
        stretch_notes.append(f"Funding is elevated for longs ({fund:.3%})")
    if fund is not None and direction == "short" and fund <= -0.001:
        stretch_notes.append(f"Funding is elevated for shorts ({fund:.3%})")

    # State
    at_poi = False
    if entry_low is not None and entry_high is not None:
        pad = (atr_1 or atr_4 or last * 0.01) * 0.5
        at_poi = entry_low - pad <= last <= entry_high + pad
    if direction == "watch":
        state = "watch"
    elif stretched:
        state = "stretched"
    elif brk["retest_held"] and brk["breakout"] in ("up", "down"):
        state = "confirmed" if (rr is None or rr >= 1.2) else "watch"
    elif at_poi:
        state = "trigger" if (rr is None or rr >= 1.0) else "watch"
    elif brk["breakout"] and not brk["retest"]:
        state = "watch"
    else:
        state = "watch"

    # --- Scoring (explicitly mapped to rules) ---
    rs = {str(i): 0 for i in range(1, 9)}
    rn: dict[str, str] = {}

    # Rule 1: four parts, 7 pts each, max 28
    r1 = 0
    parts = []
    bias_clear = (bias_d != "unclear") or (bias_4 != "unclear")
    if bias_clear:
        r1 += 7
        parts.append(f"bias is {bias_d} daily / {bias_4} 4H")
    poi_defined = entry_low is not None and entry_high is not None and direction in ("long", "short")
    if poi_defined:
        r1 += 7
        parts.append("POI/entry zone is defined")
    liq_defined = (direction == "long" and tp1 and tp1 > last) or (direction == "short" and tp1 and tp1 < last)
    if liq_defined:
        r1 += 7
        parts.append("liquidity / TP target is defined")
    inv_defined = stop is not None and direction in ("long", "short")
    if inv_defined:
        r1 += 7
        parts.append("invalidation is defined")
    rs["1"] = r1
    rn["1"] = "; ".join(parts) if parts else "Missing one or more of: bias, POI, liquidity target, invalidation"

    # Rule 2: breakout + retest, max 16
    r2 = 0
    if brk["breakout"] and brk["retest_held"]:
        r2 = 16
        rn["2"] = brk["notes"] or "Breakout and retest held"
    elif brk["breakout"] and brk["retest"] and not brk["retest_held"]:
        r2 = 8
        rn["2"] = brk["notes"] or "Retest in progress, not yet held"
    elif brk["breakout"] and not brk["retest"]:
        r2 = 6
        rn["2"] = brk["notes"] or "Breakout without a retest — wait"
    elif box_4 and box_4["width_pct"] >= 10 and (box_4["touches_high"] >= 2 or box_4["touches_low"] >= 2):
        r2 = 6
        rn["2"] = f"4H accumulation/range is building (width {box_4['width_pct']:.1f}%) — waiting for break and retest"
    else:
        rn["2"] = "No breakout-and-retest structure on the 4H yet"
    rs["2"] = r2

    # Rule 3: OB / liquidity proximity, max 10
    r3 = 0
    if direction == "long" and bull_ob and _near(last, bull_ob["low"], bull_ob["high"], atr_4):
        r3 = 10
        rn["3"] = "Price is at/near a 4H bullish order block"
    elif direction == "short" and bear_ob and _near(last, bear_ob["low"], bear_ob["high"], atr_4):
        r3 = 10
        rn["3"] = "Price is at/near a 4H bearish order block"
    elif brk["retest"] and at_poi:
        r3 = 8
        rn["3"] = "Price is reacting at the broken level (the current POI)"
    elif (eq_highs or eq_lows) and at_poi:
        r3 = 6
        rn["3"] = "Equal highs/lows (liquidity) are mapped and price is near a POI"
    elif bull_ob or bear_ob or eq_highs or eq_lows:
        r3 = 4
        rn["3"] = "Order block or liquidity is mapped, but price is not at the zone yet — wait for a reaction"
    else:
        rn["3"] = "No clear order-block or equal-high/low cluster nearby"
    rs["3"] = r3

    # Rule 4: volatility + volume, max 12; dead volume / tiny range scores poorly
    r4 = 0
    notes4 = []
    if rng_24h is not None and rng_24h >= 20:
        r4 += 7
        notes4.append(f"24h range {rng_24h:.1f}% (his ~20% day filter)")
    elif rng_7d is not None and rng_7d >= 20:
        r4 += 6
        notes4.append(f"7d range {rng_7d:.1f}%")
    elif (rng_24h or 0) >= 10 or (rng_7d or 0) >= 12:
        r4 += 4
        notes4.append(f"moderate range (24h {rng_24h or 0:.1f}%, 7d {rng_7d or 0:.1f}%)")
    else:
        notes4.append("Range is muted — he often stands down when names do not move")
    if qvol is not None and qvol >= 5_000_000:
        r4 += 5
        notes4.append(f"24h turnover ${qvol/1e6:.1f}M")
    elif qvol is not None and qvol >= 1_500_000:
        r4 += 3
        notes4.append(f"24h turnover ${qvol/1e6:.1f}M (tradable but not deep)")
    else:
        notes4.append("Turnover is thin — liquidity risk")
    rs["4"] = min(12, r4)
    rn["4"] = "; ".join(notes4)

    # Rule 5 placeholder (funding + BTC only for now; FA filled later)
    r5 = 0
    notes5 = []
    aligned = None
    if direction == "long" and btc_bias == "bullish":
        r5 += 4
        aligned = True
        notes5.append("BTC daily/structure bias is bullish (supports alt longs)")
    elif direction == "short" and btc_bias == "bearish":
        r5 += 4
        aligned = True
        notes5.append("BTC daily/structure bias is bearish (supports alt shorts)")
    elif direction in ("long", "short") and btc_bias == "range":
        r5 += 2
        aligned = None
        notes5.append("BTC is ranging — alt setups are allowed but weaker")
    elif direction in ("long", "short") and btc_bias not in ("unclear",):
        aligned = False
        notes5.append(f"BTC bias is {btc_bias}, which fights a {direction} on this alt")
    if fund is not None:
        extreme_fund = abs(fund) >= 0.2
        if extreme_fund:
            notes5.append(
                f"Funding is extreme ({fund:.3%}) — often an exchange cap or index dislocation, not a clean crowding signal"
            )
        elif direction == "long" and fund < 0:
            r5 += 2
            notes5.append(f"Funding is negative ({fund:.3%}) — longs are not crowded")
        elif direction == "short" and fund > 0:
            r5 += 2
            notes5.append(f"Funding is positive ({fund:.3%}) — shorts are not crowded")
        elif direction == "long" and fund >= 0.0015:
            notes5.append(f"Funding is rich for longs ({fund:.3%}) — squeeze/extension risk")
        elif direction == "short" and fund <= -0.0015:
            notes5.append(f"Funding is rich for shorts ({fund:.3%}) — squeeze risk")
        else:
            r5 += 1
            notes5.append(f"Funding is mild ({fund:.3%})")
    rs["5"] = min(12, r5)  # remaining points added when FA attaches
    rn["5"] = "; ".join(notes5) if notes5 else "No BTC/funding context yet; fundamentals attach after the scan"

    # Rule 6: defined plan + RR, max 8
    r6 = 0
    if direction in ("long", "short") and stop and tp1 and entry_low is not None:
        r6 += 3
        if rr is not None and rr >= 2:
            r6 += 5
            rn["6"] = f"Entry, invalidation and TP1 are set; R:R to TP1 is {rr:.2f}"
        elif rr is not None and rr >= 1.4:
            r6 += 3
            rn["6"] = f"Levels are set but R:R to TP1 is only {rr:.2f} (want ≥ 2 if possible)"
        elif rr is not None and rr >= 1:
            r6 += 1
            rn["6"] = f"Levels are set but R:R to TP1 is tight ({rr:.2f})"
        else:
            rn["6"] = "Stop and target are set but the reward does not justify the risk — skip or wait"
    else:
        rn["6"] = "No complete entry / stop / target plan yet"
    rs["6"] = min(8, r6)

    # Rule 7: do not chase, max 6
    r7 = 0
    if stretched:
        r7 = 0
        rn["7"] = "Do not chase: " + "; ".join(stretch_notes)
    elif brk["breakout"] and not brk["retest"] and not at_poi:
        r7 = 2
        rn["7"] = "Breakout is in, but there is no retest yet — he would usually wait rather than FOMO"
    elif at_poi or brk["retest_held"]:
        r7 = 6
        rn["7"] = "Price is at the planned zone (or a held retest) — this is a reaction, not a chase"
    elif state == "watch":
        r7 = 4
        rn["7"] = "Setup is on the watchlist; no need to force an entry"
    else:
        r7 = 3
        rn["7"] = "Not clearly extended, but also not clearly at the POI"
    rs["7"] = r7

    # Rule 8: MTF alignment, max 8
    r8 = 0
    htf = bias_d if bias_d != "unclear" else bias_4
    if direction == "long" and htf == "bullish" and bias_4 in ("bullish", "range"):
        r8 += 5
        if bias_1 in ("bullish", "range") or at_poi:
            r8 += 3
        rn["8"] = f"HTF bias is {htf} (daily {bias_d}, 4H {bias_4}, 1H {bias_1})"
    elif direction == "short" and htf == "bearish" and bias_4 in ("bearish", "range"):
        r8 += 5
        if bias_1 in ("bearish", "range") or at_poi:
            r8 += 3
        rn["8"] = f"HTF bias is {htf} (daily {bias_d}, 4H {bias_4}, 1H {bias_1})"
    elif direction in ("long", "short") and htf == "range":
        r8 += 3
        rn["8"] = f"Higher timeframe is ranging (daily {bias_d}, 4H {bias_4}) — trade the range edges, not a trend story"
    elif direction in ("long", "short"):
        r8 += 1
        rn["8"] = f"Timeframes conflict (daily {bias_d}, 4H {bias_4}, 1H {bias_1}) — lower conviction"
    else:
        rn["8"] = f"Daily {bias_d}, 4H {bias_4}, 1H {bias_1} — waiting for alignment"
    rs["8"] = min(8, r8)

    score = int(sum(rs.values()))
    if direction == "watch":
        score = min(score, 48)

    coin = symbol.replace("USDT", "")
    entry_zone = "unknown"
    if entry_low is not None and entry_high is not None:
        if abs(entry_high - entry_low) / max(last, 1e-12) < 0.001:
            entry_zone = _fmt(entry_low)
        else:
            entry_zone = f"{_fmt(entry_low)} – {_fmt(entry_high)}"

    narrative = _narrative(
        coin, direction, state, reasons, bias_d, bias_4, bias_1, brk, box_4,
        bull_ob, bear_ob, eq_highs, eq_lows, stretched, stretch_notes, fund, rng_24h, rng_7d,
        last, stop, tp1, tp2, rr, btc_bias,
    )

    automated = [
        "Prices, 24h volume, funding and klines from Bitunix public futures API",
        "Daily / 4H / 1H market structure, range, breakout/retest, approximate order blocks, equal highs/lows, RSI, ATR, and R:R",
        "Score points for rules 1–4 and 6–8, plus the BTC/funding slice of rule 5",
    ]
    needs_judgement = [
        "Whether the detected order block is a high-quality POI (body vs wick, which candle) — rulebook open question",
        "Whether to actually enter, size the position, or wait for a closer reaction (Rule 3 and 7)",
        "Narrative quality, token unlocks, and news are attached when available but still need a human read (Rule 5)",
        "Chart patterns such as H&S or wedges are not auto-traded; they are only hinted when structure looks like a range or double swing",
    ]

    ta = {
        "bias_daily": bias_d,
        "bias_4h": bias_4,
        "bias_1h": bias_1,
        "btc_bias": btc_bias,
        "range_4h": box_4,
        "range_daily": box_d,
        "breakout": brk,
        "bullish_ob": bull_ob,
        "bearish_ob": bear_ob,
        "equal_highs": eq_highs[:3],
        "equal_lows": eq_lows[:3],
        "liquidity_up": liq_up,
        "liquidity_down": liq_dn,
        "rsi_4h": rsi_4,
        "rsi_1h": rsi_1,
        "atr_4h": atr_4,
        "atr_1h": atr_1,
        "range_24h_pct": rng_24h,
        "range_7d_pct": rng_7d,
        "volume_ratio_1h": vol_r,
        "stretched": stretched,
        "stretch_notes": stretch_notes,
        "reasons": reasons,
    }

    return Setup(
        symbol=symbol,
        coin=coin,
        direction=direction if direction != "watch" else "watch",
        state=state,
        score=score,
        last_price=last,
        mark_price=mark,
        change_24h_pct=ch24,
        quote_vol_24h=qvol,
        funding_rate=fund,
        entry_zone=entry_zone,
        entry_low=entry_low,
        entry_high=entry_high,
        stop=stop,
        invalidation=invalidation,
        tp1=tp1,
        tp2=tp2,
        reward_risk=rr,
        rule_scores=rs,
        rule_notes=rn,
        ta=ta,
        narrative=narrative,
        automated=automated,
        needs_judgement=needs_judgement,
        btc_aligned=aligned,
    )


def apply_fundamentals(setup: Setup, fa: dict) -> Setup:
    """Add remaining Rule 5 points from CoinGecko / news / macro / unlocks."""
    r5 = setup.rule_scores.get("5", 0)
    notes = [setup.rule_notes.get("5") or ""]
    direction = setup.direction
    # Categories / narrative: informational, +2 if present
    cats = fa.get("categories") or []
    if cats:
        r5 += 2
        notes.append("Narrative tags: " + ", ".join(cats[:4]))
    # Upcoming unlock: bearish lean
    unlock = fa.get("unlock")
    if unlock and unlock.get("headline"):
        if direction == "short":
            r5 += 3
            notes.append("Unlock/news headline may add sell pressure (still verify): " + unlock["headline"][:140])
        elif direction == "long":
            notes.append("Unlock/news headline may fight a long (verify): " + unlock["headline"][:140])
        else:
            notes.append("Unlock/news: " + unlock["headline"][:140])
    # Macro
    macro = fa.get("macro") or {}
    if macro.get("stance") == "risk_off" and direction == "short":
        r5 += 2
        notes.append("Near-term macro calendar is risk-off (CPI/FOMC) — supports shorts, still judgement")
    elif macro.get("stance") == "risk_off" and direction == "long":
        notes.append("Near-term macro calendar is risk-off — be careful with longs")
    elif macro.get("next_high"):
        r5 += 1
        notes.append("Next high-impact event: " + macro["next_high"])
    # Market structure FA: far below ATH can support "base building" longs
    ath_chg = fa.get("ath_change_pct")
    if direction == "long" and ath_chg is not None and ath_chg <= -70:
        r5 += 1
        notes.append(f"Price is {ath_chg:.0f}% below ATH (base-building context, not a reason to buy alone)")
    setup.rule_scores["5"] = min(12, r5)
    setup.rule_notes["5"] = "; ".join(n for n in notes if n)
    setup.score = int(sum(setup.rule_scores.values()))
    if setup.direction == "watch":
        setup.score = min(setup.score, 48)
    setup.fa = fa
    return setup


def _narrative(
    coin, direction, state, reasons, bias_d, bias_4, bias_1, brk, box_4,
    bull_ob, bear_ob, eq_highs, eq_lows, stretched, stretch_notes, fund,
    rng_24h, rng_7d, last, stop, tp1, tp2, rr, btc_bias,
) -> str:
    bits = [f"${coin} is a {direction} {state} setup on Bitunix USDT perps."]
    bits.append(f"Structure: daily {bias_d}, 4H {bias_4}, 1H {bias_1}. BTC context is {btc_bias}.")
    if reasons:
        bits.append(reasons[0] + ".")
    if brk.get("notes"):
        bits.append(brk["notes"])
    if box_4:
        bits.append(f"The 4H range in view is {_fmt(box_4['low'])}–{_fmt(box_4['high'])} ({box_4['width_pct']:.1f}% wide).")
    if direction == "long" and bull_ob:
        bits.append(f"Bullish order block (approx.) sits at {_fmt(bull_ob['low'])}–{_fmt(bull_ob['high'])}.")
    if direction == "short" and bear_ob:
        bits.append(f"Bearish order block (approx.) sits at {_fmt(bear_ob['low'])}–{_fmt(bear_ob['high'])}.")
    if eq_highs:
        bits.append(f"Equal highs (buy-side liquidity) around {_fmt(eq_highs[0]['price'])} ({eq_highs[0]['touches']} touches).")
    if eq_lows:
        bits.append(f"Equal lows (sell-side liquidity) around {_fmt(eq_lows[0]['price'])} ({eq_lows[0]['touches']} touches).")
    if rng_24h is not None or rng_7d is not None:
        bits.append(f"Range: 24h {rng_24h or 0:.1f}%, 7d {rng_7d or 0:.1f}%.")
    if fund is not None:
        if abs(fund) >= 0.2:
            bits.append(f"Current funding is {fund:.3%} (extreme / possibly at the exchange cap — do not treat as a normal crowding read).")
        else:
            bits.append(f"Current funding is {fund:.3%}.")
    if stretched:
        bits.append("Extension warning: " + "; ".join(stretch_notes) + ".")
    if stop and tp1:
        bits.append(
            f"Plan: work the entry zone, invalidate at {_fmt(stop)}, first liquidity/TP1 {_fmt(tp1)}"
            + (f", TP2 {_fmt(tp2)}" if tp2 else "")
            + (f" (R:R {rr:.2f} to TP1)." if rr else ".")
        )
    bits.append("This is a rules-based scan, not a trade order. Confirm the levels on the chart.")
    return " ".join(bits)


def _f(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _last(xs):
    for x in reversed(xs or []):
        if x is not None:
            return x
    return None


def _near(price: float, lo: float, hi: float, atr: float | None) -> bool:
    pad = (atr or (hi - lo) or price * 0.01) * 0.6
    return lo - pad <= price <= hi + pad


def _fmt(x: float | None) -> str:
    if x is None:
        return "—"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 1:
        return f"{x:.4f}".rstrip("0").rstrip(".")
    if ax >= 0.01:
        return f"{x:.6f}".rstrip("0").rstrip(".")
    return f"{x:.8f}".rstrip("0").rstrip(".")
