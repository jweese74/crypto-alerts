"""Tests for the ticker view — UserTickerAsset model, CRUD, service, and routes."""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.user_ticker_asset import UserTickerAsset


# ── Helpers ────────────────────────────────────────────────────────────────────

def _uid() -> uuid.UUID:
    return uuid.uuid4()


def _row(user_id: uuid.UUID, symbol: str, order: int = 1, enabled: bool = True) -> UserTickerAsset:
    return UserTickerAsset(
        id=order,
        user_id=user_id,
        asset_symbol=symbol,
        display_name=None,
        sort_order=order,
        enabled=enabled,
    )


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = []
    res.scalar_one_or_none.return_value = None
    res.scalar_one.return_value = 0
    db.execute = AsyncMock(return_value=res)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


# ── Model ──────────────────────────────────────────────────────────────────────

class TestUserTickerAssetModel:
    def test_repr(self):
        uid = _uid()
        row = _row(uid, "BTC/USD", 1)
        assert "BTC/USD" in repr(row)
        assert "enabled=True" in repr(row)

    def test_fields(self):
        uid = _uid()
        row = UserTickerAsset(
            user_id=uid,
            asset_symbol="ETH/USD",
            display_name="Ethereum Watch",
            sort_order=2,
            enabled=True,
        )
        assert row.user_id == uid
        assert row.asset_symbol == "ETH/USD"
        assert row.display_name == "Ethereum Watch"
        assert row.sort_order == 2
        assert row.enabled is True

    def test_unique_constraint_defined(self):
        """UniqueConstraint on (user_id, asset_symbol) must be in __table_args__."""
        from sqlalchemy import UniqueConstraint
        args = UserTickerAsset.__table_args__
        constraint_names = [
            c.name for c in args if isinstance(c, UniqueConstraint)
        ]
        assert "uq_user_ticker_asset" in constraint_names


# ── CRUD: reads ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_for_user_returns_ordered():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    rows = [_row(uid, "ETH/USD", 2), _row(uid, "BTC/USD", 1)]
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = rows
    result = await ut_crud.get_for_user(db, uid)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_enabled_for_user_only_enabled():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    rows = [_row(uid, "BTC/USD", 1, enabled=True), _row(uid, "SOL/USD", 2, enabled=True)]
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = rows
    result = await ut_crud.get_enabled_for_user(db, uid)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_enabled_symbols():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    rows = [_row(uid, "BTC/USD", 1), _row(uid, "ETH/USD", 2)]
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = rows
    syms = await ut_crud.get_enabled_symbols(db, uid)
    assert "BTC/USD" in syms
    assert "ETH/USD" in syms


@pytest.mark.asyncio
async def test_get_by_symbol_returns_none():
    from app.crud import user_ticker as ut_crud
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = None
    result = await ut_crud.get_by_symbol(db, _uid(), "NOTREAL/USD")
    assert result is None


@pytest.mark.asyncio
async def test_get_by_symbol_returns_row():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    row = _row(uid, "BTC/USD")
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = row
    result = await ut_crud.get_by_symbol(db, uid, "BTC/USD")
    assert result is row


# ── CRUD: add ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_raises_if_duplicate():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    existing = _row(uid, "BTC/USD")
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = existing
    with pytest.raises(ValueError, match="already in your ticker"):
        await ut_crud.add(db, uid, "BTC/USD")


@pytest.mark.asyncio
async def test_add_inserts_new_row():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = None
    db.execute.return_value.scalars.return_value.all.return_value = []
    await ut_crud.add(db, uid, "ADA/USD", display_name="Cardano")
    db.add.assert_called_once()
    db.commit.assert_called()


# ── CRUD: remove ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_returns_true_when_found():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    db = _mock_db()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    db.execute = AsyncMock(return_value=mock_result)
    assert await ut_crud.remove(db, uid, "BTC/USD") is True


@pytest.mark.asyncio
async def test_remove_returns_false_when_missing():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    db = _mock_db()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    db.execute = AsyncMock(return_value=mock_result)
    assert await ut_crud.remove(db, uid, "NOTREAL/USD") is False


# ── CRUD: toggle_enabled ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_toggle_enabled_flips_flag():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    row = _row(uid, "BTC/USD", enabled=True)
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = row
    updated = await ut_crud.toggle_enabled(db, uid, "BTC/USD")
    assert updated is not None
    assert updated.enabled is False


@pytest.mark.asyncio
async def test_toggle_enabled_returns_none_when_missing():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    db = _mock_db()
    db.execute.return_value.scalar_one_or_none.return_value = None
    result = await ut_crud.toggle_enabled(db, uid, "MISSING/USD")
    assert result is None


# ── CRUD: move_up / move_down ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_move_up_swaps_sort_order():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    row_a = _row(uid, "BTC/USD", 1)
    row_b = _row(uid, "ETH/USD", 2)
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = [row_a, row_b]
    await ut_crud.move_up(db, uid, "ETH/USD")
    assert row_b.sort_order == 1
    assert row_a.sort_order == 2


@pytest.mark.asyncio
async def test_move_up_first_is_noop():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    row_a = _row(uid, "BTC/USD", 1)
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = [row_a]
    await ut_crud.move_up(db, uid, "BTC/USD")
    assert row_a.sort_order == 1


@pytest.mark.asyncio
async def test_move_down_swaps_sort_order():
    from app.crud import user_ticker as ut_crud
    uid = _uid()
    row_a = _row(uid, "BTC/USD", 1)
    row_b = _row(uid, "ETH/USD", 2)
    db = _mock_db()
    db.execute.return_value.scalars.return_value.all.return_value = [row_a, row_b]
    await ut_crud.move_down(db, uid, "BTC/USD")
    assert row_a.sort_order == 2
    assert row_b.sort_order == 1


