"""
Market data service — Kraken public REST API.
"""
from typing import Optional
import httpx
from app.core.config import get_settings
from app.core.logging import logger
from datetime import datetime, timezone

settings = get_settings()

_KNOWN_RESULT_KEYS: dict[str, str] = {
    "XBTUSD": "XXBTZUSD",
    "ETHUSD": "XETHZUSD",
    "XRPUSD": "XXRPZUSD",
    "LTCUSD": "XLTCZUSD",
    "XDGUSD": "XDGUSD",
}


def _find_result_price(
    result: dict, kraken_name: str, result_key: Optional[str] = None
) -> Optional[float]:
    """
    Locate the price inside a Kraken Ticker result dict.
    Tries result_key first (exact, from AssetPairs discovery), then
    known aliases, then substring fallback.
    """
    if result_key and result_key in result:
        return float(result[result_key]["c"][0])

    candidates = [
        kraken_name,
        _KNOWN_RESULT_KEYS.get(kraken_name, ""),
        f"X{kraken_name}",
        f"X{kraken_name[:3]}Z{kraken_name[3:]}",
    ]
    for key in candidates:
        if key and key in result:
            return float(result[key]["c"][0])

    base = kraken_name[:-3] if len(kraken_name) >= 6 else kraken_name
    for key, ticker in result.items():
        if base in key:
            return float(ticker["c"][0])

    return None


class MarketDataService:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._last_prices: dict[str, float] = {}
        self._last_poll_time: datetime | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.KRAKEN_API_BASE_URL,
            timeout=15.0,
            headers={"User-Agent": "crypto-alert-system/0.1"},
        )
        logger.info("MarketDataService started")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
        logger.info("MarketDataService stopped")

    async def fetch_prices(self, pairs: Optional[list[str]] = None) -> dict[str, float]:
        if not self._client:
            raise RuntimeError("MarketDataService not started")

        from app.services.kraken_assets import kraken_assets_service

        requested = pairs if pairs is not None else kraken_assets_service.get_symbols()
        if not requested:
            return {}

        pair_tuples: list[tuple[str, str, Optional[str]]] = []
        for symbol in requested:
            pair_info = kraken_assets_service.get_pair(symbol)
            if pair_info:
                pair_tuples.append((symbol, pair_info.query_name, pair_info.result_key))
            else:
                query = symbol.replace("/", "").replace("BTC", "XBT")
                pair_tuples.append((symbol, query, None))

        query_str = ",".join(dict.fromkeys(q for _, q, _ in pair_tuples))
        logger.debug(f"Fetching Kraken ticker: {query_str}")

        response = await self._client.get("/Ticker", params={"pair": query_str})
        response.raise_for_status()

        data = response.json()
        if data.get("error"):
            raise ValueError(f"Kraken API error: {data['error']}")

        result = data["result"]
        out: dict[str, float] = {}

        for symbol, query_name, result_key in pair_tuples:
            price = _find_result_price(result, query_name, result_key)
            if price is not None:
                out[symbol] = price
                self._last_prices[symbol] = price
            else:
                logger.warning(f"No price found for {symbol} (query={query_name})")
                if symbol in self._last_prices:
                    out[symbol] = self._last_prices[symbol]

        logger.debug(f"Prices fetched: { {k: f'${v:,.2f}' for k, v in out.items()} }")
        self._last_poll_time = datetime.now(timezone.utc)
        return out

    @property
    def last_poll_time(self) -> datetime | None:
        return self._last_poll_time

    @property
    def last_prices(self) -> dict[str, float]:
        return dict(self._last_prices)

    def get_supported_pairs(self) -> list[str]:
        from app.services.kraken_assets import kraken_assets_service
        return kraken_assets_service.get_symbols()


market_data_service = MarketDataService()
