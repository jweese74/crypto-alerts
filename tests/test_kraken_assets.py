"""
Tests for Kraken asset discovery logic.
These tests do NOT require a database or running Kraken API.
"""
import pytest

from app.services.kraken_assets import (
    KrakenAssetsService,
    KrakenUsdPair,
    _FALLBACK_PAIRS,
    _normalize_base,
    _USD_QUOTES,
)


class TestNormalizeBase:
    def test_known_alias_xxbt(self):
        assert _normalize_base("XXBT") == "BTC"

    def test_known_alias_xeth(self):
        assert _normalize_base("XETH") == "ETH"

    def test_known_alias_xxrp(self):
        assert _normalize_base("XXRP") == "XRP"

    def test_four_char_x_prefix(self):
        # XSOL → SOL (X + 3-char code)
        assert _normalize_base("XSOL") == "SOL"

    def test_passthrough_plain(self):
        assert _normalize_base("SOL") == "SOL"

    def test_passthrough_ada(self):
        assert _normalize_base("ADA") == "ADA"


class TestUsdQuotesFilter:
    def test_zusd_is_usd(self):
        assert "ZUSD" in _USD_QUOTES

    def test_usd_is_usd(self):
        assert "USD" in _USD_QUOTES

    def test_eur_not_usd(self):
        assert "ZEUR" not in _USD_QUOTES

    def test_btc_not_usd_quote(self):
        assert "XXBT" not in _USD_QUOTES


class TestFallbackPairs:
    def test_fallback_includes_btc(self):
        symbols = [p.symbol for p in _FALLBACK_PAIRS]
        assert "BTC/USD" in symbols

    def test_fallback_includes_eth(self):
        symbols = [p.symbol for p in _FALLBACK_PAIRS]
        assert "ETH/USD" in symbols

    def test_fallback_includes_existing_pairs(self):
        """Existing rules for TAO and FET must still work."""
        symbols = [p.symbol for p in _FALLBACK_PAIRS]
        assert "TAO/USD" in symbols
        assert "FET/USD" in symbols

    def test_fallback_all_end_with_usd(self):
        for p in _FALLBACK_PAIRS:
            assert p.symbol.endswith("/USD"), f"{p.symbol} must end with /USD"

    def test_fallback_query_names_nonempty(self):
        for p in _FALLBACK_PAIRS:
            assert p.query_name, f"{p.symbol} missing query_name"


class TestKrakenAssetsServiceFallback:
    """Tests that use the fallback path (no network)."""

    def _service_with_fallback(self) -> KrakenAssetsService:
        svc = KrakenAssetsService()
        svc._pairs = list(_FALLBACK_PAIRS)
        svc._by_symbol = {p.symbol: p for p in svc._pairs}
        svc._using_fallback = True
        return svc

    def test_validate_known_symbol(self):
        svc = self._service_with_fallback()
        assert svc.validate_symbol("BTC/USD") is True

    def test_validate_unknown_symbol(self):
        svc = self._service_with_fallback()
        assert svc.validate_symbol("FAKECOIN/USD") is False

    def test_get_pair_returns_pair(self):
        svc = self._service_with_fallback()
        pair = svc.get_pair("BTC/USD")
        assert pair is not None
        assert pair.symbol == "BTC/USD"
        assert pair.base == "BTC"

    def test_get_pair_unknown_returns_none(self):
        svc = self._service_with_fallback()
        assert svc.get_pair("NOTREAL/USD") is None

    def test_get_all_usd_pairs_returns_list(self):
        svc = self._service_with_fallback()
        pairs = svc.get_all_usd_pairs()
        assert len(pairs) > 0
        assert all(p.symbol.endswith("/USD") for p in pairs)

    def test_get_symbols(self):
        svc = self._service_with_fallback()
        symbols = svc.get_symbols()
        assert "BTC/USD" in symbols
        assert "ETH/USD" in symbols

    def test_is_using_fallback(self):
        svc = self._service_with_fallback()
        assert svc.is_using_fallback is True

    def test_get_query_name_btc(self):
        svc = self._service_with_fallback()
        assert svc.get_query_name("BTC/USD") == "XBTUSD"

    def test_get_result_key_btc(self):
        svc = self._service_with_fallback()
        assert svc.get_result_key("BTC/USD") == "XXBTZUSD"


class TestAssetPairParsing:
    """Test the internal _fetch_from_kraken parsing logic via mocked data."""

    def _make_service(self) -> KrakenAssetsService:
        return KrakenAssetsService()

    def test_parse_mock_response(self):
        """Simulate what _fetch_from_kraken does with a mock AssetPairs response."""
        svc = self._make_service()
        mock_result = {
            "XXBTZUSD": {
                "altname": "XBTUSD",
                "wsname": "XBT/USD",
                "base": "XXBT",
                "quote": "ZUSD",
                "status": "online",
            },
            "XETHZUSD": {
                "altname": "ETHUSD",
                "wsname": "ETH/USD",
                "base": "XETH",
                "quote": "ZUSD",
                "status": "online",
            },
            "SOLUSD": {
                "altname": "SOLUSD",
                "wsname": "SOL/USD",
                "base": "SOL",
                "quote": "USD",
                "status": "online",
            },
            "XXBTZEUR": {
                "altname": "XBTEUR",
                "wsname": "XBT/EUR",
                "base": "XXBT",
                "quote": "ZEUR",
                "status": "online",
            },
            "XXBTZUSD.d": {  # Dark pool — should be excluded
                "altname": "XBTUSD.d",
                "wsname": "XBT/USD",
                "base": "XXBT",
                "quote": "ZUSD",
                "status": "online",
            },
        }
        import asyncio

        async def run():
            import httpx
            from unittest.mock import AsyncMock, MagicMock, patch
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"error": [], "result": mock_result}
            mock_resp.raise_for_status = MagicMock()
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.get = AsyncMock(return_value=mock_resp)
                mock_client_class.return_value = mock_client
                return await svc._fetch_from_kraken()

        pairs = asyncio.run(run())
        symbols = [p.symbol for p in pairs]

        assert "BTC/USD" in symbols
        assert "ETH/USD" in symbols
        assert "SOL/USD" in symbols
        # EUR pair filtered out
        assert "BTC/EUR" not in symbols
        # Dark pool filtered out
        assert len([p for p in pairs if ".d" in p.result_key]) == 0

    def test_backward_compat_old_rules(self):
        """Existing BTC/USD, ETH/USD, TAO/USD, FET/USD rules must still validate."""
        svc = self._make_service()
        svc._pairs = list(_FALLBACK_PAIRS)
        svc._by_symbol = {p.symbol: p for p in svc._pairs}
        for symbol in ["BTC/USD", "ETH/USD", "TAO/USD", "FET/USD"]:
            assert svc.validate_symbol(symbol), f"Backward compat: {symbol} must be valid"