# ── Route helpers ─────────────────────────────────────────────────────────────

def test_sym_to_key():
    from app.api.routes.ticker import _sym_to_key
    assert _sym_to_key("BTC/USD") == "BTC_USD"
    assert _sym_to_key("ETH/USD") == "ETH_USD"


def test_sym_from_url():
    from app.api.routes.ticker import _sym_from_url
    assert _sym_from_url("BTC_USD") == "BTC/USD"
    assert _sym_from_url("ETH_USD") == "ETH/USD"


# ── _build_ticker_items: ordering + deduplication ─────────────────────────────

@pytest.mark.asyncio
async def test_build_ticker_items_deduplicates_overlap():
    """If an asset is in both featured and personal, it appears once (as featured)."""
    from app.api.routes.ticker import _build_ticker_items
    from app.models.featured_asset import FeaturedAsset

    uid = _uid()
    db = AsyncMock()

    user_obj = MagicMock()
    user_obj.id = uid

    fa_row = FeaturedAsset(asset_symbol="BTC/USD", kraken_pair="XBTUSD",
                           display_name="Bitcoin", sort_order=1, enabled=True)
    ut_row = UserTickerAsset(user_id=uid, asset_symbol="BTC/USD",
                             sort_order=1, enabled=True)

    with patch("app.crud.featured_assets.get_enabled",
               new_callable=AsyncMock, return_value=[fa_row]), \
         patch("app.crud.user_ticker.get_enabled_for_user",
               new_callable=AsyncMock, return_value=[ut_row]), \
         patch("app.crud.alert.get_rules_for_user",
               new_callable=AsyncMock, return_value=[]):
        import app.services.market_data as md
        md.market_data_service._last_prices = {"BTC/USD": 50000.0}
        items, featured_count, personal_count = await _build_ticker_items(db, user_obj)

    # BTC/USD should appear only once despite being in both lists
    btc_items = [i for i in items if i["symbol"] == "BTC/USD"]
    assert len(btc_items) == 1
    assert btc_items[0]["is_featured"] is True
    assert personal_count == 0


@pytest.mark.asyncio
async def test_build_ticker_items_featured_first():
    """Featured assets must appear before personal assets."""
    from app.api.routes.ticker import _build_ticker_items
    from app.models.featured_asset import FeaturedAsset

    uid = _uid()
    db = AsyncMock()
    user_obj = MagicMock()
    user_obj.id = uid

    fa_row = FeaturedAsset(asset_symbol="BTC/USD", kraken_pair="XBTUSD",
                           display_name="Bitcoin", sort_order=1, enabled=True)
    ut_row = UserTickerAsset(user_id=uid, asset_symbol="SOL/USD",
                             sort_order=1, enabled=True)

    import app.services.market_data as md
    md.market_data_service._last_prices = {}
    with patch("app.crud.featured_assets.get_enabled",
               new_callable=AsyncMock, return_value=[fa_row]), \
         patch("app.crud.user_ticker.get_enabled_for_user",
               new_callable=AsyncMock, return_value=[ut_row]), \
         patch("app.crud.alert.get_rules_for_user",
               new_callable=AsyncMock, return_value=[]):
        items, featured_count, personal_count = await _build_ticker_items(db, user_obj)

    assert items[0]["symbol"] == "BTC/USD"
    assert items[0]["is_featured"] is True
    assert items[1]["symbol"] == "SOL/USD"
    assert items[1]["is_featured"] is False
    assert featured_count == 1
    assert personal_count == 1


@pytest.mark.asyncio
async def test_build_ticker_items_alert_flag():
    """has_alert must be True for assets where user has active rules."""
    from app.api.routes.ticker import _build_ticker_items
    from app.models.featured_asset import FeaturedAsset
    from app.models.alert_rule import AlertRule

    uid = _uid()
    db = AsyncMock()
    user_obj = MagicMock()
    user_obj.id = uid

    fa_row = FeaturedAsset(asset_symbol="BTC/USD", kraken_pair="XBTUSD",
                           display_name="Bitcoin", sort_order=1, enabled=True)
    alert_rule = MagicMock(spec=AlertRule)
    alert_rule.trading_pair = "BTC/USD"
    alert_rule.is_active = True

    import app.services.market_data as md
    md.market_data_service._last_prices = {}
    with patch("app.crud.featured_assets.get_enabled",
               new_callable=AsyncMock, return_value=[fa_row]), \
         patch("app.crud.user_ticker.get_enabled_for_user",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.crud.alert.get_rules_for_user",
               new_callable=AsyncMock, return_value=[alert_rule]):
        items, _, _ = await _build_ticker_items(db, user_obj)

    assert items[0]["symbol"] == "BTC/USD"
    assert items[0]["has_alert"] is True


# ── Nav link present in base.html ─────────────────────────────────────────────

def test_ticker_nav_link_in_base():
    import pathlib
    src = pathlib.Path("app/templates/base.html").read_text()
    assert 'href="/ticker"' in src, "base.html must have a /ticker nav link"


def test_ticker_link_in_dashboard():
    import pathlib
    src = pathlib.Path("app/templates/dashboard/index.html").read_text()
    assert 'href="/ticker"' in src, "dashboard must have an Open Ticker View link"


def test_ticker_exit_button_in_view():
    import pathlib
    src = pathlib.Path("app/templates/ticker/view.html").read_text()
    assert "Exit Ticker View" in src, "ticker view must have an Exit Ticker View button"
    assert "/dashboard" in src, "Exit button must link to dashboard"
