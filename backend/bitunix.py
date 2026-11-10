"""Bitunix public futures REST client. No auth."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

BASE = "https://fapi.bitunix.com"
# Stay under the documented 10 req/sec/ip limit.
MAX_CONCURRENCY = 6


class BitunixError(RuntimeError):
    pass


class Bitunix:
    def __init__(self, timeout: float = 20.0):
        self._client = httpx.AsyncClient(
            base_url=BASE,
            timeout=timeout,
            headers={
                "User-Agent": "HoodySetupScanner/1.0 (research; +local)",
                "Accept": "application/json",
            },
        )
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def aclose(self):
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None, retries: int = 4) -> Any:
        last = None
        for i in range(retries):
            async with self._sem:
                try:
                    r = await self._client.get(path, params=params)
                    if r.status_code in (429, 500, 502, 503):
                        await asyncio.sleep(0.6 * (i + 1))
                        last = r.status_code
                        continue
                    r.raise_for_status()
                    js = r.json()
                    if js.get("code") not in (0, "0", None) and "data" not in js:
                        raise BitunixError(f"{path}: {js}")
                    if isinstance(js, dict) and js.get("code") not in (0, "0", None) and js.get("data") is None:
                        raise BitunixError(f"{path}: {js}")
                    return js.get("data", js)
                except (httpx.HTTPError, BitunixError) as e:
                    last = e
                    await asyncio.sleep(0.5 * (i + 1))
        raise BitunixError(f"GET {path} failed: {last}")

    async def tickers(self) -> list[dict]:
        data = await self._get("/api/v1/futures/market/tickers")
        return data or []

    async def funding_batch(self) -> list[dict]:
        data = await self._get("/api/v1/futures/market/funding_rate/batch")
        return data or []

    async def trading_pairs(self) -> list[dict]:
        data = await self._get("/api/v1/futures/market/trading_pairs")
        return data or []

    async def klines(self, symbol: str, interval: str = "1h", limit: int = 200, end_time: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": min(int(limit), 200)}
        if end_time:
            params["endTime"] = int(end_time)
        data = await self._get("/api/v1/futures/market/kline", params)
        rows = data or []
        out = []
        for x in rows:
            out.append(
                {
                    "t": int(x["time"]),
                    "o": float(x["open"]),
                    "h": float(x["high"]),
                    "l": float(x["low"]),
                    "c": float(x["close"]),
                    "v": float(x.get("baseVol") or 0),
                    "q": float(x.get("quoteVol") or 0),
                }
            )
        out.sort(key=lambda z: z["t"])
        return out

    async def depth(self, symbol: str, limit: str = "15") -> dict:
        data = await self._get("/api/v1/futures/market/depth", {"symbol": symbol, "limit": limit})
        return data or {"asks": [], "bids": []}
