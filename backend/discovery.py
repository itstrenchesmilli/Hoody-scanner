"""Hoody discovery ranker.

Quantified from the public channel trade log (451 unique tickets, 216 coins,
May 2023–Nov 2026): only ~13% of tickets were mega-majors (BTC/ETH/XRP/BNB/SOL),
and the 2025–2026 Escape-era book is mostly long-tail high-beta names (POPCAT,
BIGTIME, LIGHT, MAGMA, BTR, AKE, JCT, IRYS, HYPE, AVAAI, memes and new listings)
that can move on the order of 15–30% in a swing. Median MFE on verified formal
tickets was ~17%, and 42% reached a 20% favorable excursion.

This stage ranks the *universe* by similarity to that selection profile.
Rules 1–8 (SMC-style structure) are a secondary confluence, clearly labelled,
and do not dominate the board sort. Majors are scored for market context only.
"""
from __future__ import annotations

from typing import Any

from .universe import is_context_major, is_mega_major

# Weights sum to 100 — these are the discovery buckets, not the rulebook score.
W_NOT_MAJOR = 22
W_VOLATILITY = 20
W_VOLUME = 14
W_FRESHNESS = 12
W_RS = 10
W_COMPRESSION = 8
W_DISTANCE = 7
W_NARRATIVE = 7


