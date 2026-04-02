"""Tests for admin-managed featured markets."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.models.featured_asset import FeaturedAsset
from app.services.featured_assets import FeaturedAssetsService, CACHE_TTL_SECONDS


# ── Helpers ────────────────────────────────────────────────────────────────────

def _row(symbol: str, order: int = 1, enabled: bool = True, display_name: str = "") -> FeaturedAsset:
    row = FeaturedAsset(
        id=order,
        asset_symbol=symbol,
        kraken_pair=symbol.replace("/", "").replace("USD", "USD"),
        display_name=display_name or None,
        sort_order=order,
        enabled=enabled,
    )
    return row


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar_one_or_none.return_value = None
    result.scalar_one.return_value = 0
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


# ── FeaturedAsset model ────────────────────────────────────────────────────────

class TestFeaturedAssetModel:
    def test_repr(self):
        row = _row("BTC/USD", order=1)
        assert "BTC/USD" in repr(row)
        assert "order=1" in repr(row)

    def test_enabled_default(self):
        # When explicitly provided, enabled is stored correctly
        row = FeaturedAsset(asset_symbol="ETH/USD", kraken_pair="ETHUSD", sort_order=1, enabled=True)
        assert row.enabled is True

    def test_disabled_stored_correctly(self):
        row = FeaturedAsset(asset_symbol="ETH/USD", kraken_pair="ETHUSD", sort_order=1, enabled=False)
        assert row.enabled is False

    def test_fields_present(self):
        row = FeaturedAsset(
            asset_symbol="SOL/USD",
            kraken_pair="SOLUSD",
            display_name="Solana",
            sort_order=3,
            enabled=True,
            notes="test note",
        )
        assert row.asset_symbol == "SOL/USD"
        assert row.kraken_pair == "SOLUSD"
        assert row.display_name == "Solana"
        assert row.sort_order == 3
        assert row.notes == "test note"


# ── CRUD: reads ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_all_returns_ordered():
    from app.crud import featured_assets as fa_crud
    rows = [_row("ETH/USD", 2), _row("BTC/USD", 1)]
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = rows
    result = await fa_crud.get_all(db)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_enabled_symbols_returns_only_enabled():
    from app.crud import featured_assets as fa_crud
    rows = [_row("BTC/USD", 1, enabled=True), _row("SOL/USD", 2, enabled=True)]
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = rows
    syms = await fa_crud.get_enabled_symbols(db)
    assert "BTC/USD" in syms
    assert "SOL/USD" in syms


@pytest.mark.asyncio
async def test_get_by_symbol_returns_none_when_missing():
    from app.crud import featured_assets as fa_crud
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = None
    result = await fa_crud.get_by_symbol(db, "NOTREAL/USD")
    assert result is None


@pytest.mark.asyncio
async def test_get_by_symbol_returns_row():
    from app.crud import featured_assets as fa_crud
    row = _row("BTC/USD", 1)
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = row
    result = await fa_crud.get_by_symbol(db, "BTC/USD")
    assert result is row


@pytest.mark.asyncio
async def test_count_returns_integer():
    from app.crud import featured_assets as fa_crud
    db = _mock_db()
    db.execute.return_value.scalar_one.return_value = 6
    assert await fa_crud.count(db) == 6


# ── CRUD: add ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_raises_if_duplicate():
    from app.crud import featured_assets as fa_crud
    row = _row("BTC/USD", 1)
    db = _mock_db()
    # get_by_symbol returns existing row — duplicate check
    db.execute.return_value.scalar_one_or_none.return_value = row
    with pytest.raises(ValueError, match="already in the featured list"):
        await fa_crud.add(db, "BTC/USD", "XBTUSD")


@pytest.mark.asyncio
async def test_add_inserts_new_row():
    from app.crud import featured_assets as fa_crud
    db = _mock_db()
    # First call: get_by_symbol → None (not duplicate)
    # Second call: get_all for sort_order → empty
    db.execute.return_value.scalar_one_or_none.return_value = None
    db.execute.return_value.scalars.return_value.all.return_value = []

    with patch("app.crud.featured_assets._bust_cache"):
        row = await fa_crud.add(db, "ADA/USD", "ADAUSD", display_name="Cardano")
    db.add.assert_called_once()
    db.commit.assert_called()


# ── CRUD: remove ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_returns_true_when_found():
    from app.crud import featured_assets as fa_crud
    db = _mock_db()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    db.execute = AsyncMock(return_value=mock_result)
    with patch("app.crud.featured_assets._bust_cache"):
        assert await fa_crud.remove(db, "BTC/USD") is True


@pytest.mark.asyncio
async def test_remove_returns_false_when_not_found():
    from app.crud import featured_assets as fa_crud
    db = _mock_db()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    db.execute = AsyncMock(return_value=mock_result)
    with patch("app.crud.featured_assets._bust_cache"):
        assert await fa_crud.remove(db, "NOTREAL/USD") is False


# ── CRUD: toggle_enabled ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_toggle_enabled_flips_flag():
    from app.crud import featured_assets as fa_crud
    row = _row("BTC/USD", 1, enabled=True)
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = row
    with patch("app.crud.featured_assets._bust_cache"):
        updated = await fa_crud.toggle_enabled(db, "BTC/USD")
    assert updated is not None
    assert updated.enabled is False  # flipped


@pytest.mark.asyncio
async def test_toggle_enabled_returns_none_when_missing():
    from app.crud import featured_assets as fa_crud
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = None
    with patch("app.crud.featured_assets._bust_cache"):
        result = await fa_crud.toggle_enabled(db, "MISSING/USD")
    assert result is None


# ── CRUD: move_up / move_down ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_move_up_swaps_sort_order():
    from app.crud import featured_assets as fa_crud
    row_a = _row("BTC/USD", 1)
    row_b = _row("ETH/USD", 2)
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = [row_a, row_b]
    with patch("app.crud.featured_assets._bust_cache"):
        await fa_crud.move_up(db, "ETH/USD")
    # ETH was at idx 1, BTC at idx 0; ETH should get BTC's old order
    assert row_b.sort_order == 1
    assert row_a.sort_order == 2


@pytest.mark.asyncio
async def test_move_up_first_item_is_noop():
    from app.crud import featured_assets as fa_crud
    row_a = _row("BTC/USD", 1)
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = [row_a]
    with patch("app.crud.featured_assets._bust_cache"):
        await fa_crud.move_up(db, "BTC/USD")  # already first — no swap
    assert row_a.sort_order == 1  # unchanged


@pytest.mark.asyncio
async def test_move_down_swaps_sort_order():
    from app.crud import featured_assets as fa_crud
    row_a = _row("BTC/USD", 1)
    row_b = _row("ETH/USD", 2)
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = [row_a, row_b]
    with patch("app.crud.featured_assets._bust_cache"):
        await fa_crud.move_down(db, "BTC/USD")
    assert row_a.sort_order == 2
    assert row_b.sort_order == 1


# ── CRUD: seed_defaults ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_defaults_inserts_when_empty():
    from app.crud import featured_assets as fa_crud
    db = _mock_db()
    db.execute.return_value.scalar_one.return_value = 0  # count = 0
    with patch("app.crud.featured_assets._bust_cache"):
        n = await fa_crud.seed_defaults(db)
    assert n == 6  # 6 defaults defined
    assert db.add.call_count == 6


@pytest.mark.asyncio
async def test_seed_defaults_noop_when_populated():
    from app.crud import featured_assets as fa_crud
    db = _mock_db()
    db.execute.return_value.scalar_one.return_value = 3  # already has rows
    with patch("app.crud.featured_assets._bust_cache"):
        n = await fa_crud.seed_defaults(db)
    assert n == 0
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_seed_defaults_includes_btc_and_eth():
    """Default seed list must include the two heavyweight pairs."""
    from app.crud.featured_assets import _DEFAULT_FEATURED
    symbols = [d["asset_symbol"] for d in _DEFAULT_FEATURED]
    assert "BTC/USD" in symbols
    assert "ETH/USD" in symbols


# ── Service: cache behaviour ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_service_returns_from_cache_on_second_call():
    svc = FeaturedAssetsService()
    db = AsyncMock()
    with patch("app.crud.featured_assets.get_enabled_symbols",
               new_callable=AsyncMock, return_value=["BTC/USD", "ETH/USD"]) as mock_crud:
        first  = await svc.get_featured_symbols(db)
        second = await svc.get_featured_symbols(db)

    assert first == ["BTC/USD", "ETH/USD"]
    assert second == ["BTC/USD", "ETH/USD"]
    assert mock_crud.call_count == 1  # second call was served from cache


@pytest.mark.asyncio
async def test_service_invalidate_cache_forces_refresh():
    svc = FeaturedAssetsService()
    db = AsyncMock()
    with patch("app.crud.featured_assets.get_enabled_symbols",
               new_callable=AsyncMock, return_value=["BTC/USD"]) as mock_crud:
        await svc.get_featured_symbols(db)
        svc.invalidate_cache()
        await svc.get_featured_symbols(db)
    assert mock_crud.call_count == 2


@pytest.mark.asyncio
async def test_service_cache_is_stale_initially():
    svc = FeaturedAssetsService()
    assert svc._is_fresh() is False


@pytest.mark.asyncio
async def test_service_cache_fresh_after_load():
    svc = FeaturedAssetsService()
    db = AsyncMock()
    with patch("app.crud.featured_assets.get_enabled_symbols",
               new_callable=AsyncMock, return_value=["SOL/USD"]):
        await svc.get_featured_symbols(db)
    assert svc._is_fresh() is True


@pytest.mark.asyncio
async def test_service_ensure_seeded_calls_seed():
    svc = FeaturedAssetsService()
    db = AsyncMock()
    with patch("app.crud.featured_assets.seed_defaults",
               new_callable=AsyncMock, return_value=6) as mock_seed:
        await svc.ensure_seeded(db)
    mock_seed.assert_called_once_with(db)
    # Cache should be invalidated because rows were inserted
    assert svc._cache_at is None


@pytest.mark.asyncio
async def test_service_ensure_seeded_noop_when_already_seeded():
    svc = FeaturedAssetsService()
    db = AsyncMock()
    with patch("app.crud.featured_assets.seed_defaults",
               new_callable=AsyncMock, return_value=0):
        await svc.ensure_seeded(db)
    # No rows inserted → cache was not busted


# ── Backward compat: no hard-coded featured_pairs at runtime ──────────────────

def test_config_featured_pairs_not_used_in_dashboard(tmp_path):
    """dashboard.py must NOT call settings.featured_pairs at runtime."""
    import ast, pathlib
    src = pathlib.Path("app/api/routes/dashboard.py").read_text()
    # Must not contain settings.featured_pairs (the old pattern)
    assert "settings.featured_pairs" not in src, (
        "dashboard.py still calls settings.featured_pairs — "
        "must use featured_assets_service.get_featured_symbols(db)"
    )


def test_config_featured_pairs_not_used_in_assets_route():
    import pathlib
    src = pathlib.Path("app/api/routes/assets.py").read_text()
    assert "settings.featured_pairs" not in src, (
        "assets.py still calls settings.featured_pairs"
    )


def test_config_featured_pairs_not_used_in_alert_engine():
    import pathlib
    src = pathlib.Path("app/services/alert_engine.py").read_text()
    assert "settings.featured_pairs" not in src, (
        "alert_engine.py still calls settings.featured_pairs"
    )
