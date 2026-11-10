"""Free fundamental / catalyst sources. All clearly labelled as automated, still needing judgement."""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

GECKO = "https://api.coingecko.com/api/v3"
FF_CAL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_RSS = "https://news.google.com/rss/search"


class Fundamentals:
    def __init__(self):
        self._http = httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": "HoodySetupScanner/1.0 (research; local journal scanner)"},
            follow_redirects=True,
        )
        self._markets: dict[str, dict] = {}
        self._macro: dict[str, Any] = {}
        self._news_cache: dict[str, list] = {}

    async def aclose(self):
        await self._http.aclose()

    async def preload_markets(self) -> None:
        """Cache first ~750 coins by USD market cap for symbol lookup."""
        pages = []
        for page in (1, 2, 3):
            try:
                r = await self._http.get(
                    f"{GECKO}/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "order": "market_cap_desc",
                        "per_page": 250,
                        "page": page,
                        "sparkline": "false",
                    },
                )
                if r.status_code == 429:
                    await asyncio.sleep(2)
                    continue
                if r.status_code != 200:
                    break
                pages.extend(r.json() or [])
                await asyncio.sleep(0.4)
            except httpx.HTTPError:
                break
        by_sym: dict[str, dict] = {}
        for c in pages:
            sym = (c.get("symbol") or "").upper()
            if not sym:
                continue
            # Prefer the higher-ranked (earlier) listing
            if sym not in by_sym:
                by_sym[sym] = c
        self._markets = by_sym

    async def load_macro(self) -> dict:
        events = []
        try:
            r = await self._http.get(FF_CAL)
            if r.status_code == 200:
                raw = r.json()
                if isinstance(raw, list):
                    events = raw
        except httpx.HTTPError:
            events = []
        now = dt.datetime.now(dt.timezone.utc)
        high_usd = []
        for e in events:
            title = e.get("title") or ""
            country = (e.get("country") or "").upper()
            impact = (e.get("impact") or "").lower()
            if country not in ("USD", "UNITED STATES", "US"):
                # FOMC/CPI sometimes tagged USD
                if not re.search(r"CPI|FOMC|Federal Reserve|PCE|NFP|Non-Farm|Interest Rate", title, re.I):
                    continue
            if impact not in ("high", "medium"):
                if not re.search(r"CPI|FOMC|Federal Funds|Interest Rate Decision|NFP|Non-Farm|PCE", title, re.I):
                    continue
            try:
                when = dt.datetime.fromisoformat(e["date"])
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
                else:
                    when = when.astimezone(dt.timezone.utc)
            except Exception:
                continue
            high_usd.append(
                {
                    "title": title,
                    "date": when.isoformat(),
                    "impact": e.get("impact"),
                    "forecast": e.get("forecast") or "",
                    "previous": e.get("previous") or "",
                    "actual": e.get("actual") or "",
                    "country": country or "USD",
                }
            )
        high_usd.sort(key=lambda x: x["date"])
        upcoming = [e for e in high_usd if dt.datetime.fromisoformat(e["date"]) >= now - dt.timedelta(hours=2)]
        recent = [e for e in high_usd if dt.datetime.fromisoformat(e["date"]) < now - dt.timedelta(hours=2)]
        stance = "neutral"
        # If a high-impact CPI/FOMC is within 48h, flag event risk
        next_high = upcoming[0] if upcoming else None
        if next_high:
            t = dt.datetime.fromisoformat(next_high["date"])
            hours = (t - now).total_seconds() / 3600
            if hours <= 48 and re.search(r"CPI|FOMC|Interest Rate|PCE|NFP", next_high["title"], re.I):
                stance = "event_risk"
        # If CPI just printed and actual > forecast (hot), lean risk_off — only when actual exists
        for e in reversed(recent[-6:]):
            if re.search(r"CPI|Core CPI", e["title"], re.I) and e.get("actual") and e.get("forecast"):
                if _num(e["actual"]) is not None and _num(e["forecast"]) is not None:
                    if _num(e["actual"]) > _num(e["forecast"]):
                        stance = "risk_off"
                    elif _num(e["actual"]) < _num(e["forecast"]):
                        stance = "risk_on"
                break
        next_label = None
        if next_high:
            t = dt.datetime.fromisoformat(next_high["date"]).astimezone(dt.timezone(dt.timedelta(hours=3)))
            next_label = f"{next_high['title']} — {t.strftime('%d %b %Y, %H:%M')} Asia/Qatar"
        self._macro = {
            "stance": stance,
            "next_high": next_label,
            "upcoming": upcoming[:8],
            "recent": recent[-6:],
            "source": "Forex Factory this-week JSON (unofficial, free). Automated, verify times.",
        }
        return self._macro

    def macro(self) -> dict:
        return self._macro or {"stance": "neutral", "upcoming": [], "recent": [], "next_high": None}

    def coin_market(self, symbol: str) -> dict | None:
        s = symbol.upper().replace("USDT", "")
        # 1000PEPE -> try PEPE
        raw = s
        if s.startswith("1000") and len(s) > 5:
            s = s[4:]
        if s.startswith("10000") and len(s) > 6:
            s = s[5:]
        return self._markets.get(s) or self._markets.get(raw)

    async def news_for(self, coin: str, name: str | None = None) -> list[dict]:
        key = coin.upper()
        if key in self._news_cache:
            return self._news_cache[key]
        q = f"{coin} crypto OR {coin} token"
        if name:
            q = f"\"{name}\" OR {coin} crypto token unlock"
        items: list[dict] = []
        try:
            r = await self._http.get(NEWS_RSS, params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
            if r.status_code == 200 and r.content:
                items = _parse_rss(r.text, limit=5)
        except httpx.HTTPError:
            items = []
        self._news_cache[key] = items
        return items

    async def enrich(self, coin: str, macro: dict) -> dict:
        mkt = self.coin_market(coin)
        name = (mkt or {}).get("name")
        news = await self.news_for(coin, name)
        unlock_hit = None
        for n in news:
            if re.search(r"unlock|vesting|cliff|token release|airdrop", n.get("title") or "", re.I):
                unlock_hit = {"headline": n["title"], "url": n.get("link"), "source": "Google News RSS (keyword match)"}
                break
        cats = []
        # markets endpoint has no categories; we only have name/mcap. Optional second call is rate-limited —
        # skip per-coin gecko detail during a bulk scan unless we already know the id and want one field.
        fa = {
            "source": "CoinGecko public API + Google News RSS + Forex Factory calendar",
            "found": bool(mkt),
            "id": (mkt or {}).get("id"),
            "name": name,
            "image": (mkt or {}).get("image"),
            "market_cap": (mkt or {}).get("market_cap"),
            "market_cap_rank": (mkt or {}).get("market_cap_rank"),
            "ath": (mkt or {}).get("ath"),
            "ath_change_pct": (mkt or {}).get("ath_change_percentage"),
            "atl": (mkt or {}).get("atl"),
            "price_change_7d": (mkt or {}).get("price_change_percentage_7d_in_currency")
            or (mkt or {}).get("price_change_percentage_7d"),
            "price_change_24h": (mkt or {}).get("price_change_percentage_24h"),
            "categories": cats,  # filled only if we later attach /coins/{id}
            "news": news,
            "unlock": unlock_hit,
            "macro": {
                "stance": macro.get("stance"),
                "next_high": macro.get("next_high"),
            },
            "automated": True,
            "needs_judgement": True,
            "notes": "Market-cap and news are automated. Categories, unlock size, and whether a headline is actually material still need judgement. There is no free official Bitunix open-interest feed.",
        }
        return fa

    async def attach_categories(self, fa: dict) -> dict:
        cid = fa.get("id")
        if not cid:
            return fa
        try:
            r = await self._http.get(f"{GECKO}/coins/{cid}", params={"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false", "sparkline": "false"})
            if r.status_code == 200:
                js = r.json()
                fa["categories"] = [c for c in (js.get("categories") or []) if c][:6]
                # some coins list "upcoming" in description only — leave unlocks to news
        except httpx.HTTPError:
            pass
        return fa


def _parse_rss(text: str, limit: int = 5) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return items
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title:
            items.append({"title": title, "link": link, "published": pub})
        if len(items) >= limit:
            break
    return items


def _num(s: str) -> float | None:
    try:
        return float(re.sub(r"[^\d.\-]", "", s))
    except (TypeError, ValueError):
        return None