def _f(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _pct_range(rows: list[dict], days: int) -> float | None:
    if not rows or days <= 0:
        return None
    window = rows[-days:] if len(rows) >= days else rows
    if not window:
        return None
    try:
        hi = max(float(r["h"]) for r in window)
        lo = min(float(r["l"]) for r in window)
        if lo <= 0:
            return None
        return (hi - lo) / lo * 100
    except (KeyError, TypeError, ValueError):
        return None


def _close(rows: list[dict], i: int = -1) -> float | None:
    if not rows or abs(i) > len(rows):
        return None
    try:
        return float(rows[i]["c"])
    except (KeyError, TypeError, ValueError):
        return None


def _ret(rows: list[dict], days: int) -> float | None:
    if not rows or len(rows) < days + 1:
        days = max(1, len(rows) - 1)
    a = _close(rows, -days - 1) if len(rows) > days else _close(rows, 0)
    b = _close(rows, -1)
    if not a or not b or a == 0:
        return None
    return (b - a) / a * 100


def _avg_vol(rows: list[dict], days: int) -> float | None:
    window = rows[-days:] if rows else []
    if not window:
        return None
    vals = []
    for r in window:
        v = _f(r.get("q") or r.get("quote_vol") or r.get("v") or r.get("volume"))
        if v is not None:
            vals.append(v)
    return sum(vals) / len(vals) if vals else None


def _atr_pct(rows: list[dict], n: int = 14) -> float | None:
    if not rows or len(rows) < n + 1:
        return _pct_range(rows, n or 7)
    trs = []
    for i in range(-n, 0):
        try:
            h = float(rows[i]["h"])
            l = float(rows[i]["l"])
            prev = float(rows[i - 1]["c"])
            tr = max(h - l, abs(h - prev), abs(l - prev))
            if rows[i]["c"] and float(rows[i]["c"]) > 0:
                trs.append(tr / float(rows[i]["c"]) * 100)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return sum(trs) / len(trs) if trs else None


def _dist_from_high(rows: list[dict], days: int = 30) -> float | None:
    window = rows[-days:] if rows else []
    if not window:
        return None
    try:
        hi = max(float(r["h"]) for r in window)
        last = float(window[-1]["c"])
        if hi <= 0:
            return None
        return (last / hi - 1) * 100  # 0 at the high, negative if below
    except (KeyError, TypeError, ValueError):
        return None


def _consolidation_score(d1: list[dict]) -> tuple[float, str]:
    """Higher when a quieter mid-window precedes a recent expansion — his breakout tape."""
    if not d1 or len(d1) < 20:
        return 0.0, "Not enough daily history to judge consolidation"
    mid = _atr_pct(d1[:-5], 14) if len(d1) > 20 else _atr_pct(d1[:-3], 10)
    recent = _atr_pct(d1, 5) or _pct_range(d1, 5)
    if mid is None or recent is None or mid <= 0:
        return 0.0, "Consolidation not measurable"
    ratio = recent / mid
    if mid < 8 and ratio >= 1.4:
        return 8.0, f"Quiet mid-window (ATR {mid:.1f}%) then expansion ({recent:.1f}%)"
    if mid < 12 and ratio >= 1.2:
        return 6.0, f"Compression then a pickup (ATR {mid:.1f}% → {recent:.1f}%)"
    if mid < 10:
        return 4.0, f"Still compressed (mid ATR {mid:.1f}%) — waiting for the break"
    if mid > 25 and recent > 30:
        return 2.0, f"Already a wide, noisy tape (ATR {recent:.1f}%)"
    return 3.0, f"Average structure (mid ATR {mid:.1f}%, recent {recent:.1f}%)"


NARRATIVE_CATS = {
    "meme-token", "memes", "ai-meme", "ai-agents", "artificial-intelligence",
    "gaming", "gamefi", "layer-2", "layer-1", "solana-ecosystem", "base-ecosystem",
    "desci", "depin", "restaking", "modular-blockchain", "inscriptions",
    "dog-themed-coins", "cat-themed-coins", "political-memes", "animal-memes",
    "pump-fun", "launchpad",
}


def extract_features(
    *,
    coin: str,
    d1: list[dict],
    h4: list[dict] | None = None,
    ticker: dict | None = None,
    btc_d1: list[dict] | None = None,
    launch_ms: int | float | None = None,
    now_ms: int | None = None,
    mcap_rank: int | None = None,
    market_cap: float | None = None,
    categories: list[str] | None = None,
    has_unlock_headline: bool = False,
    has_catalyst_headline: bool = False,
    quote_vol_24h: float | None = None,
) -> dict[str, Any]:
    import time as _time

    t = ticker or {}
    qvol = quote_vol_24h if quote_vol_24h is not None else _f(t.get("quoteVol") or t.get("quote_vol"))
    rng_24 = _pct_range(d1, 1) or _pct_range(h4 or [], 6)
    rng_7 = _pct_range(d1, 7)
    rng_30 = _pct_range(d1, 30)
    atr14 = _atr_pct(d1, 14)
    ret_7 = _ret(d1, 7)
    ret_14 = _ret(d1, 14)
    btc_7 = _ret(btc_d1 or [], 7)
    rs_7 = None
    if ret_7 is not None and btc_7 is not None:
        rs_7 = ret_7 - btc_7
    vol_20 = _avg_vol(d1, 20)
    vol_3 = _avg_vol(d1, 3)
    vol_spike = (vol_3 / vol_20) if vol_20 and vol_3 else None
    dist_30 = _dist_from_high(d1, 30)
    dist_90 = _dist_from_high(d1, 90) if d1 and len(d1) >= 20 else dist_30
    age_days = None
    if launch_ms:
        try:
            ms = now_ms or int(_time.time() * 1000)
            age_days = max(0, (ms - int(launch_ms)) / 86400000)
        except (TypeError, ValueError):
            age_days = None
    consol_pts, consol_note = _consolidation_score(d1)
    return {
        "coin": coin.upper(),
        "is_mega_major": is_mega_major(coin),
        "is_context_major": is_context_major(coin),
        "quote_vol_24h": qvol,
        "range_24h_pct": rng_24,
        "range_7d_pct": rng_7,
        "range_30d_pct": rng_30,
        "atr14_pct": atr14,
        "ret_7d_pct": ret_7,
        "ret_14d_pct": ret_14,
        "btc_ret_7d_pct": btc_7,
        "rs_vs_btc_7d_pct": rs_7,
        "volume_spike": vol_spike,
        "dist_30d_high_pct": dist_30,
        "dist_90d_high_pct": dist_90,
        "age_days": age_days,
        "mcap_rank": mcap_rank,
        "market_cap": market_cap,
        "categories": list(categories or []),
        "has_unlock_headline": bool(has_unlock_headline),
        "has_catalyst_headline": bool(has_catalyst_headline),
        "consol_pts": consol_pts,
        "consol_note": consol_note,
    }


def score_discovery(feat: dict[str, Any]) -> tuple[int, dict[str, int], list[str]]:
    """Return (score 0–100, bucket points, notes)."""
    pts: dict[str, int] = {}
    notes: list[str] = []
    coin = feat.get("coin") or ""

    # 1. Not a mega-major — this is the main board filter's scoring twin.
    if feat.get("is_mega_major"):
        pts["not_major"] = 0
        notes.append("Mega-major (BTC/ETH/XRP/BNB/SOL) — market context, not a discovery name")
    elif feat.get("is_context_major"):
        pts["not_major"] = 5
        notes.append("Large-cap major — usually context, rarely how he hunts new setups")
    elif feat.get("mcap_rank") and feat["mcap_rank"] <= 15:
        pts["not_major"] = 8
        notes.append(f"CoinGecko rank #{feat['mcap_rank']} — large cap, weaker discovery fit")
    elif feat.get("mcap_rank") and feat["mcap_rank"] <= 40:
        pts["not_major"] = 14
        notes.append(f"Mid-large cap (rank #{feat['mcap_rank']})")
    else:
        pts["not_major"] = W_NOT_MAJOR
        if feat.get("mcap_rank"):
            notes.append(f"Long-tail / mid-small cap (rank #{feat['mcap_rank']}) — matches his typical book")
        else:
            notes.append("Not a mega-major — fits the long-tail names he actually journals")

    # 2. Volatility — sweet spot around his 20%/day filter; dead tape scores poorly.
    vola = feat.get("range_7d_pct") or feat.get("atr14_pct") or feat.get("range_24h_pct") or 0
    r24 = feat.get("range_24h_pct") or 0
    r7 = feat.get("range_7d_pct") or 0
    if 15 <= r7 <= 55 or 12 <= r24 <= 35:
        pts["volatility"] = W_VOLATILITY
        notes.append(f"High-beta range (24h {r24:.1f}%, 7d {r7:.1f}%) — in his ~20% move band")
    elif 10 <= r7 <= 70 or 8 <= r24 <= 45:
        pts["volatility"] = 14
        notes.append(f"Usable volatility (24h {r24:.1f}%, 7d {r7:.1f}%)")
    elif r7 >= 80 or r24 >= 50:
        pts["volatility"] = 8
        notes.append(f"Extreme range (24h {r24:.1f}%, 7d {r7:.1f}%) — mover, but often already stretched")
    elif r7 < 6 and r24 < 5:
        pts["volatility"] = 0
        notes.append("Dead / muted range — he usually stands down")
    else:
        pts["volatility"] = 6
        notes.append(f"Moderate range (24h {r24:.1f}%, 7d {r7:.1f}%)")

    # 3. Volume: tradeable but not BTC-tape; reward spikes vs the 20-day average.
    q = feat.get("quote_vol_24h") or 0
    spike = feat.get("volume_spike")
    vpts = 0
    if 2_000_000 <= q <= 120_000_000:
        vpts += 8
        notes.append(f"24h turnover ${q/1e6:.1f}M — liquid enough without being a major")
    elif 1_500_000 <= q < 2_000_000:
        vpts += 5
        notes.append(f"24h turnover ${q/1e6:.1f}M — tradable but thin")
    elif q > 120_000_000:
        vpts += 4
        notes.append(f"Very large turnover ${q/1e6:.0f}M — more major-like")
    else:
        notes.append("Turnover is thin")
    if spike and 1.6 <= spike <= 8:
        vpts += 6
        notes.append(f"Volume spike {spike:.1f}× the 20-day average")
    elif spike and spike >= 8:
        vpts += 4
        notes.append(f"Huge volume spike ({spike:.1f}×) — attention is already here")
    elif spike and 1.2 <= spike < 1.6:
        vpts += 3
        notes.append(f"Volume picking up ({spike:.1f}×)")
    pts["volume"] = min(W_VOLUME, vpts)

    # 4. Freshness — newer Bitunix listings / younger names score higher.
    # Venue-open majors all share ~Jun 2024 launchTime, so age alone is weak
    # for BTC; the not-major bucket already handles that.
    age = feat.get("age_days")
    if age is None:
        pts["freshness"] = 4
        notes.append("Listing age unknown")
    elif age <= 90:
        pts["freshness"] = W_FRESHNESS
        notes.append(f"Very new listing (~{age:.0f}d on Bitunix)")
    elif age <= 220:
        pts["freshness"] = 9
        notes.append(f"Relatively new listing (~{age:.0f}d)")
    elif age <= 450:
        pts["freshness"] = 6
        notes.append(f"Listing age ~{age:.0f}d — still in the newer-name band of the 2025–26 book")
    else:
        pts["freshness"] = 2
        notes.append(f"Older listing (~{age:.0f}d)")

    # 5. Relative strength vs BTC — he hunts names that actually move vs the benchmark.
    rs = feat.get("rs_vs_btc_7d_pct")
    if rs is None:
        pts["rs"] = 3
    else:
        ars = abs(rs)
        if ars >= 15:
            pts["rs"] = W_RS
            notes.append(f"Strong 7d relative move vs BTC ({rs:+.1f} pp)")
        elif ars >= 8:
            pts["rs"] = 7
            notes.append(f"Clear 7d relative move vs BTC ({rs:+.1f} pp)")
        elif ars >= 3:
            pts["rs"] = 4
            notes.append(f"Mild 7d relative move vs BTC ({rs:+.1f} pp)")
        else:
            pts["rs"] = 1
            notes.append(f"Tracking BTC ({rs:+.1f} pp) — less of an independent tape")

    # 6. Prior consolidation / compression-expansion.
    pts["compression"] = int(round(min(W_COMPRESSION, feat.get("consol_pts") or 0)))
    if feat.get("consol_note"):
        notes.append(feat["consol_note"])

    # 7. Distance from 30d high — breakout-close is interesting; already-extended is not.
    dist = feat.get("dist_30d_high_pct")
    if dist is None:
        pts["distance"] = 3
    elif -4 <= dist <= 0.5:
        pts["distance"] = W_DISTANCE
        notes.append(f"Pressed into the 30d high ({dist:.1f}%) — breakout-style location")
    elif -12 <= dist < -4:
        pts["distance"] = 5
        notes.append(f"{dist:.1f}% under the 30d high — in range, not a chase")
    elif dist < -25:
        pts["distance"] = 3
        notes.append(f"Well off the 30d high ({dist:.1f}%) — deep discount / weak, needs a trigger")
    elif dist > 0.5:
        pts["distance"] = 1
        notes.append("Holding a new high — often late if you are chasing")
    else:
        pts["distance"] = 3

    # 8. Narrative / catalyst (categories + unlock/news flags). Low weight; needs judgement.
    npts = 0
    cats = [str(c).lower() for c in (feat.get("categories") or [])]
    if any(any(k in c for k in ("meme", "ai", "game", "depin", "layer", "solana", "pump", "inscription", "agent")) for c in cats):
        npts += 4
        notes.append("Narrative/sector tag matches the high-beta themes he often writes about")
    if feat.get("has_unlock_headline"):
        npts += 2
        notes.append("Unlock / vesting language in recent headlines (catalyst — still needs judgement)")
    if feat.get("has_catalyst_headline"):
        npts += 2
        notes.append("Catalyst-like headline present")
    if not cats and not feat.get("has_unlock_headline") and not feat.get("has_catalyst_headline"):
        notes.append("No automated narrative/catalyst tag")
    pts["narrative"] = min(W_NARRATIVE, npts)

    total = int(round(sum(pts.values())))
    return max(0, min(100, total)), pts, notes


def board_rank(discovery: int, setup_score: int) -> float:
    """Discovery leads; rule-based setup quality is a secondary confluence (30%)."""
    return 0.70 * (discovery or 0) + 0.30 * (setup_score or 0)


def attach_discovery(
    setup: dict[str, Any],
    feat: dict[str, Any],
    score: int,
    buckets: dict[str, int],
    notes: list[str],
) -> dict[str, Any]:
    setup["discovery_score"] = score
    setup["discovery_buckets"] = buckets
    setup["discovery_notes"] = notes
    setup["discovery_features"] = {
        k: feat.get(k)
        for k in (
            "range_24h_pct", "range_7d_pct", "atr14_pct", "quote_vol_24h",
            "volume_spike", "rs_vs_btc_7d_pct", "age_days", "mcap_rank",
            "dist_30d_high_pct", "is_mega_major", "is_context_major",
        )
    }
    setup["board_rank"] = round(board_rank(score, setup.get("score") or 0), 2)
    setup["is_context_major"] = bool(feat.get("is_context_major"))
    return setup
