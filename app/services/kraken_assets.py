"""
Kraken asset discovery service.
Fetches all USD pairs from Kraken /0/public/AssetPairs, normalises,
caches with TTL, and provides a clean API for the rest of the app.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.core.logging import logger


@dataclass(frozen=True)
class KrakenUsdPair:
    symbol: str       # Display: "BTC/USD"
    base: str         # Base asset display name: "BTC"
    query_name: str   # Kraken altname for Ticker query: "XBTUSD"
    result_key: str   # Kraken pair key in Ticker response: "XXBTZUSD"
    ws_name: str      # WebSocket name: "XBT/USD"


_BASE_ALIASES: dict[str, str] = {
    "XXBT": "BTC", "XETH": "ETH", "XLTC": "LTC",
    "XXRP": "XRP", "XXLM": "XLM", "XZEC": "ZEC",
    "XREP": "REP", "XICN": "ICN", "XMLN": "MLN",
    "XGNO": "GNO", "XXDG": "DOGE",
}

_USD_QUOTES = frozenset({"ZUSD", "USD"})

_FALLBACK_PAIRS = [
    KrakenUsdPair("BTC/USD", "BTC", "XBTUSD", "XXBTZUSD", "XBT/USD"),
    KrakenUsdPair("ETH/USD", "ETH", "ETHUSD", "XETHZUSD", "ETH/USD"),
    KrakenUsdPair("SOL/USD", "SOL", "SOLUSD", "SOLUSD", "SOL/USD"),
    KrakenUsdPair("ADA/USD", "ADA", "ADAUSD", "ADAUSD", "ADA/USD"),
    KrakenUsdPair("XRP/USD", "XRP", "XRPUSD", "XXRPZUSD", "XRP/USD"),
    KrakenUsdPair("DOGE/USD", "DOGE", "XDGUSD", "XDGUSD", "XDG/USD"),
    KrakenUsdPair("TAO/USD", "TAO", "TAOUSD", "TAOUSD", "TAO/USD"),
    KrakenUsdPair("FET/USD", "FET", "FETUSD", "FETUSD", "FET/USD"),
    KrakenUsdPair("LTC/USD", "LTC", "LTCUSD", "XLTCZUSD", "LTC/USD"),
    KrakenUsdPair("DOT/USD", "DOT", "DOTUSD", "DOTUSD", "DOT/USD"),
]


def _normalize_base(raw: str) -> str:
    if raw in _BASE_ALIASES:
        return _BASE_ALIASES[raw]
    if len(raw) == 4 and raw[0] in ("X", "Z"):
        return raw[1:]
    if len(raw) == 5 and raw[0] in ("X", "Z"):
        return raw[1:]
    return raw


class KrakenAssetsService:
    def __init__(self, cache_hours: int = 6) -> None:
        self._cache_hours = cache_hours
        self._pairs: list[KrakenUsdPair] = []
        self._by_symbol: dict[str, KrakenUsdPair] = {}
        self._last_refresh: Optional[datetime] = None
        self._using_fallback: bool = False
        self._refresh_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self._refresh()
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        source = "fallback" if self._using_fallback else "Kraken API"
        logger.info(f"KrakenAssetsService started — {len(self._pairs)} USD pairs ({source})")

    async def stop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        logger.info("KrakenAssetsService stopped")

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._cache_hours * 3600)
            await self._refresh()

    async def _refresh(self) -> None:
        try:
            pairs = await self._fetch_from_kraken()
            if pairs:
                self._pairs = pairs
                self._by_symbol = {p.symbol: p for p in pairs}
                self._last_refresh = datetime.now(timezone.utc)
                self._using_fallback = False
                logger.info(f"KrakenAssetsService: refreshed — {len(pairs)} USD pairs")
            else:
                logger.warning("KrakenAssetsService: empty response, keeping cached data")
        except Exception as exc:
            logger.error(f"KrakenAssetsService: refresh failed — {exc}")
            if not self._pairs:
                logger.warning("KrakenAssetsService: using built-in fallback pair list")
                self._pairs = list(_FALLBACK_PAIRS)
                self._by_symbol = {p.symbol: p for p in self._pairs}
                self._using_fallback = True

    async def _fetch_from_kraken(self) -> list[KrakenUsdPair]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.kraken.com/0/public/AssetPairs",
                headers={"User-Agent": "crypto-alert-system/0.1"},
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("error"):
            raise ValueError(f"Kraken API error: {data['error']}")

        pairs: list[KrakenUsdPair] = []
        seen_symbols: set[str] = set()

        for result_key, info in data.get("result", {}).items():
            if ".d" in result_key or result_key.endswith(".s"):
                continue
            if info.get("status", "online") not in ("online", ""):
                continue
            quote = info.get("quote", "")
            if quote not in _USD_QUOTES:
                continue

            base_raw = info.get("base", "")
            base_display = _normalize_base(base_raw)
            symbol = f"{base_display}/USD"

            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)

            alt_name = info.get("altname", result_key)
            ws_name = info.get("wsname", f"{base_display}/USD")

            pairs.append(KrakenUsdPair(
                symbol=symbol,
                base=base_display,
                query_name=alt_name,
                result_key=result_key,
                ws_name=ws_name,
            ))

        return sorted(pairs, key=lambda p: p.symbol)

    def get_all_usd_pairs(self) -> list[KrakenUsdPair]:
        return list(self._pairs)

    def get_pair(self, symbol: str) -> Optional[KrakenUsdPair]:
        return self._by_symbol.get(symbol)

    def validate_symbol(self, symbol: str) -> bool:
        return symbol in self._by_symbol

    def get_symbols(self) -> list[str]:
        return [p.symbol for p in self._pairs]

    def get_query_name(self, symbol: str) -> Optional[str]:
        pair = self._by_symbol.get(symbol)
        return pair.query_name if pair else None

    def get_result_key(self, symbol: str) -> Optional[str]:
        pair = self._by_symbol.get(symbol)
        return pair.result_key if pair else None

    async def force_refresh(self) -> int:
        await self._refresh()
        return len(self._pairs)

    @property
    def last_refresh(self) -> Optional[datetime]:
        return self._last_refresh

    @property
    def pair_count(self) -> int:
        return len(self._pairs)

    @property
    def is_using_fallback(self) -> bool:
        return self._using_fallback


kraken_assets_service = KrakenAssetsService()
